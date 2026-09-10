#!/usr/bin/env python3
"""Temperature x algorithm table checks for NV and hBN.

On-chip 2q gate = SPIN (microwave/dipole) gate, so signal_retention is the spin
T2 + depolarization budget only (the optical HOM channel is descriptive and does
NOT gate the run). NV (T2 ~ 1.8 ms @ 300 K) computes at room temperature; hBN
(T2 ~ 2 us @ 300 K) is a sensor -- shallow circuits work at 300 K, deep ones do
not. Cooling extends spin T2 (more headroom for deep circuits).
"""
import os
import sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qssc import get_profile, coherence_at_temperature
from api.runner import run_circuit

TEMPS = [300.0, 200.0, 100.0, 77.0, 30.0, 10.0, 4.0, 1e-3]

# (label, algorithm, kwargs)
ALGOS = [
    ("Bell(2q)",     "bell",   {"num_qubits": 2}),
    ("QFT-3",        "qft",    {"num_qubits": 3, "x": 1}),
    ("Grover-3",     "grover", {"n_items": 3, "marked": 5}),
    ("Shor-15",      "shor",   {"n": 15, "a": 7}),
    ("Math 3x4",     "math",   {"op": "mul", "a": 3, "b": 4, "bits": 4}),
    ("QRNG-8",       "qrand",  {"num_qubits": 8, "num_bytes": 16,
                                "common_name": "tbl"}),
    ("BB84-128",     "bb84",   {"num_bits": 128, "eavesdrop": False}),
]


def _result_tag(algo, r):
    if "error" in r:
        return "ERR"
    a = r.get("assessment", {})
    if not a.get("within_coherence", True):
        return "decoh"
    if algo == "shor":
        f = r.get("factors")
        return "OK(%s)" % (",".join(map(str, f)) if f else "-")
    if algo == "grover":
        return "OK" if r.get("success") else "no"
    if algo == "math":
        return "OK(%s)" % r.get("result") if r.get("result") is not None else "-"
    if algo == "qft":
        return "OK" if r.get("uniform") else "biased"
    if algo == "qrand":
        return "OK(%dB)" % (len(r.get("entropy_hex", "")) // 2)
    if algo == "bb84":
        return "OK(qber%.2f)" % r.get("qber", 0)
    if algo == "bell":
        top = max(r["counts"], key=r["counts"].get) if r.get("counts") else "?"
        return "OK(%s)" % top
    return "OK"


def table(profile):
    prof = get_profile(profile)
    print(f"\n=== {profile}  (spin 2q gate, on-chip compute) ===")
    hdr = "T(K)" + "".join("%-13s" % a for a, _, _ in ALGOS)
    print(hdr)
    print("-" * len(hdr))
    for T in TEMPS:
        t1, t2 = coherence_at_temperature(prof, T)
        row = "%-6s" % ("%g" % T if T >= 1 else "%.0fmK" % (T * 1000))
        for label, algo, kw in ALGOS:
            payload = {"algorithm": algo, "profile": profile,
                       "temperature_k": T, "seed": 42,
                       "noise_mode": "real", "shots": 1024}
            payload.update(kw)
            try:
                r = run_circuit(payload)
                a = r.get("assessment", {})
                ret = a.get("signal_retention", 0.0)
                tag = _result_tag(algo, r)
                cell = "%s r%.2f" % (tag, ret)
            except Exception as e:  # noqa: BLE001
                cell = "ERR:%s" % type(e).__name__
            row += "%-13s" % cell
        print(row)
    print("\nLegend: tag = result verdict; r = signal_retention (spin T2 x depol).")
    print("T2_spin @300K: NV=1.8ms  hBN=2us. 'decoh' = exceeds spin coherence.")


if __name__ == "__main__":
    np.seterr(all="ignore")
    for prof in ("NV", "hBN-VB"):
        table(prof)