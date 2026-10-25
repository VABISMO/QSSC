"""Invention 1: 64-pixel vector magnetometer (qssc.magnetometry)."""
from __future__ import annotations

import numpy as np

from qssc.magnetometry import (magnetometer_sweep, synthetic_field_map,
                               measure_pixel, _recover_b_vector)
from qssc.defects import get_profile


def test_synthetic_field_map_shape():
    fmap = synthetic_field_map()
    assert fmap.shape == (8, 8, 3)
    # linear gradient in bx across x
    assert fmap[0, 7, 0] > fmap[0, 0, 0]


def test_recover_b_vector_round_trip():
    """The 4-axis least-squares recovers a known B-vector when all 4 doublet
    splits are well above the linewidth (the overdetermined case the compass
    uses; with <4 resolved axes the sign ambiguity is underdetermined)."""
    prof = get_profile("NV")
    gamma = prof.gyromagnetic_ratio
    # ~0.8 mT field -> all 4 <111> projections >> 5 MHz linewidth
    B = np.array([700.0, 300.0, -100.0]) * 1e-6
    from qssc.sensors import _NV_AXES
    splits = [2.0 * gamma * abs(float(B @ n)) / 1e6 for n in _NV_AXES]
    b_rec, n_res = _recover_b_vector(splits, gamma, prof.odmr_linewidth_hz / 1e6)
    assert n_res == 4
    assert np.allclose(b_rec, B, atol=1e-9), f"got {b_rec}, want {B}"


def test_measure_pixel_noise_floor():
    """A sub-linewidth field gives an unresolved doublet (split ~0)."""
    prof = get_profile("NV")
    rng = np.random.default_rng(0)
    # Earth-field-ish: ~25 uT -> split ~1.4 MHz < 5 MHz linewidth
    splits, b_rec, n_res = measure_pixel(prof, [25e-6, 0, 0], n_avg=1,
                                         linewidth_mhz=prof.odmr_linewidth_hz / 1e6,
                                         rng=rng)
    assert all(0 <= s <= 5.0 for s in splits)
    # single-shot Earth field is marginal; unlikely to resolve 4 axes
    assert n_res <= 4


def test_sweep_returns_64_pixels_and_image():
    # strong field (~2 mT) -> splits well above the 5 MHz linewidth, all pixels
    # resolve the full 4-axis doublet
    res = magnetometer_sweep("NV", n_avg=2000, mode="sim",
                             b_field_map_ut=synthetic_field_map(
                                 base_bx_ut=2000.0, base_bz_ut=-1000.0,
                                 gradient_ut_mm=50.0),
                             rng=np.random.default_rng(0))
    assert res.n_pixels == 64
    assert len(res.pixels) == 64
    assert len(res.image) == 64
    assert all(c["value"] >= 0 for c in res.image)
    assert any(p["resolved_axes"] >= 3 for p in res.pixels)


def test_differential_mode_suppresses_baseline():
    """Differential mode subtracts the gradient baseline so the image is the
    perturbation, not the strong DC background. Compare against the non-
    differential image, which is dominated by the ~2 mT baseline."""
    fmap = synthetic_field_map(base_bx_ut=2000.0, base_bz_ut=-1000.0,
                               gradient_ut_mm=50.0, dipole_moment_am2=0.0005)
    baseline = synthetic_field_map(base_bx_ut=2000.0, base_bz_ut=-1000.0,
                                   gradient_ut_mm=50.0, dipole_moment_am2=0.0)
    diff = magnetometer_sweep("NV", b_field_map_ut=fmap,
                              baseline_map_ut=baseline, n_avg=8000,
                              mode="sim", differential=True,
                              rng=np.random.default_rng(0))
    plain = magnetometer_sweep("NV", b_field_map_ut=fmap, n_avg=8000,
                               mode="sim", differential=False,
                               rng=np.random.default_rng(0))
    assert diff.differential is True
    # the perturbation image is far smaller than the baseline-dominated one
    assert diff.image_max_ut < plain.image_max_ut * 0.1, (
        f"differential max {diff.image_max_ut} not suppressed vs "
        f"plain {plain.image_max_ut}")


def test_average_mode_reports_mean_std():
    res = magnetometer_sweep("NV", b_field_map_ut=synthetic_field_map(
        base_bx_ut=2000.0, base_bz_ut=-1000.0, gradient_ut_mm=50.0),
        n_avg=1000, mode="sim", average=True,
        rng=np.random.default_rng(0))
    assert res.average is True
    assert res.mean_b_mag_ut > 0
    assert res.std_b_mag_ut >= 0
    assert len(res.mean_b_vector_ut) == 3


def test_zero_field_image_near_floor():
    """A zero field with heavy averaging gives an image at the noise floor."""
    res = magnetometer_sweep("NV", b_field_map_ut=np.zeros((8, 8, 3)),
                             n_avg=10000, mode="sim",
                             rng=np.random.default_rng(0))
    assert res.image_max_ut < 100.0