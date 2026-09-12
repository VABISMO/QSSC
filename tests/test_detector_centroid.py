"""Invention 6 — charge-sharing centroiding (sub-pixel detector resolution).

The continuous graphene/MoS2 sheet on ITO lets a spot's photocurrent split across
neighbouring pads; the per-pad ratio localizes the spot to ~pitch/(2*SNR). These
tests pin the physics: weights are conserved, a spot at a pad center peaks there,
centroiding recovers a known position, the sub-pixel factor tracks SNR, and --
the key limitation -- two within-pad spots merge into one (centroiding localizes
one spot, it does not resolve multiple sub-pad spots).
"""
from __future__ import annotations

import numpy as np

from qssc.detector_centroid import (
    DEFAULT_SNR,
    N_SITES,
    SubPixelModel,
    charge_share_weights,
    centroid_estimate,
    localize_spots,
    pad_grid_centers,
    sub_pixel_factor_from_snr,
)
from qssc.simulator import BRIGHT_CODE, READOUT_NOISE


def _dip_codes(spot_xy, intensity, centers, sigma, noise=0.0, rng=None):
    """Forward-model a single spot's 64-pad codes (a DIP = lower code on the
    spot's pads). Bright baseline everywhere, dip = intensity * charge-share."""
    w = charge_share_weights(spot_xy, centers, sigma)
    codes = np.full(centers.shape[0], float(BRIGHT_CODE))
    codes = codes - intensity * w
    if noise > 0.0 and rng is not None:
        codes = codes + rng.normal(0.0, noise, size=codes.shape)
    return np.clip(codes, 0, None)


def test_charge_share_weights_sum_to_one():
    centers = pad_grid_centers()
    for spot in [(0.0, 0.0), (1.25, 0.0), (0.6, -0.9), (3.0, 3.0)]:
        w = charge_share_weights(spot, centers, sigma_mm=1.0)
        assert w.shape == (N_SITES,)
        assert abs(float(w.sum()) - 1.0) < 1e-12


def test_charge_share_weights_peak_at_nearest_pad():
    centers = pad_grid_centers()
    # a spot exactly at pad index 0's center -> pad 0 carries the most weight
    w = charge_share_weights(tuple(centers[0]), centers, sigma_mm=1.0)
    assert int(np.argmax(w)) == 0
    # a spot between pads -> neither single pad gets all the weight
    mid = (centers[0] + centers[1]) / 2.0
    wmid = charge_share_weights(tuple(mid), centers, sigma_mm=1.0)
    assert wmid[0] < w[0]            # less concentrated than on-pad
    assert wmid[1] > 0.05            # neighbour carries meaningful weight


def test_sub_pixel_factor_from_snr():
    assert sub_pixel_factor_from_snr(80.0) == 8          # default SNR
    assert sub_pixel_factor_from_snr(80.0, threshold=10.0) == 8
    assert sub_pixel_factor_from_snr(10.0) == 1          # at threshold -> 1
    assert sub_pixel_factor_from_snr(0.0) == 1           # no signal -> 1
    # monotonic in SNR
    assert sub_pixel_factor_from_snr(40.0) <= sub_pixel_factor_from_snr(80.0)
    assert sub_pixel_factor_from_snr(800.0) == 80        # 100 shots -> S=80
    assert sub_pixel_factor_from_snr(-5.0) == 1


def test_subpixel_model_defaults():
    m = SubPixelModel()
    assert m.effective_snr() == DEFAULT_SNR
    assert m.space_bins() == 8
    assert m.spatial_channels() == 64 * 8
    # explicit n_space_bins overrides the SNR-derived value
    m2 = SubPixelModel(n_space_bins=16)
    assert m2.space_bins() == 16
    assert m2.spatial_channels() == 64 * 16


def test_centroid_recovers_known_spot_position():
    """A single well-separated spot: the centroid recovers its (x, y) within
    ~pad_pitch/(2*SNR), the centroiding localization precision."""
    centers = pad_grid_centers()
    sigma = 1.0
    # spot near the center but offset by 0.3 mm in x (sub-pixel position)
    spot = (0.3, -0.2)
    codes = _dip_codes(spot, intensity=15000.0, centers=centers, sigma=sigma)
    cx, cy, uncert, snr = centroid_estimate(codes, centers)
    assert abs(cx - spot[0]) < 0.25      # within ~uncert (SNR~23 -> ~0.05mm, be generous)
    assert abs(cy - spot[1]) < 0.25
    assert snr > 3.0
    assert uncert > 0.0


def test_localize_two_well_separated_spots():
    """Two spots ~3 pads apart (different pixels) -> resolved as 2 spots."""
    centers = pad_grid_centers()
    sigma = 1.0
    # pad 0 center ~(-8.75, -8.75), pad 9 center ~(-6.25, -6.25) -- far apart
    s0 = tuple(centers[0])
    s1 = tuple(centers[9])
    codes = _dip_codes(s0, 12000.0, centers, sigma)
    codes = codes - (12000.0 * charge_share_weights(s1, centers, sigma))
    codes = np.clip(codes, 0, None)
    spots = localize_spots(codes, centers, sigma_mm=sigma)
    assert len(spots) == 2
    # each recovered near its true position
    xs = sorted(s.x_mm for s in spots)
    assert abs(xs[0] - s0[0]) < 1.5 and abs(xs[1] - s1[0]) < 1.5


def test_localize_merged_within_pad_spots_give_one():
    """Two spots well under one pad-pitch apart (the within-pixel case) merge
    into ONE spot -- the fundamental limitation that means centroiding improves
    imaging resolution but NOT the per-pixel qubit count."""
    centers = pad_grid_centers()
    sigma = 1.0
    pad0 = tuple(centers[0])
    # two spots 0.1 mm apart (well under the 2.5 mm pad pitch) -> one blob
    s0 = (pad0[0], pad0[1])
    s1 = (pad0[0] + 0.1, pad0[1])
    codes = _dip_codes(s0, 12000.0, centers, sigma)
    codes = codes - (12000.0 * charge_share_weights(s1, centers, sigma))
    codes = np.clip(codes, 0, None)
    spots = localize_spots(codes, centers, sigma_mm=sigma)
    assert len(spots) == 1, "two sub-pad spots must merge (within-pad limitation)"


def test_localize_no_signal_returns_empty():
    centers = pad_grid_centers()
    # uniform bright baseline, no dip -> no spots
    codes = np.full(N_SITES, float(BRIGHT_CODE))
    assert localize_spots(codes, centers, sigma_mm=1.0) == []


def test_centroiding_with_readout_noise():
    """Centroiding still works at the real readout noise level (640 counts)."""
    rng = np.random.default_rng(42)
    centers = pad_grid_centers()
    sigma = 1.0
    spot = (0.4, 0.1)
    codes = _dip_codes(spot, 15000.0, centers, sigma, noise=READOUT_NOISE, rng=rng)
    cx, cy, _u, snr = centroid_estimate(codes, centers)
    assert snr > 3.0
    assert abs(cx - spot[0]) < 0.5 and abs(cy - spot[1]) < 0.5