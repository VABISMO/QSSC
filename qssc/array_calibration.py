"""Invention 4 + 5 — per-pixel array calibration (sim + real).

The QSSC die is 64 INDEPENDENT pixels at 300 K. Each pixel's usable qubit
register width is NOT a fixed constant — it is a MEASURED quantity: the number
of ODMR lines a field gradient (Invention 5) makes resolvable inside that pixel,
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
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from .defects import DefectProfile, get_profile
from .odmr_decompose import decompose_odmr
from .simulator import resolvable_register_width

# 64 die pixels (N_SITES in qssc.simulator); kept here to avoid an import cycle
# with the backend just for the constant.
N_PIXELS_DEFAULT = 64

# User-supplied product spec: a microdiamond can carry up to ~100 NVs. This is a
# MODEL INPUT (the chip's microdiamond product), NOT a datasheet physics
# constant. It bounds the per-pixel NV draw; the MEASURED register width is what
# is used and is capped by the gradient resolution regardless.
MAX_NV_PER_MICRODIAMOND = 100
# A pixel holds a small number of microdiamonds ("several per pixel" per the
# per-pixel register model); the draw places 1..MAX_MICRODIAMONDS_PER_PIXEL of
# them, each with up to MAX_NV_PER_MICRODIAMOND NVs.
MAX_MICRODIAMONDS_PER_PIXEL = 3


@dataclass
class PixelCalibration:
    """One pixel's measured calibration."""

    pixel_id: int
    nv_present: int          # NVs physically in the pixel (drawn, varies)
    microdiamonds: int       # how many microdiamonds were drawn in this pixel
    register_width: int      # MEASURED resolvable line count = usable qubits
    odmr_lines: list[float] = field(default_factory=list)  # resolved line freqs (GHz)

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
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "ArrayCalibration":
        return cls.from_dict(json.loads(Path(path).read_text()))


def _draw_nv_present(rng: np.random.Generator, pixel_id: int,
                     max_nv_per_microdiamond: int,
                     max_microdiamonds_per_pixel: int) -> tuple[int, int]:
    """Draw (microdiamonds, nv_present) for one pixel.

    The number of microdiamonds in the pixel is drawn first (1..max), then each
    carries up to ``max_nv_per_microdiamond`` NVs. The total ``nv_present`` is
    what the gradient must resolve. A per-pixel offset decorrelates neighbouring
    pixels so the 64 widths genuinely vary (not all equal).
    """
    n_stones = int(rng.integers(1, max_microdiamonds_per_pixel + 1))
    # NVs per microdiamond: log-normal-ish, median ~40% of the cap, heavy tail
    # toward the cap for a few stones. Rounded, >= 1.
    nv_per_stone = []
    for _ in range(n_stones):
        frac = float(rng.lognormal(mean=-0.8, sigma=0.6))
        frac = min(max(frac, 0.02), 1.0)
        nv_per_stone.append(max(1, int(round(frac * max_nv_per_microdiamond))))
    return n_stones, sum(nv_per_stone)


def _pixel_odmr_spectrum(prof: DefectProfile, nv_present: int,
                         n_bins: int,
                         gradient_mhz_per_um: float, pixel_size_um: float,
                         rng: np.random.Generator,
                         n_points: int = 800,
                         contrast_single: float = 0.03,
                         noise_sigma: float = 0.004,
                         ) -> tuple[np.ndarray, np.ndarray]:
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
    contrast = contrast + rng.normal(0.0, noise_sigma, size=freqs.size)
    contrast = np.clip(contrast, 0.0, 1.0)
    return freqs, contrast


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
                    ) -> ArrayCalibration:
    """Calibrate all 64 pixels: measure each pixel's usable qubit register width.

    The width is the number of ODMR lines the field gradient makes resolvable in
    that pixel, capped by the NVs physically present. It is MEASURED by
    synthesizing each pixel's ODMR spectrum and counting the resolved lines with
    :func:`qssc.odmr_decompose.decompose_odmr` (the same line-counter the
    real-hardware path uses on the ``RUN_ALL_ODMR`` reply).

    Parameters mirror the backend's gradient knobs. ``gradient_mhz_per_um`` is
    the Invention-5 field gradient (the hardware that produces it is the
    ``qssc-grad`` PCB variant); 0 gives one ensemble qubit per pixel.
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    if rng is None:
        rng = np.random.default_rng(seed)
    linewidth_mhz = prof.odmr_linewidth_hz * 1e-6
    resolvable = resolvable_register_width(
        gradient_mhz_per_um, pixel_size_um, linewidth_mhz)

    pixels: list[PixelCalibration] = []
    for i in range(n_pixels):
        n_stones, nv_present = _draw_nv_present(
            rng, i, max_nv_per_microdiamond, max_microdiamonds_per_pixel)
        # n_bins = the gradient resolution cap (Case D); nv_present NVs are
        # dropped into these bins. The MEASURED line count (decompose_odmr) is
        # the usable register width, capped by both the bin count and the NVs
        # present.
        n_bins = max(1, min(nv_present, resolvable)) if nv_present > 0 else 1
        freqs, contrast = _pixel_odmr_spectrum(
            prof, nv_present, n_bins, gradient_mhz_per_um, pixel_size_um, rng)
        comps = decompose_odmr(
            freqs, contrast,
            min_snr=3.0,
            linewidth_guess_mhz=linewidth_mhz,
            min_separation_mhz=linewidth_mhz * 0.8,
        )
        measured = max(1, len(comps))
        register_width = min(measured, n_bins)
        odmr_lines = [round(c.freq_ghz, 6) for c in comps]
        pixels.append(PixelCalibration(
            pixel_id=i,
            nv_present=nv_present,
            microdiamonds=n_stones,
            register_width=register_width,
            odmr_lines=odmr_lines,
        ))
    return ArrayCalibration(
        profile=prof.short if isinstance(profile, str) else prof.short,
        gradient_mhz_per_um=float(gradient_mhz_per_um),
        pixel_size_um=float(pixel_size_um),
        seed=int(seed),
        temperature_k=float(temperature_k),
        pixels=pixels,
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
    min_snr: float = 3.0,
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
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    codes = np.asarray(codes, dtype=float)
    freqs = np.asarray(freqs_ghz, dtype=float)
    n_points, n_pixels = codes.shape
    linewidth_mhz = prof.odmr_linewidth_hz * 1e-6
    pixels: list[PixelCalibration] = []
    for j in range(n_pixels):
        col = codes[:, j]
        peak = float(col.max()) or 1.0
        contrast = col / peak  # 1 = bright, dips toward 0
        comps = decompose_odmr(
            freqs, contrast,
            min_snr=min_snr,
            linewidth_guess_mhz=linewidth_mhz,
            min_separation_mhz=linewidth_mhz * 0.8,
        )
        register_width = max(1, len(comps))
        odmr_lines = [round(c.freq_ghz, 6) for c in comps]
        pixels.append(PixelCalibration(
            pixel_id=j,
            nv_present=register_width,   # not measurable from codes; report width
            microdiamonds=0,             # not measurable from codes
            register_width=register_width,
            odmr_lines=odmr_lines,
        ))
    return ArrayCalibration(
        profile=prof.short,
        gradient_mhz_per_um=float(gradient_mhz_per_um),
        pixel_size_um=float(pixel_size_um),
        seed=int(seed),
        temperature_k=float(temperature_k),
        pixels=pixels,
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