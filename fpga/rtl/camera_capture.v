`timescale 1ns / 1ps
//============================================================================
// camera_capture.v
// ----------------------------------------------------------------------------
// Double-buffered BRAM frame capture for the OV7670. A configurable window
// (X_SIZE x Y_SIZE pixels, default 64x48 = 3072 bytes) is grabbed from each
// frame into one of two BRAMs while the host reads the previously completed
// buffer over UART. One byte per pixel (grayscale = average of the two
// RGB565/RGB444 bytes produced by the camera).
//
// Clock domains:
//   * write side runs on the camera PCLK (pclk).
//   * read side runs on the 100 MHz system clock (rd_clk).
//   * capture_start (rd_clk domain) is synchronized into pclk.
//   * frame_ready (pclk domain) is synchronized into rd_clk.
// The reader reads the buffer that is NOT currently being written
// (selected by the synchronized copy of buf_sel).
//============================================================================
module camera_capture #(
    parameter X_START    = 288,
    parameter X_SIZE     = 64,
    parameter Y_START    = 216,
    parameter Y_SIZE     = 48,
    parameter FRAME_SIZE = X_SIZE * Y_SIZE   // 3072
) (
    input             pclk,
    input             reset,
    input             vsync,
    input             href,
    input      [7:0]  d,
    input             capture_start,    // rd_clk domain: arm for next frame
    output            frame_ready,      // rd_clk domain: a frame is available
    // read port (rd_clk domain)
    input             rd_clk,
    input      [15:0] rd_addr,
    output reg [7:0]  rd_data,
    // diagnostic
    output            capturing_out
);
    // ---- Dual BRAM ----------------------------------------------------------
    reg [7:0] mem_a [0:FRAME_SIZE-1];
    reg [7:0] mem_b [0:FRAME_SIZE-1];

    // Write-side registers (pclk domain)
    reg        buf_sel;          // 0 = writing A, 1 = writing B
    reg        armed;
    reg        capturing;
    reg [11:0] waddr;
    reg [9:0]  row_idx;
    reg [9:0]  pix_in_row;
    reg        byte_one;
    reg [7:0]  byte_high;
    reg        vsync_d;

    // Synchronizers (rd_clk -> pclk for capture_start)
    reg cap_s1, cap_s2, cap_s3;
    // Synchronizer (pclk -> rd_clk for buf_sel). buf_sel toggles on every
    // completed frame, so a single synchronized toggle carries BOTH the
    // "frame ready" event and the buffer-select — no two-signal skew.
    reg bs_s1, bs_s2, bs_s3;

    initial begin
        buf_sel     = 1'b0;
        armed       = 1'b0;
        capturing   = 1'b0;
        waddr       = 12'd0;
        row_idx     = 10'd0;
        pix_in_row  = 10'd0;
        byte_one    = 1'b1;
        byte_high   = 8'd0;
        vsync_d     = 1'b0;
        cap_s1=1'b0; cap_s2=1'b0; cap_s3=1'b0;
        bs_s1=1'b0; bs_s2=1'b0; bs_s3=1'b0;
        rd_data     = 8'd0;
    end

    wire vsync_rise = vsync & ~vsync_d;
    // 9-bit add then shift avoids 8-bit overflow for bright pixels.
    wire [8:0] gray_full = {1'b0, byte_high} + {1'b0, d};
    wire [7:0] gray      = gray_full[8:1];
    wire in_window  = (row_idx >= Y_START) &&
                      (row_idx <  Y_START + Y_SIZE) &&
                      (pix_in_row >= X_START) &&
                      (pix_in_row <  X_START + X_SIZE);

    assign capturing_out = capturing;
    // frame_ready: a 1-cycle pulse in rd_clk on each completed frame, derived
    // from the SAME synchronized buf_sel toggle used for buffer selection, so
    // ready and select can never disagree across clock domains.
    assign frame_ready   = (bs_s2 ^ bs_s3);

    // ---- Write side (pclk) --------------------------------------------------
    always @(posedge pclk) begin
        if (reset) begin
            buf_sel     <= 1'b0;
            armed       <= 1'b0;
            capturing   <= 1'b0;
            waddr       <= 12'd0;
            row_idx     <= 10'd0;
            pix_in_row  <= 10'd0;
            byte_one    <= 1'b1;
            byte_high   <= 8'd0;
            vsync_d     <= 1'b0;
            cap_s1<=1'b0; cap_s2<=1'b0; cap_s3<=1'b0;
        end else begin
            // Synchronize capture_start into pclk domain and edge-detect.
            cap_s1 <= capture_start;
            cap_s2 <= cap_s1;
            cap_s3 <= cap_s2;
            if (cap_s2 & ~cap_s3) begin
                armed <= 1'b1;
            end

            vsync_d <= vsync;

            // Start a capture on the next VSYNC rising edge after arming.
            if (vsync_rise && armed && !capturing) begin
                capturing  <= 1'b1;
                armed      <= 1'b0;
                row_idx    <= 10'd0;
                pix_in_row <= 10'd0;
                byte_one   <= 1'b1;
                waddr      <= 12'd0;
            end else if (capturing) begin
                if (href) begin
                    if (byte_one) begin
                        byte_high <= d;
                        byte_one  <= 1'b0;
                    end else begin
                        // second byte -> pixel complete
                        if (in_window) begin
                            if (buf_sel == 1'b0) mem_a[waddr] <= gray;
                            else                 mem_b[waddr] <= gray;
                            waddr <= waddr + 12'd1;
                        end
                        byte_one <= 1'b1;
                        if (pix_in_row == 10'd639) begin
                            pix_in_row <= 10'd0;
                            if (row_idx == (Y_START + Y_SIZE - 1)) begin
                                // frame complete: toggle buf_sel (single
                                // CDC toggle -> rd_clk side edge-detects it)
                                capturing  <= 1'b0;
                                buf_sel    <= ~buf_sel;
                            end else begin
                                row_idx <= row_idx + 10'd1;
                            end
                        end else begin
                            pix_in_row <= pix_in_row + 10'd1;
                        end
                    end
                end
            end
        end
    end

    // ---- Synchronize buf_sel into rd_clk domain (2-FF sync + edge detect) ---
    always @(posedge rd_clk) begin
        if (reset) begin
            bs_s1 <= 1'b0; bs_s2 <= 1'b0; bs_s3 <= 1'b0;
        end else begin
            bs_s1 <= buf_sel;    // 1st FF (metastability resolution)
            bs_s2 <= bs_s1;      // 2nd FF (stable)
            bs_s3 <= bs_s2;      // delay for edge detect
        end
    end

    // ---- Read side (rd_clk): read the completed buffer (~synchronized sel) --
    always @(posedge rd_clk) begin
        if (reset) begin
            rd_data <= 8'd0;
        end else begin
            if (bs_s2 == 1'b0)
                rd_data <= mem_b[rd_addr[11:0]];   // was writing A -> read B
            else
                rd_data <= mem_a[rd_addr[11:0]];   // was writing B -> read A
        end
    end
endmodule