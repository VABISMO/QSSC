"""Invention 4+5 — per-pixel array calibration tests.

The per-pixel qubit register width is a MEASURED quantity (the number of ODMR
lines a field gradient makes resolvable inside a pixel, capped by the NVs
physically present), not a fixed constant. ``calibrate_array`` models the
calibration for all 64 pixels; ``best_uniform_width`` picks the circuit width
that maximizes working qubits for the "same circuit on all pixels" case.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from qssc.array_calibration import (
    ArrayCalibration,
    PixelCalibration,
    best_uniform_width,
    calibrate_array,
    calibrate_array_from_codes,
)
from qssc.defects import get_profile
from qssc.simulator import resolvable_register_width


def test_calibrate_array_returns_64_pixels():
    cal = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=42)
    assert isinstance(cal, ArrayCalibration)
    assert cal.n_pixels == 64
    assert len(cal.pixels) == 64
    assert all(isinstance(p, PixelCalibration) for p in cal.pixels)
    # pixel ids are 0..63
    assert [p.pixel_id for p in cal.pixels] == list(range(64))


def test_calibrate_array_widths_vary_per_pixel():
    """Each microdiamond carries a different NV count, so the measured widths
    genuinely vary across pixels (not all equal)."""
    cal = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=42)
    widths = cal.widths
    assert len(widths) == 64
    assert len(set(widths)) > 1  # not all identical
    assert all(w >= 1 for w in widths)


def test_calibrate_array_width_bounded_by_resolvable_and_present():
    """register_width <= min(nv_present, resolvable_register_width(...))."""
    prof = get_profile("NV")
    lw_mhz = prof.odmr_linewidth_hz * 1e-6
    grad, psz = 1.0, 200.0
    resolvable = resolvable_register_width(grad, psz, lw_mhz)
    cal = calibrate_array("NV", gradient_mhz_per_um=grad,
                          pixel_size_um=psz, seed=42)
    for p in cal.pixels:
        assert p.register_width <= p.nv_present
        assert p.register_width <= resolvable
        assert p.register_width >= 1


def test_calibrate_array_width_scales_with_gradient():
    """A stronger gradient resolves more lines -> wider registers on average."""
    cal_lo = calibrate_array("NV", gradient_mhz_per_um=0.5, seed=42)
    cal_hi = calibrate_array("NV", gradient_mhz_per_um=2.0, seed=42)
    # the same seed draws the same nv_present, so the hi-gradient measurement
    # can only be >= (more lines resolved), never lower on average
    assert sum(cal_hi.widths) >= sum(cal_lo.widths)
    assert cal_hi.max_width >= cal_lo.max_width


def test_calibrate_array_is_reproducible():
    """A fixed seed reproduces the same calibration (same widths)."""
    a = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=123)
    b = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=123)
    assert a.widths == b.widths


def test_calibrate_array_json_round_trip(tmp_path: Path):
    cal = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=7, n_pixels=8)
    f = tmp_path / "cal.json"
    cal.to_json(f)
    loaded = ArrayCalibration.from_json(f)
    assert loaded.profile == cal.profile
    assert loaded.gradient_mhz_per_um == cal.gradient_mhz_per_um
    assert loaded.widths == cal.widths
    assert loaded.n_pixels == 8
    # the raw JSON is valid and carries the per-pixel fields
    raw = json.loads(f.read_text())
    assert raw["pixels"][0]["register_width"] == cal.pixels[0].register_width


def test_calibrate_array_from_codes():
    """The real-hardware path builds an ArrayCalibration from a measured
    (n_points, 64) code matrix by decomposing each pixel column."""
    prof = get_profile("NV")
    D = prof.d_ghz
    n_points = 200
    freqs = np.linspace(D - 0.05, D + 0.05, n_points)
    # a single bright baseline with one dip at D on every column -> width 1
    codes = np.full((n_points, 64), 40000, dtype=np.int32)
    dip = np.exp(-((freqs - D) ** 2) / (2 * (5e-3) ** 2))
    codes = codes - (30000 * dip).astype(np.int32)[..., None]
    cal = calibrate_array_from_codes(
        codes, freqs, "NV", gradient_mhz_per_um=1.0, seed=42)
    assert cal.n_pixels == 64
    assert all(w >= 1 for w in cal.widths)
    # every column has the same single dip -> every pixel resolves 1 line
    assert set(cal.widths) == {1}


def test_best_uniform_width_argmax():
    """best_uniform_width returns (N, k) maximizing N * k."""
    # widths: 8 pixels of width >=4, 5 of width >=6, 2 of width >=10
    # N=4: k=8 -> 32 ; N=6: k=5 -> 30 ; N=10: k=2 -> 20 -> winner N=4
    widths = [4, 6, 6, 10, 10, 6, 4, 4]
    n, k = best_uniform_width(widths)
    assert n == 4
    assert k == 8
    assert n * k == 32


def test_best_uniform_width_picks_higher_score():
    # widths [2,2,6,6,6,6,10]: N=2 k=7 ->14 ; N=6 k=5 ->30 ; N=10 k=1 ->10
    # the mid width 6 wins (not the widest, not the most pixels) -> argmax
    widths = [2, 2, 6, 6, 6, 6, 10]
    n, k = best_uniform_width(widths)
    assert n == 6
    assert k == 5
    assert n * k == 30


def test_best_uniform_width_empty():
    assert best_uniform_width([]) == (0, 0)