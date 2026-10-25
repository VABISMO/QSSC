#!/usr/bin/env python3
"""
patch_formatters.py -- replace the combinational decimal formatters in
rtl/command_parser.v with instances of dec_fmt.v (double-dabble).

WHY: the combinational helpers chain repeated 32/48-bit `/10` and `%10`
divisions.  Vivado expands them into 30 351 LUTs -- 81 % of the design, and
359 % of an XC7A15T on its own, so the design cannot be placed at all
([DRC UTLZ-1]).  dec_fmt costs ~25 LUTs each and is bit-identical (proven by
tb_dec_fmt: 1259 vectors, 0 errors).

WHAT CHANGES BEHAVIOUR: dec_fmt is free-running and publishes a result at the
end of a conversion, so each formatted value lags its source by at most
2*(W+1) cycles (<=0.98 us at 100 MHz).  The digits themselves are exact.

The ORIGINAL fpga/rtl/command_parser.v is never written to.  Output:
fpga/qmtech/build/command_parser_fmt.v

    python3 fpga/qmtech/patch_formatters.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Runs AFTER patch_gate_cmd.py, which pre-divides the RF gate duration.
SRC = ROOT / "fpga" / "qmtech" / "build" / "command_parser_gate.v"
DST = ROOT / "fpga" / "qmtech" / "build" / "command_parser_fmt.v"

# ---------------------------------------------------------------- replacements
# each entry: exact source line -> replacement block (or "" to delete)
R = {}

R["    wire [127:0] lip_I_dec = signed_dec48(lip_I);"] = """\
    wire [127:0] lip_I_dec;
    wire         lip_I_dec_v;
    dec_fmt #(.W(48), .D(15), .SIGNED(1)) u_ff_lipI (
        .clk(clk), .rst(reset), .din(lip_I), .dout(lip_I_dec),
        .valid(lip_I_dec_v));"""

R["    wire [127:0] lip_Q_dec = signed_dec48(lip_Q);"] = """\
    wire [127:0] lip_Q_dec;
    wire         lip_Q_dec_v;
    dec_fmt #(.W(48), .D(15), .SIGNED(1)) u_ff_lipQ (
        .clk(clk), .rst(reset), .din(lip_Q), .dout(lip_Q_dec),
        .valid(lip_Q_dec_v));"""

R["    wire [79:0]  lip_n_dec = dec32_pad(lip_n);"] = """\
    wire [79:0]  lip_n_dec;
    wire         lip_n_dec_v;
    dec_fmt #(.W(32), .D(10), .SIGNED(0)) u_ff_lipn (
        .clk(clk), .rst(reset), .din(lip_n), .dout(lip_n_dec),
        .valid(lip_n_dec_v));"""

R["    wire [47:0] enc = enc_dec(code_latch);"] = """\
    // SHARED FORMATTER: enc, sweep_c_pad and seq_code_pad were ALL
    // dec5_pad(code_latch) -- the same value instantiated three times.  One
    // instance plus aliases replaces three, saving two full formatters; the
    // design was 82 LUT over an XC7A15T at STEPS=2 without this.  Output is
    // byte-for-byte identical (tb_dec_fmt + tb_top_level).
    wire [39:0] code_pad;
    wire        code_pad_v;
    dec_fmt #(.W(16), .D(5), .SIGNED(0)) u_ff_code (
        .clk(clk), .rst(reset), .din(code_latch), .dout(code_pad),
        .valid(code_pad_v));
    wire [39:0] enc_d = code_pad;
    wire [7:0] enc_len = (enc_d[39:32] != 8'h30) ? 8'd5 :
                         (enc_d[31:24] != 8'h30) ? 8'd4 :
                         (enc_d[23:16] != 8'h30) ? 8'd3 :
                         (enc_d[15:8]  != 8'h30) ? 8'd2 : 8'd1;
    wire [47:0] enc = {enc_len, enc_d};"""

R["    wire [47:0] enc_raw       = enc_dec(rng_raw_count);"] = """\
    wire [39:0] enc_raw_d;
    wire        enc_raw_d_v;
    dec_fmt #(.W(16), .D(5), .SIGNED(0)) u_ff_encraw (
        .clk(clk), .rst(reset), .din(rng_raw_count), .dout(enc_raw_d),
        .valid(enc_raw_d_v));
    wire [7:0] enc_raw_len = (enc_raw_d[39:32] != 8'h30) ? 8'd5 :
                             (enc_raw_d[31:24] != 8'h30) ? 8'd4 :
                             (enc_raw_d[23:16] != 8'h30) ? 8'd3 :
                             (enc_raw_d[15:8]  != 8'h30) ? 8'd2 : 8'd1;
    wire [47:0] enc_raw = {enc_raw_len, enc_raw_d};"""

R["    wire [79:0] sweep_f_pad = dec32_pad(sweep_f);"] = """\
    wire [79:0] sweep_f_pad;
    wire        sweep_f_pad_v;
    dec_fmt #(.W(32), .D(10), .SIGNED(0)) u_ff_sweepf (
        .clk(clk), .rst(reset), .din(sweep_f), .dout(sweep_f_pad),
        .valid(sweep_f_pad_v));"""

R["    wire [39:0] sweep_c_pad = dec5_pad(code_latch);"] = """\
    wire [39:0] sweep_c_pad = code_pad;   // shared with enc / seq_code_pad"""

R["    wire [39:0] seq_count_pad = dec5_pad({5'd0, seq_n});"] = """\
    wire [15:0] seq_count_in = {5'd0, seq_n};
    wire [39:0] seq_count_pad;
    wire        seq_count_pad_v;
    dec_fmt #(.W(16), .D(5), .SIGNED(0)) u_ff_seqc (
        .clk(clk), .rst(reset), .din(seq_count_in), .dout(seq_count_pad),
        .valid(seq_count_pad_v));"""

R["    wire [39:0] seq_code_pad  = dec5_pad(code_latch);"] = """\
    wire [39:0] seq_code_pad = code_pad;  // shared with enc / sweep_c_pad"""

R["    wire [79:0] li_sum_on_pad  = dec32_pad(li_sum_on);"] = """\
    wire [79:0] li_sum_on_pad;
    wire        li_sum_on_pad_v;
    dec_fmt #(.W(32), .D(10), .SIGNED(0)) u_ff_lion (
        .clk(clk), .rst(reset), .din(li_sum_on), .dout(li_sum_on_pad),
        .valid(li_sum_on_pad_v));"""

R["    wire [79:0] li_sum_off_pad = dec32_pad(li_sum_off);"] = """\
    wire [79:0] li_sum_off_pad;
    wire        li_sum_off_pad_v;
    dec_fmt #(.W(32), .D(10), .SIGNED(0)) u_ff_lioff (
        .clk(clk), .rst(reset), .din(li_sum_off), .dout(li_sum_off_pad),
        .valid(li_sum_off_pad_v));"""

R["    wire [79:0] li_n_on_pad    = dec32_pad(li_n_on);"] = """\
    wire [79:0] li_n_on_pad;
    wire        li_n_on_pad_v;
    dec_fmt #(.W(32), .D(10), .SIGNED(0)) u_ff_linon (
        .clk(clk), .rst(reset), .din(li_n_on), .dout(li_n_on_pad),
        .valid(li_n_on_pad_v));"""

R["    wire [79:0] li_n_off_pad   = dec32_pad(li_n_off);"] = """\
    wire [79:0] li_n_off_pad;
    wire        li_n_off_pad_v;
    dec_fmt #(.W(32), .D(10), .SIGNED(0)) u_ff_linoff (
        .clk(clk), .rst(reset), .din(li_n_off), .dout(li_n_off_pad),
        .valid(li_n_off_pad_v));"""


def main() -> int:
    src = SRC.read_text()
    lines = src.split("\n")
    out = []
    replaced = []
    missing = []

    for line in lines:
        if line in R:
            if line in replaced:
                print("ERROR: source line matched twice: %r" % line)
                return 1
            replaced.append(line)
            out.extend(R[line].split("\n"))
        else:
            out.append(line)

    # every target must have been found exactly once
    for key in R:
        if key not in replaced:
            missing.append(key)
    if missing:
        print("ERROR: %d target line(s) not found -- source changed?" % len(missing))
        for m in missing:
            print("   %r" % m)
        return 1

    text = "\n".join(out)

    # Force the pipelined configuration on every instance.
    #   SEQ=0 (combinational) measured 6004 LUT of formatting -> 13011 total,
    #   which still trips [DRC UTLZ-1] (limit 10400).
    #   SEQ=1, STEPS=8 costs ~1470 LUT and holds latency to ~10 cycles.
    # Verified bit-identical by tb_dec_fmt for STEPS in {1,4,8}.
    # 13 declaration sites, but the three dec5_pad(code_latch) users collapse
    # into ONE shared instance -> 11 dec_fmt instances in the output.
    N_INSTANCES = 11

    text, n_sub = re.subn(r"\.SIGNED\((\d)\)\)",
                          r".SIGNED(\1), .SEQ(1), .STEPS(2))", text)
    if n_sub != N_INSTANCES:
        print("ERROR: could not set SEQ/STEPS on all %d instances (got %d)"
              % (N_INSTANCES, n_sub))
        return 1
    print("mode     : SEQ=1 STEPS=2 (pipelined) on %d instance(s)" % n_sub)

    # ---- validations -------------------------------------------------------
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
        print("ERROR: control bytes in output")
        return 1

    n_inst = len(re.findall(r"\bdec_fmt\s*#", text))
    if n_inst != N_INSTANCES:
        print("ERROR: expected %d dec_fmt instances, found %d"
              % (N_INSTANCES, n_inst))
        return 1

    # No combinational formatter may remain as a *call*.  Strip comments first:
    # several explanatory comments mention dec32_pad(...) / dec5_pad(...) and
    # would otherwise trip this check.
    code = re.sub(r"//[^\n]*", "", text)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    for fn in ("dec32_pad", "dec5_pad", "signed_dec48", "enc_dec"):
        calls = re.findall(re.escape(fn) + r"\s*\(", code)
        if calls:
            print("ERROR: %s( still called %d time(s) in code" % (fn, len(calls)))
            return 1

    # Balance checks run on comment-stripped code: the source contains English
    # comments such as "end of cycle" / "end-of-row" which would otherwise
    # look like unmatched `end`.
    if len(re.findall(r"^\s*module\b", code, re.M)) != \
       len(re.findall(r"^\s*endmodule\b", code, re.M)):
        print("ERROR: module/endmodule mismatch")
        return 1
    nb = len(re.findall(r"\bbegin\b", code))
    ne = len(re.findall(r"\bend\b", code))
    if nb != ne:
        print("ERROR: begin/end mismatch (%d vs %d)" % (nb, ne))
        return 1
    if len(re.findall(r"\bcase\b|\bcasez\b|\bcasex\b", code)) != \
       len(re.findall(r"\bendcase\b", code)):
        print("ERROR: case/endcase mismatch")
        return 1
    if len(re.findall(r"\bfunction\b", code)) != \
       len(re.findall(r"\bendfunction\b", code)):
        print("ERROR: function/endfunction mismatch")
        return 1
    if len(re.findall(r"\btask\b", code)) != len(re.findall(r"\bendtask\b", code)):
        print("ERROR: task/endtask mismatch")
        return 1

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(text)
    print("replaced : %d declaration(s)" % len(replaced))
    print("instances: %d dec_fmt" % n_inst)
    print("checks   : no leftover calls, balanced module/begin/case, no ctrl bytes")
    print("input    : %s (%d lines)" % (SRC, src.count("\n")))
    print("output   : %s (%d lines)" % (DST, text.count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
