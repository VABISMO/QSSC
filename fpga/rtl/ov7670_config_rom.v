`timescale 1ns / 1ps
//============================================================================
// ov7670_config_rom.v
// ----------------------------------------------------------------------------
// Small register-init sequence ROM for the OV7670. Each entry is a 16-bit
// word {register_address[15:8], register_value[7:0]}. The terminator entry
// 16'hFFFF marks the end of the table. Address width supports up to 32
// entries (5-bit address); the table below uses 25.
//============================================================================
module ov7670_config_rom (
    input      [4:0] addr,
    output reg [15:0] dout
);
    always @(*) begin
        case (addr)
            5'd0 :  dout = 16'h1280; // COM7  - reset
            5'd1 :  dout = 16'h1280; // COM7  - reset again
            5'd2 :  dout = 16'h1101; // CLKRC - prescaler /1
            5'd3 :  dout = 16'h1204; // COM7  - RGB output enable
            5'd4 :  dout = 16'h3A04; // TSLB  - line buffer
            5'd5 :  dout = 16'h4000; // COM15 - normal range
            5'd6 :  dout = 16'h8C00; // RGB444 enable
            5'd7 :  dout = 16'h3E19; // COM14 - scaling / QVGA
            5'd8 :  dout = 16'h7211; // DSP_CTRL3
            5'd9 :  dout = 16'h733F; // DSP_CTRL4
            5'd10 : dout = 16'h1404; // SCALING_XSC
            5'd11 : dout = 16'h158D; // SCALING_YSC
            5'd12 : dout = 16'h3D00; // SCALING_DCWCTR
            5'd13 : dout = 16'h3C00; // SCALING_PCLK_DIV
            5'd14 : dout = 16'h1700; // HSTART
            5'd15 : dout = 16'h1801; // HSTOP
            5'd16 : dout = 16'h3202; // HREF
            5'd17 : dout = 16'h1903; // VSTART
            5'd18 : dout = 16'h1A7B; // VSTOP
            5'd19 : dout = 16'h030A; // VREF
            5'd20 : dout = 16'h0C00; // COM3
            5'd21 : dout = 16'h3E00; // COM14
            5'd22 : dout = 16'h7000; // DSP_CTRL1
            5'd23 : dout = 16'h7100; // DSP_CTRL2
            5'd24 : dout = 16'hFFFF; // terminator
            default: dout = 16'hFFFF;
        endcase
    end
endmodule