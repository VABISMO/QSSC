#!/usr/bin/env python3
"""
patch_pll_wait.py -- give adf4351_ctrl's combinational divide time to settle.

THE BUG (pre-existing, found by post-route timing):
    ST_IDLE latches the target frequency and goes straight to ST_CALC on the
    NEXT cycle, where the ADF4351 register words are captured:

        ST_IDLE  : tgt_reg <= target_freq_khz;   state <= ST_CALC;   // cycle T
        ST_CALC  : r0_r <= r0; ...                                 // cycle T+1

    The comment claims "the combinational divide has now settled", but only
    ONE cycle elapses while the path measures 52.612 ns (72 logic levels,
    37 CARRY4 + 1 DSP48E1) against a 10 ns budget -> WNS -42.695 ns.  The PLL
    would be programmed with a garbage frequency.

WHY THE WAIT IS SAFE
    The author's intent -- latch the target, let the divide settle, then copy
    the result -- is preserved exactly; only the settle time is made real.
    8 extra cycles = 80 ns > 52.6 ns.  Nothing outside this module observes
    the delay: the sequencer only starts shifting after ST_CALC anyway.

ORIGINAL FILES ARE NEVER WRITTEN TO.
    input : fpga/rtl/adf4351_ctrl.v
    output: fpga/qmtech/build/adf4351_ctrl_wait.v

    python3 fpga/qmtech/patch_pll_wait.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "fpga" / "rtl" / "adf4351_ctrl.v"
DST = ROOT / "fpga" / "qmtech" / "build" / "adf4351_ctrl_wait.v"

DECLS_OLD = "    reg [3:0]  state;"
DECLS_NEW = """\
    reg [3:0]  state;
    reg [3:0]  settle_cnt;   // cycles to let the combinational divide settle"""

STATE_OLD = "    localparam [3:0] ST_DONE  = 4'd5;"
STATE_NEW = """\
    localparam [3:0] ST_DONE  = 4'd5;
    // ST_SETTLE is new (patch_pll_wait): holds the FSM after ST_IDLE so the
    // combinational divide from tgt_reg has time to reach r0..r5.
    localparam [3:0] ST_SETTLE = 4'd6;"""

GO_OLD = "                        state  <= ST_CALC;"
GO_NEW = """\
                        state       <= ST_SETTLE;
                        settle_cnt  <= 4'd8;   // 80 ns > 52.6 ns path delay"""

ARM_ANCHOR = "                ST_CALC : begin"
ARM = """\
                ST_SETTLE : begin
                    if (settle_cnt == 4'd0)
                        state <= ST_CALC;
                    else
                        settle_cnt <= settle_cnt - 4'd1;
                end
"""


def main() -> int:
    src = SRC.read_text()
    if "ST_SETTLE" in src:
        print("ERROR: patch already applied?")
        return 1

    text = src
    for name, old, new in (("declarations", DECLS_OLD, DECLS_NEW),
                           ("state value", STATE_OLD, STATE_NEW),
                           ("ST_IDLE exit", GO_OLD, GO_NEW)):
        n = text.count(old)
        if n != 1:
            print("ERROR: %s anchor found %d times (expected 1)" % (name, n))
            return 1
        text = text.replace(old, new, 1)
        print("patched : %s" % name)

    n = text.count(ARM_ANCHOR)
    if n != 1:
        print("ERROR: ST_CALC arm anchor found %d times" % n)
        return 1
    text = text.replace(ARM_ANCHOR, ARM + ARM_ANCHOR, 1)
    print("patched : ST_SETTLE state arm")

    # ---- validations -------------------------------------------------------
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
        print("ERROR: control bytes in output")
        return 1

    code = re.sub(r"//[^\n]*", "", text)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    for name, a, b in (("module", r"\bmodule\b", r"\bendmodule\b"),
                       ("begin", r"\bbegin\b", r"\bend\b"),
                       ("case", r"\bcase\b|\bcasez\b|\bcasex\b", r"\bendcase\b"),
                       ("function", r"\bfunction\b", r"\bendfunction\b")):
        na, nb = len(re.findall(a, code)), len(re.findall(b, code))
        if na != nb:
            print("ERROR: %s mismatch (%d vs %d)" % (name, na, nb))
            return 1

    # ST_SETTLE must be both declared and handled
    if text.count("ST_SETTLE") < 3:
        print("ERROR: ST_SETTLE not fully wired (%d refs)" % text.count("ST_SETTLE"))
        return 1

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(text)
    print("checks  : balanced, ST_SETTLE declared+used, no ctrl bytes")
    print("output   : %s (%d lines)" % (DST, text.count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
