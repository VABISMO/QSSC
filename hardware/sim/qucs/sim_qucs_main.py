"""QSSC main control board - analog cross-engine validation (qucsator + ngspice).

Scope: the two analog circuits of the main board (hardware/pcb/qssc.kicad_sch,
HARDWARE_DESIGN.md section 6) as netlist ground truth:

  (1) ADF4351 loop filter ladder
        CP (U3.7) -> C31 220p -> GND
        CP        -> R67 1k   -> LF_MID -> C32 10n -> GND
        LF_MID    -> R68 2.2k -> LF_OUT (VCOF, U3.20) -> C33 220p -> GND
  (2) RF chain
        U3.12 (RF_OUT) -> C34 100p -> U5.1 (GVA-84+)
        U5.3 -> L2 100n (choke to +5V_RF = AC ground) -> C35 100p -> J3 SMA (50 Ohm)
        D3 ESD5V0 shunt at RF_AMP_OUT

Method mirrors the eco-board V3 suite (hardware/eco branch, sim/qucs/):
pre-registered checks (fixed below before the first run of this module),
two independent engines, results JSON, engine-difference ledger.

Tool: qucsator_rf (Qucs-S AppImage, no Docker) resolved via the QUCSATOR
environment variable; ngspice from PATH.

Pre-registration date: 2026-09-19, before the first run of this module.
"""

import json
import math
import os
import re
import shutil
import subprocess
import time

QUCSATOR = os.environ.get(
    "QUCSATOR",
    "/run/media/julian/ML2/Python/QNOS-2026/tools/squashfs-root/usr/bin/qucsator_rf",
)
NGSPICE = os.environ.get("NGSPICE", "ngspice")
HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "_build")

# ---------------------------------------------------------------------------
# Assumption ledger (each item stated; falsifier = change the assumption and
# re-run; assumptions are NOT silently extended after runs)
# ---------------------------------------------------------------------------
ASSUMPTIONS = {
    "a_values": (
        "Component values from the kicadsexpr netlist export of "
        "hardware/pcb/qssc.kicad_sch (126 components, 172 nets; parsed "
        "2026-09-19): C31=220p NP0, C32=10n, C33=220p NP0, R67=1k, R68=2.2k, "
        "C34=100p, C35=100p, L2=100n."
    ),
    "b_cap_ideal": (
        "Capacitors modelled ideal (no ESR/ESL). C31/C33 are NP0 per netlist "
        "notes; C32 X7R dielectric tolerance affects absolute BW, not the "
        "cross-engine comparison."
    ),
    "c_cp_ideal": (
        "Charge pump modelled as ideal current source (pulse/step); ADF4351 "
        "CP output impedance and switching transients not modelled."
    ),
    "d_kphi": "Kphi = Icp/(2*pi) = 2.19e-3/(2*pi) A/rad (section 6: CP 2.19 mA).",
    "e_kvco": (
        "Kvco = 40 MHz/V (ADF4351 datasheet-typical). Real Kvco varies with "
        "tuning voltage; this scales the derived loop bandwidth linearly."
    ),
    "f_n_divider": "N = f_out/f_pfd = 2.87 GHz / 25 MHz = 114.8.",
    "g_esd_cap": (
        "D3 ESD5V0 modelled as pure junction capacitance; the schematic "
        "carries no capacitance value, so 0.3 pF (low-cap ESD variant) and "
        "1 pF (generic 5 V ESD) are both simulated."
    ),
    "h_rail_ac_gnd": (
        "+5V_RF treated as AC ground (7x 100nF decoupling + FB1 600 ohm); "
        "L2 therefore modelled as a shunt inductor to ground in the RF decks."
    ),
    "i_amp_model": (
        "GVA-84+ modelled as a unilateral ideal amplifier: G=16 dB, "
        "Zin=Zout=50 ohm, S12=0 (section 6: 16 dB @ 3 GHz). NF and OIP3 not "
        "simulated."
    ),
    "j_engines": (
        "ngspice-47 as the independent engine; identical element values, no "
        "shared physics code path. Both SP models are behavioural "
        "idealisations, so SP cross-engine agreement validates the harness, "
        "not the amp model choice."
    ),
    "k_bleed": (
        "The ladder CP node carries a 10 Mohm shunt to ground in both "
        "engines (CP output impedance / leakage proxy). SPICE requires a DC "
        "path at every node for the operating point; qucsator AC does not, "
        "but the same shunt is added there to keep the netlists identical. "
        "It is negligible at all probe frequencies (Xc31(1 kHz) = 723k)."
    ),
    "l_ipulse_t2": (
        "qucsator Ipulse T2 is the ABSOLUTE pulse stop time, not the width "
        "(width = T2 - T1). Verified in the pilot run via the plateau "
        "charge: T1=10n/T2=20n produced a 1.88 mV plateau (~21.9 pC into "
        "10.44 nF total ladder capacitance = 10 ns of 2.19 mA), while the "
        "ngspice PULSE(20n) produced 4.32 mV (~43.8 pC = 20 ns). The "
        "qucsator pulse deck therefore uses T2=30n so both engines drive "
        "the same 43.8 pC."
    ),
    "m_amp_g_linear": (
        "The qucsator Amp device parameter G is a LINEAR voltage gain, not "
        "dB. Empirically verified in the pilot run: G=16 produced "
        "|S21| = 24.01 dB ~= 20*log10(16). 16 dB is therefore set as "
        "G = 10^(16/20) = 6.3096."
    ),
}

# ---------------------------------------------------------------------------
# Component values (netlist ground truth)
# ---------------------------------------------------------------------------
C31 = 220e-12
C32 = 10e-9
C33 = 220e-12
R67 = 1.0e3
R68 = 2.2e3
C34 = 100e-12
C35 = 100e-12
L2 = 100e-9
ICP = 2.19e-3          # A, section 6 charge pump current
KPHI = ICP / (2 * math.pi)
KVCO = 40e6            # Hz/V, assumption (e)
N_DIV = 2.87e9 / 25e6  # assumption (f)

# ---------------------------------------------------------------------------
# Pre-registered checks. Each check: id, claim, falsifier band, method.
# Bands are fixed before the first run of this module (2026-09-19).
# ---------------------------------------------------------------------------
CHECKS = [
    {
        "id": "AC-1",
        "claim": "Loop filter |Zt| agrees across engines within 2 % at "
                 "probe frequencies {1k, 10k, 50k, 100k, 1M, 10M} Hz.",
        "band": "max relative |Zt| difference <= 0.02",
        "method": "log-log interpolation of both |Zt(f)| sweeps at probes.",
    },
    {
        "id": "AC-2",
        "claim": "Derived open-loop unity-gain frequency falls inside the "
                 "section 6 ~50 kHz loop-bandwidth claim band.",
        "band": "30 kHz <= fc <= 80 kHz; outside -> documented DEVIATION "
                "(documentation-consistency finding, not a design fail)",
        "method": "|L(f)| = Kphi*|Zt(f)|*Kvco/(N*2*pi*f) from the measured "
                  "|Zt| curve; fc = crossing of |L| = 1 (log interpolation).",
    },
    {
        "id": "TR-1",
        "claim": "CP pulse response (20 ns, Icp) final decay agrees across "
                 "engines; peak value agrees within 10 mV.",
        "band": "|V_peak_qucs - V_peak_ngspice| <= 10 mV",
        "method": "transient 0..2 ms, single 2.19 mA / 20 ns pulse at 10 ns.",
    },
    {
        "id": "TR-2",
        "claim": "CP step response (Icp continuous) final value agrees "
                 "across engines within 10 mV; DC expectation "
                 "Icp*(R67+R68) = 7.008 V.",
        "band": "|V_final_qucs - V_final_ngspice| <= 10 mV and "
                "|V_final - 7.008 V| <= 10 mV (both engines)",
        "method": "transient 0..2 ms, 2.19 mA step at 10 ns; V(LF_OUT) tail "
                  "average over the last 100 us.",
        "amendment": "VOID after the pilot run. The 7.008 V DC expectation "
                     "has no valid DC path in this deck: C33 blocks the "
                     "ladder from LF_OUT, so the only DC path is the "
                     "10 Mohm bleed (assumption k), which puts the DC "
                     "asymptote at Icp*10 Mohm = 21.9 kV with a bleed "
                     "time constant far beyond the 2 ms window. The ~400 V "
                     "tails seen in both engines are the unconverged bleed "
                     "ramp, not ladder physics. The check as pre-registered "
                     "is a bad check (garbage-in) and is voided rather than "
                     "redesigned mid-run (eco F3 lesson: check the deck "
                     "before redesigning). The real board's DC path is the "
                     "ADF4351 CP output / VCO tuning-port input impedance, "
                     "which is intentionally not modelled here.",
    },
    {
        "id": "TR-3",
        "claim": "Step-response settling time (to within 1 mV of final) "
                 "agrees across engines within 10 %.",
        "band": "relative settle-time difference <= 0.10",
        "method": "first time at which |V(t) - V_final| <= 1 mV "
                  "permanently.",
        "amendment": "VOID after the pilot run. Settling against the TR-2 "
                     "'final value' inherits TR-2's flawed DC premise: the "
                     "tail is still ramping at 2 ms, so a 1 mV settle "
                     "criterion against that tail is a garbage-in pass.",
    },
    {
        "id": "SP-A-1",
        "claim": "Passive RF coupling chain (C34, L2, C35; no D3, no amp) "
                 "insertion loss <= 1.0 dB at 2.87 GHz.",
        "band": "IL <= 1.0 dB",
        "method": "SP 2.4..3.6 GHz, |S21| interpolated at 2.87 GHz.",
    },
    {
        "id": "SP-A-2",
        "claim": "Passive chain return loss at the J3 port <= -10 dB at "
                 "2.87 GHz.",
        "band": "S11 <= -10 dB",
        "method": "SP port 2 reflection with port 1 terminated (50 ohm).",
    },
    {
        "id": "SP-A-3",
        "claim": "Passive chain S21/S11 agree across engines within 0.5 dB "
                 "at probe frequencies {2.4, 2.87, 3.6} GHz.",
        "band": "max dB difference <= 0.5",
        "method": "log-magnitude interpolation from both SP sweeps.",
    },
    {
        "id": "SP-B-03",
        "claim": "Passive chain plus D3 at 0.3 pF junction cap keeps IL <= "
                 "1.5 dB at 2.87 GHz.",
        "band": "IL <= 1.5 dB",
        "method": "as SP-A with 0.3 pF shunt at RF_AMP_OUT.",
    },
    {
        "id": "SP-B-10",
        "claim": "Documentation guard: if a generic-ESD junction cap of "
                 "1 pF is assumed, chain IL must stay small; larger values "
                 "indicate the ESD diode materially loads the RF path.",
        "band": "IL <= 3 dB; IL > 3 dB -> FINDING 'verify actual ESD5V0 "
                "variant junction capacitance before fab'",
        "method": "as SP-A with 1 pF shunt at RF_AMP_OUT.",
    },
    {
        "id": "SP-C-1",
        "claim": "With the GVA-84+ modelled at 16 dB, chain |S21| lands in "
                 "[13, 17.5] dB at 2.87 GHz (amp gain minus up to 3 dB "
                 "chain loss).",
        "band": "13 dB <= |S21| <= 17.5 dB",
        "method": "SP with Amp device, D3 = 0.3 pF, interpolated at 2.87 GHz.",
    },
    {
        "id": "SP-C-2",
        "claim": "Amp-deck S-parameters agree across engines within 0.5 dB.",
        "band": "max dB difference <= 0.5",
        "method": "|S21| and |S11| at probe frequencies from both engines.",
    },
]


# ---------------------------------------------------------------------------
# Deck builders
# ---------------------------------------------------------------------------
LF_NODES = "n_cp n_mid n_out"


def deck_lf_ac():
    """qucsator AC deck: 1 A current drive into the ladder, log sweep."""
    return [
        "# QSSC main board - ADF4351 loop filter AC (ladder only)",
        "# CP modelled as ideal 1 A AC current source; Zt = V(LF_OUT)/1 A",
        "Iac:Icp n_cp gnd I=\"1\"",
        "R:Bleed n_cp gnd R=\"10e6\"",
        f"C:C31 n_cp gnd C=\"{C31:.6g}\"",
        f"R:R67 n_cp n_mid R=\"{R67:.6g}\"",
        f"C:C32 n_mid gnd C=\"{C32:.6g}\"",
        f"R:R68 n_mid n_out R=\"{R68:.6g}\"",
        f"C:C33 n_out gnd C=\"{C33:.6g}\"",
        ".AC:lf_ac Type=\"log\" Start=\"100\" Stop=\"1e8\" Points=\"201\"",
    ]


def ngspice_lf_ac():
    return [
        "* QSSC main board - loop filter AC",
        "i1 0 n_cp dc 0 ac 1",
        "rb n_cp 0 10meg",
        f"c31 n_cp 0 {C31:.6g}",
        f"r67 n_cp n_mid {R67:.6g}",
        f"c32 n_mid 0 {C32:.6g}",
        f"r68 n_mid n_out {R68:.6g}",
        f"c33 n_out 0 {C33:.6g}",
        ".control",
        "ac dec 200 100 100meg",
        "wrdata lf_ac.csv v(n_cp) v(n_mid) v(n_out)",
        "quit",
        ".endc",
        ".end",
    ]


def _ipulse(kind):
    """qucsator Ipulse line for the two transient variants.

    T2 is the ABSOLUTE pulse stop time (verified via plateau charge:
    T1=10n/T2=20n delivered 10 ns of current, not 20 ns), so the width
    is T2 - T1. 20 ns width -> T2 = 30n. The ngspice deck keeps the
    canonical PULSE(...) width of 20 ns, so both engines drive the
    same 43.8 pC into the ladder.
    """
    if kind == "pulse":
        return "Ipulse:Icp n_cp gnd I1=\"0\" I2=\"2.19m\" T1=\"10n\" " \
               "T2=\"30n\" Tr=\"1n\" Tf=\"1n\""
    # step: stop = 1 s -> stays high inside the 2 ms window
    return "Ipulse:Icp n_cp gnd I1=\"0\" I2=\"2.19m\" T1=\"10n\" " \
           "T2=\"1s\" Tr=\"1n\" Tf=\"1n\""


def deck_lf_tr(kind):
    lines = [
        f"# QSSC main board - loop filter transient ({kind})",
        _ipulse(kind),
        "R:Bleed n_cp gnd R=\"10e6\"",
        f"C:C31 n_cp gnd C=\"{C31:.6g}\"",
        f"R:R67 n_cp n_mid R=\"{R67:.6g}\"",
        f"C:C32 n_mid gnd C=\"{C32:.6g}\"",
        f"R:R68 n_mid n_out R=\"{R68:.6g}\"",
        f"C:C33 n_out gnd C=\"{C33:.6g}\"",
        ".TR:tr1 relaxTSR=\"yes\" Type=\"lin\" Start=\"0.0\" "
        "Stop=\"2.0m\" Points=\"40001\"",
    ]
    return lines


def ngspice_lf_tr(kind):
    if kind == "pulse":
        src = "pulse(0 2.19m 10n 1n 1n 20n 1)"
    else:
        src = "pulse(0 2.19m 10n 1n 1n 1.9m 1)"
    return [
        f"* QSSC main board - loop filter transient ({kind})",
        f"i1 0 n_cp {src}",
        "rb n_cp 0 10meg",
        f"c31 n_cp 0 {C31:.6g}",
        f"r67 n_cp n_mid {R67:.6g}",
        f"c32 n_mid 0 {C32:.6g}",
        f"r68 n_mid n_out {R68:.6g}",
        f"c33 n_out 0 {C33:.6g}",
        ".control",
        "tran 50n 2m",
        f"wrdata lf_tr_{kind}.csv v(n_cp) v(n_mid) v(n_out)",
        "quit",
        ".endc",
        ".end",
    ]


def deck_rf_sp(c_esd, with_amp):
    """qucsator SP deck of the RF chain (+ optional amp and ESD cap).

    Passive variants (SP-A/SP-B) model the output chain only: port 1 sits
    at RF_AMP_OUT (the amp output is a 50-ohm source by model (i)), the
    chain is L2 shunt + C35 series to J3. The amp variant (SP-C) adds the
    input coupling C34 and the unilateral Amp device.
    """
    lines = [
        f"# QSSC main board - RF chain SP (c_esd={c_esd:.3g} F, "
        f"amp={'yes' if with_amp else 'no'})",
        "Pac:p1 n_src gnd Num=\"1\" Z=\"50\" f=\"2.87e9\"",
    ]
    if with_amp:
        lines.append(f"C:C34 n_src n_amp C=\"{C34:.6g}\"")
        # Amp G is a LINEAR voltage gain (verified empirically: G=16 ->
        # 24.01 dB, i.e. 20*log10(16)); 16 dB -> G = 10^(16/20) = 6.3096
        lines.append("Amp:U5 n_amp n_out G=\"6.3096\" Z1=\"50\" Z2=\"50\"")
    else:
        # passive variant: port 1 sits directly at RF_AMP_OUT (the amp
        # output is a 50-ohm source by model (i))
        lines[1] = "Pac:p1 n_out gnd Num=\"1\" Z=\"50\" f=\"2.87e9\""
    if c_esd > 0:
        lines.append(f"C:D3 n_out gnd C=\"{c_esd:.6g}\"")
    lines.append(f"L:L2 n_out gnd L=\"{L2:.6g}\"")
    lines.append(f"C:C35 n_out n_j C=\"{C35:.6g}\"")
    lines.append("Pac:p2 n_j gnd Num=\"2\" Z=\"50\" f=\"2.87e9\"")
    lines.append(".SP:sp1 Type=\"lin\" Start=\"2.4e9\" "
                 "Stop=\"3.6e9\" Points=\"201\"")
    return lines


def ngspice_rf_sp(c_esd, with_amp, name):
    """ngspice equivalent: .ac with 50-ohm source/load, extract S11/S21.

    Source: 1 V through 50 ohm series -> incident a1 = 0.5 V, so
    S11 = 2*V(dut_in) - 1 and S21 = 2*V(n_load). Amp model: 50 ohm input,
    Thevenin output (VCVS + 50 ohm). The VCVS amplitude is 2x the wave
    gain because a Thevenin source delivers b2 = V_oc/2 into a matched
    load, while the qucsator Amp device delivers |S21| = G directly
    (verified in the pilot run: G=16 -> 15.94 linear). VCVS gain
    = 2*10^(16/20) makes the two models wave-equivalent at 16 dB.
    """
    gain = 2.0 * 10 ** (16.0 / 20.0)
    lines = [
        f"* QSSC main board - RF chain ({c_esd:.3g} F ESD, "
        f"amp={'yes' if with_amp else 'no'})",
        "v1 n_in gnd dc 0 ac 1",
        "rs n_in n_src 50",
    ]
    if with_amp:
        lines += [
            f"c34 n_src n_amp {C34:.6g}",
            f"rin n_amp 0 50",
            f"eamp n_int 0 n_amp 0 {gain:.6g}",
            f"ramp n_int n_out 50",
        ]
    else:
        lines += [
            # passive variant: the 50-ohm source drives RF_AMP_OUT directly
            f"rlink n_src n_out 1e-3",
        ]
    if c_esd > 0:
        lines.append(f"cesd n_out 0 {c_esd:.6g}")
    lines += [
        f"l2 n_out 0 {L2:.6g}",
        f"c35 n_out n_load {C35:.6g}",
        f"rl n_load 0 50",
        ".control",
        "ac lin 201 2.4g 3.6g",
        f"wrdata {name}.csv v(n_src) v(n_load)",
        "quit",
        ".endc",
        ".end",
    ]
    return lines


# ---------------------------------------------------------------------------
# qucsator .dat parser (complex-aware; adapted from the eco V3 reader)
# ---------------------------------------------------------------------------
_NUM = r"[+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?"
_COMPLEX = re.compile(rf"({_NUM})\s*([+-])j({_NUM})")


def _parse_value(text):
    text = text.strip()
    m = _COMPLEX.fullmatch(text)
    if m:
        val = complex(float(m.group(1)), float(m.group(3)))
        return -val if m.group(2) == "-" else val
    return float(text)


def read_dat(path):
    """-> ({vector: [values]}, indep_name)."""
    vectors, indep, current = {}, None, None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            m = re.fullmatch(r"<indep (\S+) (\d+)>", line)
            if m:
                indep, current = m.group(1), []
                vectors[m.group(1)] = current
                continue
            m = re.fullmatch(r"</indep>", line)
            if m:
                current = None
                continue
            m = re.fullmatch(r"<dep (\S+) (\S+)>", line)
            if m:
                current = []
                vectors[m.group(1)] = current
                continue
            if line == "</dep>":
                current = None
                continue
            if line.startswith("<") or not line:
                continue
            if current is not None:
                current.append(_parse_value(line))
    return vectors, indep


def read_wrdata(path, ncols_per_vec=2):
    """ngspice wrdata text: rows 'freq re im re im ...' (complex vars)."""
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 1:
                rows.append([float(p) for p in parts])
    return rows


# ---------------------------------------------------------------------------
# Engine runners
# ---------------------------------------------------------------------------
def run_qucs(name, lines):
    net_path = os.path.join(BUILD, name + ".net")
    dat_path = os.path.join(BUILD, name + ".dat")
    with open(net_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    t0 = time.time()
    proc = subprocess.run(
        [QUCSATOR, "-i", net_path, "-o", dat_path],
        capture_output=True, text=True, timeout=900,
    )
    dt = time.time() - t0
    if proc.returncode != 0 or not os.path.exists(dat_path):
        raise RuntimeError(
            f"qucsator failed for {name} (rc={proc.returncode}): "
            f"{proc.stderr.strip()[-400:]}"
        )
    vectors, indep = read_dat(dat_path)
    return {"vectors": vectors, "indep": indep, "runtime_s": dt,
            "stderr": proc.stderr.strip()[-200:]}


def run_ngspice(name, lines):
    cir_path = os.path.join(BUILD, name + ".cir")
    csv_path = os.path.join(BUILD, name + ".csv")
    with open(cir_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    t0 = time.time()
    proc = subprocess.run(
        [NGSPICE, "-b", cir_path], capture_output=True, text=True,
        timeout=900, cwd=BUILD,
    )
    dt = time.time() - t0
    if proc.returncode != 0 or not os.path.exists(csv_path):
        raise RuntimeError(
            f"ngspice failed for {name} (rc={proc.returncode}): "
            f"{proc.stderr.strip()[-400:]}"
        )
    rows = read_wrdata(csv_path)
    return {"rows": rows, "runtime_s": dt, "stderr": proc.stderr.strip()[-200:]}


# ---------------------------------------------------------------------------
# Interpolation helpers
# ---------------------------------------------------------------------------
def interp_abs(xs, ys, x):
    """Linear interpolation of |ys| over log10(xs)."""
    logs = [math.log10(v) for v in xs]
    target = math.log10(x)
    mags = [abs(v) for v in ys]
    for i in range(len(logs) - 1):
        if logs[i] <= target <= logs[i + 1]:
            frac = (target - logs[i]) / (logs[i + 1] - logs[i])
            return mags[i] * (1 - frac) + mags[i + 1] * frac
    return None


def db(x):
    return 20 * math.log10(abs(x))


def interp_db(xs, ys, x):
    m = interp_abs(xs, ys, x)
    return None if m is None else db(m)


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------
def qucs_zt(result):
    """-> (freqs, [|Zt|]) from the qucsator AC run (V(n_out) at 1 A drive)."""
    freqs = result["vectors"]["acfrequency"]
    vout = result["vectors"]["n_out.v"]
    return freqs, [abs(v) for v in vout]


def ngspice_zt(result):
    """wrdata: freq, re(n_cp), im(n_cp), re(n_mid), ..., re(n_out), im(n_out)."""
    rows = result["rows"]
    freqs = [r[0] for r in rows]
    vout = [complex(r[-2], r[-1]) for r in rows]
    return freqs, [abs(v) for v in vout]


def qucs_sp(result):
    """-> (freqs, s11, s21) from the qucsator SP run."""
    freqs = result["vectors"]["frequency"]
    return (freqs,
            result["vectors"].get("S[1,1]"),
            result["vectors"].get("S[2,1]"))


def ngspice_sp(result):
    """-> (freqs, s11, s21); S11 = 2*V(n_src) - 1, S21 = 2*V(n_load).

    wrdata repeats the frequency per variable, so the columns are
    (f, re(n_src), im(n_src), f, re(n_load), im(n_load)).
    """
    rows = result["rows"]
    freqs = [r[0] for r in rows]
    s11, s21 = [], []
    for r in rows:
        v_src = complex(r[1], r[2])
        v_load = complex(r[4], r[5])
        s11.append(2 * v_src - 1)
        s21.append(2 * v_load)
    return freqs, s11, s21


def qucs_tr(result):
    """-> (times, V(n_out)) from the qucsator TR run.

    TR vectors: indep 'time', node transient voltage 'n_out.Vt' (real).
    """
    times = result["vectors"]["time"]
    vout = result["vectors"]["n_out.Vt"]
    return times, [abs(v) for v in vout]


def ngspice_tr(result):
    """wrdata transient layout: (t, v(n_cp), t, v(n_mid), t, v(n_out)).

    The time is repeated per variable and real transient values get a
    single column (no re/im split, unlike AC).
    """
    rows = result["rows"]
    times = [r[0] for r in rows]
    vout = [r[5] for r in rows]
    return times, vout


def settle_time(times, vals, final, tol=1e-3):
    """Last time at which |V - final| > tol; settle = that instant."""
    last = None
    for t, v in zip(times, vals):
        if abs(v - final) > tol:
            last = t
    return last


def cross_engine_db(freqs_q, sq, freqs_n, sn, probes):
    out = {}
    for f in probes:
        vq = interp_db(freqs_q, sq, f)
        vn = interp_db(freqs_n, sn, f)
        if vq is not None and vn is not None:
            out[f] = abs(vq - vn)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if os.path.isdir(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)

    # ngspice prints a '*' banner; the version sits on the banner's
    # second line ("** ngspice-47 : Circuit level simulation program")
    ng_out = subprocess.run(
        [NGSPICE, "--version"], capture_output=True, text=True
    ).stdout.splitlines()
    ng_ver = next(
        (l.strip().strip("*").strip() for l in ng_out if "ngspice-" in l),
        ng_out[0].strip() if ng_out else "?",
    )
    versions = {
        "qucsator": subprocess.run(
            [QUCSATOR, "-v"], capture_output=True, text=True
        ).stdout.splitlines()[0].strip(),
        "ngspice": ng_ver,
    }

    results = {"versions": versions, "assumptions": ASSUMPTIONS,
               "checks": [], "runs": {}}

    # ---- LF AC ----
    q_ac = run_qucs("lf_ac", deck_lf_ac())
    n_ac = run_ngspice("lf_ac", ngspice_lf_ac())
    results["runs"]["lf_ac"] = {
        "qucsator_s": q_ac["runtime_s"], "ngspice_s": n_ac["runtime_s"]}

    fq, zq = qucs_zt(q_ac)
    fn, zn = ngspice_zt(n_ac)
    probes = [1e3, 1e4, 5e4, 1e5, 1e6, 1e7]
    diffs = {}
    for f in probes:
        vq, vn = interp_abs(fq, zq, f), interp_abs(fn, zn, f)
        if vq and vn:
            diffs[f] = abs(vq - vn) / vn
    results["checks"].append({
        "id": "AC-1", "metric": {f"{f:.0e}": d for f, d in diffs.items()},
        "pass": bool(diffs) and max(diffs.values()) <= 0.02,
        "claim": CHECKS[0]["claim"], "band": CHECKS[0]["band"],
    })

    # open-loop gain curve and unity-gain frequency
    def crossing(freqs, mags):
        for i in range(len(freqs) - 1):
            fq_ = KPHI * mags[i] * KVCO / (N_DIV * 2 * math.pi * freqs[i])
            fn_ = KPHI * mags[i + 1] * KVCO / (N_DIV * 2 * math.pi * freqs[i + 1])
            if fq_ >= 1 >= fn_:
                l0, l1 = math.log10(freqs[i]), math.log10(freqs[i + 1])
                frac = (fq_ - 1) / (fq_ - fn_) if fq_ != fn_ else 0
                return 10 ** (l0 + frac * (l1 - l0))
        return None

    fc_q, fc_n = crossing(fq, zq), crossing(fn, zn)
    fc = fc_q if fc_q is not None else fc_n
    in_band = fc is not None and 30e3 <= fc <= 80e3
    results["checks"].append({
        "id": "AC-2",
        "metric": {"fc_qucsator_hz": fc_q, "fc_ngspice_hz": fc_n,
                   "assumptions": "Kphi=Icp/2pi, Kvco=40 MHz/V, N=114.8"},
        "pass": in_band,
        "deviation": fc is not None and not in_band,
        "claim": CHECKS[1]["claim"], "band": CHECKS[1]["band"],
    })

    # ---- LF TR ----
    for kind in ("pulse", "step"):
        q_tr = run_qucs(f"lf_tr_{kind}", deck_lf_tr(kind))
        n_tr = run_ngspice(f"lf_tr_{kind}", ngspice_lf_tr(kind))
        results["runs"][f"lf_tr_{kind}"] = {
            "qucsator_s": q_tr["runtime_s"], "ngspice_s": n_tr["runtime_s"]}
        tq, vq = qucs_tr(q_tr)
        tn, vn = ngspice_tr(n_tr)
        if kind == "pulse":
            peak_q, peak_n = max(vq), max(vn)
            results["checks"].append({
                "id": "TR-1",
                "metric": {"v_peak_qucsator_v": peak_q,
                           "v_peak_ngspice_v": peak_n,
                           "delta_mv": abs(peak_q - peak_n) * 1e3},
                "pass": abs(peak_q - peak_n) <= 10e-3,
                "claim": CHECKS[2]["claim"], "band": CHECKS[2]["band"],
            })
        else:
            tail_q = sum(vq[-100:]) / 100
            tail_n = sum(vn[-100:]) / 100
            dc_expect = ICP * (R67 + R68)
            st_q = settle_time(tq, vq, tail_q)
            st_n = settle_time(tn, vn, tail_n)
            rel = (abs(st_q - st_n) / max(st_q, st_n)) if st_q and st_n else None
            # TR-2/TR-3 are VOID (pre-registration flaw, see amendment in
            # CHECKS): the metrics are still recorded for transparency but
            # carry no pass/fail verdict.
            results["checks"].append({
                "id": "TR-2",
                "void": True,
                "void_reason": CHECKS[3]["amendment"],
                "metric": {"v_final_qucsator_v": tail_q,
                           "v_final_ngspice_v": tail_n,
                           "dc_expectation_v": dc_expect,
                           "delta_engines_mv": abs(tail_q - tail_n) * 1e3,
                           "delta_dc_mv": abs((tail_q + tail_n) / 2
                                              - dc_expect) * 1e3},
                "claim": CHECKS[3]["claim"], "band": CHECKS[3]["band"],
            })
            results["checks"].append({
                "id": "TR-3",
                "void": True,
                "void_reason": CHECKS[4]["amendment"],
                "metric": {"settle_qucsator_s": st_q, "settle_ngspice_s": st_n,
                           "rel_diff": rel},
                "claim": CHECKS[4]["claim"], "band": CHECKS[4]["band"],
            })

    # ---- RF SP ----
    sp_cases = [
        ("rf_sp_a", 0.0, False, "SP-A", "SP-A-1/SP-A-2/SP-A-3",
         "Passive output chain (L2, C35; no D3, no amp): IL <= 1.0 dB at "
         "2.87 GHz, S11 <= -10 dB, cross-engine within 0.5 dB."),
        ("rf_sp_b03", 0.3e-12, False, "SP-B-03", "SP-B-03",
         "Passive chain plus D3 at 0.3 pF junction cap: IL <= 1.5 dB at "
         "2.87 GHz."),
        ("rf_sp_b10", 1.0e-12, False, "SP-B-10", "SP-B-10",
         "Documentation guard with a generic 1 pF ESD cap: IL must stay "
         "small; IL > 3 dB = FINDING (verify actual ESD5V0 variant "
         "junction capacitance before fab)."),
        ("rf_sp_c", 0.3e-12, True, "SP-C", "SP-C-1/SP-C-2",
         "Full chain with GVA-84+ at 16 dB and D3 = 0.3 pF: |S21| in "
         "[13, 17.5] dB at 2.87 GHz, cross-engine within 0.5 dB."),
    ]
    for name, c_esd, with_amp, cid, ref_ids, claim in sp_cases:
        q_sp = run_qucs(name, deck_rf_sp(c_esd, with_amp))
        n_sp = run_ngspice(name, ngspice_rf_sp(c_esd, with_amp, name))
        results["runs"][name] = {
            "qucsator_s": q_sp["runtime_s"], "ngspice_s": n_sp["runtime_s"]}
        fq_, s11q, s21q = qucs_sp(q_sp)
        fn_, s11n, s21n = ngspice_sp(n_sp)
        il_q = interp_db(fq_, s21q, 2.87e9)
        il_n = interp_db(fn_, s21n, 2.87e9)
        s11_q = interp_db(fq_, s11q, 2.87e9)
        s11_n = interp_db(fn_, s11n, 2.87e9)
        probes_sp = [2.4e9, 2.87e9, 3.6e9]
        d21 = cross_engine_db(fq_, s21q, fn_, s21n, probes_sp)
        d11 = cross_engine_db(fq_, s11q, fn_, s11n, probes_sp)
        worst = max(list(d21.values()) + list(d11.values()))
        entry = {
            "id": cid, "check_ids": ref_ids,
            "metric": {
                "s21_db_qucsator@2.87g": il_q, "s21_db_ngspice@2.87g": il_n,
                "s11_db_qucsator@2.87g": s11_q,
                "s11_db_ngspice@2.87g": s11_n,
                "worst_cross_engine_db": worst,
            },
            "claim": claim,
            "band": [c["band"] for c in CHECKS
                     if c["id"] in ref_ids.split("/")],
        }
        il = (il_q + il_n) / 2 if il_q is not None and il_n is not None else None
        if None in (il_q, il_n, s11_q, s11_n):
            entry["pass"] = False
            entry["metric_missing"] = True
            results["checks"].append(entry)
            continue
        if cid == "SP-A":
            entry["pass"] = il >= -1.0 and s11_q <= -10 and s11_n <= -10 \
                and worst <= 0.5
        elif cid == "SP-B-03":
            entry["pass"] = il >= -1.5
        elif cid == "SP-B-10":
            entry["pass"] = il >= -3.0
            entry["finding"] = il < -3.0
        else:
            entry["pass"] = (13 <= il_q <= 17.5 and 13 <= il_n <= 17.5
                             and worst <= 0.5)
        results["checks"].append(entry)

    out_path = os.path.join(HERE, "qucs_main_results.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    n_pass = sum(1 for c in results["checks"] if c.get("pass"))
    n_dev = sum(1 for c in results["checks"] if c.get("deviation"))
    n_find = sum(1 for c in results["checks"] if c.get("finding"))
    n_void = sum(1 for c in results["checks"] if c.get("void"))
    print(f"checks: {n_pass}/{len(results['checks'])} pass, "
          f"{n_dev} deviation, {n_find} finding, {n_void} void")
    print(f"results: {out_path}")
    for c in results["checks"]:
        flag = ("PASS" if c.get("pass")
                else "DEVIATION" if c.get("deviation")
                else "FINDING" if c.get("finding")
                else "VOID" if c.get("void") else "FAIL")
        print(f"  [{flag}] {c['id']}")


if __name__ == "__main__":
    main()