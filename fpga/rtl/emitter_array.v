`timescale 1ns / 1ps
//============================================================================
// emitter_array.v
// ----------------------------------------------------------------------------
// 8x8 on-chip CQD ASE/LED emitter array enable driver.
//
// The FPGA does not drive the CQD pixels directly -- the emitter stack needs
// 4-8 V / 2-20 mA per pixel, more than a 3.3 V FPGA pin can source. This module
// instead drives an *external* higher-voltage emitter driver through the 8-row
// anode-enable x 8-column cathode-enable crossbar: a drive request asserts one
// row enable (active high) and one column select (active low) for the requested
// duration, lighting exactly one CQD pixel, then deasserts. The timer counts at
// the 100 MHz system clock: 1 ms = 100,000 ticks.
//
// Port names mirror the crossbar topology, not the old VCSEL naming.
//============================================================================
module emitter_array (
    input             clk,
    input             reset,
    input             drive,          // level/edge: begin a pixel pulse
    input      [2:0]  row,             // 0..7
    input      [2:0]  col,             // 0..7
    input      [31:0] duration_ms,     // on-time in milliseconds
    output reg [7:0]  emitter_rows,    // row anode-enables (active high)
    output reg [7:0]  emitter_cols,    // column cathode-enables (active low)
    output            busy
);
    localparam [31:0] TICKS_PER_MS  = 32'd100_000; // 100 MHz -> 1 ms = 100k ticks
    // Saturation guard: duration_ms * TICKS_PER_MS overflows a 32-bit timer at
    // ~42.9 s (2^32 / 100k). Any pulse longer than MAX_DUR_MS is clamped to
    // MAX_TICKS (40 s, still < 2^32) instead of wrapping to a short glitch.
    localparam [31:0] MAX_DUR_MS  = 32'd40_000;   // 40 s ceiling
    localparam [31:0] MAX_TICKS   = 32'd4_000_000_000; // 40 s * 100k < 2^32

    reg        firing;
    reg [31:0] timer;
    reg        drive_d;

    initial begin
        firing       = 1'b0;
        timer        = 32'd0;
        drive_d      = 1'b0;
        emitter_rows = 8'd0;
        emitter_cols = 8'hFF;
    end

    assign busy = firing;

    // rising-edge detect on drive
    always @(posedge clk) begin
        if (reset) begin
            drive_d <= 1'b0;
        end else begin
            drive_d <= drive;
        end
    end

    always @(posedge clk) begin
        if (reset) begin
            firing       <= 1'b0;
            timer         <= 32'd0;
            emitter_rows <= 8'd0;
            emitter_cols <= 8'hFF;
        end else if (drive & ~drive_d & ~firing) begin
            // start a new pixel pulse. A 0-ms request is a no-op: a single
            // 10-ns tick is physically meaningless as an optical pump for the
            // CQD pixels (they need a real ms-scale pulse to emit), so rather
            // than fire a 1-tick glitch we simply don't fire. Saturate the
            // timer against 32-bit overflow (see MAX_DUR_MS / MAX_TICKS).
            if (duration_ms == 32'd0) begin
                firing <= 1'b0;            // no-op: nothing to time
            end else begin
                firing       <= 1'b1;
                timer        <= (duration_ms > MAX_DUR_MS) ? MAX_TICKS
                                                          : (duration_ms * TICKS_PER_MS);
                emitter_rows <= (8'd1 << row);
                emitter_cols <= ~(8'd1 << col);
            end
        end else if (firing) begin
            if (timer <= 32'd1) begin
                firing       <= 1'b0;
                emitter_rows <= 8'd0;
                emitter_cols <= 8'hFF;
                timer        <= 32'd0;
            end else begin
                timer <= timer - 32'd1;
            end
        end
    end
endmodule