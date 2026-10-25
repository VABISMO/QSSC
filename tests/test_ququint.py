"""Tests for the ququint GF(5) sim layer (all SIM-ONLY, no hardware claims)."""
import numpy as np
import pytest

from qssc.ququint import (
    OMEGA,
    apply_depolarizing,
    basis_state,
    ccz5_matrix,
    ccz_qubit_matrix,
    f5,
    qssc_level_structure,
    survived_fidelity,
    x5,
    z5,
)


def test_x5_unitary_and_shift():
    m = x5()
    assert np.allclose(m.conj().T @ m, np.eye(5))
    assert np.allclose(m @ np.array([1, 0, 0, 0, 0], dtype=complex),
                       [0, 1, 0, 0, 0])


def test_z5_phases():
    m = z5()
    assert np.allclose(m.conj().T @ m, np.eye(5))
    assert m[2, 2] == pytest.approx(OMEGA ** 2)


def test_f5_is_dft():
    m = f5()
    assert np.allclose(m.conj().T @ m, np.eye(5))
    # F|0> uniform
    out = m @ np.eye(5)[0]
    assert np.allclose(np.abs(out), 1 / np.sqrt(5))


def test_ccz5_unitary_and_phase():
    m = ccz5_matrix()
    assert np.allclose(m.conj().T @ m, np.eye(125))
    ph = m @ basis_state([1, 1, 1])
    assert abs(ph[31] - OMEGA) < 1e-9          # 1*25 + 1*5 + 1 = 31
    assert abs(ph[0]) < 1e-9                   # orthogonal component stays 0
    z0 = ccz5_matrix() @ basis_state([0, 0, 0])
    assert abs(z0[0] - 1.0) < 1e-9             # |000> unchanged (phase 1)


def test_ccz5_is_cubic_phase():
    m = np.diag(ccz5_matrix())
    for a in range(5):
        for b in range(5):
            for c in range(5):
                assert abs(m[a * 25 + b * 5 + c]
                           - OMEGA ** ((a * b * c) % 5)) < 1e-9


def test_depolarizing_preserves_norm_and_identity_at_p0():
    rng = np.random.default_rng(42)
    s = basis_state([2, 3, 1])
    out = apply_depolarizing(s, rng, 0.0)
    assert np.allclose(out, s)
    rng = np.random.default_rng(42)
    for _ in range(50):
        out = apply_depolarizing(s, rng, 0.9)
        assert np.isclose(np.vdot(out, out).real, 1.0)


def test_depolarizing_destroys_fidelity_at_high_p():
    rng = np.random.default_rng(42)
    prep = basis_state([1, 1, 1])
    ccz = ccz5_matrix()
    f_low = survived_fidelity(ccz, prep, 4, 0.001, 500, rng)
    f_high = survived_fidelity(ccz, prep, 4, 0.9, 500, rng)
    assert f_low > 0.9
    assert f_high < 0.3          # near maximally mixed: |<ideal|mix>|^2 ~ 1/125


def test_qubit_ccz_matrix():
    m = ccz_qubit_matrix()
    assert m[7, 7] == -1
    assert np.allclose(m.conj().T @ m, np.eye(8))


def test_level_structure_documents_no_gf5_hardware():
    ls = qssc_level_structure()
    assert ls["native_gf5_system"] is None
    assert "simulation-only" in ls["conclusion"]