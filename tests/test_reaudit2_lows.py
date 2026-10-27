# tests/test_reaudit2_lows.py
"""Regression tests for the re-audit-2 LOW fixes (#151-#159).

Python LOWs (#151, #152, #156, #157, #158, #159) are unit-tested here.
The RTL LOWs (#153, #154) are additive watchdog fallbacks that are DORMANT in
normal operation; they are covered by the iverilog self-checking testbench
(``tests/test_fpga_sim.py`` -> ``fpga/sim/tb_top_level.v``) still passing
unchanged -- a stuck-high ``mw_gate`` or never-asserting ``pll_busy`` now
escapes via the watchdog instead of hanging, but no normal scenario trips it.
#155 is a comment-only fix in adf4351_ctrl.v (no testable behaviour).
"""
from __future__ import annotations

import threading
import time

import numpy as np


# ---------------------------------------------------------------------------
# #151: /api/sensor/tensor real-path n_avg is bounded to 1024
# ---------------------------------------------------------------------------

def test_tensor_n_avg_clamped_to_1024():
    """#151: n_avg=100000 must clamp to 1024 (sim x8 -> n_eff=8192), not pass
    through to the real-path loop that runs n_shots serial RUN_ALL_ODMR
    round-trips. The clamp mirrors api_gyro/api_signal."""
    from api.routes_sensor import api_sensor_tensor
    # source=sim avoids the real-hardware path (no QSSC_FPGA_PORT needed); the
    # sim path still reports n_eff in the response so the clamp is observable.
    resp = api_sensor_tensor(source="sim", n_avg=100000, seed=42)
    assert isinstance(resp, dict), f"unexpected response: {type(resp)}"
    # n_eff = clamped_n_avg * 8 (sim) = 1024 * 8 = 8192
    assert resp.get("n_avg") == 8192, \
        f"n_avg not clamped to 1024 (sim x8 -> 8192): got {resp.get('n_avg')} (#151)"
    # a normal value is unchanged
    resp2 = api_sensor_tensor(source="sim", n_avg=500, seed=42)
    assert resp2.get("n_avg") == 4000, f"500 x8 = 4000, got {resp2.get('n_avg')}"


# ---------------------------------------------------------------------------
# #152: routes_fpga._fpga() cache creation is serialized by a lock
# ---------------------------------------------------------------------------

def test_fpga_cache_lock_serializes_creation(monkeypatch):
    """#152: N threads calling _fpga() concurrently (with a slow sim factory)
    must create the SimulatedFPGA EXACTLY ONCE. Without the lock, many threads
    see _FPGA is None during the slow ctor and each create one."""
    import api.routes_fpga as rf

    monkeypatch.setenv("QSSC_FPGA_PORT", "")   # sim path
    rf._FPGA = None
    created = {"n": 0}
    lock = threading.Lock()

    class _SlowSim:
        def __init__(self, profile="NV", seed=None):
            with lock:
                created["n"] += 1
            time.sleep(0.05)            # widen the race window

    monkeypatch.setattr("qssc.simulator.SimulatedFPGA", _SlowSim)
    try:
        results: list = []

        def worker():
            results.append(rf._fpga())

        ts = [threading.Thread(target=worker) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert created["n"] == 1, \
            f"cache created {created['n']} times -> _fpga() lock missing (#152)"
        assert all(r is results[0] for r in results), "callers got different handles"
    finally:
        rf._FPGA = None


# ---------------------------------------------------------------------------
# #156: _n_prime_symbols recomputed from the ACTUAL (capped) register width
# ---------------------------------------------------------------------------

def test_n_prime_symbols_recomputed_after_register_cap():
    """#156: when explicit sites cap the register below the requested
    n_nv_per_pixel, _n_prime_symbols + _prime_snr must follow the ACTUAL width
    (len(sites)), not the requested width. Before the fix the prime SNR factor
    was understated ~2x when the gradient caps the register."""
    from qssc.backend import QSSCBackend
    from qssc.simulator_site import auto_align
    from qssc.prime_readout import prime_noise_rejection

    # request 10 NVs but hand in only 5 explicit sites -> the register is
    # capped to 5 (the explicit-sites branch sets n_nv_per_pixel = len(sites)).
    sites = auto_align("NV", n_nv_per_pixel=10, register=True,
                       rng=np.random.default_rng(0))[:5]
    be = QSSCBackend(profile="NV", sites=sites, n_nv_per_pixel=10,
                 prime_readout=True, n_noise_bins=64)
    assert be.n_nv_per_pixel == 5, "fixture: explicit sites should cap to 5"
    assert be._n_prime_symbols == 5, \
        f"_n_prime_symbols should be 5 (capped width), got {be._n_prime_symbols} (#156)"
    assert be._n_prime_symbols != 10, "regressed: using the requested width 10"
    expected_snr = prime_noise_rejection(5, 64)
    assert abs(be._prime_snr - expected_snr) < 1e-9, \
        f"_prime_snr should be prime_noise_rejection(5,64)={expected_snr}, got {be._prime_snr}"
    # prime_noise_rejection = bins/symbols, so the OLD code (10 symbols) gave
    # 64/10 = 6.4 -- the ~2x understatement vs the correct 64/5 = 12.8.
    assert prime_noise_rejection(10, 64) < expected_snr


# ---------------------------------------------------------------------------
# #157: noise_summary.effective_readout_error matches _effective_readout_err
# ---------------------------------------------------------------------------

def test_noise_summary_matches_effective_readout_err():
    """#157: in the factor>4 + prime_snr>1 regime where the OLD clip-first
    formula disagreed with the noise model, noise_summary must now report the
    SAME effective readout error as _effective_readout_err (the value actually
    used in the noise model)."""
    from qssc.backend import QSSCBackend
    from qssc.simulator_site import auto_align
    from qssc.noise_filters import NoiseEnvironment

    # a harsh env so _readout_noise_factor > 4 (the regime where the old
    # noise_summary clipped env_ro to base*4 then divided by prime_snr, while
    # the model divided first then clipped).
    env = NoiseEnvironment(vibration_counts=3000.0, emi_counts=2000.0,
                           temp_drift_counts=1000.0, pressure_counts=0.0)
    sites = auto_align("NV", n_nv_per_pixel=5, register=True,
                       rng=np.random.default_rng(0))
    # uniform readout_err so noise_summary's mean(base) == _effective_readout_err's base
    for s in sites:
        s.readout_err = 0.005
    be = QSSCBackend(profile="NV", sites=sites, n_nv_per_pixel=5,
                 prime_readout=True, n_noise_bins=64, noise_env=env)
    assert be._readout_noise_factor > 4.0, \
        f"fixture needs factor>4, got {be._readout_noise_factor}"
    assert be._prime_snr > 1.0
    summ = be.noise_summary()
    eff_model = be._effective_readout_err(0)
    assert abs(summ["effective_readout_error"] - eff_model) < 1e-6, \
        f"noise_summary {summ['effective_readout_error']} != model {eff_model} (#157)"
    # sanity: the effective error is below base*4 (prime denoiser helped)
    assert summ["effective_readout_error"] < 0.005 * 4.0


# ---------------------------------------------------------------------------
# #158: prime_decompose p==2 factor is 1 (not 2)
# ---------------------------------------------------------------------------

def test_prime_decompose_p2_factor_is_one_not_two():
    """#158: for p=2 the cos reference is +-1 (sum cos^2 = n, not n/2), so the
    recovered amplitude must be A, not 2A. Matches lockin_iq_result's p==2
    branch. p=3 (sum cos^2 = n/2) still uses factor 2 -> A."""
    from qssc.prime_readout import prime_decompose
    A = 100.0
    # p=2: cos(pi*t) = (-1)^t, 50 whole periods
    t2 = np.arange(2 * 50)
    sig2 = A * np.cos(2.0 * np.pi * t2 / 2.0)
    out2 = prime_decompose(sig2, tau_ns=1.0, primes=[2])
    assert abs(out2[2] - A) < 1e-6, \
        f"p=2 amplitude should be A={A}, got {out2[2]} (factor-2 regression #158)"
    # p=3: cos(2*pi*t/3), 50 whole periods -> factor 2 -> A (unchanged)
    t3 = np.arange(3 * 50)
    sig3 = A * np.cos(2.0 * np.pi * t3 / 3.0)
    out3 = prime_decompose(sig3, tau_ns=1.0, primes=[3])
    assert abs(out3[3] - A) < 1e-6, \
        f"p=3 amplitude should be A={A}, got {out3[3]}"


# ---------------------------------------------------------------------------
# #159: dipole_coupling_hz r=2 nm example is ~6.5 MHz (not ~16 MHz)
# ---------------------------------------------------------------------------

def test_dipole_coupling_hz_r2nm_is_6_5_mhz():
    """#159: the function returns ~6.5 MHz at r=2 nm (the formula is correct;
    only the docstring example was wrong). Pin the value so a future doc
    'fix' that reverts to 16 MHz is caught, and assert the corrected example
    text is present in the docstring."""
    import inspect
    from qssc.dipole import dipole_coupling_hz
    v2 = dipole_coupling_hz(2.0)
    # ~6.5 MHz (mu0/4pi * moment^2 / (h * (2nm)^3))
    assert 6.0e6 < v2 < 7.0e6, f"r=2nm should be ~6.5 MHz, got {v2:.3e} (#159)"
    # r=3 nm ~1.9 MHz and r=5 nm ~415 kHz (the unchanged examples) stay correct
    v3 = dipole_coupling_hz(3.0)
    assert 1.7e6 < v3 < 2.1e6, f"r=3nm should be ~1.9 MHz, got {v3:.3e}"
    v5 = dipole_coupling_hz(5.0)
    assert 3.9e5 < v5 < 4.4e5, f"r=5nm should be ~415 kHz, got {v5:.3e}"
    doc = inspect.getdoc(dipole_coupling_hz)
    assert "~6.5 MHz" in doc, "docstring should now say ~6.5 MHz for r=2 nm (#159)"
    assert "~16 MHz" not in doc, "docstring still has the wrong ~16 MHz example (#159)"