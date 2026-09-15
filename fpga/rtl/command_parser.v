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
//                                               channel, reply "<hex> 00064 0 0\n"
//                                               (footer: ' ' + 5 zero-padded
//                                               decimal raw-sample count + " 0 0"
//                                               + LF; kept/rate host-derived)
//   ID  / STATUS / RESET / ODMR_SWEEP ... -> PR2 bring-up verbs (see below)
//   RUN_SEQUENCE <site> <kind 0..2> <n> <t_step_ns>
//                                          -> drive a Ramsey(0)/Hahn(1)/CPMG(2)
//                                               pulse sequence over n windows
//                                               (T = i*t_step), reply
//                                               "<n> <c0> ... <cN-1>\n" -- one
//                                               raw ADC code per optical read
//                                               window; the host fits T2/rate.
//                                               NO OK (the LF terminates it).
//   READ_LOCKIN <site> <n_cycles>          -> self-timed on/off lock-in, reply
//                                               "<sum_on> <sum_off> <n_on>
//                                               <n_off>\n" -- raw accumulations;
//                                               the host computes diff/sigma/SNR.
//                                               NO OK (the LF terminates it).
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
    output reg        pll_phase_only,   // 1 = R1-only phase step (no re-lock)
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
    // MW gate status (echo of top_level mw_gate). The pulse-sequence engine
    // watches this rise then fall to know each MW pulse has finished before
    // starting the free-evolution delay / next pulse (no overlap at T=0).
    input             mw_gate,
    // QRNG (qrng_entropy): READ_RNG <n_bytes> harvests von-Neumann-debiased
    // AD7616 LSB-noise entropy and streams it as hex + a " 00064 0 0\n" stats
    // footer (raw = samples_seen delta, the raw LSB samples this request fed
    // to the extractor, decimal; kept/rate are host-derived). harvest_en
    // gates the extractor, flush drops the partial pair/word on dispatch
    // (keeps kept <= raw/2 per request); rng_valid pulses per W-bit word.
    output reg        rng_harvest_en,
    output reg        rng_flush,
    input      [23:0] rng_samples_seen,  // running raw-sample counter (24-bit)
    input      [31:0] rng_word,
    input             rng_valid,
    // Gradient DAC (Invention 5 field-gradient coil current driver). The
    // MCP4921 12-bit DAC sets the coil current (I = Vdac / Rsense); a SET_GRADIENT
    // verb writes the 12-bit code and waits for the dac_ctrl SPI shift to finish
    // before replying OK (serialized, like SET_EMITTER). The DAC framing bits are
    // added in top_level; the parser only carries the 12-bit value.
    output reg [11:0] grad_dac_value,   // 12-bit MCP4921 code (0..4095)
    output reg        grad_dac_load,    // 1-cycle pulse: start a DAC write
    input             grad_dac_busy,    // dac_ctrl shift in progress
    // Soft reset (RESET verb): one-cycle pulse ORed into the peripheral reset
    // tree in top_level (emitter_array / adc_reader / adf4351_ctrl / qrng /
    // mw_gate timer / dac_ctrl). The parser itself is NOT reset by it, so it can
    // still reply OK after re-initialising the peripherals.
    output reg        soft_rst
);
    // ---- Command IDs -------------------------------------------------------
    // 5-bit: CMD_SWEEP=15 filled the old 4-bit space, so the id reg is widened
    // to [4:0] for the #58 pulse-engine verbs (CMD_SEQ/CMD_LOCKIN).
    localparam [4:0] CMD_DRIVE     = 5'd0;   // DRIVE_EMITTER (2 args)
    localparam [4:0] CMD_PULSE     = 5'd1;   // APPLY_PULSE (5 args)
    localparam [4:0] CMD_READ_ONE  = 5'd2;   // READ_DETECTOR (1 arg)
    localparam [4:0] CMD_READ_ALL  = 5'd3;   // READ_DETECTORS (0 args)
    localparam [4:0] CMD_CALIBRATE = 5'd4;   // CALIBRATE (0 args)
    localparam [4:0] CMD_CLEAR     = 5'd5;   // CLEAR_DETECTORS (0 args, ADC no-op)
    localparam [4:0] CMD_ERR       = 5'd7;
    localparam [4:0] CMD_SET       = 5'd8;   // SET_EMITTER (2 args: index, level)
    localparam [4:0] CMD_PATTERN   = 5'd9;   // DRIVE_PATTERN (1 arg: duration_ms)
    localparam [4:0] CMD_CLEAR_EM  = 5'd10;  // CLEAR_EMITTERS (0 args)
    localparam [4:0] CMD_RNG       = 5'd11;  // READ_RNG (1 arg: n_bytes 1..256)
    // PR2 bring-up / calibration verbs:
    localparam [4:0] CMD_ID        = 5'd12;  // ID -> "QSSC v1\n" (probe token)
    localparam [4:0] CMD_STATUS    = 5'd13;  // STATUS -> rails/PLL/ADC self-status
    localparam [4:0] CMD_RESET     = 5'd14;  // RESET -> soft-reset peripherals, OK
    localparam [4:0] CMD_SWEEP     = 5'd15;  // ODMR_SWEEP q f0 f1 n (4 args)
    // #58 pulse-engine verbs. READ_GYRO is NOT an RTL verb -- in hardware mode
    // the host runs RUN_SEQUENCE (Ramsey) and fits the rate, so no CMD_GYRO.
    localparam [4:0] CMD_SEQ       = 5'd16;  // RUN_SEQUENCE site kind n t_step (4 args)
    localparam [4:0] CMD_LOCKIN    = 5'd17;  // READ_LOCKIN site n_cycles (2 args)
    localparam [4:0] CMD_LOCKINP   = 5'd18;  // READ_LOCKINP site n_samples prime_idx (3 args, prime-period IQ lock-in)
    // Invention 5 field-gradient verb. SET_GRADIENT <code 0..4095> programs the
    // MCP4921 DAC that sets the gradient-coil current; ODMR_SWEEP then counts the
    // resolved NV lines = the per-pixel register width at that gradient.
    localparam [4:0] CMD_GRADIENT  = 5'd19;  // SET_GRADIENT code (1 arg)
    // Invention 4+5 — 64-parallel calibration sweep. Sweep the ADF4351 across
    // [f_start, f_stop] in n points; at each point gate the MW and read ALL 64
    // AD7616 channels -> one row of 64 codes per point. The host runs
    // decompose_odmr per column to count each pixel's resolved ODMR lines =
    // its calibrated register width. Reply = n rows of 64 space-separated
    // decimal codes (LF-terminated) + OK.
    localparam [4:0] CMD_ALL_SWEEP = 5'd20;  // RUN_ALL_ODMR f0 f1 n (3 args)

    // ---- FSM states --------------------------------------------------------
    // 7-bit: S_SWEEP_NEXT=29 plus the #58 pulse-engine states (30..51) exceed
    // 5 bits, so the fsm reg is widened to [6:0]. Existing encodings unchanged.
    localparam [6:0] S_IDLE          = 7'd0;
    localparam [6:0] S_KW            = 7'd1;
    localparam [6:0] S_ARGSP         = 7'd2;
    localparam [6:0] S_ARGNUM        = 7'd3;
    localparam [6:0] S_DISPATCH      = 7'd4;
    localparam [6:0] S_REPLY_LOAD    = 7'd5;   // load OK/ERR string
    localparam [6:0] S_REPLY_SEND    = 7'd6;   // stream reply_mem
    localparam [6:0] S_RONE_WAIT     = 7'd7;   // wait det_code_valid (read_one)
    localparam [6:0] S_ENC           = 7'd8;   // encode code -> decimal reply_mem
    localparam [6:0] S_RALL_WAIT     = 7'd9;   // wait det_code_valid (read_all)
    localparam [6:0] S_CAL_DRIVE     = 7'd10;  // calibrate: clear + drive emitter
    localparam [6:0] S_CAL_WAIT_EM   = 7'd11;  // calibrate: wait emitter !busy
    localparam [6:0] S_CAL_READ      = 7'd12;  // calibrate: capture max count
    localparam [6:0] S_CAL_STORE     = 7'd13;  // calibrate: store LUT, next pixel
    localparam [6:0] S_PULSE_WAIT    = 7'd14;  // APPLY_PULSE: wait PLL done, arm gate
    // SET/CLEAR_EMITTER: wait for the TLC5947 load to finish, then reply OK.
    localparam [6:0] S_EM_WAIT       = 7'd16;
    // SET_GRADIENT: wait for the dac_ctrl SPI shift to finish, then reply OK.
    localparam [6:0] S_GRAD_WAIT     = 7'd55;
    localparam [6:0] S_RNG_HARVEST   = 7'd17;  // READ_RNG: wait for a qrng word
    localparam [6:0] S_RNG_EMIT      = 7'd18;  // READ_RNG: stream one hex nibble
    localparam [6:0] S_RNG_STATS     = 7'd19;  // READ_RNG: build the stats footer
    localparam [6:0] S_RNG_TAIL      = 7'd20;  // READ_RNG: stream the stats footer
    // PR2 verbs (ID / STATUS / RESET / ODMR_SWEEP). All use the direct tx_load
    // streaming path (replies too long for the 8-byte reply_mem, like RNG_TAIL).
    localparam [6:0] S_ID_SEND       = 7'd21;  // ID: stream "QSSC v1\n"
    localparam [6:0] S_STATUS_SEND   = 7'd22;  // STATUS: stream the self-status line
    localparam [6:0] S_RESET_WAIT    = 7'd23;  // RESET: hold soft_rst a few cycles
    localparam [6:0] S_SWEEP_PROG    = 7'd24;  // ODMR_SWEEP: program PLL for point i
    localparam [6:0] S_SWEEP_PLL     = 7'd25;  // ODMR_SWEEP: wait PLL done, arm gate
    localparam [6:0] S_SWEEP_READ    = 7'd26;  // ODMR_SWEEP: read_one site q
    localparam [6:0] S_SWEEP_ENC     = 7'd27;  // ODMR_SWEEP: encode "f c\n" line
    localparam [6:0] S_SWEEP_SEND    = 7'd28;  // ODMR_SWEEP: stream the line
    localparam [6:0] S_SWEEP_NEXT    = 7'd29;  // ODMR_SWEEP: f += step; loop or finish
    // #58 RUN_SEQUENCE pulse engine (kind: 0=Ramsey, 1=Hahn, 2=CPMG). Per
    // window i: pi/2 gate -> free-precession delay T=i*t_step -> [pi gate ->
    // delay T] (Hahn/CPMG) -> pi/2 readout gate -> read ADC site -> stream
    // code. Reply: "<n> c0 c1 ... cN-1\n" then OK. The host fits T2/rate on the
    // real codes; the RTL only sequences MW gates + snapshots the ADC. CPMG is
    // one pi echo here (== Hahn in the RTL); the multi-echo decay model is
    // host-side. mw_gate_ns is set the cycle BEFORE mw_gate_load (the gate
    // timer latches gate_cnt=mw_gate_ns/10 on mw_gate_load), so each pulse
    // sets mw_gate_ns at the tail of the preceding state and fires
    // mw_gate_load on entry to the S_SEQ_PI* state -- no race.
    localparam [6:0] S_SEQ_INIT      = 7'd30;  // latch site/kind/n/tstep
    localparam [6:0] S_SEQ_COUNT_ENC = 7'd31;  // build "<n> " count prefix
    localparam [6:0] S_SEQ_COUNT_SEND= 7'd32;  // stream the 6-byte count prefix
    localparam [6:0] S_SEQ_PI2       = 7'd33;  // fire pi/2 gate (phase 0)
    localparam [6:0] S_SEQ_DLY1      = 7'd34;  // free-precession delay T
    localparam [6:0] S_SEQ_PI        = 7'd35;  // fire pi gate (Hahn/CPMG, phase 0)
    localparam [6:0] S_SEQ_DLY2      = 7'd36;  // free-precession delay T
    localparam [6:0] S_SEQ_PI2B      = 7'd37;  // fire pi/2 readout gate (phase 90 Ramsey)
    localparam [6:0] S_SEQ_READ      = 7'd38;  // read_one site, latch code
    localparam [6:0] S_SEQ_CODE_ENC  = 7'd39;  // build "<code><sep>" line
    localparam [6:0] S_SEQ_CODE_SEND = 7'd40;  // stream the 6-byte code line
    localparam [6:0] S_SEQ_NEXT      = 7'd41;  // i++, T += t_step; loop or OK
    // #58 READ_LOCKIN lock-in accumulator (self-timed on/off, independent of
    // the mod_sync period so it is sim-friendly). For n_cycles: MW on for a
    // half-period (gate) + read ADC into sum_on; MW off for a half-period +
    // read into sum_off. Reply: "<sum_on> <sum_off> <n_on> <n_off>\n" then OK
    // -- raw accumulations; the host computes diff/sigma/SNR (no RTL divide).
    localparam [6:0] S_LI_INIT       = 7'd42;  // latch site/n, zero accumulators
    localparam [6:0] S_LI_ON         = 7'd43;  // fire MW gate for the on half
    localparam [6:0] S_LI_ON_WAIT    = 7'd44;  // wait the on half-period
    localparam [6:0] S_LI_ON_READ    = 7'd45;  // read_one, sum_on += code
    localparam [6:0] S_LI_OFF        = 7'd46;  // start the off half (no gate)
    localparam [6:0] S_LI_OFF_WAIT   = 7'd47;  // wait the off half-period
    localparam [6:0] S_LI_OFF_READ   = 7'd48;  // read_one, sum_off += code
    localparam [6:0] S_LI_NEXT       = 7'd49;  // i++; loop or encode
    localparam [6:0] S_LI_ENC        = 7'd50;  // build the 44-byte accumulators line
    localparam [6:0] S_LI_SEND       = 7'd51;  // stream the accumulators line
    // PR4b: gate-rise-synchronized read + Ramsey R1-only phase write states.
    localparam [6:0] S_SEQ_PI2B_PLL  = 7'd52;  // Ramsey: wait R1-only PLL write, then gate+read
    localparam [6:0] S_SWEEP_GATE_WAIT=7'd53;  // ODMR: wait gate rise, then read
    localparam [6:0] S_SWEEP_GATE_FALL=7'd54;  // ODMR: wait gate fall, then encode
    // RUN_ALL_ODMR (Invention 4+5): same PLL-sweep + gate as ODMR_SWEEP, but
    // each point reads ALL 64 channels (one row) via the S_RALL_WAIT/S_ENC/
    // S_REPLY_SEND read_all path (asweep_mode reroutes the post-row target to
    // S_ASWEEP_GATE_FALL instead of S_IDLE).
    localparam [6:0] S_ASWEEP_PROG     = 7'd60;  // program PLL for point i
    localparam [6:0] S_ASWEEP_PLL      = 7'd61;  // wait PLL done, arm gate
    localparam [6:0] S_ASWEEP_GATE_WAIT= 7'd62;  // wait gate rise, then read_all
    localparam [6:0] S_ASWEEP_GATE_FALL= 7'd63;  // wait gate fall, then next
    localparam [6:0] S_ASWEEP_NEXT     = 7'd64;  // f += step; loop or finish OK
    // READ_LOCKINP cosine/sine IQ demodulator (prime-period lock-in). Replaces
    // the old on/off square-wave boxcar with a true NCO-cosine reference so the
    // odd-prime +-1/(p*q) cross-talk residual of a 2-sample square wave is gone
    // (residual now bounded by the 64-entry LUT quantization, ~1/64^2). The FSM
    // reads the ADC once per loop tick, multiplies by cos/sin of the NCO phase,
    // and accumulates signed I/Q. No MW gate -- this is a pure detector demod;
    // the host drives any modulation (APPLY_PULSE/DRIVE_PATTERN) separately.
    localparam [6:0] S_LIP_INIT       = 7'd65;  // zero I/Q/n/phase, latch args
    localparam [6:0] S_LIP_READ       = 7'd66;  // trigger read_one(site)
    localparam [6:0] S_LIP_WAIT       = 7'd67;  // wait det_code_valid (or wdog)
    localparam [6:0] S_LIP_MAC        = 7'd68;  // I+=code*cos, Q+=code*sin, phase+=inc
    localparam [6:0] S_LIP_ENC        = 7'd69;  // build the 45-byte "<I> <Q> <n>\n" line
    localparam [6:0] S_LIP_SEND       = 7'd70;  // stream the IQ line

    // ---- Line buffer ------------------------------------------------------
    reg [7:0] cmd_buffer [0:63];
    reg [6:0] buf_idx;
    reg       line_ready;

    // ---- Parser registers --------------------------------------------------
    reg [6:0]  fsm;
    reg [6:0]  parse_idx;
    reg [7:0]  cmd_str [0:15];
    reg [3:0]  cmd_len;
    reg [4:0]  cmd_id;
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
    // RUN_ALL_ODMR (Invention 4+5) mode: like read_all_mode but the 64-code
    // stream is one row of an n-point sweep, so after the last code S_REPLY_SEND
    // continues to the all-sweep gate-fall/next state instead of S_IDLE.
    reg        asweep_mode;

    // ---- APPLY_PULSE / SET_EMITTER: wait for the peripheral -----------------
    reg        pll_busy_seen;
    reg        em_busy_seen;
    reg        grad_busy_seen;

    // ---- Calibration helpers ----------------------------------------------
    reg [15:0] cal_max_code;
    reg [5:0]  cal_max_det;
    reg [5:0]  cal_em;          // calibration emitter pixel 0..63

    // ---- READ_RNG harvest/stream state ------------------------------------
    localparam [26:0] RNG_WATCHDOG_CYCLES = 27'd134_217_727;  // 2^27-1 = 1.34 s
    // @ 100 MHz. A healthy dark channel completes one 32-bit word well inside
    // this window even at the worst documented von-Neumann yield (~1 %: 32
    // kept bits need ~3200 raw samples = 0.3 s at the 10.8 kS/s channel-scan
    // rate, HARDWARE_DESIGN.md §15E); a stuck/degenerate LSB instead ERRs.
    reg [31:0] rng_word_buf;    // qrng word currently being streamed as hex
    reg [2:0]  nibble_idx;      // 0..7 which nibble of rng_word_buf is next
    reg [6:0]  rng_word_cnt;    // qrng words harvested so far this READ_RNG
    reg [6:0]  rng_need_words;  // ceil(n_bytes/4) words to harvest
    reg [2:0]  rng_last_bytes;  // 1..4 bytes emitted from the final word
    reg [8:0]  rng_n_bytes;     // 1..256 bytes requested
    reg [23:0] rng_raw_start;   // samples_seen when READ_RNG started (raw =
                                // rng_samples_seen - rng_raw_start; the 24-bit
                                // counter cannot wrap within one request, and
                                // the footer saturates the delta at 65535 —
                                // see the rng_raw_count wire below)
    reg [26:0] rng_watchdog;    // harvest inactivity timer -> ERR, not a hang
    reg [7:0]  rng_tail [0:10]; // footer bytes: ' ' d d d d d ' ' 0 ' ' 0 LF
    reg [3:0]  rng_tail_idx;    // footer streaming cursor (0..10)

    // ---- PR2 verbs: ID / STATUS / RESET / ODMR_SWEEP ----------------------
    // ID reply: "QSSC v1\n" (8 bytes) -- the FPGAConnection probe token.
    reg [7:0]  id_str [0:7];
    // STATUS reply template (79 bytes): the two single-digit dynamic fields
    // (PLL_LOCKED at idx 11, ADC_BUSY at idx 22) are patched at send time; the
    // rail voltages are static nominal mV (no voltage-monitor ADC is fitted)
    // and TEMP_C is a placeholder (no XADC in this build -- a future on-die
    // temperature readback replaces it; reported as a fixed 25 C nominal).
    reg [7:0]  status_tpl [0:78];
    localparam [6:0] STATUS_LEN      = 7'd79;
    localparam [6:0] STATUS_PLL_IDX  = 7'd11;  // "PLL_LOCKED <d>"
    localparam [6:0] STATUS_ADC_IDX  = 7'd22;  // "ADC_BUSY <d>"
    reg [7:0]  status_pll_dig;       // '0'/'1' latched at dispatch (!pll_busy)
    reg [7:0]  status_adc_dig;       // '0'/'1' latched at dispatch (det_busy)

    // ODMR_SWEEP loop state. f(i) = f_start + step*i with step = (f_stop -
    // f_start)/(n-1); implemented incrementally (f += step each point) so no
    // per-point multiply. q = detector site read per point. Each point streams
    // a fixed-width "fffffff ccccc\n" line (7-digit kHz + 5-digit ADC code).
    reg [5:0]  sweep_q;              // site/qubit index (0..63)
    reg [31:0] sweep_f;              // current point frequency, kHz
    reg [31:0] sweep_step;           // kHz per point (signed via $signed add)
    reg [31:0] sweep_n;              // number of points (1..256)
    reg [31:0] sweep_i;              // points completed
    reg [5:0]  sweep_len;            // bytes in the current line (14)
    reg [7:0]  sweep_line [0:15];    // one "f c\n" line buffer
    reg        sweep_pll_seen;       // pll_busy-seen guard for S_SWEEP_PLL

    // Generic string-stream cursor (shared by ID / STATUS / SWEEP senders).
    reg [6:0]  str_idx;
    // RESET soft-rst hold counter (a few cycles so the peripherals catch it).
    reg [2:0]  rst_hold;

    // ---- #58 RUN_SEQUENCE / READ_LOCKIN state -----------------------------
    // MW pulse durations (ns). PI_NS = defects.py gate_1q_ns = a full pi pulse;
    // PI2_NS is the pi/2 (half duration). The ADF4351 amplitude is fixed (R4
    // RF_PWR), so pi/2 vs pi is a duration ratio, set here.
    localparam [31:0] PI2_NS      = 32'd25;
    localparam [31:0] PI_NS       = 32'd50;
    // READ_LOCKIN self-timed on/off half-period (ns). 200 us >= the ~86 us
    // background scan, so the wait-for-fresh read_one sample lands within the
    // half that was just started. Independent of the 1 kHz mod_sync period so
    // the accumulator is sim-friendly; mod_sync stays the external lock-in
    // reference for other uses.
    localparam [31:0] LI_HALF_NS  = 32'd200_000;
    localparam [31:0] LI_HALF_CYC = LI_HALF_NS / 32'd10;   // 100 MHz clk cycles
    // ODMR_SWEEP RF gate per point (ns). 200 us >= the ~86 us scan so the
    // fresh sample is taken DURING the RF pulse, not outside it.
    localparam [31:0] ODMR_GATE_NS = 32'd200_000;
    // FSM watchdogs: if a gate-rise/fall or a det_code_valid does not arrive
    // within this many clk cycles, proceed with a sentinel so the FSM can never
    // hang on a stuck mw_gate or dead ADC (only reset otherwise escapes). 2 ms
    // covers the 200 us max gate + read latency with margin.
    localparam [31:0] GATE_WDOG_CYC = 32'd200_000;
    localparam [31:0] READ_WDOG_CYC = 32'd200_000;

    // RUN_SEQUENCE loop state. kind: 0=Ramsey, 1=Hahn, 2=CPMG. Per window i the
    // free-evolution time T = i*t_step_ns; seq_delay holds T in clk cycles and
    // grows by seq_dly_inc each window (no per-window multiply). seq_dcnt is
    // the countdown reload from seq_delay at each pulse.
    reg [5:0]  seq_site;
    reg [1:0]  seq_kind;
    reg [10:0] seq_n;           // windows 1..1024
    reg [10:0] seq_i;
    reg [31:0] seq_tstep;       // t_step_ns (arg)
    reg [31:0] seq_dly_inc;     // t_step_ns / 10 (clk cycles per window step)
    reg [31:0] seq_delay;       // T = i*seq_dly_inc (clk cycles) for this window
    reg [31:0] seq_dcnt;        // countdown for the current free-precession gap
    reg [7:0]  seq_line [0:7];  // 6-byte stream buffer: 5-digit dec + sep
    reg        seq_is_last;     // this code is the final window -> '\n' not ' '
    reg        gate_seen;       // mw_gate rose this pulse (high->low = done)
    reg [31:0] seq_wdog;        // FSM watchdog for gate/read waits (no-hang)

    // READ_LOCKIN accumulator state. Raw on/off sums + counts; the host derives
    // diff/sigma/SNR (no RTL divide/sqrt). li_dcnt is the on/off half countdown.
    reg [5:0]  li_site;
    reg [31:0] li_n;            // n_cycles 1..10000
    reg [31:0] li_i;
    reg [31:0] li_sum_on;
    reg [31:0] li_sum_off;
    reg [31:0] li_n_on;
    reg [31:0] li_n_off;
    reg [31:0] li_dcnt;
    reg [31:0] li_half_ns;      // on/off half-period (ns); default LI_HALF_NS,
                               // set by READ_LOCKINP for the prime-period lock-in
    reg [31:0] li_half_cyc;     // li_half_ns / 10 (100 MHz clk cycles)
    reg [7:0]  li_line [0:43];  // 44-byte line: 4x dec32_pad + 3 ' ' + '\n'

    // ---- READ_LOCKINP cosine/sine IQ demodulator state --------------------
    // I/Q are 48-bit signed MAC accumulators: each ADC sample (16-bit unsigned,
    // 0..65535) is multiplied by the 12-bit signed cos/sin (+-2047), giving a
    // 30-bit signed product; summed over up to 100000 samples -> < 2^44, so 48
    // bits is safe. The reference is EXACTLY periodic with period p (one of the
    // 16 system primes): a sample counter lip_cnt wraps at p (lip_p) and indexes
    // a per-prime cos/sin ROM. Exact periodicity is what makes two distinct-prime
    // references orthogonal over the LCM (to 12-bit rounding, ~0.02%), NOT the
    // +-1/(p*q) of the old 2-sample square-wave boxcar. The host supplies a 4-bit
    // prime_idx (0..15) selecting PRIME_SYMBOLS[prime_idx]; no RTL divide. n
    // counts samples taken.
    reg [5:0]          lip_site;        // detector channel 0..63
    reg [31:0]         lip_n_samples;   // total samples 1..100000
    reg [3:0]          lip_prime_idx;   // prime channel index 0..15
    reg [5:0]          lip_p;           // prime period (samples) = prime_tab[idx]
    reg [5:0]          lip_cnt;         // sample-in-period counter 0..p-1 (wraps)
    reg signed [47:0]  lip_I;           // in-phase  accumulator (Σ code·cos)
    reg signed [47:0]  lip_Q;           // quadrature accumulator (Σ code·sin)
    reg [31:0]         lip_n;           // samples taken
    reg [15:0]         lip_code;        // latched ADC code for the MAC
    reg [7:0]          lip_line [0:44]; // 45-byte IQ reply: <I 16> <Q 16> <n 10>

    wire [127:0] lip_I_dec = signed_dec48(lip_I);
    wire [127:0] lip_Q_dec = signed_dec48(lip_Q);
    wire [79:0]  lip_n_dec = dec32_pad(lip_n);

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

    // Convert the APPLY_PULSE phase argument (milliradians, signed; 0 = +X,
    // 3142 ~ pi, 6283 ~ 2*pi) into the ADF4351 R1 PHASE register value, which
    // is 12 bits over a full 360-degree turn (1 LSB ~ 0.088 deg, 4095 = 360).
    // The host sends physical milliradians on the wire; the FPGA must rescale
    // to the chip's phase-step units and wrap mod 2*pi -- writing mrad directly
    // (the old `arg_vals[4][11:0]`) mis-scaled every non-zero-phase pulse (a
    // 180-degree / 3142-mrad request was read back as ~276 degrees) and wrapped
    // values > 4095 to garbage. Negative angles fold into [0, 2*pi).
    function [11:0] mrad_to_phase;
        input signed [31:0] mrad;
        integer m;
        begin
            m = mrad % 6283;          // signed remainder, -6282..6282
            if (m < 0) m = m + 6283;  // -> 0..6282  (mod 2*pi in mrad)
            mrad_to_phase = (m * 4096) / 6283;  // -> 0..4095 phase steps
        end
    endfunction

    // 32-bit value -> 10 zero-padded ASCII decimal digits (ones at [7:0],
    // billions at [79:72]). Used for the ODMR_SWEEP frequency field (we take
    // the low 7 digits = kHz up to 9_999_999, well past the ADF4351 4.4 GHz
    // / 4_400_000 kHz ceiling). Pure combinational; synthesises to a chain of
    // divide-by-constant/mod-10 which Vivado folds into ROM/LUT.
    function [79:0] dec32_pad;
        input [31:0] v;
        reg [31:0] t;
        begin
            t = v;
            dec32_pad[7:0]    = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[15:8]   = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[23:16]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[31:24]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[39:32]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[47:40]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[55:48]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[63:56]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[71:64]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[79:72]  = 8'h30 + (t % 32'd10);
        end
    endfunction

    // 16-bit value -> 5 zero-padded ASCII decimal digits (ones at [7:0]).
    // Used for the ODMR_SWEEP ADC-code field (0..65535).
    function [39:0] dec5_pad;
        input [15:0] v;
        reg [15:0] t;
        begin
            t = v;
            dec5_pad[7:0]    = 8'h30 + (t % 16'd10);  t = t / 16'd10;
            dec5_pad[15:8]   = 8'h30 + (t % 16'd10);  t = t / 16'd10;
            dec5_pad[23:16]  = 8'h30 + (t % 16'd10);  t = t / 16'd10;
            dec5_pad[31:24]  = 8'h30 + (t % 16'd10);  t = t / 16'd10;
            dec5_pad[39:32]  = 8'h30 + (t % 16'd10);
        end
    endfunction

    // 48-bit SIGNED value -> 16 ASCII chars: sign ('-' or ' ') + 15 zero-padded
    // decimal digits (ones at the right). Used for the READ_LOCKINP I/Q
    // accumulators (signed MAC sums). Range +-2^47 ~ +-1.4e14 -> 15 digits.
    function [127:0] signed_dec48;
        input signed [47:0] v;
        integer i;
        reg [47:0] mag;
        reg [7:0]  sign_c;
        reg [7:0]  d [0:14];
        begin
            if (v < 48'sd0) begin sign_c = 8'h2D; mag = -v; end  // '-'
            else            begin sign_c = 8'h20; mag =  v; end  // ' ' pad
            for (i = 0; i < 15; i = i + 1) begin
                d[14 - i] = 8'h30 + (mag % 48'sd10);
                mag       = mag / 48'sd10;
            end
            signed_dec48 = {sign_c,
                            d[0], d[1], d[2], d[3], d[4], d[5], d[6],
                            d[7], d[8], d[9], d[10], d[11], d[12], d[13], d[14]};
        end
    endfunction

    // Per-prime cos/sin ROM for the READ_LOCKINP IQ demodulator. 12-bit signed
    // (range +-2047, ~1.0). For each of the 16 system primes p, a contiguous
    // block of p entries holds round(2047*cos(2*pi*i/p)) (cos_rom) and
    // round(2047*sin(2*pi*i/p)) (sin_rom), i=0..p-1. The demod indexes the block
    // for the selected prime at base prime_base[lip_prime_idx] + lip_cnt, where
    // lip_cnt wraps 0..p-1 -> the reference is EXACTLY periodic with period p.
    // Exact periodicity gives LCM orthogonality to 12-bit rounding (~0.02%),
    // ~75x better than the +-1/(p*q) odd-prime square-wave residual. prime_tab
    // maps idx->p (the wrap value); prime_base maps idx->ROM offset. Inlined
    // (no external .hex) so iverilog and Vivado ROM inference are self-contained.
    reg [5:0]          prime_tab  [0:15];   // PRIME_SYMBOLS[idx] = period p
    reg [8:0]          prime_base [0:15];   // cumulative ROM offset per prime
    reg signed [11:0]  cos_rom    [0:380];  // per-prime cos blocks
    reg signed [11:0]  sin_rom    [0:380];  // per-prime sin blocks
    initial begin
    // prime period (samples) for channel index 0..15 = PRIME_SYMBOLS
    prime_tab[ 0]=6'd2;    prime_tab[ 1]=6'd3;    prime_tab[ 2]=6'd5;    prime_tab[ 3]=6'd7;    prime_tab[ 4]=6'd11;    prime_tab[ 5]=6'd13;    prime_tab[ 6]=6'd17;    prime_tab[ 7]=6'd19;    prime_tab[ 8]=6'd23;    prime_tab[ 9]=6'd29;    prime_tab[10]=6'd31;    prime_tab[11]=6'd37;    prime_tab[12]=6'd41;    prime_tab[13]=6'd43;    prime_tab[14]=6'd47;    prime_tab[15]=6'd53;
    // cumulative base offset of each prime's cos/sin block in the ROM
    prime_base[ 0]=9'd0;    prime_base[ 1]=9'd2;    prime_base[ 2]=9'd5;    prime_base[ 3]=9'd10;    prime_base[ 4]=9'd17;    prime_base[ 5]=9'd28;    prime_base[ 6]=9'd41;    prime_base[ 7]=9'd58;    prime_base[ 8]=9'd77;    prime_base[ 9]=9'd100;    prime_base[10]=9'd129;    prime_base[11]=9'd160;    prime_base[12]=9'd197;    prime_base[13]=9'd238;    prime_base[14]=9'd281;    prime_base[15]=9'd328;
    // p=2 (entries 0..1)
    cos_rom[  0]=12'sd2047;  cos_rom[  1]=-12'sd2047;
    sin_rom[  0]=12'sd0;  sin_rom[  1]=12'sd0;
    // p=3 (entries 2..4)
    cos_rom[  2]=12'sd2047;  cos_rom[  3]=-12'sd1023;  cos_rom[  4]=-12'sd1024;
    sin_rom[  2]=12'sd0;  sin_rom[  3]=12'sd1773;  sin_rom[  4]=-12'sd1773;
    // p=5 (entries 5..9)
    cos_rom[  5]=12'sd2047;  cos_rom[  6]=12'sd633;  cos_rom[  7]=-12'sd1656;  cos_rom[  8]=-12'sd1656;  cos_rom[  9]=12'sd633;
    sin_rom[  5]=12'sd0;  sin_rom[  6]=12'sd1947;  sin_rom[  7]=12'sd1203;  sin_rom[  8]=-12'sd1203;  sin_rom[  9]=-12'sd1947;
    // p=7 (entries 10..16)
    cos_rom[ 10]=12'sd2047;  cos_rom[ 11]=12'sd1276;  cos_rom[ 12]=-12'sd456;  cos_rom[ 13]=-12'sd1844;  cos_rom[ 14]=-12'sd1844;  cos_rom[ 15]=-12'sd456;  cos_rom[ 16]=12'sd1276;
    sin_rom[ 10]=12'sd0;  sin_rom[ 11]=12'sd1600;  sin_rom[ 12]=12'sd1996;  sin_rom[ 13]=12'sd888;  sin_rom[ 14]=-12'sd888;  sin_rom[ 15]=-12'sd1996;  sin_rom[ 16]=-12'sd1600;
    // p=11 (entries 17..27)
    cos_rom[ 17]=12'sd2047;  cos_rom[ 18]=12'sd1722;  cos_rom[ 19]=12'sd850;  cos_rom[ 20]=-12'sd291;  cos_rom[ 21]=-12'sd1340;  cos_rom[ 22]=-12'sd1964;  cos_rom[ 23]=-12'sd1964;  cos_rom[ 24]=-12'sd1340;
    cos_rom[ 25]=-12'sd291;  cos_rom[ 26]=12'sd850;  cos_rom[ 27]=12'sd1722;
    sin_rom[ 17]=12'sd0;  sin_rom[ 18]=12'sd1107;  sin_rom[ 19]=12'sd1862;  sin_rom[ 20]=12'sd2026;  sin_rom[ 21]=12'sd1547;  sin_rom[ 22]=12'sd577;  sin_rom[ 23]=-12'sd577;  sin_rom[ 24]=-12'sd1547;
    sin_rom[ 25]=-12'sd2026;  sin_rom[ 26]=-12'sd1862;  sin_rom[ 27]=-12'sd1107;
    // p=13 (entries 28..40)
    cos_rom[ 28]=12'sd2047;  cos_rom[ 29]=12'sd1813;  cos_rom[ 30]=12'sd1163;  cos_rom[ 31]=12'sd247;  cos_rom[ 32]=-12'sd726;  cos_rom[ 33]=-12'sd1532;  cos_rom[ 34]=-12'sd1988;  cos_rom[ 35]=-12'sd1988;
    cos_rom[ 36]=-12'sd1532;  cos_rom[ 37]=-12'sd726;  cos_rom[ 38]=12'sd247;  cos_rom[ 39]=12'sd1163;  cos_rom[ 40]=12'sd1813;
    sin_rom[ 28]=12'sd0;  sin_rom[ 29]=12'sd951;  sin_rom[ 30]=12'sd1685;  sin_rom[ 31]=12'sd2032;  sin_rom[ 32]=12'sd1914;  sin_rom[ 33]=12'sd1357;  sin_rom[ 34]=12'sd490;  sin_rom[ 35]=-12'sd490;
    sin_rom[ 36]=-12'sd1357;  sin_rom[ 37]=-12'sd1914;  sin_rom[ 38]=-12'sd2032;  sin_rom[ 39]=-12'sd1685;  sin_rom[ 40]=-12'sd951;
    // p=17 (entries 41..57)
    cos_rom[ 41]=12'sd2047;  cos_rom[ 42]=12'sd1909;  cos_rom[ 43]=12'sd1513;  cos_rom[ 44]=12'sd912;  cos_rom[ 45]=12'sd189;  cos_rom[ 46]=-12'sd560;  cos_rom[ 47]=-12'sd1234;  cos_rom[ 48]=-12'sd1740;
    cos_rom[ 49]=-12'sd2012;  cos_rom[ 50]=-12'sd2012;  cos_rom[ 51]=-12'sd1740;  cos_rom[ 52]=-12'sd1234;  cos_rom[ 53]=-12'sd560;  cos_rom[ 54]=12'sd189;  cos_rom[ 55]=12'sd912;  cos_rom[ 56]=12'sd1513;
    cos_rom[ 57]=12'sd1909;
    sin_rom[ 41]=12'sd0;  sin_rom[ 42]=12'sd739;  sin_rom[ 43]=12'sd1379;  sin_rom[ 44]=12'sd1832;  sin_rom[ 45]=12'sd2038;  sin_rom[ 46]=12'sd1969;  sin_rom[ 47]=12'sd1634;  sin_rom[ 48]=12'sd1078;
    sin_rom[ 49]=12'sd376;  sin_rom[ 50]=-12'sd376;  sin_rom[ 51]=-12'sd1078;  sin_rom[ 52]=-12'sd1634;  sin_rom[ 53]=-12'sd1969;  sin_rom[ 54]=-12'sd2038;  sin_rom[ 55]=-12'sd1832;  sin_rom[ 56]=-12'sd1379;
    sin_rom[ 57]=-12'sd739;
    // p=19 (entries 58..76)
    cos_rom[ 58]=12'sd2047;  cos_rom[ 59]=12'sd1936;  cos_rom[ 60]=12'sd1615;  cos_rom[ 61]=12'sd1120;  cos_rom[ 62]=12'sd503;  cos_rom[ 63]=-12'sd169;  cos_rom[ 64]=-12'sd822;  cos_rom[ 65]=-12'sd1386;
    cos_rom[ 66]=-12'sd1800;  cos_rom[ 67]=-12'sd2019;  cos_rom[ 68]=-12'sd2019;  cos_rom[ 69]=-12'sd1800;  cos_rom[ 70]=-12'sd1386;  cos_rom[ 71]=-12'sd822;  cos_rom[ 72]=-12'sd169;  cos_rom[ 73]=12'sd503;
    cos_rom[ 74]=12'sd1120;  cos_rom[ 75]=12'sd1615;  cos_rom[ 76]=12'sd1936;
    sin_rom[ 58]=12'sd0;  sin_rom[ 59]=12'sd665;  sin_rom[ 60]=12'sd1257;  sin_rom[ 61]=12'sd1714;  sin_rom[ 62]=12'sd1984;  sin_rom[ 63]=12'sd2040;  sin_rom[ 64]=12'sd1875;  sin_rom[ 65]=12'sd1506;
    sin_rom[ 66]=12'sd974;  sin_rom[ 67]=12'sd337;  sin_rom[ 68]=-12'sd337;  sin_rom[ 69]=-12'sd974;  sin_rom[ 70]=-12'sd1506;  sin_rom[ 71]=-12'sd1875;  sin_rom[ 72]=-12'sd2040;  sin_rom[ 73]=-12'sd1984;
    sin_rom[ 74]=-12'sd1714;  sin_rom[ 75]=-12'sd1257;  sin_rom[ 76]=-12'sd665;
    // p=23 (entries 77..99)
    cos_rom[ 77]=12'sd2047;  cos_rom[ 78]=12'sd1971;  cos_rom[ 79]=12'sd1749;  cos_rom[ 80]=12'sd1397;  cos_rom[ 81]=12'sd942;  cos_rom[ 82]=12'sd416;  cos_rom[ 83]=-12'sd140;  cos_rom[ 84]=-12'sd685;
    cos_rom[ 85]=-12'sd1180;  cos_rom[ 86]=-12'sd1588;  cos_rom[ 87]=-12'sd1878;  cos_rom[ 88]=-12'sd2028;  cos_rom[ 89]=-12'sd2028;  cos_rom[ 90]=-12'sd1878;  cos_rom[ 91]=-12'sd1588;  cos_rom[ 92]=-12'sd1180;
    cos_rom[ 93]=-12'sd685;  cos_rom[ 94]=-12'sd140;  cos_rom[ 95]=12'sd416;  cos_rom[ 96]=12'sd942;  cos_rom[ 97]=12'sd1397;  cos_rom[ 98]=12'sd1749;  cos_rom[ 99]=12'sd1971;
    sin_rom[ 77]=12'sd0;  sin_rom[ 78]=12'sd552;  sin_rom[ 79]=12'sd1064;  sin_rom[ 80]=12'sd1496;  sin_rom[ 81]=12'sd1818;  sin_rom[ 82]=12'sd2004;  sin_rom[ 83]=12'sd2042;  sin_rom[ 84]=12'sd1929;
    sin_rom[ 85]=12'sd1672;  sin_rom[ 86]=12'sd1292;  sin_rom[ 87]=12'sd816;  sin_rom[ 88]=12'sd279;  sin_rom[ 89]=-12'sd279;  sin_rom[ 90]=-12'sd816;  sin_rom[ 91]=-12'sd1292;  sin_rom[ 92]=-12'sd1672;
    sin_rom[ 93]=-12'sd1929;  sin_rom[ 94]=-12'sd2042;  sin_rom[ 95]=-12'sd2004;  sin_rom[ 96]=-12'sd1818;  sin_rom[ 97]=-12'sd1496;  sin_rom[ 98]=-12'sd1064;  sin_rom[ 99]=-12'sd552;
    // p=29 (entries 100..128)
    cos_rom[100]=12'sd2047;  cos_rom[101]=12'sd1999;  cos_rom[102]=12'sd1858;  cos_rom[103]=12'sd1630;  cos_rom[104]=12'sd1325;  cos_rom[105]=12'sd959;  cos_rom[106]=12'sd548;  cos_rom[107]=12'sd111;
    cos_rom[108]=-12'sd331;  cos_rom[109]=-12'sd758;  cos_rom[110]=-12'sd1149;  cos_rom[111]=-12'sd1486;  cos_rom[112]=-12'sd1754;  cos_rom[113]=-12'sd1940;  cos_rom[114]=-12'sd2035;  cos_rom[115]=-12'sd2035;
    cos_rom[116]=-12'sd1940;  cos_rom[117]=-12'sd1754;  cos_rom[118]=-12'sd1486;  cos_rom[119]=-12'sd1149;  cos_rom[120]=-12'sd758;  cos_rom[121]=-12'sd331;  cos_rom[122]=12'sd111;  cos_rom[123]=12'sd548;
    cos_rom[124]=12'sd959;  cos_rom[125]=12'sd1325;  cos_rom[126]=12'sd1630;  cos_rom[127]=12'sd1858;  cos_rom[128]=12'sd1999;
    sin_rom[100]=12'sd0;  sin_rom[101]=12'sd440;  sin_rom[102]=12'sd860;  sin_rom[103]=12'sd1239;  sin_rom[104]=12'sd1560;  sin_rom[105]=12'sd1809;  sin_rom[106]=12'sd1972;  sin_rom[107]=12'sd2044;
    sin_rom[108]=12'sd2020;  sin_rom[109]=12'sd1902;  sin_rom[110]=12'sd1694;  sin_rom[111]=12'sd1408;  sin_rom[112]=12'sd1055;  sin_rom[113]=12'sd654;  sin_rom[114]=12'sd221;  sin_rom[115]=-12'sd221;
    sin_rom[116]=-12'sd654;  sin_rom[117]=-12'sd1055;  sin_rom[118]=-12'sd1408;  sin_rom[119]=-12'sd1694;  sin_rom[120]=-12'sd1902;  sin_rom[121]=-12'sd2020;  sin_rom[122]=-12'sd2044;  sin_rom[123]=-12'sd1972;
    sin_rom[124]=-12'sd1809;  sin_rom[125]=-12'sd1560;  sin_rom[126]=-12'sd1239;  sin_rom[127]=-12'sd860;  sin_rom[128]=-12'sd440;
    // p=31 (entries 129..159)
    cos_rom[129]=12'sd2047;  cos_rom[130]=12'sd2005;  cos_rom[131]=12'sd1881;  cos_rom[132]=12'sd1680;  cos_rom[133]=12'sd1410;  cos_rom[134]=12'sd1083;  cos_rom[135]=12'sd711;  cos_rom[136]=12'sd310;
    cos_rom[137]=-12'sd104;  cos_rom[138]=-12'sd513;  cos_rom[139]=-12'sd901;  cos_rom[140]=-12'sd1253;  cos_rom[141]=-12'sd1553;  cos_rom[142]=-12'sd1790;  cos_rom[143]=-12'sd1953;  cos_rom[144]=-12'sd2036;
    cos_rom[145]=-12'sd2036;  cos_rom[146]=-12'sd1953;  cos_rom[147]=-12'sd1790;  cos_rom[148]=-12'sd1553;  cos_rom[149]=-12'sd1253;  cos_rom[150]=-12'sd901;  cos_rom[151]=-12'sd513;  cos_rom[152]=-12'sd104;
    cos_rom[153]=12'sd310;  cos_rom[154]=12'sd711;  cos_rom[155]=12'sd1083;  cos_rom[156]=12'sd1410;  cos_rom[157]=12'sd1680;  cos_rom[158]=12'sd1881;  cos_rom[159]=12'sd2005;
    sin_rom[129]=12'sd0;  sin_rom[130]=12'sd412;  sin_rom[131]=12'sd807;  sin_rom[132]=12'sd1169;  sin_rom[133]=12'sd1484;  sin_rom[134]=12'sd1737;  sin_rom[135]=12'sd1920;  sin_rom[136]=12'sd2023;
    sin_rom[137]=12'sd2044;  sin_rom[138]=12'sd1982;  sin_rom[139]=12'sd1838;  sin_rom[140]=12'sd1619;  sin_rom[141]=12'sd1333;  sin_rom[142]=12'sd993;  sin_rom[143]=12'sd613;  sin_rom[144]=12'sd207;
    sin_rom[145]=-12'sd207;  sin_rom[146]=-12'sd613;  sin_rom[147]=-12'sd993;  sin_rom[148]=-12'sd1333;  sin_rom[149]=-12'sd1619;  sin_rom[150]=-12'sd1838;  sin_rom[151]=-12'sd1982;  sin_rom[152]=-12'sd2044;
    sin_rom[153]=-12'sd2023;  sin_rom[154]=-12'sd1920;  sin_rom[155]=-12'sd1737;  sin_rom[156]=-12'sd1484;  sin_rom[157]=-12'sd1169;  sin_rom[158]=-12'sd807;  sin_rom[159]=-12'sd412;
    // p=37 (entries 160..196)
    cos_rom[160]=12'sd2047;  cos_rom[161]=12'sd2018;  cos_rom[162]=12'sd1930;  cos_rom[163]=12'sd1787;  cos_rom[164]=12'sd1593;  cos_rom[165]=12'sd1352;  cos_rom[166]=12'sd1073;  cos_rom[167]=12'sd763;
    cos_rom[168]=12'sd431;  cos_rom[169]=12'sd87;  cos_rom[170]=-12'sd260;  cos_rom[171]=-12'sd599;  cos_rom[172]=-12'sd922;  cos_rom[173]=-12'sd1217;  cos_rom[174]=-12'sd1478;  cos_rom[175]=-12'sd1696;
    cos_rom[176]=-12'sd1865;  cos_rom[177]=-12'sd1981;  cos_rom[178]=-12'sd2040;  cos_rom[179]=-12'sd2040;  cos_rom[180]=-12'sd1981;  cos_rom[181]=-12'sd1865;  cos_rom[182]=-12'sd1696;  cos_rom[183]=-12'sd1478;
    cos_rom[184]=-12'sd1217;  cos_rom[185]=-12'sd922;  cos_rom[186]=-12'sd599;  cos_rom[187]=-12'sd260;  cos_rom[188]=12'sd87;  cos_rom[189]=12'sd431;  cos_rom[190]=12'sd763;  cos_rom[191]=12'sd1073;
    cos_rom[192]=12'sd1352;  cos_rom[193]=12'sd1593;  cos_rom[194]=12'sd1787;  cos_rom[195]=12'sd1930;  cos_rom[196]=12'sd2018;
    sin_rom[160]=12'sd0;  sin_rom[161]=12'sd346;  sin_rom[162]=12'sd682;  sin_rom[163]=12'sd998;  sin_rom[164]=12'sd1286;  sin_rom[165]=12'sd1537;  sin_rom[166]=12'sd1743;  sin_rom[167]=12'sd1899;
    sin_rom[168]=12'sd2001;  sin_rom[169]=12'sd2045;  sin_rom[170]=12'sd2030;  sin_rom[171]=12'sd1957;  sin_rom[172]=12'sd1828;  sin_rom[173]=12'sd1646;  sin_rom[174]=12'sd1416;  sin_rom[175]=12'sd1146;
    sin_rom[176]=12'sd843;  sin_rom[177]=12'sd516;  sin_rom[178]=12'sd174;  sin_rom[179]=-12'sd174;  sin_rom[180]=-12'sd516;  sin_rom[181]=-12'sd843;  sin_rom[182]=-12'sd1146;  sin_rom[183]=-12'sd1416;
    sin_rom[184]=-12'sd1646;  sin_rom[185]=-12'sd1828;  sin_rom[186]=-12'sd1957;  sin_rom[187]=-12'sd2030;  sin_rom[188]=-12'sd2045;  sin_rom[189]=-12'sd2001;  sin_rom[190]=-12'sd1899;  sin_rom[191]=-12'sd1743;
    sin_rom[192]=-12'sd1537;  sin_rom[193]=-12'sd1286;  sin_rom[194]=-12'sd998;  sin_rom[195]=-12'sd682;  sin_rom[196]=-12'sd346;
    // p=41 (entries 197..237)
    cos_rom[197]=12'sd2047;  cos_rom[198]=12'sd2023;  cos_rom[199]=12'sd1952;  cos_rom[200]=12'sd1834;  cos_rom[201]=12'sd1674;  cos_rom[202]=12'sd1475;  cos_rom[203]=12'sd1241;  cos_rom[204]=12'sd978;
    cos_rom[205]=12'sd692;  cos_rom[206]=12'sd390;  cos_rom[207]=12'sd78;  cos_rom[208]=-12'sd235;  cos_rom[209]=-12'sd542;  cos_rom[210]=-12'sd837;  cos_rom[211]=-12'sd1113;  cos_rom[212]=-12'sd1362;
    cos_rom[213]=-12'sd1579;  cos_rom[214]=-12'sd1760;  cos_rom[215]=-12'sd1899;  cos_rom[216]=-12'sd1993;  cos_rom[217]=-12'sd2041;  cos_rom[218]=-12'sd2041;  cos_rom[219]=-12'sd1993;  cos_rom[220]=-12'sd1899;
    cos_rom[221]=-12'sd1760;  cos_rom[222]=-12'sd1579;  cos_rom[223]=-12'sd1362;  cos_rom[224]=-12'sd1113;  cos_rom[225]=-12'sd837;  cos_rom[226]=-12'sd542;  cos_rom[227]=-12'sd235;  cos_rom[228]=12'sd78;
    cos_rom[229]=12'sd390;  cos_rom[230]=12'sd692;  cos_rom[231]=12'sd978;  cos_rom[232]=12'sd1241;  cos_rom[233]=12'sd1475;  cos_rom[234]=12'sd1674;  cos_rom[235]=12'sd1834;  cos_rom[236]=12'sd1952;
    cos_rom[237]=12'sd2023;
    sin_rom[197]=12'sd0;  sin_rom[198]=12'sd312;  sin_rom[199]=12'sd618;  sin_rom[200]=12'sd908;  sin_rom[201]=12'sd1178;  sin_rom[202]=12'sd1419;  sin_rom[203]=12'sd1628;  sin_rom[204]=12'sd1798;
    sin_rom[205]=12'sd1927;  sin_rom[206]=12'sd2010;  sin_rom[207]=12'sd2045;  sin_rom[208]=12'sd2033;  sin_rom[209]=12'sd1974;  sin_rom[210]=12'sd1868;  sin_rom[211]=12'sd1718;  sin_rom[212]=12'sd1528;
    sin_rom[213]=12'sd1302;  sin_rom[214]=12'sd1046;  sin_rom[215]=12'sd765;  sin_rom[216]=12'sd466;  sin_rom[217]=12'sd157;  sin_rom[218]=-12'sd157;  sin_rom[219]=-12'sd466;  sin_rom[220]=-12'sd765;
    sin_rom[221]=-12'sd1046;  sin_rom[222]=-12'sd1302;  sin_rom[223]=-12'sd1528;  sin_rom[224]=-12'sd1718;  sin_rom[225]=-12'sd1868;  sin_rom[226]=-12'sd1974;  sin_rom[227]=-12'sd2033;  sin_rom[228]=-12'sd2045;
    sin_rom[229]=-12'sd2010;  sin_rom[230]=-12'sd1927;  sin_rom[231]=-12'sd1798;  sin_rom[232]=-12'sd1628;  sin_rom[233]=-12'sd1419;  sin_rom[234]=-12'sd1178;  sin_rom[235]=-12'sd908;  sin_rom[236]=-12'sd618;
    sin_rom[237]=-12'sd312;
    // p=43 (entries 238..280)
    cos_rom[238]=12'sd2047;  cos_rom[239]=12'sd2025;  cos_rom[240]=12'sd1960;  cos_rom[241]=12'sd1853;  cos_rom[242]=12'sd1707;  cos_rom[243]=12'sd1525;  cos_rom[244]=12'sd1309;  cos_rom[245]=12'sd1066;
    cos_rom[246]=12'sd801;  cos_rom[247]=12'sd518;  cos_rom[248]=12'sd224;  cos_rom[249]=-12'sd75;  cos_rom[250]=-12'sd372;  cos_rom[251]=-12'sd661;  cos_rom[252]=-12'sd936;  cos_rom[253]=-12'sd1191;
    cos_rom[254]=-12'sd1421;  cos_rom[255]=-12'sd1620;  cos_rom[256]=-12'sd1785;  cos_rom[257]=-12'sd1912;  cos_rom[258]=-12'sd1998;  cos_rom[259]=-12'sd2042;  cos_rom[260]=-12'sd2042;  cos_rom[261]=-12'sd1998;
    cos_rom[262]=-12'sd1912;  cos_rom[263]=-12'sd1785;  cos_rom[264]=-12'sd1620;  cos_rom[265]=-12'sd1421;  cos_rom[266]=-12'sd1191;  cos_rom[267]=-12'sd936;  cos_rom[268]=-12'sd661;  cos_rom[269]=-12'sd372;
    cos_rom[270]=-12'sd75;  cos_rom[271]=12'sd224;  cos_rom[272]=12'sd518;  cos_rom[273]=12'sd801;  cos_rom[274]=12'sd1066;  cos_rom[275]=12'sd1309;  cos_rom[276]=12'sd1525;  cos_rom[277]=12'sd1707;
    cos_rom[278]=12'sd1853;  cos_rom[279]=12'sd1960;  cos_rom[280]=12'sd2025;
    sin_rom[238]=12'sd0;  sin_rom[239]=12'sd298;  sin_rom[240]=12'sd590;  sin_rom[241]=12'sd869;  sin_rom[242]=12'sd1129;  sin_rom[243]=12'sd1366;  sin_rom[244]=12'sd1573;  sin_rom[245]=12'sd1747;
    sin_rom[246]=12'sd1884;  sin_rom[247]=12'sd1980;  sin_rom[248]=12'sd2035;  sin_rom[249]=12'sd2046;  sin_rom[250]=12'sd2013;  sin_rom[251]=12'sd1937;  sin_rom[252]=12'sd1820;  sin_rom[253]=12'sd1665;
    sin_rom[254]=12'sd1474;  sin_rom[255]=12'sd1251;  sin_rom[256]=12'sd1002;  sin_rom[257]=12'sd731;  sin_rom[258]=12'sd445;  sin_rom[259]=12'sd149;  sin_rom[260]=-12'sd149;  sin_rom[261]=-12'sd445;
    sin_rom[262]=-12'sd731;  sin_rom[263]=-12'sd1002;  sin_rom[264]=-12'sd1251;  sin_rom[265]=-12'sd1474;  sin_rom[266]=-12'sd1665;  sin_rom[267]=-12'sd1820;  sin_rom[268]=-12'sd1937;  sin_rom[269]=-12'sd2013;
    sin_rom[270]=-12'sd2046;  sin_rom[271]=-12'sd2035;  sin_rom[272]=-12'sd1980;  sin_rom[273]=-12'sd1884;  sin_rom[274]=-12'sd1747;  sin_rom[275]=-12'sd1573;  sin_rom[276]=-12'sd1366;  sin_rom[277]=-12'sd1129;
    sin_rom[278]=-12'sd869;  sin_rom[279]=-12'sd590;  sin_rom[280]=-12'sd298;
    // p=47 (entries 281..327)
    cos_rom[281]=12'sd2047;  cos_rom[282]=12'sd2029;  cos_rom[283]=12'sd1974;  cos_rom[284]=12'sd1885;  cos_rom[285]=12'sd1761;  cos_rom[286]=12'sd1606;  cos_rom[287]=12'sd1423;  cos_rom[288]=12'sd1214;
    cos_rom[289]=12'sd984;  cos_rom[290]=12'sd736;  cos_rom[291]=12'sd475;  cos_rom[292]=12'sd205;  cos_rom[293]=-12'sd68;  cos_rom[294]=-12'sd340;  cos_rom[295]=-12'sd606;  cos_rom[296]=-12'sd862;
    cos_rom[297]=-12'sd1101;  cos_rom[298]=-12'sd1322;  cos_rom[299]=-12'sd1518;  cos_rom[300]=-12'sd1688;  cos_rom[301]=-12'sd1827;  cos_rom[302]=-12'sd1934;  cos_rom[303]=-12'sd2006;  cos_rom[304]=-12'sd2042;
    cos_rom[305]=-12'sd2042;  cos_rom[306]=-12'sd2006;  cos_rom[307]=-12'sd1934;  cos_rom[308]=-12'sd1827;  cos_rom[309]=-12'sd1688;  cos_rom[310]=-12'sd1518;  cos_rom[311]=-12'sd1322;  cos_rom[312]=-12'sd1101;
    cos_rom[313]=-12'sd862;  cos_rom[314]=-12'sd606;  cos_rom[315]=-12'sd340;  cos_rom[316]=-12'sd68;  cos_rom[317]=12'sd205;  cos_rom[318]=12'sd475;  cos_rom[319]=12'sd736;  cos_rom[320]=12'sd984;
    cos_rom[321]=12'sd1214;  cos_rom[322]=12'sd1423;  cos_rom[323]=12'sd1606;  cos_rom[324]=12'sd1761;  cos_rom[325]=12'sd1885;  cos_rom[326]=12'sd1974;  cos_rom[327]=12'sd2029;
    sin_rom[281]=12'sd0;  sin_rom[282]=12'sd273;  sin_rom[283]=12'sd541;  sin_rom[284]=12'sd799;  sin_rom[285]=12'sd1043;  sin_rom[286]=12'sd1269;  sin_rom[287]=12'sd1471;  sin_rom[288]=12'sd1648;
    sin_rom[289]=12'sd1795;  sin_rom[290]=12'sd1910;  sin_rom[291]=12'sd1991;  sin_rom[292]=12'sd2037;  sin_rom[293]=12'sd2046;  sin_rom[294]=12'sd2018;  sin_rom[295]=12'sd1955;  sin_rom[296]=12'sd1857;
    sin_rom[297]=12'sd1725;  sin_rom[298]=12'sd1563;  sin_rom[299]=12'sd1373;  sin_rom[300]=12'sd1158;  sin_rom[301]=12'sd923;  sin_rom[302]=12'sd671;  sin_rom[303]=12'sd408;  sin_rom[304]=12'sd137;
    sin_rom[305]=-12'sd137;  sin_rom[306]=-12'sd408;  sin_rom[307]=-12'sd671;  sin_rom[308]=-12'sd923;  sin_rom[309]=-12'sd1158;  sin_rom[310]=-12'sd1373;  sin_rom[311]=-12'sd1563;  sin_rom[312]=-12'sd1725;
    sin_rom[313]=-12'sd1857;  sin_rom[314]=-12'sd1955;  sin_rom[315]=-12'sd2018;  sin_rom[316]=-12'sd2046;  sin_rom[317]=-12'sd2037;  sin_rom[318]=-12'sd1991;  sin_rom[319]=-12'sd1910;  sin_rom[320]=-12'sd1795;
    sin_rom[321]=-12'sd1648;  sin_rom[322]=-12'sd1471;  sin_rom[323]=-12'sd1269;  sin_rom[324]=-12'sd1043;  sin_rom[325]=-12'sd799;  sin_rom[326]=-12'sd541;  sin_rom[327]=-12'sd273;
    // p=53 (entries 328..380)
    cos_rom[328]=12'sd2047;  cos_rom[329]=12'sd2033;  cos_rom[330]=12'sd1990;  cos_rom[331]=12'sd1919;  cos_rom[332]=12'sd1821;  cos_rom[333]=12'sd1698;  cos_rom[334]=12'sd1551;  cos_rom[335]=12'sd1382;
    cos_rom[336]=12'sd1193;  cos_rom[337]=12'sd988;  cos_rom[338]=12'sd769;  cos_rom[339]=12'sd540;  cos_rom[340]=12'sd302;  cos_rom[341]=12'sd61;  cos_rom[342]=-12'sd182;  cos_rom[343]=-12'sd422;
    cos_rom[344]=-12'sd656;  cos_rom[345]=-12'sd880;  cos_rom[346]=-12'sd1093;  cos_rom[347]=-12'sd1290;  cos_rom[348]=-12'sd1469;  cos_rom[349]=-12'sd1627;  cos_rom[350]=-12'sd1763;  cos_rom[351]=-12'sd1873;
    cos_rom[352]=-12'sd1958;  cos_rom[353]=-12'sd2015;  cos_rom[354]=-12'sd2043;  cos_rom[355]=-12'sd2043;  cos_rom[356]=-12'sd2015;  cos_rom[357]=-12'sd1958;  cos_rom[358]=-12'sd1873;  cos_rom[359]=-12'sd1763;
    cos_rom[360]=-12'sd1627;  cos_rom[361]=-12'sd1469;  cos_rom[362]=-12'sd1290;  cos_rom[363]=-12'sd1093;  cos_rom[364]=-12'sd880;  cos_rom[365]=-12'sd656;  cos_rom[366]=-12'sd422;  cos_rom[367]=-12'sd182;
    cos_rom[368]=12'sd61;  cos_rom[369]=12'sd302;  cos_rom[370]=12'sd540;  cos_rom[371]=12'sd769;  cos_rom[372]=12'sd988;  cos_rom[373]=12'sd1193;  cos_rom[374]=12'sd1382;  cos_rom[375]=12'sd1551;
    cos_rom[376]=12'sd1698;  cos_rom[377]=12'sd1821;  cos_rom[378]=12'sd1919;  cos_rom[379]=12'sd1990;  cos_rom[380]=12'sd2033;
    sin_rom[328]=12'sd0;  sin_rom[329]=12'sd242;  sin_rom[330]=12'sd481;  sin_rom[331]=12'sd713;  sin_rom[332]=12'sd935;  sin_rom[333]=12'sd1144;  sin_rom[334]=12'sd1336;  sin_rom[335]=12'sd1510;
    sin_rom[336]=12'sd1663;  sin_rom[337]=12'sd1793;  sin_rom[338]=12'sd1897;  sin_rom[339]=12'sd1975;  sin_rom[340]=12'sd2025;  sin_rom[341]=12'sd2046;  sin_rom[342]=12'sd2039;  sin_rom[343]=12'sd2003;
    sin_rom[344]=12'sd1939;  sin_rom[345]=12'sd1848;  sin_rom[346]=12'sd1731;  sin_rom[347]=12'sd1590;  sin_rom[348]=12'sd1426;  sin_rom[349]=12'sd1242;  sin_rom[350]=12'sd1041;  sin_rom[351]=12'sd825;
    sin_rom[352]=12'sd598;  sin_rom[353]=12'sd362;  sin_rom[354]=12'sd121;  sin_rom[355]=-12'sd121;  sin_rom[356]=-12'sd362;  sin_rom[357]=-12'sd598;  sin_rom[358]=-12'sd825;  sin_rom[359]=-12'sd1041;
    sin_rom[360]=-12'sd1242;  sin_rom[361]=-12'sd1426;  sin_rom[362]=-12'sd1590;  sin_rom[363]=-12'sd1731;  sin_rom[364]=-12'sd1848;  sin_rom[365]=-12'sd1939;  sin_rom[366]=-12'sd2003;  sin_rom[367]=-12'sd2039;
    sin_rom[368]=-12'sd2046;  sin_rom[369]=-12'sd2025;  sin_rom[370]=-12'sd1975;  sin_rom[371]=-12'sd1897;  sin_rom[372]=-12'sd1793;  sin_rom[373]=-12'sd1663;  sin_rom[374]=-12'sd1510;  sin_rom[375]=-12'sd1336;
    sin_rom[376]=-12'sd1144;  sin_rom[377]=-12'sd935;  sin_rom[378]=-12'sd713;  sin_rom[379]=-12'sd481;  sin_rom[380]=-12'sd242;
    end

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

    // READ_RNG footer: decimal encoding of this request's raw-sample delta.
    // enc_dec is 16-bit (5 digits), so a delta above 65535 — a 256-byte
    // request at a sub-~6% von-Neumann yield consumes more than 2^16 raw
    // samples — saturates the footer at "65535": a conservative lower bound
    // that still keeps kept <= raw/2 checkable host-side (kept <= 2048).
    // enc_dec returns {len, tenk..ones} with the digits already ASCII.
    wire [23:0] rng_raw_delta = rng_samples_seen - rng_raw_start;
    wire [15:0] rng_raw_count = (rng_raw_delta > 24'd65535)
                                ? 16'hFFFF : rng_raw_delta[15:0];
    wire [47:0] enc_raw       = enc_dec(rng_raw_count);

    // ODMR_SWEEP line encode: 7-digit zero-padded kHz + ' ' + 5-digit zero-
    // padded ADC code + '\n' = 14 bytes. dec32_pad gives 10 digits (ones at
    // [7:0]); the kHz field takes the low 7 (positions 6..0 -> [55:8]).
    wire [79:0] sweep_f_pad = dec32_pad(sweep_f);
    wire [39:0] sweep_c_pad = dec5_pad(code_latch);

    // RUN_SEQUENCE encode wires. The count prefix is dec5_pad(seq_n); each code
    // is dec5_pad(code_latch); the separator is ' ' between codes, '\n' after
    // the last, then an "OK\n" follows via S_REPLY_LOAD.
    wire [39:0] seq_count_pad = dec5_pad({5'd0, seq_n});
    wire [39:0] seq_code_pad  = dec5_pad(code_latch);
    wire [7:0]  seq_sep       = seq_is_last ? 8'h0A : 8'h20;
    // READ_LOCKIN encode wires: the 4 raw accumulations, each dec32_pad (10
    // zero-padded digits), space-separated, newline-terminated.
    wire [79:0] li_sum_on_pad  = dec32_pad(li_sum_on);
    wire [79:0] li_sum_off_pad = dec32_pad(li_sum_off);
    wire [79:0] li_n_on_pad    = dec32_pad(li_n_on);
    wire [79:0] li_n_off_pad   = dec32_pad(li_n_off);

    // STATUS send byte: stream the template, patching the two dynamic digit
    // slots (PLL_LOCKED / ADC_BUSY) at their fixed indices.
    wire [7:0] status_byte = (str_idx == STATUS_PLL_IDX) ? status_pll_dig :
                             (str_idx == STATUS_ADC_IDX) ? status_adc_dig :
                             status_tpl[str_idx[6:0]];

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
        asweep_mode    = 1'b0;
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
        grad_busy_seen = 1'b0;
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
        pll_phase_only = 1'b0;
        mw_gate_ns     = 32'd0;
        mw_gate_load   = 1'b0;
        grad_dac_value = 12'd0;
        grad_dac_load  = 1'b0;
        pll_busy_seen  = 1'b0;
        rng_harvest_en = 1'b0;
        rng_flush      = 1'b0;
        rng_word_buf   = 32'd0;
        nibble_idx     = 3'd0;
        rng_word_cnt   = 7'd0;
        rng_need_words = 7'd0;
        rng_last_bytes = 3'd0;
        rng_n_bytes    = 9'd0;
        rng_raw_start  = 24'd0;
        rng_watchdog   = 27'd0;
        rng_tail_idx   = 4'd0;
        // PR2 verb initial state
        soft_rst        = 1'b0;
        str_idx         = 7'd0;
        rst_hold        = 3'd0;
        status_pll_dig  = 8'h30;
        status_adc_dig  = 8'h30;
        sweep_q         = 6'd0;
        sweep_f         = 32'd0;
        sweep_step      = 32'd0;
        sweep_n         = 32'd0;
        sweep_i         = 32'd0;
        sweep_len       = 6'd0;
        sweep_pll_seen  = 1'b0;
        // #58 pulse-engine / lock-in initial state
        seq_site      = 6'd0;
        seq_kind      = 2'd0;
        seq_n         = 11'd0;
        seq_i         = 11'd0;
        seq_tstep     = 32'd0;
        seq_dly_inc   = 32'd0;
        seq_delay     = 32'd0;
        seq_dcnt      = 32'd0;
        seq_is_last   = 1'b0;
        gate_seen     = 1'b0;
        seq_wdog      = 32'd0;
        li_site       = 6'd0;
        li_n          = 32'd0;
        li_i          = 32'd0;
        li_sum_on     = 32'd0;
        li_sum_off    = 32'd0;
        li_n_on       = 32'd0;
        li_n_off      = 32'd0;
        li_dcnt       = 32'd0;
        li_half_ns    = LI_HALF_NS;
        li_half_cyc   = LI_HALF_CYC;
        // READ_LOCKINP IQ demodulator defaults
        lip_site      = 6'd0;
        lip_n_samples = 32'd0;
        lip_prime_idx = 4'd0;
        lip_p         = 6'd0;
        lip_cnt       = 6'd0;
        lip_I         = 48'sd0;
        lip_Q         = 48'sd0;
        lip_n         = 32'd0;
        lip_code      = 16'd0;
        // ID reply string "QSSC v1\n"
        id_str[0]="Q"; id_str[1]="S"; id_str[2]="S"; id_str[3]="C"; id_str[4]=" "; id_str[5]="v"; id_str[6]="1"; id_str[7]=8'h0A;
        // STATUS template (79 bytes; digits at idx 11/22 patched at send time)
        status_tpl[0]="P"; status_tpl[1]="L"; status_tpl[2]="L"; status_tpl[3]="_";
        status_tpl[4]="L"; status_tpl[5]="O"; status_tpl[6]="C"; status_tpl[7]="K";
        status_tpl[8]="E"; status_tpl[9]="D"; status_tpl[10]=" "; status_tpl[11]="1";
        status_tpl[12]=" "; status_tpl[13]="A"; status_tpl[14]="D"; status_tpl[15]="C";
        status_tpl[16]="_"; status_tpl[17]="B"; status_tpl[18]="U"; status_tpl[19]="S";
        status_tpl[20]="Y"; status_tpl[21]=" "; status_tpl[22]="0"; status_tpl[23]=" ";
        status_tpl[24]="V"; status_tpl[25]="C"; status_tpl[26]="C"; status_tpl[27]="3";
        status_tpl[28]="V"; status_tpl[29]="3"; status_tpl[30]="_"; status_tpl[31]="m";
        status_tpl[32]="v"; status_tpl[33]=" "; status_tpl[34]="3"; status_tpl[35]="3";
        status_tpl[36]="0"; status_tpl[37]="0"; status_tpl[38]=" "; status_tpl[39]="V";
        status_tpl[40]="C"; status_tpl[41]="C"; status_tpl[42]="5"; status_tpl[43]="V";
        status_tpl[44]="_"; status_tpl[45]="m"; status_tpl[46]="v"; status_tpl[47]=" ";
        status_tpl[48]="4"; status_tpl[49]="9"; status_tpl[50]="8"; status_tpl[51]="0";
        status_tpl[52]=" "; status_tpl[53]="V"; status_tpl[54]="C"; status_tpl[55]="C";
        status_tpl[56]="1"; status_tpl[57]="2"; status_tpl[58]="V"; status_tpl[59]="_";
        status_tpl[60]="m"; status_tpl[61]="v"; status_tpl[62]=" "; status_tpl[63]="1";
        status_tpl[64]="2"; status_tpl[65]="0"; status_tpl[66]="5"; status_tpl[67]="0";
        status_tpl[68]=" "; status_tpl[69]="T"; status_tpl[70]="E"; status_tpl[71]="M";
        status_tpl[72]="P"; status_tpl[73]="_"; status_tpl[74]="C"; status_tpl[75]=" ";
        status_tpl[76]="2"; status_tpl[77]="5"; status_tpl[78]=8'h0A;
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
            grad_busy_seen <= 1'b0;
            pll_start      <= 1'b0;
            pll_freq_khz   <= 32'd0;
            det_read_one   <= 1'b0;
            det_read_all   <= 1'b0;
            asweep_mode    <= 1'b0;
            det_clear      <= 1'b0;
            det_site       <= 6'd0;
            det_code_ack   <= 1'b0;
            code_latch     <= 16'd0;
            pll_phase      <= 12'd0;
            pll_rf_pwr     <= 2'd0;
            pll_phase_only <= 1'b0;
            mw_gate_ns     <= 32'd0;
            mw_gate_load   <= 1'b0;
            grad_dac_value <= 12'd0;
            grad_dac_load  <= 1'b0;
            pll_busy_seen  <= 1'b0;
            rng_harvest_en <= 1'b0;
            rng_flush      <= 1'b0;
            rng_word_buf   <= 32'd0;
            nibble_idx     <= 3'd0;
            rng_word_cnt   <= 7'd0;
            rng_raw_start  <= 24'd0;
            rng_watchdog   <= 27'd0;
            rng_tail_idx   <= 4'd0;
            tx_load        <= 1'b0;
            tx_din         <= 8'd0;
            // PR2 verb state
            soft_rst       <= 1'b0;
            rst_hold       <= 3'd0;
            str_idx        <= 7'd0;
            status_pll_dig <= 8'h30;
            status_adc_dig <= 8'h30;
            sweep_q        <= 6'd0;
            sweep_f        <= 32'd0;
            sweep_step     <= 32'd0;
            sweep_n        <= 32'd0;
            sweep_i        <= 32'd0;
            sweep_pll_seen <= 1'b0;
            sweep_len      <= 6'd0;
            // #58 pulse-engine / lock-in reset state
            seq_site       <= 6'd0;
            seq_kind       <= 2'd0;
            seq_n          <= 11'd0;
            seq_i          <= 11'd0;
            seq_tstep      <= 32'd0;
            seq_dly_inc    <= 32'd0;
            seq_delay      <= 32'd0;
            seq_dcnt       <= 32'd0;
            seq_is_last    <= 1'b0;
            gate_seen      <= 1'b0;
            seq_wdog       <= 32'd0;
            li_site        <= 6'd0;
            li_n           <= 32'd0;
            li_i           <= 32'd0;
            li_sum_on      <= 32'd0;
            li_sum_off     <= 32'd0;
            li_n_on        <= 32'd0;
            li_n_off       <= 32'd0;
            li_dcnt        <= 32'd0;
            lip_site       <= 6'd0;
            lip_n_samples  <= 32'd0;
            lip_prime_idx  <= 4'd0;
            lip_p          <= 6'd0;
            lip_cnt        <= 6'd0;
            lip_I          <= 48'sd0;
            lip_Q          <= 48'sd0;
            lip_n          <= 32'd0;
        end else begin
            // Default one-cycle pulses
            tx_load        <= 1'b0;
            em_cmd_drive   <= 1'b0;
            em_cmd_set     <= 1'b0;
            em_cmd_pattern <= 1'b0;
            em_cmd_clear   <= 1'b0;
            pll_start      <= 1'b0;
            pll_phase_only <= 1'b0;
            det_read_one   <= 1'b0;
            det_read_all   <= 1'b0;
            det_clear      <= 1'b0;
            det_code_ack   <= 1'b0;
            mw_gate_load   <= 1'b0;
            grad_dac_load  <= 1'b0;
            rng_flush      <= 1'b0;

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
                        end else if (cmd_len == 4'd12) begin
                            // RUN_SEQUENCE (12 chars): site kind n_windows t_step_ns
                            if (cmd_str[0]=="R" && cmd_str[1]=="U" && cmd_str[2]=="N" &&
                                cmd_str[3]=="_" && cmd_str[4]=="S" && cmd_str[5]=="E" &&
                                cmd_str[6]=="Q" && cmd_str[7]=="U" && cmd_str[8]=="E" &&
                                cmd_str[9]=="N" && cmd_str[10]=="C" && cmd_str[11]=="E") begin
                                cmd_id <= CMD_SEQ; arg_count <= 3'd4;
                                if (!is_eol && ch == 8'h20) begin
                                    parse_idx <= parse_idx + 7'd1;
                                    fsm       <= S_ARGSP;
                                end else begin
                                    cmd_id <= CMD_ERR;  // RUN_SEQUENCE needs args
                                    fsm    <= S_DISPATCH;
                                end
                            end else if (cmd_str[0]=="R" && cmd_str[1]=="E" &&
                                         cmd_str[2]=="A" && cmd_str[3]=="D" &&
                                         cmd_str[4]=="_" && cmd_str[5]=="L" &&
                                         cmd_str[6]=="O" && cmd_str[7]=="C" &&
                                         cmd_str[8]=="K" && cmd_str[9]=="I" &&
                                         cmd_str[10]=="N" && cmd_str[11]=="P") begin
                                // READ_LOCKINP (12 chars): site n_samples prime_idx
                                // -- prime-period cosine/sine IQ lock-in. The host
                                // passes prime_idx (0..15) selecting a system prime
                                // p = PRIME_SYMBOLS[prime_idx] (qssc.prime_readout
                                // assigns the prime); the RTL demodulates against an
                                // EXACTLY period-p cos/sin reference (per-prime ROM +
                                // wrapping sample counter), returning I Q n.
                                cmd_id <= CMD_LOCKINP; arg_count <= 3'd3;
                                if (!is_eol && ch == 8'h20) begin
                                    parse_idx <= parse_idx + 7'd1;
                                    fsm       <= S_ARGSP;
                                end else begin
                                    cmd_id <= CMD_ERR;  // READ_LOCKINP needs args
                                    fsm    <= S_DISPATCH;
                                end
                            end else if (
                                // RUN_ALL_ODMR (12 chars): R U N _ A L L _ O D M R
                                cmd_str[0]=="R" && cmd_str[1]=="U" && cmd_str[2]=="N" &&
                                cmd_str[3]=="_" && cmd_str[4]=="A" && cmd_str[5]=="L" &&
                                cmd_str[6]=="L" && cmd_str[7]=="_" && cmd_str[8]=="O" &&
                                cmd_str[9]=="D" && cmd_str[10]=="M" &&
                                cmd_str[11]=="R") begin
                                cmd_id <= CMD_ALL_SWEEP; arg_count <= 3'd3;
                                if (!is_eol && ch == 8'h20) begin
                                    parse_idx <= parse_idx + 7'd1;
                                    fsm       <= S_ARGSP;
                                end else begin
                                    cmd_id <= CMD_ERR;   // RUN_ALL_ODMR needs 3 args
                                    fsm    <= S_DISPATCH;
                                end
                            end else if
                                // SET_GRADIENT (12 chars): code 0..4095
                                (cmd_str[0]=="S" && cmd_str[1]=="E" && cmd_str[2]=="T" &&
                                 cmd_str[3]=="_" && cmd_str[4]=="G" && cmd_str[5]=="R" &&
                                 cmd_str[6]=="A" && cmd_str[7]=="D" && cmd_str[8]=="I" &&
                                 cmd_str[9]=="E" && cmd_str[10]=="N" && cmd_str[11]=="T") begin
                                cmd_id <= CMD_GRADIENT; arg_count <= 3'd1;
                                if (!is_eol && ch == 8'h20) begin
                                    parse_idx <= parse_idx + 7'd1;
                                    fsm       <= S_ARGSP;
                                end else begin
                                    cmd_id <= CMD_ERR;  // SET_GRADIENT needs a code
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
                            end else if (cmd_str[0]=="R" && cmd_str[1]=="E" &&
                                         cmd_str[2]=="A" && cmd_str[3]=="D" &&
                                         cmd_str[4]=="_" && cmd_str[5]=="L" &&
                                         cmd_str[6]=="O" && cmd_str[7]=="C" &&
                                         cmd_str[8]=="K" && cmd_str[9]=="I" &&
                                         cmd_str[10]=="N") begin
                                // READ_LOCKIN (11 chars): site n_cycles
                                cmd_id <= CMD_LOCKIN; arg_count <= 3'd2;
                                if (!is_eol && ch == 8'h20) begin
                                    parse_idx <= parse_idx + 7'd1;
                                    fsm       <= S_ARGSP;
                                end else begin
                                    cmd_id <= CMD_ERR;   // READ_LOCKIN needs args
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
                        end else if (cmd_len == 4'd10 &&
                            // ODMR_SWEEP (10 chars): O D M R _ S W E E P
                            cmd_str[0]=="O" && cmd_str[1]=="D" && cmd_str[2]=="M" &&
                            cmd_str[3]=="R" && cmd_str[4]=="_" && cmd_str[5]=="S" &&
                            cmd_str[6]=="W" && cmd_str[7]=="E" && cmd_str[8]=="E" &&
                            cmd_str[9]=="P") begin
                            cmd_id <= CMD_SWEEP; arg_count <= 3'd4;
                            if (!is_eol && ch == 8'h20) begin
                                parse_idx <= parse_idx + 7'd1;
                                fsm       <= S_ARGSP;
                            end else begin
                                cmd_id <= CMD_ERR;   // ODMR_SWEEP needs 4 args
                                fsm    <= S_DISPATCH;
                            end
                        end else if (cmd_len == 4'd6 &&
                            // STATUS (6 chars): S T A T U S
                            cmd_str[0]=="S" && cmd_str[1]=="T" && cmd_str[2]=="A" &&
                            cmd_str[3]=="T" && cmd_str[4]=="U" && cmd_str[5]=="S") begin
                            cmd_id <= CMD_STATUS; arg_count <= 3'd0;
                            fsm    <= S_DISPATCH;
                        end else if (cmd_len == 4'd5 &&
                            // RESET (5 chars): R E S E T
                            cmd_str[0]=="R" && cmd_str[1]=="E" && cmd_str[2]=="S" &&
                            cmd_str[3]=="E" && cmd_str[4]=="T") begin
                            cmd_id <= CMD_RESET; arg_count <= 3'd0;
                            fsm    <= S_DISPATCH;
                        end else if (cmd_len == 4'd2 &&
                            // ID (2 chars): I D
                            cmd_str[0]=="I" && cmd_str[1]=="D") begin
                            cmd_id <= CMD_ID; arg_count <= 3'd0;
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
                            // the pulse phase (arg 4, milliradians -> R1 PHASE
                            // via mrad_to_phase: 12 bits, 4095 = 360 deg) and a
                            // coarse output power (arg 2 -> R4 RF_PWR, 4 levels).
                            // The pulse duration (arg 3, ns) gates an RF switch
                            // via the mw_gate timer in top_level, armed once the
                            // PLL has finished programming the carrier. Reject
                            // negative / out-of-range args with ERR.
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) >= N_DET ||
                                $signed(arg_vals[1]) <= 0 ||
                                $signed(arg_vals[2]) < 0 ||
                                $signed(arg_vals[3]) < 0) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                pll_freq_khz <= arg_vals[1];
                                pll_phase    <= mrad_to_phase($signed(arg_vals[4]));  // mrad -> R1 PHASE
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
                                seq_wdog      <= 32'd0;
                                fsm           <= S_RONE_WAIT;
                            end
                        end
                        CMD_READ_ALL : begin
                            det_read_all  <= 1'b1;
                            read_all_mode <= 1'b1;
                            rall_loop     <= 1'b0;
                            seq_wdog      <= 32'd0;
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
                            // stream it as hex + a " 00064 0 0\n" stats footer
                            // (raw = the raw-sample delta reported in the
                            // footer; kept/extract_rate are host-derived; the
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
                                rng_raw_start  <= rng_samples_seen;
                                rng_watchdog   <= 27'd0;
                                rng_flush      <= 1'b1;   // fresh extractor state
                                rall_loop      <= 1'b0;
                                read_all_mode  <= 1'b0;
                                fsm            <= S_RNG_HARVEST;
                            end
                        end
                        CMD_ID : begin
                            // ID -> "QSSC v1\n" (the FPGAConnection probe token;
                            // host validates reply startswith "QSSC").
                            str_idx <= 7'd0;
                            fsm     <= S_ID_SEND;
                        end
                        CMD_STATUS : begin
                            // STATUS -> "PLL_LOCKED <0/1> ADC_BUSY <0/1>
                            // VCC3V3_mv 3300 VCC5V_mv 4980 VCC12V_mv 12050
                            // TEMP_C 25\n". PLL_LOCKED = !pll_busy (idle =>
                            // settled; the ADF4351 MUXOUT lock-detect is a
                            // future refinement), ADC_BUSY = det_busy. The rail
                            // voltages are static nominal mV (no voltage-monitor
                            // ADC fitted); TEMP_C is a 25 C placeholder (no XADC
                            // in this build).
                            status_pll_dig <= pll_busy ? 8'h30 : 8'h31;
                            status_adc_dig <= det_busy  ? 8'h31 : 8'h30;
                            str_idx        <= 7'd0;
                            fsm            <= S_STATUS_SEND;
                        end
                        CMD_RESET : begin
                            // RESET -> assert soft_rst for a few cycles to re-
                            // init the peripherals (emitter/ADC/PLL/QRNG/gate),
                            // then reply OK. The parser is not reset by it.
                            // Also zero the gradient DAC code so RESET turns the
                            // gradient coil OFF. The actual DAC load pulse is
                            // deferred to the end of S_RESET_WAIT (below): firing
                            // it here would be ignored, because soft_rst ORs into
                            // peri_rst which holds dac_ctrl in sync reset for the
                            // whole rst_hold window. Without this clear the coil
                            // keeps its last programmed current across a RESET
                            // (only power-on `reset` cleared grad_dac_value),
                            // leaving a stray field on the next ODMR sweep.
                            soft_rst       <= 1'b1;
                            rst_hold       <= 3'd4;
                            grad_dac_value <= 12'd0;
                            fsm            <= S_RESET_WAIT;
                        end
                        CMD_SWEEP : begin
                            // ODMR_SWEEP <q> <f_start_khz> <f_stop_khz> <n>:
                            // per point reprogram the ADF4351 to f(i), gate the
                            // RF, read detector site q, stream "f c\n". q must
                            // be 0..63, n 1..256, f_start/f_stop > 0. step =
                            // (f_stop - f_start)/(n-1) (0 when n=1).
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) >= N_DET ||
                                $signed(arg_vals[1]) <= 0 ||
                                $signed(arg_vals[2]) <= 0 ||
                                $signed(arg_vals[3]) < 32'd1 ||
                                $signed(arg_vals[3]) > 32'd256) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                sweep_q        <= arg_vals[0][5:0];
                                sweep_f        <= arg_vals[1];
                                sweep_n        <= arg_vals[3];
                                sweep_i        <= 32'd0;
                                sweep_step     <= (arg_vals[3] == 32'd1)
                                                  ? 32'd0
                                                  : ($signed(arg_vals[2]) - $signed(arg_vals[1])) /
                                                    $signed(arg_vals[3] - 32'd1);
                                mw_gate_ns     <= ODMR_GATE_NS; // 200 us ODMR gate per point
                                sweep_pll_seen <= 1'b0;
                                read_all_mode  <= 1'b0;
                                fsm            <= S_SWEEP_PROG;
                            end
                        end
                        CMD_ALL_SWEEP : begin
                            // RUN_ALL_ODMR <f_start_khz> <f_stop_khz> <n_points>
                            // (Invention 4+5): per point reprogram the ADF4351 to
                            // f(i), gate the RF, and read ALL 64 AD7616 channels
                            // (one row of 64 codes), then advance. f_start/f_stop
                            // > 0, n 1..256. step = (f_stop-f_start)/(n-1).
                            if ($signed(arg_vals[0]) <= 0 ||
                                $signed(arg_vals[1]) <= 0 ||
                                $signed(arg_vals[2]) < 32'd1 ||
                                $signed(arg_vals[2]) > 32'd256) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                sweep_f        <= arg_vals[0];
                                sweep_n        <= arg_vals[2];
                                sweep_i        <= 32'd0;
                                sweep_step     <= (arg_vals[2] == 32'd1)
                                                  ? 32'd0
                                                  : ($signed(arg_vals[1]) - $signed(arg_vals[0])) /
                                                    $signed(arg_vals[2] - 32'd1);
                                mw_gate_ns     <= ODMR_GATE_NS; // 200 us ODMR gate per point
                                sweep_pll_seen <= 1'b0;
                                asweep_mode    <= 1'b1;
                                fsm            <= S_ASWEEP_PROG;
                            end
                        end
                        CMD_SEQ : begin
                            // RUN_SEQUENCE <site> <kind> <n> <t_step_ns>:
                            // drive a Ramsey(0)/Hahn(1)/CPMG(2) sequence over n
                            // windows with free-evolution time T = i*t_step, and
                            // stream one ADC readout code per window. Bounds:
                            // site 0..63, kind 0..2, n 1..1024, t_step >= 0.
                            // The RTL returns raw codes; the host fits T2/rate.
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) >= N_DET ||
                                $signed(arg_vals[1]) < 0 || $signed(arg_vals[1]) > 32'd2 ||
                                $signed(arg_vals[2]) < 32'd1  || $signed(arg_vals[2]) > 32'd1024 ||
                                $signed(arg_vals[3]) < 32'd0) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                seq_site    <= arg_vals[0][5:0];
                                seq_kind    <= arg_vals[1][1:0];
                                seq_n       <= arg_vals[2][10:0];
                                seq_tstep   <= arg_vals[3];
                                seq_dly_inc <= arg_vals[3] / 32'd10;  // cycles per ns (10 ns clk)
                                seq_i       <= 11'd0;
                                seq_delay   <= 32'd0;
                                seq_dcnt    <= 32'd0;
                                seq_is_last <= 1'b0;
                                pll_phase   <= 12'd0;
                                read_all_mode <= 1'b0;
                                str_idx     <= 7'd0;
                                fsm         <= S_SEQ_COUNT_ENC;
                            end
                        end
                        CMD_LOCKIN : begin
                            // READ_LOCKIN <site> <n_cycles>: self-timed on/off
                            // lock-in accumulation. Returns four raw ints
                            // (sum_on sum_off n_on n_off); the host computes
                            // diff/sigma/SNR. Bounds: site 0..63, n 1..10000.
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) >= N_DET ||
                                $signed(arg_vals[1]) < 32'd1 ||
                                $signed(arg_vals[1]) > 32'd10000) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                li_site   <= arg_vals[0][5:0];
                                li_n      <= arg_vals[1];
                                li_i      <= 32'd0;
                                li_sum_on <= 32'd0;
                                li_sum_off<= 32'd0;
                                li_n_on   <= 32'd0;
                                li_n_off  <= 32'd0;
                                li_dcnt   <= 32'd0;
                                li_half_ns<= LI_HALF_NS;       // default half
                                li_half_cyc<= LI_HALF_CYC;
                                pll_phase <= 12'd0;
                                read_all_mode <= 1'b0;
                                str_idx   <= 7'd0;
                                fsm       <= S_LI_INIT;
                            end
                        end
                        CMD_LOCKINP : begin
                            // READ_LOCKINP <site> <n_samples> <prime_idx>:
                            // prime-period cosine/sine IQ lock-in. prime_idx
                            // (0..15) selects one of the 16 system primes p =
                            // PRIME_SYMBOLS[prime_idx] (2,3,5,...,53); the demod
                            // reference is a cosine/sine at EXACTLY period p
                            // (a sample counter wraps at p, indexing the per-prime
                            // ROM), so over the LCM two distinct-prime references
                            // are orthogonal to 12-bit rounding (~0.02%) -- NOT
                            // the +-1/(p*q) odd-prime residual of the old 2-sample
                            // square-wave boxcar. The host sends prime_idx (not a
                            // phase_inc) so no RTL divide and no NCO frequency
                            // detuning. Bounds: site 0..63, n_samples 1..100000,
                            // prime_idx 0..15. No MW gate -- pure detector demod.
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) >= N_DET ||
                                $signed(arg_vals[1]) < 32'd1 ||
                                $signed(arg_vals[1]) > 32'd100000 ||
                                $signed(arg_vals[2]) < 32'd0 ||
                                $signed(arg_vals[2]) > 32'd15) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                lip_site      <= arg_vals[0][5:0];
                                lip_n_samples <= arg_vals[1];
                                lip_prime_idx <= arg_vals[2][3:0];
                                lip_p         <= prime_tab[arg_vals[2][3:0]];
                                lip_cnt       <= 6'd0;
                                lip_I         <= 48'sd0;
                                lip_Q         <= 48'sd0;
                                lip_n         <= 32'd0;
                                read_all_mode <= 1'b0;
                                str_idx       <= 7'd0;
                                fsm           <= S_LIP_INIT;
                            end
                        end
                        CMD_GRADIENT : begin
                            // SET_GRADIENT <code 0..4095>: program the MCP4921
                            // DAC that sets the gradient-coil current. Wait for
                            // the dac_ctrl SPI shift to finish before replying
                            // (serialized, like SET_EMITTER on the TLC5947).
                            if ($signed(arg_vals[0]) < 32'd0 ||
                                $signed(arg_vals[0]) > 32'd4095) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                grad_dac_value <= arg_vals[0][11:0];
                                grad_dac_load  <= 1'b1;
                                grad_busy_seen <= 1'b0;
                                fsm            <= S_GRAD_WAIT;
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
                    end else if (seq_wdog >= READ_WDOG_CYC) begin
                        code_latch   <= 16'd0;    // dead-ADC sentinel
                        det_code_ack <= 1'b1;
                        fsm          <= S_ENC;
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;
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
                    end else if (seq_wdog >= READ_WDOG_CYC) begin
                        code_latch   <= 16'd0;    // dead-ADC sentinel
                        code_is_last <= 1'b1;     // stop the read_all loop
                        det_code_ack <= 1'b1;
                        fsm          <= S_ENC;
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;
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
                                seq_wdog  <= 32'd0;
                                fsm       <= S_RALL_WAIT;
                            end else if (asweep_mode) begin
                                // last code of this all-sweep freq point sent;
                                // wait for the MW gate to fall before advancing
                                fsm <= S_ASWEEP_GATE_FALL;
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

                // ---- SET_GRADIENT: wait for the dac_ctrl SPI shift to finish,
                // then reply OK. The busy-seen guard avoids racing on a
                // momentary !busy before dac_ctrl has asserted busy.
                S_GRAD_WAIT : begin
                    if (grad_dac_busy)        grad_busy_seen <= 1'b1;
                    if (grad_busy_seen && !grad_dac_busy) begin
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
                    rng_flush      <= 1'b0;   // flush was a one-cycle pulse
                    if (rng_valid) begin
                        rng_word_buf   <= rng_word;
                        nibble_idx     <= 3'd0;
                        rng_harvest_en <= 1'b0;   // pause extractor during emit
                        rng_watchdog   <= 27'd0;  // healthy: keep resetting
                        fsm            <= S_RNG_EMIT;
                    end else if (rng_watchdog >= RNG_WATCHDOG_CYCLES) begin
                        // no qrng word completed in time (stuck/degenerate
                        // dark channel): refuse instead of hanging forever.
                        rng_harvest_en <= 1'b0;
                        reply_ok       <= 1'b0;   // ERR
                        fsm            <= S_REPLY_LOAD;
                    end else begin
                        rng_watchdog <= rng_watchdog + 27'd1;
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

                // READ_RNG footer: build " 00064 0 0\n" in one cycle. raw =
                // the raw LSB samples this request fed to the extractor
                // (decimal, zero-padded to 5 digits); kept and rate stay "0"
                // placeholders — the host derives kept = n_bytes*8 and
                // rate = kept/raw.
                S_RNG_STATS : begin
                    rng_harvest_en <= 1'b0;
                    rng_tail[0]  <= 8'h20;                 // ' '
                    rng_tail[1]  <= enc_raw[39:32];        // ten-thousands
                    rng_tail[2]  <= enc_raw[31:24];        // thousands
                    rng_tail[3]  <= enc_raw[23:16];        // hundreds
                    rng_tail[4]  <= enc_raw[15:8];         // tens
                    rng_tail[5]  <= enc_raw[7:0];          // ones
                    rng_tail[6]  <= 8'h20;                 // ' '
                    rng_tail[7]  <= 8'h30;                 // '0' (kept: host)
                    rng_tail[8]  <= 8'h20;                 // ' '
                    rng_tail[9]  <= 8'h30;                 // '0' (rate: host)
                    rng_tail[10] <= 8'h0A;                 // '\n'
                    rng_tail_idx   <= 4'd0;
                    fsm            <= S_RNG_TAIL;
                end

                // Stream the footer (11 bytes: ' ' + 5 zero-padded raw digits
                // + " 0 0" + LF). Too long for the 8-byte reply_mem, so it
                // goes out through tx_load like the hex stream above.
                S_RNG_TAIL : begin
                    if (tx_ready) begin
                        tx_load <= 1'b1;
                        tx_din  <= rng_tail[rng_tail_idx];
                        if (rng_tail_idx == 4'd10)
                            fsm <= S_IDLE;            // footer was the last byte
                        else
                            rng_tail_idx <= rng_tail_idx + 4'd1;
                    end
                end

                // ── PR2 verbs: ID / STATUS / RESET / ODMR_SWEEP ───────────────
                // ID — stream the 8-byte identity string "QSSC v1\n".
                S_ID_SEND : begin
                    if (tx_ready) begin
                        tx_load <= 1'b1;
                        tx_din  <= id_str[str_idx[2:0]];
                        if (str_idx == 7'd7)
                            fsm <= S_IDLE;
                        else
                            str_idx <= str_idx + 7'd1;
                    end
                end

                // STATUS — stream the 79-byte template, substituting the live
                // PLL-lock and ADC-busy digits at template indices 11 and 22.
                S_STATUS_SEND : begin
                    if (tx_ready) begin
                        tx_load <= 1'b1;
                        tx_din  <= status_byte;
                        if (str_idx == STATUS_LEN - 7'd1)
                            fsm <= S_IDLE;
                        else
                            str_idx <= str_idx + 7'd1;
                    end
                end

                // RESET — hold soft_rst for a few cycles so the peripheral
                // reset trees see a clean pulse, then reply OK.
                S_RESET_WAIT : begin
                    if (rst_hold == 3'd0) begin
                        // soft_rst deasserts next cycle -> peri_rst drops ->
                        // dac_ctrl leaves sync reset. Pulse grad_dac_load HERE
                        // so dac_ctrl sees start=1 (with grad_dac_value=0) on
                        // the first out-of-reset cycle and writes 0 to the
                        // MCP4921, hard-clearing the coil current. The load is a
                        // 1-cycle pulse (the line-815 default clears it next
                        // cycle); the SPI shift runs in the background after the
                        // OK reply is sent.
                        soft_rst      <= 1'b0;
                        grad_dac_load <= 1'b1;
                        reply_ok      <= 1'b1;
                        fsm           <= S_REPLY_LOAD;
                    end else begin
                        rst_hold <= rst_hold - 3'd1;
                    end
                end

                // ODMR_SWEEP — program the ADF4351 for the current sweep point.
                S_SWEEP_PROG : begin
                    pll_freq_khz   <= sweep_f;
                    pll_phase      <= 12'd0;
                    pll_rf_pwr     <= 2'd0;
                    pll_start      <= 1'b1;
                    sweep_pll_seen <= 1'b0;
                    fsm            <= S_SWEEP_PLL;
                end

                // Wait for PLL busy to assert then deassert (rising then done),
                // then arm the RF gate ONLY (the read fires on the gate rise so
                // the fresh sample is taken DURING the 200 us RF pulse).
                S_SWEEP_PLL : begin
                    if (pll_busy)
                        sweep_pll_seen <= 1'b1;
                    if (sweep_pll_seen && !pll_busy) begin
                        mw_gate_load  <= 1'b1;        // arm the ODMR gate (mw_gate_ns set at dispatch)
                        gate_seen     <= 1'b0;
                        seq_wdog      <= 32'd0;
                        read_all_mode <= 1'b0;
                        fsm           <= S_SWEEP_GATE_WAIT;
                    end
                end

                // Wait for the RF gate to rise, then trigger the fresh read.
                S_SWEEP_GATE_WAIT : begin
                    if (seq_wdog >= GATE_WDOG_CYC) begin
                        det_site     <= sweep_q;
                        det_read_one <= 1'b1;
                        fsm          <= S_SWEEP_READ;
                    end else if (mw_gate) begin
                        gate_seen    <= 1'b1;
                        det_site     <= sweep_q;
                        det_read_one <= 1'b1;
                        seq_wdog     <= 32'd0;
                        fsm          <= S_SWEEP_READ;
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;
                    end
                end

                // Latch the detector code for this point.
                S_SWEEP_READ : begin
                    if (det_code_valid) begin
                        code_latch   <= det_code;
                        det_code_ack <= 1'b1;
                        seq_wdog     <= 32'd0;
                        fsm          <= S_SWEEP_GATE_FALL;
                    end else if (seq_wdog >= READ_WDOG_CYC) begin
                        code_latch   <= 16'd0;        // dead-ADC sentinel
                        det_code_ack <= 1'b1;
                        seq_wdog     <= 32'd0;
                        fsm          <= S_SWEEP_GATE_FALL;
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;
                    end
                end

                // Wait for the RF gate to fall before encoding the next point
                // (no gate overlap onto the next frequency).
                S_SWEEP_GATE_FALL : begin
                    if (seq_wdog >= GATE_WDOG_CYC || (gate_seen && !mw_gate)) begin
                        fsm <= S_SWEEP_ENC;
                    end else begin
                        if (mw_gate) gate_seen <= 1'b1;
                        seq_wdog <= seq_wdog + 32'd1;
                    end
                end

                // Encode one "fffffff ccccc\n" line (14 bytes, fixed width).
                S_SWEEP_ENC : begin
                    sweep_line[0]  <= sweep_f_pad[55:48];
                    sweep_line[1]  <= sweep_f_pad[47:40];
                    sweep_line[2]  <= sweep_f_pad[39:32];
                    sweep_line[3]  <= sweep_f_pad[31:24];
                    sweep_line[4]  <= sweep_f_pad[23:16];
                    sweep_line[5]  <= sweep_f_pad[15:8];
                    sweep_line[6]  <= sweep_f_pad[7:0];
                    sweep_line[7]  <= 8'h20;          // ' '
                    sweep_line[8]  <= sweep_c_pad[39:32];
                    sweep_line[9]  <= sweep_c_pad[31:24];
                    sweep_line[10] <= sweep_c_pad[23:16];
                    sweep_line[11] <= sweep_c_pad[15:8];
                    sweep_line[12] <= sweep_c_pad[7:0];
                    sweep_line[13] <= 8'h0A;          // '\n'
                    sweep_len      <= 6'd14;
                    str_idx        <= 7'd0;
                    fsm            <= S_SWEEP_SEND;
                end

                // Stream the 14-byte sweep line.
                S_SWEEP_SEND : begin
                    if (tx_ready) begin
                        tx_load <= 1'b1;
                        tx_din  <= sweep_line[str_idx[3:0]];
                        if (str_idx == 7'd13)
                            fsm <= S_SWEEP_NEXT;
                        else
                            str_idx <= str_idx + 7'd1;
                    end
                end

                // Advance to the next point, or finish with an OK reply.
                S_SWEEP_NEXT : begin
                    sweep_i <= sweep_i + 32'd1;
                    sweep_f <= sweep_f + sweep_step;
                    if (sweep_i + 32'd1 >= sweep_n) begin
                        reply_ok <= 1'b1;
                        fsm      <= S_REPLY_LOAD;
                    end else begin
                        sweep_pll_seen <= 1'b0;
                        fsm            <= S_SWEEP_PROG;
                    end
                end

                // ============================================================
                // Invention 4+5 — RUN_ALL_ODMR (CMD_ALL_SWEEP).
                // Same PLL-sweep + RF-gate structure as ODMR_SWEEP, but each
                // point reads ALL 64 AD7616 channels (one row) instead of one
                // site. The 64-code row is emitted by the shared S_RALL_WAIT /
                // S_ENC / S_REPLY_SEND read_all path (read_all_mode drives the
                // space separator + the per-channel rall_loop; asweep_mode
                // reroutes the end-of-row target from S_IDLE to S_ASWEEP_GATE_FALL
                // so the sweep can advance to the next frequency). No per-row
                // header: the reply is just n rows of 64 decimal codes + OK.
                // ============================================================
                S_ASWEEP_PROG : begin
                    pll_freq_khz   <= sweep_f;
                    pll_phase      <= 12'd0;
                    pll_rf_pwr     <= 2'd0;
                    pll_start      <= 1'b1;
                    sweep_pll_seen <= 1'b0;
                    fsm            <= S_ASWEEP_PLL;
                end

                // Wait for PLL busy to assert then deassert, then arm the RF gate.
                S_ASWEEP_PLL : begin
                    if (pll_busy)
                        sweep_pll_seen <= 1'b1;
                    if (sweep_pll_seen && !pll_busy) begin
                        mw_gate_load  <= 1'b1;        // arm the ODMR gate
                        gate_seen     <= 1'b0;
                        seq_wdog      <= 32'd0;
                        read_all_mode <= 1'b1;        // 64-code row, space-separated
                        fsm           <= S_ASWEEP_GATE_WAIT;
                    end
                end

                // Wait for the RF gate to rise, then trigger the all-channel read.
                S_ASWEEP_GATE_WAIT : begin
                    if (seq_wdog >= GATE_WDOG_CYC) begin
                        det_read_all <= 1'b1;
                        seq_wdog     <= 32'd0;
                        fsm          <= S_RALL_WAIT;  // shared 64-code read loop
                    end else if (mw_gate) begin
                        gate_seen    <= 1'b1;
                        det_read_all <= 1'b1;
                        seq_wdog     <= 32'd0;
                        fsm          <= S_RALL_WAIT;
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;
                    end
                end

                // All 64 codes for this row streamed (S_REPLY_SEND -> here via
                // asweep_mode). Wait for the RF gate to fall before the next
                // frequency point (no gate overlap onto the next PLL setting).
                S_ASWEEP_GATE_FALL : begin
                    if (seq_wdog >= GATE_WDOG_CYC || (gate_seen && !mw_gate)) begin
                        fsm <= S_ASWEEP_NEXT;
                    end else begin
                        if (mw_gate) gate_seen <= 1'b1;
                        seq_wdog <= seq_wdog + 32'd1;
                    end
                end

                // Advance to the next point, or finish with an OK reply.
                S_ASWEEP_NEXT : begin
                    sweep_i <= sweep_i + 32'd1;
                    sweep_f <= sweep_f + sweep_step;
                    if (sweep_i + 32'd1 >= sweep_n) begin
                        asweep_mode <= 1'b0;
                        reply_ok    <= 1'b1;
                        fsm         <= S_REPLY_LOAD;
                    end else begin
                        sweep_pll_seen <= 1'b0;
                        fsm            <= S_ASWEEP_PROG;
                    end
                end

                // ============================================================
                // #58 RUN_SEQUENCE pulse-sequence engine.
                // The carrier is pre-set by the host (APPLY_PULSE); the engine
                // only gates the RF switch for each MW pulse and snapshots one
                // ADC code per window. mw_gate is watched rise->fall so a pulse
                // is complete before the free-evolution delay / next pulse
                // starts (no overlap at T=0). For Ramsey (kind==0) the readout
                // pi/2 applies a 90-deg carrier phase step via an R1-only PLL
                // write (pll_phase_only) that shifts phase WITHOUT re-locking the
                // loop, mid-sequence; Hahn/CPMG skip the phase write. CPMG = Hahn
                // (one pi echo) here; the multi-echo decay is a host-side model.
                // ============================================================

                // Build the "<count> " prefix (5-digit dec5_pad + space).
                S_SEQ_COUNT_ENC : begin
                    seq_line[0] <= seq_count_pad[39:32];
                    seq_line[1] <= seq_count_pad[31:24];
                    seq_line[2] <= seq_count_pad[23:16];
                    seq_line[3] <= seq_count_pad[15:8];
                    seq_line[4] <= seq_count_pad[7:0];
                    seq_line[5] <= 8'h20;            // ' '
                    str_idx     <= 7'd0;
                    fsm         <= S_SEQ_COUNT_SEND;
                end

                S_SEQ_COUNT_SEND : begin
                    if (tx_ready) begin
                        tx_load <= 1'b1;
                        tx_din  <= seq_line[str_idx[2:0]];
                        if (str_idx == 7'd5)
                            fsm <= S_SEQ_PI2;
                        else
                            str_idx <= str_idx + 7'd1;
                    end
                end

                // First pi/2 (phase 0). Arms the gate + loads this window's
                // free-evolution countdown (T = i*t_step, 0 for window 0).
                S_SEQ_PI2 : begin
                    mw_gate_ns   <= PI2_NS;
                    pll_phase    <= 12'd0;
                    mw_gate_load <= 1'b1;
                    gate_seen    <= 1'b0;
                    seq_wdog     <= 32'd0;
                    seq_dcnt     <= seq_delay;
                    seq_is_last  <= (seq_i + 11'd1 >= seq_n);
                    fsm          <= S_SEQ_DLY1;
                end

                // Wait for the pi/2 gate to finish, then count down T. The
                // watchdog forces the gate-done transition if mw_gate ever
                // sticks (the T countdown then proceeds to completion).
                S_SEQ_DLY1 : begin
                    if (seq_wdog >= GATE_WDOG_CYC && !gate_seen) begin
                        gate_seen <= 1'b1;
                    end else if (!gate_seen) begin
                        if (mw_gate) gate_seen <= 1'b1;
                        seq_wdog <= seq_wdog + 32'd1;
                    end else if (!mw_gate) begin
                        if (seq_dcnt == 32'd0) begin
                            fsm <= (seq_kind == 2'd0) ? S_SEQ_PI2B : S_SEQ_PI;
                        end else begin
                            seq_dcnt <= seq_dcnt - 32'd1;
                        end
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;   // seen, waiting for fall
                    end
                end

                // Pi echo (Hahn/CPMG only, phase 0).
                S_SEQ_PI : begin
                    mw_gate_ns   <= PI_NS;
                    pll_phase    <= 12'd0;
                    mw_gate_load <= 1'b1;
                    gate_seen    <= 1'b0;
                    seq_wdog     <= 32'd0;
                    seq_dcnt     <= seq_delay;
                    fsm          <= S_SEQ_DLY2;
                end

                // Wait for the pi gate to finish, then count down T.
                S_SEQ_DLY2 : begin
                    if (seq_wdog >= GATE_WDOG_CYC && !gate_seen) begin
                        gate_seen <= 1'b1;
                    end else if (!gate_seen) begin
                        if (mw_gate) gate_seen <= 1'b1;
                        seq_wdog <= seq_wdog + 32'd1;
                    end else if (!mw_gate) begin
                        if (seq_dcnt == 32'd0)
                            fsm <= S_SEQ_PI2B;
                        else
                            seq_dcnt <= seq_dcnt - 32'd1;
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;   // seen, waiting for fall
                    end
                end

                // Readout pi/2. Ramsey (kind==0) first applies a 90-deg carrier
                // phase step via an R1-only PLL write (no re-lock) and waits for
                // that write to complete before gating+reading; Hahn/CPMG gate
                // and read immediately (phase 0, no PLL write).
                S_SEQ_PI2B : begin
                    mw_gate_ns <= PI2_NS;
                    if (seq_kind == 2'd0) begin
                        // Ramsey: phase-step the locked carrier, then gate+read.
                        pll_phase       <= 12'd1024;    // 90-deg R1 PHASE step
                        pll_phase_only  <= 1'b1;        // R1-only write, no re-lock
                        pll_start       <= 1'b1;
                        pll_busy_seen   <= 1'b0;
                        seq_wdog        <= 32'd0;
                        fsm             <= S_SEQ_PI2B_PLL;
                    end else begin
                        // Hahn/CPMG: readout pi/2 at phase 0, no PLL write.
                        pll_phase    <= 12'd0;
                        mw_gate_load <= 1'b1;
                        det_read_one <= 1'b1;
                        det_site     <= seq_site;
                        seq_wdog     <= 32'd0;
                        fsm          <= S_SEQ_READ;
                    end
                end

                // Ramsey: wait for the R1-only phase write to finish (~2.5 us
                // SPI transaction, NOT a re-lock), then gate + read.
                S_SEQ_PI2B_PLL : begin
                    if (pll_busy) pll_busy_seen <= 1'b1;
                    if (pll_busy_seen && !pll_busy) begin
                        mw_gate_load    <= 1'b1;
                        det_read_one    <= 1'b1;
                        det_site        <= seq_site;
                        pll_phase_only  <= 1'b0;
                        seq_wdog        <= 32'd0;
                        fsm             <= S_SEQ_READ;
                    end else if (seq_wdog >= GATE_WDOG_CYC) begin
                        // PLL stuck: gate+read anyway so the FSM cannot hang.
                        mw_gate_load    <= 1'b1;
                        det_read_one    <= 1'b1;
                        det_site        <= seq_site;
                        pll_phase_only  <= 1'b0;
                        fsm             <= S_SEQ_READ;
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;
                    end
                end

                // Latch the readout code for this window.
                S_SEQ_READ : begin
                    if (det_code_valid) begin
                        code_latch   <= det_code;
                        det_code_ack <= 1'b1;
                        fsm          <= S_SEQ_CODE_ENC;
                    end else if (seq_wdog >= READ_WDOG_CYC) begin
                        code_latch   <= 16'd0;        // dead-ADC sentinel
                        det_code_ack <= 1'b1;
                        fsm          <= S_SEQ_CODE_ENC;
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;
                    end
                end

                // Build "<code><sep>" (5-digit dec5_pad + ' ' or '\n').
                S_SEQ_CODE_ENC : begin
                    seq_line[0] <= seq_code_pad[39:32];
                    seq_line[1] <= seq_code_pad[31:24];
                    seq_line[2] <= seq_code_pad[23:16];
                    seq_line[3] <= seq_code_pad[15:8];
                    seq_line[4] <= seq_code_pad[7:0];
                    seq_line[5] <= seq_sep;
                    str_idx     <= 7'd0;
                    fsm         <= S_SEQ_CODE_SEND;
                end

                S_SEQ_CODE_SEND : begin
                    if (tx_ready) begin
                        tx_load <= 1'b1;
                        tx_din  <= seq_line[str_idx[2:0]];
                        if (str_idx == 7'd5)
                            fsm <= S_SEQ_NEXT;
                        else
                            str_idx <= str_idx + 7'd1;
                    end
                end

                // Advance to the next window, or finish (no OK -- the final
                // '\n' terminates the single-line reply).
                S_SEQ_NEXT : begin
                    seq_i     <= seq_i + 11'd1;
                    seq_delay <= seq_delay + seq_dly_inc;
                    if (seq_i + 11'd1 >= seq_n)
                        fsm <= S_IDLE;
                    else
                        fsm <= S_SEQ_PI2;
                end

                // ============================================================
                // #58 READ_LOCKIN lock-in accumulator (self-timed on/off).
                // For each cycle: MW on for a half-period (gate) + read into
                // sum_on; MW off for a half-period + read into sum_off. Reply
                // is one line of four raw 10-digit ints then '\n' (no OK); the
                // host computes diff/sigma/SNR.
                // ============================================================

                S_LI_INIT : begin
                    mw_gate_ns <= li_half_ns;
                    pll_phase  <= 12'd0;
                    gate_seen  <= 1'b0;
                    fsm        <= S_LI_ON;
                end

                // Fire the MW gate for the on half-period.
                S_LI_ON : begin
                    mw_gate_load <= 1'b1;
                    gate_seen    <= 1'b0;
                    seq_wdog     <= 32'd0;
                    fsm          <= S_LI_ON_WAIT;
                end

                // Wait for the on-gate to RISE, then trigger the on read. The
                // read completes <=86 us after the rise, i.e. DURING the 200 us
                // on-gate (the gate stays high for the full half; the gate timer
                // closes it independently). The off-half countdown then places
                // the off-read the same interval after the gate fall -> symmetric.
                S_LI_ON_WAIT : begin
                    if (seq_wdog >= GATE_WDOG_CYC) begin
                        det_read_one <= 1'b1;
                        det_site     <= li_site;
                        seq_wdog     <= 32'd0;
                        fsm          <= S_LI_ON_READ;
                    end else if (mw_gate) begin
                        gate_seen    <= 1'b1;
                        det_read_one <= 1'b1;
                        det_site     <= li_site;
                        seq_wdog     <= 32'd0;
                        fsm          <= S_LI_ON_READ;
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;
                    end
                end

                S_LI_ON_READ : begin
                    if (det_code_valid) begin
                        li_sum_on    <= li_sum_on + det_code;
                        li_n_on      <= li_n_on + 32'd1;
                        det_code_ack <= 1'b1;
                        fsm          <= S_LI_OFF;
                    end else if (seq_wdog >= READ_WDOG_CYC) begin
                        li_n_on      <= li_n_on + 32'd1;   // count, no sample
                        det_code_ack <= 1'b1;
                        fsm          <= S_LI_OFF;
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;
                    end
                end

                // Off half: no gate, count down the half-period then read.
                S_LI_OFF : begin
                    li_dcnt   <= li_half_cyc;
                    seq_wdog  <= 32'd0;
                    fsm       <= S_LI_OFF_WAIT;
                end

                S_LI_OFF_WAIT : begin
                    if (li_dcnt == 32'd0) begin
                        det_read_one <= 1'b1;
                        det_site     <= li_site;
                        seq_wdog     <= 32'd0;
                        fsm          <= S_LI_OFF_READ;
                    end else begin
                        li_dcnt <= li_dcnt - 32'd1;
                    end
                end

                S_LI_OFF_READ : begin
                    if (det_code_valid) begin
                        li_sum_off   <= li_sum_off + det_code;
                        li_n_off     <= li_n_off + 32'd1;
                        det_code_ack <= 1'b1;
                        fsm          <= S_LI_NEXT;
                    end else if (seq_wdog >= READ_WDOG_CYC) begin
                        li_n_off     <= li_n_off + 32'd1;   // count, no sample
                        det_code_ack <= 1'b1;
                        fsm          <= S_LI_NEXT;
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;
                    end
                end

                S_LI_NEXT : begin
                    li_i <= li_i + 32'd1;
                    if (li_i + 32'd1 >= li_n)
                        fsm <= S_LI_ENC;
                    else
                        fsm <= S_LI_ON;
                end

                // Build the 44-byte line:
                // "<sum_on 10d> <sum_off 10d> <n_on 10d> <n_off 10d>\n"
                S_LI_ENC : begin
                    li_line[0]  <= li_sum_on_pad[79:72];
                    li_line[1]  <= li_sum_on_pad[71:64];
                    li_line[2]  <= li_sum_on_pad[63:56];
                    li_line[3]  <= li_sum_on_pad[55:48];
                    li_line[4]  <= li_sum_on_pad[47:40];
                    li_line[5]  <= li_sum_on_pad[39:32];
                    li_line[6]  <= li_sum_on_pad[31:24];
                    li_line[7]  <= li_sum_on_pad[23:16];
                    li_line[8]  <= li_sum_on_pad[15:8];
                    li_line[9]  <= li_sum_on_pad[7:0];
                    li_line[10] <= 8'h20;
                    li_line[11] <= li_sum_off_pad[79:72];
                    li_line[12] <= li_sum_off_pad[71:64];
                    li_line[13] <= li_sum_off_pad[63:56];
                    li_line[14] <= li_sum_off_pad[55:48];
                    li_line[15] <= li_sum_off_pad[47:40];
                    li_line[16] <= li_sum_off_pad[39:32];
                    li_line[17] <= li_sum_off_pad[31:24];
                    li_line[18] <= li_sum_off_pad[23:16];
                    li_line[19] <= li_sum_off_pad[15:8];
                    li_line[20] <= li_sum_off_pad[7:0];
                    li_line[21] <= 8'h20;
                    li_line[22] <= li_n_on_pad[79:72];
                    li_line[23] <= li_n_on_pad[71:64];
                    li_line[24] <= li_n_on_pad[63:56];
                    li_line[25] <= li_n_on_pad[55:48];
                    li_line[26] <= li_n_on_pad[47:40];
                    li_line[27] <= li_n_on_pad[39:32];
                    li_line[28] <= li_n_on_pad[31:24];
                    li_line[29] <= li_n_on_pad[23:16];
                    li_line[30] <= li_n_on_pad[15:8];
                    li_line[31] <= li_n_on_pad[7:0];
                    li_line[32] <= 8'h20;
                    li_line[33] <= li_n_off_pad[79:72];
                    li_line[34] <= li_n_off_pad[71:64];
                    li_line[35] <= li_n_off_pad[63:56];
                    li_line[36] <= li_n_off_pad[55:48];
                    li_line[37] <= li_n_off_pad[47:40];
                    li_line[38] <= li_n_off_pad[39:32];
                    li_line[39] <= li_n_off_pad[31:24];
                    li_line[40] <= li_n_off_pad[23:16];
                    li_line[41] <= li_n_off_pad[15:8];
                    li_line[42] <= li_n_off_pad[7:0];
                    li_line[43] <= 8'h0A;
                    str_idx     <= 7'd0;
                    fsm         <= S_LI_SEND;
                end

                S_LI_SEND : begin
                    if (tx_ready) begin
                        tx_load <= 1'b1;
                        tx_din  <= li_line[str_idx[5:0]];
                        if (str_idx == 7'd43)
                            fsm <= S_IDLE;
                        else
                            str_idx <= str_idx + 7'd1;
                    end
                end

                // ---- READ_LOCKINP cosine/sine IQ demodulator ---------------
                // Zero the accumulators + reset the period counter (prime_idx and
                // lip_p already latched in the dispatch).
                S_LIP_INIT : begin
                    lip_cnt       <= 6'd0;
                    lip_I         <= 48'sd0;
                    lip_Q         <= 48'sd0;
                    lip_n         <= 32'd0;
                    seq_wdog      <= 32'd0;
                    fsm           <= S_LIP_READ;
                end

                // Trigger a fresh read_one of the demod channel.
                S_LIP_READ : begin
                    det_read_one <= 1'b1;
                    det_site     <= lip_site;
                    seq_wdog     <= 32'd0;
                    fsm          <= S_LIP_WAIT;
                end

                // Wait for the fresh sample (or the read watchdog -> sentinel 0).
                S_LIP_WAIT : begin
                    if (det_code_valid) begin
                        lip_code     <= det_code;
                        det_code_ack <= 1'b1;
                        fsm          <= S_LIP_MAC;
                    end else if (seq_wdog >= READ_WDOG_CYC) begin
                        lip_code     <= 16'd0;     // dead-ADC sentinel -> MACs as 0
                        det_code_ack <= 1'b1;
                        fsm          <= S_LIP_MAC;
                    end else begin
                        seq_wdog <= seq_wdog + 32'd1;
                    end
                end

                // Multiply-accumulate against the per-prime cos/sin ROM. The ROM
                // index for the current sample is prime_base[lip_prime_idx] +
                // lip_cnt; lip_cnt wraps at lip_p so the reference is EXACTLY
                // periodic with period p. cos/sin and the code are read
                // combinationally from the CURRENT lip_code (latched in
                // S_LIP_WAIT, held stable here) and the CURRENT lip_cnt (advanced
                // at end of cycle via <=), so every one of the n_samples samples
                // contributes exactly once.
                S_LIP_MAC : begin
                    lip_I  <= lip_I + ($signed({1'b0, lip_code})
                                       * $signed(cos_rom[prime_base[lip_prime_idx] + lip_cnt]));
                    lip_Q  <= lip_Q + ($signed({1'b0, lip_code})
                                       * $signed(sin_rom[prime_base[lip_prime_idx] + lip_cnt]));
                    lip_cnt <= (lip_cnt == lip_p - 6'd1) ? 6'd0 : (lip_cnt + 6'd1);
                    lip_n   <= lip_n + 32'd1;
                    if (lip_n + 32'd1 >= lip_n_samples)
                        fsm <= S_LIP_ENC;
                    else
                        fsm <= S_LIP_READ;
                end

                // Build the 45-byte line: "<I 16-char signed> <Q 16-char signed>
                // <n 10-digit>\n". signed_dec48 lays out sign at [127:120], most-
                // significant digit at [119:112], ones at [7:0]; dec32_pad lays
                // out billions at [79:72], ones at [7:0]. We emit MSB-first.
                S_LIP_ENC : begin
                    lip_line[ 0] <= lip_I_dec[127:120]; lip_line[ 1] <= lip_I_dec[119:112];
                    lip_line[ 2] <= lip_I_dec[111:104]; lip_line[ 3] <= lip_I_dec[103:96];
                    lip_line[ 4] <= lip_I_dec[95:88];   lip_line[ 5] <= lip_I_dec[87:80];
                    lip_line[ 6] <= lip_I_dec[79:72];   lip_line[ 7] <= lip_I_dec[71:64];
                    lip_line[ 8] <= lip_I_dec[63:56];   lip_line[ 9] <= lip_I_dec[55:48];
                    lip_line[10] <= lip_I_dec[47:40];   lip_line[11] <= lip_I_dec[39:32];
                    lip_line[12] <= lip_I_dec[31:24];   lip_line[13] <= lip_I_dec[23:16];
                    lip_line[14] <= lip_I_dec[15:8];    lip_line[15] <= lip_I_dec[7:0];
                    lip_line[16] <= 8'h20;              // ' '
                    lip_line[17] <= lip_Q_dec[127:120]; lip_line[18] <= lip_Q_dec[119:112];
                    lip_line[19] <= lip_Q_dec[111:104]; lip_line[20] <= lip_Q_dec[103:96];
                    lip_line[21] <= lip_Q_dec[95:88];   lip_line[22] <= lip_Q_dec[87:80];
                    lip_line[23] <= lip_Q_dec[79:72];   lip_line[24] <= lip_Q_dec[71:64];
                    lip_line[25] <= lip_Q_dec[63:56];   lip_line[26] <= lip_Q_dec[55:48];
                    lip_line[27] <= lip_Q_dec[47:40];   lip_line[28] <= lip_Q_dec[39:32];
                    lip_line[29] <= lip_Q_dec[31:24];   lip_line[30] <= lip_Q_dec[23:16];
                    lip_line[31] <= lip_Q_dec[15:8];    lip_line[32] <= lip_Q_dec[7:0];
                    lip_line[33] <= 8'h20;              // ' '
                    lip_line[34] <= lip_n_dec[79:72];   lip_line[35] <= lip_n_dec[71:64];
                    lip_line[36] <= lip_n_dec[63:56];   lip_line[37] <= lip_n_dec[55:48];
                    lip_line[38] <= lip_n_dec[47:40];   lip_line[39] <= lip_n_dec[39:32];
                    lip_line[40] <= lip_n_dec[31:24];   lip_line[41] <= lip_n_dec[23:16];
                    lip_line[42] <= lip_n_dec[15:8];    lip_line[43] <= lip_n_dec[7:0];
                    lip_line[44] <= 8'h0A;              // '\n'
                    str_idx      <= 7'd0;
                    fsm          <= S_LIP_SEND;
                end

                S_LIP_SEND : begin
                    if (tx_ready) begin
                        tx_load <= 1'b1;
                        tx_din  <= lip_line[str_idx[6:0]];
                        if (str_idx == 7'd44)
                            fsm <= S_IDLE;
                        else
                            str_idx <= str_idx + 7'd1;
                    end
                end

                default : fsm <= S_IDLE;
            endcase
        end
    end
endmodule