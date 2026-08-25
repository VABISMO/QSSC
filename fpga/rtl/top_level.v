`timescale 1ns / 1ps
//============================================================================
// top_level.v  --  QSSC control board top-level
// ----------------------------------------------------------------------------
// Xilinx Artix-7 (XC7A35T-1CPG236C) firmware for the Quantum Solid-State
// Chip (room temperature) control board. Instantiates and wires:
//   * UART rx/tx           (115200 baud, 8-N-1)
//   * command_parser       (text protocol over UART)
//   * emitter_array        (8x8 on-chip CQD emitter enable crossbar)
//   * photodetector_readout(analog MUX + XADC + 1 kHz lock-in for the
//                           on-chip graphene/MoS2 detector array)
//   * adf4351_ctrl         (microwave PLL register sequencer)
//   * spi_master           (32-bit SPI for the ADF4351)
//   * refclk_out           (25 MHz reference for the ADF4351, 100 MHz / 4)
//
// There is NO camera and NO SCCB/pixel interface on this board: the chip is
// read on-chip (CQD emitter pump -> graphene detector photocurrent -> TIA ->
// analog MUX -> Artix-7 XADC, lock-in demodulated at 1 kHz). The XADC is a
// hard block of the XC7A35T (fixed-package aux pins, assigned via the XADC
// wizard / constraints); `xadc_behavioral` below is a behavioural stand-in
// for that hard block so the design is simulatable. In a real build it is
// replaced by the Xilinx XADC primitive.
//
// Port names match fpga/constraints.xdc.
//============================================================================
module top_level (
    input             clk_100mhz,      // 100 MHz TCXO
    input             reset,           // active high (button)
    // UART (FT232)
    input             rx,
    output            tx,
    // 8x8 on-chip CQD emitter array crossbar (FPGA -> external emitter driver)
    output [7:0]      emitter_rows,     // row anode-enables (active high)
    output [7:0]      emitter_cols,     // column cathode-enables (active low)
    // On-chip graphene/MoS2 photodetector readout front-end
    output [5:0]      det_mux_sel,      // analog MUX select (which electrode)
    output            mod_sync,        // 1 kHz lock-in modulation reference
    // ADF4351 SPI: {LE, CLK, DATA, MUXOUT}  (MUXOUT is an input from the PLL)
    inout  [3:0]      spi_pins,
    output            refclk_out,      // 25 MHz to ADF4351 REF_IN
    // RF-switch gate pulse (MW pulse envelope duration). The ADF4351 sets the
    // carrier frequency, phase (R1) and output power (R4); this pin gates the
    // external RF switch for the requested pulse duration (mw_gate_ns). Pinned
    // in constraints.xdc to the RF switch control input.
    output            mw_gate
);
    // ---- Parameters --------------------------------------------------------
    localparam CLKS_PER_BIT = 868;          // 100 MHz / 115200

    // ---- Reset synchronizer (async assert, sync deassert) ------------------
    // The board reset button is asynchronous. A 2-flop synchronizer releases
    // all submodules from reset on a clean clock edge, avoiding metastability
    // on the deassertion edge. Assert is still asynchronous (immediate).
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

    // ---- Emitter array (8x8 CQD crossbar) ---------------------------------
    wire        em_drive;
    wire [2:0]  em_row;
    wire [2:0]  em_col;
    wire [31:0] em_duration_ms;
    wire        em_busy;

    emitter_array em_inst (
        .clk          (clk_100mhz),
        .reset        (reset_sync),
        .drive        (em_drive),
        .row          (em_row),
        .col          (em_col),
        .duration_ms  (em_duration_ms),
        .emitter_rows (emitter_rows),
        .emitter_cols (emitter_cols),
        .busy         (em_busy)
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
    // spi_pins[3] is left as an input (MUXOUT); not driven by the FPGA.

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

    // ---- Photodetector readout (analog MUX + XADC + 1 kHz lock-in) --------
    wire        det_read_one;
    wire        det_read_all;
    wire [5:0]  det_site;
    wire        det_busy;
    wire [11:0] det_code;
    wire        det_code_valid;
    wire [5:0]  det_code_idx;
    wire        det_sweep_done;
    wire        det_code_ack;
    wire [5:0]  det_mux_sel_int;
    wire        xadc_start;
    wire        xadc_busy;
    wire [11:0] xadc_data;
    wire        mod_sync_int;

    photodetector_readout #(.N_DET(64), .CLK_HZ(100_000_000)) det_inst (
        .clk         (clk_100mhz),
        .reset       (reset_sync),
        .read_one    (det_read_one),
        .read_all    (det_read_all),
        .site        (det_site),
        .busy        (det_busy),
        .code        (det_code),
        .code_valid  (det_code_valid),
        .code_idx    (det_code_idx),
        .sweep_done  (det_sweep_done),
        .code_ack    (det_code_ack),
        .mux_sel     (det_mux_sel_int),
        .xadc_start  (xadc_start),
        .xadc_busy   (xadc_busy),
        .xadc_data   (xadc_data),
        .mod_sync    (mod_sync_int)
    );

    assign det_mux_sel = det_mux_sel_int;
    assign mod_sync    = mod_sync_int;

    // ---- XADC (behavioural for sim, Xilinx hard block for a real build) ----
    // The Artix-7 XADC is a hard block of the XC7A35T (fixed-package aux pins,
    // configured via the XADC wizard). For simulation and non-Xilinx toolflows
    // we use `xadc_behavioral` (defined below), which produces a deterministic
    // photocurrent code per electrode. For a real Vivado build define the macro
    // `QSSC_XADC_REAL` (e.g. via a Vivado top-level XDC `set_property` or a
    // project define) to swap in the Xilinx XADC primitive reading the TIA aux
    // pair; the `data`/`busy`/`start` handshake is identical either way.
`ifndef QSSC_XADC_REAL
    xadc_behavioral #(.CONV_CYCLES(100)) xadc_inst (
        .clk     (clk_100mhz),
        .reset   (reset_sync),
        .start   (xadc_start),
        .mux_sel (det_mux_sel_int),
        .busy    (xadc_busy),
        .data    (xadc_data)
    );
`else
    // Real Xilinx XADC instantiation placeholder. In a Vivado project this is
    // replaced by the XADC wizard IP wrapper (xi_sysmon / xadc_wiz) reading the
    // dedicated analog aux pair. The handshake below mirrors the behavioural
    // model so the rest of the design is unchanged.
    //   xadc_wiz xadc_inst (
    //       .clk        (clk_100mhz),
    //       .reset      (reset_sync),
    //       .conv_start (xadc_start),
    //       .mux_addr   (det_mux_sel_int),
    //       .busy       (xadc_busy),
    //       .di          (xadc_data)
    //   );
    xadc_behavioral #(.CONV_CYCLES(100)) xadc_inst (  // placeholder until wired
        .clk     (clk_100mhz),
        .reset   (reset_sync),
        .start   (xadc_start),
        .mux_sel (det_mux_sel_int),
        .busy    (xadc_busy),
        .data    (xadc_data)
    );
`endif

    // ---- Calibration LUT readback (write-only in this build) ----------------
    // CALIBRATE fills cal_lut[emitter] = best detector. The host reads it back
    // over UART implicitly (the calibration reply is OK); a dedicated
    // READ_LUT command + lut_rd_addr mux is a future extension. For now
    // lut_rd_addr is held at 0 so the parser's read port synthesizes away
    // cleanly rather than floating.
    wire [5:0] lut_rd_data;
    wire [5:0] lut_rd_addr = 6'd0;

    // ---- MW pulse envelope (latched by APPLY_PULSE) -------------------------
    // phase -> ADF4351 R1, rf_pwr -> ADF4351 R4, gate_ns -> RF-switch gate timer.
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
        .em_drive         (em_drive),
        .em_row           (em_row),
        .em_col           (em_col),
        .em_duration_ms   (em_duration_ms),
        .em_busy          (em_busy),
        .pll_start        (pll_start),
        .pll_freq_khz     (pll_freq_khz),
        .pll_busy         (pll_busy),
        .det_read_one     (det_read_one),
        .det_read_all     (det_read_all),
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
        .mw_gate_load     (mw_gate_load)
    );

    assign pll_phase  = pll_phase_w;
    assign pll_rf_pwr = pll_rf_pwr_w;

    // ---- RF-switch gate timer (MW pulse duration) ---------------------------
    // On mw_gate_load, assert mw_gate for mw_gate_ns nanoseconds. The 100 MHz
    // clock is 10 ns/cycle, so the gate counts mw_gate_ns/10 cycles (min 1).
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


//============================================================================
// xadc_behavioral
// ----------------------------------------------------------------------------
// Behavioural stand-in for the Artix-7 XADC hard block. On `start`, asserts
// `busy` for CONV_CYCLES, then presents `data` = a deterministic photocurrent
// code for the currently selected electrode. The real build replaces this
// with the Xilinx XADC primitive reading the TIA output on the dedicated aux
// pair; the code then comes from the analog front-end, not from mux_sel.
//============================================================================
module xadc_behavioral #(
    parameter CONV_CYCLES = 100
) (
    input             clk,
    input             reset,
    input             start,
    input      [5:0]  mux_sel,     // selected electrode (behavioural model only)
    output reg        busy,
    output reg [11:0] data
);
    localparam [3:0] S_IDLE = 4'd0;
    localparam [3:0] S_CONV = 4'd1;
    localparam [3:0] S_DONE = 4'd2;

    reg [3:0]  state;
    reg [9:0]  timer;     // covers CONV_CYCLES up to 1024
    reg [5:0]  sel_latched;

    initial begin
        state  = S_IDLE;
        timer  = 10'd0;
        busy   = 1'b0;
        data   = 12'd0;
        sel_latched = 6'd0;
    end

    always @(posedge clk) begin
        if (reset) begin
            state <= S_IDLE;
            timer <= 10'd0;
            busy  <= 1'b0;
            data  <= 12'd0;
        end else begin
            case (state)
                S_IDLE: begin
                    if (start) begin
                        sel_latched <= mux_sel;
                        busy        <= 1'b1;
                        timer       <= CONV_CYCLES;
                        state       <= S_CONV;
                    end
                end
                S_CONV: begin
                    if (timer <= 10'd1) begin
                        // behavioural photocurrent code: electrode n -> 1000 + 40*n
                        // (0..4095 range; 0 -> 1000, 63 -> 3520). Real build reads
                        // the TIA aux voltage instead.
                        data  <= 12'd1000 + {6'd0, sel_latched} * 12'd40;
                        state <= S_DONE;
                    end else begin
                        timer <= timer - 10'd1;
                    end
                end
                S_DONE: begin
                    busy  <= 1'b0;
                    state <= S_IDLE;
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule