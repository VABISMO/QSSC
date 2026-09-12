#!/usr/bin/env python3
"""Comprehensive parameter-sweep benchmark for every QSSC circuit.

Sweeps all 9 dashboard algorithms (bell, shor, period, math, grover, qft,
qrand, qftnos, bb84) across the axes that change the physics:

  * profile       : NV, hBN-VB, SiC-VSi   (T2 1.8 ms / 2 us / 40 us)
  * temperature_k : 300, 77, 4            (cryo recovery of coherence)
  * noise_mode    : ideal, real, phenomenological
  * size / operand: per-algorithm (N, num_qubits, marked item, a*b, ...)

For every run it records:
  qubits, depth, signal_retention, top-state confidence (share of shots),
  a per-algorithm CORRECTNESS verdict, elapsed_ms, and whether the run used
the exact Aer density-matrix path or the analytic decoherence limit
(approximate).

The output is a table to stdout + a JSON file (benchmark_circuits.json) so
the report artifact can render it. This is the "decent tests of all circuits
that measure how well each does while changing parameters" the user asked
for -- it is not a pass/fail pytest (that lives in
test_circuit_physics_audit.py); it is a measured characterization.

Run:  .venv/bin/python tools/benchmark_circuits.py
"""
from __future__ import annotations

import json
import os
import sys
import time

# make the repo root importable when run as `python tools/benchmark_circuits.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qiskit import QuantumCircuit

from api.runner import run_circuit
from qssc.backend import QSSCBackend
from qssc.defects import get_profile, coherence_at_temperature

PROFILES = ["NV", "hBN-VB", "SiC-VSi"]
TEMPS = [300, 77, 4]
MODES = ["ideal", "real", "phenomenological"]
SHOTS = 2048


# --------------------------------------------------------------------------
# per-algorithm parameter sweeps + correctness scoring
# --------------------------------------------------------------------------
def _confidence(counts: dict) -> float:
    total = sum(counts.values()) if counts else 0
    if not total:
        return 0.0
    return max(counts.values()) / total


def sweep_bell():
    rows = []
    for n in (2, 4, 8, 16):
        for prof in PROFILES:
            for T in TEMPS:
                for mode in MODES:
                    req = {"algorithm": "bell", "num_qubits": n, "profile": prof,
                           "temperature_k": T, "noise_mode": mode, "shots": SHOTS,
                           "seed": 42}
                    t0 = time.perf_counter()
                    r = run_circuit(req)
                    dt = (time.perf_counter() - t0) * 1000
                    counts = r.get("counts", {})
                    # Bell/GHZ: ideal top states are 0...0 and 1...1 (equal).
                    top = max(counts, key=counts.get) if counts else ""
                    ideal_states = {"0" * n, "1" * n}
                    correct = top.replace(" ", "") in ideal_states
                    rows.append(_row("bell", prof, T, mode, r, dt, n,
                                     correct, _confidence(counts),
                                     detail=f"n={n}"))
    return rows


def sweep_shor():
    rows = []
    for n in (15,):  # MAX_FACTOR_N cap; 21 decoheres/hangs
        for prof in PROFILES:
            for T in TEMPS:
                for mode in MODES:
                    req = {"algorithm": "shor", "n": n, "profile": prof,
                           "temperature_k": T, "noise_mode": mode, "shots": SHOTS,
                           "seed": 42}
                    t0 = time.perf_counter()
                    r = run_circuit(req)
                    dt = (time.perf_counter() - t0) * 1000
                    factors = r.get("factors")
                    correct = bool(factors and factors[0] * factors[1] == n)
                    rows.append(_row("shor", prof, T, mode, r, dt,
                                     r.get("num_qubits", 0), correct,
                                     _confidence(r.get("counts", {})),
                                     detail=f"N={n} factors={factors} "
                                            f"attempts={r.get('attempts')}"))
    return rows


def sweep_period():
    rows = []
    for n in (15,):
        for prof in PROFILES:
            for T in TEMPS:
                for mode in MODES:
                    req = {"algorithm": "period", "n": n, "profile": prof,
                           "temperature_k": T, "noise_mode": mode, "shots": SHOTS,
                           "seed": 42}
                    t0 = time.perf_counter()
                    r = run_circuit(req)
                    dt = (time.perf_counter() - t0) * 1000
                    # period of a mod 15: a in {2,4,7,8,11,13}; r must divide
                    # the multiplicative order and yield factors via gcd.
                    r_period = r.get("period")
                    a = r.get("a")
                    ok_period = bool(r_period and r_period > 1
                                     and pow(a, r_period, n) == 1) if a else False
                    rows.append(_row("period", prof, T, mode, r, dt,
                                     r.get("num_qubits", 0), ok_period,
                                     _confidence(r.get("counts", {})),
                                     detail=f"N={n} a={a} r={r_period}"))
    return rows


def sweep_math():
    rows = []
    cases = [("add", 3, 5, 8, 4), ("mul", 3, 5, 15, 4), ("mul", 3, 4, 12, 4)]
    # NOTE: mul 7x6 b6 (24q) is deliberately omitted from the full sweep -- a
    # single ideal Aer statevector run is ~25 s, x27 profile/T/mode combos =
    # ~11 min. It is documented (ideal=42 conf 100%, real=garbage at 300 K NV
    # decoherence) and covered by test_circuit_histogram. Add it back for a
    # deep dive on a single (NV,300,ideal) cell if needed.
    for op, a, b, expect, bits in cases:
        for prof in PROFILES:
            for T in TEMPS:
                for mode in MODES:
                    req = {"algorithm": "math", "op": op, "a": a, "b": b,
                           "bits": bits, "profile": prof, "temperature_k": T,
                           "noise_mode": mode, "shots": SHOTS, "seed": 42}
                    t0 = time.perf_counter()
                    r = run_circuit(req)
                    dt = (time.perf_counter() - t0) * 1000
                    got = r.get("result")
                    correct = (got == expect)
                    rows.append(_row("math", prof, T, mode, r, dt,
                                     r.get("num_qubits", 0), correct,
                                     _confidence(r.get("counts", {})),
                                     detail=f"{op} {a}x{b if op!='add' else '+'+str(b)}"
                                            f"={expect} bits={bits} got={got}"))
    return rows


def sweep_grover():
    rows = []
    for nq in (3, 4):
        marked = 2 ** nq - 1
        for prof in PROFILES:
            for T in TEMPS:
                for mode in MODES:
                    req = {"algorithm": "grover", "n_items": nq,
                           "marked": marked, "profile": prof,
                           "temperature_k": T, "noise_mode": mode,
                           "shots": SHOTS, "seed": 42}
                    t0 = time.perf_counter()
                    r = run_circuit(req)
                    dt = (time.perf_counter() - t0) * 1000
                    correct = (r.get("found") == marked)
                    rows.append(_row("grover", prof, T, mode, r, dt,
                                     r.get("num_qubits", 0), correct,
                                     _confidence(r.get("counts", {})),
                                     detail=f"nq={nq} marked={marked} "
                                            f"found={r.get('found')}"))
    return rows


def sweep_qft():
    rows = []
    for n in (4, 8, 16, 64):
        for prof in ("NV",):  # QFT is profile-agnostic in correctness; NV only
            for T in TEMPS:
                for mode in ("ideal", "real"):
                    req = {"algorithm": "qft", "num_qubits": n, "x": 1,
                           "profile": prof, "temperature_k": T,
                           "noise_mode": mode, "shots": SHOTS, "seed": 42}
                    t0 = time.perf_counter()
                    r = run_circuit(req)
                    dt = (time.perf_counter() - t0) * 1000
                    # QFT of |x> -> ~uniform; correctness = the uniformity flag
                    correct = bool(r.get("uniform"))
                    rows.append(_row("qft", prof, T, mode, r, dt,
                                     r.get("num_qubits", 0), correct,
                                     _confidence(r.get("counts", {})),
                                     detail=f"n={n} chi2={r.get('uniformity_chi2')}"))
    return rows


def sweep_qrand():
    rows = []
    for n in (8, 32, 64):
        for prof in ("NV",):
            for T in (300, 4):
                for mode in ("ideal", "real"):
                    req = {"algorithm": "qrand", "num_qubits": n,
                           "num_bytes": 16, "profile": prof,
                           "temperature_k": T, "noise_mode": mode,
                           "shots": 8192, "seed": 42}
                    t0 = time.perf_counter()
                    r = run_circuit(req)
                    dt = (time.perf_counter() - t0) * 1000
                    ok = bool(r.get("entropy_hex")) and r.get("health", {}).get("passed")
                    rows.append(_row("qrand", prof, T, mode, r, dt,
                                     r.get("num_qubits", 0), bool(ok),
                                     1.0 - float(r.get("raw_bias_p1", 0.5)),
                                     detail=f"n={n} bias_p1={r.get('raw_bias_p1')} "
                                            f"health={r.get('health',{}).get('passed')}"))
    return rows


def sweep_qftnos():
    rows = []
    for n in (15,):
        for prof in PROFILES:
            for T in TEMPS:
                for mode in MODES:
                    req = {"algorithm": "qftnos", "n": n, "profile": prof,
                           "temperature_k": T, "noise_mode": mode, "shots": SHOTS,
                           "seed": 42}
                    t0 = time.perf_counter()
                    r = run_circuit(req)
                    dt = (time.perf_counter() - t0) * 1000
                    factors = r.get("factors")
                    correct = bool(factors and factors[0] * factors[1] == n)
                    rows.append(_row("qftnos", prof, T, mode, r, dt,
                                     r.get("num_qubits", 0), correct,
                                     _confidence(r.get("counts", {})),
                                     detail=f"N={n} factors={factors} "
                                            f"attempts={r.get('attempts')}"))
    return rows


def sweep_bb84():
    rows = []
    for eavesdrop in (False, True):
        for prof in PROFILES:
            for T in TEMPS:
                for mode in ("ideal", "real"):
                    req = {"algorithm": "bb84", "num_bits": 128,
                           "eavesdrop": eavesdrop, "profile": prof,
                           "temperature_k": T, "noise_mode": mode,
                           "shots": 1024, "seed": 42}
                    t0 = time.perf_counter()
                    r = run_circuit(req)
                    dt = (time.perf_counter() - t0) * 1000
                    qber = float(r.get("qber", 0.0))
                    # BB84 correctness: no-eavesdrop -> QBER below the 11%
                    # security threshold; eavesdrop -> above it (detected).
                    if eavesdrop:
                        correct = qber > 0.11
                    else:
                        correct = qber < 0.11
                    rows.append(_row("bb84", prof, T, mode, r, dt, 1, correct,
                                     1.0 - qber,
                                     detail=f"eve={eavesdrop} qber={qber:.3f} "
                                            f"key={r.get('key_bits','?')}"))
    return rows


def _row(algo, prof, T, mode, r, dt_ms, nq, correct, confidence, detail):
    asm = r.get("assessment", {}) or {}
    if "error" in r:
        return {"algo": algo, "profile": prof, "T": T, "mode": mode,
                "qubits": nq, "correct": False, "confidence": 0.0,
                "signal_retention": None, "approximate": None,
                "elapsed_ms": round(dt_ms, 1), "detail": f"ERROR: {r['error']}"}
    return {"algo": algo, "profile": prof, "T": T, "mode": mode,
            "qubits": nq, "correct": bool(correct),
            "confidence": round(confidence, 3),
            "signal_retention": round(float(asm.get("signal_retention", 0) or 0), 4),
            "approximate": r.get("approximate"),
            "elapsed_ms": round(dt_ms, 1), "detail": detail}


# --------------------------------------------------------------------------
# physics audit (dipole coupling + error-channel completeness)
# --------------------------------------------------------------------------
def physics_audit():
    """Introspect the backend to verify the physics is implemented as claimed."""
    audit = {"profiles": {}, "error_channels": {}, "coupling": {}}
    for name in PROFILES:
        prof = get_profile(name)
        b = QSSCBackend(prof, temperature_k=300.0, seed=42, noise_mode="real")
        # the register is ONE pixel's ~10-NV register, all-to-all dipole
        # coupled (complete graph K_n; 1/r**3 at ~3 nm). The Target was built
        # from these edges.
        edges = b._neighbor_pairs()
        n = b._n_qubits
        # every edge is within the one-pixel register (all sites share pixel_id)
        same_pixel = all(b.sites[i].pixel_id == b.sites[j].pixel_id
                        for i, j in edges)
        n_edges = len(edges)
        n_pairs = len(edges) // 2  # undirected count
        # frequencies are per-NV (detuned), not uniform
        freqs = [s.frequency for s in b.sites]
        freq_uniform = (max(freqs) - min(freqs)) < 1.0  # <1 Hz spread = uniform
        t1, t2 = coherence_at_temperature(prof, 300.0)
        audit["profiles"][name] = {
            "t2_room_s": prof.t2_room, "t2_at_T_s": t2,
            "depol_1q": prof.depol_1q, "depol_2q": prof.depol_2q,
            "readout_error": prof.readout_error,
            "readout_asymmetry": prof.readout_asymmetry,
            "freq_detuning_spread_hz": prof.frequency_detuning_spread_hz,
        }
        audit["coupling"][name] = {
            "topology": "within-pixel all-to-all dipole (1/r^3, ~10 NVs/pixel)",
            "register_qubits": n,
            "directed_edges": n_edges,
            "undirected_pairs": n_pairs,
            "is_complete_graph": n_edges == n * (n - 1),
            "all_edges_within_one_pixel": same_pixel,
            "per_nv_frequency_detuned": not freq_uniform,
        }
    # error channels present in the Aer noise model (build a tiny circuit)
    qc = QuantumCircuit(2, 2)
    qc.h(0); qc.cx(0, 1); qc.measure([0, 1], [0, 1])
    b = QSSCBackend("NV", temperature_k=300.0, seed=42, noise_mode="real")
    nm = b._build_real_noise_model(qc)
    quantum_errors = nm._local_quantum_errors if hasattr(nm, "_local_quantum_errors") else {}
    readout_errors = nm._local_readout_errors if hasattr(nm, "_local_readout_errors") else {}
    # NoiseModel stores errors as nested dicts {qubits: {gate: [errors]}}; the
    # exact nesting varies across qiskit versions, so flatten defensively.
    def _flatten_errors(d):
        out = []
        for v in d.values():
            if isinstance(v, dict):
                for vv in v.values():
                    if isinstance(vv, (list, tuple)):
                        out.extend(vv)
                    else:
                        out.append(vv)
            elif isinstance(v, (list, tuple)):
                out.extend(v)
            else:
                out.append(v)
        return out
    qerrs = _flatten_errors(quantum_errors)
    rerrs = _flatten_errors(readout_errors)
    # qiskit composes thermal_relaxation_error + depolarizing_error into ONE
    # QuantumError (Kraus), so the channel is no longer labelled "thermal" or
    # "depolarizing" by type. Detect it structurally instead: a noisy channel
    # has >1 Kraus operator (identity has 1), and the composition is built in
    # _build_real_noise_model from real T1/T2 + depol, so a non-unitary 1q and
    # 2q error present == both thermal AND depolarizing are wired in.
    def _kraus_count(e):
        try:
            return int(e.size)
        except Exception:
            try:
                return len(e.to_instruction().definition or [])
            except Exception:
                return 0
    has_quantum_1q = any(_kraus_count(e) > 1 for e in qerrs
                         if getattr(e, "num_qubits", 0) == 1)
    has_quantum_2q = any(_kraus_count(e) > 1 for e in qerrs
                         if getattr(e, "num_qubits", 0) == 2)
    has_readout = len(rerrs) > 0
    # asymmetric readout check: the ReadoutError matrix rows differ
    asym_found = False
    for e in rerrs:
        try:
            m = e.probabilities.reshape(2, 2)
            if abs(m[0, 1] - m[1, 0]) > 1e-9:
                asym_found = True
        except Exception:
            pass
    audit["error_channels"] = {
        # thermal + depolarizing are composed into one Kraus QuantumError;
        # both are present iff non-unitary 1q AND 2q channels are attached
        # (built from real T1/T2 + depol_1q/2q in _build_real_noise_model).
        "thermal_relaxation_T1_T2_composed_with_depol": has_quantum_1q and has_quantum_2q,
        "quantum_error_1q_nonunitary": has_quantum_1q,
        "quantum_error_2q_nonunitary": has_quantum_2q,
        "readout_error": has_readout,
        "readout_asymmetric": asym_found,
        "quantum_error_keys_sample": list(quantum_errors.keys())[:6],
        "readout_error_keys": list(readout_errors.keys()),
    }
    # qiskit BackendV2 compliance
    audit["qiskit"] = {
        "is_BackendV2": hasattr(b, "max_circuits") and hasattr(b, "target"),
        "has_target": b.target is not None,
        "has_qubit_properties": b.target.qubit_properties is not None,
        "num_qubits": b.target.num_qubits,
        "basis_gates": sorted(b.target.operation_names)[:20],
    }
    return audit


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    print("QSSC comprehensive circuit benchmark")
    print("=" * 72)
    all_rows = []
    sweeps = [sweep_bell, sweep_shor, sweep_period, sweep_math, sweep_grover,
              sweep_qft, sweep_qrand, sweep_qftnos, sweep_bb84]
    for sw in sweeps:
        name = sw.__name__.replace("sweep_", "")
        print(f"\n--- {name} ---")
        rows = sw()
        all_rows.extend(rows)
        for r in rows:
            flag = "OK " if r["correct"] else "XX "
            print(f"{flag} {r['algo']:7s} {r['profile']:8s} T={r['T']:>3}K "
                  f"{r['mode']:16s} q={r['qubits']:>3} ret={r['signal_retention']} "
                  f"conf={r['confidence']:.2f} {r['elapsed_ms']:>8.1f}ms  {r['detail']}")
    # summary
    print("\n" + "=" * 72)
    print("SUMMARY (correctness rate by algorithm x profile x temperature, REAL mode)")
    print("-" * 72)
    real = [r for r in all_rows if r["mode"] == "real"]
    for algo in sorted({r["algo"] for r in all_rows}):
        for prof in PROFILES:
            for T in TEMPS:
                sub = [r for r in real if r["algo"] == algo
                       and r["profile"] == prof and r["T"] == T]
                if not sub:
                    continue
                ok = sum(1 for r in sub if r["correct"])
                avg_ret = sum(r["signal_retention"] or 0 for r in sub) / len(sub)
                print(f"  {algo:7s} {prof:8s} T={T:>3}K : {ok}/{len(sub)} correct, "
                      f"avg_retention={avg_ret:.3f}")

    print("\n--- physics audit ---")
    audit = physics_audit()
    print(json.dumps(audit, indent=2, default=str))

    out = {"rows": all_rows, "audit": audit,
           "summary": {"total_runs": len(all_rows),
                       "ideal_correct": sum(1 for r in all_rows if r["mode"] == "ideal" and r["correct"]),
                       "real_correct": sum(1 for r in all_rows if r["mode"] == "real" and r["correct"])}}
    path = os.path.join(os.path.dirname(__file__), "..", "benchmark_circuits.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nwrote {len(all_rows)} rows + audit -> {path}")
    return out


if __name__ == "__main__":
    main()