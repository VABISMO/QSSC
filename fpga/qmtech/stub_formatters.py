#!/usr/bin/env python3
"""
stub_formatters.py -- diagnostic only: replace the combinational
decimal-formatting functions of rtl/command_parser.v with constant stubs.

Purpose: measure, with Vivado itself, how much of command_parser's LUT
footprint comes from `dec32_pad`, `dec5_pad`, `enc_dec`, `signed_dec48` and
`mrad_to_phase` (each of which chains repeated 32/48-bit `/10` and `%10`
divisions, fully unrolled and combinational).

The ORIGINAL rtl/command_parser.v is never written to: output goes to
fpga/qmtech/build/command_parser_nofmt.v.

    python3 fpga/qmtech/stub_formatters.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "fpga" / "rtl" / "command_parser.v"
DST = ROOT / "fpga" / "qmtech" / "build" / "command_parser_nofmt.v"

# name -> (width vector, constant to return, input declaration)
FUNCTIONS = {
    "dec32_pad":     ("[79:0]",  "80'd0",   "input [31:0] v;"),
    "dec5_pad":      ("[39:0]",  "40'd0",   "input [15:0] v;"),
    "enc_dec":       ("[47:0]",  "48'd0",   "input [15:0] v;"),
    "signed_dec48":  ("[127:0]", "128'd0",  "input signed [47:0] v;"),
    "mrad_to_phase": ("[11:0]",  "12'd0",   "input signed [31:0] mrad;"),
}


def main() -> int:
    src = SRC.read_text()
    out = src
    done = []

    for name, (width, val, arg) in FUNCTIONS.items():
        pat = re.compile(
            r"(function\s+" + re.escape(width) + r"\s+" + re.escape(name)
            + r"\s*;)(.*?)(endfunction)", re.S)

        # NOTE: use a CALLABLE replacement.  A string replacement would have to
        # escape "\1"/"\3" group references, and Python turns a bare "\3" into
        # the raw byte 0x03 -- which silently swallows the trailing
        # "endfunction" and leaves Vivado with a syntax error.  A callable
        # bypasses all escape processing.
        def repl(m, _name=name, _arg=arg, _val=val):
            return (m.group(1) + "\n    " + _arg
                    + "\n    begin " + _name + " = " + _val + "; end\n"
                    + m.group(3))

        out, n = pat.subn(repl, out, count=1)
        if n:
            done.append(name)
        else:
            print("WARNING: pattern did not match %s -- left untouched" % name)

    # --- validate before writing ---------------------------------------------
    if len(done) != len(FUNCTIONS):
        print("ERROR: only %d of %d functions stubbed"
              % (len(done), len(FUNCTIONS)))
        return 1
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", out):
        print("ERROR: control bytes present in output")
        return 1
    nf_open = len(re.findall(r"\bfunction\b", out))
    nf_close = len(re.findall(r"\bendfunction\b", out))
    if nf_open != nf_close:
        print("ERROR: function/endfunction mismatch: %d vs %d"
              % (nf_open, nf_close))
        return 1

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(out)
    print("stubbed : %s" % ", ".join(done))
    print("checks  : no control bytes, function/endfunction = %d/%d"
          % (nf_open, nf_close))
    print("input   : %s (%d lines)" % (SRC, src.count("\n")))
    print("output  : %s (%d lines)" % (DST, out.count("\n")))

    # sanity: no live division left inside the stubbed functions
    for name in done:
        blk = re.search(
            r"function\s+\[[^\]]+\]\s+" + name + r"\s*;.*?endfunction",
            out, re.S)
        if not blk:
            print("ERROR: %s not found after patch" % name)
            return 1
        if re.search(r"\d\s*'%'\s*\d|\d\s*'/'\s*\d|[%/]\s*32'd|[%/]\s*48'sd",
                     blk.group(0)):
            print("ERROR: %s still contains a division" % name)
            return 1
    print("checks  : no division left in stubbed functions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
