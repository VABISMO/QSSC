`timescale 1ns / 1ps
//============================================================================
// spi_master.v
// ----------------------------------------------------------------------------
// Generic 32-bit SPI master tailored to the ADF4351 serial interface.
// Protocol: on `start`, drive LE low, shift 32 bits MSB-first on the rising
// edge of SCLK (data set up on falling edge), then pulse LE high to latch.
// One transaction per start. `done` pulses for one clock when complete.
// CLK_DIV sets the SCLK half-period (default 4 -> 40 ns high / 40 ns low =
// 12.5 MHz SCLK from 100 MHz; both halves >= the ADF4351 25 ns min).
//============================================================================
module spi_master #(
    parameter CLK_DIV = 4
) (
    input            clk,
    input            reset,
    input            start,
    input      [31:0] data_in,
    output           busy,
    output           done,
    output reg       sclk,
    output reg       mosi,
    output reg       le
);
    localparam [2:0] S_IDLE  = 3'd0;
    localparam [2:0] S_PRE   = 3'd1;   // set up first bit, LE low
    localparam [2:0] S_HI    = 3'd2;   // SCLK high (sample by device)
    localparam [2:0] S_LO    = 3'd3;   // SCLK low, advance
    localparam [2:0] S_LATCH = 3'd4;   // LE high
    localparam [2:0] S_DONE  = 3'd5;

    reg [2:0]  state;
    reg [5:0]  bit_idx;       // 32 bits -> 0..31, plus a guard
    reg [31:0] shift;
    reg [15:0] div_cnt;
    reg        done_reg;

    initial begin
        state    = S_IDLE;
        bit_idx  = 6'd31;
        shift    = 32'd0;
        div_cnt  = 16'd0;
        done_reg = 1'b0;
        sclk     = 1'b0;
        mosi     = 1'b0;
        le       = 1'b1;        // idle LE high
    end

    assign busy = (state != S_IDLE);
    assign done = done_reg;

    always @(posedge clk) begin
        if (reset) begin
            state    <= S_IDLE;
            sclk     <= 1'b0;
            mosi     <= 1'b0;
            le       <= 1'b1;
            done_reg <= 1'b0;
            bit_idx  <= 6'd31;
            div_cnt  <= 16'd0;
        end else begin
            done_reg <= 1'b0;     // default, pulsed in S_DONE
            case (state)
                S_IDLE : begin
                    le      <= 1'b1;
                    sclk    <= 1'b0;
                    div_cnt <= 16'd0;
                    if (start) begin
                        shift   <= data_in;
                        bit_idx <= 6'd31;
                        le      <= 1'b0;     // bring LE low to begin
                        state   <= S_PRE;
                    end
                end
                S_PRE : begin
                    mosi   <= shift[bit_idx];
                    sclk   <= 1'b0;
                    div_cnt<= 16'd0;
                    state  <= S_HI;
                end
                S_HI : begin
                    if (div_cnt == CLK_DIV-1) begin
                        div_cnt <= 16'd0;
                        sclk    <= 1'b1;
                        state   <= S_LO;
                    end else begin
                        div_cnt <= div_cnt + 16'd1;
                    end
                end
                S_LO : begin
                    if (div_cnt == CLK_DIV-1) begin
                        div_cnt <= 16'd0;
                        sclk    <= 1'b0;
                        if (bit_idx == 6'd0) begin
                            state <= S_LATCH;
                        end else begin
                            bit_idx <= bit_idx - 6'd1;
                            mosi    <= shift[bit_idx - 6'd1];
                            state   <= S_HI;
                        end
                    end else begin
                        div_cnt <= div_cnt + 16'd1;
                    end
                end
                S_LATCH : begin
                    le    <= 1'b1;       // latch on LE rising edge
                    state <= S_DONE;
                end
                S_DONE : begin
                    done_reg <= 1'b1;
                    le       <= 1'b1;
                    state    <= S_IDLE;
                end
                default : state <= S_IDLE;
            endcase
        end
    end
endmodule