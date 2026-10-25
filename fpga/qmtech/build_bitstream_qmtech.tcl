## ===========================================================================
## build_bitstream_qmtech.tcl -- P&R + bitstream for the QSSC firmware on the
##                              QMTECH test board (XC7A15T-1CSG325C)
## ---------------------------------------------------------------------------
## RUN THIS ON A MACHINE WITH VIVADO INSTALLED (the development box has no
## Vivado; this script cannot be exercised there):
##
##   vivado -mode batch -source fpga/qmtech/build_bitstream_qmtech.tcl
##
## Output: fpga/qmtech/build/qssc_qmtech.bit
##
## Unlike ../synth/build_bitstream.tcl (which consumes the yosys EDIF, because
## the custom board pins xc7a35tcsg324-1), HERE the Verilog is read directly:
## Vivado synthesises it, and it has to, because top_qmtech.v instantiates
## MMCME2_BASE which yosys does not recognise.
##
## TARGET PART
##   set_part xc7a35tcsg324-1   (custom board -- NOT valid for the QMTECH)
##   set_part xc7a15tcsg325-1   (QMTECH -- this script)
##
## ODDR2 PATCH (read before touching anything)
## ---------------------------------------------------------------------------
## rtl/top_level.v line 443 instantiates ODDR2, which is a Spartan-6/Virtex-6
## primitive and does NOT exist in the xc7 family (Artix-7 uses ODDR, with
## different ports).  Without a patch, Vivado aborts during elaboration, and
## the custom-board build (xc7a35t) is affected too.
##
## This script does NOT modify rtl/top_level.v: it reads the file, replaces
## ONLY the ODDR2 instantiation with an equivalent assign (same semantics as
## the `ifdef FAST_SIM` branch in that file: refclk_div[1] already comes from a
## flip-flop) and works on the copy in build/.  The original is left intact.
##
## The CORRECT fix (recommended, not yet applied) is to change top_level.v to
## use the 7-series ODDR:
##     ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b0), .SRTYPE("SYNC")) u (
##         .C (clk_100mhz), .CE (1'b1), .D1 (refclk_25m), .D2 (refclk_25m),
##         .R (reset_sync), .S (1'b0), .Q (refclk_out));
## and then delete this patch.
## ===========================================================================

set HERE [file normalize [file dirname [info script]]]
set ROOT [file normalize "$HERE/../.."]
set RTL  [file join $ROOT fpga rtl]
set OUT  [file join $HERE build]

file mkdir $OUT

# --- 0. target part ---------------------------------------------------------
# NOTE: deliberately NOT `set_part`.  set_part puts Vivado into project mode,
# after which `link_design` expects an already-synthesised structural netlist
# and rejects RTL ("Designutils 20-61 ... not supported in the structural
# netlist").  The non-project flow passes the part to synth_design below.

# --- 1. patch ODDR2 into a copy, leaving the original untouched -------------
set top_in [file join $RTL top_level.v]
set fh  [open $top_in r]
set txt [read $fh]
close $fh

# Anchor: the instantiation ends with `.Q (refclk_out));`
set re {ODDR2\s*#.*?\.Q\s*\(refclk_out\)\)\s*;}
if {[regexp $re $txt]} {
    set txt [regsub $re $txt \
        "assign refclk_out = refclk_25m;  // XC7 PATCH: ODDR2 (Spartan-6) -> plain flop"]
    puts "INFO: ODDR2 replaced by an assign in the build/ copy (original untouched)"
    puts "INFO: the permanent 7-series ODDR fix is still pending, see header"
} else {
    # If someone already migrated top_level.v to ODDR, just carry on.
    if {[string match "*ODDR2*" $txt]} {
        error "top_level.v contains ODDR2 but the patch pattern did not match. Review by hand."
    }
    puts "INFO: no ODDR2 in top_level.v, no patch needed"
}

set top_patched [file join $OUT top_level_xc7.v]
set fh [open $top_patched w]
puts  $fh "// Generated automatically by build_bitstream_qmtech.tcl -- DO NOT EDIT"
puts  $fh "// Patched copy of rtl/top_level.v (see the .tcl header)"
puts  $fh $txt
close $fh

# Second pass on the same file: delay the RF-gate /10 divide by 3 cycles.
# Must run after the ODDR2 patch above (it consumes top_level_xc7.v).
if {[catch {exec python3 [file join $HERE patch_top_gate.py]} pout]} {
    error "patch_top_gate.py failed:\n$pout"
}
puts $pout
set top_patched [file join $OUT top_level_final.v]

# --- 2. RTL -----------------------------------------------------------------
# command_parser.v carries combinational decimal formatters that cost 30 351
# LUTs (359 % of this part).  Two patches regenerate a copy under build/;
# the original is never written to:
#   patch_formatters.py  -> build/command_parser_fmt.v
#                           13 wire decls -> 13 x dec_fmt (SEQ=1 STEPS=8)
#   patch_fsm_wait.py    -> build/command_parser_wait.v
#                           10 reply transitions diverted through S_FMT_WAIT
#                           so the registered dec_fmt has time to settle
# Both are verified by the project regression: tb_top_level ALL CHECKS PASSED.
foreach p {patch_gate_cmd.py patch_formatters.py patch_div_seq.py \
           patch_pll_wait.py patch_fsm_wait.py} {
    if {[catch {exec python3 [file join $HERE $p]} pout]} {
        error "$p failed:\n$pout"
    }
    puts $pout
}

foreach f {uart_rx.v uart_tx.v spi_master.v tlc5947_ctrl.v emitter_array.v \
           adc_reader.v qrng_entropy.v dac_ctrl.v} {
    read_verilog [file join $RTL $f]
}
read_verilog [file join $OUT command_parser_wait.v]  ;# patched command_parser
read_verilog [file join $OUT adf4351_ctrl_wait.v]    ;# patched adf4351_ctrl
read_verilog [file join $HERE dec_fmt.v]             ;# double-dabble formatter
read_verilog [file join $HERE div_seq.v]             ;# sequential divider
read_verilog $top_patched                            ;# top_level (patched)
read_verilog [file join $HERE top_qmtech.v]

# --- 3. QMTECH constraints --------------------------------------------------
read_xdc [file join $HERE constraints_qmtech.xdc]

# --- 4. synthesis + place & route -------------------------------------------
# synth_design is the non-project entry point: it takes the part, elaborates
# the RTL and synthesises it in one step.  link_design would be wrong here.
synth_design -top top_qmtech -part xc7a15tcsg325-1

# Checkpoint right after synthesis: lets timing be re-analysed (or P&R
# re-run) without repeating the whole flow every iteration.
write_checkpoint -force [file join $OUT post_synth.dcp]

opt_design
place_design -directive Explore
route_design -directive Explore

# Keep the routed design so timing can be queried without a rebuild.
write_checkpoint -force [file join $OUT post_route.dcp]

# --- 5. timing closure pass -- MUST precede write_bitstream ------------------
# The remaining violations are genuine single-cycle paths (e.g. the lock-in
# MAC: lip_prime_idx -> lip_I DSP input, 10.712 ns vs 10 ns) -- they cannot be
# widened with a constraint because the logic really does run every cycle.
# phys_opt_design is Vivado's post-route retiming/spreading pass and is the
# standard way to close a sub-nanosecond residual.  Running it after
# write_bitstream would silently produce a bitstream without its changes.
if {[catch {phys_opt_design} e]} {
    puts "WARNING: phys_opt_design failed: $e"
} else {
    puts "INFO: phys_opt_design completed"
}

# --- 6. bitstream -----------------------------------------------------------
# Diagnostics below must not cost a completed place-and-route run.
set bit [file join $OUT qssc_qmtech.bit]
write_bitstream -force $bit

# --- 6. setup slack (warn, never abort) --------------------------------------
if {[catch {get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]} tns]} {
    puts "WARNING: could not read setup slack: $tns"
} else {
    puts "INFO: worst setup slack = $tns ns"
    if {$tns < 0} {
        puts "WARNING: TIMING FAILED (negative slack). See timing_summary_qmtech.rpt"
    } else {
        puts "INFO: TIMING MET"
    }
}

# --- 7. reports (isolated so one failure cannot abort the rest) --------------
foreach {rpt cmd} {
    timing_summary_qmtech.rpt  {report_timing_summary}
    utilization_qmtech.rpt     {report_utilization}
    clocks_qmtech.rpt          {report_clock_utilization}
} {
    if {[catch {eval "$cmd -file [file join $OUT $rpt]"} e]} {
        puts "WARNING: report $rpt not written: $e"
    } else {
        puts "INFO: wrote $rpt"
    }
}

# --- 7. hot-bitstream JTAG programming --------------------------------------
# Uncomment once the JTAG cable is attached:
# open_hw_manager
# connect_hw_server -url localhost:3141
# open_hw_target
# set dev [lindex [get_hw_devices xc7a15t_0] 0]
# current_hw_device $dev
# set_property PROGRAM.FILE $bit $dev
# program_hw_devices $dev
# refresh_hw_device -update_hw_probes false $dev

puts "BITSTREAM: $bit"
puts "Flash it with:  openFPGALoader -c ft232 $bit"
