`timescale 1ns / 1ps
//============================================================================
// top_level.v  --  QSSC single-board parallel control firmware
// ----------------------------------------------------------------------------
// Xilinx Artix-7 (XC7A35T-1CSG324C) firmware for the Quantum Solid-State Chip
// control board. Single merged board, fully parallel readout/drive:
//   * UART rx/tx           (115200 baud, 8-N-1)
//   * command_parser       (text protocol over UART)
//   * emitter_array        (64 parallel CQD emitters via 3x TLC5947, 4-wire SPI)
//   * adc_reader           (64-ch parallel readout via 4x AD7616 16-bit ADC,
//                           9-wire SPI; replaces photon_counter_array)
//   * adf4351_ctrl         (microwave PLL register sequencer)
//   * spi_master           (32-bit SPI for the ADF4351)
//   * refclk_out           (25 MHz reference for the ADF4351, 100 MHz / 4)
//
// There is NO analog MUX, NO XADC, NO photon counting, NO MCP4725 threshold
// DAC: each of the 64 detectors has its own TIA on the board, and the 64 TIA
// outputs are digitised IN PARALLEL by 4x AD7616 16-bit dual simultaneous-
// sampling ADCs over a 9-wire SPI bus (adc_convst/sclk/miso/busy/cs[3:0]/
// resetn). The +/-5V range is HW-pin-strapped, so there is no MOSI / config
// write. adc_reader continuously refreshes a 64x16 sample RAM and serves 16-bit
// intensity samples to the host; the 1 kHz lock-in demodulation is host-side
// (mod_sync is the reference). The 64 CQD emitters are driven IN PARALLEL by
// 3x TLC5947 constant-current LED drivers over a 4-wire SPI
// (em_sclk/sin/xlat/blank): each channel has its own 12-bit grayscale word, so
// multiple emitters light simultaneously at independent intensities. No 8x8
// row/col matrix, no 64 FPGA GPIO.
//
// Port names match fpga/constraints.xdc.
//============================================================================
module top_level (
    input             clk_100mhz,      // 100 MHz TCXO
    input             reset,           // active high (button)
    // UART (FT232)
    input             rx,
    output            tx,
    // 4x AD7616 parallel 16-bit ADC readout (64 TIA outputs digitised, 9-wire
    // SPI bus; the +/-5V range is HW-pin-strapped on each AD7616 -> no MOSI).
    output            adc_convst,      // shared conversion start (rising edge)
    output            adc_sclk,        // shared SPI clock (<=40 MHz, idles high)
    input             adc_miso,        // shared SDOA (per-chip CS selects driver)
    input             adc_busy,        // chip0 BUSY (all 4 finish a CONVST together)
    output [3:0]      adc_cs,          // per-chip chip select (active low)
    output            adc_resetn,      // shared reset (active low)
    // 64 parallel CQD emitters via 3x TLC5947 (4-wire SPI, 72-ch chain, 64 used)
    output            em_sclk,
    output            em_sin,
    output            em_xlat,
    output            em_blank,
    // lock-in modulation reference (1 kHz, host-side demodulation)
    output            mod_sync,
    // ADF4351 SPI: {LE, CLK, DATA, MUXOUT}  (MUXOUT is an input from the PLL)
    inout  [3:0]      spi_pins,
    output            refclk_out,      // 25 MHz to ADF4351 REF_IN
    // RF-switch gate pulse (MW pulse envelope duration)
    output            mw_gate
);
    // ---- Parameters --------------------------------------------------------
    // FAST_SIM collapses the slow real-time counters (UART bit period, emitter
    // 1 ms pump) into a handful of clock cycles so the self-checking testbench
    // runs in seconds instead of minutes. Production timing is untouched when
    // the define is absent (the synthesised build never sets it).
`ifdef FAST_SIM
    localparam CLKS_PER_BIT = 8;             // ~12.5 MBaud in fast sim
`else
    localparam CLKS_PER_BIT = 868;           // 100 MHz / 115200
`endif

    // ---- Reset synchronizer (async assert, sync deassert) ------------------
    reg [1:0] rst_sync;
    initial rst_sync = 2'b11;               // come up in reset
    always @(posedge clk_100mhz or posedge reset) begin
        if (reset) rst_sync <= 2'b11;       // async assert
        else       rst_sync <= {rst_sync[0], 1'b0};  // sync deassert (2-stage)
    end
    wire reset_sync = rst_sync[1];

    // ---- UART --------------------------------------------------------------
    wire [7:0] rx_byte;
    wire       rx_valid;
    wire [7:0] tx_byte;
    wire       tx_start;
    wire       tx_busy;

    uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) uart_rx_inst (
        .i_Clock    (clk_100mhz),
        .i_Reset    (reset_sync),
        .i_Rx_Serial(rx),
        .o_Rx_DV    (rx_valid),
        .o_Rx_Byte  (rx_byte)
    );

    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) uart_tx_inst (
        .i_Clock   (clk_100mhz),
        .i_Tx_DV   (tx_start),
        .i_Tx_Byte (tx_byte),
        .o_Tx_Active(tx_busy),
        .o_Tx_Serial(tx),
        .o_Tx_Done ()
    );

    // ---- Emitter array (64 parallel CQD emitters via 3x TLC5947) ----------
    wire        em_cmd_drive;
    wire        em_cmd_set;
    wire        em_cmd_pattern;
    wire        em_cmd_clear;
    wire [5:0]  em_index;
    wire [11:0] em_level;
    wire [31:0] em_duration_ms;
    wire        em_busy;

    emitter_array em_inst (
        .clk          (clk_100mhz),
        .reset        (reset_sync),
        .cmd_drive    (em_cmd_drive),
        .cmd_set      (em_cmd_set),
        .cmd_pattern  (em_cmd_pattern),
        .cmd_clear    (em_cmd_clear),
        .index        (em_index),
        .level        (em_level),
        .duration_ms  (em_duration_ms),
        .busy         (em_busy),
        .em_sclk      (em_sclk),
        .em_sin       (em_sin),
        .em_xlat      (em_xlat),
        .em_blank     (em_blank)
    );

    // ---- ADF4351 PLL + SPI -------------------------------------------------
    wire        pll_start;
    wire [31:0] pll_freq_khz;
    wire [11:0] pll_phase;
    wire [1:0]  pll_rf_pwr;
    wire        pll_busy;
    wire        pll_done;
    wire        spi_start_w;
    wire [31:0] spi_data_w;
    wire        spi_busy_w;
    wire        spi_done_w;

    // spi_pins: [0]=LE, [1]=CLK, [2]=DATA (FPGA->PLL), [3]=MUXOUT (PLL->FPGA)
    wire spi_le, spi_sclk, spi_mosi;
    wire spi_muxout = spi_pins[3];

    spi_master #(.CLK_DIV(4)) spi_inst (
        .clk     (clk_100mhz),
        .reset   (reset_sync),
        .start   (spi_start_w),
        .data_in (spi_data_w),
        .busy    (spi_busy_w),
        .done    (spi_done_w),
        .sclk    (spi_sclk),
        .mosi    (spi_mosi),
        .le      (spi_le)
    );
    assign spi_pins[0] = spi_le;
    assign spi_pins[1] = spi_sclk;
    assign spi_pins[2] = spi_mosi;
    // spi_pins[3] is an input (MUXOUT); not driven by the FPGA.

    adf4351_ctrl pll_inst (
        .clk             (clk_100mhz),
        .reset           (reset_sync),
        .start           (pll_start),
        .target_freq_khz (pll_freq_khz),
        .phase           (pll_phase),
        .rf_pwr          (pll_rf_pwr),
        .busy            (pll_busy),
        .done            (pll_done),
        .spi_start       (spi_start_w),
        .spi_data        (spi_data_w),
        .spi_busy        (spi_busy_w),
        .spi_done        (spi_done_w)
    );

    // ---- ADC reader (64-ch parallel readout via 4x AD7616, 9-wire SPI) -----
    // Replaces photon_counter_array: same host handshake to command_parser
    // (read_one/read_all/clear/site/busy/code/code_valid/code_idx/sweep_done/
    // code_ack), but the 16-bit `code` is an AD7616 intensity sample of the
    // detector TIA output, not a photon count. adc_reader also generates the
    // 1 kHz mod_sync lock-in reference (previously in photon_counter_array).
    wire        det_read_one;
    wire        det_read_all;
    wire        det_clear;
    wire [5:0]  det_site;
    wire        det_busy;
    wire [15:0] det_code;
    wire        det_code_valid;
    wire [5:0]  det_code_idx;
    wire        det_sweep_done;
    wire        det_code_ack;
    wire        mod_sync_int;

    // ---- QRNG entropy tap (dark AD7616 channel -> qrng_entropy) ------------
    wire        entropy_valid;
    wire [15:0] entropy_data;
    wire        rng_harvest_en;
    wire        rng_flush;
    wire [31:0] rng_word;
    wire        rng_valid;
    wire [23:0] rng_samples_seen;

    adc_reader #(.N_DET(64), .CLK_HZ(100_000_000), .ENTROPY_CH(6'd0)) det_inst (
        .clk         (clk_100mhz),
        .reset       (reset_sync),
        .read_one    (det_read_one),
        .read_all    (det_read_all),
        .site        (det_site),
        .clear       (det_clear),
        .busy        (det_busy),
        .code        (det_code),
        .code_valid  (det_code_valid),
        .code_idx    (det_code_idx),
        .sweep_done  (det_sweep_done),
        .code_ack    (det_code_ack),
        .adc_convst  (adc_convst),
        .adc_sclk    (adc_sclk),
        .adc_miso    (adc_miso),
        .adc_busy    (adc_busy),
        .adc_cs      (adc_cs),
        .adc_resetn  (adc_resetn),
        .mod_sync    (mod_sync_int),
        .entropy_valid (entropy_valid),
        .entropy_data  (entropy_data)
    );

    assign mod_sync = mod_sync_int;

    // ---- On-FPGA QRNG: von-Neumann-debias the dark-channel LSB noise -------
    // Free-running: every fresh dark-channel sample (entropy_valid) feeds the
    // extractor; command_parser gates harvesting via rng_harvest_en (READ_RNG),
    // pulses rng_flush at request start (fresh extractor state per request) and
    // reads back debiased 32-bit words on rng_valid. rng_samples_seen loops
    // back into the parser so READ_RNG can report the raw-sample count it
    // actually consumed.
    qrng_entropy #(.W(32)) rng_inst (
        .clk            (clk_100mhz),
        .rst            (reset_sync),
        .adc_data       (entropy_data),
        .adc_valid      (entropy_valid),
        .harvest_en     (rng_harvest_en),
        .flush          (rng_flush),
        .rng_word       (rng_word),
        .rng_valid      (rng_valid),
        .bits_harvested (),
        .samples_seen   (rng_samples_seen)
    );

    // ---- Calibration LUT readback (write-only in this build) ---------------
    wire [5:0] lut_rd_data;
    wire [5:0] lut_rd_addr = 6'd0;

    // ---- MW pulse envelope (latched by APPLY_PULSE) ------------------------
    wire [11:0] pll_phase_w;
    wire [1:0]  pll_rf_pwr_w;
    wire [31:0] mw_gate_ns_w;
    wire        mw_gate_load;

    // ---- Command parser (ties everything together) -------------------------
    command_parser #(.N_DET(64)) parser_inst (
        .clk              (clk_100mhz),
        .reset            (reset_sync),
        .rx_byte          (rx_byte),
        .rx_valid         (rx_valid),
        .tx_byte          (tx_byte),
        .tx_start         (tx_start),
        .tx_busy          (tx_busy),
        .em_cmd_drive     (em_cmd_drive),
        .em_cmd_set       (em_cmd_set),
        .em_cmd_pattern   (em_cmd_pattern),
        .em_cmd_clear     (em_cmd_clear),
        .em_index         (em_index),
        .em_level         (em_level),
        .em_duration_ms   (em_duration_ms),
        .em_busy          (em_busy),
        .pll_start        (pll_start),
        .pll_freq_khz     (pll_freq_khz),
        .pll_busy         (pll_busy),
        .det_read_one     (det_read_one),
        .det_read_all     (det_read_all),
        .det_clear        (det_clear),
        .det_site         (det_site),
        .det_busy         (det_busy),
        .det_code         (det_code),
        .det_code_valid   (det_code_valid),
        .det_code_idx     (det_code_idx),
        .det_sweep_done   (det_sweep_done),
        .det_code_ack     (det_code_ack),
        .lut_rd_addr      (lut_rd_addr),
        .lut_rd_data      (lut_rd_data),
        .pll_phase        (pll_phase_w),
        .pll_rf_pwr       (pll_rf_pwr_w),
        .mw_gate_ns       (mw_gate_ns_w),
        .mw_gate_load     (mw_gate_load),
        .rng_harvest_en   (rng_harvest_en),
        .rng_flush        (rng_flush),
        .rng_word         (rng_word),
        .rng_valid        (rng_valid),
        .rng_samples_seen (rng_samples_seen)
    );

    assign pll_phase  = pll_phase_w;
    assign pll_rf_pwr = pll_rf_pwr_w;

    // ---- RF-switch gate timer (MW pulse duration) ---------------------------
    reg        mw_gate_r;
    reg        mw_gate_active;
    reg [31:0] gate_cnt;
    initial begin mw_gate_r = 1'b0; mw_gate_active = 1'b0; gate_cnt = 32'd0; end
    always @(posedge clk_100mhz) begin
        if (reset_sync) begin
            mw_gate_r      <= 1'b0;
            mw_gate_active <= 1'b0;
            gate_cnt       <= 32'd0;
        end else if (mw_gate_load) begin
            mw_gate_r      <= 1'b1;             // open the RF switch
            mw_gate_active <= 1'b1;
            gate_cnt       <= (mw_gate_ns_w >= 32'd10) ? (mw_gate_ns_w / 32'd10)
                                                      : 32'd1;
        end else if (mw_gate_active) begin
            if (gate_cnt <= 32'd1) begin
                mw_gate_r      <= 1'b0;          // close the RF switch
                mw_gate_active <= 1'b0;
            end else begin
                gate_cnt <= gate_cnt - 32'd1;
            end
        end
    end
    assign mw_gate = mw_gate_r;

    // ---- 25 MHz reference clock for ADF4351 (100 MHz / 4) ------------------
    reg [1:0] refclk_div;
    initial refclk_div = 2'd0;
    always @(posedge clk_100mhz) begin
        if (reset_sync) refclk_div <= 2'd0;
        else            refclk_div <= refclk_div + 2'd1;
    end
    assign refclk_out = refclk_div[1];
endmodule