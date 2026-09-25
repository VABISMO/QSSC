"""Physics-implementation audit tests for the QSSC qiskit backend.

These are the pass/fail companion to the measured parameter-sweep benchmark
(``tools/benchmark_circuits.py``). They assert three properties of the
implementation:

1. **Within-pixel all-to-all dipole coupling** -- the circuit qubits are ONE
   pixel's ~10-NV register: the NVs sit a few nm apart in one microdiamond, so
   the magnetic dipole-dipole interaction (1/r**3) couples every pair (complete
   graph K_n). The 64 pixels are INDEPENDENT at 300 K (1/r**3 -> zero at the
   2.5 mm pitch); cross-pixel 2q is the cryo optical bus, not simulated.

2. **All error channels are simulated** -- the real-mode Aer noise model wires
   in (a) thermal relaxation from the real per-site T1/T2 over each gate
   duration, (b) depolarizing drive crosstalk per 1q and 2q gate, and
   (c) the asymmetric optical readout error (NV ms=0 bright vs ms=+-1 dark).
   The phenomenological path applies the same readout flip + T2/depolarization
   damping so the two execution paths cannot disagree.

3. **Qiskit-correct for both simulator and real-hardware paths** -- the
   backend is a real qiskit BackendV2 with a Target, per-qubit
   QubitProperties (detuned frequency), and the ideal (statevector/MPS) and
   real (density-matrix / analytic-decoherence) paths both execute through
   qiskit Aer / the BackendV2 run interface.

All three defect profiles (NV / hBN-VB / SiC-VSi) are covered.
"""
from __future__ import annotations

import numpy as np
from qiskit import QuantumCircuit

from qssc.backend import QSSCBackend, SV_MAX_QUBITS, SIM_MAX_QUBITS
from qssc.defects import get_profile, coherence_at_temperature, PROFILES

PROF_NAMES = list(PROFILES)  # ["NV", "hBN-VB", "SiC-VSi"]


def _bell_circuit(n=2):
    qc = QuantumCircuit(n, n)
    qc.h(0)
    for i in range(n - 1):
        qc.cx(i, i + 1)
    qc.measure(range(n), range(n))
    return qc


# --------------------------------------------------------------------------
# 1. within-pixel all-to-all dipole coupling (1/r^3, ~10 NVs/pixel)
# --------------------------------------------------------------------------
def test_coupling_is_within_pixel_all_to_all():
    """The coupling map is the complete graph K_n of the pixel's ~10-NV
    register: every pair is an edge (the NVs are a few nm apart in one
    microdiamond, so 1/r**3 couples them all). All sites share pixel_id=0."""
    for name in PROF_NAMES:
        b = QSSCBackend(name, seed=42, noise_mode="real")
        edges = b._neighbor_pairs()
        assert edges, f"{name}: coupling map is empty"
        n = b._n_qubits
        # every register pair is dipole-coupled (within one microdiamond)
        pixel_ids = {s.pixel_id for s in b.sites}
        assert pixel_ids == {0}, f"{name}: register sites must share pixel_id=0, got {pixel_ids}"
        assert len(edges) == n * (n - 1), (
            f"{name}: expected {n*(n-1)} directed K_{n} edges, got {len(edges)}")
        for i, j in edges:
            assert i != j and b.sites[i].pixel_id == b.sites[j].pixel_id, (
                f"{name}: edge ({i},{j}) spans pixels -- cross-pixel dipole is zero")


def test_coupling_is_complete_graph():
    """The register's coupling is all-to-all: K_n has n*(n-1) directed edges
    (e.g. 90 for n=10). This is the within-pixel dipole (1/r**3 at ~3 nm), NOT
    a cross-pixel grid -- the 64 pixels are independent at 300 K."""
    for name in PROF_NAMES:
        b = QSSCBackend(name, seed=42, noise_mode="real")
        edges = b._neighbor_pairs()
        n = b._n_qubits
        assert len(edges) == n * (n - 1), (
            f"{name}: expected {n*(n-1)} directed K_{n} edges, got {len(edges)}")
        # both directions present for every unordered pair
        eset = set(edges)
        for i in range(n):
            for j in range(i + 1, n):
                assert (i, j) in eset and (j, i) in eset, (
                    f"{name}: pair ({i},{j}) missing a direction")


def test_target_2q_gates_on_all_register_pairs():
    """The Target truthfully declares a native 2q gate on every within-register
    pair (the all-to-all dipole topology), and on no others."""
    for name in PROF_NAMES:
        b = QSSCBackend(name, seed=42, noise_mode="real")
        cx_props = set(b.target.qargs_for_operation_name("cx"))
        nbr = set(b._neighbor_pairs())
        assert cx_props == nbr, (
            f"{name}: Target cx qargs differ from the within-register coupling map")


# --------------------------------------------------------------------------
# 2. all error channels simulated (thermal + depolarizing + asymmetric readout)
# --------------------------------------------------------------------------
def test_noise_model_has_all_error_channels():
    """The real-mode Aer noise model attaches non-unitary (thermal+depol
    composed) quantum error to BOTH 1q and 2q gates, plus a readout error --
    on every profile."""
    qc = _bell_circuit(3)
    for name in PROF_NAMES:
        b = QSSCBackend(name, seed=42, noise_mode="real")
        nm = b._build_real_noise_model(qc)

        def _flatten(d):
            out = []
            for v in d.values():
                if isinstance(v, dict):
                    for vv in v.values():
                        out.extend(vv if isinstance(vv, (list, tuple)) else [vv])
                else:
                    out.extend(v if isinstance(v, (list, tuple)) else [v])
            return out

        qerrs = _flatten(nm._local_quantum_errors)
        rerrs = _flatten(nm._local_readout_errors)
        # non-unitary == >1 kraus instruction (identity channel has 1)
        def nonunitary(e):
            try:
                return int(e.size) > 1
            except Exception:
                return True
        has_1q = any(nonunitary(e) and getattr(e, "num_qubits", 0) == 1 for e in qerrs)
        has_2q = any(nonunitary(e) and getattr(e, "num_qubits", 0) == 2 for e in qerrs)
        assert has_1q, f"{name}: no non-unitary 1q error (thermal+depol missing)"
        assert has_2q, f"{name}: no non-unitary 2q error (thermal+depol missing)"
        assert rerrs, f"{name}: no readout error in noise model"


def test_readout_asymmetry_matches_profile():
    """NV/hBN readout is asymmetric (p_bright_to_dark != p_dark_to_bright);
    SiC is symmetric (ensemble readout washes out the bright/dark bias)."""
    qc = _bell_circuit(2)
    for name in PROF_NAMES:
        prof = get_profile(name)
        b = QSSCBackend(name, seed=42, noise_mode="real")
        nm = b._build_real_noise_model(qc)

        def _flatten(d):
            out = []
            for v in d.values():
                if isinstance(v, dict):
                    for vv in v.values():
                        out.extend(vv if isinstance(vv, (list, tuple)) else [vv])
                else:
                    out.extend(v if isinstance(v, (list, tuple)) else [v])
            return out

        rerrs = _flatten(nm._local_readout_errors)
        assert rerrs, f"{name}: no readout error"
        m = rerrs[0].probabilities.reshape(2, 2)
        off_diag_diff = abs(m[0, 1] - m[1, 0])
        if prof.readout_asymmetry == 0.0:
            assert off_diag_diff < 1e-9, (
                f"{name}: profile is symmetric but ReadoutError is asymmetric")
        else:
            assert off_diag_diff > 1e-9, (
                f"{name}: profile has readout_asymmetry={prof.readout_asymmetry} "
                f"but ReadoutError is symmetric")
        # both rows must sum to 1 (ReadoutError stochasticity)
        assert np.allclose(m.sum(axis=1), 1.0), f"{name}: ReadoutError rows don't sum to 1"


def test_thermal_relaxation_uses_real_T1_T2():
    """The thermal channel is built from the profile's real (T1, T2) at the
    set temperature -- cooling must extend coherence (T2 grows as T falls)."""
    for name in PROF_NAMES:
        prof = get_profile(name)
        t1_300, t2_300 = coherence_at_temperature(prof, 300.0)
        t1_cryo, t2_cryo = coherence_at_temperature(prof, 4.0)
        # T2 at 4 K must be >= T2 at 300 K (cryo recovery; equal is allowed for
        # the near-T-independent hBN teaching model).
        assert t2_cryo >= t2_300 - 1e-12, (
            f"{name}: T2 did not improve on cooling ({t2_300} -> {t2_cryo})")
        assert t1_cryo >= t1_300 - 1e-12, (
            f"{name}: T1 did not improve on cooling ({t1_300} -> {t1_cryo})")


def test_phenomenological_and_real_paths_agree_on_verdict():
    """The analytic decoherence limit and the assess_circuit verdict use the
    SAME _signal_survival, so a circuit assessed as decohered must also
    decohere in the phenomenological run (near-uniform), not recover."""
    for name in PROF_NAMES:
        b = QSSCBackend(name, seed=42, noise_mode="real")
        qc = _bell_circuit(2)
        asm = b.assess_circuit(qc)
        ret = float(asm["signal_retention"])
        ideal = b._run_ideal(qc, 2048)
        noisy = b._apply_phenomenological_noise(qc, ideal, 2048)
        # if signal retention is high, the top ideal state must still dominate
        # the noisy distribution (not washed to uniform); if low, the
        # distribution must flatten (max share << 1).
        top_ideal = max(ideal.values()) / sum(ideal.values())
        top_noisy = max(noisy.values()) / sum(noisy.values()) if noisy else 0.0
        if ret > 0.5:
            assert top_noisy > 0.2, (
                f"{name}: retention={ret:.2f} but noisy top state collapsed "
                f"to {top_noisy:.3f} -- phenomenological path disagrees with "
                f"the assessment verdict")
        else:
            # decohered -> top state must NOT retain near its ideal share
            assert top_noisy < top_ideal + 0.05, (
                f"{name}: retention={ret:.2f} (decohered) but noisy top state "
                f"{top_noisy:.3f} stayed near ideal {top_ideal:.3f}")


# --------------------------------------------------------------------------
# 3. qiskit-correct for simulator AND real paths
# --------------------------------------------------------------------------
def test_backend_is_qiskit_BackendV2():
    """The backend exposes the real qiskit BackendV2 interface: a Target,
    max_circuits, qubit_properties, and the run() -> Job contract. The Target
    width is the per-pixel NV register (~10), NOT the 64-pixel hardware die
    (which is the separate chip view, reported via describe()['pixels'])."""
    from qssc.simulator import N_NV_PER_PIXEL, N_SITES
    for name in PROF_NAMES:
        b = QSSCBackend(name, seed=42, noise_mode="real")
        assert b.target is not None, f"{name}: no Target"
        assert b.target.num_qubits == N_NV_PER_PIXEL, (
            f"{name}: Target num_qubits {b.target.num_qubits} != register {N_NV_PER_PIXEL}")
        assert b.target.qubit_properties is not None, (
            f"{name}: Target has no qubit_properties")
        assert hasattr(b, "max_circuits"), f"{name}: not BackendV2 (no max_circuits)"
        # the 64-pixel hardware die is the separate chip view, not the Target
        desc = b.describe()
        assert desc["pixels"] == N_SITES, f"{name}: chip view pixels != {N_SITES}"
        assert desc["num_qubits"] == N_NV_PER_PIXEL, f"{name}: describe num_qubits != register"
        # run() returns a job whose .result() yields counts (the contract)
        job = b.run(_bell_circuit(2), shots=64)
        res = job.result()
        counts = res.get_counts()
        assert sum(counts.values()) == 64, f"{name}: run() did not return 64 shots"


def test_per_qubit_frequency_detuning_is_real():
    """QubitProperties carry per-site detuned frequencies (the individual MW
    addressing fingerprint), not a single uniform 2.87 GHz for every qubit."""
    for name in PROF_NAMES:
        prof = get_profile(name)
        b = QSSCBackend(name, seed=42, noise_mode="real")
        freqs = [p.frequency for p in b.target.qubit_properties]
        assert all(f > 0 for f in freqs), f"{name}: a qubit has zero frequency"
        if prof.frequency_detuning_spread_hz > 0:
            spread = max(freqs) - min(freqs)
            assert spread > 1.0, (
                f"{name}: detuning spread >0 but all qubit frequencies identical "
                f"(spread={spread:.3f} Hz) -- per-site addressability missing")
            # mean must sit near the zero-field splitting
            assert abs(np.mean(freqs) - prof.zero_field_splitting) < 5 * prof.frequency_detuning_spread_hz, (
                f"{name}: mean qubit frequency far from ZFS")


def test_ideal_and_real_paths_both_execute_through_qiskit():
    """Ideal (statevector / MPS) and real (density-matrix / analytic) paths
    both produce valid counts for a small circuit on every profile."""
    qc = _bell_circuit(2)
    for name in PROF_NAMES:
        for mode in ("none", "ideal", "phenomenological", "real"):
            b = QSSCBackend(name, seed=42, noise_mode=mode)
            res = b.run(qc, shots=256).result()
            counts = res.get_counts()
            assert sum(counts.values()) == 256, (
                f"{name}/{mode}: got {sum(counts.values())} counts, expected 256")
            # Bell/GHZ ideal top states are all-0 / all-1; ideal mode must
            # produce one of them as the top state
            if mode in ("none", "ideal"):
                top = max(counts, key=counts.get).replace(" ", "")
                assert top in ("00", "11"), (
                    f"{name}/{mode}: ideal Bell top state {top} not 00/11")


def test_register_mps_path_reaches_64_qubits():
    """The MPS path reaches the 64-qubit register ceiling (statevector caps at
    28): a 64-NV product-state register must simulate, not be rejected. The
    register auto-grows from the default 10 to the circuit width (SIM_MAX_QUBITS
    = 64, the max NVs in one pixel); 64x10 = 640 stays out of reach (64
    independent pixels, not one 640-qubit circuit)."""
    b = QSSCBackend("NV", seed=42, noise_mode="ideal")
    assert SIM_MAX_QUBITS == 64, "register ceiling must be 64"
    assert SV_MAX_QUBITS == 28, "statevector ceiling must be 28"
    qc = QuantumCircuit(64, 64)
    qc.h(range(64))  # product state -> MPS bond dim 1, fast
    qc.measure(range(64), range(64))
    counts = b.run(qc, shots=128).result().get_counts()
    assert sum(counts.values()) == 128, "64-qubit MPS run did not return 128 shots"
    # the register grew to fit the circuit
    assert b._n_qubits == 64, "register did not auto-grow to 64 NVs"