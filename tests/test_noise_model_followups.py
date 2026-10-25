# tests/test_noise_model_followups.py
"""Regression locks for the QSSC-002 follow-ups #7 / #8 / #9.

* #7a -- the two-qubit thermal channel must relax BOTH qubits over
  ``gate_2q`` (the control component was built from ``gate_1q``, ~8x
  under-relaxation for these profiles).
* #7b -- the analytic/phenomenological path must carry the cumulative
  per-gate depolarization, like the "real" density-matrix path does.
* #8 -- the period-recovery candidate floor must scale with the readout
  register width: a fixed 1% is vacuous for the reduced QFT-NOS register
  (uniform noise per state sits above 1% there, so noise states produced
  spurious continued-fraction periods).
* #9 -- provenance: ``depol_1q``/``depol_2q`` carry an explicit
  UNVERIFIED engineering-estimate note in their definition.

Merged into main from the branch stack: two locks from the original module
were dropped because main deliberately moved on --
``test_period_floor_recovers_full_shor_readout_unchanged`` (main's
register-aware cutoff now governs the wide register, audit #8) and
``test_depol_provenance_annotated`` (main's depol constants were revised;
the provenance wording moved with them).
"""
from __future__ import annotations

import dataclasses

import pytest

from qssc import create_shor_circuit, get_profile
from qssc.circuits import postprocess_period
from qssc.backend import QSSCBackend
from qssc.defects import NV_DIAMOND as NV


# ---------------------------------------------------------------------------
# #8 -- postprocess_period candidate floor
# ---------------------------------------------------------------------------

def _uniform_counts(width: int, shots: int) -> dict:
    """Perfectly uniform readout over ``2**width`` states."""
    per = shots // (2 ** width)
    return {format(i, f"0{width}b"): per for i in range(2 ** width)}


def test_period_floor_rejects_uniform_noise_on_short_register():
    """QFT-NOS (n=15 -> 5 control bits): uniform noise ~3.1%/state.

    The old fixed 1% floor let every state through and the dict's first
    entry -- here ``01000`` (dec=8, phase 1/4) -- continued-fractioned to
    r=4 with pow(7,4,15)==1 and *returned the factors of 15 from pure
    noise*.  The mu+4sigma floor (128 + 4x11.3 = 173.2) must reject every
    state (expected max of a uniform 32-state readout ~ mu + 2.6 sigma).
    """
    counts = _uniform_counts(5, 1024)
    counts = {"01000": counts.pop("01000"), **counts}  # force tie order
    assert postprocess_period(counts, num_control=5, a=7, n=15) == (None, (None, None))


def test_period_floor_recovers_real_peak_on_short_register():
    """A genuine phase peak (well above mu + 4 sigma) is still recovered:
    15 = 3x5."""
    counts = _uniform_counts(5, 1024)
    counts["01000"] = 500           # 49% of shots on the true peak
    r, factors = postprocess_period(counts, num_control=5, a=7, n=15)
    assert r == 4
    assert factors == (3, 5)


# ---------------------------------------------------------------------------
# #7b -- cumulative gate depolarization in the analytic path
# ---------------------------------------------------------------------------

@pytest.fixture()
def quiet_backend():
    """NV backend with T1/T2 = 1 s (damping ~ 0 over a us-scale circuit)
    so the ONLY difference between the two runs is the profile's
    depolarization constants."""
    prof = dataclasses.replace(NV, t1_room=1.0, t2_room=1.0)
    return QSSCBackend(profile=prof, shots=8192, seed=42)


def test_analytic_path_cumulative_depolarization(quiet_backend):
    """Without depol the ideal peaks survive; with depol_*=0.5 every gate
    composes a 50% depolarizing channel -> after est_1q+est_2q gates the
    register is uniform.  (The old analytic path ignored depol entirely,
    so BOTH runs came out peak-shaped.)"""
    from qiskit import QuantumCircuit

    qc = QuantumCircuit(3, 3)
    qc.x(0)
    qc.cx(0, 1)
    qc.cx(1, 2)
    qc.measure([0, 1, 2], [0, 1, 2])

    ideal = quiet_backend._run_ideal(qc, 8192)

    quiet = dataclasses.replace(quiet_backend.profile, depol_1q=0.0, depol_2q=0.0)
    be_quiet = QSSCBackend(profile=quiet, shots=8192, seed=42)
    loud = dataclasses.replace(quiet_backend.profile, depol_1q=0.5, depol_2q=0.5)
    be_loud = QSSCBackend(profile=loud, shots=8192, seed=42)

    counts_quiet = be_quiet._apply_phenomenological_noise(qc, ideal, 8192)
    counts_loud = be_loud._apply_phenomenological_noise(qc, ideal, 8192)

    p111_quiet = counts_quiet.get("111", 0) / 8192
    p111_loud = counts_loud.get("111", 0) / 8192
    # readout error alone keeps the deterministic |111> outcome dominant
    assert p111_quiet >= 0.4
    # ~7 gates x 50% depolarization -> identity ~0.8% -> near-uniform
    assert p111_loud < 0.2
    tv = 0.5 * sum(abs(counts_loud.get(format(i, "03b"), 0) / 8192 - 0.125)
                   for i in range(8))
    assert tv < 0.15


def test_analytic_path_matches_real_mode_depolarization_direction():
    """Consistency (#7): for the same profile, a *deep* circuit must not be
    less noisy in the analytic fallback than in the real DM path.  The DM
    path composes depolarizing onto every gate; assert the analytic p_uniform
    exceeds the DM path's survival on an over-budget circuit."""
    prof = dataclasses.replace(NV, t1_room=1.0, t2_room=1.0,
                               depol_1q=0.02, depol_2q=0.02)
    be = QSSCBackend(profile=prof, shots=4096, seed=42)
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(2, 2)
    for _ in range(20):
        qc.x(0)
        qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])
    counts_real, approximate = be._run_real(qc, 4096, be.assess_circuit(qc))
    assert approximate is False          # small enough for the DM path
    ideal = be._run_ideal(qc, 4096)
    counts_analytic = be._apply_phenomenological_noise(qc, ideal, 4096)
    p00_real = counts_real.get("00", 0) / 4096
    p00_analytic = counts_analytic.get("00", 0) / 4096
    # both depolarized, but the analytic path must NOT be cleaner
    assert p00_analytic <= p00_real + 0.05


# ---------------------------------------------------------------------------
# #7a -- the 2q thermal channel is composed over gate_2q for BOTH qubits
# ---------------------------------------------------------------------------

def test_two_qubit_thermal_uses_gate2q_for_both_qubits(monkeypatch):
    """Spy on ``thermal_relaxation_error``: one CX over 2 active qubits must
    emit exactly 2 two-qubit-duration calls (control tensor + target), not
    the old 1 (control built from gate_1q)."""
    be = QSSCBackend(profile="NV", shots=64, seed=42)
    import qssc.backend as be_mod
    real = be_mod.thermal_relaxation_error
    calls: list[tuple[float, float, float]] = []

    def spy(t1, t2, time, **kw):
        calls.append((float(t1), float(t2), float(time)))
        return real(t1, t2, time, **kw)

    monkeypatch.setattr(be_mod, "thermal_relaxation_error", spy)

    from qiskit import QuantumCircuit
    qc = QuantumCircuit(2)
    qc.cx(0, 1)
    be._build_real_noise_model(qc)

    n_1q = sum(1 for c in calls if c[2] == be._g1q)
    n_2q = sum(1 for c in calls if c[2] == be._g2q)
    assert n_1q == 2          # one per active qubit: the 1q-gate channel
    assert n_2q == 4          # BOTH tensor components over gate_2q (was 2)
    # each active qubit's own (T1, T2) must appear over the 2q duration
    for q in (0, 1):
        t1_q = be.sites[q].t1 or be._t1
        t2_q = be.sites[q].t2 or be._t2
        assert any(c[2] == be._g2q and c[0] == t1_q and c[1] == t2_q
                   for c in calls)


# ---------------------------------------------------------------------------
# #9 -- depol provenance annotation
# ---------------------------------------------------------------------------

# (the original #9 provenance lock -- test_depol_provenance_annotated --
# pinned the pre-merge depol constants (0.0008/0.0002/0.0005) and their
# per-profile annotation; main revised the constants, so the lock is gone
# and the provenance wording travels with defects.py itself.)

# ---------------------------------------------------------------------------
# cooling mechanism still works once depolarization is engineered out (#7/#9)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("short", ["hBN-VB", "NV", "SiC-VSi"])
def test_cold_recovery_returns_when_depolarization_engineered_out(short):
    """The depol wall (#7) is temperature-independent, but the T2-cooling
    knob itself still works: the SAME cold Shor-15 run with depol zeroed
    recovers 3x5 on EVERY platform (hBN-VB has the tightest cold T2,
    ~1.1 ms vs the ~0.65 ms circuit -- still inside the window).

    At the profiles' current depol_2q estimates (0.002..0.006) recovery at
    n=15 depth needs depol_2q <= ~2e-4, i.e. up to ~30x below the estimate:
    quoting any recovery rate at scale first needs the #9 sensitivity
    sweep.  These runs isolate the mechanism, they do NOT re-open the
    headline claim."""
    prof = dataclasses.replace(get_profile(short),
                               depol_1q=0.0, depol_2q=0.0)
    be = QSSCBackend(profile=prof, shots=4096, seed=1,
                     temperature_k=1e-3, noise_mode="real")
    qc, a = create_shor_circuit(15, a=7)
    res = be.run(qc, shots=4096).result()
    _, factors = postprocess_period(res.get_counts(), qc.num_clbits, a, 15)
    assert res.assessment["within_coherence"] is True
    assert factors == (3, 5)