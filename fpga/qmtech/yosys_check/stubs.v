// =============================================================================
// stubs.v -- YOSYS-ONLY primitive stubs.  NEVER pass this file to Vivado.
// -----------------------------------------------------------------------------
// Purpose: let yosys elaborate and measure fpga/qmtech/top_qmtech.v on a box
// without Vivado.  Three cells are involved that yosys 0.52 does not provide
// by itself for the xc7 family:
//
//   * MMCME2_BASE -- hard clock-management macro.  Marked (* blackbox *) so
//     yosys treats it as a fixed cell and does not try to map it into fabric.
//     It is NOT logic: it occupies a dedicated hard block, so blackboxing it
//     gives an accurate fabric-usage figure.
//   * BUFG       -- global clock buffer; yosys does not predefine it for the
//     hierarchy pass, so it is declared here as a blackbox.  It is a genuine
//     Xilinx cell and is left untouched in the netlist.
//   * ODDR2       -- instantiated inside rtl/top_level.v for the 25 MHz
//     reference forward.  This is a Spartan-6/Virtex-6 primitive that does
//     not exist on Artix-7 at all (that is a real bug in top_level.v, see
//     fpga/qmtech/build_bitstream_qmtech.tcl).  Stubbed here purely so
//     elaboration can proceed.
//
// Vivado resolves both from its own libraries; build_bitstream_qmtech.tcl
// deliberately does not read this file.
// =============================================================================

(* blackbox *)
module MMCME2_BASE (
    output CLKFBOUT,  output CLKFBOUTB,
    output CLKOUT0,   output CLKOUT0B,
    output CLKOUT1,   output CLKOUT1B,
    output CLKOUT2,   output CLKOUT2B,
    output CLKOUT3,   output CLKOUT3B,
    output CLKOUT4,   output CLKOUT5,  output CLKOUT6,
    output LOCKED,
    input  CLKFBIN,   input CLKIN1,
    input  PWRDWN,    input RST
);
    parameter BANDWIDTH          = "OPTIMIZED";    parameter CLKFBOUT_MULT_F    = 5.000;
    parameter CLKFBOUT_PHASE     = 0.000;
    parameter CLKIN1_PERIOD      = 0.000;
    parameter CLKOUT0_DIVIDE_F   = 1.000;
    parameter CLKOUT0_DUTY_CYCLE = 0.500;
    parameter CLKOUT0_PHASE      = 0.000;
    parameter DIVCLK_DIVIDE      = 1;
    parameter REF_JITTER1        = 0.010;
    parameter STARTUP_WAIT       = "FALSE";
endmodule

// Spartan-6/Virtex-6 primitive -- does not exist on Artix-7.  See header.
(* blackbox *)
module ODDR2 (
    output Q,
    input  C0, input C1, input CE,
    input  D0, input D1,
    input  R,  input S
);
    parameter DDR_ALIGNMENT = "C0";
    parameter INIT          = 1'b0;
    parameter SRTYPE        = "SYNC";
endmodule

// Global clock buffer.  yosys does not predefine it either, so hierarchy
// rejects the reference in top_qmtech.v before synth_xilinx could map it.
// It is a real Xilinx cell, declared here as a blackbox so it survives to
// the netlist unchanged.
(* blackbox *)
module BUFG (output O, input I);
endmodule
