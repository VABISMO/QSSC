"""Invention 13 — compressed-sensing / sparse super-resolution ODMR (300 K).

The per-pixel ODMR spectrum under a field gradient (Invention 5) is **sparse**:
``nv_present`` NVs (≤ 300) are dropped into ``n_bins`` gradient-shifted
frequency bins (``n_bins`` can reach ~300 at a steep gradient), so the line-
amplitude vector is k-sparse with ``k ≪ n_bins`` once the gradient is wider than
the NV count warrants. The default line counter
(:func:`qssc.odmr_decompose.decompose_odmr`) uses a **hard per-line SNR
threshold** (``min_snr=4``): a lone-NV bin whose single-shot SNR is below 4 is
discarded as noise, so the MEASURED register width is noise-bound well below the
``min(resolvable, nv_present)`` cap.

This module replaces the hard threshold with a **sparse-recovery estimator**
(orthogonal matching pursuit, OMP) over the *known* gradient-bin dictionary.
Because the line positions are known a priori (the gradient places one bin every
linewidth across the pixel), the problem is a sparse linear inverse
``dip ≈ A x`` where ``A`` is the Gaussian-line dictionary at the bin centres and
``x`` is the k-sparse line-amplitude vector. Joint least-squares over the
selected support **deconvolves overlapping lines** (two NVs in adjacent bins
whose Gaussians smear into one broad dip are resolved as two), and the sparsity
prior lets the per-atom acceptance threshold drop from ``min_snr`` (~4) to
``superres_factor · min_snr`` (~2.4) at a controlled false-positive rate —
recovering the *marginal* lone-NV lines that sit just below the hard bar.

What this does NOT do: recover lines whose SNR is below the noise floor
(``< ~2``). No estimator pulls a line out of pure noise — that is an
information-theoretic limit, not a threshold choice. The gain is the *marginal*
band (SNR ~2.4–4.0) plus deconvolution of overlapping lines, which is real and
grounded in the known dictionary. Stacks with Invention 7 (1/√N averaging) and
Invention 9 (anti-noise): both lower the noise floor, so more lines clear even
the lowered super-res threshold.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .odmr_decompose import OdmrComponent


def _gaussian_dict(freqs_ghz: np.ndarray, centres_ghz: np.ndarray,
                   sigma_ghz: float) -> np.ndarray:
    """Build the Gaussian-line dictionary: column j is the unit-norm Gaussian
    dip at ``centres_ghz[j]`` with width ``sigma_ghz``, sampled on ``freqs_ghz``.
    """
    f = freqs_ghz[:, None]                     # (n_points, 1)
    c = centres_ghz[None, :]                   # (1, n_bins)
    atoms = np.exp(-((f - c) ** 2) / (2.0 * sigma_ghz ** 2))
    # unit-norm columns so the OMP correlation is comparable across bins
    norms = np.linalg.norm(atoms, axis=0)
    norms[norms == 0.0] = 1.0
    return atoms / norms


def _gradient_bin_centres(d_ghz: float, n_bins: int, gradient_mhz_per_um: float,
                          pixel_size_um: float, linewidth_mhz: float
                          ) -> np.ndarray:
    """The known gradient-bin centres (GHz): one bin every linewidth across the
    gradient spread, mirroring ``array_calibration._pixel_odmr_spectrum``.
    """
    if n_bins <= 1 or gradient_mhz_per_um <= 0.0:
        return np.array([d_ghz])
    spread_mhz = gradient_mhz_per_um * pixel_size_um
    start_mhz = -spread_mhz / 2.0 + linewidth_mhz / 2.0
    centres_mhz = start_mhz + np.arange(n_bins) * linewidth_mhz
    return d_ghz + centres_mhz * 1e-3


def superres_decompose(freqs_ghz: np.ndarray, contrast: np.ndarray, *,
                        n_bins: int, linewidth_hz: float,
                        gradient_mhz_per_um: float, pixel_size_um: float,
                        d_ghz: float = 2.87,
                        noise_sigma: float = 0.0125,
                        min_snr: float = 4.0,
                        superres_factor: float = 0.6,
                        max_atoms: Optional[int] = None,
                        ) -> list[OdmrComponent]:
    """Sparse super-resolution recovery of the per-pixel ODMR line set.

    Solves ``dip ≈ A x`` (``dip = 1 - contrast``, ``A`` the Gaussian-line
    dictionary at the ``n_bins`` gradient-bin centres) for a k-sparse amplitude
    vector ``x`` via orthogonal matching pursuit. The per-atom acceptance
    threshold is ``superres_factor · min_snr · noise_sigma`` (the super-res
    lever: lower than the hard ``min_snr · noise_sigma`` bar because the
    sparsity prior + joint deconvolution control the false-positive rate).
    Iteration stops when the residual reaches the noise floor or ``max_atoms``
    bins are selected.

    Returns a list of :class:`~qssc.odmr_decompose.OdmrComponent` (a drop-in
    replacement for :func:`decompose_odmr`), one per recovered line, with the
    joint-fit amplitude and the recovered SNR. ``superres_factor=1.0`` recovers
    the hard-threshold behaviour (accept at ``min_snr``); ``< 1.0`` is the
    super-resolution gain.
    """
    freqs = np.asarray(freqs_ghz, dtype=float)
    contrast = np.asarray(contrast, dtype=float)
    # work in the dip domain: dip = 1 - contrast (the Gaussian line amplitudes)
    dip = np.clip(1.0 - contrast, 0.0, 1.0)
    linewidth_mhz = linewidth_hz * 1e-6
    sigma_ghz = linewidth_mhz * 1e-3 / 2.355  # FWHM -> sigma (matches the synthesizer)

    centres = _gradient_bin_centres(d_ghz, n_bins, gradient_mhz_per_um,
                                    pixel_size_um, linewidth_mhz)
    A = _gaussian_dict(freqs, centres, sigma_ghz)         # (n_points, n_bins)
    n_points, n_atoms = A.shape

    # per-atom amplitude acceptance: the super-res threshold. An atom is added
    # only if its correlation with the residual exceeds this; the joint LS refit
    # then deconvolves overlaps. noise_sigma is on the contrast (= dip) axis.
    accept = superres_factor * min_snr * noise_sigma
    noise_floor = noise_sigma * math.sqrt(n_points)        # residual stop norm
    cap = n_atoms if max_atoms is None else min(max_atoms, n_atoms)

    support: list[int] = []
    residual = dip.copy()
    while len(support) < cap:
        corr = A.T @ residual                              # (n_bins,)
        # mask out already-selected atoms
        for j in support:
            corr[j] = 0.0
        j_best = int(np.argmax(np.abs(corr)))
        if abs(corr[j_best]) < accept:
            break                                          # no atom above the bar
        support.append(j_best)
        # joint least-squares over the current support (deconvolves overlaps)
        Asub = A[:, support]
        x, *_ = np.linalg.lstsq(Asub, dip, rcond=None)
        residual = dip - Asub @ x
        if np.linalg.norm(residual) <= noise_floor:
            break

    if not support:
        return []
    # final amplitudes (unit-norm atoms -> contrast-axis amplitudes)
    Asub = A[:, support]
    x, *_ = np.linalg.lstsq(Asub, dip, rcond=None)
    comps: list[OdmrComponent] = []
    for j, amp in zip(support, x):
        if amp <= 0.0:
            continue
        comps.append(OdmrComponent(
            freq_ghz=float(centres[j]),
            linewidth_mhz=linewidth_mhz,
            contrast=float(min(amp, 1.0)),
            snr=float(amp / noise_sigma) if noise_sigma > 0 else float(amp),
        ))
    # sort by frequency for a stable, comparable output
    comps.sort(key=lambda c: c.freq_ghz)
    return comps


def superres_measured_width(freqs_ghz, contrast, *, n_bins, linewidth_hz,
                             gradient_mhz_per_um, pixel_size_um,
                             d_ghz=2.87, noise_sigma=0.0125,
                             min_snr=4.0, superres_factor=0.6,
                             max_atoms=None) -> tuple[int, list[OdmrComponent]]:
    """Recover the sparse line set and return ``(measured_width, components)``.

    ``measured_width = min(len(components), n_bins)`` — the same cap as the
    hard-threshold path, so the super-res gain is purely more lines clearing the
    (lowered) bar, never a violation of the resolvable-bin cap.
    """
    comps = superres_decompose(
        freqs_ghz, contrast, n_bins=n_bins, linewidth_hz=linewidth_hz,
        gradient_mhz_per_um=gradient_mhz_per_um, pixel_size_um=pixel_size_um,
        d_ghz=d_ghz, noise_sigma=noise_sigma, min_snr=min_snr,
        superres_factor=superres_factor, max_atoms=max_atoms)
    return min(len(comps), max(1, n_bins)), comps