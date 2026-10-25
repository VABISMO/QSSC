#!/usr/bin/env python3
"""
patch_div_seq.py -- replace the combinational variable/variable divisions in
command_parser.v with the sequential divider (div_seq.v) plus a wait state.

THE BUG (pre-existing, found by post-route timing analysis):
    command_parser.v:1614 and :1642 compute

        sweep_step = (f_stop - f_start) / (n_points - 1)

    as a COMBINATIONAL division between two variables.  Vivado builds a full
    divider from it:

        Data Path Delay : 89.650 ns against a 10 ns budget
        Logic Levels    : 328  (CARRY4=295)

    -> WNS -79.6 ns, 3983 failing endpoints.  The ORIGINAL (unpatched) RTL is
    worse still: WNS -119.6 ns, TNS -19489.  This has never closed timing on
    any device; the design had never been through P&R before.

WHY DIVIDING SEQUENTIALLY IS SAFE
    `sweep_step` is consumed ONCE per command, in the sweep loop
    (`sweep_f <= sweep_f + sweep_step`), and only after the FSM has programmed
    the ADF4351 over SPI -- hundreds of cycles later.  Waiting 34 cycles for
    the quotient before leaving dispatch changes nothing observable: the
    sweep still starts with the correct step, just 0.34 us later.

    The n==1 case (step = 0) keeps its original single-cycle path.

ORIGINAL FILES ARE NEVER WRITTEN TO.
    input : fpga/qmtech/build/command_parser_fmt.v   (after patch_formatters.py)
    output: fpga/qmtech/build/command_parser_div.v

    python3 fpga/qmtech/patch_div_seq.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "fpga" / "qmtech"
SRC = HERE / "build" / "command_parser_fmt.v"
DST = HERE / "build" / "command_parser_div.v"

# ---------------------------------------------------------------- 1. ODMR_SWEEP
ODMR_EXPR = """\
                                sweep_step     <= (arg_vals[3] == 32'd1)
                                                  ? 32'd0
                                                  : ($signed(arg_vals[2]) - $signed(arg_vals[1])) /
                                                    $signed(arg_vals[3] - 32'd1);"""

ODMR_NEW = """\
                                // DIVIDED SEQUENTIALLY (see div_seq.v): the
                                // original combinational 32/32 divide cost
                                // 295 CARRY4 / 89.65 ns.  sweep_step is only
                                // read in the sweep loop, long after this.
                                sweep_step     <= 32'd0;   // filled on div_done
                                div_num        <= $signed(arg_vals[2]) - $signed(arg_vals[1]);
                                div_den        <= $signed(arg_vals[3] - 32'd1);
                                div_start      <= (arg_vals[3] != 32'd1);
                                div_next       <= S_SWEEP_PROG;"""

ODMR_FSM_OLD = """\
                                read_all_mode  <= 1'b0;
                                fsm            <= S_SWEEP_PROG;"""
ODMR_FSM_NEW = """\
                                read_all_mode  <= 1'b0;
                                // n == 1 => step 0, straight through as
                                // before; otherwise wait for div_seq.
                                fsm            <= (arg_vals[3] == 32'd1)
                                                  ? S_SWEEP_PROG
                                                  : S_STEP_WAIT;"""

# ------------------------------------------------------------- 2. ALL_SWEEP
ALL_EXPR = """\
                                sweep_step     <= (arg_vals[2] == 32'd1)
                                                  ? 32'd0
                                                  : ($signed(arg_vals[1]) - $signed(arg_vals[0])) /
                                                    $signed(arg_vals[2] - 32'd1);"""

ALL_NEW = """\
                                // DIVIDED SEQUENTIALLY (see div_seq.v) --
                                // same reasoning as the ODMR_SWEEP path.
                                sweep_step     <= 32'd0;
                                div_num        <= $signed(arg_vals[1]) - $signed(arg_vals[0]);
                                div_den        <= $signed(arg_vals[2] - 32'd1);
                                div_start      <= (arg_vals[2] != 32'd1);
                                div_next       <= S_ASWEEP_PROG;"""

ALL_FSM_OLD = """\
                                asweep_mode    <= 1'b1;
                                fsm            <= S_ASWEEP_PROG;"""
ALL_FSM_NEW = """\
                                asweep_mode    <= 1'b1;
                                fsm            <= (arg_vals[2] == 32'd1)
                                                  ? S_ASWEEP_PROG
                                                  : S_STEP_WAIT;"""

# --------------------------------------------------------------- 3. declarations
DECL_ANCHOR = "    reg [6:0]  fsm;"
DECL = """\
    // --- sequential divider replacing the combinational / (patch_div_seq) ---
    localparam [6:0] S_STEP_WAIT = 7'd72;
    reg               div_start;
    reg signed [31:0] div_num;
    reg signed [31:0] div_den;
    reg [6:0]         div_next;      // state to enter once the quotient lands
    wire signed [31:0] div_quot;
    wire               div_done;
    div_seq u_div (
        .clk   (clk),
        .rst   (reset),
        .start (div_start),
        .num   (div_num),
        .den   (div_den),
        .quot  (div_quot),
        .done  (div_done)
    );
"""

# ------------------------------------------------------------ 4. wait state arm
ARM_ANCHOR = "\n                S_IDLE : begin"
ARM = """\
                S_STEP_WAIT : begin
                    div_start <= 1'b0;
                    if (div_done) begin
                        sweep_step <= div_quot;
                        fsm        <= div_next;
                    end
                end
"""


def main() -> int:
    src = SRC.read_text()
    if "div_seq" in src:
        print("ERROR: patch already applied?")
        return 1

    text = src
    steps = [
        ("ODMR expression", ODMR_EXPR, ODMR_NEW),
        ("ODMR fsm", ODMR_FSM_OLD, ODMR_FSM_NEW),
        ("ALL expression", ALL_EXPR, ALL_NEW),
        ("ALL fsm", ALL_FSM_OLD, ALL_FSM_NEW),
    ]
    for name, old, new in steps:
        n = text.count(old)
        if n != 1:
            print("ERROR: %s anchor found %d times (expected 1)" % (name, n))
            return 1
        text = text.replace(old, new, 1)
        print("patched : %s" % name)

    if text.count(DECL_ANCHOR) != 1:
        print("ERROR: fsm declaration anchor found %d times"
              % text.count(DECL_ANCHOR))
        return 1
    text = text.replace(DECL_ANCHOR, DECL + DECL_ANCHOR, 1)
    print("patched : declarations (div_seq instance, S_STEP_WAIT)")

    if text.count(ARM_ANCHOR) != 1:
        print("ERROR: S_IDLE arm anchor found %d times" % text.count(ARM_ANCHOR))
        return 1
    text = text.replace(ARM_ANCHOR, "\n" + ARM + ARM_ANCHOR, 1)
    print("patched : S_STEP_WAIT state arm")

    # ---- validations -------------------------------------------------------
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
        print("ERROR: control bytes in output")
        return 1

    # Only VARIABLE/VARIABLE divisions are a problem.  `arg_vals[3] / 32'd10`
    # (line 1668) divides by a constant, which Vivado turns into a
    # multiply-by-reciprocal -- it appears 0 times in the timing report and is
    # not a failing path, so it is deliberately left alone.  Matching on
    # "arg_vals AFTER the slash" catches exactly the two divisors that were
    # replaced and ignores the constant one.
    code = re.sub(r"//[^\n]*", "", text)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    leftover = re.findall(r"/[^;\n]*arg_vals\[\d+\]", code)
    if leftover:
        print("ERROR: a variable/variable division is still present:")
        for l in leftover:
            print("   %s" % l.strip()[:90])
        return 1

    checks = [("module", r"\bmodule\b", r"\bendmodule\b"),
              ("begin", r"\bbegin\b", r"\bend\b"),
              ("case", r"\bcase\b|\bcasez\b|\bcasex\b", r"\bendcase\b"),
              ("function", r"\bfunction\b", r"\bendfunction\b"),
              ("task", r"\btask\b", r"\bendtask\b")]
    for name, a, b in checks:
        na, nb = len(re.findall(a, code)), len(re.findall(b, code))
        if na != nb:
            print("ERROR: %s mismatch (%d vs %d)" % (name, na, nb))
            return 1

    if len(re.findall(r"^\s*module\b", text, re.M)) != \
       len(re.findall(r"^\s*endmodule\b", text, re.M)):
        print("ERROR: module/endmodule mismatch")
        return 1

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(text)
    print("checks  : no variable division left, balanced, no ctrl bytes")
    print("output   : %s (%d lines)" % (DST, text.count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
