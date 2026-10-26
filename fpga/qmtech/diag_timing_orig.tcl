## ---------------------------------------------------------------------------
## diag_timing_orig.tcl -- does the ORIGINAL (unpatched) RTL already fail
## timing at 100 MHz?
##
## The QMTECH build reports WNS = -79.6 ns with 3983 failing endpoints, but
## every failing path sits in command_parser's own logic -- none mention
## dec_fmt or S_FMT_WAIT.  This script synthesises the ORIGINAL
## rtl/command_parser.v (combinational formatters, no FSM patch) and reports
## timing WITHOUT place/route, so it answers "was this already broken?" in
## a couple of minutes instead of a full P&R run.
##
## Not a placeable design: 13011 LUT > 10400.  Timing is estimated post-synth.
## ---------------------------------------------------------------------------
set HERE [file normalize [file dirname [info script]]]
set ROOT [file normalize "$HERE/../.."]
set RTL  [file join $ROOT fpga rtl]
set OUT  [file join $HERE build]

file mkdir $OUT

# same ODDR2 patch as every other flow
set fh  [open [file join $RTL top_level.v] r]; set txt [read $fh]; close $fh
set re {ODDR2\s*#.*?\.Q\s*\(refclk_out\)\)\s*;}
if {[regexp $re $txt]} {
    set txt [regsub $re $txt "assign refclk_out = refclk_25m;  // XC7 PATCH"]
}
set top_patched [file join $OUT top_level_xc7.v]
set fh [open $top_patched w]
puts  $fh "// generated - DO NOT EDIT"; puts  $fh $txt; close $fh

foreach f {uart_rx.v uart_tx.v spi_master.v tlc5947_ctrl.v emitter_array.v \
           adc_reader.v adf4351_ctrl.v qrng_entropy.v dac_ctrl.v \
           command_parser.v} {
    read_verilog [file join $RTL $f]
}
read_verilog $top_patched
read_verilog [file join $HERE top_qmtech.v]
read_xdc     [file join $HERE constraints_qmtech.xdc]

synth_design -top top_qmtech -part xc7a15tcsg325-1

report_timing_summary -file [file join $OUT timing_ORIGINAL_postsynth.rpt]
report_utilization    -file [file join $OUT util_ORIGINAL_postsynth.rpt]

set fh [open [file join $OUT timing_ORIGINAL_postsynth.rpt] r]
set rpt [read $fh]; close $fh
foreach line [split $rpt "\n"] {
    if {[regexp {\|\s*LUT as Logic\s*\|\s*(\d+)} $line -> v]} {
        puts "ORIG_LUT_AS_LOGIC=$v (limit 10400)"
    }
}
# pull the WNS row out of the Design Timing Summary
set n 0
foreach line [split $rpt "\n"] {
    incr n
    if {$n > 130 && $n < 160} {
        if {[regexp {^\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+(\d+)} $line -> wns tns fail tot]} {
            puts "ORIG_WNS=$wns  ORIG_TNS=$tns  FAILING=$fail TOTAL=$tot"
            break
        }
    }
}
puts "=== ORIGINAL (unpatched) timing, post-synthesis ==="
