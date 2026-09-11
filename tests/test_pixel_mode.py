"""Pixel mode selector (ensemble vs register) — the panel "multiqbit mode"
switch (Invention: per-pixel NV register).

``register`` mode (default): the circuit qubits are one pixel's N-NV register
(within-pixel all-to-all dipole 2q); N is the MEASURED resolvable ODMR line
count when ``register_line_freqs_hz`` is given, else the nominal default.

``ensemble`` mode: the 64 chip pixels ARE the circuit qubits (one ensemble
qubit per pixel -- the bulk-microdiamond ODMR collapse). Cross-pixel 2q is
zero at the 2.5 mm pitch, so the coupling map is empty and 2q circuits are
rejected. 1q-only circuits run on the 64 sites.
"""
from __future__ import annotations

import numpy as np
import pytest
from qiskit import QuantumCircuit

from qssc.backend import QSSCBackend
from qssc.defects import get_profile
from qssc.simulator import auto_align


def test_ensemble_mode_64_sites_no_2q():
    """Ensemble mode builds 64 chip-pixel circuit qubits with an empty coupling
    map (no within-pixel 2q at 300 K)."""
    b = QSSCBackend("NV", seed=42, pixel_mode="ensemble", noise_mode="real")
    assert b.pixel_mode == "ensemble"
    assert b.num_qubits == 64
    assert b.n_nv_per_pixel == 1
    assert b._neighbor_pairs() == []
    desc = b.describe()
    assert desc["pixel_mode"] == "ensemble"
    assert "none at 300 K" in desc["within_pixel_2q"]


def test_ensemble_mode_1q_circuit_runs():
    """A 1q-only circuit runs in ensemble mode (64 chip pixels)."""
    b = QSSCBackend("NV", seed=42, pixel_mode="ensemble", noise_mode="real")
    qc = QuantumCircuit(1)
    qc.h(0)
    qc.measure_all()
    res = b.run(qc, shots=64).result()
    counts = res.get_counts()
    assert sum(counts.values()) == 64


def test_ensemble_mode_2q_circuit_rejected():
    """A 2q circuit in ensemble mode is rejected (no within-pixel 2q at 300 K),
    not silently executed as noiseless CXs."""
    b = QSSCBackend("NV", seed=42, pixel_mode="ensemble", noise_mode="real")
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure_all()
    with pytest.raises(ValueError, match="ensemble mode has no within-pixel 2q"):
        b.run(qc, shots=64).result()


def test_register_mode_default_all_to_all():
    """Register mode (default) keeps the within-pixel all-to-all dipole K_n."""
    b = QSSCBackend("NV", seed=42, pixel_mode="register", noise_mode="real")
    assert b.pixel_mode == "register"
    # nominal default register width, complete graph K_n
    n = b.n_nv_per_pixel
    pairs = b._neighbor_pairs()
    assert len(pairs) == n * (n - 1)  # directed both ways
    desc = b.describe()
    assert desc["pixel_mode"] == "register"
    assert "dipole CZ" in desc["within_pixel_2q"]


def test_register_mode_measured_n():
    """When register_line_freqs_hz is given, N = len(lines) and the NVs sit on
    the measured frequency grid (the truth from decompose_odmr), not the
    synthetic 6 MHz grid."""
    prof = get_profile("NV")
    zfs = prof.zero_field_splitting
    measured = [zfs - 6e6, zfs, zfs + 6e6]  # 3 resolved lines, 6 MHz apart
    b = QSSCBackend("NV", seed=42, pixel_mode="register", noise_mode="real",
                    register_line_freqs_hz=measured)
    assert b.n_nv_per_pixel == 3
    assert b.num_qubits == 3
    assert b.target.num_qubits == 3
    assert b.describe()["register_n_measured"] is True
    # the 3 NV frequencies cluster around the measured line centres (within the
    # per-NV strain jitter, ~2 MHz spread)
    freqs = sorted(s.frequency for s in b.sites)
    for f, target in zip(freqs, measured):
        assert abs(f - target) < 5e6, f"freq {f} not near measured {target}"


def test_register_mode_measured_n_overrides_request():
    """A requested n_nv_per_pixel is overridden by the measured line count."""
    measured = [2.87e9, 2.876e9]  # 2 lines
    b = QSSCBackend("NV", seed=42, pixel_mode="register", noise_mode="real",
                    n_nv_per_pixel=10, register_line_freqs_hz=measured)
    assert b.n_nv_per_pixel == 2  # measured truth beats the request of 10


def test_explicit_sites_keep_kn_regardless_of_pixel_mode():
    """Backward-compat: explicit ``sites`` keep the all-to-all coupling whether
    pixel_mode is register or ensemble (existing tests rely on this)."""
    sites = auto_align(get_profile("NV"), register=True, n_nv_per_pixel=4,
                       rng=np.random.default_rng(0))
    for mode in ("register", "ensemble"):
        b = QSSCBackend("NV", sites=sites, pixel_mode=mode, noise_mode="real")
        # explicit sites -> K_n coupling, NOT the ensemble empty map
        assert b._neighbor_pairs() != []
        assert b.num_qubits == 4
        # a 2q circuit runs even if pixel_mode="ensemble" (explicit-sites path)
        qc = QuantumCircuit(2)
        qc.cx(0, 1)
        qc.measure_all()
        res = b.run(qc, shots=64).result()
        assert sum(res.get_counts().values()) == 64


def test_invalid_pixel_mode_raises():
    with pytest.raises(ValueError, match="pixel_mode must be"):
        QSSCBackend("NV", pixel_mode="gradient", noise_mode="real")


def test_ensemble_nuclear_memory_forced_off():
    """Nuclear memory is a per-NV-register concept; in ensemble mode it is
    forced off (no within-pixel register to pair 13C with)."""
    b = QSSCBackend("NV", seed=42, pixel_mode="ensemble", noise_mode="real",
                    nuclear_memory=True)
    assert b._nuclear_memory is False