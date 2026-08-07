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
    QFT, DraperQFTAdder, CDKMRippleCarryAdder, RGQFTMultiplier,
    PhaseEstimation, GroverOperator,
)
from qiskit.circuit.library.standard_gates import RYGate
from qiskit.circuit.library import UnitaryGate


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
    cr = ClassicalRegister(t, "out")
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
    qc.measure(qr_c, cr)
    return qc


def postprocess_period(counts: dict, num_control: int, a: int, n: int) -> tuple:
    """Recover the period and, for Shor, the factors of ``n``.

    Returns (period, (factor1, factor2)).  A trivial result yields
    ``(None, (None, None))``.
    """
    total = sum(counts.values())
    for state, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:12]:
        if count < 0.01 * total:
            continue
        dec = int(state.replace(" ", ""), 2)
        phase = dec / (2 ** num_control)
        frac = Fraction(phase).limit_denominator(n)
        r = frac.denominator
        if r % 2 == 0 and pow(a, r, n) == 1:
            if n > 1:
                p = gcd(pow(a, r // 2, n) - 1, n)
                q_ = gcd(pow(a, r // 2, n) + 1, n)
                if p * q_ == n and p > 1 and q_ > 1:
                    return r, (p, q_)
            return r, (None, None)
    return None, (None, None)


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
    cr = ClassicalRegister(t, "out")
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
    qc.measure(qr_c, cr)
    return qc, a


def postprocess_period_swapless(counts: dict, num_control: int,
                                a: int, n: int) -> tuple:
    """Period + factors for the swapless QFT-NOS readout.

    Identical maths to :func:`postprocess_period` but reverses the bit order
    of each measured string first, because the swapless inverse QFT leaves the
    counting register in reversed bit order.  Returns ``(period, (p, q))``.
    """
    total = sum(counts.values())
    for state, count in sorted(counts.items(), key=lambda x: x[1],
                               reverse=True)[:12]:
        if count < 0.01 * total:
            continue
        raw = state.replace(" ", "")
        # swapless QFT: measured MSB..LSB is reversed -> reverse to recover phase
        dec = int(raw[::-1], 2)
        phase = dec / (2 ** num_control)
        frac = Fraction(phase).limit_denominator(n)
        r = frac.denominator
        if r % 2 == 0 and pow(a, r, n) == 1:
            if n > 1:
                p = gcd(pow(a, r // 2, n) - 1, n)
                q_ = gcd(pow(a, r // 2, n) + 1, n)
                if p * q_ == n and p > 1 and q_ > 1:
                    return r, (p, q_)
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
        if op == "sub":
            n2 = -n2; a2 = abs(n2)
        qr_a = QuantumRegister(bits, "a"); qr_b = QuantumRegister(bits, "b")
        cr = ClassicalRegister(bits, "out")
        qc = QuantumCircuit(qr_a, qr_b, cr)
        for i in range(bits):
            if (a1 >> i) & 1:
                qc.x(qr_a[i])
            if (a2 >> i) & 1:
                qc.x(qr_b[i])
        qc.append(DraperQFTAdder(bits), qr_a[:] + qr_b[:])
        qc.measure(qr_b, cr)
        return qc
    if op == "mul":
        qr_a = QuantumRegister(bits, "a"); qr_b = QuantumRegister(bits, "b")
        qr_r = QuantumRegister(2 * bits, "res")
        cr = ClassicalRegister(2 * bits, "out")
        qc = QuantumCircuit(qr_a, qr_b, qr_r, cr)
        for i in range(bits):
            if (a1 >> i) & 1:
                qc.x(qr_a[i])
            if (a2 >> i) & 1:
                qc.x(qr_b[i])
        qc.append(RGQFTMultiplier(bits), qr_a[:] + qr_b[:] + qr_r[:])
        qc.measure(qr_r, cr)
        return qc
    if op == "sqrt":
        qr = QuantumRegister(bits); qr_a = QuantumRegister(1)
        cr = ClassicalRegister(bits, "out")
        qc = QuantumCircuit(qr, qr_a, cr)
        qc.ry(math.pi / 4, qr_a)
        unitary = RYGate(math.pi / 2)  # 1-qubit unitary
        qc.append(PhaseEstimation(bits, unitary), qr[:] + qr_a[:])
        qc.measure(qr, cr)
        return qc
    if op == "log":
        qr = QuantumRegister(bits); qr_a = QuantumRegister(1)
        cr = ClassicalRegister(bits, "out")
        qc = QuantumCircuit(qr, qr_a, cr)
        angle = math.asin(math.sqrt(a1 / (2 ** bits))) if 0 < a1 < 2 ** bits else 0
        qc.ry(2 * angle, qr_a)
        unitary = RYGate(2 * math.pi / bits)  # 1-qubit unitary
        qc.append(PhaseEstimation(bits, unitary), qr[:] + qr_a[:])
        qc.measure(qr, cr)
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


class UserCircuitError(ValueError):
    """Raised when a user-supplied circuit definition cannot be built."""


# --- AST sandbox for the Python circuit path ------------------------------
# A restricted-builtins dict alone is escapable: any exposed object (e.g.
# QuantumCircuit) leaks the real builtins through `obj.__init__.__globals__`,
# giving arbitrary os/system execution.  We therefore validate the parsed AST
# and reject dunder-attribute access (the escape hatch) and calls to dangerous
# builtins, in addition to the import whitelist.  Normal Qiskit usage
# (`QuantumCircuit(2)`, `qc.h(0)`, `qc.cx(0,1)`, `qc.measure(...)`) uses only
# non-dunder attributes and is unaffected.
import ast as _ast

_USER_ALLOWED_MODULES = {
    "qiskit", "qiskit.circuit", "qiskit.circuit.library",
    "numpy", "math", "fractions",
}
_USER_DANGEROUS_CALLS = {
    "eval", "exec", "compile", "getattr", "setattr", "delattr", "globals",
    "locals", "vars", "open", "input", "__import__", "breakpoint", "exit",
    "quit", "memoryview", "type", "object", "classmethod", "staticmethod",
    "property", "super",
}


class _UserAstValidator(_ast.NodeVisitor):
    """Reject AST nodes that could escape the restricted namespace."""

    def __init__(self):
        self.errors: list = []

    @staticmethod
    def _is_dunder(attr: str) -> bool:
        return attr.startswith("__") and attr.endswith("__")

    def visit_Attribute(self, node):  # noqa: D401
        # block `x.__globals__`, `x.__init__`, `x.__class__`, `x.__dict__`, ...
        if self._is_dunder(node.attr):
            self.errors.append(
                f"attribute access to '{node.attr}' is not allowed")
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa: D401
        func = node.func
        if isinstance(func, _ast.Name) and func.id in _USER_DANGEROUS_CALLS:
            self.errors.append(f"call to '{func.id}' is not allowed")
        self.generic_visit(node)

    def visit_Import(self, node):  # noqa: D401
        for n in node.names:
            if n.name.split(".")[0] not in _USER_ALLOWED_MODULES:
                self.errors.append(f"import of '{n.name}' is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):  # noqa: D401
        mod = (node.module or "").split(".")[0]
        if mod not in _USER_ALLOWED_MODULES:
            self.errors.append(f"import from '{node.module}' is not allowed")
        self.generic_visit(node)


def build_user_circuit(code: str, max_qubits: int = 64) -> QuantumCircuit:
    """Build a Qiskit circuit from user-supplied source.

    Accepts either:

    * **OpenQASM 2.0** — detected when the text contains ``OPENQASM`` or
      starts with a ``qreg``/``creg`` declaration. Parsed with
      :meth:`QuantumCircuit.from_qasm_str`.
    * **Python** — a snippet that constructs a :class:`QuantumCircuit` named
      ``qc`` (or ``circuit``) using a restricted namespace exposing the common
      Qiskit primitives. The snippet is first validated by an **AST sandbox**
      that rejects dunder-attribute access (the route by which a restricted
      builtins dict is normally escaped) and dangerous calls, then executed
      with a restricted builtins namespace and an import whitelist.

    The resulting circuit must not exceed ``max_qubits`` qubits.

    .. note::

       This is a **local lab / demo tool**. The AST sandbox closes the known
       restricted-builtins escape (`obj.__init__.__globals__`), but no pure-Python
       sandbox is absolute; do not expose this endpoint to untrusted remote
       clients. The API already binds to localhost.
    """
    text = (code or "").strip()
    if not text:
        raise UserCircuitError("empty circuit source")

    # --- OpenQASM 2.0 path -------------------------------------------------
    if "OPENQASM" in text or text.startswith(("qreg", "creg", "gate ", "opaque")):
        # qiskit 2.x qasm2 does not auto-include the standard gate library,
        # so inject qelib1.inc when the user omitted it (lets plain `h`/`cx`
        # QASM "just work").
        if "qelib1.inc" not in text and "include" not in text:
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("OPENQASM"):
                lines.insert(1, 'include "qelib1.inc";')
            else:
                lines.insert(0, 'OPENQASM 2.0;', 'include "qelib1.inc";')
            text = "\n".join(lines)
        try:
            qc = QuantumCircuit.from_qasm_str(text)
        except Exception as exc:  # noqa: BLE001 - surface as a clean error
            raise UserCircuitError(f"QASM parse failed: {exc}") from exc
        if qc.num_qubits > max_qubits:
            raise UserCircuitError(f"circuit has {qc.num_qubits} qubits > {max_qubits}")
        if qc.num_clbits == 0:  # ensure measurable
            qc.measure_all()
        return qc

    # --- Python path (AST-sandboxed) --------------------------------------
    import builtins as _builtins

    # 1. parse + validate the AST before any execution
    try:
        tree = _ast.parse(text, filename="<user_circuit>")
    except SyntaxError as exc:
        raise UserCircuitError(f"Python parse failed: {exc}") from exc
    validator = _UserAstValidator()
    validator.visit(tree)
    if validator.errors:
        raise UserCircuitError(
            "blocked by sandbox: " + "; ".join(sorted(set(validator.errors))))

    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0 or name not in _USER_ALLOWED_MODULES:
            raise UserCircuitError(f"import of '{name}' is not allowed")
        return _builtins.__import__(name, globals, locals, fromlist, level)

    namespace = {
        # primitives the user script may use directly (no import needed)
        "QuantumCircuit": QuantumCircuit,
        "QuantumRegister": QuantumRegister,
        "ClassicalRegister": ClassicalRegister,
        # convenience aliases matching common tutorials
        "np": np, "math": math, "pi": math.pi,
    }
    safe_builtins = {
        "abs": abs, "min": min, "max": max, "range": range, "len": len,
        "sum": sum, "round": round, "int": int, "float": float, "bool": bool,
        "list": list, "tuple": tuple, "dict": dict, "set": set,
        "enumerate": enumerate, "zip": zip, "sorted": sorted, "str": str,
        "True": True, "False": False, "None": None,
        "__import__": _safe_import, "__build_class__": _builtins.__build_class__,
        "print": print,
    }
    namespace["__builtins__"] = safe_builtins
    try:
        exec(compile(tree, "<user_circuit>", "exec"), namespace)  # noqa: S102
    except UserCircuitError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UserCircuitError(f"Python build failed: {exc}") from exc

    qc = namespace.get("qc") or namespace.get("circuit")
    if not isinstance(qc, QuantumCircuit):
        raise UserCircuitError(
            "script must define a QuantumCircuit named `qc` (or `circuit`)")
    if qc.num_qubits > max_qubits:
        raise UserCircuitError(f"circuit has {qc.num_qubits} qubits > {max_qubits}")
    if qc.num_clbits == 0:
        qc.measure_all()
    return qc


# keep Optional import used
__all__ = [
    "is_prime", "select_coprime", "mod_mult_gate",
    "create_period_finding_circuit", "postprocess_period", "create_shor_circuit",
    "create_qft_nos_circuit", "postprocess_period_swapless",
    "create_math_circuit", "grover_search",
    "build_user_circuit", "UserCircuitError",
]