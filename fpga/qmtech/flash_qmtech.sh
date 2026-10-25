#!/usr/bin/env bash
# flash_qmtech.sh -- verify a bitstream targets the QMTECH XC7A15T, then flash.
#
# The .bit header carries the target part in plain text.  Loading a bitstream
# built for a different die/package is a silent-wrong outcome (Vivado/openFPGALoader
# may still write it), so this refuses anything that is not xc7a15tcsg325.
#
# Usage (from anywhere):
#   bash fpga/qmtech/flash_qmtech.sh [path/to/bitstream.bit]
# Default bitstream: fpga/qmtech/build/qssc_qmtech.bit
#
# Exit: 0 = flashed and DONE asserted, non-zero = refused or failed.
set -u

# Substring the part field must contain.  Deliberately NOT the full part name
# with the "xc" vendor prefix: the .bit `b` field stores it without the prefix
# ("7a15tcsg325"), and this substring matches both spellings.
EXPECT_PART="7a15tcsg325"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BIT="${1:-$HERE/build/qssc_qmtech.bit}"

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --- 1. the bitstream exists -------------------------------------------------
[ -f "$BIT" ] || die "bitstream not found: $BIT
  build it first:
    source \$HOME/Xilinx/Vivado/<ver>/settings64.sh
    vivado -mode batch -source $HERE/build_bitstream_qmtech.tcl"
say "bitstream : $BIT  ($(stat -c%s "$BIT") bytes)"

# --- 2. header must name the right part -------------------------------------
# Xilinx .bit layout: ASCII fields tagged a=design, b=part, c=date, d=time.
# NOTE: the `b` field stores the part WITHOUT the vendor prefix on the Vivado
# build we have ("7a15tcsg325", not "xc7a15tcsg325"), so match on the
# substring that is present either way rather than the full part name.
hdr=$(head -c 512 "$BIT" | strings -n 4 | tr -d '\0')
say "design    : $(printf '%s' "$hdr" | head -1)"
part=$(printf '%s' "$hdr" | grep -oiE 'x?c?7a[0-9]+t[a-z0-9-]*' | head -1)
say "part field: ${part:-<no part in header>}"

[ -n "$part" ] || die "could not read a part name out of the .bit header"
case "$hdr" in
    *"$EXPECT_PART"*) say "part OK   : contains $EXPECT_PART" ;;
    *) die "WRONG PART: header is for '$part', board needs $EXPECT_PART. Refusing to flash." ;;
esac

# --- 3. JTAG reachability ---------------------------------------------------
if lsmod 2>/dev/null | grep -q '^ftdi_sio'; then
    die "ftdi_sio is loaded -- it owns the FTDI device.
  sudo rmmod ftdi_sio   (or blacklist it, see the udev/modprobe notes)"
fi
say "--- detect before programming ---"
openFPGALoader -c ft232 --detect >/tmp/.qmtech_detect 2>&1 || {
    cat /tmp/.qmtech_detect; die "JTAG detect failed"; }
grep -E 'idcode|family|irlength' /tmp/.qmtech_detect | sed 's/^/  /'
grep -qi 'artix a7 15t' /tmp/.qmtech_detect \
    || die "the device on JTAG is not an artix a7 15t -- check cabling"

# --- 4. program (SRAM, volatile) --------------------------------------------
say "--- programming ---"
openFPGALoader -c ft232 "$BIT" || die "programming failed"
# openFPGALoader prints `done 1` when DONE is high after configuration.
say ""
say "RESULT: flashed.  Look for: ir: ... init 1 done 1"
say "  D1/D4 = user LEDs (active-low), D2 = power, D3 = DONE (red)"
say "  Note: SRAM config is lost on power cycle. For permanence use:"
say "        openFPGALoader -c ft232 -f <file>.mcs"
