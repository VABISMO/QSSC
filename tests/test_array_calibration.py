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
    # a single bright baseline with one NV-linewidth dip at D on every column.
    # The dip FWHM = the NV inhomogeneous linewidth (5 MHz) so it matches the
    # CLEAN PSF and resolves as exactly one qubit line.
    sigma_ghz = (prof.odmr_linewidth_hz * 1e-9) / 2.355  # FWHM -> sigma
    codes = np.full((n_points, 64), 40000, dtype=np.int32)
    dip = np.exp(-((freqs - D) ** 2) / (2 * sigma_ghz ** 2))
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


# -- Invention 7: N-shot lock-in integration (raises measured width) ----------
def test_integration_shots_raises_measured_width():
    """Averaging N shots raises per-line SNR by sqrt(N), so dimmer lone-NV bins
    clear the min_snr bar -> the MEASURED width rises toward the resolvable/NV
    cap. The same seed draws the same nv_present, so more shots can only be >=."""
    from qssc.array_calibration import calibrate_array
    cal1 = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=42)
    cal16 = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=42,
                            integration_shots=16)
    assert cal16.integration_shots == 16
    assert cal1.integration_shots == 1
    # integration can only add detected lines (lower noise), never remove
    assert sum(cal16.widths) >= sum(cal1.widths)
    assert cal16.max_width >= cal1.max_width


def test_integration_shots_one_is_back_compat():
    """integration_shots=1 must be bit-identical to the original (no averaging)."""
    from qssc.array_calibration import calibrate_array
    a = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=42)
    b = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=42, integration_shots=1)
    assert a.widths == b.widths


def test_integration_width_capped_by_resolvable_and_nv():
    """Even with heavy integration the width cannot exceed min(resolvable, nv)."""
    from qssc.array_calibration import calibrate_array
    from qssc.simulator import resolvable_register_width
    from qssc.defects import get_profile
    prof = get_profile("NV")
    lw = prof.odmr_linewidth_hz * 1e-6
    resolvable = resolvable_register_width(1.0, 200.0, lw)
    cal = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=42,
                          integration_shots=64)
    for p in cal.pixels:
        assert p.register_width <= p.nv_present
        assert p.register_width <= resolvable


# -- Few-NV compute regime + CLEAN resolver (fixes 1+2+3) ---------------------
def test_one_stone_per_pixel_compute_regime():
    """Fix 1+2: each pixel holds ONE few-NV compute stone (100-300 NVs), and the
    measured width is the number of DISTINCT frequency-addressed ODMR lines the
    gradient resolves -- NOT one ensemble qubit. With a strong gradient (g=2.0,
    80 resolvable bins) and ~100-300 NVs, most bins hold 1-2 NVs -> the occupied-
    bin count (the 2009 Neumann/Taminiau multi-qubit-per-stone regime) is well
    above 1 on every pixel."""
    from qssc.array_calibration import calibrate_array, MAX_MICRODIAMONDS_PER_PIXEL
    assert MAX_MICRODIAMONDS_PER_PIXEL == 1  # fab reality: one stone per site
    cal = calibrate_array("NV", gradient_mhz_per_um=2.0, seed=42)
    for p in cal.pixels:
        assert p.microdiamonds == 1            # exactly one stone per pixel
        assert 100 <= p.nv_present <= 300      # compute-regime NV count
        assert p.register_width >= 1
        # the few-NV compute regime gives several frequency-addressed qubits,
        # not the single ensemble qubit of the 10^12-NV sensing stone
        assert p.register_width > 1


def test_clean_closes_measured_to_occupied_gap():
    """Fix 3 (CLEAN resolver): with a strong gradient the few-NV compute stone's
    NVs spread into ~80 resolvable bins. The TRUE qubit count is the number of
    occupied bins; the MEASURED count (decompose_odmr) approaches it as
    integration raises per-line SNR (dim lone-NV bins clear the 4-sigma bar).
    At 1 shot lone-NV bins (SNR ~2.4) are below the bar; at 16 shots (SNR ~9.6)
    they are recovered -> the measured->true gap closes (monotonic, upward)."""
    import numpy as np
    from qssc.array_calibration import _draw_nv_present, _pixel_odmr_spectrum
    from qssc.odmr_decompose import decompose_odmr
    from qssc.defects import get_profile
    from qssc.simulator import resolvable_register_width
    prof = get_profile("NV")
    lw = prof.odmr_linewidth_hz * 1e-6
    g, psz = 2.0, 200.0
    resolvable = resolvable_register_width(g, psz, lw)
    rng = np.random.default_rng(42)
    measured1 = measured16 = occupied = 0
    for pix in range(16):
        _n_stones, nv = _draw_nv_present(rng, pix, 300, 1)
        n_bins = max(1, min(nv, resolvable))
        # TRUE occupied-bin count (what the gradient makes resolvable)
        picks = rng.integers(0, n_bins, size=nv)
        n_occ = int(np.bincount(picks, minlength=n_bins).astype(bool).sum())
        rng_state = rng.bit_generator.state  # keep the two shots in sync
        f1, c1, _n1 = _pixel_odmr_spectrum(prof, nv, n_bins, g, psz, rng,
                                           integration_shots=1)
        rng.bit_generator.state = rng_state
        f16, c16, _n16 = _pixel_odmr_spectrum(prof, nv, n_bins, g, psz, rng,
                                              integration_shots=16)
        m1 = min(len(decompose_odmr(f1, c1, min_snr=4.0,
                                    linewidth_guess_mhz=lw,
                                    min_separation_mhz=lw * 0.8)), n_bins)
        m16 = min(len(decompose_odmr(f16, c16, min_snr=4.0,
                                     linewidth_guess_mhz=lw,
                                     min_separation_mhz=lw * 0.8)), n_bins)
        measured1 += m1
        measured16 += m16
        occupied += n_occ
    # integration is monotonic (reveals dim lone-NV lines, never hides them)
    assert measured16 >= measured1
    # the 16-shot measured count is within ~25% of the true occupied-bin count
    # (CLEAN closes the gap; the residual is overlap + the 4-sigma bar, not a
    # find_peaks under-count)
    assert measured16 >= 0.75 * occupied, (measured16, occupied)
    # and strictly better than the 1-shot measurement
    assert (occupied - measured16) <= (occupied - measured1)


# -- Invention 6: sub-pixel centroiding (spatial reporting, NOT qubit gain) ---
def test_sub_pixel_reports_spatial_channels():
    """A SubPixelModel reports spatial_channels = 64*S (the imaging resolution)
    and a per-pixel sub_pixel_position. It does NOT change register_width."""
    from qssc.array_calibration import calibrate_array
    from qssc.detector_centroid import SubPixelModel
    sp = SubPixelModel()  # S=8 at the default SNR
    cal = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=42, sub_pixel=sp)
    assert cal.sub_pixel_factor == 8
    assert cal.spatial_channels == 64 * 8
    assert cal.sub_pixel is not None
    # per-pixel positions are populated (imaging metadata)
    assert sum(1 for p in cal.pixels if p.sub_pixel_position is not None) == 64


def test_sub_pixel_does_not_change_register_width():
    """Centroiding is imaging metadata: the per-pixel qubit count is set by
    min(freq_bins, nv_present, SNR-detectable lines) -- sub_pixel does NOT
    multiply it (within-pad spots merge; the 8x-qubit claim was rejected)."""
    from qssc.array_calibration import calibrate_array
    from qssc.detector_centroid import SubPixelModel
    plain = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=42)
    sp = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=42,
                         sub_pixel=SubPixelModel())
    assert plain.widths == sp.widths, "sub_pixel must not change register_width"


def test_sub_pixel_none_is_back_compat():
    """sub_pixel=None (the default) -> sub_pixel_factor=0, spatial_channels=0,
    no per-pixel positions, identical widths."""
    from qssc.array_calibration import calibrate_array
    cal = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=42)
    assert cal.sub_pixel_factor == 0
    assert cal.spatial_channels == 0
    assert cal.sub_pixel is None
    assert all(p.sub_pixel_position is None for p in cal.pixels)


def test_sub_pixel_json_round_trip(tmp_path: Path):
    """The sub-pixel + integration fields survive a JSON round-trip."""
    from qssc.array_calibration import ArrayCalibration, calibrate_array
    from qssc.detector_centroid import SubPixelModel
    cal = calibrate_array("NV", gradient_mhz_per_um=1.0, seed=7, n_pixels=8,
                          integration_shots=4, sub_pixel=SubPixelModel())
    f = tmp_path / "cal_sp.json"
    cal.to_json(f)
    loaded = ArrayCalibration.from_json(f)
    assert loaded.integration_shots == 4
    assert loaded.sub_pixel_factor == 8
    assert loaded.spatial_channels == 64 * 8
    assert loaded.sub_pixel is not None
    assert loaded.widths == cal.widths


def test_calibrate_array_from_codes_with_sub_pixel():
    """The real-hardware path: a synthetic (n,64) matrix WITH charge sharing
    is decomposed per column (register_width) and centroided per row (sub-pixel
    positions). register_width is unchanged by sub_pixel; positions are reported."""
    from qssc.array_calibration import calibrate_array_from_codes
    from qssc.detector_centroid import (SubPixelModel, charge_share_weights,
                                        pad_grid_centers)
    from qssc.defects import get_profile
    prof = get_profile("NV")
    D = prof.d_ghz
    n_points = 400
    freqs = np.linspace(D - 0.05, D + 0.05, n_points)
    centers = pad_grid_centers()
    sigma = 1.0
    # give each pixel a distinct detuning so at any one MW frequency only a few
    # pixels dip -> there is SPATIAL contrast across the 64 pads for centroiding
    # (a uniform all-pixel dip has no spatial structure to centroid).
    rng = np.random.default_rng(0)
    detun_ghz = rng.uniform(-0.03, 0.03, size=64)
    sigma_ghz = (prof.odmr_linewidth_hz * 1e-9) / 2.355  # NV-linewidth dip
    base = np.full((n_points, 64), 40000.0, dtype=float)
    for j in range(64):
        w = charge_share_weights(tuple(centers[j]), centers, sigma)
        dip_j = np.exp(-((freqs - (D + detun_ghz[j])) ** 2) / (2 * sigma_ghz ** 2))
        # pixel j's dip spills to its neighbours via the charge-share footprint
        base[:, :] = base[:, :] - (30000.0 * dip_j)[:, None] * w[None, :]
    codes = np.clip(base, 0, None).astype(np.int32)
    cal_plain = calibrate_array_from_codes(
        codes, freqs, "NV", gradient_mhz_per_um=1.0, seed=42)
    cal_sp = calibrate_array_from_codes(
        codes, freqs, "NV", gradient_mhz_per_um=1.0, seed=42,
        sub_pixel=SubPixelModel(sigma_sheet_mm=sigma))
    assert cal_sp.sub_pixel_factor == 8
    assert cal_sp.spatial_channels == 64 * 8
    assert cal_plain.widths == cal_sp.widths, "sub_pixel must not change width"
    # at frequencies where a pixel dips, centroiding localizes it -> positions
    assert sum(1 for p in cal_sp.pixels if p.sub_pixel_position is not None) > 0