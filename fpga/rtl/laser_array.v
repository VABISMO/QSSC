`timescale 1ns / 1ps
//============================================================================
// laser_array.v
// ----------------------------------------------------------------------------
// 8x8 VCSEL array driver. A fire request (fire with row, col, duration_ms)
// asserts one row enable and one column select for the requested duration,
// then deasserts. Rows are active-high enables; columns are active-low
// sinks (the VCSEL cathode is pulled low through the selected column).
// The timer counts at the 100 MHz system clock: 1 ms = 100,000 ticks.
//============================================================================
module laser_array (
    input             clk,
    input             reset,
    input             fire,           // level/edge: begin a pulse
    input      [2:0]  row,            // 0..7
    input      [2:0]  col,            // 0..7
    input      [31:0] duration_ms,    // on-time in milliseconds
    output reg [7:0]  laser_rows,     // row enables (active high)
    output reg [7:0]  laser_cols,     // column selects (active low)
    output            busy
);
    localparam [31:0] TICKS_PER_MS = 32'd100_000; // 100 MHz

    reg        firing;
    reg [31:0] timer;
    reg        fire_d;

    initial begin
        firing     = 1'b0;
        timer      = 32'd0;
        fire_d     = 1'b0;
        laser_rows = 8'd0;
        laser_cols = 8'hFF;
    end

    assign busy = firing;

    // rising-edge detect on fire
    always @(posedge clk) begin
        if (reset) begin
            fire_d <= 1'b0;
        end else begin
            fire_d <= fire;
        end
    end

    always @(posedge clk) begin
        if (reset) begin
            firing     <= 1'b0;
            timer      <= 32'd0;
            laser_rows <= 8'd0;
            laser_cols <= 8'hFF;
        end else if (fire & ~fire_d & ~firing) begin
            // start a new pulse
            firing     <= 1'b1;
            timer      <= (duration_ms > 0) ? (duration_ms * TICKS_PER_MS) : 32'd1;
            laser_rows <= (8'd1 << row);
            laser_cols <= ~(8'd1 << col);
        end else if (firing) begin
            if (timer <= 32'd1) begin
                firing     <= 1'b0;
                laser_rows <= 8'd0;
                laser_cols <= 8'hFF;
                timer      <= 32'd0;
            end else begin
                timer <= timer - 32'd1;
            end
        end
    end
endmodule