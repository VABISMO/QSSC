## ---------------------------------------------------------------------------
## diag_timing_paths.tcl -- where do the timing violations actually live?
##
## Opens the routed checkpoint from the last build and lists the worst setup
## paths, grouped by the module they sit in.  Needed because the summary only
## shows the single worst path, while 6323 endpoints are failing.
##
##   vivado -mode batch -source diag_timing_paths.tcl
## ---------------------------------------------------------------------------
set HERE [file normalize [file dirname [info script]]]
set OUT  [file join $HERE build]
set dcp  [file join $OUT post_route.dcp]

if {![file exists $dcp]} {
    error "$dcp not found -- run build.sh first"
}

open_checkpoint $dcp

report_timing -max_paths 60 -nworst 1 -setup -quiet \
    -file [file join $OUT paths60.rpt]
report_timing_summary -report_unconstrained -quiet \
    -file [file join $OUT unconstrained.rpt]

# ---- mine the report --------------------------------------------------------
set fh [open [file join $OUT paths60.rpt] r]
set rpt [read $fh]; close $fh

puts "=== WORST SETUP PATHS (top 60) ==="
set cur {}
foreach line [split $rpt "\n"] {
    if {[regexp {Slack \((VIOLATED|MET)\)\s*:\s*(-?\d+\.\d+)ns} line -> st slack]} {
        if {[llength $cur]} { puts [join $cur " | "] }
        set cur [list [format "%9s" $slack]]
    }
    if {[regexp {^\s+Source:\s+(\S+)} line -> s]} {
        # keep only module path (strip the leaf cell)
        regsub {/[^/]+$} $s "" m
        lappend cur $m
    }
    if {[regexp {^\s+Destination:\s+(\S+)} line -> d]} {
        regsub {/[^/]+$} $d "" m
        lappend cur "-> $m"
    }
    if {[regexp {Data Path Delay:\s+(-?\d+\.\d+)ns\s+\(logic\s+(-?\d+\.\d+)} \
            line -> pd lg]} {
        lappend cur "delay=${pd}ns logic=${lg}"
    }
}
if {[llength $cur]} { puts [join $cur " | "] }

# ---- how many violations per source module ---------------------------------
puts ""
puts "=== VIOLATIONS BY SOURCE MODULE (from the 60 worst) ==="
set fh [open [file join $OUT paths60.rpt] r]
set rpt [read $fh]; close $fh
array set cnt {}
set curmod ""
foreach line [split $rpt "\n"] {
    if {[regexp {Slack \(VIOLATED\)} line]} { set curmod "?" }
    if {[regexp {^\s+Source:\s+(\S+)} line -> s] && $curmod ne ""} {
        regsub {/[^/]+$} $s "" m
        incr cnt($m)
        set curmod ""
    }
}
foreach k [lsort [array names cnt]] {
    puts [format "  %-46s %d" $k $cnt($k)]
}
puts "=== DONE ==="
