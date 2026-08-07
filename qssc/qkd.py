# qssc/qkd.py
"""Quantum key distribution (BB84) for the QSSC platform.

BB84 is the canonical prepare-and-measure QKD protocol and a genuinely
NISQ-friendly routine: it needs only single-qubit state preparation and
measurement, so it runs as real simultaneous operations on today's IBM NISQ
hardware.  This module simulates the protocol using the QSSC noisy backend as
the quantum channel — the chip's readout error and decoherence (T2) show up
directly as the quantum bit-error rate (QBER), so cooling the chip lowers the
QBER and an intercept-resend eavesdropper raises it above the ~11% BB84
security threshold.

Protocol simulated:

1. Alice picks a random bit ``a`` and a random basis ``bA`` (Z=0 or X=1) and
   prepares the corresponding qubit state (|0>/|1> in Z, |+>/|-> in X).
2. The qubit traverses the channel (modelled by one run of the QSSC backend
   per prepare/measure basis combination, so the noise is the real chip noise,
   not a hand-picked number).
3. Bob measures in a random basis ``bB``.
4. Sifting: only rounds where ``bA == bB`` are kept.
5. QBER = error fraction among the sifted bits.  Secure iff QBER < 11%.
6. An optional intercept-resend eavesdropper (Eve measures in a random basis
   and resends) adds ~25% QBER on the sifted key -> detected.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from qiskit import QuantumCircuit

# BB84 abort threshold: above this QBER the key is considered compromised.
BB84_QBER_THRESHOLD = 0.11


def _combo_p1(backend, alice_bit: int, alice_basis: int,
              bob_basis: int, shots: int) -> float:
    """Probability that Bob measures 1 for one (a, bA, bB) combination.

    Builds the 1-qubit prepare/measure circuit and runs it on the QSSC backend
    so the channel noise is the chip's real readout error + decoherence.
    """
    qc = QuantumCircuit(1, 1)
    if alice_bit:
        qc.x(0)
    if alice_basis:
        qc.h(0)        # encode in X basis
    if bob_basis:
        qc.h(0)        # measure in X basis
    qc.measure(0, 0)
    res = backend.run(qc, shots=shots).result()
    counts = res.get_counts()
    ones = counts.get("1", 0)
    return ones / max(1, sum(counts.values()))


def simulate_bb84(backend, num_bits: int = 128, eavesdrop: bool = False,
                  shots: int = 512, seed: int = 42) -> dict:
    """Run a BB84 QKD simulation over the QSSC backend (quantum channel).

    Returns a dict with the sifted Alice/Bob keys (hex), the sifted length,
    the QBER, a security flag, and whether an eavesdropper was detected.
    """
    num_bits = max(16, min(2048, int(num_bits)))
    rng = np.random.default_rng(seed)

    # Pre-compute the 8 prepare/measure channel probabilities once.
    p1 = {}
    for a in (0, 1):
        for bA in (0, 1):
            for bB in (0, 1):
                p1[(a, bA, bB)] = _combo_p1(backend, a, bA, bB, shots)

    alice_bits, bob_bits, alice_basis, bob_basis = [], [], [], []
    for _ in range(num_bits):
        a = int(rng.random() < 0.5)
        bA = int(rng.random() < 0.5)
        bB = int(rng.random() < 0.5)
        # Bob's noisy outcome from the measured channel distribution
        b = 1 if rng.random() < p1[(a, bA, bB)] else 0
        # Intercept-resend eavesdropper: Eve picks a random basis bE; when
        # bE != bA she disturbs the state, flipping the sifted bit 50% of
        # the time -> ~25% extra QBER on the sifted key.
        if eavesdrop:
            bE = int(rng.random() < 0.5)
            if bE != bA and rng.random() < 0.5:
                b ^= 1
        alice_bits.append(a)
        bob_bits.append(b)
        alice_basis.append(bA)
        bob_basis.append(bB)

    # Sift: keep rounds where bases match.
    sift_a, sift_b = [], []
    for a, b, bA, bB in zip(alice_bits, bob_bits, alice_basis, bob_basis):
        if bA == bB:
            sift_a.append(a)
            sift_b.append(b)
    sifted = len(sift_a)
    errors = sum(1 for a, b in zip(sift_a, sift_b) if a != b)
    qber = (errors / sifted) if sifted else 0.0
    secure = qber < BB84_QBER_THRESHOLD

    def _to_hex(bits):
        if not bits:
            return ""
        val = 0
        for x in bits:
            val = (val << 1) | (x & 1)
        pad = (8 - (len(bits) % 8)) % 8
        val <<= pad
        nbytes = (len(bits) + pad) // 8
        return val.to_bytes(nbytes, "big").hex()

    return {
        "protocol": "BB84",
        "num_bits": num_bits,
        "sifted_bits": sifted,
        "sifting_rate": round(sifted / num_bits, 4),
        "qber": round(qber, 4),
        "secure": bool(secure),
        "eavesdrop": bool(eavesdrop),
        "eve_detected": bool(qber >= BB84_QBER_THRESHOLD),
        "qber_threshold": BB84_QBER_THRESHOLD,
        "alice_key_hex": _to_hex(sift_a),
        "bob_key_hex": _to_hex(sift_b),
        "matches": sum(1 for a, b in zip(sift_a, sift_b) if a == b),
    }