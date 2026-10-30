"""Per-pixel array calibration (sim + real).

The QSSC die is 64 INDEPENDENT pixels at 300 K. Each pixel's usable qubit
register width is NOT a fixed constant — it is a MEASURED quantity: the number
of ODMR lines a field gradient makes resolvable inside that pixel,
capped by the NVs physically present. Each microdiamond can carry up to ~100 NVs
(a user-supplied product spec, NOT a datasheet physics constant); the count
varies per pixel, so the register width varies per pixel.

``calibrate_array`` models the calibration for all 64 pixels:

1. Draw a per-pixel ``nv_present`` (NVs physically in the pixel, seed-fixed so a
   given seed reproduces; varies per pixel, upper bound ``max_nv_per_microdiamond``
   times the number of microdiamonds in the pixel).
2. ``resolvable = resolvable_register_width(gradient, pixel_um, linewidth)`` —
   the gradient cap (Case D: separation > linewidth), reused from
   :mod:`qssc.simulator`.
3. ``n_lines = min(nv_present, resolvable)`` — the physics cap.
4. MEASURE it, don't just compute it: synthesize the pixel's multi-line ODMR
   spectrum (``n_lines`` NVs one linewidth apart on the gradient grid) and run
   :func:`qssc.odmr_decompose.decompose_odmr` to count the resolved lines. The
   measured count IS ``register_width`` (>= 1). This mirrors the real-hardware
   path exactly — same line-counter, same physics.

The real path (``source="real"`` in the API) drives the ``RUN_ALL_ODMR`` FPGA
verb (sweeps MW frequency, reads all 64 detectors per point) and runs the same
``decompose_odmr`` per pixel — so sim and real produce the same
``ArrayCalibration`` shape.

Grounding: linewidth (5 MHz) and T2 (1.8 ms) come from :mod:`qssc.defects` (the
NV datasheet values). ``max_nv_per_microdiamond`` is a user-supplied model input
(the microdiamond product spec), flagged here, NOT a datasheet physics constant.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

from .defects import DefectProfile, get_profile
from .odmr_decompose import decompose_odmr
from .simulator import resolvable_register_width, BRIGHT_CODE, READOUT_NOISE
from .superres_odmr import superres_measured_width
from .bayesian_readout import bayesian_measured_width
from .detector_centroid import SubPixelModel, localize_spots, pad_grid_centers
from .noise_filters import (
    AntiNoiseConfig,
    NoiseEnvironment,
    effective_noise_sigma,
)

# Readout noise expressed on the ODMR contrast axis (0..1): the lock-in photocurrent
# noise (READOUT_NOISE, 16-bit counts) over the bright level (BRIGHT_CODE) -- the
# datasheet-grounded relative noise. A lone-NV bin has contrast ~0.03, so its
# single-shot SNR is 0.03 / 0.0125 ~= 2.4, BELOW the min_snr=3 detection bar -> it
# is NOT resolved at 1 shot. N-shot integration raises SNR by sqrt(N),
# so e.g. 16 shots -> SNR 9.6 -> the lone-NV bin is resolved. This is the physical
# reason integration raises the MEASURED width toward min(resolvable, nv_present).
READOUT_NOISE_REL: float = READOUT_NOISE / BRIGHT_CODE

# 64 die pixels (N_SITES in qssc.simulator); kept here to avoid an import cycle
# with the backend just for the constant.
N_PIXELS_DEFAULT = 64

# Two NV-count regimes, kept explicit so the model never conflates them:
#
# (a) FEW-NV COMPUTE stone -- the per-pixel microdiamond the QSSC compute path
#     uses. It carries O(100-300) NV centres (user-supplied product spec, a
#     MODEL INPUT not a datasheet physics constant). Under the Invention-5 field
#     gradient these NVs sit at DISTINCT, frequency-addressed resonances (one
#     per gradient bin, ~5 MHz / linewidth apart) -- the 2009 Neumann/Taminiau
#     regime: several frequency-tuned qubits + 2q gates from ONE stone, NOT one
#     ensemble qubit. ``MAX_NV_PER_MICRODIAMOND`` bounds this draw; the MEASURED
#     register width is the number of resolvable lines (capped by the gradient
#     resolution), not the raw NV count.
# (b) ENSEMBLE SENSING stone -- the 200 um bulk Adamas stone
#     (``count_nv_centers(200, 10) ~= 7.4e12`` NVs). There the lines pile into a
#     saturated continuum -> ONE ensemble qubit (magnetometry/sensing), NOT
#     compute. That regime is ``count_nv_centers`` in odmr_decompose; it is NOT
#     what ``calibrate_array`` draws below.
MAX_NV_PER_MICRODIAMOND = 300            # compute-stone upper NV count (100-300 range)
# Fab reality: ONE microdiamond per pixel site (the die seat holds one stone;
# "several per pixel" was the earlier bulk-ensemble sketch, superseded by the
# one-stone-per-site compute geometry). The draw places exactly one stone.
MAX_MICRODIAMONDS_PER_PIXEL = 1

# --- Physically-possible input limits (enforced in calibrate_array) ----------
# These bound what a user can ask for so the panel never reports a count the
# hardware/physics cannot deliver. They are PHYSICS/GEOMETRY floors+ceilings,
# not arbitrary UI caps -- a request outside them is silently clamped (the
# frontend mirrors the same limits so the slider never offers an impossible
# value in the first place).
#
# Gradient: the on-board MCP4921 coil driver tops out at GRAD_COIL_MAX_MHZ_PER_UM
# (full-scale DAC code 4095 -> 1.47 MHz/um at the 3.3 V VDD/VREF rail --
# issue #168-C; mirrors api._guards.GRAD_COIL_MAX_MHZ_PER_UM). Above this the
# coil cannot follow.
GRAD_COIL_MAX_MHZ_PER_UM = 1.47


def gradient_dac_code_from_mhz(grad_mhz_per_um: float) -> int:
    """Nearest 12-bit MCP4921 DAC code that produces the requested field
    gradient (inverse of the coil driver's code -> gradient map). Capped at
    4095 (coil full-scale -> GRAD_COIL_MAX_MHZ_PER_UM); 0 for a non-positive
    request (coil off). Matches the single-pixel calibration path's
    ``_gradient_from_code`` inverse: code/4095 * GRAD_COIL_MAX_MHZ_PER_UM."""
    if not grad_mhz_per_um or grad_mhz_per_um <= 0:
        return 0
    return max(0, min(4095, round(grad_mhz_per_um / GRAD_COIL_MAX_MHZ_PER_UM * 4095)))


# Microdiamond size: the stone must fit inside one die pixel. The die pad pitch
# is 2.5 mm, so a stone larger than ~1 mm would overlap its neighbour. 10 um is
# the smallest commercially-polished microdiamond that seats reliably.
PIXEL_SIZE_MIN_UM = 10.0
PIXEL_SIZE_MAX_UM = 1000.0
# NV ceiling: the compute-stone NV count scales with the stone VOLUME (NVs are a
# fixed <10 ppm fraction of the carbon lattice). Anchored at the product spec --
# 300 NV in a 200 um stone -- so nv_ceiling(d) = 300 * (d/200)**3. A request for
# more NVs than the stone can hold is physically impossible; clamp it.
NV_ANCHOR_COUNT = MAX_NV_PER_MICRODIAMOND     # 300 NV ...
NV_ANCHOR_SIZE_UM = 200.0                     # ... at a 200 um stone


def nv_ceiling_for_size(pixel_size_um: float) -> int:
    """Max NV centres a microdiamond of ``pixel_size_um`` can physically hold.

    Volume scaling off the 300-NV @ 200 um product anchor (<10 ppm NV grade).
    Never below 1. This is the ceiling on ``max_nv_per_microdiamond``.
    """
    d = max(float(pixel_size_um), 0.0)
    return max(1, round(NV_ANCHOR_COUNT * (d / NV_ANCHOR_SIZE_UM) ** 3))


def _linewidth_mhz_for(profile: str) -> float:
    """ODMR linewidth (MHz) for a profile short-name; 5.0 fallback."""
    try:
        return float(get_profile(profile).odmr_linewidth_hz * 1e-6)
    except Exception as exc:
        logger.debug("ODMR linewidth for %r unavailable, using 5.0 MHz: %s",
                     profile, exc)
        return 5.0


@dataclass
class PixelCalibration:
    """One pixel's measured calibration."""

    pixel_id: int
    nv_present: int          # NVs physically in the pixel (drawn, varies)
    microdiamonds: int       # how many microdiamonds were drawn in this pixel
    register_width: int      # MEASURED resolvable line count = usable qubits
    odmr_lines: list[float] = field(default_factory=list)  # resolved line freqs (GHz)
    # Sub-pixel centroiding: the sub-pixel (x, y) position of this pixel's
    # dip on the 64-pad grid (mm, grid-centered), if a SubPixelModel was used.
    # Imaging metadata only -- does NOT change register_width (within-pad spots
    # merge; centroiding localizes one spot, it does not resolve multiple).
    sub_pixel_position: Optional[tuple[float, float]] = None

    @property
    def n_qubits(self) -> int:
        return self.register_width


@dataclass
class ArrayCalibration:
    """The 64-pixel calibration (the per-pixel qubit-count measurement)."""

    profile: str
    gradient_mhz_per_um: float
    pixel_size_um: float
    seed: int
    temperature_k: float
    pixels: list[PixelCalibration] = field(default_factory=list)
    # N-shot lock-in averaging raises per-line SNR by
    # sqrt(N), so dimmer lone-NV bins clear the min_snr detection bar and the
    # MEASURED width rises toward min(resolvable, nv_present). 1 = no averaging
    # (identical to the default behaviour).
    integration_shots: int = 1
    # Sub-pixel detector model used. Reporting only --
    # spatial_channels = 64 * S is the detector's imaging resolution; it does NOT
    # change register_width (the per-pixel qubit count).
    sub_pixel_factor: int = 0        # 0 = no sub-pixel model (back-compat)
    spatial_channels: int = 0        # 64 * S when a SubPixelModel was used, else 0
    sub_pixel: Optional[dict] = None  # the SubPixelModel params (JSON round-trip)
    # Anti-noise stack: the environmental model + filter config the
    # calibration was measured under. Recording only on the calibration object --
    # the noise was already applied inside _pixel_odmr_spectrum (sim) / baked into
    # the codes matrix (real). None = no environmental model (back-compat).
    noise_env: Optional[dict] = None
    anti_noise: Optional[dict] = None

    @property
    def widths(self) -> list[int]:
        """``[register_width per pixel]`` — the per-pixel qubit counts."""
        return [p.register_width for p in self.pixels]

    @property
    def n_pixels(self) -> int:
        return len(self.pixels)

    @property
    def min_width(self) -> int:
        return min(self.widths, default=0)

    @property
    def max_width(self) -> int:
        return max(self.widths, default=0)

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "gradient_mhz_per_um": self.gradient_mhz_per_um,
            "pixel_size_um": self.pixel_size_um,
            "seed": self.seed,
            "temperature_k": self.temperature_k,
            "pixels": [asdict(p) for p in self.pixels],
            "integration_shots": self.integration_shots,
            "sub_pixel_factor": self.sub_pixel_factor,
            "spatial_channels": self.spatial_channels,
            "sub_pixel": self.sub_pixel,
            "noise_env": self.noise_env,
            "anti_noise": self.anti_noise,
            # Physically-possible caps the inputs were clamped to (mirrors the
            # frontend limits). Lets the UI show the achievable ceiling, not an
            # impossible request.
            "physics_limits": {
                "gradient_max_mhz_per_um": GRAD_COIL_MAX_MHZ_PER_UM,
                "pixel_size_min_um": PIXEL_SIZE_MIN_UM,
                "pixel_size_max_um": PIXEL_SIZE_MAX_UM,
                "nv_ceiling": nv_ceiling_for_size(self.pixel_size_um),
                "linewidth_mhz": _linewidth_mhz_for(self.profile),
            },
        }

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def from_dict(cls, d: dict) -> "ArrayCalibration":
        pixels = [PixelCalibration(**p) for p in d.get("pixels", [])]
        return cls(
            profile=d["profile"],
            gradient_mhz_per_um=float(d["gradient_mhz_per_um"]),
            pixel_size_um=float(d["pixel_size_um"]),
            seed=int(d["seed"]),
            temperature_k=float(d.get("temperature_k", 300.0)),
            pixels=pixels,
            integration_shots=int(d.get("integration_shots", 1)),
            sub_pixel_factor=int(d.get("sub_pixel_factor", 0)),
            spatial_channels=int(d.get("spatial_channels", 0)),
            sub_pixel=d.get("sub_pixel"),
            noise_env=d.get("noise_env"),
            anti_noise=d.get("anti_noise"),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "ArrayCalibration":
        return cls.from_dict(json.loads(Path(path).read_text()))


def _draw_nv_present(rng: np.random.Generator, pixel_id: int,
                     max_nv_per_microdiamond: int,
                     max_microdiamonds_per_pixel: int) -> tuple[int, int]:
    """Draw (microdiamonds, nv_present) for one pixel.

    One compute stone per pixel (``max_microdiamonds_per_pixel`` is 1 for the
    as-built die; kept as a parameter so the bulk-ensemble sketch can still be
    exercised). The stone carries a few-NV COMPUTE register: NVs drawn in the
    ``[max_nv_per_microdiamond/3, max_nv_per_microdiamond]`` range (100-300 at
    the default 300 cap) -- enough that, spread across the gradient bins, most
    bins hold 1-2 NVs (distinct frequency-addressed lines), not the 10^12 of the
    ensemble sensing stone. A per-pixel offset decorrelates neighbouring pixels
    so the 64 widths genuinely vary (not all equal).
    """
    n_stones = int(rng.integers(1, max_microdiamonds_per_pixel + 1))
    lo = max(1, max_nv_per_microdiamond // 3)
    nv_per_stone = []
    for _ in range(n_stones):
        # uniform in [lo, max] -- a stone's NV count anywhere in the compute range
        nv = int(rng.integers(lo, max_nv_per_microdiamond + 1))
        nv_per_stone.append(nv)
    return n_stones, sum(nv_per_stone)


def _pixel_odmr_spectrum(prof: DefectProfile, nv_present: int,
                         n_bins: int,
                         gradient_mhz_per_um: float, pixel_size_um: float,
                         rng: np.random.Generator,
                         n_points: int = 800,
                         contrast_single: float = 0.03,
                         noise_sigma: float = READOUT_NOISE_REL,
                         integration_shots: int = 1,
                         noise_env: Optional[NoiseEnvironment] = None,
                         anti_noise: Optional[AntiNoiseConfig] = None,
                         ) -> tuple[np.ndarray, np.ndarray, float]:
    """Synthesize one pixel's multi-line ODMR spectrum under the gradient.

    The gradient spreads NV resonances across the pixel by
    ``gradient * pixel_size`` (MHz) and tiles that span into ``n_bins`` bins one
    linewidth apart (Case D: separation == linewidth). The ``nv_present`` NVs are
    dropped into those bins (uniformly, with replacement) — a bin holding ``k``
    NVs is a line of contrast ``1 - (1 - contrast_single)**k`` (NV contrasts add
    and saturate), so lines where NVs pile up are brighter and clear the SNR bar
    easily while a lone-NV bin is marginal. This is the per-NV-additive model:
    the ensemble brightness is NOT shared evenly, it tracks how many NVs sit at
    each gradient-shifted frequency.

    Returns ``(freqs_ghz, contrast)`` in the convention
    :func:`qssc.odmr_decompose.decompose_odmr` expects (1 = bright, dips toward
    0). With ``gradient <= 0`` or ``n_bins <= 1`` all NVs collapse onto one
    ensemble dip at D (one resolvable qubit regardless of count).
    """
    D = prof.d_ghz
    lw_mhz = prof.odmr_linewidth_hz * 1e-6
    sigma_ghz = lw_mhz * 1e-3 / 2.355  # FWHM -> sigma (GHz)

    if gradient_mhz_per_um <= 0.0 or n_bins <= 1:
        spread_mhz = 0.0
        centres_ghz = np.array([D])
        # all nv_present NVs pile into the single ensemble line
        occ = np.array([nv_present])
    else:
        spread_mhz = gradient_mhz_per_um * pixel_size_um
        start_mhz = -spread_mhz / 2.0 + lw_mhz / 2.0
        centres_mhz = start_mhz + np.arange(n_bins) * lw_mhz
        centres_ghz = D + centres_mhz * 1e-3
        # drop nv_present NVs into the n_bins bins (uniform, with replacement)
        if nv_present > 0:
            picks = rng.integers(0, n_bins, size=nv_present)
            occ = np.bincount(picks, minlength=n_bins)
        else:
            occ = np.zeros(n_bins, dtype=int)

    # frequency axis covers the spread + margin
    half_span_ghz = max(spread_mhz * 1e-3, 6.0 * sigma_ghz, 0.04) + 0.01
    freqs = np.linspace(D - half_span_ghz, D + half_span_ghz, n_points)
    f_hz = freqs * 1e9
    sigma_hz = sigma_ghz * 1e9

    contrast = np.ones_like(freqs)
    for c_ghz, k in zip(centres_ghz, occ):
        if k <= 0:
            continue
        amp = 1.0 - (1.0 - contrast_single) ** int(k)  # NV contrasts add+sat
        c_hz = c_ghz * 1e9
        contrast = contrast - amp * np.exp(-((f_hz - c_hz) ** 2) / (2 * sigma_hz ** 2))
    contrast = np.clip(contrast, 0.0, 1.0)
    # N-shot lock-in averaging raises per-line SNR by sqrt(N) (the
    # noise on the averaged spectrum drops as 1/sqrt(N) for uncorrelated
    # thermal/Johnson readout noise). This lets dimmer lone-NV bins clear the
    # min_snr detection bar in decompose_odmr -> the MEASURED width rises toward
    # min(resolvable, nv_present). integration_shots=1 -> unchanged.
    # When an environmental model + filter stack is supplied, the
    # per-point sigma is the stack-composed effective sigma (servo/iso/shield/
    # feed-forward/common-mode attenuating the env channels on top of 1/sqrt(N)),
    # converted to the contrast axis (/ BRIGHT_CODE). With no env (None) the bare
    # noise_sigma/sqrt(N) is used -> bit-identical to the prior model.
    if noise_env is not None or anti_noise is not None:
        eff_noise = (effective_noise_sigma(READOUT_NOISE, noise_env, anti_noise,
                                            integration_shots) / BRIGHT_CODE)
    else:
        eff_noise = noise_sigma / math.sqrt(max(1, int(integration_shots)))
    contrast = contrast + rng.normal(0.0, eff_noise, size=freqs.size)
    contrast = np.clip(contrast, 0.0, 1.0)
    return freqs, contrast, float(eff_noise)


def calibrate_array(profile: DefectProfile | str = "NV",
                    *,
                    gradient_mhz_per_um: float,
                    pixel_size_um: float = 200.0,
                    seed: int = 42,
                    n_pixels: int = N_PIXELS_DEFAULT,
                    max_nv_per_microdiamond: int = MAX_NV_PER_MICRODIAMOND,
                    max_microdiamonds_per_pixel: int = MAX_MICRODIAMONDS_PER_PIXEL,
                    temperature_k: float = 300.0,
                    rng: Optional[np.random.Generator] = None,
                    integration_shots: int = 1,
                    sub_pixel: Optional[SubPixelModel] = None,
                    noise_env: Optional[NoiseEnvironment] = None,
                    anti_noise: Optional[AntiNoiseConfig] = None,
                    superres: Optional[float] = None,
                    bayesian: Optional[float] = None,
                    ) -> ArrayCalibration:
    """Calibrate all 64 pixels: measure each pixel's usable qubit register width.

    The width is the number of ODMR lines the field gradient makes resolvable in
    that pixel, capped by the NVs physically present. It is MEASURED by
    synthesizing each pixel's ODMR spectrum and counting the resolved lines with
    :func:`qssc.odmr_decompose.decompose_odmr` (the same line-counter the
    real-hardware path uses on the ``RUN_ALL_ODMR`` reply).

    Parameters mirror the backend's gradient knobs. ``gradient_mhz_per_um`` is
    the Invention-5 field gradient (the hardware that produces it is the
    gradient block on the main PCB); 0 gives one ensemble qubit per pixel.

    ``integration_shots``: N-shot lock-in averaging raises per-line
    SNR by sqrt(N), so dimmer lone-NV bins clear the ``min_snr`` bar and the
    MEASURED width rises toward ``min(resolvable, nv_present)``. 1 = no averaging
    (identical to the default behaviour).

    ``sub_pixel``: a :class:`~qssc.detector_centroid.SubPixelModel`
    for charge-sharing centroiding. When provided, the calibration reports
    ``spatial_channels = 64 * S`` (the detector's sub-pixel imaging resolution)
    and a per-pixel ``sub_pixel_position`` (the microdiamond's localized (x, y) on
    the pad grid). This is imaging metadata only -- it does NOT change
    ``register_width`` (within-pad spots merge; centroiding localizes one spot,
    not multiple). See :mod:`qssc.detector_centroid`.

    ``superres``: when set (a float ``0 < factor ≤ 1``, the
    super-resolution acceptance fraction), the line counter uses sparse OMP
    recovery (:mod:`qssc.superres_odmr`) over the known gradient-bin dictionary
    instead of the hard ``min_snr`` threshold, recovering marginal lone-NV lines
    down to ``factor · min_snr`` SNR + deconvolving overlapping lines → the
    MEASURED width rises toward ``min(resolvable, nv_present)``. ``None`` = the
    hard-threshold path (back-compat). Stacks with ``integration_shots`` /
    ``noise_env`` / ``anti_noise`` (all lower the noise floor).

    ``bayesian``: when set (a float ``0 < confidence < 1``, the
    per-family credible level), the line counter uses the Bayesian one-sided
    credible-interval detector (:mod:`qssc.bayesian_readout`) instead of the
    hard ``min_snr`` threshold — the one-sided matched-filter test at the same
    false-positive rate accepts lines at a lower SNR → more marginal lone-NV
    lines counted. ``None`` = the hard-threshold path (back-compat). An
    alternative estimator to ``superres`` (pick one; if both are set,
    ``superres`` takes precedence).
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    if rng is None:
        rng = np.random.default_rng(seed)
    # Clamp the gradient/pixel/NV inputs to physically-possible values so a
    # request (raw API JSON, or a UI that didn't mirror the limits) can never
    # produce a count the hardware/physics cannot deliver. The frontend applies
    # the same caps; this is defense-in-depth.
    gradient_mhz_per_um = float(np.clip(gradient_mhz_per_um, 0.0,
                                        GRAD_COIL_MAX_MHZ_PER_UM))
    pixel_size_um = float(np.clip(pixel_size_um, PIXEL_SIZE_MIN_UM,
                                  PIXEL_SIZE_MAX_UM))
    nv_cap = nv_ceiling_for_size(pixel_size_um)
    max_nv_per_microdiamond = int(max(1, min(int(max_nv_per_microdiamond), nv_cap)))
    linewidth_mhz = prof.odmr_linewidth_hz * 1e-6
    resolvable = resolvable_register_width(
        gradient_mhz_per_um, pixel_size_um, linewidth_mhz)
    # The 64-pad grid (for per-pixel sub-pixel positions). Drawn
    # once, fixed by the SubPixelModel pitch. A SEPARATE rng draws the sub-pixel
    # jitter so it never perturbs the main nv_present draw sequence (which would
    # change register_width and break reproducibility vs. sub_pixel=None).
    pad_centers = (pad_grid_centers(sub_pixel.pad_pitch_mm)
                   if sub_pixel is not None else None)
    s_factor = sub_pixel.space_bins() if sub_pixel is not None else 0
    s_channels = sub_pixel.spatial_channels() if sub_pixel is not None else 0
    sp_rng = (np.random.default_rng((seed * 1_000_003) + 7)
              if sub_pixel is not None else None)

    pixels: list[PixelCalibration] = []
    for i in range(n_pixels):
        n_stones, nv_present = _draw_nv_present(
            rng, i, max_nv_per_microdiamond, max_microdiamonds_per_pixel)
        # n_bins = the gradient resolution cap (Case D); nv_present NVs are
        # dropped into these bins. The MEASURED line count (decompose_odmr) is
        # the usable register width, capped by both the bin count and the NVs
        # present.
        n_bins = max(1, min(nv_present, resolvable)) if nv_present > 0 else 1
        freqs, contrast, eff_noise = _pixel_odmr_spectrum(
            prof, nv_present, n_bins, gradient_mhz_per_um, pixel_size_um, rng,
            integration_shots=integration_shots,
            noise_env=noise_env, anti_noise=anti_noise)
        if superres is not None and superres > 0.0:
            # Sparse OMP recovery over the gradient-bin dictionary.
            measured, comps = superres_measured_width(
                freqs, contrast, n_bins=n_bins,
                linewidth_hz=prof.odmr_linewidth_hz,
                gradient_mhz_per_um=gradient_mhz_per_um,
                pixel_size_um=pixel_size_um, d_ghz=prof.d_ghz,
                noise_sigma=max(eff_noise, 1e-6), min_snr=4.0,
                superres_factor=float(superres))
        elif bayesian is not None and 0.0 < bayesian < 1.0:
            # Bayesian one-sided credible-interval detector.
            measured, comps = bayesian_measured_width(
                freqs, contrast, n_bins=n_bins,
                linewidth_hz=prof.odmr_linewidth_hz,
                gradient_mhz_per_um=gradient_mhz_per_um,
                pixel_size_um=pixel_size_um, d_ghz=prof.d_ghz,
                noise_sigma=max(eff_noise, 1e-6),
                confidence=float(bayesian))
        else:
            comps = decompose_odmr(
                freqs, contrast,
                min_snr=4.0,
                linewidth_guess_mhz=linewidth_mhz,
                min_separation_mhz=linewidth_mhz * 0.8,
            )
            measured = max(1, len(comps))
        register_width = min(measured, n_bins)
        # odmr_lines are the qubit frequencies the register sits on -- one per
        # INDEPENDENT qubit. Lines beyond register_width are sub-cap structure
        # the gradient cannot resolve into separate qubits (separation <
        # linewidth -> envelope, not independent lines), so truncate to the
        # capped width: len(odmr_lines) == register_width always.
        odmr_lines = [round(c.freq_ghz, 6) for c in comps[:register_width]]
        # A sub-pixel (x, y) offset for this pixel's microdiamond on
        # the pad grid (drawn from the pad center +/- a sub-pixel jitter). The
        # centroiding would localize the pixel's dip here. Imaging metadata only.
        sub_pos: Optional[tuple[float, float]] = None
        if sub_pixel is not None and pad_centers is not None and sp_rng is not None:
            base = pad_centers[i % N_PIXELS_DEFAULT]
            jitter = sp_rng.normal(0.0, sub_pixel.pad_pitch_mm / (2.0 * max(1, s_factor)),
                                   size=2)
            sub_pos = (float(base[0] + jitter[0]), float(base[1] + jitter[1]))
        pixels.append(PixelCalibration(
            pixel_id=i,
            nv_present=nv_present,
            microdiamonds=n_stones,
            register_width=register_width,
            odmr_lines=odmr_lines,
            sub_pixel_position=sub_pos,
        ))
    return ArrayCalibration(
        profile=prof.short if isinstance(profile, str) else prof.short,
        gradient_mhz_per_um=float(gradient_mhz_per_um),
        pixel_size_um=float(pixel_size_um),
        seed=int(seed),
        temperature_k=float(temperature_k),
        pixels=pixels,
        integration_shots=int(integration_shots),
        sub_pixel_factor=int(s_factor),
        spatial_channels=int(s_channels),
        sub_pixel=sub_pixel.to_dict() if sub_pixel is not None else None,
        noise_env=noise_env.to_dict() if noise_env is not None else None,
        anti_noise=anti_noise.to_dict() if anti_noise is not None else None,
    )


def calibrate_array_from_codes(
    codes: np.ndarray,
    freqs_ghz: np.ndarray,
    profile: DefectProfile | str = "NV",
    *,
    gradient_mhz_per_um: float,
    pixel_size_um: float = 200.0,
    seed: int = 42,
    temperature_k: float = 300.0,
    min_snr: float = 4.0,
    integration_shots: int = 1,
    sub_pixel: Optional[SubPixelModel] = None,
    noise_env: Optional[NoiseEnvironment] = None,
    anti_noise: Optional[AntiNoiseConfig] = None,
    superres: Optional[float] = None,
    bayesian: Optional[float] = None,
    cap_to_resolvable: bool = True,
) -> ArrayCalibration:
    """Build an :class:`ArrayCalibration` from a MEASURED ``(n_points, 64)`` code
    matrix — the real-hardware path.

    ``codes[i, j]`` is the 16-bit AD7616 sample for pixel ``j`` at MW frequency
    point ``i`` (high = bright, dip on resonance); ``freqs_ghz[i]`` the MW
    frequency in GHz. Each column is normalised to the contrast convention
    :func:`decompose_odmr` expects (1 = bright, dips toward 0) and decomposed
    into its resolved lines; the line count IS that pixel's ``register_width``.
    This is the same line-counter :func:`calibrate_array` uses on the sim
    synthesis, so sim and real produce the same ``ArrayCalibration`` shape.

    ``nv_present`` / ``microdiamonds`` are NOT measurable from the photocurrent
    codes alone (only the resolved line count is), so they are reported as the
    measured width / 0 — the usable register width is what matters and is the
    field the backend consumes.

    ``integration_shots`` is recorded on the calibration (the caller
    is expected to pass the N-shot-AVERAGED ``codes`` matrix, whose noise is
    already reduced by sqrt(N); the sim path models this internally, the real
    path does the averaging in firmware/host before calling here).

    ``sub_pixel``: when provided, :func:`~qssc.detector_centroid.localize_spots`
    is run on each frequency-point row (the 64-pad snapshot) to report the
    sub-pixel (x, y) position of each pixel's dip -- the charge-sharing
    centroiding image. This is imaging metadata (``spatial_channels = 64 * S``)
    and does NOT change ``register_width``.

    ``cap_to_resolvable`` (default True) truncates each pixel's resolved lines
    to the gradient-register cap ``resolvable_register_width(...)`` -- the
    number of NVs an ADDRESSING field gradient can separate across the pixel. Set it False for a measurement whose lines do NOT come from
    an addressing gradient, so the cap must not prune them: the tensor
    gradiometer reads the 4 NV <111> Zeeman doublets split by
    the AMBIENT field (no addressing gradient -> ``gradient_mhz_per_um=0`` ->
    ``resolvable=1``), and those doublets are the lines the B-vector inversion
    needs; capping them to 1 would collapse the reconstruction. The qubit
    register-width calibration (the ``run-parallel`` path) keeps the default
    True so the gradient cap still bounds the qubit count.
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    codes = np.asarray(codes, dtype=float)
    freqs = np.asarray(freqs_ghz, dtype=float)
    n_points, n_pixels = codes.shape
    linewidth_mhz = prof.odmr_linewidth_hz * 1e-6
    # The gradient resolvable cap is a real hardware constraint (how many
    # linewidth-spaced bins fit in the pixel's gradient spread) and IS
    # measurable from gradient/pixel/linewidth -- unlike nv_present, which is
    # not readable from photocurrent codes. The real-path width is capped by it,
    # same as the sim path, so noise false-positives above the cap cannot inflate
    # the reported register width.
    resolvable = resolvable_register_width(
        gradient_mhz_per_um, pixel_size_um, linewidth_mhz)
    # When the caller opts out of the gradient-register cap (tensor gradiometry:
    # ambient-field <111> doublets, not gradient-binned NVs), do not truncate.
    width_cap = resolvable if cap_to_resolvable else n_pixels * 64 + 16
    # Per-frequency-point centroiding on the 64-pad rows. Each row
    # is one MW frequency; the dips across the 64 pads are the resonant pixels,
    # localized to sub-pixel (x, y) by charge-sharing centroiding.
    pad_centers = (pad_grid_centers(sub_pixel.pad_pitch_mm)
                   if sub_pixel is not None else None)
    s_factor = sub_pixel.space_bins() if sub_pixel is not None else 0
    s_channels = sub_pixel.spatial_channels() if sub_pixel is not None else 0
    spot_pos: dict[int, tuple[float, float]] = {}
    if sub_pixel is not None and pad_centers is not None:
        for i in range(n_points):
            spots = localize_spots(
                codes[i, :], pad_centers, sub_pixel.sigma_sheet_mm,
                min_snr=min_snr)
            for sp in spots:
                # attribute the spot to its nearest pad (pixel) -- the imaging
                # output is the sub-pixel position of each pixel's dip
                idx = int(np.argmin(
                    (pad_centers[:, 0] - sp.x_mm) ** 2 +
                    (pad_centers[:, 1] - sp.y_mm) ** 2))
                spot_pos.setdefault(idx, (sp.x_mm, sp.y_mm))
    pixels: list[PixelCalibration] = []
    # The bright baseline (no ODMR dip) is the fluorescence at the sweep edges:
    # the endpoint span = gradient_spread + 4*linewidth margin, so the first/last
    # ~10% of points are guaranteed off-resonance. Averaging those edges gives a
    # noise-robust brightness reference. Using the raw column MAX instead would
    # let a single +noise spike inflate the peak and depress the whole baseline
    # into a fake ~3% "pedestal" that the resolver traces as dozens of false
    # lines -- the edges are immune to that (no dip, just the bright floor).
    edge = max(5, n_points // 10)
    for j in range(n_pixels):
        col = codes[:, j]
        bright = float(np.mean(np.concatenate([col[:edge], col[-edge:]]))) or 1.0
        contrast = col / bright  # 1 = bright, dips toward 0
        if superres is not None and superres > 0.0:
            # Estimate the per-column noise from the off-resonance
            # edge (no dip there, just the bright floor) and run sparse OMP.
            edge_std = float(np.std(np.concatenate([col[:edge], col[-edge:]])))
            noise_sigma = max(edge_std / bright, 1e-6)
            measured, comps = superres_measured_width(
                freqs, contrast, n_bins=resolvable,
                linewidth_hz=prof.odmr_linewidth_hz,
                gradient_mhz_per_um=gradient_mhz_per_um,
                pixel_size_um=pixel_size_um, d_ghz=prof.d_ghz,
                noise_sigma=noise_sigma, min_snr=min_snr,
                superres_factor=float(superres))
            register_width = max(1, min(measured, width_cap))
        elif bayesian is not None and 0.0 < bayesian < 1.0:
            # Bayesian one-sided credible-interval detector.
            edge_std = float(np.std(np.concatenate([col[:edge], col[-edge:]])))
            noise_sigma = max(edge_std / bright, 1e-6)
            measured, comps = bayesian_measured_width(
                freqs, contrast, n_bins=resolvable,
                linewidth_hz=prof.odmr_linewidth_hz,
                gradient_mhz_per_um=gradient_mhz_per_um,
                pixel_size_um=pixel_size_um, d_ghz=prof.d_ghz,
                noise_sigma=noise_sigma, confidence=float(bayesian))
            register_width = max(1, min(measured, width_cap))
        else:
            comps = decompose_odmr(
                freqs, contrast,
                min_snr=min_snr,
                linewidth_guess_mhz=linewidth_mhz,
                min_separation_mhz=linewidth_mhz * 0.8,
            )
            register_width = max(1, min(len(comps), width_cap))
        # Truncate to the capped width (same as the sim path): lines beyond the
        # resolvable cap are not independent qubits. len(odmr_lines) == width.
        odmr_lines = [round(c.freq_ghz, 6) for c in comps[:register_width]]
        pixels.append(PixelCalibration(
            pixel_id=j,
            nv_present=register_width,   # not measurable from codes; report width
            microdiamonds=0,             # not measurable from codes
            register_width=register_width,
            odmr_lines=odmr_lines,
            sub_pixel_position=spot_pos.get(j),
        ))
    return ArrayCalibration(
        profile=prof.short,
        gradient_mhz_per_um=float(gradient_mhz_per_um),
        pixel_size_um=float(pixel_size_um),
        seed=int(seed),
        temperature_k=float(temperature_k),
        pixels=pixels,
        integration_shots=int(integration_shots),
        sub_pixel_factor=int(s_factor),
        spatial_channels=int(s_channels),
        sub_pixel=sub_pixel.to_dict() if sub_pixel is not None else None,
        noise_env=noise_env.to_dict() if noise_env is not None else None,
        anti_noise=anti_noise.to_dict() if anti_noise is not None else None,
    )


def best_uniform_width(widths: Sequence[int]) -> tuple[int, int]:
    """Pick the circuit width ``N`` that maximizes working qubits.

    For the "same circuit on all pixels" case, a circuit of width ``N`` can only
    run on pixels whose calibrated width is ``>= N``. The total working qubits
    is ``N * k`` where ``k = count(widths >= N)``. Return ``(N, k)`` at the
    argmax — the width that puts the most real qubits to work without
    truncating any pixel (truncation would break algorithms like Shor, so we
    never do it). ``N >= 1``; returns ``(0, 0)`` for an empty list.
    """
    ws = sorted(set(int(w) for w in widths if w > 0), reverse=True)
    best_n, best_k, best_score = 0, 0, 0
    for w in ws:
        k = sum(1 for x in widths if x >= w)
        score = w * k
        if score > best_score:
            best_n, best_k, best_score = w, k, score
    return best_n, best_k