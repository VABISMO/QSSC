#!/usr/bin/env python3
"""
patch_top_gate.py -- drop the /10 from the RF-gate load.

Runs AFTER the ODDR2 substitution in build_bitstream_qmtech.tcl, on
build/top_level_xc7.v.

THE PROBLEM
    top_level converted the gate duration to counts on the load pulse:

        gate_cnt <= (mw_gate_ns_w >= 32'd10) ? (mw_gate_ns_w / 32'd10)
                                              : 32'd1;

    13.779 ns of path (21 levels, 14 CARRY4) into a 10 ns budget.  At
    S_SEQ_PI2 command_parser writes mw_gate_ns and pulses mw_gate_load in the
    SAME cycle, so the window really is one cycle and set_multicycle_path
    cannot fix it.  Delaying the load instead stretched the gate from 50 to 53
    cycles, which tb_top_level rejects ("expect 50 for 500 ns") -- correctly,
    because that IS the measurement window of the ODMR experiment.

THE FIX
    command_parser now publishes COUNTS (patch_gate_cmd.py), so top_level
    only has to store them: no arithmetic, no timing problem, and the gate
    width is bit-for-bit what the command asked for.

    Input : fpga/qmtech/build/top_level_xc7.v   (ODDR2 already substituted)
    Output: fpga/qmtech/build/top_level_final.v

    python3 fpga/qmtech/patch_top_gate.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "build" / "top_level_xc7.v"
DST = HERE / "build" / "top_level_final.v"

OLD = ("gate_cnt       <= (mw_gate_ns_w >= 32'd10) ? (mw_gate_ns_w / 32'd10)")
NEW = ("gate_cnt       <= (mw_gate_ns_w == 32'd0) ? 32'd1 : mw_gate_ns_w;")

TAIL = "                                                  : 32'd1;"


def main() -> int:
    if not SRC.exists():
        print("ERROR: %s missing (the ODDR2 step must run first)" % SRC)
        return 1
    text = SRC.read_text()
    if "mw_gate_ns_w == 32'd0" in text:
        print("ERROR: patch already applied?")
        return 1

    lines = text.split("\n")
    hits = [i for i, l in enumerate(lines) if OLD in l]
    if len(hits) != 1:
        print("ERROR: gate_cnt divide found %d times (expected 1)" % len(hits))
        return 1
    i = hits[0]

    # the assignment spans two lines: "... / 32'd10)\n ... : 32'd1;"
    if TAIL not in lines[i + 1]:
        print("ERROR: unexpected continuation line:")
        print("   %r" % lines[i + 1])
        return 1
    indent = lines[i + 1][:len(lines[i + 1]) - len(lines[i + 1].lstrip())]
    lines[i:i + 2] = [indent + NEW]
    text = "\n".join(lines)
    print("patched : gate_cnt now stores counts directly (no divide)")

    # ---- validations -------------------------------------------------------
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
        print("ERROR: control bytes in output")
        return 1
    code = re.sub(r"//[^\n]*", "", text)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    if "mw_gate_ns_w /" in code:
        print("ERROR: a divide on mw_gate_ns_w is still present")
        return 1
    if "ODDR2 #" in code:
        print("ERROR: ODDR2 still present -- the ODDR2 step did not run")
        return 1
    for name, a, b in (("module", r"\bmodule\b", r"\bendmodule\b"),
                       ("begin", r"\bbegin\b", r"\bend\b"),
                       ("case", r"\bcase\b|\bcasez\b|\bcasex\b", r"\bendcase\b")):
        na, nb = len(re.findall(a, code)), len(re.findall(b, code))
        if na != nb:
            print("ERROR: %s mismatch (%d vs %d)" % (name, na, nb))
            return 1

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(text)
    print("checks  : no gate divide, no ODDR2, balanced")
    print("output   : %s (%d lines)" % (DST, text.count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
