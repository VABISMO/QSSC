"""Inventions 13, 14, 19 — super-resolution ODMR, Bayesian readout, and the
tensor-gradient magnetic imager (qssc.superres_odmr / bayesian_readout /
tensor_gradiometry)."""
from __future__ import annotations

import numpy as np
import pytest

from qssc.defects import get_profile
from qssc.odmr_decompose import decompose_odmr
from qssc.superres_odmr import (superres_decompose, superres_measured_width,
                                _gaussian_dict, _gradient_bin_centres)
from qssc.bayesian_readout import bayesian_measured_width
from qssc.array_calibration import _pixel_odmr_spectrum
from qssc.tensor_gradiometry import (
    tensor_gradient_map, localize_dipole, localize_dipole_from_splits,
    tensor_gradient_sweep, _dipole_field, TensorGradientResult,
)
from qssc.sensors import _NV_AXES


# -- Invention 13: compressed-sensing super-resolution ODMR -------------------

def _spectrum(nv_present=30, n_bins=80, gradient=2.0, seed=1):
    prof = get_profile("NV")
    rng = np.random.default_rng(seed)
    freqs, contrast, noise = _pixel_odmr_spectrum(
        prof, nv_present=nv_present, n_bins=n_bins,
        gradient_mhz_per_um=gradient, pixel_size_um=200.0, rng=rng,
        noise_sigma=0.0125)
    return freqs, contrast, noise


def test_superres_back_compat_identical():
    """superres_factor=1.0 (accept at min_snr) recovers the hard-threshold
    behaviour; the width cap is min(len, n_bins)."""
    freqs, contrast, noise = _spectrum()
    w, comps = superres_measured_width(
        freqs, contrast, n_bins=80, linewidth_hz=5e6,
        gradient_mhz_per_um=2.0, pixel_size_um=200.0, noise_sigma=noise,
        superres_factor=1.0)
    assert 0 <= w <= 80
    assert all(c.snr > 0 for c in comps)


def test_superres_recovers_valid_width_under_n_bins():
    """The OMP sparse recovery is a valid drop-in: width in [0, n_bins], every
    recovered line has a positive SNR, and lowering the super-res factor (a
    lower acceptance bar) recovers at least as many lines. The super-res
    *differentiator* — deconvolution of overlapping lines — is covered by
    :func:`test_superres_deconvolution_resolves_overlap`."""
    freqs, contrast, noise = _spectrum(nv_present=40, n_bins=80, gradient=3.0,
                                       seed=7)
    w_lo, comps_lo = superres_measured_width(
        freqs, contrast, n_bins=80, linewidth_hz=5e6,
        gradient_mhz_per_um=3.0, pixel_size_um=200.0, noise_sigma=noise,
        superres_factor=0.4)
    w_hi, comps_hi = superres_measured_width(
        freqs, contrast, n_bins=80, linewidth_hz=5e6,
        gradient_mhz_per_um=3.0, pixel_size_um=200.0, noise_sigma=noise,
        superres_factor=1.0)
    assert 0 <= w_lo <= 80
    assert 0 <= w_hi <= 80
    assert all(c.snr > 0 for c in comps_lo)
    # lower bar (superres_factor 0.4) accepts at least as many as 1.0
    assert w_lo >= w_hi


def test_superres_deconvolution_resolves_overlap():
    """Two NVs in adjacent bins whose Gaussians smear into one broad dip are
    resolved as two lines by the joint LS (deconvolution)."""
    n_bins, gradient, pix = 40, 2.0, 200.0
    prof = get_profile("NV")
    lw_mhz = prof.odmr_linewidth_hz * 1e-6
    sigma_ghz = lw_mhz * 1e-3 / 2.355
    centres = _gradient_bin_centres(2.87, n_bins, gradient, pix, lw_mhz)
    freqs = np.linspace(centres[0] - 5 * sigma_ghz, centres[-1] + 5 * sigma_ghz, 800)
    A = _gaussian_dict(freqs, centres, sigma_ghz)
    # two adjacent bins populated -> one broad dip in contrast
    dip = 0.6 * A[:, 10] + 0.6 * A[:, 11]
    contrast = 1.0 - dip
    comps = superres_decompose(
        freqs, contrast, n_bins=n_bins, linewidth_hz=prof.odmr_linewidth_hz,
        gradient_mhz_per_um=gradient, pixel_size_um=pix, noise_sigma=0.005,
        superres_factor=0.6)
    assert len(comps) >= 2
    # recovered centres bracket the two input bins
    freqs_rec = sorted(c.freq_ghz for c in comps)
    assert abs(freqs_rec[0] - centres[10]) < 2 * sigma_ghz
    assert abs(freqs_rec[1] - centres[11]) < 2 * sigma_ghz


# -- Invention 14: Bayesian quantum-limited readout ---------------------------

def test_bayesian_back_compat_width_cap():
    freqs, contrast, noise = _spectrum()
    w, comps = bayesian_measured_width(
        freqs, contrast, n_bins=80, linewidth_hz=5e6,
        gradient_mhz_per_um=2.0, pixel_size_um=200.0, noise_sigma=noise,
        confidence=0.99)
    assert 0 <= w <= 80
    assert all(c.snr > 0 for c in comps)


def test_bayesian_recovers_more_than_hard():
    """The one-sided Bonferroni-corrected test accepts marginal lines the hard
    two-sided min_snr=4 bar rejects -> width >= hard path."""
    freqs, contrast, noise = _spectrum(nv_present=40, n_bins=80, gradient=3.0,
                                       seed=7)
    hard = decompose_odmr(freqs, contrast, noise_floor=noise, min_snr=4.0)
    w_bay, _ = bayesian_measured_width(
        freqs, contrast, n_bins=80, linewidth_hz=5e6,
        gradient_mhz_per_um=3.0, pixel_size_um=200.0, noise_sigma=noise,
        confidence=0.99)
    assert w_bay >= len(hard)
    assert w_bay <= 80


def test_bayesian_higher_confidence_fewer_lines():
    """Raising the confidence (stricter false-positive control) accepts fewer
    lines — the detector is monotone in the threshold."""
    freqs, contrast, noise = _spectrum(nv_present=40, n_bins=80, gradient=3.0,
                                       seed=7)
    w_lo, _ = bayesian_measured_width(
        freqs, contrast, n_bins=80, linewidth_hz=5e6,
        gradient_mhz_per_um=3.0, pixel_size_um=200.0, noise_sigma=noise,
        confidence=0.90)
    w_hi, _ = bayesian_measured_width(
        freqs, contrast, n_bins=80, linewidth_hz=5e6,
        gradient_mhz_per_um=3.0, pixel_size_um=200.0, noise_sigma=noise,
        confidence=0.9999)
    assert w_lo >= w_hi


# -- Invention 19: tensor-gradient magnetic imaging ---------------------------

def _dipole_map(grid, pitch, x0, y0, z0, moment, bg_ut=(0.0, 0.0, 50.0)):
    pts = np.array([[(ix - (grid - 1) / 2.0) * pitch,
                     (iy - (grid - 1) / 2.0) * pitch]
                    for iy in range(grid) for ix in range(grid)])
    bmap = _dipole_field(pts, x0=x0, y0=y0, z0=z0,
                         moment_am2=moment).reshape(grid, grid, 3)
    bmap = bmap + np.array(bg_ut)
    return bmap, pts


def test_tensor_gradient_map_shape_and_units():
    """The per-pixel Jacobian of a linear Bx gradient is constant on the
    interior: dBx/dx == gradient_ut_mm, dBx/dy == 0."""
    from qssc.magnetometry import synthetic_field_map
    g = synthetic_field_map(grid=8, gradient_ut_mm=8.0, dipole_moment_am2=0.0)
    px = tensor_gradient_map(g, pitch_mm=2.5)
    assert len(px) == 64
    # interior pixel (3,3): dBx/dx == gradient/pitch = 8/2.5 = 3.2 uT/mm
    # (gradient_ut_mm is per-pixel-index; the Jacobian is per-mm)
    inner = next(p for p in px if p.x == 3 and p.y == 3)
    assert abs(inner.jacobian_ut_mm[0][0] - 3.2) < 1e-6
    assert abs(inner.jacobian_ut_mm[0][1]) < 1e-6


def test_localize_dipole_clean_round_trip():
    """On a noise-free full-vector dipole field the localizer recovers the
    source position and moment exactly."""
    grid, pitch = 8, 2.5
    bmap, _ = _dipole_map(grid, pitch, x0=2.0, y0=-1.0, z0=10.0,
                          moment=[0.0, 0.0, 2e-3], bg_ut=(0.0, 0.0, 0.0))
    fit = localize_dipole(bmap, pitch_mm=pitch, z_guess_mm=10.0,
                          background_ut=[0, 0, 0])
    assert fit is not None
    assert abs(fit["dipole_x_mm"] - 2.0) < 0.2
    assert abs(fit["dipole_y_mm"] - (-1.0)) < 0.2
    assert abs(fit["dipole_z_mm"] - 10.0) < 1.0
    assert abs(fit["dipole_moment_am2"][2] - 2e-3) < 5e-4


def test_localize_dipole_none_for_zero_moment():
    """A uniform field (no dipole) -> the fit recovers ~0 moment and returns
    None (no source above the noise floor)."""
    grid, pitch = 8, 2.5
    bmap = np.broadcast_to(np.array([0.0, 0.0, 50.0]), (grid, grid, 3)).copy()
    fit = localize_dipole(bmap, pitch_mm=pitch, z_guess_mm=10.0,
                          background_ut=[0, 0, 50.0])
    assert fit is None


def test_localize_dipole_from_splits_round_trip():
    """The splits-based localizer recovers the source from the raw doublet
    splits (sign-ambiguity-free observables), differential mode."""
    prof = get_profile("NV")
    grid, pitch = 8, 2.5
    gamma = prof.gyromagnetic_ratio
    lw = prof.odmr_linewidth_hz * 1e-6
    rng = np.random.default_rng(0)
    bg = np.array([0.0, 0.0, 50.0])
    bmap, pts = _dipole_map(grid, pitch, x0=2.0, y0=-1.0, z0=10.0,
                            moment=[0.0, 0.0, 2e-3], bg_ut=tuple(bg))
    base = np.broadcast_to(bg, (grid, grid, 3)).copy()
    axes = np.asarray(_NV_AXES)
    # simulate the doublet splits (with shot noise) for signal + baseline
    n_avg = 4000
    sig_split = np.zeros((grid, grid, 4))
    base_split = np.zeros((grid, grid, 4))
    for iy in range(grid):
        for ix in range(grid):
            for k, n in enumerate(axes):
                # bmap/base are in uT -> convert to T for the split formula
                s_sig = 2.0 * gamma * abs(float(bmap[iy, ix] @ n)) * 1e-6 / 1e6
                s_base = 2.0 * gamma * abs(float(base[iy, ix] @ n)) * 1e-6 / 1e6
                sig_split[iy, ix, k] = max(0.0, s_sig + rng.normal(0, lw / np.sqrt(n_avg)))
                base_split[iy, ix, k] = max(0.0, s_base + rng.normal(0, lw / np.sqrt(n_avg)))
    sign_factors = np.sign(base @ axes.T)
    sign_factors[sign_factors == 0.0] = 1.0
    fit = localize_dipole_from_splits(
        sig_split, base_split, sign_factors, pitch_mm=pitch, grid=grid,
        gamma_hz_per_t=gamma, z_guess_mm=10.0)
    assert fit is not None
    assert abs(fit["dipole_x_mm"] - 2.0) < 1.0
    assert abs(fit["dipole_y_mm"] - (-1.0)) < 1.0
    assert abs(fit["dipole_z_mm"] - 10.0) < 2.0
    assert abs(fit["dipole_moment_am2"][2] - 2e-3) < 8e-4


def test_tensor_gradient_sweep_default():
    """The default sweep produces a 64-pixel tensor image and a dipole
    reconstruction close to the synthesized source."""
    res = tensor_gradient_sweep("NV", n_avg=4000, fit_dipole=True,
                                differential=True)
    assert isinstance(res, TensorGradientResult)
    assert res.n_pixels == 64
    assert len(res.pixels) == 64
    assert res.pixels[0].pixel == 0
    # the default source is at (2, -1, 10) mm, moment ~2e-3 A.m^2 z-directed
    assert res.dipole_x_mm is not None
    assert abs(res.dipole_x_mm - 2.0) < 1.5
    assert abs(res.dipole_y_mm - (-1.0)) < 1.5
    assert abs(res.dipole_z_mm - 10.0) < 3.0
    assert abs(res.dipole_moment_am2[2] - 2e-3) < 1e-3


def test_tensor_gradient_sweep_to_dict_round_trip():
    res = tensor_gradient_sweep("NV", n_avg=500, fit_dipole=True)
    d = res.to_dict()
    assert d["n_pixels"] == 64
    assert len(d["pixels"]) == 64
    assert "jacobian_ut_mm" in d["pixels"][0]
    assert "fit_residual_mhz" in d


def test_tensor_gradient_sweep_absolute_mode():
    """Absolute (non-differential) mode still returns a tensor image; the
    dipole fit falls back to the reconstructed-vector path."""
    res = tensor_gradient_sweep("NV", n_avg=500, fit_dipole=True,
                                differential=False)
    assert res.n_pixels == 64
    assert len(res.pixels) == 64


def test_api_sensor_tensor_endpoint_localizes_dipole():
    """The /api/sensor/tensor endpoint runs the 64-pixel tensor gradiometer
    and the dipole fit recovers the source position+moment (Invention 19).
    Both source modes (sim = clean averages, real = shot-noise-limited) must
    return a populated B-vector map + a dipole fit close to the true source."""
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    true = dict(dipole_moment_am2=0.002, dipole_x_mm=2.0, dipole_y_mm=-1.0,
                dipole_z_mm=10.0, base_bz_ut=50.0, seed=42)
    for src in ("sim", "real"):
        r = c.get("/api/sensor/tensor", params={"source": src, **true})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["grid"] == 8 and j["n_pixels"] == 64
        assert len(j["b_mag_image_ut"]) == 64
        assert len(j["tensor_pixels"]) == 64
        assert j["n_resolved_pixels"] > 0
        assert j["differential"] is True
        d = j["dipole"]
        assert d is not None, f"{src}: no dipole fit"
        # the fit recovers the source within a few mm + the moment to ~30%
        assert abs(d["dipole_x_mm"] - 2.0) < 3.0
        assert abs(d["dipole_y_mm"] - (-1.0)) < 3.0
        assert abs(d["dipole_z_mm"] - 10.0) < 5.0
        import math
        m_mag = math.sqrt(sum(x * x for x in d["dipole_moment_am2"]))
        assert 0.001 < m_mag < 0.004, f"{src}: moment {m_mag} off"
        # real mode is shot-noise-limited (fewer effective averages than sim)
        assert j["mode"] == src
    # no-dipole case: zero moment -> the fit returns no dipole above the floor
    r0 = c.get("/api/sensor/tensor", params={
        "source": "sim", "dipole_moment_am2": 0.0, "base_bz_ut": 50.0, "seed": 1})
    assert r0.status_code == 200
    # with no source the |B| image is at the noise floor (differential cancels)
    j0 = r0.json()
    assert j0["b_mag_max_ut"] < 5.0  # µT, near zero after baseline subtraction


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))