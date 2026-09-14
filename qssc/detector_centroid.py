"""Invention 6 — sub-pixel detector resolution via charge-sharing centroiding.

The QSSC detector is a continuous graphene/MoS2+PEDOT sheet on ITO
(``DET_COMMON``) read by 64 discrete pads on a 2.5 mm pitch (8x8 grid, 1.0x1.0 mm
active pad area — ``docs/FABRICATION_PROTOCOL_P0.md``, ``qssc/magnetometry.py``).
A fluorescence spot landing on the sheet splits its photocurrent across the
neighbouring pads by the sheet's lateral charge-spreading length; measuring the
per-pad ratio localizes the spot to a fraction of the pad pitch. This is the
resistive-charge-sharing / centroiding principle behind wedge-and-strip and
delay-line anodes.

**What this buys:** spatial *imaging* resolution. One pad's spot is localized to
~``pitch/(2*SNR)`` ~ 15 um, so the 64-pad grid resolves ``64 * S`` spatial
positions (``S = floor(SNR/threshold)`` = 8 at the default readout SNR 80). This
is the detector improvement the user asked for ("sacar mas resolucion de 64
pixels").

**What it does NOT buy** (important, see ``docs/INVENTIONS.md`` §6): more
per-pixel *qubits*. The 64 pads are on 2.5 mm pitch but each pixel's NV active
region is only ~200 um wide, so two microdiamonds inside one pixel are <=200 um
apart -- far under the ~one-pad-pitch separation needed for their charge clouds
to hit *different* pads. Centroiding *localizes one spot* but cannot *resolve
two* within a pad (they merge into one blob). So the per-pixel register width
(``min(freq_bins, nv_present, SNR-detectable lines)`` in
:mod:`qssc.array_calibration`) is unchanged by centroiding. The qubit-register
gain comes from Invention 7 (N-shot lock-in integration), not here.

Grounding: ``pad_pitch_mm=2.5`` and ``1.0x1.0 mm`` active pad area from the fab
docs + ``qssc/magnetometry.py:269``. ``SNR = BRIGHT_CODE/READOUT_NOISE = 80`` from
:mod:`qssc.simulator` (datasheet-derived readout constants). ``sigma_sheet_mm``
(the sheet charge-spreading length) is a **tunable engineering input**
(graphene/MoS2 sheet resistance is engineerable), NOT a datasheet constant.
``threshold=10`` is a conservative model assumption (flagged), not a datasheet
value.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np

from .simulator import BRIGHT_CODE, READOUT_NOISE, BG_CODE, N_SITES

# Default readout SNR = bright photocurrent / lock-in noise (datasheet-derived
# constants in qssc.simulator). Centroiding precision scales with this.
DEFAULT_SNR: float = BRIGHT_CODE / READOUT_NOISE  # 51200 / 640 = 80

# Conservative model assumption: a spot is "resolved" to a new sub-pixel bin when
# the per-bin SNR clears this threshold. NOT a datasheet constant -- it sets the
# sub-pixel factor S = floor(SNR/threshold) = 8 at the default SNR. Lowering it
# claims more sub-pixel bins (more aggressive); raising it claims fewer.
SUB_PIXEL_SNR_THRESHOLD: float = 10.0


@dataclass
class Spot:
    """One localized fluorescence spot in a 64-pad snapshot."""

    x_mm: float        # centroid x position on the pad grid (mm, grid-centered)
    y_mm: float        # centroid y position (mm)
    intensity: float   # spot dip depth above the local baseline (16-bit counts)
    snr: float         # peak dip / readout noise


@dataclass
class SubPixelModel:
    """Charge-sharing centroiding model for the continuous graphene/MoS2 sheet.

    ``sigma_sheet_mm`` is the sheet's lateral charge-spreading length -- the
    distance over which a spot's photocurrent spreads to neighbouring pads. It is
    a **tunable engineering input** (set by the graphene/MoS2 sheet resistance +
    PEDOT conductivity), NOT a datasheet constant; it must be characterized per
    die. ``sigma`` ~ pad_pitch/2 gives useful multi-pad spreading (centroiding
    needs the spot to spill to >=2 pads); ``sigma`` << pad_pitch makes each pad
    independent (no centroiding); ``sigma`` >> pad_pitch blurs everything (no
    localization).
    """

    pad_pitch_mm: float = 2.5
    sigma_sheet_mm: float = 1.0
    snr: Optional[float] = None        # None -> DEFAULT_SNR (80)
    n_space_bins: Optional[int] = None  # None -> sub_pixel_factor_from_snr(snr)

    def effective_snr(self) -> float:
        return DEFAULT_SNR if self.snr is None else float(self.snr)

    def space_bins(self) -> int:
        """The sub-pixel factor S (resolvable positions per pad along one axis)."""
        if self.n_space_bins is not None:
            return max(1, int(self.n_space_bins))
        return sub_pixel_factor_from_snr(self.effective_snr())

    def spatial_channels(self) -> int:
        """Total resolvable spatial positions across the 64-pad grid (64 * S)."""
        return N_SITES * self.space_bins()

    def to_dict(self) -> dict:
        return asdict(self)


def sub_pixel_factor_from_snr(snr: float,
                              threshold: float = SUB_PIXEL_SNR_THRESHOLD) -> int:
    """Sub-pixel bins resolvable along one pad axis = ``max(1, floor(snr/threshold))``.

    Centroiding localizes a spot to ~``pitch/(2*SNR)`` per single measurement, so
    the number of distinct positions resolvable along one pad pitch grows with
    SNR. With the default readout SNR (``BRIGHT_CODE/READOUT_NOISE = 80``) and the
    conservative ``threshold=10`` this gives S=8. Multi-shot integration raises
    the effective SNR by ``sqrt(N)`` (classical, uncorrelated thermal/Johnson
    noise), so S grows as ``floor(80*sqrt(N)/10)``.

    ``threshold`` is a **model assumption**, not a datasheet constant.
    """
    if snr <= 0.0 or threshold <= 0.0:
        return 1
    return max(1, int(np.floor(snr / threshold)))


def pad_grid_centers(pad_pitch_mm: float = 2.5) -> np.ndarray:
    """The 64 pad centers as an ``(64, 2)`` array of ``(x_mm, y_mm)``.

    8x8 grid centered on the origin, pad ``(i, j)`` at
    ``((j-3.5)*pitch, (i-3.5)*pitch)`` with linear index ``i*8 + j``. This is the
    detector electrode layout (the same 64 sites ``qssc.simulator.N_SITES``).
    """
    grid = (np.arange(8) - 3.5) * float(pad_pitch_mm)
    xs, ys = np.meshgrid(grid, grid)  # (8,8), row i = y, col j = x
    return np.column_stack([xs.ravel(), ys.ravel()])


def charge_share_weights(spot_xy_mm: tuple[float, float],
                         pad_centers_mm: np.ndarray,
                         sigma_mm: float) -> np.ndarray:
    """Per-pad photocurrent fraction for a spot at ``spot_xy``.

    Gaussian spreading kernel ``exp(-d_p^2 / 2 sigma^2)``, normalized so
    ``sum_p w_p = 1`` (the spot's total photocurrent is conserved and splits
    across pads by proximity). A spot at a pad center puts the most weight on
    that pad; a spot between pads splits across the nearest 2-4. With
    ``sigma <= 0`` the spot stays on the single nearest pad (no sharing, the
    degenerate limit).
    """
    centers = np.asarray(pad_centers_mm, dtype=float)
    sx, sy = float(spot_xy_mm[0]), float(spot_xy_mm[1])
    d2 = (centers[:, 0] - sx) ** 2 + (centers[:, 1] - sy) ** 2
    if sigma_mm <= 0.0:
        w = np.zeros(centers.shape[0])
        w[int(np.argmin(d2))] = 1.0
        return w
    w = np.exp(-d2 / (2.0 * sigma_mm ** 2))
    s = float(w.sum())
    return w / s if s > 0 else w


def _dip_image(pad_codes: np.ndarray) -> np.ndarray:
    """Convert 64-pad codes to a dip-depth image (spots = peaks).

    ODMR is a PL *dip* on resonance: bright off-resonance, lower on resonance.
    The baseline is the brightest pad (the off-resonance reference); dip depth =
    ``baseline - code`` so a resonant spot is a positive peak. Pads below the
    background floor (``BG_CODE``) are clamped to zero dip (unpumped electrodes
    carry no ODMR signal).
    """
    codes = np.asarray(pad_codes, dtype=float)
    baseline = float(np.max(codes))
    dip = baseline - codes
    # a pad near the background floor has no PL to dip -> zero contribution
    dip = np.where(codes < BG_CODE + 2.0 * READOUT_NOISE, 0.0, dip)
    return np.clip(dip, 0.0, None)


def centroid_estimate(pad_codes: np.ndarray,
                      pad_centers_mm: np.ndarray,
                      noise: float = READOUT_NOISE
                      ) -> tuple[float, float, float, float]:
    """Intensity-weighted centroid of the 64-pad dip image -> ``(x, y, uncert_mm, snr)``.

    The centroid of a symmetric Gaussian-spreading kernel is an unbiased
    estimator of the spot position. ``uncert_mm ~ pad_pitch / (2 * spot_snr)`` --
    the centroiding localization precision. ``spot_snr`` is the peak dip over the
    readout noise. For a single well-separated spot this recovers the true
    position to within ``uncert_mm``; for two merged spots it returns their
    intensity-weighted average (one blob, not two -- the within-pad limitation).
    """
    centers = np.asarray(pad_centers_mm, dtype=float)
    dip = _dip_image(pad_codes)
    total = float(dip.sum())
    if total <= 0.0:
        cx = float(centers[:, 0].mean())
        cy = float(centers[:, 1].mean())
        return cx, cy, float("inf"), 0.0
    w = dip / total
    cx = float(np.sum(w * centers[:, 0]))
    cy = float(np.sum(w * centers[:, 1]))
    peak = float(np.max(dip))
    spot_snr = peak / noise if noise > 0 else float("inf")
    pitch = float(np.ptp(centers[:, 0]) / 7.0) if centers.shape[0] > 1 else 2.5
    uncert = pitch / (2.0 * spot_snr) if spot_snr > 0 else float("inf")
    return cx, cy, uncert, spot_snr


def localize_spots(pad_codes: np.ndarray,
                   pad_centers_mm: np.ndarray,
                   sigma_mm: float,
                   noise: float = READOUT_NOISE,
                   min_snr: float = 3.0,
                   max_spots: int = 16) -> list[Spot]:
    """Resolve the spatially-resolvable spots in one 64-pad snapshot.

    Iterative peak/centroid + subtract (the multi-spot wedge-and-strip method):
    find the brightest dip, centroid it, subtract its charge-share footprint from
    the residual, repeat until no pad clears ``min_snr``. Returns the list of
    localized spots (the per-frequency-point spatial image).

    Spots ~one pad-pitch apart (different pixels) are resolved as separate spots;
    spots well under one pad-pitch apart (e.g. two microdiamonds inside one 200 um
    pixel under a 2.5 mm pad) merge into one -- the within-pad limitation that
    means centroiding improves *imaging* resolution, not the per-pixel qubit
    count (see module docstring).
    """
    centers = np.asarray(pad_centers_mm, dtype=float)
    residual = _dip_image(pad_codes)
    spots: list[Spot] = []
    for _ in range(max_spots):
        idx = int(np.argmax(residual))
        peak = float(residual[idx])
        if noise <= 0.0 or peak / noise < min_snr:
            break
        # centroid the current residual (intensity-weighted, full grid -- the
        # Gaussian footprint is narrow enough that far pads carry ~zero weight)
        total = float(residual.sum())
        if total <= 0.0:
            break
        w = residual / total
        cx = float(np.sum(w * centers[:, 0]))
        cy = float(np.sum(w * centers[:, 1]))
        spots.append(Spot(x_mm=cx, y_mm=cy, intensity=peak, snr=peak / noise))
        # subtract this spot's charge-share footprint from the residual
        footprint = charge_share_weights((cx, cy), centers, sigma_mm)
        # the spot's total dip = peak / weight_at_centroid-pad; subtract that
        # total times the footprint from every pad
        foot_idx = int(np.argmax(footprint))
        w_peak = float(footprint[foot_idx])
        if w_peak <= 0.0:
            break
        i_total = peak / w_peak
        residual = np.clip(residual - i_total * footprint, 0.0, None)
    return spots