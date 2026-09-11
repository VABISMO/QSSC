## ===========================================================================
## build_bitstream.tcl -- Vivado place-and-route + bitstream for the QSSC FPGA
## ---------------------------------------------------------------------------
## Consumes the yosys-synthesised EDIF netlist (fpga/synth/qssc_top.edif, top
## module `top_level`) produced by `bash fpga/synth/run_synth.sh` and runs the
## Vivado P&R flow to a bitstream:
##
##   read_edif  qssc_top.edif      ;# structural netlist from yosys synth_xilinx
##   read_xdc   ../constraints.xdc ;# pin + clock + IO constraints for top_level
##   link_design -top top_level
##   opt_design / place_design / route_design
##   report_timing_summary
##   write_bitstream qssc_top.bit
##
## Run from the repo root on a machine with Vivado installed (this box has no
## Vivado, so the script is validated by inspection only here):
##
##   make -C fpga/synth bitstream          # yosys synth, then this Tcl flow
##   # or, if qssc_top.edif already exists:
##   vivado -mode batch -source fpga/synth/build_bitstream.tcl
##
## Target part: Artix-7 XC7A35T-1CSG324C (single-board parallel QSSC control).
##
## NOTE on the QMTECH carrier: top_level.v expects a 100 MHz clock directly on
## clk_100mhz (constraints.xdc pins it to B8). The QMTECH XC7A15T board has a
## 50 MHz crystal and uses top_qmtech.v (an MMCM wrapper, MMCME2_BASE) to make
## 100 MHz -- for that board synthesise top_qmtech with Vivado (yosys cannot
## instantiate the MMCM primitive) and use constraints_qmtech.xdc instead of
## this flow. This script targets the bare top_level + constraints.xdc path.
## ===========================================================================

set HERE [file normalize [file dirname [info script]]]
set ROOT [file normalize "$HERE/../.."]

# --- device + netlist + constraints -----------------------------------------
set_part xc7a35tcsg324-1

read_edif [file join $HERE qssc_top.edif]
read_xdc  [file join $ROOT fpga constraints.xdc]

link_design -top top_level

# --- place and route --------------------------------------------------------
opt_design
# opt_design is enough for a -1 speed grade at 100 MHz on this logic depth;
# if timing misses appear, add post-place and post-route opt passes:
#   opt_design -directive Explore
#   place_design -directive Explore
#   route_design -directive Explore
place_design
route_design

# --- timing / utilisation reports -------------------------------------------
report_timing_summary -file [file join $HERE timing_summary.rpt]
report_utilization    -file [file join $HERE utilization.rpt]

# --- bitstream --------------------------------------------------------------
write_bitstream -force [file join $HERE qssc_top.bit]

puts "BITSTREAM: [file join $HERE qssc_top.bit]"