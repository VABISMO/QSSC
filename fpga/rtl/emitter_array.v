`timescale 1ns / 1ps
//============================================================================
// emitter_array.v
// ----------------------------------------------------------------------------
// 64 individual on-chip CQD/ASE/LED emitter enable driver (parallel
// architecture -- no 8x8 row/col matrix).
//
// The FPGA does not drive the emitters directly: each emitter needs 4-8 V /
// 2-20 mA, more than a 3.3 V FPGA pin can source. Each FPGA `emitter_en[i]`
// line drives a 74HCT240 buffer -> P-MOS high-side switch -> 220 R set
// resistor -> emitter anode. A drive request asserts a single
// `emitter_en[index]` for the requested duration, lighting exactly one
// emitter, then deasserts. The timer counts at the 100 MHz system clock:
// 1 ms = 100,000 ticks.
//============================================================================
module emitter_array (
    input             clk,
    input             reset,
    input             drive,          // rising edge: begin an emitter pulse
    input      [5:0]  index,          // 0..63 (which emitter to fire)
    input      [31:0] duration_ms,     // on-time in milliseconds
    output reg [63:0] emitter_en,     // 64 individual emitter enables (active high)
    output            busy
);
    // FAST_SIM shrinks the 1 ms pump to a few cycles so the self-checking
    // testbench doesn't burn 6.4M cycles on the 64-pixel CALIBRATE sweep.
    // Production timing (no FAST_SIM define) is 100,000 ticks/ms at 100 MHz.
`ifdef FAST_SIM
    localparam [31:0] TICKS_PER_MS = 32'd10;        // 10 clk per "ms" in fast sim
    localparam [31:0] MAX_DUR_MS   = 32'd40_000;
    localparam [31:0] MAX_TICKS    = 32'd400_000;   // scaled (40 s * 10 clk/ms)
`else
    localparam [31:0] TICKS_PER_MS = 32'd100_000;   // 100 MHz -> 1 ms = 100k ticks
    // Saturation guard: duration_ms * TICKS_PER_MS overflows a 32-bit timer at
    // ~42.9 s. Pulses longer than MAX_DUR_MS are clamped to MAX_TICKS (40 s).
    localparam [31:0] MAX_DUR_MS   = 32'd40_000;
    localparam [31:0] MAX_TICKS    = 32'd4_000_000_000;
`endif

    reg        firing;
    reg [31:0] timer;
    reg        drive_d;

    initial begin
        firing     = 1'b0;
        timer      = 32'd0;
        drive_d    = 1'b0;
        emitter_en = 64'd0;
    end

    assign busy = firing;

    // rising-edge detect on drive
    always @(posedge clk) begin
        if (reset) drive_d <= 1'b0;
        else       drive_d <= drive;
    end

    always @(posedge clk) begin
        if (reset) begin
            firing     <= 1'b0;
            timer      <= 32'd0;
            emitter_en <= 64'd0;
        end else if (drive & ~drive_d & ~firing) begin
            // start a new emitter pulse. A 0-ms request is a no-op: a single
            // 10-ns tick is physically meaningless as an optical pump, so we
            // simply don't fire. Saturate the timer against 32-bit overflow.
            if (duration_ms == 32'd0) begin
                firing <= 1'b0;
            end else begin
                firing     <= 1'b1;
                timer      <= (duration_ms > MAX_DUR_MS) ? MAX_TICKS
                                                         : (duration_ms * TICKS_PER_MS);
                emitter_en <= (64'd1 << index);    // one-hot enable
            end
        end else if (firing) begin
            if (timer <= 32'd1) begin
                firing     <= 1'b0;
                emitter_en <= 64'd0;
                timer      <= 32'd0;
            end else begin
                timer <= timer - 32'd1;
            end
        end
    end
endmodule