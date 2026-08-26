# api/runner.py
"""Pure circuit-execution functions for the QSSC API.

These run a demo algorithm or a user-supplied circuit on a :class:`QSSCBackend`
and return a JSON-serialisable result dict. They hold no FastAPI / request
state, so they can be called both directly from the endpoints (synchronous
fallback) **and** from an RQ worker process (asynchronous path).
"""
from __future__ import annotations

import math
import time
from fractions import Fraction
from math import gcd

import numpy as np

from qssc import (
    auto_align, QSSCBackend, SimulatedFPGA,
    create_period_finding_circuit, create_shor_circuit, create_qft_nos_circuit,
    create_math_circuit, grover_search, postprocess_period,
    postprocess_period_swapless, select_coprime, is_prime,
    build_user_circuit, UserCircuitError,
)
from qssc.crypto import (
    derive_keypair, von_neumann_extract, bits_to_bytes,
)
from qssc.qkd import simulate_bb84


def _backend(profile: str, temperature_k: float, seed: int, noise_mode: str,
             grid: int = 8) -> QSSCBackend:
    sites = auto_align(profile=profile, grid=grid, temperature_k=temperature_k,
                       rng=np.random.default_rng(seed))
    # Attach a SimulatedFPGA so the on-chip readout path fires per run -- the
    # CQD emitter + graphene detector + lock-in model produces a displayed
    # bitstring, per-site read states and a photocurrent mean that the
    # frontend's Execution component physics card reflects the circuit that
    # actually ran (not a fixed snapshot).
    fpga = SimulatedFPGA(profile=profile, temperature_k=temperature_k, seed=seed)
    return QSSCBackend(profile=profile, sites=sites, seed=seed,
                       temperature_k=temperature_k, noise_mode=noise_mode,
                       hardware=fpga)


def _circuit_meta(qc) -> dict:
    return {"num_qubits": qc.num_qubits, "num_clbits": qc.num_clbits,
            "depth": qc.depth()}


def run_circuit(req: dict) -> dict:
    """Execute a demo algorithm. ``req`` mirrors :class:`RunRequest`."""
    profile = req.get("profile", "NV")
    temperature_k = float(req.get("temperature_k", 300.0))
    seed = int(req.get("seed", 42))
    shots = int(req.get("shots", 2048))
    noise_mode = req.get("noise_mode", "real")
    algo = str(req.get("algorithm", "shor")).lower()
    b = _backend(profile, temperature_k, seed, noise_mode)
    result = {"algorithm": algo, "profile": profile,
              "temperature_k": temperature_k, "shots": shots,
              "noise_mode": noise_mode}
    t0 = time.perf_counter()
    res = None

    if algo == "bell":
        from qiskit import QuantumCircuit
        n = max(2, int(req.get("num_qubits", 2)))
        qc = QuantumCircuit(n, n)
        qc.h(0)
        for i in range(n - 1):
            qc.cx(i, i + 1)
        qc.measure(range(n), range(n))
        res = b.run(qc, shots=shots).result()
        result["counts"] = res.get_counts()
        result.update(_circuit_meta(qc))

    elif algo == "shor":
        n = int(req.get("n", 15))
        if n < 2 or is_prime(n):
            return {"error": "n must be a composite integer > 1"}
        rng = np.random.default_rng(seed)
        chosen_a = req.get("a")
        a = r = factors = qc = counts = None
        attempt = 0
        for attempt in range(8):
            a = (chosen_a if (chosen_a is not None and attempt == 0)
                 else select_coprime(n, rng))
            qc = create_shor_circuit(n, a=a, rng=rng)[0]
            res = b.run(qc, shots=shots).result()
            counts = res.get_counts()
            r, factors = postprocess_period(counts, qc.num_clbits, a, n)
            if factors[0] and factors[0] * factors[1] == n:
                break
            chosen_a = None
        ok = bool(factors[0]) and factors[0] * factors[1] == n
        result.update({"n": n, "a": a, "counts": counts,
                       "period": r,
                       "factors": list(factors) if factors[0] else None,
                       "success": ok, "attempts": attempt + 1})
        result.update(_circuit_meta(qc))

    elif algo == "period":
        n = int(req.get("n", 15))
        rng = np.random.default_rng(seed)
        a = req.get("a") or select_coprime(n, rng)
        if gcd(a, n) != 1:
            a = select_coprime(n, rng)
        qc = create_period_finding_circuit(n, a)
        res = b.run(qc, shots=shots).result()
        counts = res.get_counts()
        # recover the period by iterating the top phase peaks (not just the
        # single top state): the k=0 peak (phase 0 -> denominator 1) ties with
        # the k/r peaks, so a naive max-counts lookup would wrongly report
        # period 1. postprocess_period skips trivial / odd candidates.
        r, _ = postprocess_period(counts, qc.num_clbits, a, n)
        bs = max(counts, key=counts.get)
        phase = int(bs.replace(" ", ""), 2) / 2 ** qc.num_clbits
        frac = Fraction(phase).limit_denominator(n - 1)
        result.update({"n": n, "a": a, "counts": counts,
                       "top_state": bs, "phase": round(phase, 6),
                       "fraction": str(frac), "period": r})
        result.update(_circuit_meta(qc))

    elif algo == "math":
        op = req.get("op", "add")
        num1 = float(req.get("a") if req.get("a") is not None
                     else req.get("num1", 2.0))
        num2 = float(req.get("b") if req.get("b") is not None
                     else req.get("num2"))
        qc = create_math_circuit(op, num1, num2, bits=req.get("bits"))
        res = b.run(qc, shots=shots).result()
        counts = res.get_counts()
        bs = max(counts, key=counts.get)
        out = int(bs, 2)
        if op in ("sqrt", "log"):
            out = (out / 2 ** qc.num_clbits) * num1
        result.update({"op": op, "a": num1, "b": num2,
                       "counts": counts, "result": out})
        result.update(_circuit_meta(qc))

    elif algo == "grover":
        from qiskit import QuantumCircuit, QuantumRegister
        nq = max(2, min(8, int(req.get("n_items") or 3)))
        marked = int(req.get("marked") or 0) % (2 ** nq)
        qr_o = QuantumRegister(nq, "o")
        oracle = QuantumCircuit(qr_o)
        for i in range(nq):
            if not ((marked >> i) & 1):
                oracle.x(qr_o[i])
        oracle.h(qr_o[-1])
        oracle.mcx(list(range(nq - 1)), qr_o[-1])
        oracle.h(qr_o[-1])
        for i in range(nq):
            if not ((marked >> i) & 1):
                oracle.x(qr_o[i])
        iters = max(1, int(round(math.pi / 4 * math.sqrt(2 ** nq))))
        qc = grover_search(nq, oracle, iterations=iters)
        res = b.run(qc, shots=shots).result()
        counts = res.get_counts()
        bs = max(counts, key=counts.get)
        found = int(bs, 2) if qc.num_clbits else 0
        result.update({"n_items": nq, "marked": marked, "counts": counts,
                       "found": found, "success": found == marked,
                       "iterations": iters})
        result.update(_circuit_meta(qc))

    elif algo == "qft":
        from qiskit import QuantumCircuit
        n = max(2, min(8, int(req.get("num_qubits", 4))))
        x = int(req.get("x", 1)) % (2 ** n)
        qc = QuantumCircuit(n, n)
        for i in range(n):
            if (x >> i) & 1:
                qc.x(i)
        # QFT of a computational-basis state |x> is an equal-amplitude
        # superposition (with a phase gradient) -> measurement is ~uniform
        # across all 2^n basis states. That uniformity is the QFT signature.
        try:
            from qiskit.circuit.library import QFTGate
            gate = QFTGate(n)
        except Exception:  # noqa: BLE001 - fallback for older qiskit
            from qiskit.circuit.library import QFT
            gate = QFT(n).to_gate()
        qc.append(gate, range(n))
        qc.measure(range(n), range(n))
        res = b.run(qc, shots=shots).result()
        counts = res.get_counts()
        states = 2 ** n
        expected = shots / states
        observed = list(counts.values())
        chi2_norm = (sum((c - expected) ** 2 / expected
                         for c in observed) / max(1, states))
        result.update({"n": n, "x": x, "counts": counts,
                       "basis_states": states,
                       "expected_per_state": round(expected, 2),
                       "uniformity_chi2": round(chi2_norm, 3),
                       "uniform": bool(chi2_norm < 2.0),
                       "qft": "forward"})
        result.update(_circuit_meta(qc))

    elif algo == "qrand":
        # Quantum random-number / cryptographic key generation. Prepare n
        # qubits in |+>, measure many times, and treat the measurement
        # outcomes as a (biased, because of readout error / decoherence at the
        # chosen temperature) random bitstream. A von Neumann extractor
        # removes the bias, the uniform bits seed an Ed25519 keypair + a
        # self-signed X.509 device certificate. Cooling the chip -> less
        # bias -> higher extraction rate.
        from qiskit import QuantumCircuit
        n = max(2, min(16, int(req.get("num_qubits", 8))))
        num_bytes = max(16, min(64, int(req.get("num_bytes", 32))))
        cn = str(req.get("common_name") or "QSSC-quantum-device")
        need_bits = num_bytes * 8
        qc = QuantumCircuit(n, n)
        qc.h(range(n))
        qc.measure(range(n), range(n))
        # plenty of shots so the VN extractor has raw bits even when biased
        raw_shots = max(int(shots), 8192)
        res = b.run(qc, shots=raw_shots).result()
        counts = res.get_counts()
        # expand the measured outcomes into a flat list and shuffle so that
        # adjacent pairs are ~independent samples (the backend returns a
        # histogram, not the per-shot sequence; on hardware the raw
        # per-shot sequence is used directly).
        rng = np.random.default_rng(seed)
        flat = []
        for k, v in counts.items():
            s = k.replace(" ", "")
            flat.extend([s] * int(v))
        rng.shuffle(flat)
        raw: list[int] = []
        for s in flat:
            for c in s:
                raw.append(1 if c == "1" else 0)
        bits, vstats = von_neumann_extract(raw, need_bits)
        p1 = (sum(raw) / len(raw)) if raw else 0.0
        if not vstats["sufficient"]:
            return {"error": "insufficient quantum entropy from this run "
                             "(increase shots or cool the chip to reduce "
                             "readout bias)"}
        entropy = bits_to_bytes(bits[:need_bits])
        # pad to >=52 bytes so derive_keypair has a serial-number source
        seed_block = entropy + bytes(entropy.hex().encode()[:40])
        key = derive_keypair(seed_block, common_name=cn, days=365)
        result.update({
            "n": n, "num_bytes": num_bytes, "counts": counts,
            "entropy_hex": entropy.hex(),
            "raw_bias_p1": round(p1, 4),
            "extraction": vstats,
            "symmetric_key_hex": key["symmetric_key_hex"],
            "public_key_pem": key["public_key_pem"],
            "private_key_pem": key["private_key_pem"],
            "certificate_pem": key["certificate_pem"],
            "common_name": key["common_name"],
            "serial": key["serial"],
            "key_algorithm": key["algorithm"],
        })
        result.update(_circuit_meta(qc))

    elif algo == "qftnos":
        # QFT-NOS: lightweight QFT-based period finding (reduced counting
        # register + swapless inverse QFT + precomputed controlled mod-mult),
        # then direct classical factoring from the period. Far fewer gates /
        # qubits than full Shor. We also report a gate-count comparison against
        # the full Shor circuit for the same (n, a).
        n = int(req.get("n", 15))
        if n < 2 or is_prime(n):
            return {"error": "n must be a composite integer > 1"}
        rng = np.random.default_rng(seed)
        chosen_a = req.get("a")
        a = r = factors = qc = counts = None
        attempt = 0
        for attempt in range(8):
            a = (chosen_a if (chosen_a is not None and attempt == 0)
                 else select_coprime(n, rng))
            qc, a = create_qft_nos_circuit(n, a=a, rng=rng)
            res = b.run(qc, shots=shots).result()
            counts = res.get_counts()
            r, factors = postprocess_period_swapless(counts, qc.num_clbits, a, n)
            if factors[0] and factors[0] * factors[1] == n:
                break
            chosen_a = None
        ok = bool(factors[0]) and factors[0] * factors[1] == n
        comparison = None
        try:
            full_qc, _ = create_shor_circuit(n, a=a)
            comparison = {
                "num_qubits": full_qc.num_qubits, "depth": full_qc.depth(),
                "size": full_qc.size(), "ops": dict(full_qc.count_ops()),
            }
        except Exception:  # noqa: BLE001
            comparison = None
        result.update({"n": n, "a": a, "counts": counts, "period": r,
                       "factors": list(factors) if factors[0] else None,
                       "success": ok, "attempts": attempt + 1,
                       "counting_qubits": qc.num_clbits,
                       "shor_comparison": comparison})
        result.update(_circuit_meta(qc))

    elif algo == "bb84":
        # BB84 quantum key distribution. Single-qubit prepare-and-measure over
        # the QSSC backend (the chip's readout/decoherence is the channel
        # noise). Optional intercept-resend eavesdropper raises the QBER above
        # the 11% security threshold.
        from types import SimpleNamespace
        from qiskit import QuantumCircuit as _QC
        num_bits = int(req.get("num_bits", 128))
        eavesdrop = bool(req.get("eavesdrop", False))
        info = simulate_bb84(b, num_bits=num_bits, eavesdrop=eavesdrop,
                             shots=max(int(shots), 256), seed=seed)
        result.update(info)
        result.update({"num_qubits": 1, "num_clbits": 1, "depth": 1})
        # bb84 never assigns `res` (it runs many 1-qubit combos inside
        # simulate_bb84); build an honest assessment of the 1-qubit channel
        # circuit so the dashboard verdict is not empty/NaN. A single H+measure
        # is the deepest BB84 op and always fits the T2 window.
        _chan = _QC(1, 1); _chan.h(0); _chan.measure(0, 0)
        res = SimpleNamespace(assessment=b.assess_circuit(_chan),
                              approximate=False)

    else:
        return {"error": f"unknown algorithm '{algo}'"}

    result["assessment"] = getattr(res, "assessment", {})
    result["approximate"] = getattr(res, "approximate", False)
    if getattr(res, "optical", None) is not None:
        result["optical"] = res.optical
    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return result


def run_custom(req: dict) -> dict:
    """Execute a user-supplied Qiskit circuit (QASM2 or Python snippet)."""
    t0 = time.perf_counter()
    try:
        qc = build_user_circuit(req.get("code", ""),
                                max_qubits=int(req.get("max_qubits", 12)))
    except UserCircuitError as exc:
        return {"error": str(exc)}
    profile = req.get("profile", "NV")
    b = _backend(profile, float(req.get("temperature_k", 300.0)),
                 int(req.get("seed", 42)), req.get("noise_mode", "real"))
    try:
        res = b.run(qc, shots=int(req.get("shots", 2048))).result()
        counts = res.get_counts()
    except Exception as exc:  # noqa: BLE001 - surface runtime errors to the user
        return {"error": f"run failed: {exc}"}
    return {
        "algorithm": "custom", "profile": profile,
        "temperature_k": float(req.get("temperature_k", 300.0)),
        "shots": int(req.get("shots", 2048)),
        "noise_mode": req.get("noise_mode", "real"),
        "counts": counts,
        "num_qubits": qc.num_qubits, "num_clbits": qc.num_clbits,
        "depth": qc.depth(), "ops": qc.count_ops(),
        "assessment": getattr(res, "assessment", {}),
        "approximate": getattr(res, "approximate", False),
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        **({"optical": res.optical} if getattr(res, "optical", None) is not None else {}),
    }