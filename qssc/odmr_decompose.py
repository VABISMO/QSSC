"""ODMR spectral decomposition: resolve a per-pixel ODMR spectrum into its
individual NV (or other spin-defect) qubit lines.

The QSSC readout uses ONE global microwave antenna plus a per-pixel optical
detector. Across pixels the separation is spatial (each pixel's detector);
WITHIN a pixel the only handle is frequency -- sweep the MW, each NV's
fluorescence dips at its own resonance, and this module fits that dip spectrum
into a sum of lines, one per resolvable qubit.

This only resolves qubits down to the resolvability limit (line separation >
linewidth). A bulk ensemble (10^11+ NVs) collapses to one continuous line and
yields ONE qubit; a controlled-few-NV microdiamond yields a handful. The
``count_nv_centers`` helper below makes that explicit for a given particle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks, savgol_filter

# Diamond physical constants for the NV-count estimator.
_DIAMOND_DENSITY_G_CM3 = 3.52
_C_MOLAR_MASS = 12.0
_AVOGADRO = 6.02214076e23


@dataclass
class OdmrComponent:
    """One resolved qubit line from a pixel's ODMR spectrum."""

    freq_ghz: float       # resonance frequency (GHz)
    linewidth_mhz: float  # FWHM (MHz)
    contrast: float       # dip depth (0..1)
    snr: float            # contrast / noise floor

    def __repr__(self) -> str:
        return (
            f"NV(f={self.freq_ghz:.6f} GHz, lw={self.linewidth_mhz:.3f} MHz, "
            f"contrast={self.contrast:.4f}, snr={self.snr:.1f})"
        )


@dataclass
class BFieldEstimate:
    """Magnetic field recovered from a pixel's ODMR Zeeman splitting."""

    b_field_t: float          # recovered |B| (Tesla)
    splitting_mhz: float     # gap between the two Zeeman manifolds (MHz)
    f_low_ghz: float         # contrast-weighted centre of the lower manifold
    f_high_ghz: float        # contrast-weighted centre of the upper manifold
    n_lines_low: int         # number of resolved lines in the lower manifold
    n_lines_high: int        # number of resolved lines in the upper manifold
    resolvable: bool         # False if splitting < linewidth (B buried in line)

    def __repr__(self) -> str:
        return (
            f"B(|B|={self.b_field_t*1e3:.4f} mT, split={self.splitting_mhz:.2f} MHz, "
            f"f_lo={self.f_low_ghz:.6f}, f_hi={self.f_high_ghz:.6f}, "
            f"lines={self.n_lines_low}/{self.n_lines_high}, "
            f"resolvable={self.resolvable})"
        )


def b_field_from_odmr(
    components: list[OdmrComponent],
    d_ghz: float = 2.87,
    gamma_ghz_per_t: float = 28.0,
) -> Optional[BFieldEstimate]:
    """Recover the magnetic field from a decomposed ODMR spectrum.

    The two NV electron transitions (ms=0 <-> +-1) sit at D + gamma*B and
    D - gamma*B (Zeeman), so their SPLITTING = 2*gamma*B encodes |B| while the
    centre (D) is B-insensitive to first order.  This groups the resolved
    lines into the lower (f < D) and upper (f > D) manifold, takes the
    contrast-weighted mean of each, and returns

        B = (f_high - f_low) / (2 * gamma).

    Defaults match qssc.defects.NV_DIAMOND (D=2.87 GHz, gamma_e=28 GHz/T).

    Returns None if no lines are resolved.  ``resolvable`` is False when the
    splitting is smaller than the mean line width -- the two manifolds overlap
    and B is unreliable without linewidth deconvolution (the lock-in / long-T2
    regime still recovers small B from line-shape asymmetry, not peak fitting).
    """
    if not components:
        return None
    low = [c for c in components if c.freq_ghz < d_ghz]
    high = [c for c in components if c.freq_ghz >= d_ghz]
    # if all lines landed on one side, B is ~0 (manifolds overlap) or the
    # spectrum is a single ensemble line -- nothing to split.
    if not low or not high:
        f = components[0].freq_ghz
        return BFieldEstimate(0.0, 0.0, f, f, len(low), len(high), False)
    w_low = sum(c.contrast for c in low) or 1.0
    w_high = sum(c.contrast for c in high) or 1.0
    f_low = sum(c.freq_ghz * c.contrast for c in low) / w_low
    f_high = sum(c.freq_ghz * c.contrast for c in high) / w_high
    splitting_ghz = f_high - f_low
    splitting_mhz = splitting_ghz * 1e3
    b_t = splitting_ghz / (2.0 * gamma_ghz_per_t)
    mean_lw = sum(c.linewidth_mhz for c in components) / len(components)
    resolvable = splitting_mhz > mean_lw
    return BFieldEstimate(
        b_field_t=float(b_t), splitting_mhz=float(splitting_mhz),
        f_low_ghz=float(f_low), f_high_ghz=float(f_high),
        n_lines_low=len(low), n_lines_high=len(high), resolvable=bool(resolvable))


def count_nv_centers(diameter_um: float, nv_ppm: float) -> int:
    """Estimate the number of NV centres in a single spherical microdiamond.

    ``diameter_um`` particle diameter in micrometres; ``nv_ppm`` NV
    concentration in ppm (parts per million of carbon sites).  Returns the
    expected NV count (a large ensemble for any typical microdiamond).
    """
    radius_cm = (diameter_um * 1e-4) / 2.0
    volume_cm3 = (4.0 / 3.0) * np.pi * radius_cm**3
    mass_g = volume_cm3 * _DIAMOND_DENSITY_G_CM3
    moles_c = mass_g / _C_MOLAR_MASS
    atoms_c = moles_c * _AVOGADRO
    return int(round(atoms_c * nv_ppm * 1e-6))


def _gaussian_sum(freqs_ghz, *params):
    """Sum of Gaussian dips: params = [a0,f0,s0, a1,f1,s1, ...] (sigma in GHz)."""
    y = np.zeros_like(freqs_ghz)
    for i in range(0, len(params), 3):
        a, f, s = params[i], params[i + 1], params[i + 2]
        y = y + a * np.exp(-((freqs_ghz - f) ** 2) / (2.0 * s ** 2))
    return y


def decompose_odmr(
    freqs_ghz: np.ndarray,
    contrast: np.ndarray,
    noise_floor: Optional[float] = None,
    min_snr: float = 3.0,
    linewidth_guess_mhz: float = 2.0,
    min_separation_mhz: float = 1.5,
) -> list[OdmrComponent]:
    """Decompose a per-pixel ODMR spectrum into resolved qubit lines.

    Parameters
    ----------
    freqs_ghz : array of MW frequencies (GHz).
    contrast : array of fluorescence contrast (1 = bright, dips toward 0).
        Same convention as ``qssc.simulator.simulate_odmr``.
    noise_floor : noise sigma on the contrast (estimated if None).
    min_snr : minimum contrast/noise to accept a component as a real qubit.
    linewidth_guess_mhz : initial FWHM guess for each line (MHz).
    min_separation_mhz : reject duplicate peaks closer than this (MHz).

    Returns a list of ``OdmrComponent`` (the resolved qubits in this pixel),
    sorted by frequency.  Empty if the spectrum is a flat ensemble or no line
    clears the SNR bar.
    """
    freqs = np.asarray(freqs_ghz, dtype=float)
    dip = 1.0 - np.asarray(contrast, dtype=float)  # positive dips
    n = freqs.size
    if n < 8:
        return []

    # --- 1. estimate noise floor from the high-frequency residual ----------
    if noise_floor is None:
        smooth = savgol_filter(dip, window_length=min(11, n if n % 2 else n - 1), polyorder=3) \
            if n >= 11 else dip
        noise_floor = float(np.std(dip - smooth))
    if noise_floor <= 0:
        noise_floor = 1e-6

    # --- 2. smooth lightly for peak detection ------------------------------
    win = min(11, n if n % 2 else n - 1)
    smooth = savgol_filter(dip, window_length=win, polyorder=3) if n >= 11 else dip

    # --- 3. detect candidate peaks -----------------------------------------
    prominence = max(min_snr * noise_floor, 0.05 * float(smooth.max()))
    min_dist = max(1, int(round(min_separation_mhz * 1e-3 /
                               (freqs[1] - freqs[0]))))
    peaks, props = find_peaks(smooth, prominence=prominence, distance=min_dist)
    if peaks.size == 0:
        return []

    # --- 4. fit a sum of Gaussians, seeded from the detected peaks ---------
    sigma0 = linewidth_guess_mhz * 1e-3 / 2.355  # FWHM -> sigma (GHz)
    p0, lb, ub = [], [], []
    f_min, f_max = float(freqs.min()), float(freqs.max())
    for pk in peaks:
        a = float(smooth[pk])
        f = float(freqs[pk])
        p0 += [a, f, sigma0]
        lb += [0.0, f_min, 1e-5]
        ub += [1.0, f_max, (f_max - f_min)]
    try:
        popt, _ = curve_fit(_gaussian_sum, freqs, dip, p0=p0,
                            bounds=(lb, ub), maxfev=20000)
    except Exception:
        # fall back to the raw peak amplitudes if the global fit fails
        popt = p0

    # --- 5. accept components above the SNR bar ----------------------------
    components: list[OdmrComponent] = []
    for i in range(0, len(popt), 3):
        a, f, s = popt[i], popt[i + 1], popt[i + 2]
        if a <= 0 or s <= 0:
            continue
        fwhm_mhz = 2.355 * s * 1e3
        snr = a / noise_floor
        if snr < min_snr:
            continue
        components.append(OdmrComponent(
            freq_ghz=float(f), linewidth_mhz=float(fwhm_mhz),
            contrast=float(a), snr=float(snr)))
    components.sort(key=lambda c: c.freq_ghz)
    return components