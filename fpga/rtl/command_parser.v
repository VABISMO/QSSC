`timescale 1ns / 1ps
//============================================================================
// command_parser.v
// ----------------------------------------------------------------------------
// Text command parser for the QSSC UART protocol. Accepts bytes from
// uart_rx, buffers a line up to '\n', then parses and dispatches:
//
//   FIRE_LASER <row> <col> <duration_ms>      -> fire 8x8 VCSEL, reply OK\n
//   APPLY_PULSE <qubit> <freq_khz> <amp> <duration_ns> <phase>
//                                             -> program ADF4351, reply OK\n
//   CAPTURE_FRAME                             -> grab + stream a frame, OK\n
//   CAPTURE_DARK                              -> same as CAPTURE_FRAME
//
// Unknown / malformed commands reply "ERR\n". Frame bytes are streamed raw
// (FRAME_BYTES bytes) right after capture completes, followed by "OK\n".
// All numbers are decimal integers (optionally signed).
//
// A small TX engine (separate always block) arbitrates the UART transmitter:
// the main FSM pulses tx_load with tx_din when tx_ready is asserted.
//============================================================================
module command_parser #(
    parameter FRAME_BYTES = 3072
) (
    input             clk,
    input             reset,
    // UART RX side
    input      [7:0]  rx_byte,
    input             rx_valid,
    // UART TX side
    output reg [7:0]  tx_byte,
    output reg        tx_start,
    input             tx_busy,
    // Laser array
    output reg        laser_fire,
    output reg [2:0]  laser_row,
    output reg [2:0]  laser_col,
    output reg [31:0] laser_duration_ms,
    input             laser_busy,
    // ADF4351 PLL
    output reg        pll_start,
    output reg [31:0] pll_freq_khz,
    input             pll_busy,
    // Camera capture
    output reg        cam_capture_start,
    input             cam_frame_ready,
    output reg [15:0] cam_rd_addr,
    input      [7:0]  cam_rd_data,
    input             cam_capturing
);
    // ---- Command IDs -------------------------------------------------------
    localparam [1:0] CMD_FIRE    = 2'd0;
    localparam [1:0] CMD_PULSE   = 2'd1;
    localparam [1:0] CMD_CAPTURE = 2'd2;
    localparam [1:0] CMD_ERR     = 2'd3;

    // ---- FSM states --------------------------------------------------------
    localparam [3:0] S_IDLE         = 4'd0;
    localparam [3:0] S_KW           = 4'd1;
    localparam [3:0] S_ARGSP        = 4'd2;
    localparam [3:0] S_ARGNUM       = 4'd3;
    localparam [3:0] S_DISPATCH     = 4'd4;
    localparam [3:0] S_CAPTURE_WAIT = 4'd5;
    localparam [3:0] S_STREAM_INIT  = 4'd6;
    localparam [3:0] S_STREAM       = 4'd7;
    localparam [3:0] S_REPLY_LOAD   = 4'd8;
    localparam [3:0] S_REPLY_SEND   = 4'd9;

    // ---- Line buffer -------------------------------------------------------
    reg [7:0] cmd_buffer [0:63];
    reg [6:0] buf_idx;
    reg       line_ready;

    // ---- Parser registers --------------------------------------------------
    reg [3:0]  fsm;
    reg [6:0]  parse_idx;
    reg [7:0]  cmd_str [0:15];
    reg [3:0]  cmd_len;
    reg [1:0]  cmd_id;
    reg [2:0]  arg_count;
    reg [2:0]  arg_idx;
    reg [31:0] current_num;
    reg        num_sign;
    reg [31:0] arg_vals [0:4];
    reg        dark;
    reg [15:0] stream_idx;
    reg        reply_sel;       // 0=OK, 1=ERR
    reg [2:0]  reply_idx;
    reg [2:0]  reply_len;
    reg [7:0]  reply_mem [0:7];

    // ---- TX engine (internal) ---------------------------------------------
    reg  [7:0] tx_din;
    reg        tx_load;
    reg  [1:0] txst;

    // Current character under the parse cursor.
    wire [7:0] ch = cmd_buffer[parse_idx];
    wire is_digit = (ch >= 8'h30) && (ch <= 8'h39);   // '0'..'9'
    wire is_eol   = (parse_idx >= buf_idx);

    initial begin
        buf_idx       = 7'd0;
        line_ready    = 1'b0;
        fsm           = S_IDLE;
        parse_idx     = 7'd0;
        cmd_len       = 4'd0;
        cmd_id        = CMD_ERR;
        arg_count     = 3'd0;
        arg_idx       = 3'd0;
        current_num   = 32'd0;
        num_sign      = 1'b0;
        dark          = 1'b0;
        stream_idx    = 16'd0;
        reply_sel     = 1'b0;
        reply_idx     = 3'd0;
        reply_len     = 3'd0;
        laser_fire    = 1'b0;
        laser_row     = 3'd0;
        laser_col     = 3'd0;
        laser_duration_ms = 32'd0;
        pll_start     = 1'b0;
        pll_freq_khz  = 32'd0;
        cam_capture_start = 1'b0;
        cam_rd_addr   = 16'd0;
        tx_din        = 8'd0;
        tx_load       = 1'b0;
        txst          = 2'd0;
        tx_byte       = 8'd0;
        tx_start      = 1'b0;
    end

    // tx_ready is asserted only when the engine is idle AND no load is
    // currently requested, which prevents a double-load on the cycle the
    // main FSM pulses tx_load.
    wire tx_ready = (txst == 2'd0) && (tx_load == 1'b0);

    // ---- TX engine: drives tx_byte/tx_start, handshakes via tx_load --------
    always @(posedge clk) begin
        if (reset) begin
            txst      <= 2'd0;
            tx_start  <= 1'b0;
            tx_byte   <= 8'd0;
        end else begin
            case (txst)
                2'd0: begin
                    tx_start <= 1'b0;
                    if (tx_load) begin
                        tx_byte   <= tx_din;
                        tx_start  <= 1'b1;
                        txst      <= 2'd1;
                    end
                end
                2'd1: begin
                    tx_start <= 1'b0;
                    if (tx_busy) txst <= 2'd2;
                end
                2'd2: begin
                    if (!tx_busy) txst <= 2'd0;
                end
                default: txst <= 2'd0;
            endcase
        end
    end

    // ---- Main FSM + line accumulation --------------------------------------
    always @(posedge clk) begin
        if (reset) begin
            fsm               <= S_IDLE;
            buf_idx           <= 7'd0;
            line_ready        <= 1'b0;
            parse_idx         <= 7'd0;
            cmd_len           <= 4'd0;
            cmd_id            <= CMD_ERR;
            arg_count         <= 3'd0;
            arg_idx           <= 3'd0;
            current_num       <= 32'd0;
            num_sign          <= 1'b0;
            dark              <= 1'b0;
            stream_idx        <= 16'd0;
            reply_sel         <= 1'b0;
            reply_idx         <= 3'd0;
            reply_len         <= 3'd0;
            laser_fire        <= 1'b0;
            laser_row         <= 3'd0;
            laser_col         <= 3'd0;
            laser_duration_ms <= 32'd0;
            pll_start         <= 1'b0;
            pll_freq_khz      <= 32'd0;
            cam_capture_start <= 1'b0;
            cam_rd_addr       <= 16'd0;
            tx_load           <= 1'b0;
            tx_din            <= 8'd0;
        end else begin
            // Default one-cycle pulses
            tx_load           <= 1'b0;
            laser_fire        <= 1'b0;
            pll_start         <= 1'b0;
            cam_capture_start <= 1'b0;

            // Line accumulation only while idle and no line pending.
            if (fsm == S_IDLE && !line_ready && rx_valid) begin
                if (rx_byte == 8'h0A) begin
                    line_ready <= 1'b1;          // end of line; buf_idx = length
                end else if (buf_idx < 7'd63) begin
                    cmd_buffer[buf_idx] <= rx_byte;
                    buf_idx <= buf_idx + 7'd1;
                end else begin
                    cmd_buffer[buf_idx] <= rx_byte;   // overflow: force end
                    line_ready <= 1'b1;
                end
            end

            case (fsm)
                // ---- Wait for a full line --------------------------------
                S_IDLE : begin
                    if (line_ready) begin
                        parse_idx   <= 7'd0;
                        cmd_len     <= 4'd0;
                        cmd_id      <= CMD_ERR;
                        arg_count   <= 3'd0;
                        arg_idx     <= 3'd0;
                        current_num <= 32'd0;
                        num_sign    <= 1'b0;
                        fsm         <= S_KW;
                    end
                end

                // ---- Accumulate keyword ----------------------------------
                S_KW : begin
                    if (is_eol || ch == 8'h20) begin
                        // Keyword finished -> identify it and pick next state.
                        arg_idx     <= 3'd0;
                        current_num <= 32'd0;
                        num_sign    <= 1'b0;
                        if (cmd_len == 4'd10 &&
                            cmd_str[0]=="F" && cmd_str[1]=="I" && cmd_str[2]=="R" &&
                            cmd_str[3]=="E" && cmd_str[4]=="_" && cmd_str[5]=="L" &&
                            cmd_str[6]=="A" && cmd_str[7]=="S" && cmd_str[8]=="E" &&
                            cmd_str[9]=="R") begin
                            cmd_id    <= CMD_FIRE;
                            arg_count <= 3'd3;
                            if (!is_eol && ch == 8'h20) begin
                                parse_idx <= parse_idx + 7'd1;
                                fsm       <= S_ARGSP;
                            end else begin
                                cmd_id <= CMD_ERR;     // needs 3 args
                                fsm    <= S_DISPATCH;
                            end
                        end else if (cmd_len == 4'd11 &&
                            cmd_str[0]=="A" && cmd_str[1]=="P" && cmd_str[2]=="P" &&
                            cmd_str[3]=="L" && cmd_str[4]=="Y" && cmd_str[5]=="_" &&
                            cmd_str[6]=="P" && cmd_str[7]=="U" && cmd_str[8]=="L" &&
                            cmd_str[9]=="S" && cmd_str[10]=="E") begin
                            cmd_id    <= CMD_PULSE;
                            arg_count <= 3'd5;
                            if (!is_eol && ch == 8'h20) begin
                                parse_idx <= parse_idx + 7'd1;
                                fsm       <= S_ARGSP;
                            end else begin
                                cmd_id <= CMD_ERR;     // needs 5 args
                                fsm    <= S_DISPATCH;
                            end
                        end else if (cmd_len == 4'd13 &&
                            cmd_str[0]=="C" && cmd_str[1]=="A" && cmd_str[2]=="P" &&
                            cmd_str[3]=="T" && cmd_str[4]=="U" && cmd_str[5]=="R" &&
                            cmd_str[6]=="E" && cmd_str[7]=="_" && cmd_str[8]=="F" &&
                            cmd_str[9]=="R" && cmd_str[10]=="A" && cmd_str[11]=="M" &&
                            cmd_str[12]=="E") begin
                            cmd_id <= CMD_CAPTURE;
                            dark   <= 1'b0;
                            fsm    <= S_DISPATCH;
                        end else if (cmd_len == 4'd12 &&
                            cmd_str[0]=="C" && cmd_str[1]=="A" && cmd_str[2]=="P" &&
                            cmd_str[3]=="T" && cmd_str[4]=="U" && cmd_str[5]=="R" &&
                            cmd_str[6]=="E" && cmd_str[7]=="_" && cmd_str[8]=="D" &&
                            cmd_str[9]=="A" && cmd_str[10]=="R" && cmd_str[11]=="K") begin
                            cmd_id <= CMD_CAPTURE;
                            dark   <= 1'b1;
                            fsm    <= S_DISPATCH;
                        end else begin
                            cmd_id <= CMD_ERR;
                            fsm    <= S_DISPATCH;
                        end
                    end else begin
                        if (cmd_len < 4'd15) begin
                            cmd_str[cmd_len] <= ch;
                            cmd_len <= cmd_len + 4'd1;
                        end
                        parse_idx <= parse_idx + 7'd1;
                    end
                end

                // ---- Skip spaces before an argument ----------------------
                S_ARGSP : begin
                    if (is_eol) begin
                        // line ended before all args were read
                        if (arg_idx < arg_count) cmd_id <= CMD_ERR;
                        fsm <= S_DISPATCH;
                    end else if (ch == 8'h20) begin
                        parse_idx <= parse_idx + 7'd1;
                    end else if (ch == 8'h2D) begin   // '-'
                        num_sign    <= 1'b1;
                        current_num <= 32'd0;
                        parse_idx   <= parse_idx + 7'd1;
                    end else if (is_digit) begin
                        num_sign    <= 1'b0;
                        current_num <= 32'd0;
                        fsm         <= S_ARGNUM;
                    end else begin
                        cmd_id <= CMD_ERR;
                        fsm    <= S_DISPATCH;
                    end
                end

                // ---- Read a decimal number -------------------------------
                S_ARGNUM : begin
                    if (is_digit) begin
                        current_num <= current_num * 32'd10 + (ch - 8'h30);
                        parse_idx   <= parse_idx + 7'd1;
                    end else begin
                        // end of number: store into arg_vals[arg_idx]
                        if (num_sign)
                            arg_vals[arg_idx] <= -current_num;
                        else
                            arg_vals[arg_idx] <= current_num;
                        arg_idx     <= arg_idx + 3'd1;
                        current_num <= 32'd0;
                        num_sign    <= 1'b0;
                        if (arg_idx + 3'd1 >= arg_count) begin
                            fsm <= S_DISPATCH;
                        end else begin
                            fsm <= S_ARGSP;
                        end
                    end
                end

                // ---- Execute the parsed command --------------------------
                S_DISPATCH : begin
                    // Free the line buffer for the next command.
                    line_ready <= 1'b0;
                    buf_idx    <= 7'd0;
                    case (cmd_id)
                        CMD_FIRE : begin
                            laser_row         <= arg_vals[0][2:0];
                            laser_col         <= arg_vals[1][2:0];
                            laser_duration_ms <= arg_vals[2];
                            laser_fire        <= 1'b1;       // 1-cycle pulse
                            reply_sel         <= 1'b0;       // OK
                            fsm               <= S_REPLY_LOAD;
                        end
                        CMD_PULSE : begin
                            pll_freq_khz <= arg_vals[1];
                            pll_start    <= 1'b1;            // 1-cycle pulse
                            reply_sel    <= 1'b0;            // OK
                            fsm          <= S_REPLY_LOAD;
                        end
                        CMD_CAPTURE : begin
                            cam_capture_start <= 1'b1;      // 1-cycle pulse
                            stream_idx        <= 16'd0;
                            cam_rd_addr       <= 16'd0;
                            fsm               <= S_CAPTURE_WAIT;
                        end
                        default : begin
                            reply_sel <= 1'b1;              // ERR
                            fsm       <= S_REPLY_LOAD;
                        end
                    endcase
                end

                // ---- Wait for the camera frame to be ready --------------
                S_CAPTURE_WAIT : begin
                    if (cam_frame_ready) begin
                        cam_rd_addr <= 16'd0;
                        stream_idx  <= 16'd0;
                        fsm         <= S_STREAM_INIT;
                    end
                end

                // ---- One cycle for registered BRAM read to settle --------
                S_STREAM_INIT : begin
                    fsm <= S_STREAM;
                end

                // ---- Stream FRAME_BYTES over UART ------------------------
                S_STREAM : begin
                    if (tx_ready) begin
                        tx_load <= 1'b1;
                        tx_din  <= cam_rd_data;
                        if (stream_idx == (FRAME_BYTES-1)) begin
                            stream_idx <= 16'd0;
                            reply_sel  <= 1'b0;     // OK after the frame
                            fsm        <= S_REPLY_LOAD;
                        end else begin
                            stream_idx  <= stream_idx + 16'd1;
                            cam_rd_addr <= cam_rd_addr + 16'd1;
                        end
                    end
                end

                // ---- Load reply string -----------------------------------
                S_REPLY_LOAD : begin
                    if (reply_sel == 1'b0) begin
                        reply_mem[0] <= "O";
                        reply_mem[1] <= "K";
                        reply_mem[2] <= 8'h0A;       // '\n'
                        reply_len    <= 3'd3;
                    end else begin
                        reply_mem[0] <= "E";
                        reply_mem[1] <= "R";
                        reply_mem[2] <= "R";
                        reply_mem[3] <= 8'h0A;       // '\n'
                        reply_len    <= 3'd4;
                    end
                    reply_idx <= 3'd0;
                    fsm       <= S_REPLY_SEND;
                end

                // ---- Send reply bytes ------------------------------------
                S_REPLY_SEND : begin
                    if (tx_ready) begin
                        if (reply_idx == reply_len) begin
                            fsm <= S_IDLE;
                        end else begin
                            tx_load    <= 1'b1;
                            tx_din     <= reply_mem[reply_idx];
                            reply_idx  <= reply_idx + 3'd1;
                        end
                    end
                end

                default : fsm <= S_IDLE;
            endcase
        end
    end
endmodule