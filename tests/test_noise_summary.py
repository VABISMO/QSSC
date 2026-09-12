"""Per-run noise measurement — the prime-on vs prime-off comparison
(Invention 2+ denoiser wired into the noise model).

``noise_summary()`` reports the base optical readout error, the prime SNR
improvement factor (n_noise_bins / n_symbols), and the EFFECTIVE readout
error after the coprime matched-filter denoiser. With prime readout on, the
real Aer noise model and the phenomenological readout path both use the
reduced effective readout error; with prime off (or SNR == 1) the effective
error equals the base, so every existing noise realization is unchanged.
"""
from __future__ import annotations

from qiskit import QuantumCircuit

from qssc.backend import QSSCBackend
from qssc.prime_readout import PRIME_SYMBOLS, prime_noise_rejection


def _bell(n=2):
    qc = QuantumCircuit(n, n)
    qc.h(0)
    for i in range(n - 1):
        qc.cx(i, i + 1)
    qc.measure(range(n), range(n))
    return qc


def test_noise_summary_prime_off_is_base():
    """Prime off: effective readout error == base, SNR factor == 1."""
    b = QSSCBackend("NV", seed=42, noise_mode="real", prime_readout=False)
    s = b.noise_summary()
    assert s["prime_readout"] is False
    assert s["prime_snr_factor"] == 1.0
    assert s["effective_readout_error"] == s["readout_error"]
    # the error budget fields are present and finite
    assert s["depol_1q"] >= 0.0
    assert s["depol_2q"] >= 0.0
    assert s["t2_us"] > 0.0


def test_noise_summary_prime_on_reduces_readout_error():
    """Prime on: effective readout error < base, SNR factor > 1, and the
    reduction matches prime_noise_rejection(n_symbols, n_noise_bins)."""
    b = QSSCBackend("NV", seed=42, noise_mode="real", prime_readout=True,
                    n_noise_bins=64)
    s = b.noise_summary()
    assert s["prime_readout"] is True
    assert s["prime_snr_factor"] > 1.0
    assert s["effective_readout_error"] < s["readout_error"]
    expected_snr = prime_noise_rejection(s["n_symbols"], s["n_noise_bins"])
    assert abs(s["prime_snr_factor"] - round(expected_snr, 4)) < 1e-6
    # effective = base / snr (clamped), within float rounding
    assert abs(s["effective_readout_error"]
               - round(s["readout_error"] / expected_snr, 6)) < 1e-4


def test_n_symbols_capped_at_prime_symbol_count():
    """The number of prime address channels is min(register width, 16)."""
    b = QSSCBackend("NV", seed=42, noise_mode="real", prime_readout=True,
                    n_nv_per_pixel=64)
    s = b.noise_summary()
    assert s["n_symbols"] == len(PRIME_SYMBOLS)


def test_prime_readout_changes_counts():
    """A real-mode run with prime on yields a measurably different readout
    distribution than prime off (the effective readout error is lower, so
    fewer bit-flips). Same seed, same circuit -> the only difference is the
    denoiser."""
    qc = _bell(3)
    off = QSSCBackend("NV", seed=7, noise_mode="real", prime_readout=False)
    on = QSSCBackend("NV", seed=7, noise_mode="real", prime_readout=True,
                     n_noise_bins=64)
    c_off = off.run(qc, shots=4096).result().get_counts()
    c_on = on.run(qc, shots=4096).result().get_counts()
    # the noise summaries confirm the effective error differs
    assert off.noise_summary()["effective_readout_error"] > \
        on.noise_summary()["effective_readout_error"]
    # and the counts are not identical (fewer flips with prime on)
    assert c_off != c_on


def test_prime_readout_off_preserves_existing_realization():
    """Prime off (the default) must not perturb the historical noise
    realization: a backend built without the new params produces the same
    counts as one built with prime_readout=False explicitly."""
    qc = _bell(3)
    legacy = QSSCBackend("NV", seed=7, noise_mode="real")
    explicit = QSSCBackend("NV", seed=7, noise_mode="real",
                           prime_readout=False)
    assert legacy.run(qc, shots=2048).result().get_counts() == \
        explicit.run(qc, shots=2048).result().get_counts()