`timescale 1ns / 1ps
//============================================================================
// top_level.v  --  QSSC control board top-level
// ----------------------------------------------------------------------------
// Xilinx Artix-7 (XC7A35T-1CPG236C) firmware for the Quantum Solid-State
// Chip (room temperature) control board. Instantiates and wires:
//   * UART rx/tx           (115200 baud, 8-N-1)
//   * command_parser       (text protocol over UART)
//   * laser_array          (8x8 VCSEL driver)
//   * ov7670_ctrl          (SCCB config + XCLK for the OV7670)
//   * camera_capture       (double-buffered BRAM frame grabber)
//   * adf4351_ctrl         (microwave PLL register sequencer)
//   * spi_master           (32-bit SPI for the ADF4351)
//   * refclk_out           (25 MHz reference for the ADF4351, 100 MHz / 4)
//
// Port names match fpga/constraints.xdc.
//============================================================================
module top_level (
    input             clk_100mhz,      // 100 MHz TCXO
    input             reset,           // active high (button)
    // UART (FT232)
    input             rx,
    output            tx,
    // 8x8 VCSEL array
    output [7:0]      laser_rows,
    output [7:0]      laser_cols,
    // OV7670 SCCB + pixel interface
    inout             scl,
    inout             sda,
    output            xclk,
    input             pclk,
    input             vsync,
    input             href,
    input      [7:0]  d,
    // ADF4351 SPI: {LE, CLK, DATA, MUXOUT}  (MUXOUT is an input from the PLL)
    inout  [3:0]      spi_pins,
    output            refclk_out       // 25 MHz to ADF4351 REF_IN
);
    // ---- Parameters --------------------------------------------------------
    localparam CLKS_PER_BIT = 868;          // 100 MHz / 115200
    localparam [15:0] CAM_FRAME_BYTES = 16'd3072;  // 64x48 window

    // ---- UART --------------------------------------------------------------
    wire [7:0] rx_byte;
    wire       rx_valid;
    wire [7:0] tx_byte;
    wire       tx_start;
    wire       tx_busy;

    uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) uart_rx_inst (
        .i_Clock    (clk_100mhz),
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

    // ---- Laser array -------------------------------------------------------
    wire        laser_fire;
    wire [2:0]  laser_row;
    wire [2:0]  laser_col;
    wire [31:0] laser_duration_ms;
    wire        laser_busy;

    laser_array laser_inst (
        .clk          (clk_100mhz),
        .reset        (reset),
        .fire         (laser_fire),
        .row          (laser_row),
        .col          (laser_col),
        .duration_ms  (laser_duration_ms),
        .laser_rows   (laser_rows),
        .laser_cols   (laser_cols),
        .busy         (laser_busy)
    );

    // ---- ADF4351 PLL + SPI -------------------------------------------------
    wire        pll_start;
    wire [31:0] pll_freq_khz;
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
        .reset   (reset),
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
    // spi_pins[3] is left as an input (MUXOUT); not driven by the FPGA.

    adf4351_ctrl pll_inst (
        .clk             (clk_100mhz),
        .reset           (reset),
        .start           (pll_start),
        .target_freq_khz (pll_freq_khz),
        .busy            (pll_busy),
        .done            (pll_done),
        .spi_start       (spi_start_w),
        .spi_data        (spi_data_w),
        .spi_busy        (spi_busy_w),
        .spi_done        (spi_done_w)
    );

    // ---- OV7670 config + XCLK ---------------------------------------------
    wire config_done;
    ov7670_ctrl #(.XCLK_DIV(2)) ov_cfg_inst (
        .clk         (clk_100mhz),
        .reset       (reset),
        .scl         (scl),
        .sda         (sda),
        .xclk        (xclk),
        .config_done (config_done)
    );

    // ---- Camera capture (double-buffered BRAM) -----------------------------
    wire        cam_capture_start;
    wire        cam_frame_ready;
    wire [15:0] cam_rd_addr;
    wire [7:0]  cam_rd_data;
    wire        cam_capturing;

    camera_capture #(
        .X_START(288), .X_SIZE(64), .Y_START(216), .Y_SIZE(48),
        .FRAME_SIZE(3072)
    ) cam_inst (
        .pclk            (pclk),
        .reset           (reset),
        .vsync           (vsync),
        .href            (href),
        .d               (d),
        .capture_start   (cam_capture_start),
        .frame_ready     (cam_frame_ready),
        .rd_clk          (clk_100mhz),
        .rd_addr         (cam_rd_addr),
        .rd_data         (cam_rd_data),
        .capturing_out   (cam_capturing)
    );

    // ---- Command parser (ties everything together) -------------------------
    command_parser #(.FRAME_BYTES(3072)) parser_inst (
        .clk               (clk_100mhz),
        .reset             (reset),
        .rx_byte           (rx_byte),
        .rx_valid          (rx_valid),
        .tx_byte           (tx_byte),
        .tx_start          (tx_start),
        .tx_busy           (tx_busy),
        .laser_fire        (laser_fire),
        .laser_row         (laser_row),
        .laser_col         (laser_col),
        .laser_duration_ms (laser_duration_ms),
        .laser_busy        (laser_busy),
        .pll_start         (pll_start),
        .pll_freq_khz      (pll_freq_khz),
        .pll_busy          (pll_busy),
        .cam_capture_start (cam_capture_start),
        .cam_frame_ready   (cam_frame_ready),
        .cam_rd_addr       (cam_rd_addr),
        .cam_rd_data       (cam_rd_data),
        .cam_capturing     (cam_capturing)
    );

    // ---- 25 MHz reference clock for ADF4351 (100 MHz / 4) ------------------
    reg [1:0] refclk_div;
    initial refclk_div = 2'd0;
    always @(posedge clk_100mhz) begin
        if (reset) refclk_div <= 2'd0;
        else       refclk_div <= refclk_div + 2'd1;
    end
    assign refclk_out = refclk_div[1];
endmodule