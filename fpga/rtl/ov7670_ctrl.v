`timescale 1ns / 1ps
//============================================================================
// ov7670_ctrl.v
// ----------------------------------------------------------------------------
// OV7670 configuration controller. Reads the config ROM sequentially and
// pushes each {subaddr,value} pair through the SCCB master. Also generates
// XCLK (~24 MHz from a 96 MHz input clock via /4, or configurable) for the
// camera. Asserts config_done when the terminator entry is reached.
// NOTE: this module only programs the camera; pixel capture lives in
// camera_capture.v.
//============================================================================
module ov7670_ctrl #(
    parameter XCLK_DIV = 2         // xclk = clk / (2*XCLK_DIV): 100MHz,2 -> 25MHz
) (
    input            clk,          // 100 MHz system clock (96/100 MHz both fine)
    input            reset,
    inout            scl,
    inout            sda,
    output           xclk,
    output           config_done
);
    // ---- SCCB master instantiation -----------------------------------------
    reg  [7:0] sccb_subaddr;
    reg  [7:0] sccb_data;
    reg        sccb_start;
    wire       sccb_ready;
    wire       sccb_error;

    sccb_master #(.CLK_DIV(500)) sccb_inst (        // 100 MHz -> 100 kHz SCL
        .clk       (clk),
        .reset     (reset),
        .scl       (scl),
        .sda       (sda),
        .start     (sccb_start),
        .read_mode (1'b0),
        .addr      (8'h42),         // OV7670 SCCB write address
        .subaddr   (sccb_subaddr),
        .data_in   (sccb_data),
        .data_out  (),
        .ready     (sccb_ready),
        .error     (sccb_error)
    );

    // ---- Config ROM readout ------------------------------------------------
    reg  [4:0]  rom_addr;
    wire [15:0] rom_dout;
    reg         config_done_reg;
    // Config FSM: load one register per SCCB transaction, with a clean
    // ready/start handshake so subaddr/data are never rewritten mid-transfer.
    localparam [1:0] CFG_IDLE  = 2'd0;
    localparam [1:0] CFG_LO    = 2'd1;   // wait for sccb_ready to drop
    localparam [1:0] CFG_HI    = 2'd2;   // wait for sccb_ready to rise again
    reg [1:0]   cfg_state;

    ov7670_config_rom rom_inst (.addr(rom_addr), .dout(rom_dout));

    initial begin
        sccb_subaddr   = 8'd0;
        sccb_data      = 8'd0;
        sccb_start     = 1'b0;
        rom_addr       = 5'd0;
        config_done_reg= 1'b0;
        cfg_state      = CFG_IDLE;
    end

    always @(posedge clk) begin
        if (reset) begin
            rom_addr        <= 5'd0;
            config_done_reg <= 1'b0;
            sccb_start      <= 1'b0;
            sccb_subaddr    <= 8'd0;
            sccb_data       <= 8'd0;
            cfg_state       <= CFG_IDLE;
        end else begin
            sccb_start <= 1'b0;     // default pulse
            case (cfg_state)
                CFG_IDLE : begin
                    if (!config_done_reg && sccb_ready) begin
                        if (rom_dout != 16'hFFFF) begin
                            sccb_subaddr <= rom_dout[15:8];
                            sccb_data    <= rom_dout[7:0];
                            sccb_start   <= 1'b1;
                            cfg_state    <= CFG_LO;
                        end else begin
                            config_done_reg <= 1'b1;
                        end
                    end
                end
                CFG_LO : begin
                    // master has accepted the start when ready drops
                    if (!sccb_ready) cfg_state <= CFG_HI;
                end
                CFG_HI : begin
                    // transaction complete when ready rises again
                    if (sccb_ready) begin
                        rom_addr  <= rom_addr + 5'd1;
                        cfg_state <= CFG_IDLE;
                    end
                end
                default : cfg_state <= CFG_IDLE;
            endcase
        end
    end

    assign config_done = config_done_reg;

    // ---- XCLK divider (toggle register, no $clog2) -------------------------
    reg [15:0] xclk_cnt;
    reg        xclk_reg;
    initial begin
        xclk_cnt = 16'd0;
        xclk_reg = 1'b0;
    end
    always @(posedge clk) begin
        if (reset) begin
            xclk_cnt <= 16'd0;
            xclk_reg <= 1'b0;
        end else if (xclk_cnt == XCLK_DIV-1) begin
            xclk_cnt <= 16'd0;
            xclk_reg <= ~xclk_reg;
        end else begin
            xclk_cnt <= xclk_cnt + 16'd1;
        end
    end
    assign xclk = xclk_reg;
endmodule