#!/usr/bin/env python3
"""Rewrite READ_LOCKINP in command_parser.v from a free-running NCO + 64-entry
LUT to an EXACT-period-p counter + per-prime cos/sin ROM.

Why: a free-running NCO with phase_inc=round(2^32/p) and a power-of-2 LUT
produces a reference whose EXACT period is the LUT size N, not p (the index
sequence (k*phase_inc>>shift) mod N has period N/gcd, not p). So the cosine is
only approximately period-p and the LCM orthogonality proof (which requires an
exactly period-p reference) is invalid -- NCO spurs can make the cross-talk
WORSE than the +-1/(p*q) square-wave residual it was meant to fix.

The fix: a sample counter `lip_cnt` that wraps at exactly p (the prime period),
indexing a per-prime cos/sin ROM. The reference is EXACTLY period-p, so two
distinct-prime references are orthogonal over the LCM to within the 12-bit LUT
rounding (~0.02%, ~75x better than the square wave's +-1/(p*q)), robustly and
independent of LUT size. The host sends a 4-bit prime_idx (0..15) selecting one
of the 16 system primes [2,3,5,...,53] instead of a 32-bit phase_inc.

This script is idempotent only on the un-rewritten (NCO) source; run once.
"""
import math
import sys
from pathlib import Path

PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53]
SCALE = 2047
F = Path("/tmp/qssc-prime-cosine/fpga/rtl/command_parser.v")
src = F.read_text()


def lit(v: int) -> str:
    return f"-12'sd{-v}" if v < 0 else f"12'sd{v}"


# --- build the ROM initial block -------------------------------------------
bases = []
off = 0
for p in PRIMES:
    bases.append(off)
    off += p
TOTAL = off  # 381

rom_lines = []
rom_lines.append("    // prime period (samples) for channel index 0..15 = PRIME_SYMBOLS")
rom_lines.append("    " + "    ".join(f"prime_tab[{i:2d}]=6'd{p};" for i, p in enumerate(PRIMES)))
rom_lines.append("    // cumulative base offset of each prime's cos/sin block in the ROM")
rom_lines.append("    " + "    ".join(f"prime_base[{i:2d}]=9'd{bases[i]};" for i in range(len(PRIMES))))
for p, b in zip(PRIMES, bases):
    c = [round(SCALE * math.cos(2 * math.pi * i / p)) for i in range(p)]
    s = [round(SCALE * math.sin(2 * math.pi * i / p)) for i in range(p)]
    rom_lines.append(f"    // p={p} (entries {b}..{b + p - 1})")
    for name, vals in (("cos_rom", c), ("sin_rom", s)):
        chunks = []
        for i in range(0, len(vals), 8):
            chunks.append("  ".join(
                f"{name}[{b + i + j:3d}]={lit(vals[i + j])};" for j in range(min(8, len(vals) - i))))
        rom_lines.append("    " + "\n    ".join(chunks))
rom_body = "\n".join(rom_lines)


# --- Edit 1: declarations (NCO -> exact-period counter) --------------------
OLD_DECL = """    // ---- READ_LOCKINP cosine/sine IQ demodulator state --------------------
    // I/Q are 48-bit signed MAC accumulators: each ADC sample (16-bit unsigned,
    // 0..65535) is multiplied by the 12-bit signed NCO cos/sin (+-2047), giving
    // a 30-bit signed product; summed over up to 100000 samples -> < 2^44, so
    // 48 bits is safe. phase_acc is a 32-bit NCO accumulator; phase_inc =
    // round(2^32 / p) for the prime period p (in ADC samples) is host-supplied
    // so no RTL divide is needed. n counts samples taken.
    reg [5:0]          lip_site;        // detector channel 0..63
    reg [31:0]         lip_n_samples;   // total samples 1..100000
    reg [31:0]         lip_phase_inc;   // NCO phase increment per sample
    reg [31:0]         lip_phase_acc;   // NCO phase accumulator
    reg signed [47:0]  lip_I;           // in-phase  accumulator (Σ code·cos)
    reg signed [47:0]  lip_Q;           // quadrature accumulator (Σ code·sin)
    reg [31:0]         lip_n;           // samples taken
    reg [15:0]         lip_code;        // latched ADC code for the MAC
    reg [7:0]          lip_line [0:44]; // 45-byte IQ reply: <I 16> <Q 16> <n 10>"""

NEW_DECL = """    // ---- READ_LOCKINP cosine/sine IQ demodulator state --------------------
    // I/Q are 48-bit signed MAC accumulators: each ADC sample (16-bit unsigned,
    // 0..65535) is multiplied by the 12-bit signed cos/sin (+-2047), giving a
    // 30-bit signed product; summed over up to 100000 samples -> < 2^44, so 48
    // bits is safe. The reference is EXACTLY periodic with period p (one of the
    // 16 system primes): a sample counter lip_cnt wraps at p (lip_p) and indexes
    // a per-prime cos/sin ROM. Exact periodicity is what makes two distinct-prime
    // references orthogonal over the LCM (to 12-bit rounding, ~0.02%), NOT the
    // +-1/(p*q) of the old 2-sample square-wave boxcar. The host supplies a 4-bit
    // prime_idx (0..15) selecting PRIME_SYMBOLS[prime_idx]; no RTL divide. n
    // counts samples taken.
    reg [5:0]          lip_site;        // detector channel 0..63
    reg [31:0]         lip_n_samples;   // total samples 1..100000
    reg [3:0]          lip_prime_idx;   // prime channel index 0..15
    reg [5:0]          lip_p;           // prime period (samples) = prime_tab[idx]
    reg [5:0]          lip_cnt;         // sample-in-period counter 0..p-1 (wraps)
    reg signed [47:0]  lip_I;           // in-phase  accumulator (Σ code·cos)
    reg signed [47:0]  lip_Q;           // quadrature accumulator (Σ code·sin)
    reg [31:0]         lip_n;           // samples taken
    reg [15:0]         lip_code;        // latched ADC code for the MAC
    reg [7:0]          lip_line [0:44]; // 45-byte IQ reply: <I 16> <Q 16> <n 10>"""

assert OLD_DECL in src, "decl block not found"
src = src.replace(OLD_DECL, NEW_DECL)


# --- Edit 2: the 64-entry cos_rom -> per-prime cos/sin ROM + tables --------
OLD_ROM = """    // 64-entry full-circle cosine ROM for the READ_LOCKINP NCO. 12-bit signed
    // (range +-2047, ~1.0), sampled at 2*pi*k/64. Zero-mean over the full
    // circle (sum = 0) so a constant ADC code projects to ~0 (DC rejection) and
    // two distinct-prime cosine references are orthogonal to LUT-quantization
    // order (~1/64^2), not the +-1/(p*q) of a 2-sample square wave. sin is read
    // as cos shifted by +90 deg = index (idx + 48) mod 64. Indexed by the top
    // 6 bits of the 32-bit phase accumulator. Initialised inline (no external
    // .hex) so both iverilog and Vivado ROM inference are self-contained.
    reg signed [11:0] cos_rom [0:63];
    initial begin
        cos_rom[ 0] = 12'sd2047; cos_rom[ 1] = 12'sd2037; cos_rom[ 2] = 12'sd2008; cos_rom[ 3] = 12'sd1959;
        cos_rom[ 4] = 12'sd1891; cos_rom[ 5] = 12'sd1805; cos_rom[ 6] = 12'sd1702; cos_rom[ 7] = 12'sd1582;
        cos_rom[ 8] = 12'sd1447; cos_rom[ 9] = 12'sd1299; cos_rom[10] = 12'sd1137; cos_rom[11] = 12'sd965;
        cos_rom[12] = 12'sd783;  cos_rom[13] = 12'sd594;  cos_rom[14] = 12'sd399;  cos_rom[15] = 12'sd201;
        cos_rom[16] = 12'sd0;    cos_rom[17] = -12'sd201; cos_rom[18] = -12'sd399; cos_rom[19] = -12'sd594;
        cos_rom[20] = -12'sd783; cos_rom[21] = -12'sd965; cos_rom[22] = -12'sd1137;cos_rom[23] = -12'sd1299;
        cos_rom[24] = -12'sd1447;cos_rom[25] = -12'sd1582;cos_rom[26] = -12'sd1702;cos_rom[27] = -12'sd1805;
        cos_rom[28] = -12'sd1891;cos_rom[29] = -12'sd1959;cos_rom[30] = -12'sd2008;cos_rom[31] = -12'sd2037;
        cos_rom[32] = -12'sd2047;cos_rom[33] = -12'sd2037;cos_rom[34] = -12'sd2008;cos_rom[35] = -12'sd1959;
        cos_rom[36] = -12'sd1891;cos_rom[37] = -12'sd1805;cos_rom[38] = -12'sd1702;cos_rom[39] = -12'sd1582;
        cos_rom[40] = -12'sd1447;cos_rom[41] = -12'sd1299;cos_rom[42] = -12'sd1137;cos_rom[43] = -12'sd965;
        cos_rom[44] = -12'sd783; cos_rom[45] = -12'sd594; cos_rom[46] = -12'sd399; cos_rom[47] = -12'sd201;
        cos_rom[48] = 12'sd0;    cos_rom[49] = 12'sd201;  cos_rom[50] = 12'sd399;  cos_rom[51] = 12'sd594;
        cos_rom[52] = 12'sd783;  cos_rom[53] = 12'sd965;  cos_rom[54] = 12'sd1137; cos_rom[55] = 12'sd1299;
        cos_rom[56] = 12'sd1447; cos_rom[57] = 12'sd1582; cos_rom[58] = 12'sd1702; cos_rom[59] = 12'sd1805;
        cos_rom[60] = 12'sd1891; cos_rom[61] = 12'sd1959; cos_rom[62] = 12'sd2008; cos_rom[63] = 12'sd2037;
    end"""

NEW_ROM_HEADER = f"""    // Per-prime cos/sin ROM for the READ_LOCKINP IQ demodulator. 12-bit signed
    // (range +-2047, ~1.0). For each of the 16 system primes p, a contiguous
    // block of p entries holds round(2047*cos(2*pi*i/p)) (cos_rom) and
    // round(2047*sin(2*pi*i/p)) (sin_rom), i=0..p-1. The demod indexes the block
    // for the selected prime at base prime_base[lip_prime_idx] + lip_cnt, where
    // lip_cnt wraps 0..p-1 -> the reference is EXACTLY periodic with period p.
    // Exact periodicity gives LCM orthogonality to 12-bit rounding (~0.02%),
    // ~75x better than the +-1/(p*q) odd-prime square-wave residual. prime_tab
    // maps idx->p (the wrap value); prime_base maps idx->ROM offset. Inlined
    // (no external .hex) so iverilog and Vivado ROM inference are self-contained.
    reg [5:0]          prime_tab  [0:15];   // PRIME_SYMBOLS[idx] = period p
    reg [8:0]          prime_base [0:15];   // cumulative ROM offset per prime
    reg signed [11:0]  cos_rom    [0:{TOTAL - 1}];  // per-prime cos blocks
    reg signed [11:0]  sin_rom    [0:{TOTAL - 1}];  // per-prime sin blocks
    initial begin
{rom_body}
    end"""

assert OLD_ROM in src, "rom block not found"
src = src.replace(OLD_ROM, NEW_ROM_HEADER)


# --- Edit 3: async reset block defaults ------------------------------------
OLD_RST_A = """        lip_site      = 6'd0;
        lip_n_samples = 32'd0;
        lip_phase_inc = 32'd0;
        lip_phase_acc = 32'd0;
        lip_I         = 48'sd0;
        lip_Q         = 48'sd0;
        lip_n         = 32'd0;
        lip_code      = 16'd0;"""
NEW_RST_A = """        lip_site      = 6'd0;
        lip_n_samples = 32'd0;
        lip_prime_idx = 4'd0;
        lip_p         = 6'd0;
        lip_cnt       = 6'd0;
        lip_I         = 48'sd0;
        lip_Q         = 48'sd0;
        lip_n         = 32'd0;
        lip_code      = 16'd0;"""
assert OLD_RST_A in src, "async reset block not found"
src = src.replace(OLD_RST_A, NEW_RST_A)


# --- Edit 4: sync reset block defaults -------------------------------------
OLD_RST_S = """            lip_site       <= 6'd0;
            lip_n_samples  <= 32'd0;
            lip_phase_inc  <= 32'd0;
            lip_phase_acc  <= 32'd0;
            lip_I          <= 48'sd0;
            lip_Q          <= 48'sd0;
            lip_n          <= 32'd0;"""
NEW_RST_S = """            lip_site       <= 6'd0;
            lip_n_samples  <= 32'd0;
            lip_prime_idx  <= 4'd0;
            lip_p          <= 6'd0;
            lip_cnt        <= 6'd0;
            lip_I          <= 48'sd0;
            lip_Q          <= 48'sd0;
            lip_n          <= 32'd0;"""
assert OLD_RST_S in src, "sync reset block not found"
src = src.replace(OLD_RST_S, NEW_RST_S)


# --- Edit 5: CMD_LOCKINP dispatch ------------------------------------------
OLD_DISP = """                        CMD_LOCKINP : begin
                            // READ_LOCKINP <site> <n_samples> <phase_inc>:
                            // prime-period cosine/sine IQ lock-in. A 32-bit NCO
                            // advances phase_inc per ADC sample; phase_inc =
                            // round(2^32 / p) for the prime period p (in ADC
                            // samples) the Python qssc.prime_readout assigned to
                            // this channel, so one full NCO cycle = p samples and
                            // the cosine reference is zero-mean -> orthogonal to
                            // every other coprime-prime reference to LUT order
                            // (~1/64^2), NOT the +-1/(p*q) of the old 2-sample
                            // square-wave boxcar. The host supplies phase_inc so
                            // no RTL divide is needed. Bounds: site 0..63,
                            // n_samples 1..100000, phase_inc 1..2^31 (p >= 2).
                            // No MW gate -- pure detector demod.
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) >= N_DET ||
                                $signed(arg_vals[1]) < 32'd1 ||
                                $signed(arg_vals[1]) > 32'd100000 ||
                                $signed(arg_vals[2]) < 32'd1 ||
                                $signed(arg_vals[2]) > 32'd2147483647) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                lip_site      <= arg_vals[0][5:0];
                                lip_n_samples <= arg_vals[1];
                                lip_phase_inc <= arg_vals[2];
                                lip_phase_acc <= 32'd0;
                                lip_I         <= 48'sd0;
                                lip_Q         <= 48'sd0;
                                lip_n         <= 32'd0;
                                read_all_mode <= 1'b0;
                                str_idx       <= 7'd0;
                                fsm           <= S_LIP_INIT;
                            end
                        end"""

NEW_DISP = """                        CMD_LOCKINP : begin
                            // READ_LOCKINP <site> <n_samples> <prime_idx>:
                            // prime-period cosine/sine IQ lock-in. prime_idx
                            // (0..15) selects one of the 16 system primes p =
                            // PRIME_SYMBOLS[prime_idx] (2,3,5,...,53); the demod
                            // reference is a cosine/sine at EXACTLY period p
                            // (a sample counter wraps at p, indexing the per-prime
                            // ROM), so over the LCM two distinct-prime references
                            // are orthogonal to 12-bit rounding (~0.02%) -- NOT
                            // the +-1/(p*q) odd-prime residual of the old 2-sample
                            // square-wave boxcar. The host sends prime_idx (not a
                            // phase_inc) so no RTL divide and no NCO frequency
                            // detuning. Bounds: site 0..63, n_samples 1..100000,
                            // prime_idx 0..15. No MW gate -- pure detector demod.
                            if ($signed(arg_vals[0]) < 0 || $signed(arg_vals[0]) >= N_DET ||
                                $signed(arg_vals[1]) < 32'd1 ||
                                $signed(arg_vals[1]) > 32'd100000 ||
                                $signed(arg_vals[2]) < 32'd0 ||
                                $signed(arg_vals[2]) > 32'd15) begin
                                reply_ok <= 1'b0;            // ERR
                                fsm      <= S_REPLY_LOAD;
                            end else begin
                                lip_site      <= arg_vals[0][5:0];
                                lip_n_samples <= arg_vals[1];
                                lip_prime_idx <= arg_vals[2][3:0];
                                lip_p         <= prime_tab[arg_vals[2][3:0]];
                                lip_cnt       <= 6'd0;
                                lip_I         <= 48'sd0;
                                lip_Q         <= 48'sd0;
                                lip_n         <= 32'd0;
                                read_all_mode <= 1'b0;
                                str_idx       <= 7'd0;
                                fsm           <= S_LIP_INIT;
                            end
                        end"""

assert OLD_DISP in src, "dispatch block not found"
src = src.replace(OLD_DISP, NEW_DISP)


# --- Edit 6: S_LIP_INIT + S_LIP_MAC ----------------------------------------
OLD_FSM = """                // ---- READ_LOCKINP cosine/sine IQ demodulator ---------------
                // Zero the accumulators (args already latched in the dispatch).
                S_LIP_INIT : begin
                    lip_phase_acc <= 32'd0;
                    lip_I         <= 48'sd0;
                    lip_Q         <= 48'sd0;
                    lip_n         <= 32'd0;
                    seq_wdog      <= 32'd0;
                    fsm           <= S_LIP_READ;
                end"""

NEW_FSM = """                // ---- READ_LOCKINP cosine/sine IQ demodulator ---------------
                // Zero the accumulators + reset the period counter (prime_idx and
                // lip_p already latched in the dispatch).
                S_LIP_INIT : begin
                    lip_cnt       <= 6'd0;
                    lip_I         <= 48'sd0;
                    lip_Q         <= 48'sd0;
                    lip_n         <= 32'd0;
                    seq_wdog      <= 32'd0;
                    fsm           <= S_LIP_READ;
                end"""

assert OLD_FSM in src, "S_LIP_INIT block not found"
src = src.replace(OLD_FSM, NEW_FSM)


OLD_MAC = """                // Multiply-accumulate: I += code*cos(phase), Q += code*sin(phase);
                // advance the NCO; count the sample; loop or encode. The cos/sin
                // lookup and the code are read combinationally from the CURRENT
                // lip_code (latched in S_LIP_WAIT, held stable here) and the
                // CURRENT lip_phase_acc (incremented at end of cycle via <=), so
                // every one of the n_samples samples contributes exactly once.
                S_LIP_MAC : begin
                    lip_I      <= lip_I + ($signed({1'b0, lip_code})
                                          * $signed(cos_rom[lip_phase_acc[31:26]]));
                    lip_Q      <= lip_Q + ($signed({1'b0, lip_code})
                                          * $signed(cos_rom[lip_phase_acc[31:26] + 6'd48]));
                    lip_phase_acc <= lip_phase_acc + lip_phase_inc;
                    lip_n      <= lip_n + 32'd1;
                    if (lip_n + 32'd1 >= lip_n_samples)
                        fsm <= S_LIP_ENC;
                    else
                        fsm <= S_LIP_READ;
                end"""

NEW_MAC = """                // Multiply-accumulate against the per-prime cos/sin ROM. The ROM
                // index for the current sample is prime_base[lip_prime_idx] +
                // lip_cnt; lip_cnt wraps at lip_p so the reference is EXACTLY
                // periodic with period p. cos/sin and the code are read
                // combinationally from the CURRENT lip_code (latched in
                // S_LIP_WAIT, held stable here) and the CURRENT lip_cnt (advanced
                // at end of cycle via <=), so every one of the n_samples samples
                // contributes exactly once.
                S_LIP_MAC : begin
                    lip_I  <= lip_I + ($signed({1'b0, lip_code})
                                       * $signed(cos_rom[prime_base[lip_prime_idx] + lip_cnt]));
                    lip_Q  <= lip_Q + ($signed({1'b0, lip_code})
                                       * $signed(sin_rom[prime_base[lip_prime_idx] + lip_cnt]));
                    lip_cnt <= (lip_cnt == lip_p - 6'd1) ? 6'd0 : (lip_cnt + 6'd1);
                    lip_n   <= lip_n + 32'd1;
                    if (lip_n + 32'd1 >= lip_n_samples)
                        fsm <= S_LIP_ENC;
                    else
                        fsm <= S_LIP_READ;
                end"""

assert OLD_MAC in src, "S_LIP_MAC block not found"
src = src.replace(OLD_MAC, NEW_MAC)


# --- sanity: no NCO remnants -----------------------------------------------
for bad in ["lip_phase_inc", "lip_phase_acc", "phase_inc", "phase_acc",
            "cos_rom[31:26]", "lip_phase_acc[31:26]"]:
    if bad in src:
        print(f"WARNING: leftover token '{bad}' in source", file=sys.stderr)

F.write_text(src)
print(f"OK: rewrote {F} -- cos/sin ROM = {TOTAL} entries across 16 primes")