`timescale 1ns / 1ps
//============================================================================
// command_parser.v
// ----------------------------------------------------------------------------
// Text command parser for the QSSC UART protocol. Accepts bytes from
// uart_rx, buffers a line up to '\n', then parses and dispatches the parallel
// on-chip control/readout command set (no camera, no analog MUX):
//
//   DRIVE_EMITTER <index> <duration_ms>     -> fire one of 64 emitters, OK\n
//   APPLY_PULSE <qubit> <freq_khz> <amp> <duration_ns> <phase>
//                                          -> program ADF4351 + gate RF, OK\n
//   READ_DETECTOR <site>                   -> read one of 64 photon counters,
//                                               reply <count>\n
//   READ_DETECTORS                         -> stream all 64 counters,
//                                               reply <c0> <c1> ... <c63>\n
//   CLEAR_DETECTORS                        -> zero all 64 counters, OK\n
//   SET_THRESHOLD <0..4095>                -> MCP4725 comparator threshold, OK\n
//   CALIBRATE                              -> for each emitter: clear, pump,
//                                               read_all, store brightest det
//                                               -> cal_lut[em], then OK\n
//
// Unknown / malformed commands reply "ERR\n". Numbers are decimal integers
// (optionally signed). Detector counts are 16-bit (0..65535) sent as ASCII
// decimal, space-separated for READ_DETECTORS.
//
// A small TX engine (separate always block) drives the UART transmitter: the
// main FSM pulses tx_load with tx_din when tx_ready is asserted.
//============================================================================
module command_parser #(
    parameter N_DET = 64
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
    // Emitter array (64 individual enables)
    output reg        em_drive,
    output reg [5:0]  em_index,
    output reg [31:0] em_duration_ms,
    input             em_busy,
    // ADF4351 PLL
    output reg        pll_start,
    output reg [31:0] pll_freq_khz,
    input             pll_busy,
    // Photon counter array (64 parallel digital readout)
    output reg        det_read_one,
    output reg        det_read_all,
    output reg        det_clear,        // 1-cycle pulse: zero all 64 counters
    output reg [5:0]  det_site,
    input             det_busy,
    input      [15:0] det_code,
    input             det_code_valid,
    input      [5:0]  det_code_idx,
    input             det_sweep_done,
    output reg        det_code_ack,     // latched the code; advance the sweep
    // Calibration LUT readback (emitter pixel -> detector electrode)
    input      [5:0]  lut_rd_addr,
    output     [5:0]  lut_rd_data,
    // MW pulse envelope. The FPGA does NOT shape an analog amplitude/phase
    // envelope (a real AWG/modulator does that); it sets the ADF4351 carrier
    // phase (R1 PHASE) and output power (R4 RF_PWR, 4 levels), and gates an
    // RF switch for the requested duration (mw_gate_ns -> mw_gate_load). This
    // is the minimal honest FPGA MW path. Latched on APPLY_PULSE.
    output reg [11:0] pll_phase,        // -> ADF4351 R1 PHASE
    output reg [1:0]  pll_rf_pwr,       // -> ADF4351 R4 RF_PWR (4 levels)
    output reg [31:0] mw_gate_ns,       // -> RF-switch gate timer (duration, ns)
    output reg        mw_gate_load,     // 1-cycle pulse: arm the RF-switch gate
    // I2C to MCP4725 comparator-threshold DAC (SET_THRESHOLD)
    output reg        i2c_start,
    output reg [11:0] i2c_data,
    input             i2c_busy
);
    // ---- Command IDs -------------------------------------------------------
    localparam [3:0] CMD_DRIVE     = 4'd0;   // DRIVE_EMITTER (2 args)
    localparam [3:0] CMD_PULSE     = 4'd1;   // APPLY_PULSE (5 args)
    localparam [3:0] CMD_READ_ONE  = 4'd2;   // READ_DETECTOR (1 arg)
    localparam [3:0] CMD_READ_ALL  = 4'd3;   // READ_DETECTORS (0 args)
    localparam [3:0] CMD_CALIBRATE = 4'd4;   // CALIBRATE (0 args)
    localparam [3:0] CMD_CLEAR     = 4'd5;   // CLEAR_DETECTORS (0 args)
    localparam [3:0] CMD_THRESH    = 4'd6;   // SET_THRESHOLD (1 arg)
    localparam [3:0] CMD_ERR       = 4'd7;

    // ---- FSM states --------------------------------------------------------
    localparam [3:0] S_IDLE          = 4'd0;
    localparam [3:0] S_KW            = 4'd1;
    localparam [3:0] S_ARGSP         = 4'd2;
    localparam [3:0] S_ARGNUM        = 4'd3;
    localparam [3:0] S_DISPATCH      = 4'd4;
    localparam [3:0] S_REPLY_LOAD    = 4'd5;   // load OK/ERR string
    localparam [3:0] S_REPLY_SEND    = 4'd6;   // stream reply_mem
    localparam [3:0] S_RONE_WAIT     = 4'd7;   // wait det_code_valid (read_one)
    localparam [3:0] S_ENC          = 4'd8;   // encode code -> decimal reply_mem
    localparam [3:0] S_RALL_WAIT     = 4'd9;   // wait det_code_valid (read_all)
    localparam [3:0] S_CAL_DRIVE     = 4'd10;  // calibrate: clear + drive emitter
    localparam [3:0] S_CAL_WAIT_EM   = 4'd11;  // calibrate: wait emitter !busy
    localparam [3:0] S_CAL_READ      = 4'd12;  // calibrate: capture max count
    localparam [3:0] S_CAL_STORE     = 4'd13;  // calibrate: store LUT, next pixel
    localparam [3:0] S_PULSE_WAIT    = 4'd14;  // APPLY_PULSE: wait PLL done, arm gate
    localparam [3:0] S_THRESH_WAIT   = 4'd15;  // SET_THRESHOLD: wait I2C done

    // ---- Line buffer ------------------------------------------------------
    reg [7:0] cmd_buffer [0:63];
    reg [6:0] buf_idx;
    reg       line_ready;

    // ---- Parser registers --------------------------------------------------
    reg [3:0]  fsm;
    reg [6:0]  parse_idx;
    reg [7:0]  cmd_str [0:15];
    reg [3:0]  cmd_len;
    reg [3:0]  cmd_id;
    reg [2:0]  arg_count;
    reg [2:0]  arg_idx;
    reg [31:0] current_num;
    reg        num_sign;
    reg [31:0] arg_vals [0:4];

    // ---- Reply / decimal encoder ------------------------------------------
    reg        reply_ok;        // 1=OK, 0=ERR (for the OK/ERR path)
    reg [2:0]  reply_idx;
    reg [2:0]  reply_len;       // number of bytes to send this chunk
    reg [2:0]  reply_start;     // first reply_mem index to send
    reg [7:0]  reply_mem [0:7];
    reg        rall_loop;       // after this read_all code, loop for the next
    reg        code_is_last;    // read_all code was the final channel
    reg [15:0] code_latch;      // snapshot of det_code on code_valid (held stable
                                // while the UART drains; the live det_code may
                                // change once we ack the counter sweep)

    // ---- Read-one / read-all mode flag ------------------------------------
    reg        read_all_mode;

    // ---- APPLY_PULSE / SET_THRESHOLD: wait for the peripheral to finish -----
    reg        pll_busy_seen;
    reg        i2c_busy_seen;

    // ---- Calibration helpers ----------------------------------------------
    reg [15:0] cal_max_code;
    reg [5:0]  cal_max_det;
    reg [5:0]  cal_em;          // calibration emitter pixel 0..63

    // ---- Calibration LUT (emitter pixel -> detector electrode) ------------
    reg [5:0] cal_lut [0:63];
    assign lut_rd_data = cal_lut[lut_rd_addr];

    // ---- TX engine (internal) ---------------------------------------------
    reg  [7:0] tx_din;
    reg        tx_load;
    reg  [1:0] txst;

    // Current character under the parse cursor.
    wire [7:0] ch = (parse_idx < buf_idx) ? cmd_buffer[parse_idx] : 8'h00;
    wire is_digit = (ch >= 8'h30) && (ch <= 8'h39);   // '0'..'9'
    wire is_eol   = (parse_idx >= buf_idx);

    // decimal encode: {len[7:0], tenk[7:0], thou[7:0], hund[7:0], tens[7:0], ones[7:0]}
    // 16-bit input -> up to 5 decimal digits (0..65535).
    function [47:0] enc_dec;
        input [15:0] v;
        reg [7:0] tenk, thou, hund, tens, ones, len;
        reg [15:0] r;
        begin
            tenk = 8'h30 + (v / 16'd10000);  r = v % 16'd10000;
            thou = 8'h30 + (r / 16'd1000);   r = r % 16'd1000;
            hund = 8'h30 + (r / 16'd100);    r = r % 16'd100;
            tens = 8'h30 + (r / 16'd10);
            ones = 8'h30 + (r % 16'd10);
            if (tenk != 8'h30)      len = 8'd5;
            else if (thou != 8'h30) len = 8'd4;
            else if (hund != 8'h30) len = 8'd3;
            else if (tens != 8'h30) len = 8'd2;
            else                    len = 8'd1;
            enc_dec = {len, tenk, thou, hund, tens, ones};
        end
    endfunction

    // combinational decimal encoding of the latched detector count (the live
    // det_code may change once we ack the counter array, so S_ENC must use
    // the snapshot taken in S_RONE_WAIT / S_RALL_WAIT).
    wire [47:0] enc = enc_dec(code_latch);
    wire [2:0]  dlen = enc[42:40];      // low 3 bits of len (len is 1..5)

    // separator after a count: newline for read_one / last read_all count, else space
    wire [7:0] code_sep = (read_all_mode && !code_is_last) ? 8'h20 : 8'h0A;

    wire tx_ready = (txst == 2'd0) && (tx_load == 1'b0);

    integer k;
    initial begin
        buf_idx        = 7'd0;
        line_ready     = 1'b0;
        fsm            = S_IDLE;
        parse_idx      = 7'd0;
        cmd_len        = 4'd0;
        cmd_id         = CMD_ERR;
        arg_count      = 3'd0;
        arg_idx        = 3'd0;
        current_num    = 32'd0;
        num_sign       = 1'b0;
        reply_ok       = 1'b1;
        reply_idx      = 3'd0;
        reply_len      = 3'd0;
        reply_start    = 3'd0;
        rall_loop      = 1'b0;
        code_is_last   = 1'b0;
        read_all_mode  = 1'b0;
        cal_max_code   = 16'd0;
        cal_max_det    = 6'd0;
        cal_em         = 6'd0;
        em_drive       = 1'b0;
        em_index       = 6'd0;
        em_duration_ms = 32'd0;
        pll_start      = 1'b0;
        pll_freq_khz   = 32'd0;
        det_read_one   = 1'b0;
        det_read_all   = 1'b0;
        det_clear      = 1'b0;
        det_site       = 6'd0;
        det_code_ack   = 1'b0;
        tx_din         = 8'd0;
        tx_load        = 1'b0;
        txst           = 2'd0;
        tx_byte        = 8'd0;
        tx_start       = 1'b0;
        pll_phase      = 12'd0;
        pll_rf_pwr     = 2'd0;
        mw_gate_ns     = 32'd0;
        mw_gate_load   = 1'b0;
        pll_busy_seen  = 1'b0;
        i2c_busy_seen  = 1'b0;
        i2c_start      = 1'b0;
        i2c_data       = 12'd0;
        for (k = 0; k < 64; k = k + 1) cal_lut[k] = 6'd0;
    end

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
                        tx_byte  <= tx_din;
                        tx_start <= 1'b1;
                        txst     <= 2'd1;
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
            fsm            <= S_IDLE;
            buf_idx        <= 7'd0;
            line_ready     <= 1'b0;
            parse_idx      <= 7'd0;
            cmd_len        <= 4'd0;
            cmd_id         <= CMD_ERR;
            arg_count      <= 3'd0;
            arg_idx        <= 3'd0;
            current_num    <= 32'd0;
            num_sign       <= 1'b0;
            reply_idx      <= 3'd0;
            reply_len      <= 3'd0;
            reply_start    <= 3'd0;
            // reply_mem only ever loads bytes [0..5] (max reply_len=6 for the
            // 5-digit decimal count + separator; "OK\n"=3, "ERR\n"=4). Zero the
            // upper two slots so the send FSM's reply_start+reply_idx index
            // (a 3-bit value the synthesizer can't bound statically) never
            // reads an undriven bit.
            reply_mem[6]   <= 8'h00;
            reply_mem[7]   <= 8'h00;
            rall_loop      <= 1'b0;
            code_is_last   <= 1'b0;
            read_all_mode  <= 1'b0;
            cal_em         <= 6'd0;
            em_drive       <= 1'b0;
            em_index       <= 6'd0;
            em_duration_ms <= 32'd0;
            pll_start      <= 1'b0;
            pll_freq_khz   <= 32'd0;
            det_read_one   <= 1'b0;
            det_read_all   <= 1'b0;
            det_clear      <= 1'b0;
            det_site       <= 6'd0;
            det_code_ack   <= 1'b0;
            code_latch     <= 16'd0;
            pll_phase      <= 12'd0;
            pll_rf_pwr     <= 2'd0;
            mw_gate_ns     <= 32'd0;
            mw_gate_load   <= 1'b0;
            pll_busy_seen  <= 1'b0;
            i2c_busy_seen  <= 1'b0;
            i2c_start      <= 1'b0;
            i2c_data       <= 12'd0;
            tx_load        <= 1'b0;
            tx_din         <= 8'd0;
        end else begin
            // Default one-cycle pulses
            tx_load        <= 1'b0;
            em_drive       <= 1'b0;
            pll_start      <= 1'b0;
            det_read_one   <= 1'b0;
            det_read_all   <= 1'b0;
            det_clear      <= 1'b0;
            det_code_ack   <= 1'b0;
            mw_gate_load   <= 1'b0;
            i2c_start      <= 1'b0;

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
                        // keyword complete; classify by length then exact match.
                        // READ_DETECTORS (14) checked before READ_DETECTOR (13).
                        if (cmd_len == 4'd14 &&
                            cmd_str[0]=="R" && cmd_str[1]=="E" && cmd_str[2]=="A" &&
                            cmd_str[3]=="D" && cmd_str[4]=="_" && cmd_str[5]=="D" &&
                            cmd_str[6]=="E" && cmd_str[7]=="T" && cmd_str[8]=="E" &&
                            cmd_str[9]=="C" && cmd_str[10]=="T" && cmd_str[11]=="O" &&
                            cmd_str[12]=="R" && cmd_str[13]=="S") begin
                            cmd_id <= CMD_READ_ALL; arg_count <= 3'd0;
                            fsm    <= S_DISPATCH;
                        end else if (cmd_len == 4'd15 &&
                            // CLEAR_DETECTORS (15 chars)
                            cmd_str[0]=="C" && cmd_str[1]=="L" && cmd_str[2]=="E" &&
                            cmd_str[3]=="A" && cmd_str[4]=="R" && cmd_str[5]=="_" &&
                            cmd_str[6]=="D" && cmd_str[7]=="E" && cmd_str[8]=="T" &&
                            cmd_str[9]=="E" && cmd_str[10]=="C" && cmd_str[11]=="T" &&
                            cmd_str[12]=="O" && cmd_str[13]=="R" && cmd_str[14]=="S") begin
                            cmd_id <= CMD_CLEAR; arg_count <= 3'd0;
                            fsm    <= S_DISPATCH;
                        end else if (cmd_len == 4'd13) begin
                            if (cmd_str[0]=="D" && cmd_str[1]=="R" && cmd_str[2]=="I" &&
                                cmd_str[3]=="V" && cmd_str[4]=="E" && cmd_str[5]=="_" &&
                                cmd_str[6]=="E" && cmd_str[7]=="M" && cmd_str[8]=="I" &&
                                cmd_str[9]=="T" && cmd_str[10]=="T" && cmd_str[11]=="E" &&
                                cmd_str[12]=="R") begin
                                cmd_id <= CMD_DRIVE; arg_count <= 3'd2;
                                if (!is_eol && ch == 8'h20) begin
                                    parse_idx <= parse_idx + 7'd1;
                                    fsm       <= S_ARGSP;
                                end else begin
                                    cmd_id <= CMD_ERR;   // DRIVE_EMITTER needs args
                                    fsm    <= S_DISPATCH;
                                end
                            end else if (cmd_str[0]=="R" && cmd_str[1]=="E" &&
                                         cmd_str[2]=="A" && cmd_str[3]=="D" &&
                                         cmd_str[4]=="_" && cmd_str[5]=="D" &&
                                         cmd_str[6]=="E" && cmd_str[7]=="T" &&
                                         cmd_str[8]=="E" && cmd_str[9]=="C" &&
                                         cmd_str[10]=="T" && cmd_str[11]=="O" &&
                                         cmd_str[12]=="R") begin
                                cmd_id <= CMD_READ_ONE; arg_count <= 3'd1;
                                if (!is_eol && ch == 8'h20) begin
                                    parse_idx <= parse_idx + 7'd1;
                                    fsm       <= S_ARGSP;
                                end else begin
                                    cmd_id <= CMD_ERR;   // READ_DETECTOR needs args
                                    fsm    <= S_DISPATCH;
                                end
                            end else if (cmd_str[0]=="S" && cmd_str[1]=="E" &&
                                         cmd_str[2]=="T" && cmd_str[3]=="_" &&
                                         cmd_str[4]=="T" && cmd_str[5]=="H" &&
                                         cmd_str[6]=="R" && cmd_str[7]=="E" &&
                                         cmd_str[8]=="S" && cmd_str[9]=="H" &&
                                         cmd_str[10]=="O" && cmd_str[11]=="L" &&
                                         cmd_str[12]=="D") begin
                                cmd_id <= CMD_THRESH; arg_count <= 3'd1;
                                if (!is_eol && ch == 8'h20) begin
                                    parse_idx <= parse_idx + 7'd1;
                                    fsm       <= S_ARGSP;
                                end else begin
                                    cmd_id <= CMD_ERR;   // SET_THRESHOLD needs args
                                    fsm    <= S_DISPATCH;
                                end
                            end else begin
                                cmd_id <= CMD_ERR;
                                fsm    <= S_DISPATCH;
                            end
                        end else if (cmd_len == 4'd11 &&
                            cmd_str[0]=="A" && cmd_str[1]=="P" && cmd_str[2]=="P" &&
                            cmd_str[3]=="L" && cmd_str[4]=="Y" && cmd_str[5]=="_" &&
                            cmd_str[6]=="P" && cmd_str[7]=="U" && cmd_str[8]=="L" &&
                            cmd_str[9]=="S" && cmd_str[10]=="E") begin
                            cmd_id <= CMD_PULSE; arg_count <= 3'd5;
                            if (!is_eol && ch == 8'h20) begin
                                parse_idx <= parse_idx + 7'd1;
                                fsm       <= S_ARGSP;
                            end else begin
                                cmd_id <= CMD_ERR;     // APPLY_PULSE needs args
                                fsm    <= S_DISPATCH;
                            end
                        end else if (cmd_len == 4'd9 &&
                            cmd_str[0]=="C" && cmd_str[1]=="A" && cmd_str[2]=="L" &&
                            cmd_str[3]=="I" && cmd_str[4]=="B" && cmd_str[5]=="R" &&
                            cmd_str[6]=="A" && cmd_str[7]=="T" && cmd_str[8]=="E") begin
                            cmd_id <= CMD_CALIBRATE; arg_count <= 3'd0;
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
                        if (arg_idx < arg_count) cmd_id <= CMD_ERR; // too few args
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
                    line_ready <= 1'b0;
                    buf_idx    <= 7'd0;
                    case (cmd_id)
                        CMD_DRIVE : begin
                            // index 0..63, duration_ms >= 0. Out-of-range index
                            // used to silently wrap; now reply ERR.
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) >= N_DET ||
                                $signed(arg_vals[1]) < 0) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                em_index       <= arg_vals[0][5:0];
                                em_duration_ms <= arg_vals[1];
                                em_drive       <= 1'b1;
                                reply_ok       <= 1'b1;
                                fsm            <= S_REPLY_LOAD;
                            end
                        end
                        CMD_PULSE : begin
                            // qubit 0..N_DET-1, freq_khz > 0, amp/duration >= 0.
                            // The ADF4351 takes the carrier frequency (arg 1),
                            // the pulse phase (arg 4 -> R1 PHASE) and a coarse
                            // output power (arg 2 -> R4 RF_PWR, 4 levels). The
                            // pulse duration (arg 3, ns) gates an RF switch via
                            // the mw_gate timer in top_level, armed once the PLL
                            // has finished programming the carrier. Reject
                            // negative / out-of-range args with ERR.
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) >= N_DET ||
                                $signed(arg_vals[1]) <= 0 ||
                                $signed(arg_vals[2]) < 0 ||
                                $signed(arg_vals[3]) < 0) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                pll_freq_khz <= arg_vals[1];
                                pll_phase    <= arg_vals[4][11:0];  // -> R1 PHASE
                                pll_rf_pwr   <= arg_vals[2][1:0];   // -> R4 RF_PWR
                                mw_gate_ns   <= arg_vals[3];       // -> gate timer
                                pll_start    <= 1'b1;
                                pll_busy_seen <= 1'b0;
                                fsm          <= S_PULSE_WAIT;     // wait PLL, then gate
                            end
                        end
                        CMD_READ_ONE : begin
                            // site must be 0..N_DET-1; used to wrap via [5:0].
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) >= N_DET) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                det_site      <= arg_vals[0][5:0];
                                det_read_one  <= 1'b1;
                                read_all_mode <= 1'b0;
                                fsm           <= S_RONE_WAIT;
                            end
                        end
                        CMD_READ_ALL : begin
                            det_read_all  <= 1'b1;
                            read_all_mode <= 1'b1;
                            rall_loop     <= 1'b0;
                            fsm           <= S_RALL_WAIT;
                        end
                        CMD_CLEAR : begin
                            det_clear <= 1'b1;     // 1-cycle pulse: zero all 64 counters
                            reply_ok  <= 1'b1;
                            fsm       <= S_REPLY_LOAD;
                        end
                        CMD_THRESH : begin
                            // threshold 0..4095 (12-bit MCP4725 DAC code)
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) > 32'd4095) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                i2c_data       <= arg_vals[0][11:0];
                                i2c_start      <= 1'b1;
                                i2c_busy_seen  <= 1'b0;
                                fsm            <= S_THRESH_WAIT;
                            end
                        end
                        CMD_CALIBRATE : begin
                            cal_em       <= 6'd0;
                            cal_max_code <= 16'd0;
                            cal_max_det  <= 6'd0;
                            fsm          <= S_CAL_DRIVE;
                        end
                        default : begin
                            reply_ok <= 1'b0;            // ERR
                            fsm      <= S_REPLY_LOAD;
                        end
                    endcase
                end

                // ---- wait for the single-channel count --------------------
                S_RONE_WAIT : begin
                    if (det_code_valid) begin
                        code_latch   <= det_code;  // snapshot before ack
                        det_code_ack <= 1'b1;     // release the counter array
                        fsm          <= S_ENC;
                    end
                end

                // ---- encode det_code to decimal reply_mem + separator ----
                S_ENC : begin
                    reply_mem[0]  <= enc[39:32]; // tenk
                    reply_mem[1]  <= enc[31:24]; // thou
                    reply_mem[2]  <= enc[23:16]; // hund
                    reply_mem[3]  <= enc[15:8];  // tens
                    reply_mem[4]  <= enc[7:0];   // ones
                    reply_mem[5]  <= code_sep;   // ' ' or '\n'
                    reply_len     <= dlen + 3'd1;
                    reply_start   <= 3'd5 - dlen; // 5 - digit_count
                    reply_idx     <= 3'd0;
                    fsm           <= S_REPLY_SEND;
                end

                // ---- wait for the next channel count (read_all) ----------
                S_RALL_WAIT : begin
                    if (det_code_valid) begin
                        code_latch   <= det_code;  // snapshot before ack
                        code_is_last <= (det_code_idx == (N_DET - 1));
                        det_code_ack <= 1'b1;     // release the sweep for the next
                        fsm          <= S_ENC;
                    end
                end

                // ---- Load OK/ERR reply string ----------------------------
                S_REPLY_LOAD : begin
                    if (reply_ok) begin
                        reply_mem[0] <= "O"; reply_mem[1] <= "K";
                        reply_mem[2] <= 8'h0A; reply_len <= 3'd3;
                    end else begin
                        reply_mem[0] <= "E"; reply_mem[1] <= "R";
                        reply_mem[2] <= "R"; reply_mem[3] <= 8'h0A;
                        reply_len <= 3'd4;
                    end
                    reply_start <= 3'd0;
                    reply_idx    <= 3'd0;
                    fsm          <= S_REPLY_SEND;
                end

                // ---- Send reply bytes ------------------------------------
                S_REPLY_SEND : begin
                    if (tx_ready) begin
                        if (reply_idx >= reply_len) begin
                            // finished this chunk
                            if (rall_loop) begin
                                rall_loop <= 1'b0;
                                fsm       <= S_RALL_WAIT;
                            end else begin
                                fsm <= S_IDLE;
                            end
                        end else begin
                            tx_load   <= 1'b1;
                            tx_din    <= reply_mem[reply_start + reply_idx];
                            reply_idx <= reply_idx + 3'd1;
                            // after sending the last byte of a read_all count,
                            // decide whether to loop for the next channel
                            if (read_all_mode && (reply_idx + 3'd1 == reply_len))
                                rall_loop <= !code_is_last;
                        end
                    end
                end

                // ---- Calibration: clear the 64 counters AND drive the next
                // emitter pixel in the same cycle (both are 1-cycle pulses).
                // Clearing before the pump means the read_all snapshot reflects
                // only the photons collected during this emitter's pump window.
                S_CAL_DRIVE : begin
                    det_clear      <= 1'b1;          // zero all 64 counters
                    em_index       <= cal_em;
                    em_duration_ms <= 32'd1;        // 1 ms pump pulse
                    em_drive       <= 1'b1;
                    cal_max_code   <= 16'd0;
                    cal_max_det    <= 6'd0;
                    fsm            <= S_CAL_WAIT_EM;
                end

                S_CAL_WAIT_EM : begin
                    if (!em_busy) begin
                        det_read_all <= 1'b1;
                        fsm          <= S_CAL_READ;
                    end
                end

                S_CAL_READ : begin
                    // read_all stream is in flight; capture the channel with the
                    // highest count for this emitter pixel and ACK each code so
                    // the counter array advances the sweep. Without the ack the
                    // controller stalls in S_WAIT_ACK and CALIBRATE hangs.
                    if (det_code_valid) begin
                        if (det_code > cal_max_code) begin
                            cal_max_code <= det_code;
                            cal_max_det  <= det_code_idx;
                        end
                        det_code_ack <= 1'b1;   // release this code, next channel
                    end
                    if (det_sweep_done) begin
                        fsm <= S_CAL_STORE;
                    end
                end

                S_CAL_STORE : begin
                    cal_lut[cal_em] <= cal_max_det;
                    if (cal_em >= 6'd63) begin
                        reply_ok <= 1'b1;
                        fsm      <= S_REPLY_LOAD;
                    end else begin
                        cal_em <= cal_em + 6'd1;
                        fsm    <= S_CAL_DRIVE;
                    end
                end

                // ---- APPLY_PULSE: wait for the PLL to finish programming the
                // carrier, then arm the RF-switch gate for the requested
                // duration and reply OK. Gating only after the PLL is idle means
                // the RF switch does not open onto a still-unlocked carrier.
                S_PULSE_WAIT : begin
                    if (pll_busy)        pll_busy_seen <= 1'b1;
                    if (pll_busy_seen && !pll_busy) begin
                        mw_gate_load <= 1'b1;
                        reply_ok     <= 1'b1;
                        fsm          <= S_REPLY_LOAD;
                    end
                end

                // ---- SET_THRESHOLD: wait for the I2C master to finish
                // writing the MCP4725 DAC code, then reply OK.
                S_THRESH_WAIT : begin
                    if (i2c_busy)        i2c_busy_seen <= 1'b1;
                    if (i2c_busy_seen && !i2c_busy) begin
                        reply_ok <= 1'b1;
                        fsm      <= S_REPLY_LOAD;
                    end
                end

                default : fsm <= S_IDLE;
            endcase
        end
    end
endmodule