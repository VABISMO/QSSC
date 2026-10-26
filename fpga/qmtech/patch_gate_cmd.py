#!/usr/bin/env python3
"""
patch_gate_cmd.py -- pre-divide the RF gate duration at its source.

THE PROBLEM
    command_parser publishes mw_gate_ns (nanoseconds); top_level converts it
    to counts with

        gate_cnt <= (mw_gate_ns_w >= 32'd10) ? (mw_gate_ns_w / 32'd10) : 32'd1;

    That divide measured 13.779 ns (21 levels, 14 CARRY4) against a 10 ns
    budget, and at S_SEQ_PI2 command_parser writes mw_gate_ns and pulses
    mw_gate_load in the SAME cycle -- a genuine one-cycle window, so
    set_multicycle_path cannot help and delaying the load (as an earlier
    attempt did) stretches the RF gate from 50 to 53 cycles, which the
    testbench rightly rejects.

THE FIX
    Move the /10 to the point where mw_gate_ns is written, i.e. publish
    COUNTS instead of nanoseconds:

      * every write from a localparam (ODMR_GATE_NS, PI2_NS, PI_NS,
        LI_HALF_NS) becomes a constant expression -- synthesis folds it to
        zero logic entirely;
      * the one write from arg_vals[3] happens in S_DISPATCH, which is already
        behind the 56-cycle S_FMT_WAIT, so its divide is covered by the
        multicycle constraint added in constraints_qmtech.xdc;
      * top_level then stores the count with no arithmetic at all.

    The gate pulse width is therefore unchanged (it was before any delay was
    introduced) -- verified against tb_top_level's "expect 50 for 500 ns".

Input : fpga/rtl/command_parser.v
Output: fpga/qmtech/build/command_parser_gate.v

    python3 fpga/qmtech/patch_gate_cmd.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "fpga" / "rtl" / "command_parser.v"
DST = ROOT / "fpga" / "qmtech" / "build" / "command_parser_gate.v"

DIV = ("(%s >= 32'd10) ? (%s / 32'd10) : 32'd1" % ("{v}", "{v}"))

# every non-zero write to mw_gate_ns, in source order
REWRITES = [
    ("mw_gate_ns   <= arg_vals[3];",
     "mw_gate_ns   <= (arg_vals[3] >= 32'd10) ? (arg_vals[3] / 32'd10) : 32'd1;"),
    ("mw_gate_ns     <= ODMR_GATE_NS;",
     "mw_gate_ns     <= ODMR_GATE_NS / 32'd10;   // counts, not ns (folded)"),
    ("mw_gate_ns   <= PI2_NS;",
     "mw_gate_ns   <= PI2_NS / 32'd10;           // counts, not ns (folded)"),
    ("mw_gate_ns   <= PI_NS;",
     "mw_gate_ns   <= PI_NS / 32'd10;            // counts, not ns (folded)"),
    ("mw_gate_ns <= PI2_NS;",
     "mw_gate_ns <= PI2_NS / 32'd10;             // counts, not ns (folded)"),
    ("mw_gate_ns <= li_half_ns;",
     "mw_gate_ns <= LI_HALF_NS / 32'd10;         // counts, not ns (folded)"),
]


def main() -> int:
    text = SRC.read_text()
    if "counts, not ns" in text:
        print("ERROR: patch already applied?")
        return 1

    total = 0
    for old, new in REWRITES:
        n = text.count(old)
        if n == 0:
            print("ERROR: not found: %r" % old)
            return 1
        text = text.replace(old, new)          # replace every occurrence
        total += n
        print("patched : %-32s x%d" % (old.strip()[:32], n))

    # the ODMR_GATE_NS write appears twice (ODMR_SWEEP + RUN_ALL_ODMR)
    if total < 7:
        print("ERROR: only %d writes rewritten (expected >= 7)" % total)
        return 1

    # ---- validations -------------------------------------------------------
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
        print("ERROR: control bytes in output")
        return 1
    code = re.sub(r"//[^\n]*", "", text)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    # no mw_gate_ns write may still reference a raw nanosecond value
    for m in re.finditer(r"mw_gate_ns\s*<=\s*([^;]+);", code):
        rhs = m.group(1).strip()
        if rhs in ("32'd0", "32'd00"):
            continue
        if "/ 32'd10" not in rhs:
            print("ERROR: mw_gate_ns still written without /10: %s" % rhs)
            return 1

    for name, a, b in (("module", r"\bmodule\b", r"\bendmodule\b"),
                       ("begin", r"\bbegin\b", r"\bend\b"),
                       ("case", r"\bcase\b|\bcasez\b|\bcasex\b", r"\bendcase\b"),
                       ("function", r"\bfunction\b", r"\bendfunction\b"),
                       ("task", r"\btask\b", r"\bendtask\b")):
        na, nb = len(re.findall(a, code)), len(re.findall(b, code))
        if na != nb:
            print("ERROR: %s mismatch (%d vs %d)" % (name, na, nb))
            return 1

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(text)
    print("checks  : all mw_gate_ns writes now publish counts, balanced")
    print("output   : %s (%d lines)" % (DST, text.count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
