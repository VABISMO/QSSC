`timescale 1ns / 1ps
//============================================================================
// dac_ctrl.v
// ----------------------------------------------------------------------------
// Generic 16-bit SPI master for the MCP4921 12-bit DAC (Invention 5 field-
// gradient current driver). Modeled on spi_master.v (the ADF4351 master) but
// shifts 16 bits MSB-first and latches on CS_n (idle high, go low for the
// transfer, return high to latch -- the MCP4921 LOAD/CS edge).
//
// The 16-bit write word is assembled by top_level as
//   {4'b0011, data[11:0]}      (channel A, unbuffered, 1x gain, !SHDN)
// so this module stays a dumb shifter with no DAC-specific framing. Protocol:
// on `start`, drive CS_n low, shift 16 bits MSB-first (data set up before the
// SCLK rising edge, held through the high half -- the MCP4921 samples on the
// SCLK falling edge and sees the same bit still valid), then raise CS_n to
// latch. One transaction per start. `done` pulses for one clock when complete.
// CLK_DIV sets the SCLK half-period (default 4 -> 40 ns high / 40 ns low =
// 12.5 MHz SCLK from 100 MHz; the MCP4921 supports 20 MHz at Vdd >= 4.5 V).
//============================================================================
module dac_ctrl #(
    parameter CLK_DIV = 4
) (
    input            clk,
    input            reset,
    input            start,
    input      [15:0] data_in,
    output           busy,
    output           done,
    output reg       sclk,
    output reg       mosi,
    output reg       cs_n
);
    localparam [2:0] S_IDLE  = 3'd0;
    localparam [2:0] S_PRE   = 3'd1;   // set up first bit, CS_n low
    localparam [2:0] S_HI    = 3'd2;   // SCLK high (sample by device)
    localparam [2:0] S_LO    = 3'd3;   // SCLK low, advance
    localparam [2:0] S_LATCH = 3'd4;   // CS_n high (latch the DAC)
    localparam [2:0] S_DONE  = 3'd5;

    reg [2:0]  state;
    reg [4:0]  bit_idx;       // 16 bits -> 0..15, plus a guard
    reg [15:0] shift;
    reg [15:0] div_cnt;
    reg        done_reg;

    initial begin
        state    = S_IDLE;
        bit_idx  = 5'd15;
        shift    = 16'd0;
        div_cnt  = 16'd0;
        done_reg = 1'b0;
        sclk     = 1'b0;
        mosi     = 1'b0;
        cs_n     = 1'b1;        // idle CS high
    end

    assign busy = (state != S_IDLE);
    assign done = done_reg;

    always @(posedge clk) begin
        if (reset) begin
            state    <= S_IDLE;
            sclk     <= 1'b0;
            mosi     <= 1'b0;
            cs_n     <= 1'b1;
            done_reg <= 1'b0;
            bit_idx  <= 5'd15;
            div_cnt  <= 16'd0;
        end else begin
            done_reg <= 1'b0;     // default, pulsed in S_DONE
            case (state)
                S_IDLE : begin
                    cs_n    <= 1'b1;
                    sclk    <= 1'b0;
                    div_cnt <= 16'd0;
                    if (start) begin
                        shift   <= data_in;
                        bit_idx <= 5'd15;
                        cs_n    <= 1'b0;     // bring CS low to begin
                        state   <= S_PRE;
                    end
                end
                S_PRE : begin
                    mosi    <= shift[bit_idx];
                    sclk    <= 1'b0;
                    div_cnt <= 16'd0;
                    state   <= S_HI;
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
                        if (bit_idx == 5'd0) begin
                            state <= S_LATCH;
                        end else begin
                            bit_idx <= bit_idx - 5'd1;
                            mosi    <= shift[bit_idx - 5'd1];
                            state   <= S_HI;
                        end
                    end else begin
                        div_cnt <= div_cnt + 16'd1;
                    end
                end
                S_LATCH : begin
                    cs_n  <= 1'b1;       // latch on CS rising edge
                    state <= S_DONE;
                end
                S_DONE : begin
                    done_reg <= 1'b1;
                    cs_n     <= 1'b1;
                    state    <= S_IDLE;
                end
                default : state <= S_IDLE;
            endcase
        end
    end
endmodule