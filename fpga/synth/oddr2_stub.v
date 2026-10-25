// =============================================================================
// oddr2_stub.v -- declaration of ODDR2 for yosys only
// -----------------------------------------------------------------------------
// rtl/top_level.v instantiates ODDR2 when forwarding the 25 MHz reference to
// the ADF4351 (introduced by audit #110, commit 0170d54).  ODDR2 is a
// Spartan-6/Virtex-6 primitive; yosys does not provide it, so `hierarchy
// -check` in synth_yosys.ys used to abort the whole synthesis job with:
//
//     ERROR: Module `ODDR2' referenced in module `top_level' ... not part
//            of the design.
//
// Declaring it as a blackbox lets hierarchy bind the cell and lets the rest of
// the flow (latch assert, resource report, "Fits XC7A35T") run normally.
// synth_xilinx replaces this stub with the identical definition from
// +/xilinx/cells_xtra.v, so the netlist and the cell counts are unaffected.
//
// NOTE (Vivado flow): ODDR2 does NOT exist on Artix-7 -- the QMTECH build
// substitutes a plain assign in a COPY of top_level.v, see
// fpga/qmtech/build_bitstream_qmtech.tcl.  The correct fix for the custom
// board is to use the 7-series ODDR primitive instead.
// =============================================================================
(* blackbox *)
module ODDR2 (
    output Q,
    input  C0,    // capture on rising edge of C0
    input  C1,    // capture on rising edge of C1
    input  CE,
    input  D0,
    input  D1,
    input  R,     // synchronous reset, active high
    input  S      // synchronous set, active high
);
    parameter DDR_ALIGNMENT = "C0";
    parameter INIT          = 1'b0;
    parameter SRTYPE        = "SYNC";
endmodule
