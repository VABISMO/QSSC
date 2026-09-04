`timescale 1ns / 1ps
//============================================================================
// tlc5947_ctrl.v
// ----------------------------------------------------------------------------
// 3-chip daisy-chain TLC5947 24-channel 12-bit PWM constant-current LED
// driver controller (TI SBVS114B). The QSSC emitter array is 64 per-pixel
// cathodes, each sinking into one TLC5947 OUTn; the 64 emitter anodes share
// a common +12V_EMIT rail (EM_ANODE die-attach; the CQD EL turn-on is 4-8 V
// per the fabrication protocol, so a 12 V LED supply gives the TLC5947
// constant-current sink headroom -- OUT pins tolerate 33 V). Three TLC5947s
// daisy-chain
// SIN->SOUT for 72 sink channels (64 used = channels 0..63; channels 64..71
// are the unused OUT16..OUT23 of chip 2 + its SOUT, held at grayscale 0).
//
// 4-wire FPGA -> TLC5947 interface (the ONLY emitter pins on the FPGA -- the
// old 64-line EM_DRV/GPIO matrix is gone, freeing 60 FPGA IO):
//   em_sclk  - shift clock (<= 30 MHz per datasheet; this core runs at
//              clk/(2*SCLK_DIV) = 16.7 MHz production, 50 MHz in FAST_SIM)
//   em_sin   - serial grayscale data, MSB first. Shift order is channel 71
//              (chip2 OUT23) MSB ... channel 0 (chip0 OUT0) LSB, so that after
//              864 SCLK rising edges each channel's 12-bit word lands in its
//              own grayscale latch position (XLAT copies shift reg -> latch).
//   em_xlat  - latch strobe (high >= 4 clk after the last SCLK falling edge)
//   em_blank - PWM blank: 1 = all outputs OFF, 0 = run the 4096-step internal
//              grayscale PWM. The host holds blank_run=1 for the pump window.
//
// Host side (driven by emitter_array.v):
//   gs_we + gs_idx[5:0] + gs_data[11:0] - write one channel's 12-bit level
//   load_start (1-cycle pulse) - parallel-load the 864-bit shift register from
//                                the gs array, shift it out, then pulse XLAT
//   load_busy              - high from load_start until XLAT completes
//   blank_run              - 1 = em_blank low (PWM on), 0 = em_blank high (off)
//
// The 64 emitters run IN PARALLEL: each channel has its own 12-bit grayscale
// word, so multiple emitters fire simultaneously at independent intensities
// the instant blank_run goes high. There is no one-hot sweep -- this is the
// "parallel at FPGA speed" the architecture pivot requires.
//============================================================================
module tlc5947_ctrl #(
    parameter N_CH = 64           // used channels (0..N_CH-1); chain is 72
) (
    input             clk,
    input             reset,
    // grayscale write port
    input             gs_we,
    input      [5:0]  gs_idx,
    input      [11:0] gs_data,
    // load control
    input             load_start,
    output            load_busy,
    // blank / PWM run
    input             blank_run,
    // 4-wire TLC5947 interface
    output reg        em_sclk,
    output            em_sin,
    output reg        em_xlat,
    output reg        em_blank
);
    localparam CHAIN      = 72;             // 3 x TLC5947 (24 ch each)
    localparam TOTAL_BITS = CHAIN * 12;     // 864 grayscale bits
`ifdef FAST_SIM
    localparam SCLK_DIV   = 1;              // 50 MHz in sim (no real pad)
`else
    localparam SCLK_DIV   = 3;              // 16.7 MHz (well under 30 MHz max)
`endif
    localparam XLAT_HOLD  = 4;              // XLAT high for 4 clk (~40 ns)

    // 72 x 12-bit grayscale register (channels 64..71 stay 0)
    reg [11:0] gs [0:71];
    integer i;
    initial begin
        for (i = 0; i < 72; i = i + 1) gs[i] = 12'd0;
        em_sclk = 1'b0; em_xlat = 1'b0; em_blank = 1'b1;
    end

    // grayscale write (host must not write while load_busy -- it gates)
    always @(posedge clk) begin
        if (reset) begin
            for (i = 0; i < 72; i = i + 1) gs[i] <= 12'd0;
        end else if (gs_we && gs_idx < N_CH) begin
            gs[gs_idx] <= gs_data;
        end
    end

    // 864-bit flat shift register: bit 863 = first bit out (ch71 MSB),
    // bit 0 = last bit out (ch0 LSB). em_sin shows sreg[863] (MSB) while
    // em_sclk is low; the rising edge latches it; the falling edge shifts.
    reg [863:0] sreg;
    reg [10:0]  bit_cnt;        // 0..863 (rising edges issued)
    reg [2:0]   div;            // SCLK divider count
    reg [2:0]   xlat_cnt;       // XLAT hold counter
    reg [1:0]   state;
    localparam [1:0] S_IDLE = 2'd0, S_SHIFT = 2'd1, S_LAT = 2'd2;
    reg         load_busy_r;
    assign load_busy = load_busy_r;
    assign em_sin    = (state == S_SHIFT) ? sreg[863] : 1'b0;

    integer c, b;
    always @(posedge clk) begin
        if (reset) begin
            state       <= S_IDLE;
            bit_cnt     <= 11'd0;
            div         <= 3'd0;
            xlat_cnt    <= 3'd0;
            em_sclk     <= 1'b0;
            em_xlat     <= 1'b0;
            load_busy_r <= 1'b0;
            sreg        <= 864'd0;
        end else begin
            case (state)
                S_IDLE: begin
                    em_sclk <= 1'b0;
                    em_xlat <= 1'b0;
                    if (load_start) begin
                        // parallel-load the shift register from the gs array.
                        // sreg[(71-c)*12 + (11-b)] = gs[c][b]  =>  sreg[863]
                        // = gs[71][11] (first out), sreg[0] = gs[0][0] (last).
                        for (c = 0; c < 72; c = c + 1)
                            for (b = 0; b < 12; b = b + 1)
                                sreg[(71-c)*12 + (11-b)] <= gs[c][b];
                        bit_cnt     <= 11'd0;
                        div         <= 3'd0;
                        load_busy_r <= 1'b1;
                        state       <= S_SHIFT;
                    end
                end

                S_SHIFT: begin
                    if (div < SCLK_DIV - 1) begin
                        div <= div + 3'd1;
                    end else begin
                        div <= 3'd0;
                        if (em_sclk == 1'b0) begin
                            // em_sin (=sreg[863]) has been stable through the
                            // low half-period; rise to latch it into the TLC.
                            em_sclk <= 1'b1;
                        end else begin
                            // falling edge: consume the bit, advance.
                            em_sclk <= 1'b0;
                            if (bit_cnt + 11'd1 >= TOTAL_BITS) begin
                                bit_cnt  <= 11'd0;
                                em_xlat  <= 1'b1;        // latch next cycle
                                xlat_cnt <= 3'd0;
                                state    <= S_LAT;
                            end else begin
                                sreg    <= sreg << 1;    // next bit -> MSB
                                bit_cnt <= bit_cnt + 11'd1;
                            end
                        end
                    end
                end

                S_LAT: begin
                    if (xlat_cnt < XLAT_HOLD - 1) begin
                        xlat_cnt <= xlat_cnt + 3'd1;
                    end else begin
                        em_xlat     <= 1'b0;
                        load_busy_r <= 1'b0;
                        state       <= S_IDLE;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end

    // blank / PWM run (reset state = blanked = all outputs off)
    always @(posedge clk) begin
        if (reset) em_blank <= 1'b1;
        else       em_blank <= ~blank_run;
    end
endmodule