`timescale 1ns / 1ps
// =============================================================================
// top_qmtech.v  --  QSSC firmware bring-up wrapper for the QMTECH board
// -----------------------------------------------------------------------------
// The QMTECH board (XC7A15T-1CSG325C) has a 50 MHz oscillator on R2, but
// top_level.v expects 100 MHz on clk_100mhz (all of its timing -- UART
// CLKS_PER_BIT=868, emitter counters, gradient timing -- is computed for
// 100 MHz).  This wrapper multiplies 50 MHz up to 100 MHz with an
// MMCME2_BASE and adapts the rest of the board's differences:
//
//   * clk_50mhz (R2, MRCC)  -> MMCM -> clk_100m
//   * key0_n (SW1, H18)     -> active-high reset for top_level.
//       The board has an external 4.7k pull-up: idle = 1, pressed = 0.
//       Reset is also held until the MMCM reports LOCKED so the logic never
//       runs on an unstable clock.
//   * key1_n (SW2, H17)     -> mirrored onto led1_n to exercise the input.
//   * led0_n / led1_n (D1/D4, ACTIVE-LOW) -> heartbeat + button.
//
// IMPORTANT: top_level.v instantiates an ODDR2 (a Spartan-6 primitive) when
// forwarding the 25 MHz reference to the ADF4351.  ODDR2 does NOT exist in
// the xc7 family, so any 7-series build fails.  See ODDR2 handling in
// build_bitstream_qmtech.tcl and README.md in this folder.
//
// HARDWARE STATE: no peripheral is cabled off-board yet.  The peripheral
// ports are passed straight through so later wiring does not require
// rewriting this file.
// =============================================================================
module top_qmtech (
    input             clk_50mhz,     // 50 MHz, R2 (board CLK_50M)
    input             key0_n,        // SW1 / USER_KEY0, active-low
    input             key1_n,        // SW2 / USER_KEY1, active-low
    output            led0_n,        // D1 / USER_LED0, active-low
    output            led1_n,        // D4 / USER_LED1, active-low

    // --- peripherals: passed through to top_level (not cabled yet) ----------
    input             rx,
    output            tx,
    output            adc_convst,
    output            adc_sclk,
    input             adc_miso,
    input             adc_busy,
    output [3:0]      adc_cs,
    output            adc_resetn,
    output            em_sclk,
    output            em_sin,
    output            em_xlat,
    output            em_blank,
    output            mod_sync,
    inout  [3:0]      spi_pins,
    output            refclk_out,
    output            mw_gate,
    output            grad_cs,
    output            grad_sclk,
    output            grad_mosi,
    output            grad_en
);

    // ---- MMCM: 50 MHz -> 100 MHz --------------------------------------------
    // VCO = 50 MHz * 20 / 1 = 1000 MHz  (-1 speed grade range: 600..1200, OK)
    // PFD = 50 MHz / 1     = 50 MHz     (range 10..450 MHz, OK)
    // CLKOUT0 = 1000 / 10  = 100 MHz
    wire clk_50m_buf, clkfb_raw, clkfb, clk_100m, clk_100m_buf, mmcm_locked;

    BUFG i_bufg_clkin (.I (clk_50mhz),   .O (clk_50m_buf));
    BUFG i_bufg_clkout(.I (clk_100m),    .O (clk_100m_buf));
    BUFG i_bufg_clkfb (.I (clkfb_raw),   .O (clkfb));

    MMCME2_BASE #(
        .BANDWIDTH          ("OPTIMIZED"),
        .CLKFBOUT_MULT_F    (20.000),     // 50 * 20 = 1000 MHz VCO
        .CLKFBOUT_PHASE     (0.000),
        .CLKIN1_PERIOD      (20.000),     // 50 MHz -> 20 ns
        .CLKOUT0_DIVIDE_F   (10.000),     // 1000 / 10 = 100 MHz
        .CLKOUT0_DUTY_CYCLE (0.500),
        .CLKOUT0_PHASE      (0.000),
        .DIVCLK_DIVIDE      (1),
        .REF_JITTER1        (0.010),
        .STARTUP_WAIT       ("FALSE")
    ) i_mmcm (
        .CLKIN1    (clk_50m_buf),
        .CLKFBIN   (clkfb),
        .CLKFBOUT  (clkfb_raw),
        .CLKOUT0   (clk_100m),
        .LOCKED    (mmcm_locked),
        .CLKFBOUTB (),
        .CLKOUT0B  (),
        .CLKOUT1   (), .CLKOUT1B (),
        .CLKOUT2   (), .CLKOUT2B (),
        .CLKOUT3   (), .CLKOUT3B (),
        .CLKOUT4   (), .CLKOUT5   (), .CLKOUT6 (),
        .PWRDWN    (1'b0),
        .RST       (1'b0)
    );

    // ---- Reset for top_level (active-high) ---------------------------------
    // Idle: key0_n = 1 -> reset = 0.  Pressed: key0_n = 0 -> reset = 1.
    // Reset stays asserted until the MMCM is LOCKED so the logic never runs
    // while the clock is still coming up.
    wire reset = (~key0_n) | (~mmcm_locked);

    // ---- LED0: 1 Hz heartbeat (proves the 100 MHz clock is running) --------
    // Toggles every 0.5 s -> a visible 1 Hz blink.  Deliberately independent
    // of `reset` so it keeps beating while top_level is held: this
    // distinguishes "the clock works" from "the design works".
    localparam HB_HALF = 50_000_000;        // 0.5 s at 100 MHz
    reg [25:0] hb_cnt = 26'd0;
    reg        hb_led  = 1'b0;
    always @(posedge clk_100m_buf) begin
        if (hb_cnt >= HB_HALF - 1) begin
            hb_cnt <= 26'd0;
            hb_led <= ~hb_led;
        end else begin
            hb_cnt <= hb_cnt + 26'd1;
        end
    end
    assign led0_n = ~hb_led;                // active-low: 1 = off

    // ---- LED1: SW2 mirror (exercises the input path) -----------------------
    // Idle   key1_n = 1 -> led1_n = 1 -> D4 off.
    // Pressed key1_n = 0 -> led1_n = 0 -> D4 on.
    assign led1_n = key1_n;

    // ---- QSSC core ---------------------------------------------------------
    top_level u_top (
        .clk_100mhz (clk_100m_buf),
        .reset      (reset),
        .rx         (rx),
        .tx         (tx),
        .adc_convst (adc_convst),
        .adc_sclk   (adc_sclk),
        .adc_miso   (adc_miso),
        .adc_busy   (adc_busy),
        .adc_cs     (adc_cs),
        .adc_resetn (adc_resetn),
        .em_sclk    (em_sclk),
        .em_sin     (em_sin),
        .em_xlat    (em_xlat),
        .em_blank   (em_blank),
        .mod_sync   (mod_sync),
        .spi_pins   (spi_pins),
        .refclk_out (refclk_out),
        .mw_gate    (mw_gate),
        .grad_cs    (grad_cs),
        .grad_sclk  (grad_sclk),
        .grad_mosi  (grad_mosi),
        .grad_en    (grad_en)
    );

endmodule
