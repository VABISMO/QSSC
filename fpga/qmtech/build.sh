#!/usr/bin/env bash
# build.sh -- build the QMTECH bitstream with Vivado, headless and polite.
#
# Wraps three things that are easy to get wrong by hand:
#   1. settings64.sh          -- puts vivado on PATH (only sets PATH, no libs)
#   2. ncurses5 shim          -- Vivado's libedit.so.0 needs libncurses.so.5 and
#                                libtinfo.so.5, which Debian 13 no longer ships
#                                and the installer did not link for this distro.
#                                The shim points ONLY at those two libraries from
#                                Vivado's own bundled SuSE/ copies, so nothing
#                                else (libstdc++, libgcc) gets shadowed.
#   3. priority               -- nice 19 + ionice idle so a 20-40 min P&R run
#                                never pre-empts the desktop.
#
# Usage:
#   bash fpga/qmtech/build.sh                # build
#   VIVADO_ROOT=/path/to/2026.1 bash ...      # if Vivado is elsewhere
#
# Output: fpga/qmtech/build/qssc_qmtech.bit
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- locate Vivado -----------------------------------------------------------
VIVADO_ROOT="${VIVADO_ROOT:-}"
if [ -z "$VIVADO_ROOT" ]; then
    for c in "$HOME"/projects/vivado/*/ "$HOME"/Xilinx/*/ /opt/Xilinx/*/; do
        [ -f "$c/Vivado/settings64.sh" ] && VIVADO_ROOT="${c%/}" && break
    done
fi
[ -n "$VIVADO_ROOT" ] || { echo "ERROR: Vivado not found; set VIVADO_ROOT" >&2; exit 2; }
[ -f "$VIVADO_ROOT/Vivado/settings64.sh" ] \
    || { echo "ERROR: $VIVADO_ROOT/Vivado/settings64.sh missing" >&2; exit 2; }
echo "Vivado root : $VIVADO_ROOT"

# shellcheck disable=SC1091
. "$VIVADO_ROOT/Vivado/settings64.sh"

# --- ncurses5 shim (idempotent, created on demand) ---------------------------
SUSE="$VIVADO_ROOT/Vivado/lib/lnx64.o/SuSE"
SHIM="$HOME/vivado_ncurses_shim"
if [ ! -e "$SHIM/libncurses.so.5" ]; then
    [ -d "$SUSE" ] || { echo "ERROR: no bundled SuSE/ libs at $SUSE" >&2; exit 2; }
    mkdir -p "$SHIM"
    ln -sf "$SUSE/libncurses.so.5" "$SHIM/"
    ln -sf "$SUSE/libtinfo.so.5"   "$SHIM/"
    echo "shim        : created in $SHIM"
fi
export LD_LIBRARY_PATH="$SHIM${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
echo "shim        : $SHIM"

# --- sanity: the license must resolve parts, else P&R will fail late ---------
# (a leftover Vivado_Alveo_Package feature makes Vivado select the ALVEO tier
#  and report zero parts -- see README.md)
if command -v vivado >/dev/null 2>&1; then
    # NOTE: `-source /dev/stdin` does NOT work -- Vivado echoes the source
    # command and silently executes nothing, so the check would always fail.
    # Use a real temp file.
    LICCHK="$(mktemp /tmp/.qssc_licchk.XXXXXX.tcl)"
    trap 'rm -f "$LICCHK"' EXIT
    printf 'puts "LICPARTS=[llength [get_parts -quiet *xc7a15tcsg325-1]]"\n' > "$LICCHK"
    if vivado -mode batch -nojournal -nolog -source "$LICCHK" \
            >/tmp/.qssc_licchk 2>&1; then
        if ! grep -q 'LICPARTS=1' /tmp/.qssc_licchk; then
            echo "ERROR: xc7a15tcsg325-1 not visible -- license tier problem." >&2
            echo "       Expected a BASIC license (Alveo tier reports 0 parts)." >&2
            grep -m1 'license' /tmp/.qssc_licchk >&2 || true
            exit 3
        fi
        echo "part        : xc7a15tcsg325-1 visible (LICPARTS=1)"
    else
        echo "WARNING: pre-flight license check could not run, continuing anyway" >&2
        tail -3 /tmp/.qssc_licchk >&2 || true
    fi
fi

# --- build -------------------------------------------------------------------
PRE=(nice -n 19)
command -v ionice >/dev/null 2>&1 && PRE=(ionice -c3 nice -n 19)

echo "running     : ${PRE[*]} vivado -mode batch"
echo "log         : $HERE/build/vivado_qmtech.log"
mkdir -p "$HERE/build"

cd "$HERE"
"${PRE[@]}" vivado -mode batch -nojournal \
    -log "$HERE/build/vivado_qmtech.log" \
    -source "$HERE/build_bitstream_qmtech.tcl"

echo "done. bitstream: $HERE/build/qssc_qmtech.bit"
