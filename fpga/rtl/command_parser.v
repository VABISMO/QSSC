`timescale 1ns / 1ps
//============================================================================
// command_parser.v
// ----------------------------------------------------------------------------
// Text command parser for the QSSC UART protocol. Accepts bytes from
// uart_rx, buffers a line up to '\n', then parses and dispatches the parallel
// on-chip control/readout command set (no camera, no analog MUX):
//
//   DRIVE_EMITTER <index> <duration_ms>     -> pump one emitter at full, OK\n
//   SET_EMITTER <index> <level 0..4095>     -> arm emitter grayscale, OK\n
//   DRIVE_PATTERN <duration_ms>             -> fire ALL armed emitters in
//                                               parallel for duration_ms, OK\n
//   CLEAR_EMITTERS                         -> disarm all emitters (gs=0), OK\n
//   APPLY_PULSE <qubit> <freq_khz> <amp> <duration_ns> <phase>
//                                          -> program ADF4351 + gate RF, OK\n
//   READ_DETECTOR <site>                   -> read one of 64 AD7616 intensity
//                                               samples, reply <sample>\n
//   READ_DETECTORS                         -> stream all 64 samples,
//                                               reply <s0> <s1> ... <s63>\n
//   CLEAR_DETECTORS                        -> no-op on the ADC (no counters to
//                                               zero), reply OK\n
//   CALIBRATE                              -> for each emitter: pump, read_all,
//                                               store brightest detector
//                                               -> cal_lut[em], then OK\n
//   READ_RNG <n_bytes 1..256>             -> harvest von-Neumann-debiased AD7616
//                                               LSB-noise entropy from a dark
//                                               channel, reply "<hex> 0 0 0\n"
//                                               (raw_bits/kept/rate host-computed)
//
// The emitter commands drive a 3x TLC5947 constant-current LED driver over a
// 4-wire SPI: SET_EMITTER arms per-channel 12-bit grayscale, DRIVE_PATTERN
// fires every armed channel simultaneously (parallel multi-emitter), and
// DRIVE_EMITTER is the single-emitter convenience wrapper. CALIBRATE still
// pumps one emitter at a time (it must isolate which emitter maps to which
// detector), using DRIVE_EMITTER internally.
//
// The detector commands read 16-bit AD7616 intensity samples (0..65535, the
// digitised TIA output over the +/-5V bipolar range) from adc_reader -- NOT
// photon counts. The 1 kHz lock-in demodulation that turns these samples into
// a PL/ODMR signal is done host-side (mod_sync is the reference); the FPGA
// just delivers the raw parallel samples. The old SET_THRESHOLD / MCP4725
// comparator-threshold DAC command is GONE (the comparator chain was removed
// by the AD7616 pivot; the ADC range is HW-pin-strapped, so there is no
// threshold to program).
//
// Unknown / malformed commands reply "ERR\n". Numbers are decimal integers
// (optionally signed). Detector samples are 16-bit (0..65535) sent as ASCII
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
    // Emitter array (64 parallel CQD emitters via 3x TLC5947)
    output reg        em_cmd_drive,    // pump one emitter `index`, duration_ms
    output reg        em_cmd_set,      // set gs[index] = level
    output reg        em_cmd_pattern,  // fire all armed emitters, duration_ms
    output reg        em_cmd_clear,    // zero all 64 grayscale levels
    output reg [5:0]  em_index,
    output reg [11:0] em_level,
    output reg [31:0] em_duration_ms,
    input             em_busy,
    // ADF4351 PLL
    output reg        pll_start,
    output reg [31:0] pll_freq_khz,
    input             pll_busy,
    // ADC reader (64-ch parallel readout via 4x AD7616; same handshake the
    // photon_counter_array used, but `det_code` is a 16-bit intensity sample)
    output reg        det_read_one,
    output reg        det_read_all,
    output reg        det_clear,        // no-op on the ADC (no counters to clear)
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
    // QRNG (qrng_entropy): READ_RNG <n_bytes> harvests von-Neumann-debiased
    // AD7616 LSB-noise entropy and streams it as hex + a "0 0 0" stats footer
    // (raw_bits/kept/rate are host-computed; the hex token is what the API
    // consumes). harvest_en gates the extractor; rng_valid pulses per W-bit
    // word.
    output reg        rng_harvest_en,
    input      [31:0] rng_word,
    input             rng_valid
);
    // ---- Command IDs -------------------------------------------------------
    localparam [3:0] CMD_DRIVE     = 4'd0;   // DRIVE_EMITTER (2 args)
    localparam [3:0] CMD_PULSE     = 4'd1;   // APPLY_PULSE (5 args)
    localparam [3:0] CMD_READ_ONE  = 4'd2;   // READ_DETECTOR (1 arg)
    localparam [3:0] CMD_READ_ALL  = 4'd3;   // READ_DETECTORS (0 args)
    localparam [3:0] CMD_CALIBRATE = 4'd4;   // CALIBRATE (0 args)
    localparam [3:0] CMD_CLEAR     = 4'd5;   // CLEAR_DETECTORS (0 args, ADC no-op)
    localparam [3:0] CMD_ERR       = 4'd7;
    localparam [3:0] CMD_SET       = 4'd8;   // SET_EMITTER (2 args: index, level)
    localparam [3:0] CMD_PATTERN   = 4'd9;   // DRIVE_PATTERN (1 arg: duration_ms)
    localparam [3:0] CMD_CLEAR_EM  = 4'd10;  // CLEAR_EMITTERS (0 args)
    localparam [3:0] CMD_RNG       = 4'd11;  // READ_RNG (1 arg: n_bytes 1..256)

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
    // SET/CLEAR_EMITTER: wait for the TLC5947 load to finish, then reply OK.
    // (The 4-bit FSM was full at 0..15, so the state reg is widened to 5 bits
    //  and this state lives above the original range.)
    localparam [4:0] S_EM_WAIT       = 5'd16;
    localparam [4:0] S_RNG_HARVEST   = 5'd17;  // READ_RNG: wait for a qrng word
    localparam [4:0] S_RNG_EMIT      = 5'd18;  // READ_RNG: stream one hex nibble
    localparam [4:0] S_RNG_STATS     = 5'd19;  // READ_RNG: load " 0 0 0\n" footer

    // ---- Line buffer ------------------------------------------------------
    reg [7:0] cmd_buffer [0:63];
    reg [6:0] buf_idx;
    reg       line_ready;

    // ---- Parser registers --------------------------------------------------
    reg [4:0]  fsm;
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
                                // change once we ack the ADC reader sweep)

    // ---- Read-one / read-all mode flag ------------------------------------
    reg        read_all_mode;

    // ---- APPLY_PULSE / SET_EMITTER: wait for the peripheral -----------------
    reg        pll_busy_seen;
    reg        em_busy_seen;

    // ---- Calibration helpers ----------------------------------------------
    reg [15:0] cal_max_code;
    reg [5:0]  cal_max_det;
    reg [5:0]  cal_em;          // calibration emitter pixel 0..63

    // ---- READ_RNG harvest/stream state ------------------------------------
    reg [31:0] rng_word_buf;    // qrng word currently being streamed as hex
    reg [2:0]  nibble_idx;      // 0..7 which nibble of rng_word_buf is next
    reg [6:0]  rng_word_cnt;    // qrng words harvested so far this READ_RNG
    reg [6:0]  rng_need_words;  // ceil(n_bytes/4) words to harvest
    reg [2:0]  rng_last_bytes;  // 1..4 bytes emitted from the final word
    reg [8:0]  rng_n_bytes;     // 1..256 bytes requested

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

    // hex encode of a 4-bit nibble for the READ_RNG entropy stream (0-9, a-f).
    function [7:0] hex_nib;
        input [3:0] n;
        begin
            hex_nib = (n < 4'd10) ? (8'h30 + n) : (8'h61 + n - 8'd10);
        end
    endfunction

    // combinational decimal encoding of the latched detector sample (the live
    // det_code may change once we ack the ADC reader, so S_ENC must use
    // the snapshot taken in S_RONE_WAIT / S_RALL_WAIT).
    wire [47:0] enc = enc_dec(code_latch);
    wire [2:0]  dlen = enc[42:40];      // low 3 bits of len (len is 1..5)

    // separator after a count: newline for read_one / last read_all count, else space
    wire [7:0] code_sep = (read_all_mode && !code_is_last) ? 8'h20 : 8'h0A;

    wire tx_ready = (txst == 2'd0) && (tx_load == 1'b0);

    // READ_RNG hex stream: the current nibble of the buffered QRNG word
    // (nibble 0 = bits[31:28] ... nibble 7 = bits[3:0], MSB-first like the
    // extractor packs bits). The final word may emit fewer than 8 nibbles.
    wire [3:0] cur_nib        = rng_word_buf >> (4*(3'd7 - nibble_idx));
    wire [7:0] hex_char       = hex_nib(cur_nib);
    wire [3:0] nibs_this_word = (rng_word_cnt == rng_need_words - 7'd1)
                                ? (rng_last_bytes << 1) : 4'd8;

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
        em_cmd_drive   = 1'b0;
        em_cmd_set     = 1'b0;
        em_cmd_pattern = 1'b0;
        em_cmd_clear   = 1'b0;
        em_index       = 6'd0;
        em_level       = 12'd0;
        em_duration_ms = 32'd0;
        em_busy_seen   = 1'b0;
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
        rng_harvest_en = 1'b0;
        rng_word_buf   = 32'd0;
        nibble_idx     = 3'd0;
        rng_word_cnt   = 7'd0;
        rng_need_words = 7'd0;
        rng_last_bytes = 3'd0;
        rng_n_bytes    = 9'd0;
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
            em_cmd_drive   <= 1'b0;
            em_cmd_set     <= 1'b0;
            em_cmd_pattern <= 1'b0;
            em_cmd_clear   <= 1'b0;
            em_index       <= 6'd0;
            em_level       <= 12'd0;
            em_duration_ms <= 32'd0;
            em_busy_seen   <= 1'b0;
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
            rng_harvest_en <= 1'b0;
            rng_word_buf   <= 32'd0;
            nibble_idx     <= 3'd0;
            rng_word_cnt   <= 7'd0;
            tx_load        <= 1'b0;
            tx_din         <= 8'd0;
        end else begin
            // Default one-cycle pulses
            tx_load        <= 1'b0;
            em_cmd_drive   <= 1'b0;
            em_cmd_set     <= 1'b0;
            em_cmd_pattern <= 1'b0;
            em_cmd_clear   <= 1'b0;
            pll_start      <= 1'b0;
            det_read_one   <= 1'b0;
            det_read_all   <= 1'b0;
            det_clear      <= 1'b0;
            det_code_ack   <= 1'b0;
            mw_gate_load   <= 1'b0;

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
                        end else if (cmd_len == 4'd14 &&
                            // CLEAR_EMITTERS (14 chars: C L E A R _ E M I T T E R S)
                            cmd_str[0]=="C" && cmd_str[1]=="L" && cmd_str[2]=="E" &&
                            cmd_str[3]=="A" && cmd_str[4]=="R" && cmd_str[5]=="_" &&
                            cmd_str[6]=="E" && cmd_str[7]=="M" && cmd_str[8]=="I" &&
                            cmd_str[9]=="T" && cmd_str[10]=="T" && cmd_str[11]=="E" &&
                            cmd_str[12]=="R" && cmd_str[13]=="S") begin
                            cmd_id <= CMD_CLEAR_EM; arg_count <= 3'd0;
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
                            end else if (cmd_str[0]=="D" && cmd_str[1]=="R" &&
                                         cmd_str[2]=="I" && cmd_str[3]=="V" &&
                                         cmd_str[4]=="E" && cmd_str[5]=="_" &&
                                         cmd_str[6]=="P" && cmd_str[7]=="A" &&
                                         cmd_str[8]=="T" && cmd_str[9]=="T" &&
                                         cmd_str[10]=="E" && cmd_str[11]=="R" &&
                                         cmd_str[12]=="N") begin
                                // DRIVE_PATTERN (13 chars) -- parallel fire
                                cmd_id <= CMD_PATTERN; arg_count <= 3'd1;
                                if (!is_eol && ch == 8'h20) begin
                                    parse_idx <= parse_idx + 7'd1;
                                    fsm       <= S_ARGSP;
                                end else begin
                                    cmd_id <= CMD_ERR;   // DRIVE_PATTERN needs args
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
                            end else begin
                                cmd_id <= CMD_ERR;
                                fsm    <= S_DISPATCH;
                            end
                        end else if (cmd_len == 4'd11) begin
                            if (cmd_str[0]=="A" && cmd_str[1]=="P" && cmd_str[2]=="P" &&
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
                            end else if (cmd_str[0]=="S" && cmd_str[1]=="E" &&
                                         cmd_str[2]=="T" && cmd_str[3]=="_" &&
                                         cmd_str[4]=="E" && cmd_str[5]=="M" &&
                                         cmd_str[6]=="I" && cmd_str[7]=="T" &&
                                         cmd_str[8]=="T" && cmd_str[9]=="E" &&
                                         cmd_str[10]=="R") begin
                                // SET_EMITTER (11 chars) -- arm one emitter gs
                                cmd_id <= CMD_SET; arg_count <= 3'd2;
                                if (!is_eol && ch == 8'h20) begin
                                    parse_idx <= parse_idx + 7'd1;
                                    fsm       <= S_ARGSP;
                                end else begin
                                    cmd_id <= CMD_ERR;   // SET_EMITTER needs args
                                    fsm    <= S_DISPATCH;
                                end
                            end else begin
                                cmd_id <= CMD_ERR;
                                fsm    <= S_DISPATCH;
                            end
                        end else if (cmd_len == 4'd9 &&
                            cmd_str[0]=="C" && cmd_str[1]=="A" && cmd_str[2]=="L" &&
                            cmd_str[3]=="I" && cmd_str[4]=="B" && cmd_str[5]=="R" &&
                            cmd_str[6]=="A" && cmd_str[7]=="T" && cmd_str[8]=="E") begin
                            cmd_id <= CMD_CALIBRATE; arg_count <= 3'd0;
                            fsm    <= S_DISPATCH;
                        end else if (cmd_len == 4'd8 &&
                            // READ_RNG (8 chars): R E A D _ R N G
                            cmd_str[0]=="R" && cmd_str[1]=="E" && cmd_str[2]=="A" &&
                            cmd_str[3]=="D" && cmd_str[4]=="_" && cmd_str[5]=="R" &&
                            cmd_str[6]=="N" && cmd_str[7]=="G") begin
                            cmd_id <= CMD_RNG; arg_count <= 3'd1;
                            if (!is_eol && ch == 8'h20) begin
                                parse_idx <= parse_idx + 7'd1;
                                fsm       <= S_ARGSP;
                            end else begin
                                cmd_id <= CMD_ERR;   // READ_RNG needs n_bytes
                                fsm    <= S_DISPATCH;
                            end
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
                            // DRIVE_EMITTER <index> <duration_ms>: pump one
                            // emitter at full (gs=4095) for duration_ms. The
                            // pump runs in the background; reply OK now.
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) >= N_DET ||
                                $signed(arg_vals[1]) < 0) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                em_index       <= arg_vals[0][5:0];
                                em_duration_ms <= arg_vals[1];
                                em_cmd_drive   <= 1'b1;
                                reply_ok       <= 1'b1;
                                fsm            <= S_REPLY_LOAD;
                            end
                        end
                        CMD_SET : begin
                            // SET_EMITTER <index> <level 0..4095>: arm one
                            // emitter's 12-bit grayscale. Wait for the TLC5947
                            // load to finish before replying (serialized, like
                            // SET_THRESHOLD).
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) >= N_DET ||
                                $signed(arg_vals[1]) < 0 || $signed(arg_vals[1]) > 32'd4095) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                em_index     <= arg_vals[0][5:0];
                                em_level     <= arg_vals[1][11:0];
                                em_cmd_set   <= 1'b1;
                                em_busy_seen <= 1'b0;
                                fsm          <= S_EM_WAIT;
                            end
                        end
                        CMD_PATTERN : begin
                            // DRIVE_PATTERN <duration_ms>: fire every armed
                            // emitter (gs>0) in parallel for duration_ms. The
                            // pump runs in the background; reply OK now.
                            if ($signed(arg_vals[0]) < 0) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                em_duration_ms <= arg_vals[0];
                                em_cmd_pattern <= 1'b1;
                                reply_ok       <= 1'b1;
                                fsm            <= S_REPLY_LOAD;
                            end
                        end
                        CMD_CLEAR_EM : begin
                            // CLEAR_EMITTERS: zero all 64 grayscale levels.
                            em_cmd_clear   <= 1'b1;
                            em_busy_seen   <= 1'b0;
                            fsm            <= S_EM_WAIT;
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
                            // CLEAR_DETECTORS: the AD7616 has no counters to
                            // zero, so this is a no-op that just replies OK.
                            // (det_clear is pulsed for sim/API compatibility --
                            // adc_reader ignores it.)
                            det_clear <= 1'b1;
                            reply_ok  <= 1'b1;
                            fsm       <= S_REPLY_LOAD;
                        end
                        CMD_CALIBRATE : begin
                            cal_em       <= 6'd0;
                            cal_max_code <= 16'd0;
                            cal_max_det  <= 6'd0;
                            fsm          <= S_CAL_DRIVE;
                        end
                        CMD_RNG : begin
                            // READ_RNG <n_bytes 1..256>: harvest n_bytes of
                            // von-Neumann-debiased entropy from qrng_entropy and
                            // stream it as hex + a " 0 0 0\n" stats footer
                            // (raw_bits/kept/extract_rate are host-computed; the
                            // hex token is what the API consumes). ERR on
                            // out-of-range n_bytes.
                            if ($signed(arg_vals[0]) < 32'd1 ||
                                $signed(arg_vals[0]) > 32'd256) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                rng_n_bytes    <= arg_vals[0][8:0];
                                rng_need_words <= (arg_vals[0] + 32'd3) >> 2;
                                rng_last_bytes <= (arg_vals[0][1:0] == 2'd0)
                                                  ? 3'd4 : {1'b0, arg_vals[0][1:0]};
                                rng_word_cnt   <= 7'd0;
                                rng_harvest_en <= 1'b1;
                                rall_loop      <= 1'b0;
                                read_all_mode  <= 1'b0;
                                fsm            <= S_RNG_HARVEST;
                            end
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

                // ---- Calibration: drive the next emitter pixel, then read all
                // 64 ADC intensity samples and store the detector electrode with
                // the brightest response as cal_lut[emitter]. (det_clear is also
                // pulsed for sim/API compatibility -- the ADC has no counters to
                // zero, so it is a no-op on the reader; the read_all snapshot is
                // the latest free-running sample of each channel.)
                S_CAL_DRIVE : begin
                    det_clear      <= 1'b1;          // no-op on the ADC (compat)
                    em_index       <= cal_em;
                    em_duration_ms <= 32'd1;        // 1 ms pump pulse
                    em_cmd_drive   <= 1'b1;          // pump this one emitter (TLC5947)
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
                    // highest intensity sample for this emitter pixel and ACK each
                    // sample so the ADC reader advances the sweep. Without the ack
                    // the reader stalls in S_WAIT_ACK and CALIBRATE hangs.
                    if (det_code_valid) begin
                        if (det_code > cal_max_code) begin
                            cal_max_code <= det_code;
                            cal_max_det  <= det_code_idx;
                        end
                        det_code_ack <= 1'b1;   // release this sample, next channel
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

                // ---- SET_EMITTER / CLEAR_EMITTERS: wait for the TLC5947
                // grayscale load to finish, then reply OK. The busy-seen
                // guard avoids racing on a momentary !busy before the
                // emitter_array has asserted busy.
                S_EM_WAIT : begin
                    if (em_busy)        em_busy_seen <= 1'b1;
                    if (em_busy_seen && !em_busy) begin
                        reply_ok <= 1'b1;
                        fsm      <= S_REPLY_LOAD;
                    end
                end

                // ---- READ_RNG: harvest one qrng word at a time and stream it
                // as hex. harvest_en pauses while a word is being emitted so no
                // rng_valid pulse is missed (the extractor only accumulates
                // while harvest_en is high and we are watching in S_RNG_HARVEST).
                S_RNG_HARVEST : begin
                    rng_harvest_en <= 1'b1;
                    if (rng_valid) begin
                        rng_word_buf   <= rng_word;
                        nibble_idx     <= 3'd0;
                        rng_harvest_en <= 1'b0;   // pause extractor during emit
                        fsm            <= S_RNG_EMIT;
                    end
                end

                S_RNG_EMIT : begin
                    rng_harvest_en <= 1'b0;
                    if (tx_ready) begin
                        tx_load <= 1'b1;
                        tx_din  <= hex_char;
                        if (nibble_idx == nibs_this_word - 4'd1) begin
                            // emitted the last nibble of this word
                            rng_word_cnt <= rng_word_cnt + 7'd1;
                            if (rng_word_cnt == rng_need_words - 7'd1)
                                fsm <= S_RNG_STATS;
                            else
                                fsm <= S_RNG_HARVEST;   // re-assert harvest_en
                        end else begin
                            nibble_idx <= nibble_idx + 3'd1;
                        end
                    end
                end

                // READ_RNG footer: " 0 0 0\n" (raw_bits/kept/rate emitted as 0;
                // the host parses them best-effort and computes the real stats).
                S_RNG_STATS : begin
                    rng_harvest_en <= 1'b0;
                    reply_mem[0] <= 8'h20;  // ' '
                    reply_mem[1] <= 8'h30;  // '0'
                    reply_mem[2] <= 8'h20;  // ' '
                    reply_mem[3] <= 8'h30;  // '0'
                    reply_mem[4] <= 8'h20;  // ' '
                    reply_mem[5] <= 8'h30;  // '0'
                    reply_mem[6] <= 8'h0A;  // '\n'
                    reply_len    <= 3'd7;
                    reply_start  <= 3'd0;
                    reply_idx    <= 3'd0;
                    fsm          <= S_REPLY_SEND;
                end

                default : fsm <= S_IDLE;
            endcase
        end
    end
endmodule