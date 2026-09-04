`timescale 1ns / 1ps
//============================================================================
// emitter_array.v  --  parallel 64-emitter CQD driver via 3x TLC5947
// ----------------------------------------------------------------------------
// High-level sequencer above tlc5947_ctrl. Owns the "which emitters, how
// long, how bright" policy and drives the TLC5947's grayscale-write + load +
// blank-run handshake. The 64 CQD emitters fire IN PARALLEL: each channel has
// its own 12-bit grayscale word in the TLC5947, so multiple emitters light
// simultaneously at independent intensities the instant blank_run goes high.
// This replaces the old one-hot 64-line EM_DRV matrix (one emitter at a time).
//
// Command interface (1-cycle pulses from command_parser):
//   cmd_drive   + index + duration_ms  -- pump ONE emitter `index` at full
//                                         (gs=4095) for duration_ms, then off.
//                                         Backwards-compatible DRIVE_EMITTER.
//   cmd_set     + index + level        -- set emitter `index` grayscale to
//                                         `level` (0..4095) and load it. Stays
//                                         armed; pair with cmd_pattern.
//   cmd_pattern + duration_ms          -- fire ALL armed emitters (every channel
//                                         whose gs>0) in parallel for duration_ms.
//                                         Does NOT clear afterward, so you can
//                                         SET_EMITTER several then DRIVE_PATTERN.
//   cmd_clear                           -- zero all 64 grayscale levels (all off).
//
// busy is high from command dispatch until the final load/run completes. The
// parser replies OK immediately for cmd_drive/cmd_pattern (the pump runs in
// the background, like the old DRIVE_EMITTER) and waits for !busy for
// cmd_set/cmd_clear (quick load ops, serialized like SET_THRESHOLD).
//============================================================================
module emitter_array (
    input             clk,
    input             reset,
    // command interface (from command_parser)
    input             cmd_drive,      // pump single emitter `index`, duration_ms
    input             cmd_set,        // set gs[index] = level
    input             cmd_pattern,    // fire all armed emitters, duration_ms
    input             cmd_clear,      // zero all 64 grayscale levels
    input      [5:0]  index,
    input      [11:0] level,
    input      [31:0] duration_ms,
    output            busy,
    // 4-wire TLC5947 interface (to top-level pins)
    output            em_sclk,
    output            em_sin,
    output            em_xlat,
    output            em_blank
);
`ifdef FAST_SIM
    localparam [31:0] TICKS_PER_MS = 32'd10;        // 10 clk per "ms" in fast sim
    localparam [31:0] MAX_DUR_MS   = 32'd40_000;
    localparam [31:0] MAX_TICKS    = 32'd400_000;   // scaled (40 s * 10 clk/ms)
`else
    localparam [31:0] TICKS_PER_MS = 32'd100_000;   // 100 MHz -> 1 ms = 100k ticks
    localparam [31:0] MAX_DUR_MS   = 32'd40_000;    // saturate at 40 s (32-bit ovf guard)
    localparam [31:0] MAX_TICKS    = 32'd4_000_000_000;
`endif
    localparam [11:0] GS_FULL = 12'd4095;

    // mode (what to do after the grayscale load completes)
    localparam [2:0] M_SET     = 3'd0;   // set/clear: load then idle
    localparam [2:0] M_CLEAR   = 3'd1;   // (same path, distinct for clarity)
    localparam [2:0] M_DRIVE   = 3'd2;   // load, run, then clear+load
    localparam [2:0] M_PATTERN = 3'd3;   // load, run, then idle (leave gs armed)
    localparam [2:0] M_END     = 3'd4;   // post-drive clear-off load, then idle

    localparam [3:0] S_IDLE       = 4'd0;
    localparam [3:0] S_WRITE      = 4'd1;   // burst gs_we pulses (1 or 64)
    localparam [3:0] S_LOAD       = 4'd2;   // pulse load_start
    localparam [3:0] S_LOAD_WAIT  = 4'd3;   // wait !load_busy, then dispatch by mode
    localparam [3:0] S_RUN        = 4'd4;   // blank_run=1, count down pump timer
    localparam [3:0] S_DONE       = 4'd5;   // terminal (back to idle)

    reg [3:0]  state;
    reg [2:0]  mode;
    reg [6:0]  we_count;       // remaining gs_we pulses in this write burst
    reg [5:0]  we_idx;         // current write address
    reg [11:0] we_data;        // current write data
    reg [5:0]  drive_idx;      // channel to clear after a cmd_drive pump
    reg [31:0] run_ticks;      // pump duration in clk cycles
    reg [31:0] timer;

    // TLC5947 controller
    reg        gs_we_r;
    reg [5:0]  gs_idx_r;
    reg [11:0] gs_data_r;
    reg        load_start_r;
    reg        blank_run_r;
    wire       load_busy;

    tlc5947_ctrl #(.N_CH(64)) tlc_inst (
        .clk        (clk),
        .reset      (reset),
        .gs_we      (gs_we_r),
        .gs_idx     (gs_idx_r),
        .gs_data    (gs_data_r),
        .load_start (load_start_r),
        .load_busy  (load_busy),
        .blank_run  (blank_run_r),
        .em_sclk    (em_sclk),
        .em_sin     (em_sin),
        .em_xlat    (em_xlat),
        .em_blank   (em_blank)
    );

    assign busy = (state != S_IDLE);

    initial begin
        state       = S_IDLE;
        mode        = M_SET;
        we_count    = 7'd0;
        we_idx      = 6'd0;
        we_data     = 12'd0;
        drive_idx   = 6'd0;
        run_ticks   = 32'd0;
        timer       = 32'd0;
        gs_we_r     = 1'b0;
        gs_idx_r    = 6'd0;
        gs_data_r   = 12'd0;
        load_start_r = 1'b0;
        blank_run_r = 1'b0;
    end

    always @(posedge clk) begin
        if (reset) begin
            state        <= S_IDLE;
            mode         <= M_SET;
            we_count     <= 7'd0;
            we_idx       <= 6'd0;
            we_data      <= 12'd0;
            drive_idx    <= 6'd0;
            run_ticks    <= 32'd0;
            timer        <= 32'd0;
            gs_we_r      <= 1'b0;
            gs_idx_r     <= 6'd0;
            gs_data_r    <= 12'd0;
            load_start_r <= 1'b0;
            blank_run_r  <= 1'b0;
        end else begin
            // one-cycle pulse defaults
            gs_we_r      <= 1'b0;
            load_start_r <= 1'b0;

            case (state)
                // ---- wait for a command ----------------------------------
                S_IDLE: begin
                    blank_run_r <= 1'b0;
                    if (cmd_clear) begin
                        we_count  <= 7'd64;
                        we_idx    <= 6'd0;
                        we_data   <= 12'd0;
                        mode      <= M_CLEAR;
                        state     <= S_WRITE;
                    end else if (cmd_set) begin
                        we_count  <= 7'd1;
                        we_idx    <= index;
                        we_data   <= level;
                        mode      <= M_SET;
                        state     <= S_WRITE;
                    end else if (cmd_drive) begin
                        we_count  <= 7'd1;
                        we_idx    <= index;
                        we_data   <= GS_FULL;
                        drive_idx <= index;
                        // saturate pump duration against 32-bit timer overflow
                        run_ticks <= (duration_ms == 32'd0)        ? 32'd0 :
                                     (duration_ms > MAX_DUR_MS)    ? MAX_TICKS :
                                                                     (duration_ms * TICKS_PER_MS);
                        mode      <= M_DRIVE;
                        state     <= S_WRITE;
                    end else if (cmd_pattern) begin
                        run_ticks <= (duration_ms == 32'd0)        ? 32'd0 :
                                     (duration_ms > MAX_DUR_MS)    ? MAX_TICKS :
                                                                     (duration_ms * TICKS_PER_MS);
                        mode      <= M_PATTERN;
                        state     <= S_LOAD;      // no write; load current gs
                    end
                end

                // ---- burst gs_we pulses (1 for set/drive, 64 for clear) ----
                S_WRITE: begin
                    gs_we_r   <= 1'b1;
                    gs_idx_r  <= we_idx;
                    gs_data_r <= we_data;
                    if (we_count <= 7'd1) begin
                        state <= S_LOAD;
                    end else begin
                        we_count <= we_count - 7'd1;
                        we_idx   <= we_idx + 6'd1;   // clear sweeps ch0..63
                    end
                end

                // ---- pulse load_start (shift 864 bits + XLAT) --------------
                S_LOAD: begin
                    load_start_r <= 1'b1;
                    state        <= S_LOAD_WAIT;
                end

                // ---- wait for the TLC5947 shift+latch to finish -----------
                S_LOAD_WAIT: begin
                    if (!load_busy) begin
                        case (mode)
                            M_SET, M_CLEAR, M_END: begin
                                blank_run_r <= 1'b0;
                                state       <= S_IDLE;
                            end
                            M_DRIVE, M_PATTERN: begin
                                if (run_ticks == 32'd0) begin
                                    // zero-duration: no pump window. DRIVE must
                                    // still clear the channel it armed; PATTERN
                                    // just stops with gs left armed.
                                    if (mode == M_DRIVE) begin
                                        we_count  <= 7'd1;
                                        we_idx    <= drive_idx;
                                        we_data   <= 12'd0;
                                        mode      <= M_END;
                                        state     <= S_WRITE;
                                    end else begin
                                        blank_run_r <= 1'b0;
                                        state       <= S_IDLE;
                                    end
                                end else begin
                                    blank_run_r <= 1'b1;     // emitters ON
                                    timer       <= run_ticks;
                                    state       <= S_RUN;
                                end
                            end
                            default: state <= S_IDLE;
                        endcase
                    end
                end

                // ---- pump window: blank_run=1, count down ----------------
                S_RUN: begin
                    if (timer <= 32'd1) begin
                        blank_run_r <= 1'b0;       // emitters OFF
                        if (mode == M_DRIVE) begin
                            // clear the single armed channel, then load off-state
                            we_count  <= 7'd1;
                            we_idx    <= drive_idx;
                            we_data   <= 12'd0;
                            mode      <= M_END;
                            state     <= S_WRITE;
                        end else begin
                            // M_PATTERN: leave gs armed, return to idle
                            state <= S_IDLE;
                        end
                    end else begin
                        timer <= timer - 32'd1;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule