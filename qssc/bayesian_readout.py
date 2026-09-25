"""Bayesian / quantum-limited readout (300 K).

The default line counter (:func:`qssc.odmr_decompose.decompose_odmr`) uses a
hard **two-sided** per-line SNR threshold (``min_snr=4``): a line is accepted
only if its peak contrast exceeds ``4 · noise_sigma``. That bar is conservative
— it is set high enough to suppress false positives across all gradient bins
without modelling the detection statistics — so marginal lone-NV lines whose
true SNR sits in the 2–4 band are discarded as noise even though they carry
real information.

This module replaces the hard bar with a **Bayesian per-bin credible-interval
detector** over the same known gradient-bin dictionary used by the sparse OMP estimator.
For each bin it computes the matched-filter amplitude estimate and its posterior
standard deviation (the noise averaged over the line profile, ``σ/‖A_j‖``), then
declares a line when the **one-sided** posterior probability ``P(contrast > 0)``
exceeds a confidence level — equivalently when the matched-filter SNR
``â_j / σ_j`` exceeds the one-sided z-score ``z(confidence)``. A Bonferroni
correction over the ``n_bins`` candidate bins keeps the family-wise false-
positive rate at ``1 − confidence``.

The lever: the one-sided test at the same false-positive rate as the hard
two-sided bar accepts lines at a **lower** matched-filter SNR (``z₀.₉₉ ≈ 2.33``
vs ``min_snr = 4``), and the matched filter integrates the noise over the line
profile (``σ_j = σ/‖A_j‖`` is well below the per-point ``σ``), so the effective
contrast threshold drops sharply — recovering the marginal lone-NV lines the
hard bar misses. This is the quantum-limited readout: it extracts every bit the
photon statistics carry, instead of a conservative point-estimate cut.

What this does NOT do: recover lines whose matched-filter SNR is below the
(z-corrected) noise floor. A line at matched-filter SNR < ~2.3 is still
indistinguishable from noise — that is the information limit, not a threshold
choice. Stacks with N-shot lock-in (1/√N, lowers σ) and the anti-noise stack
(lowers σ); complementary to the sparse OMP estimator (OMP jointly deconvolves overlaps,
this detector tests bins independently).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from scipy.stats import norm

from .odmr_decompose import OdmrComponent
from .superres_odmr import _gaussian_dict, _gradient_bin_centres


def bayesian_decompose(freqs_ghz: np.ndarray, contrast: np.ndarray, *,
                       n_bins: int, linewidth_hz: float,
                       gradient_mhz_per_um: float, pixel_size_um: float,
                       d_ghz: float = 2.87,
                       noise_sigma: float = 0.0125,
                       confidence: float = 0.99,
                       max_lines: Optional[int] = None,
                       ) -> list[OdmrComponent]:
    """Bayesian per-bin credible-interval recovery of the ODMR line set.

    For each gradient bin ``j`` the matched-filter amplitude estimate is
    ``â_j = (A_j · dip) / ‖A_j‖²`` (``dip = 1 − contrast``) with posterior std
    ``σ_j = noise_sigma / ‖A_j‖``. A line is declared when the one-sided
    posterior ``P(contrast_j > 0) > confidence_adj`` where ``confidence_adj``
    is the Bonferroni-corrected level over ``n_bins`` candidates — equivalently
    ``â_j / σ_j > z(confidence_adj)``.

    Returns a list of :class:`~qssc.odmr_decompose.OdmrComponent` (a drop-in
    replacement for :func:`decompose_odmr`), sorted by frequency. ``confidence``
    is the per-family confidence; raise it to suppress false positives (closer
    to the hard-threshold behaviour) or lower it to recover dimmer lines.
    """
    freqs = np.asarray(freqs_ghz, dtype=float)
    contrast = np.asarray(contrast, dtype=float)
    dip = np.clip(1.0 - contrast, 0.0, 1.0)
    linewidth_mhz = linewidth_hz * 1e-6
    sigma_ghz = linewidth_mhz * 1e-3 / 2.355

    centres = _gradient_bin_centres(d_ghz, n_bins, gradient_mhz_per_um,
                                    pixel_size_um, linewidth_mhz)
    A = _gaussian_dict(freqs, centres, sigma_ghz)        # (n_points, n_bins)
    col_norms = np.linalg.norm(A, axis=0)
    col_norms[col_norms == 0.0] = 1.0
    # matched-filter amplitude estimate per bin (least-squares on one atom)
    amp = (A.T @ dip) / (col_norms ** 2)                 # (n_bins,)
    sigma_j = noise_sigma / col_norms                     # posterior std per bin

    # Bonferroni-corrected one-sided threshold: keep the family-wise false-
    # positive rate at (1 - confidence) across n_bins independent tests.
    n_test = max(1, n_bins)
    conf_adj = min(0.999999, 1.0 - (1.0 - confidence) / n_test)
    z_thr = float(norm.ppf(conf_adj))                     # one-sided z-score

    comps: list[OdmrComponent] = []
    cap = n_bins if max_lines is None else min(max_lines, n_bins)
    for j in range(n_bins):
        if sigma_j[j] <= 0.0:
            continue
        snr_j = amp[j] / sigma_j[j]
        if snr_j <= z_thr or amp[j] <= 0.0:
            continue
        comps.append(OdmrComponent(
            freq_ghz=float(centres[j]),
            linewidth_mhz=linewidth_mhz,
            contrast=float(min(amp[j], 1.0)),
            snr=float(snr_j),
        ))
        if len(comps) >= cap:
            break
    comps.sort(key=lambda c: c.freq_ghz)
    return comps


def bayesian_measured_width(freqs_ghz, contrast, *, n_bins, linewidth_hz,
                             gradient_mhz_per_um, pixel_size_um,
                             d_ghz=2.87, noise_sigma=0.0125,
                             confidence=0.99, max_lines=None
                             ) -> tuple[int, list[OdmrComponent]]:
    """Recover the line set by Bayesian credible-interval detection and return
    ``(measured_width, components)`` with ``measured_width = min(len, n_bins)``
    — the same resolvable-bin cap as the other paths."""
    comps = bayesian_decompose(
        freqs_ghz, contrast, n_bins=n_bins, linewidth_hz=linewidth_hz,
        gradient_mhz_per_um=gradient_mhz_per_um, pixel_size_um=pixel_size_um,
        d_ghz=d_ghz, noise_sigma=noise_sigma, confidence=confidence,
        max_lines=max_lines)
    return min(len(comps), max(1, n_bins)), comps