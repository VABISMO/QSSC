#!/usr/bin/env bash
# Run yosys synth_xilinx on the QSSC control RTL and emit a Markdown report.
# Produces:
#   fpga/synth/qssc_top.edif   - Vivado-compatible EDIF netlist
#   fpga/synth/qssc_top.json   - portable JSON netlist
#   fpga/synth/synth.log       - full yosys log
#   fpga/synth/SYNTH_REPORT.md - human summary (resource usage vs XC7A35T,
#                                inferred latches, width warnings, blackboxes)
#
# This is synthesis only (no place-and-route / no timing). yosys maps to the
# 7-series cell library so the LUT/FF/BRAM/DSP counts are a realistic resource
# estimate, but real Vivado P&R is still required for timing closure + bitstream.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$HERE"
LOG="$OUT/synth.log"
REPORT="$OUT/SYNTH_REPORT.md"

# Locate yosys (prefer oss-cad-suite on PATH, then /tmp/oss-cad-suite).
if command -v yosys >/dev/null 2>&1; then
    YOSYS=yosys
elif [[ -x "$HOME/oss-cad-suite/bin/yosys" ]]; then
    export PATH="$HOME/oss-cad-suite/bin:$PATH"
    YOSYS=yosys
elif [[ -x /tmp/oss-cad-suite/bin/yosys ]]; then
    export PATH="/tmp/oss-cad-suite/bin:$PATH"
    YOSYS=yosys
else
    echo "ERROR: yosys not found. Install oss-cad-suite or put yosys on PATH." >&2
    exit 2
fi

echo "=== Running $YOSYS $(yosys --version 2>&1 | head -1) ==="
cd "$ROOT"
# -l tees the log; -q keeps stdout quiet; the script echoes its own banner.
"$YOSYS" -l "$LOG" -q -s "$HERE/synth_yosys.ys" 2>&1 | tail -20 || {
    echo "ERROR: yosys synthesis failed — see $LOG" >&2
    tail -40 "$LOG" >&2
    exit 1
}

# --- build the human report --------------------------------------------------
# make_report.py exits non-zero if it sees hard problems (blackboxes / incomplete);
# we still want the report printed so don't let `set -e` abort here.
python3 "$HERE/make_report.py" "$LOG" "$REPORT" || true
echo "=== Report: $REPORT ==="
cat "$REPORT"