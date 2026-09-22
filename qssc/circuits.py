# qssc/circuits.py
"""Quantum algorithm circuits for the QSSC backend.

Pure-construction helpers that build Qiskit circuits for the algorithms exposed
by the CLI and the REST API: period finding (the core of Shor), full Shor
factoring, modular exponentiation, and a small arithmetic toolbox
(add / subtract / multiply / square root / log) built from Qiskit circuit
library primitives.
"""
from __future__ import annotations

import math
from fractions import Fraction
from math import gcd
from typing import Optional

import numpy as np
from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
from qiskit.circuit.library import (
    QFT, DraperQFTAdder, RGQFTMultiplier,
    PhaseEstimation, GroverOperator,
)
from qiskit.circuit.library.standard_gates import RYGate
from qiskit.circuit.library import UnitaryGate

# A run whose assessed ``signal_retention`` (coherence x depolarization
# survival, exposed by QSSCBackend.assess_circuit) falls below this is
# noise-dominated: its measured distribution is indistinguishable from the
# uniform floor, so any "period" lifted out of it is a noise artifact (audit
# issue #8). The count-based k-floor cutoff below handles registers with a
# large uniform floor (QFT-NOS's 5 clbits), but a wide register like full
# Shor (9 clbits) has such a small floor that a noise peak can still clear
# k x floor; the retention gate rejects those runs outright. 1% sits below
# every legitimate cold recovery (SiC Shor ~1.7%, NV ~5%, NV QFT-NOS ~28%)
# and above the noise-dominated hBN runs (~0.1%). Callers without a
# retention estimate (synthetic / ideal inputs) pass None and skip the gate.
NOISE_RETENTION_FLOOR = 0.01


def is_prime(n: int, k: int = 8) -> bool:
    """Miller-Rabin primality test."""
    if n < 2:
        return False
    if n in (2, 3):
        return True
    if n % 2 == 0:
        return False
    r, d = 0, n - 1
    while d % 2 == 0:
        r += 1
        d //= 2
    for _ in range(k):
        a = np.random.randint(2, n - 1)
        x = pow(int(a), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def perfect_power(n: int) -> Optional[tuple[int, int]]:
    """Return ``(base, exp)`` with ``exp >= 2`` if ``n = base**exp``, else None.

    This is Shor's classical preprocessing step 1: for ``N = p**k`` the quantum
    period-finding gcd step ``gcd(a**(r/2) +/- 1, N)`` gives TRIVIAL factors for
    every coprime base ``a`` (because ``a**(r/2) = +/- 1 mod p**k`` -> one gcd
    is 1, the other is N), so the factor is recovered classically instead of
    by the quantum circuit. Prime powers (9=3**2, 25=5**2, 27=3**3, ...) are
    exactly the composites that "run but return no factors" through the quantum
    path alone. Integer root via nearest-integer rounding + a +/-1 guard band
    (avoids float rounding error on large powers).
    """
    if n < 4:
        return None
    max_k = int(math.log2(n)) + 1
    for k in range(2, max_k + 1):
        b = round(n ** (1.0 / k))
        for cand in (b - 1, b, b + 1):
            if cand >= 2 and cand ** k == n:
                return (cand, k)
    return None


def select_coprime(n: int, rng=None) -> int:
    """Pick a random ``a`` with 1 < a < n and gcd(a, n) == 1."""
    rng = rng or np.random.default_rng()
    candidates = [a for a in range(2, n) if gcd(a, n) == 1]
    if not candidates:
        raise ValueError(f"no coprime base for n={n}")
    return int(rng.choice(candidates))


def mod_mult_gate(b: int, n: int) -> UnitaryGate:
    """Unitary gate implementing |x> -> |b*x mod n> on ceil(log2 n) qubits."""
    if gcd(b, n) != 1:
        raise ValueError(f"gcd({b},{n}) must be 1")
    nbits = max(1, math.ceil(math.log2(n)))
    size = 2 ** nbits
    U = np.zeros((size, size), dtype=complex)
    for x in range(size):
        y = (b * x) % n if x < n else x
        U[y, x] = 1.0
    return UnitaryGate(U, label=f"M_{b} mod {n}")


def create_period_finding_circuit(n: int, a: int) -> QuantumCircuit:
    """Quantum phase-estimation circuit for the period of ``a mod n``."""
    if gcd(a, n) != 1:
        raise ValueError("a must be coprime to n")
    l = max(1, math.ceil(math.log2(n)))
    t = 2 * l + 1  # precision / counting qubits
    qr_c = QuantumRegister(t, "count")
    qr_a = QuantumRegister(l, "anc")
    # Measure ALL qubits (counting register + ancilla) so the readout width
    # equals the qubit count: the dashboard histogram and 8x8 grid then show
    # one bit per chosen qubit (the operator's "13 qubits but only 9 bits shown"
    # complaint -- the 4 ancilla were never measured). The phase lives in the
    # counting register; the ancilla holds |a^x mod n> and is read out for
    # display only. Counting -> LOW classical bits so the phase peaks light the
    # low grid cells (same layout as before); ancilla -> high bits.
    cr = ClassicalRegister(t + l, "out")
    qc = QuantumCircuit(qr_c, qr_a, cr, name=f"period_{a}_mod_{n}")
    qc.h(qr_c)
    qc.x(qr_a[0])
    for q in range(t):
        b = pow(a, 2 ** q, n)
        if b == 1:
            continue
        c_u = mod_mult_gate(b, n).control(1)
        qc.append(c_u, [qr_c[q]] + qr_a[:])
    qc.append(QFT(t, inverse=True), qr_c)
    qc.measure(qr_c, cr[:t])      # counting register -> low classical bits
    qc.measure(qr_a, cr[t:])      # ancilla -> high classical bits (display)
    qc.metadata = dict(qc.metadata, counting_bits=t)
    return qc


def postprocess_period(counts: dict, num_control: int, a: int, n: int,
                       k: int = 2, signal_retention: Optional[float] = None,
                       reveal: Optional[dict] = None) -> tuple:
    """Recover the period and, for Shor, the factors of ``n``.

    Returns (period, (factor1, factor2)).  A trivial result yields
    ``(None, (None, None))``.

    A candidate peak is accepted only if it rises ``k``-fold above the uniform
    floor ``total / 2**num_control``. The previous fixed 1% cutoff was vacuous
    for QFT-NOS's reduced register (5 clbits -> 32 outcomes -> ~3.1% uniform
    floor, already above 1%): on a noise-dominated run it filtered nothing and a
    spurious period was reported. ``k = 2`` rejects uniform-noise outcomes
    (which for a 5-clbit register sit at ~1x the floor, maxing ~1.2x for a few
    thousand shots) while keeping genuine cold-run signal peaks (>= ~2x floor,
    e.g. SiC QFT-NOS via the shallow a=11/r=2 circuit, NV Shor-15) and clean
    ideal distributions (4x floor). It must stay below ~2.1 so the documented
    marginal QFT-NOS recoveries (peaks just above 2x) are not lost, and at/above
    2 so uniform noise is suppressed.

    A wide register (full Shor's 9 clbits -> 512 outcomes -> a tiny uniform
    floor) defeats the count cutoff alone: a noise peak can still clear
    ``k x floor``. When the caller supplies the run's ``signal_retention``
    (coherence x depolarization survival, from ``QSSCBackend.assess_circuit``),
    a noise-dominated run -- retention below :data:`NOISE_RETENTION_FLOOR` --
    is rejected outright so it cannot report a noise-artifact period (audit
    issue #8). Callers without a retention estimate (synthetic / ideal inputs)
    pass ``None`` and skip this gate.
    """
    if (signal_retention is not None
            and signal_retention < NOISE_RETENTION_FLOOR):
        return None, (None, None)
    mask = (1 << num_control) - 1
    # Marginalize out the ancilla high bits: the phase lives in the LOW
    # num_control classical bits (the counting register), and measuring the
    # ancilla for display splits each phase peak across the ancilla states.
    # Summing the counts per counting value recovers the not-measured-ancilla
    # marginal, so the k-fold-above-uniform-floor acceptance compares against
    # the 2**num_control counting-register floor exactly as it did before the
    # ancilla was measured (the NV-300 K / NV-cold Shor verdict is preserved).
    marginal = {}
    for state, count in counts.items():
        cval = int(state.replace(" ", ""), 2) & mask
        marginal[cval] = marginal.get(cval, 0) + count
    total = sum(marginal.values())
    if reveal is not None:
        reveal["marginal_counts"] = _marginal_counts_by_representative(
            counts, marginal, mask)
    floor = total / (2 ** num_control) if num_control > 0 else total
    for cval, mcount in sorted(marginal.items(), key=lambda x: x[1],
                              reverse=True)[:12]:
        if mcount <= k * floor:
            continue
        phase = cval / (2 ** num_control)
        frac = Fraction(phase).limit_denominator(n)
        r = frac.denominator
        if r % 2 == 0 and pow(a, r, n) == 1:
            if n > 1:
                p = gcd(pow(a, r // 2, n) - 1, n)
                q_ = gcd(pow(a, r // 2, n) + 1, n)
                if p * q_ == n and p > 1 and q_ > 1:
                    if reveal is not None:
                        reveal.update(bitstring=_top_full_for(counts, cval, mask),
                                      phase=phase, count=int(mcount), period=r,
                                      factors=(p, q_))
                    return r, (p, q_)
            if reveal is not None:
                reveal.update(bitstring=_top_full_for(counts, cval, mask),
                              phase=phase, count=int(mcount), period=r,
                              factors=(None, None))
            return r, (None, None)
    return None, (None, None)


def _top_full_for(counts: dict, cval: int, mask: int) -> str:
    """Most-likely full measured bitstring whose counting-register (masked)
    value equals ``cval`` -- the representative 13-bit bar for a recovered phase
    peak, so the dashboard can highlight one concrete measurement outcome."""
    best, best_n = None, -1
    for state, count in counts.items():
        if (int(state.replace(" ", ""), 2) & mask) == cval and count > best_n:
            best, best_n = state.replace(" ", ""), count
    return best


def _marginal_counts_by_representative(counts: dict, marginal: dict,
                                       mask: int) -> dict:
    """Collapse the full measured-bitstring distribution onto the counting
    register for display.

    For each counting value, sum the counts of every full bitstring that shares
    it (the marginal, already computed by the caller) and key the entry by that
    counting value's *representative* full bitstring (the most-likely one, via
    :func:`_top_full_for`). The returned histogram has three properties the
    dashboard relies on:

    * its bars **sum to ``shots``** -- the marginal is a partition of the full
      counts, so the bar counts correspond to the shot count exactly (a full
      13-bit distribution spreads each phase peak across the 2**ancilla
      measured-ancilla states, making every bar noise-level and disconnected
      from the shot count);
    * each phase peak is **concentrated into one bar** instead of smeared across
      the measured-ancilla states, so the signal rises above the noise floor and
      the recovered-peak bar count equals ``reveal['count']`` (the marginal),
      not the diluted single-bitstring count;
    * every key is a **full-width bitstring**, so the qubit grid still lights
      every measured qubit (the ancilla bits are the representative's ancilla
      state -- arbitrary, because the ancilla carries no phase information).
    """
    out = {}
    for cval, mcount in marginal.items():
        rep = _top_full_for(counts, cval, mask)
        if rep is not None:
            out[rep] = int(mcount)
    return out


def create_shor_circuit(n: int, a: Optional[int] = None, rng=None):
    """Build a Shor circuit and return (circuit, a)."""
    if n < 2 or is_prime(n):
        raise ValueError("n must be a composite > 1")
    a = a or select_coprime(n, rng)
    qc = create_period_finding_circuit(n, a)
    return qc, a


def create_qft_nos_circuit(n: int, a: Optional[int] = None, rng=None):
    """Lightweight QFT-based period-finding circuit ("QFT-NOS").

    The same quantum core as Shor — phase estimation over the map
    |x> -> |a*x mod n> with an inverse QFT readout — but stripped down for
    low-gate operation:

    * a **reduced counting register** ``t = l + 1`` (l = ceil(log2 n)) instead
      of the textbook ``t = 2l + 1``. Fewer counting qubits -> fewer
      controlled-U gates and a smaller QFT, at the cost of some precision
      (fine for the small composites used in demos / on real NISQ devices).
    * a **swapless inverse QFT** (``do_swaps=False``): the final bit-reversal
      swaps are dropped, which removes ~t/2 two-qubit gates. The measured
      bit order is reversed classically instead.
    * the controlled modular-multiplication gates use **precomputed**
      ``b = a**(2**q) mod n`` (classical), so the heavy modular-exponentiation
      ladder of full Shor is replaced by ``t`` controlled unitaries.

    The period is recovered from the measured phase by continued fractions
    and the factors are obtained classically as
    ``gcd(a**(r/2) ± 1, n)`` (see :func:`postprocess_period`).  Returns
    ``(circuit, a)``.

    References: Amico et al., PRA 100, 012305 (2019) — semiclassical/compiled
    QFT Shor on IBM Q; Tomčala, IJTP 2024 — QFT-domain modular exponentiation.
    """
    if n < 2 or is_prime(n):
        raise ValueError("n must be a composite > 1")
    a = a or select_coprime(n, rng)
    l = max(1, math.ceil(math.log2(n)))
    t = l + 1                      # reduced precision (textbook Shor uses 2l+1)
    qr_c = QuantumRegister(t, "count")
    qr_a = QuantumRegister(l, "anc")
    # Measure all qubits (counting + ancilla) so the readout width == qubit
    # count; counting -> low classical bits (phase), ancilla -> high (display).
    cr = ClassicalRegister(t + l, "out")
    qc = QuantumCircuit(qr_c, qr_a, cr, name=f"qftnos_{a}_mod_{n}")
    qc.h(qr_c)
    qc.x(qr_a[0])
    for q in range(t):
        b = pow(a, 2 ** q, n)
        if b == 1:
            continue
        c_u = mod_mult_gate(b, n).control(1)
        qc.append(c_u, [qr_c[q]] + qr_a[:])
    # swapless inverse QFT (drop the final bit-reversal swaps -> fewer gates);
    # the bit-reversal is undone classically when interpreting the readout.
    qc.append(QFT(t, inverse=True, do_swaps=False), qr_c)
    qc.measure(qr_c, cr[:t])      # counting register -> low classical bits
    qc.measure(qr_a, cr[t:])      # ancilla -> high classical bits (display)
    qc.metadata = dict(qc.metadata, counting_bits=t)
    return qc, a


def postprocess_period_swapless(counts: dict, num_control: int,
                                a: int, n: int, k: int = 2,
                                signal_retention: Optional[float] = None,
                                reveal: Optional[dict] = None) -> tuple:
    """Period + factors for the swapless QFT-NOS readout.

    Identical maths to :func:`postprocess_period` but reverses the bit order
    of each measured string first, because the swapless inverse QFT leaves the
    counting register in reversed bit order.  Returns ``(period, (p, q))``.

    Uses the same register-aware ``k``-fold-above-uniform-floor acceptance and
    the same ``signal_retention`` noise gate as :func:`postprocess_period`
    (see its docstring): the old fixed 1% cutoff was vacuous for the 5-clbit
    QFT-NOS register and admitted noise artifacts.
    """
    if (signal_retention is not None
            and signal_retention < NOISE_RETENTION_FLOOR):
        return None, (None, None)
    mask = (1 << num_control) - 1
    # Marginalize out the ancilla high bits (same reason as
    # postprocess_period: measuring the ancilla splits each phase peak across
    # its states). The counting register is the LOW num_control classical bits
    # and the swapless QFT left it in reversed bit order, so reverse those
    # num_control bits to recover the phase.
    marginal = {}
    for state, count in counts.items():
        cval = int(state.replace(" ", ""), 2) & mask
        marginal[cval] = marginal.get(cval, 0) + count
    total = sum(marginal.values())
    if reveal is not None:
        reveal["marginal_counts"] = _marginal_counts_by_representative(
            counts, marginal, mask)
    floor = total / (2 ** num_control) if num_control > 0 else total
    for cval, mcount in sorted(marginal.items(), key=lambda x: x[1],
                             reverse=True)[:12]:
        if mcount <= k * floor:
            continue
        dec = int(format(cval, "0{}b".format(num_control))[::-1], 2)
        phase = dec / (2 ** num_control)
        frac = Fraction(phase).limit_denominator(n)
        r = frac.denominator
        if r % 2 == 0 and pow(a, r, n) == 1:
            if n > 1:
                p = gcd(pow(a, r // 2, n) - 1, n)
                q_ = gcd(pow(a, r // 2, n) + 1, n)
                if p * q_ == n and p > 1 and q_ > 1:
                    if reveal is not None:
                        reveal.update(bitstring=_top_full_for(counts, cval, mask),
                                      phase=phase, count=int(mcount), period=r,
                                      factors=(p, q_))
                    return r, (p, q_)
            if reveal is not None:
                reveal.update(bitstring=_top_full_for(counts, cval, mask),
                              phase=phase, count=int(mcount), period=r,
                              factors=(None, None))
            return r, (None, None)
    return None, (None, None)


# ---------------------------------------------------------------------------
# Arithmetic toolbox (educational; built from Qiskit circuit library)
# ---------------------------------------------------------------------------

def create_math_circuit(op: str, num1: float, num2: Optional[float] = None,
                         bits: Optional[int] = None) -> QuantumCircuit:
    op = op.lower()
    n1 = int(num1); n2 = int(num2) if num2 is not None else 0
    a1, a2 = abs(n1), abs(n2)
    # register width: at least wide enough to hold the operands, but the caller
    # may request a wider word via the runner's "Word qubits n" field.
    min_bits = max(1, math.ceil(math.log2(max(a1, a2, 1)))) + 2
    bits = max(min_bits, int(bits)) if bits else min_bits
    if op in ("add", "sub"):
        # DraperQFTAdder computes |a>|b> -> |a>|a+b>, leaving the sum in qr_b
        # (qr_a is restored). So to add we encode the operands as-is; to subtract
        # we encode -n2. Encoding the signed value modulo 2^bits gives its
        # two's-complement representation, so negative operands and sub() both work:
        # sub(10,3) -> 10 + (2^bits-3) = 7 (mod 2^bits); add(10,-3) likewise -> 7.
        operand_b = n2 if op == "add" else -n2
        mod = 1 << bits
        enc_a = a1 % mod
        enc_b = (operand_b if operand_b >= 0 else operand_b + mod) % mod
        qr_a = QuantumRegister(bits, "a"); qr_b = QuantumRegister(bits, "b")
        cr = ClassicalRegister(2 * bits, "out")
        qc = QuantumCircuit(qr_a, qr_b, cr)
        for i in range(bits):
            if (enc_a >> i) & 1:
                qc.x(qr_a[i])
            if (enc_b >> i) & 1:
                qc.x(qr_b[i])
        qc.append(DraperQFTAdder(bits), qr_a[:] + qr_b[:])
        # measure all qubits: the sum (qr_b) -> low classical bits so it lights
        # the low grid cells; the restored operand (qr_a) -> high bits.
        qc.measure(qr_b, cr[:bits])
        qc.measure(qr_a, cr[bits:])
        qc.metadata = dict(qc.metadata, result_width=bits)
        return qc
    if op == "mul":
        qr_a = QuantumRegister(bits, "a"); qr_b = QuantumRegister(bits, "b")
        qr_r = QuantumRegister(2 * bits, "res")
        cr = ClassicalRegister(4 * bits, "out")
        qc = QuantumCircuit(qr_a, qr_b, qr_r, cr)
        for i in range(bits):
            if (a1 >> i) & 1:
                qc.x(qr_a[i])
            if (a2 >> i) & 1:
                qc.x(qr_b[i])
        qc.append(RGQFTMultiplier(bits), qr_a[:] + qr_b[:] + qr_r[:])
        # measure all qubits: the product (qr_r) -> low classical bits so it
        # lights the low grid cells; operands (qr_a, qr_b) -> high bits.
        qc.measure(qr_r, cr[:2 * bits])
        qc.measure(qr_a, cr[2 * bits:3 * bits])
        qc.measure(qr_b, cr[3 * bits:])
        qc.metadata = dict(qc.metadata, result_width=2 * bits)
        return qc
    if op == "sqrt":
        qr = QuantumRegister(bits); qr_a = QuantumRegister(1)
        cr = ClassicalRegister(bits + 1, "out")
        qc = QuantumCircuit(qr, qr_a, cr)
        qc.ry(math.pi / 4, qr_a)
        unitary = RYGate(math.pi / 2)  # 1-qubit unitary
        qc.append(PhaseEstimation(bits, unitary), qr[:] + qr_a[:])
        # measure all qubits: phase register (qr) -> low bits; ancilla -> high.
        qc.measure(qr, cr[:bits])
        qc.measure(qr_a, cr[bits:bits + 1])
        qc.metadata = dict(qc.metadata, result_width=bits)
        return qc
    if op == "log":
        qr = QuantumRegister(bits); qr_a = QuantumRegister(1)
        cr = ClassicalRegister(bits + 1, "out")
        qc = QuantumCircuit(qr, qr_a, cr)
        angle = math.asin(math.sqrt(a1 / (2 ** bits))) if 0 < a1 < 2 ** bits else 0
        qc.ry(2 * angle, qr_a)
        unitary = RYGate(2 * math.pi / bits)  # 1-qubit unitary
        qc.append(PhaseEstimation(bits, unitary), qr[:] + qr_a[:])
        qc.measure(qr, cr[:bits])
        qc.measure(qr_a, cr[bits:bits + 1])
        qc.metadata = dict(qc.metadata, result_width=bits)
        return qc
    raise ValueError(f"unknown op '{op}'; use add/sub/mul/sqrt/log")


def grover_search(num_qubits: int, oracle: QuantumCircuit, iterations: int = 1) -> QuantumCircuit:
    """Build a small Grover search circuit for a given oracle."""
    qr = QuantumRegister(num_qubits, "q")
    cr = ClassicalRegister(num_qubits, "out")
    qc = QuantumCircuit(qr, cr)
    qc.h(qr)
    grover = GroverOperator(oracle)
    for _ in range(max(1, iterations)):
        qc.compose(grover, qr, inplace=True)
    qc.measure(qr, cr)
    return qc


# ---------------------------------------------------------------------------
# AST-sandboxed user-circuit builder
# ---------------------------------------------------------------------------
# Split into circuits_user (UserCircuitError + the AST validator + the numpy
# file-IO stripping proxy + build_user_circuit). Re-exported here so
# `from qssc.circuits import build_user_circuit` keeps resolving for api/runner,
# tests and qssc/__init__. The sandbox gates the /api/circuit/custom endpoint
# (API key + localhost only); see circuits_user.py for the not-absolute caveat.
from .circuits_user import (  # noqa: F401
    UserCircuitError, build_user_circuit,
)


# keep Optional import used
__all__ = [
    "is_prime", "select_coprime", "mod_mult_gate",
    "create_period_finding_circuit", "postprocess_period", "create_shor_circuit",
    "create_qft_nos_circuit", "postprocess_period_swapless",
    "create_math_circuit", "grover_search",
    "build_user_circuit", "UserCircuitError",
]