`timescale 1ns / 1ps
//============================================================================
// photon_counter_array.v
// ----------------------------------------------------------------------------
// 64-channel parallel photon counter (digital readout -- replaces the old
// analog MUX + XADC serial chain).
//
// Each detector channel has its own TIA + Schmitt comparator on the board;
// the comparator output is brought to a dedicated FPGA LVCMOS input
// `det_in[i]`. This module keeps one 16-bit free-running counter per
// channel, incrementing on every rising edge of det_in[i] (a photon event
// that crosses the comparator threshold). The host reads a snapshot of the
// counters over UART; the microwave drive is modulated at 1 kHz and
// `mod_sync` is the reference the host uses to lock-in demodulate the
// per-channel count streams (the lock-in math is host-side, as before).
//
//   read_one  -- snapshot counter[`site`], present code + code_valid
//   read_all  -- stream all 64 counters (code/code_valid/code_idx, ack to
//                advance), sweep_done at the end
//   clear     -- 1-cycle pulse: reset every counter to 0 (host windows the
//                integration by clearing, reading after the integration time)
//
// Counters are 16-bit (0..65535). At a 1 kHz lock-in window a channel would
// need >65k events in one window to overflow, far above the expected photon
// rate; the host clears between windows. CALIBRATE clears per emitter pixel.
//============================================================================
module photon_counter_array #(
    parameter N_DET = 64,
    parameter CLK_HZ = 100_000_000
) (
    input             clk,
    input             reset,

    // host command interface (from command_parser)
    input             read_one,       // 1-cycle pulse: snapshot counter `site`
    input             read_all,       // 1-cycle pulse: stream all counters
    input      [5:0]  site,           // channel index for read_one (0..N_DET-1)
    input             clear,          // 1-cycle pulse: reset all counters
    output reg        busy,           // high while a read/sweep is in progress

    // code stream out (one counter value at a time)
    output reg [15:0] code,           // 16-bit counter snapshot
    output reg        code_valid,     // held until code_ack: `code` is valid
    output reg [5:0]  code_idx,       // which channel this code belongs to
    output reg        sweep_done,    // 1-cycle pulse: read_all sweep complete
    input             code_ack,       // consumer latched the code; advance

    // 64 comparator inputs (board front-end -> FPGA LVCMOS33)
    input      [63:0] det_in,

    // lock-in modulation reference (1 kHz square wave)
    output reg        mod_sync
);
    localparam [5:0] N6 = N_DET - 1;          // 63
    // 1 kHz toggle period at CLK_HZ: toggle every CLK_HZ/2000 cycles
    localparam [31:0] MOD_HALF = (CLK_HZ / 2000);

    // FSM
    localparam [1:0] S_IDLE     = 2'd0;
    localparam [1:0] S_CAPTURE  = 2'd1;   // snapshot cnt[idx], raise code_valid
    localparam [1:0] S_WAIT_ACK = 2'd2;   // hold code_valid until consumer acks
    localparam [1:0] S_NEXT     = 2'd3;   // advance sweep index

    reg [1:0]  state;
    reg [5:0]  idx;            // sweep index (read_all)
    reg        sweep;          // 1 = read_all sweep, 0 = read_one
    reg [63:0] det_in_d;       // previous det_in (per-channel rising-edge detect)
    reg [31:0] mod_cnt;
    // 64 x 16-bit free-running photon counters (inferred block RAM / LUTRAM
    // or distributed FFs depending on tool; small enough for FFs here).
    reg [15:0] cnt [0:63];

    integer p;
    initial begin
        state      = S_IDLE;
        idx        = 6'd0;
        sweep      = 1'b0;
        busy       = 1'b0;
        code       = 16'd0;
        code_valid = 1'b0;
        code_idx   = 6'd0;
        sweep_done = 1'b0;
        mod_sync   = 1'b0;
        mod_cnt    = 32'd0;
        det_in_d   = 64'd0;
        for (p = 0; p < 64; p = p + 1) cnt[p] = 16'd0;
    end

    // ---- per-channel rising-edge photon counting ----
    always @(posedge clk) begin
        if (reset) begin
            det_in_d <= 64'd0;
            for (p = 0; p < 64; p = p + 1) cnt[p] <= 16'd0;
        end else begin
            det_in_d <= det_in;
            for (p = 0; p < 64; p = p + 1) begin
                if (clear) cnt[p] <= 16'd0;
                else if (det_in[p] & ~det_in_d[p]) cnt[p] <= cnt[p] + 16'd1;
            end
        end
    end

    // ---- 1 kHz lock-in modulation reference ----
    always @(posedge clk) begin
        if (reset) begin
            mod_sync <= 1'b0;
            mod_cnt  <= 32'd0;
        end else if (mod_cnt >= MOD_HALF) begin
            mod_sync <= ~mod_sync;
            mod_cnt  <= 32'd0;
        end else begin
            mod_cnt <= mod_cnt + 32'd1;
        end
    end

    // ---- snapshot / stream FSM ----
    always @(posedge clk) begin
        if (reset) begin
            state      <= S_IDLE;
            idx        <= 6'd0;
            sweep      <= 1'b0;
            busy       <= 1'b0;
            code_valid <= 1'b0;
            sweep_done <= 1'b0;
        end else begin
            sweep_done <= 1'b0;            // default 1-cycle pulse
            // code_valid is HELD (cleared only on ack in S_WAIT_ACK) so the
            // slow UART consumer can take many cycles to drain each code.

            case (state)
                S_IDLE: begin
                    if (read_one) begin
                        sweep <= 1'b0;
                        idx   <= site;
                        busy  <= 1'b1;
                        state <= S_CAPTURE;
                    end else if (read_all) begin
                        sweep <= 1'b1;
                        idx   <= 6'd0;
                        busy  <= 1'b1;
                        state <= S_CAPTURE;
                    end
                end

                S_CAPTURE: begin
                    code       <= cnt[idx];   // snapshot the live counter
                    code_idx   <= idx;
                    code_valid <= 1'b1;       // held until code_ack
                    state      <= S_WAIT_ACK;
                end

                S_WAIT_ACK: begin
                    if (code_ack) begin
                        code_valid <= 1'b0;
                        if (sweep) state <= S_NEXT;
                        else begin
                            busy  <= 1'b0;   // read_one done
                            state <= S_IDLE;
                        end
                    end
                end

                S_NEXT: begin
                    if (idx >= N6) begin
                        sweep_done <= 1'b1;
                        busy       <= 1'b0;
                        state      <= S_IDLE;
                    end else begin
                        idx   <= idx + 6'd1;
                        state <= S_CAPTURE;  // snapshot next channel
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule