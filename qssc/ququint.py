"""Ququint GF(5) simulation layer for QSSC -- PROTOTYPE, SIMULATION ONLY.

A minimal GF(5)-qudit (5-level) statevector layer in the style of the
riemann-nuclear-synthesis ququint experiments (bartman081523/riemann-
nuclear-synthesis, CC-BY 4.0) and Campbell et al.'s qudit magic-state
results (QEC 2016 -- 36.3% distillation threshold for d=5).

HONESTY CONTRACT (read before using any number from this file):
  * This layer is a MATH/SIMULATION testbed. The QSSC defect stack has NO
    native 5-level system: the NV electron is 2-level (ms=0,+1), 14N is
    3-level (I=1), 13C is 2-level (I=1/2). A GF(5) qudit would physically
    need a spin-2 (I=2) nucleus, which is not present in the QSSC stack.
  * All fidelities here are from this file's own depolarizing Monte-Carlo
    on statevectors (seed=42). They are MODEL numbers, not hardware
    measurements. The 7-T-gate CCZ count and the 4-M-gate qudit CCZ count
    are literature/decomposition arguments (Campbell et al.), not
    measurements.
  * See ququint_sim_results.json "honesty" block.

Gates (d=5, omega = exp(2i pi/5)):
    X_d : |k> -> |k+1 mod d>            (shift)
    Z_d : |k> -> omega^k |k>            (phase)
    F_d : DFT, |k> -> (1/sqrt(d)) sum_j omega^{jk} |j>
    CCZ_d : |a,b,c> -> omega^{a*b*c} |a,b,c>   (the d=5 entangling gate,
        diagonal cubic phase -- magic for the d-dimensional stabilizer
        formalism; injected via the magic state CCZ|+++>)

Noise: global depolarizing per gate, Monte-Carlo -- with probability p the
qudit register is hit by a random X^a Z^b on a random qudit (a,b uniform in
0..d-1), the standard Pauli-twirl picture of depolarizing noise.

Usage:
    python3 -m qssc.ququint                 # runs the demo, writes JSON to CWD
    pytest tests/test_ququint.py -q         # unit tests
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

D = 5
OMEGA = np.exp(2j * np.pi / D)
SEED = 42
# results land in the current working directory (do not write into the
# package directory); a committed snapshot lives in docs/ququint_sim_results.json
OUT = Path("ququint_sim_results.json")


# ---------------------------------------------------------------------------
# Single-qudit gates
# ---------------------------------------------------------------------------
def x5() -> np.ndarray:
    return np.roll(np.eye(D), 1, axis=0).astype(complex)


def z5() -> np.ndarray:
    return np.diag(OMEGA ** np.arange(D)).astype(complex)


def f5() -> np.ndarray:
    j, k = np.meshgrid(np.arange(D), np.arange(D), indexing="ij")
    return np.exp(2j * np.pi * j * k / D) / math.sqrt(D)


# ---------------------------------------------------------------------------
# Three-qudit register helpers
# ---------------------------------------------------------------------------
def basis_state(idx: list[int]) -> np.ndarray:
    """|a> (x) |b> (x) |c> as a length-125 vector, index = a*25 + b*5 + c."""
    v = np.zeros(D ** 3, dtype=complex)
    v[idx[0] * D * D + idx[1] * D + idx[2]] = 1.0
    return v


def ccz5_matrix() -> np.ndarray:
    """CCZ_5 = diag over (a,b,c) of omega^{a*b*c}."""
    diag = np.ones(D ** 3, dtype=complex)
    for a in range(D):
        for b in range(D):
            for c in range(D):
                i = a * D * D + b * D + c
                diag[i] = OMEGA ** ((a * b * c) % D)
    return np.diag(diag)


def apply_depolarizing(state: np.ndarray, rng: np.random.Generator,
                       p: float) -> np.ndarray:
    """One depolarizing step on the 3-qudit register (Monte-Carlo).

    With probability p: random X^a Z^b on a uniformly random qudit
    (a, b uniform in 0..D-1). This is the Pauli-twirled depolarizing
    channel; pure-state Monte-Carlo equivalent for these observables.
    """
    if p <= 0 or rng.random() >= p:
        return state
    q = int(rng.integers(0, 3))
    a, b = int(rng.integers(0, D)), int(rng.integers(0, D))
    # apply X^a Z^b on qudit q: build the single-qudit error, then kron
    err1 = (np.linalg.matrix_power(x5(), a) @ np.linalg.matrix_power(z5(), b))
    ops = [np.eye(D, dtype=complex)] * 3
    ops[q] = err1
    full = np.kron(np.kron(ops[0], ops[1]), ops[2])
    return full @ state


def measure_z(state: np.ndarray, rng: np.random.Generator,
              qudit: int) -> int:
    """Measure one qudit in the computational basis, collapse the state."""
    probs = np.abs(state) ** 2
    probs = probs / probs.sum()
    outcome_idx = int(rng.choice(D ** 3, p=probs))
    val = (outcome_idx // (D * D if qudit == 0 else D)) % D
    collapsed = np.zeros_like(state)
    collapsed[outcome_idx] = 1.0
    state[:] = collapsed
    return val


# ---------------------------------------------------------------------------
# Qubit-side comparison: CCZ on 2 qubits + ancilla-free phase-kickback demo
# ---------------------------------------------------------------------------
def ccz_qubit_matrix() -> np.ndarray:
    """Ideal 3-qubit CCZ (diag(1,1,1,1,1,1,1,-1)) -- the qubit target unitary."""
    m = np.eye(8, dtype=complex)
    m[7, 7] = -1.0
    return m


def t_count_qubit_ccz() -> int:
    """T-gate count of the standard Clifford+T CCZ decomposition (literature:
    7 T gates, e.g. Nielsen & Chuang-derived standard constructions)."""
    return 7


def m_count_ququint_ccz() -> int:
    """M-gate count of the d=5 CCZ magic-state injection (literature:
    4 M-type gates per Campbell et al. qudit constructions)."""
    return 4


def survived_fidelity(unitary: np.ndarray, prep: np.ndarray,
                      n_gates: int, p: float, shots: int,
                      rng: np.random.Generator) -> float:
    """Average |<psi_ideal|psi_noisy>|^2 after n_gates depolarizing steps.

    The noisy state is evolved by the ideal unitary, then hit by n_gates
    depolarizing steps (one per physical gate in that implementation's
    decomposition). This models 'same logical operation, different gate
    count under the same per-gate error rate' -- the 1.75x gate-count
    advantage argument, honestly labelled as a model.
    """
    ideal = unitary @ prep
    fids = []
    for _ in range(shots):
        s = prep.copy()
        # apply the ideal operation once, then the per-gate noise budget
        s = unitary @ s
        for _ in range(n_gates):
            s = apply_depolarizing(s, rng, p)
        f = abs(np.vdot(ideal, s)) ** 2
        fids.append(f)
    return float(np.mean(fids))


# ---------------------------------------------------------------------------
# QSSC bridge (documenting, not simulating hardware)
# ---------------------------------------------------------------------------
def qssc_level_structure() -> dict:
    """The actual level structure of the QSSC defect stack (from
    qssc/defects.py NV_DIAMOND) -- and why no GF(5) hardware mapping exists
    today."""
    return {
        "nv_electron": {"levels": 2, "comment": "ms=0, ms=+-1 (S=1, but "
                        "readout/control uses the 0<->+1 two-level subspace)"},
        "n_14n_nucleus": {"levels": 3, "comment": "I=1"},
        "c_13c_memory_nucleus": {"levels": 2, "comment": "I=1/2"},
        "combined_per_site": 12,
        "native_gf5_system": None,
        "conclusion": ("No spin-2 (I=2) nucleus in the QSSC stack -> the GF(5) "
                       "layer is simulation-only. A future 5-level encoding "
                       "would need an I=2 nucleus (e.g. a deliberately "
                       "implanted donor) -- hardware work, not software."),
    }


# ---------------------------------------------------------------------------
# Demo experiment
# ---------------------------------------------------------------------------
def run_demo() -> dict:
    rng = np.random.default_rng(SEED)
    ccz5 = ccz5_matrix()
    prep = basis_state([1, 1, 1])

    results = {"simulation_only": True, "seed": SEED,
               "dimension": D, "shots_per_point": 4000,
               "honesty": {
                   "fidelities_are": "depolarizing Monte-Carlo MODEL numbers "
                                     "(this file), not measurements",
                   "gate_counts_are": "literature/decomposition arguments "
                                      "(7 T vs 4 M, Campbell et al.)",
                   "hardware_mapping": qssc_level_structure(),
               },
               "ccz_fidelity_vs_depolarizing": []}

    for p in (0.001, 0.01, 0.05, 0.1):
        # qubit CCZ: 8-dim space, ideal prep |000>, 7 T gates + 6 Cliffords
        # modelled as 13 depolarizing steps on the qubit register
        f_qubit = _qubit_ccz_fidelity(p, rng)
        # ququint CCZ: 125-dim space, prep |111>, magic injection modelled as
        # 4 M-type gates -> 4 depolarizing steps on the qudit register
        f_ququint = survived_fidelity(ccz5, prep, m_count_ququint_ccz(),
                                      p, 4000, rng)
        results["ccz_fidelity_vs_depolarizing"].append({
            "p": p,
            "qubit_ccz_7T_13steps": round(f_qubit, 4),
            "ququint_ccz_4M_4steps": round(f_ququint, 4),
            "advantage": round(f_ququint - f_qubit, 4),
        })

    # orthogonality / unitarity self-checks go in the tests; here just the
    # CCZ_5 phase sanity: CCZ|111> must pick up phase omega^(1*1*1) = omega
    ph = ccz5 @ basis_state([1, 1, 1])
    phase = complex(ph[1 * 25 + 1 * 5 + 1])
    results["ccz_phase_check"] = {
        "expected": "exp(2i pi/5)",
        "got_real": round(phase.real, 6),
        "got_imag": round(phase.imag, 6),
        "ok": bool(abs(phase - OMEGA) < 1e-9),
    }
    out = OUT
    out.write_text(json.dumps(results, indent=2) + "\n")
    return results


def _qubit_ccz_fidelity(p: float, rng: np.random.Generator) -> float:
    """Monte-Carlo fidelity of the qubit CCZ under 13 depolarizing steps
    (7 T + 6 Clifford 1q gates) on 3 qubits, same channel family as the
    qudit side (random X^a Z^b, a,b in 0..1)."""
    ideal_prep = np.zeros(8, dtype=complex)
    ideal_prep[0] = 1.0
    ccz = ccz_qubit_matrix()
    ideal = ccz @ ideal_prep

    def xq(n: int) -> np.ndarray:
        x = np.array([[0, 1], [1, 0]], dtype=complex)
        return np.linalg.matrix_power(x, n)

    def dep(state: np.ndarray) -> np.ndarray:
        if p <= 0 or rng.random() >= p:
            return state
        q = int(rng.integers(0, 3))
        a, b = int(rng.integers(0, 2)), int(rng.integers(0, 2))
        g = xq(a) @ np.linalg.matrix_power(np.diag([1, -1]).astype(complex), b)
        ops = [np.eye(2, dtype=complex)] * 3
        ops[q] = g
        full = np.kron(np.kron(ops[0], ops[1]), ops[2])
        return full @ state

    fids = []
    for _ in range(4000):
        s = ccz @ ideal_prep
        for _ in range(13):
            s = dep(s)
        fids.append(abs(np.vdot(ideal, s)) ** 2)
    return float(np.mean(fids))


if __name__ == "__main__":
    res = run_demo()
    print(json.dumps(res["ccz_phase_check"]))
    for row in res["ccz_fidelity_vs_depolarizing"]:
        print(f"p={row['p']:<6} qubit(13 steps)={row['qubit_ccz_7T_13steps']:<8}"
              f" ququint(4 steps)={row['ququint_ccz_4M_4steps']:<8}"
              f" advantage={row['advantage']}")
    print(f"simulation_only={res['simulation_only']} seed={res['seed']}")
    print(f"wrote {OUT}")