#!/usr/bin/env bash
# run_checks.sh -- run the QMTECH yosys checks under resource limits.
#
# The full synth flow (check.ys: synth_xilinx + abc9) peaked at ~6 GB RSS and
# two of them in parallel pushed the box into swap.  This wrapper always:
#   * caps virtual memory (default 4 GB; tune with MEM_MB)
#   * drops to idle CPU/IO priority (nice 19 + ionice idle when available)
#   * runs one check at a time, never in parallel
#
# Usage (from the repo root):
#   bash fpga/qmtech/yosys_check/run_checks.sh          # lite check only (~fast)
#   FULL=1 bash fpga/qmtech/yosys_check/run_checks.sh   # + full resource count
#   MEM_MB=6144 FULL=1 bash .../run_checks.sh           # raise the cap for FULL
#
# Exit code: 0 = all requested checks passed.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
LOGDIR="${LOGDIR:-/tmp/qssc_qmtech_checks}"
mkdir -p "$LOGDIR"

MEM_MB="${MEM_MB:-4096}"
FULL="${FULL:-0}"

# Refuse to run if another yosys is already up: never run two at once.
if pgrep -x yosys >/dev/null 2>&1; then
    echo "ERROR: a yosys process is already running. Run one at a time." >&2
    exit 3
fi

PRE=(nice -n 19)
command -v ionice >/dev/null 2>&1 && PRE=(ionice -c3 nice -n 19)

run_one() {  # $1 = script name (in this dir), $2 = tag
    local script="$HERE/$1" tag="$2"
    local log="$LOGDIR/$tag.log" out="$LOGDIR/$tag.out" t="$LOGDIR/$tag.time"

    echo "=== $tag (MEM cap ${MEM_MB} MB, ${PRE[*]}) ==="
    free -h | sed -n 2p | sed 's/^/    before: /'

    # ulimit -v takes kB.  NOTE: /usr/bin/time is not installed on this box,
    # so elapsed time comes from bash's `time` builtin (TIMEFORMAT -> stderr).
    ( ulimit -v $((MEM_MB * 1024))
      TIMEFORMAT='    elapsed: %3R s'
      time "${PRE[@]}" yosys -l "$log" -s "$script" ) >"$out" 2>"$t"
    local rc=$?

    if [ $rc -ne 0 ]; then
        echo "    FAILED (exit $rc) -- see $out and $t"
        grep -m4 -E 'ERROR|out of memory|memory allocation|virtual memory' \
            "$out" "$t" 2>/dev/null | sed 's/^/      /'
        return 1
    fi

    grep -E 'elapsed:' "$t" | tail -1
    grep -E '^=== .* COMPLETE' "$log" | tail -1 | sed 's/^/    /'
    free -h | sed -n 2p | sed 's/^/    after:  /'
    return 0
}

cd "$ROOT"
rc=0

# Lite check always runs: hierarchy + latch assert, a few hundred MB.
run_one check_lite.ys lite || rc=1

# Full resource count is opt-in because it is the expensive one.
if [ "$FULL" = "1" ]; then
    run_one check.ys full || rc=1
else
    echo "=== full check skipped (set FULL=1 to run it) ==="
fi

exit $rc
