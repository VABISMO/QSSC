#!/usr/bin/env python3
"""
patch_fsm_wait.py -- give the reply states time to see dec_fmt's output.

WHY THIS IS NECESSARY
    command_parser writes a formatted source and reads its digits only ~2
    cycles later, e.g.:

        S_LI_ON   : li_sum_on <= li_sum_on + det_code;
        S_LI_NEXT : if (li_i+1 >= li_n) fsm <= S_LI_ENC;
        S_LI_ENC  : li_line[0] <= li_sum_on_pad[79:72];

    No registered formatter can present a new value in 2 cycles.  dec_fmt is
    therefore parameterised (SEQ/STEPS) so it fits the XC7A15T, and this patch
    routes every reply-building transition through one generic wait state,
    giving it ~16 cycles to settle instead of adding a bespoke wait state per
    reply path.

    Reply CONTENT is unchanged; only the time at which the line is assembled
    moves by <0.2 us at 100 MHz.

Input : fpga/qmtech/build/command_parser_div.v  (after patch_div_seq.py)
Output: fpga/qmtech/build/command_parser_wait.v

    python3 fpga/qmtech/patch_fsm_wait.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "fpga" / "qmtech"
SRC = HERE / "build" / "command_parser_div.v"
DST = HERE / "build" / "command_parser_wait.v"

# reply-building states that sample a dec_fmt output in the same cycle
STATES = [
    "S_SEQ_COUNT_ENC", "S_SEQ_CODE_ENC", "S_SWEEP_ENC",
    "S_LIP_ENC", "S_LI_ENC", "S_ENC",          # longest first: S_ENC is a
                                                # suffix of the others
]

DECL = """\
    // --- dec_fmt settle wait (added by patch_fsm_wait.py) -------------------
    // The reply states below read formatted digits that were written only a
    // couple of cycles earlier; dec_fmt is registered so it needs longer.
    // S_FMT_WAIT parks the FSM for FMT_WAIT_N cycles before the reply state.
    localparam [6:0] S_FMT_WAIT = 7'd71;
    // STEPS=2 => GROUPS = W/2 (24 for W=48), period = GROUPS+1, so the worst
    // case age of an output is 2*GROUPS+1 = 49 cycles after the source
    // changes.  56 gives margin; needs 6 bits (max 63).
    localparam [5:0] FMT_WAIT_N = 6'd56;
    reg [6:0] fmt_next;
    reg [5:0] fmt_cnt;
"""

ARM = """\
                S_FMT_WAIT : begin
                    if (fmt_cnt == 6'd0)
                        fsm <= fmt_next;
                    else
                        fmt_cnt <= fmt_cnt - 6'd1;
                end
"""


def main() -> int:
    if not SRC.exists():
        print("ERROR: %s missing -- run patch_formatters.py first" % SRC)
        return 1
    text = SRC.read_text()

    # ---- 1. declarations ---------------------------------------------------
    anchor_decl = "    reg [6:0]  fsm;"
    if text.count(anchor_decl) != 1:
        print("ERROR: fsm declaration anchor found %d times"
              % text.count(anchor_decl))
        return 1
    if "S_FMT_WAIT" in text:
        print("ERROR: patch already applied?")
        return 1
    text = text.replace(anchor_decl, DECL + anchor_decl, 1)

    # ---- 2. the new case arm (before the first arm) ------------------------
    anchor_arm = "\n                S_IDLE : begin"
    if text.count(anchor_arm) != 1:
        print("ERROR: S_IDLE arm anchor found %d times"
              % text.count(anchor_arm))
        return 1
    text = text.replace(anchor_arm, "\n" + ARM + anchor_arm, 1)

    # ---- 3. redirect every reply-building transition -----------------------
    pat = re.compile(
        r"\bfsm(\s*)<=\s*(" + "|".join(STATES) + r")\s*;")

    counts = {s: 0 for s in STATES}

    def repl(m):
        st = m.group(2)
        counts[st] += 1
        # Divert through S_FMT_WAIT, remembering where to go afterwards.
        # `fsm` MUST be assigned S_FMT_WAIT -- assigning the original state
        # here would leave the transition untouched and only add dead
        # fmt_next/fmt_cnt writes (which is exactly the bug this line fixes).
        #
        # Always wrapped in begin/end: several of these transitions are the
        # sole statement of an `if` without braces, where only ONE statement
        # is legal -- appending two more would leave `else` without an `if`.
        return ("begin fsm <= S_FMT_WAIT; fmt_next <= %s; "
                "fmt_cnt <= FMT_WAIT_N; end" % st)

    text, total = pat.subn(repl, text)

    # ---- second redirect: entry to S_DISPATCH after the LAST argument -------
    # Of the 26 transitions into S_DISPATCH only this one follows a write to
    # arg_vals[] in the PREVIOUS cycle.  S_DISPATCH immediately computes
    #   pll_phase = mrad_to_phase(arg_vals[4])   -- a 39.7 ns path (two
    # divides by 6283) that only had ~3 cycles of window.  Reusing S_FMT_WAIT
    # widens it to FMT_WAIT_N+1 cycles.  The other 25 entries come from error
    # paths that never read the fresh argument, so they are left alone.
    LAST_OLD = """\
                        if (arg_idx + 3'd1 >= arg_count) begin
                            fsm <= S_DISPATCH;
                        end else begin
                            fsm <= S_ARGSP;
                        end"""
    LAST_NEW = """\
                        if (arg_idx + 3'd1 >= arg_count) begin
                            // arg_vals[] was written on the line above;
                            // S_DISPATCH derives pll_phase from arg_vals[4]
                            // through two divides by 6283 (39.7 ns path), so
                            // give it time to settle before dispatch sees it.
                            begin fsm <= S_FMT_WAIT; fmt_next <= S_DISPATCH;
                                  fmt_cnt <= FMT_WAIT_N; end
                        end else begin
                            fsm <= S_ARGSP;
                        end"""
    n_last = text.count(LAST_OLD)
    if n_last != 1:
        print("ERROR: last-arg dispatch anchor found %d times (expected 1)"
              % n_last)
        return 1
    text = text.replace(LAST_OLD, LAST_NEW, 1)
    total += 1
    print("redirect : last-arg -> S_DISPATCH via S_FMT_WAIT")

    # ---- validations -------------------------------------------------------
    if total < 8:
        print("ERROR: only %d transitions redirected (expected ~10)" % total)
        for s, c in counts.items():
            if c:
                print("   %-18s %d" % (s, c))
        return 1
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
        print("ERROR: control bytes in output")
        return 1

    code = re.sub(r"//[^\n]*", "", text)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    checks = [
        ("module",  r"\bmodule\b",     r"\bendmodule\b"),
        ("begin",   r"\bbegin\b",      r"\bend\b"),
        ("case",    r"\bcase\b|\bcasez\b|\bcasex\b", r"\bendcase\b"),
        ("function", r"\bfunction\b",  r"\bendfunction\b"),
        ("task",    r"\btask\b",       r"\bendtask\b"),
    ]
    for name, a, b in checks:
        na, nb = len(re.findall(a, code)), len(re.findall(b, code))
        if na != nb:
            print("ERROR: %s mismatch (%d vs %d)" % (name, na, nb))
            return 1

    DST.write_text(text)
    print("redirect : %d transition(s)" % total)
    for s, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        if c:
            print("    %-18s %d" % (s, c))
    print("added    : S_FMT_WAIT (7'd71), FMT_WAIT_N=56, fmt_next/fmt_cnt")
    print("checks   : balanced module/begin/case/function/task, no ctrl bytes")
    print("output   : %s (%d lines)" % (DST, text.count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
