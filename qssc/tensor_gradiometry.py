"""Invention 19 — tensor-gradient magnetic imaging (300 K).

Invention 1 recovers a **vector** B per pixel (the 4-axis NV⟨111⟩ doublet
reconstruction). With the on-PCB gradient coil (Invention 5) and the 8×8 pixel
grid at a known 2.5 mm pitch, the **spatial derivative** of that vector field is
also measurable: finite-differencing the recovered B-vectors across neighbouring
pixels yields the magnetic-field gradient **tensor** ``G_ij = ∂B_i/∂x_j`` (the
3×2 in-plane Jacobian; the full 3×3 tensor adds the vertical derivative, which a
known applied gradient from the coil supplies). This is a richer image than the
scalar |B| map: the tensor signature localizes and classifies a magnetic
source (a dipole has a characteristic tensor pattern; a current loop another).

This module computes the per-pixel gradient tensor from a measured B-vector map
and provides a **dipole localizer** — a nonlinear least-squares fit of the
point-dipole field model to the measured B-map, returning the source position
and moment. That is the application: magnetic-anomaly / current-source imaging
through the tensor, on top of the Invention-1 vector magnetometer, using the
Invention-5 gradient coil (already on the PCB, PR #79).

Physics grounding: the dipole field
``B(r) = (μ₀/4π) (3(m·r̂)r̂ − m) / |r|³`` with ``μ₀/4π = 1e-7 T·m/A``; pixel
positions from the 2.5 mm pitch (fab + ``qssc.magnetometry``); no fabricated
sensitivity numbers (the per-pixel noise comes from the NV linewidth via
:func:`qssc.magnetometry.measure_pixel`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from .defects import DefectProfile, get_profile
from .magnetometry import measure_pixel
from .sensors import _NV_AXES


MU0_4PI = 1e-7  # T·m/A


@dataclass
class TensorPixel:
    """One pixel's vector field + in-plane gradient tensor."""
    pixel: int
    x: int
    y: int
    b_vector_ut: list          # [bx, by, bz] microtesla
    jacobian_ut_mm: list       # 3x2 [[dBx/dx, dBx/dy], [dBy/dx, dBy/dy], [dBz/dx, dBz/dy]] µT/mm


@dataclass
class TensorGradientResult:
    """The 64-pixel tensor-gradient image + an optional dipole reconstruction."""
    profile: str
    grid: int
    n_pixels: int
    pitch_mm: float
    pixels: list[TensorPixel]
    # Dipole source reconstructed from the tensor image (None if not fit / no
    # dipole is detectable above the noise floor).
    dipole_x_mm: Optional[float] = None
    dipole_y_mm: Optional[float] = None
    dipole_z_mm: Optional[float] = None
    dipole_moment_am2: Optional[list] = None   # [mx, my, mz] A·m²
    fit_residual_ut: Optional[float] = None     # RMS residual of the vector fit (µT)
    fit_residual_mhz: Optional[float] = None    # RMS residual of the splits fit (MHz)

    def to_dict(self) -> dict:
        return {
            "profile": self.profile, "grid": self.grid, "n_pixels": self.n_pixels,
            "pitch_mm": self.pitch_mm,
            "pixels": [
                {"pixel": p.pixel, "x": p.x, "y": p.y,
                 "b_vector_ut": p.b_vector_ut, "jacobian_ut_mm": p.jacobian_ut_mm}
                for p in self.pixels
            ],
            "dipole_x_mm": self.dipole_x_mm, "dipole_y_mm": self.dipole_y_mm,
            "dipole_z_mm": self.dipole_z_mm,
            "dipole_moment_am2": self.dipole_moment_am2,
            "fit_residual_ut": self.fit_residual_ut,
            "fit_residual_mhz": self.fit_residual_mhz,
        }


def _dipole_field(points_mm: np.ndarray, x0: float, y0: float, z0: float,
                  moment_am2: Sequence[float]) -> np.ndarray:
    """Vector B (µT) at each (x, y, 0) pixel from a dipole at (x0, y0, z0) with
    moment ``m`` (A·m²). The pixels are on the z=0 plane; the dipole is at
    depth ``z0`` (the sensor plane-to-source distance, positive)."""
    m = np.asarray(moment_am2, dtype=float)
    rx = (points_mm[:, 0] - x0) * 1e-3
    ry = (points_mm[:, 1] - y0) * 1e-3
    rz = np.full_like(rx, -z0 * 1e-3)    # source below the z=0 plane
    r2 = rx * rx + ry * ry + rz * rz
    r2 = np.maximum(r2, 1e-12)
    r = np.sqrt(r2)
    rhat = np.stack([rx, ry, rz], axis=1) / r[:, None]
    m_dot_rhat = rhat @ m                 # (n,)
    # B = (mu0/4pi) (3 (m.r̂) r̂ - m) / r^3   [T]
    b_t = MU0_4PI * (3.0 * m_dot_rhat[:, None] * rhat - m[None, :]) / (r[:, None] ** 3)
    return b_t * 1e6                      # -> µT


def tensor_gradient_map(b_field_map_ut: np.ndarray, pitch_mm: float = 2.5
                        ) -> list[TensorPixel]:
    """Compute the per-pixel in-plane gradient tensor (3×2 Jacobian) of a
    ``(grid, grid, 3)`` B-vector map by central finite differences (forward /
    backward at the edges). Units: µT/mm."""
    b = np.asarray(b_field_map_ut, dtype=float)
    grid = b.shape[0]
    pixels: list[TensorPixel] = []
    for iy in range(grid):
        for ix in range(grid):
            # central differences, one-sided at the array edge
            if 0 < ix < grid - 1:
                dbx_dx = (b[iy, ix + 1, 0] - b[iy, ix - 1, 0]) / (2.0 * pitch_mm)
                dby_dx = (b[iy, ix + 1, 1] - b[iy, ix - 1, 1]) / (2.0 * pitch_mm)
                dbz_dx = (b[iy, ix + 1, 2] - b[iy, ix - 1, 2]) / (2.0 * pitch_mm)
            elif ix == 0:
                dbx_dx = (b[iy, 1, 0] - b[iy, 0, 0]) / pitch_mm
                dby_dx = (b[iy, 1, 1] - b[iy, 0, 1]) / pitch_mm
                dbz_dx = (b[iy, 1, 2] - b[iy, 0, 2]) / pitch_mm
            else:
                dbx_dx = (b[iy, ix, 0] - b[iy, ix - 1, 0]) / pitch_mm
                dby_dx = (b[iy, ix, 1] - b[iy, ix - 1, 1]) / pitch_mm
                dbz_dx = (b[iy, ix, 2] - b[iy, ix - 1, 2]) / pitch_mm
            if 0 < iy < grid - 1:
                dbx_dy = (b[iy + 1, ix, 0] - b[iy - 1, ix, 0]) / (2.0 * pitch_mm)
                dby_dy = (b[iy + 1, ix, 1] - b[iy - 1, ix, 1]) / (2.0 * pitch_mm)
                dbz_dy = (b[iy + 1, ix, 2] - b[iy - 1, ix, 2]) / (2.0 * pitch_mm)
            elif iy == 0:
                dbx_dy = (b[1, ix, 0] - b[0, ix, 0]) / pitch_mm
                dby_dy = (b[1, ix, 1] - b[0, ix, 1]) / pitch_mm
                dbz_dy = (b[1, ix, 2] - b[0, ix, 2]) / pitch_mm
            else:
                dbx_dy = (b[iy, ix, 0] - b[iy - 1, ix, 0]) / pitch_mm
                dby_dy = (b[iy, ix, 1] - b[iy - 1, ix, 1]) / pitch_mm
                dbz_dy = (b[iy, ix, 2] - b[iy - 1, ix, 2]) / pitch_mm
            pixels.append(TensorPixel(
                pixel=iy * grid + ix, x=ix, y=iy,
                b_vector_ut=[round(float(v), 6) for v in b[iy, ix]],
                jacobian_ut_mm=[
                    [round(float(dbx_dx), 6), round(float(dbx_dy), 6)],
                    [round(float(dby_dx), 6), round(float(dby_dy), 6)],
                    [round(float(dbz_dx), 6), round(float(dbz_dy), 6)],
                ],
            ))
    return pixels


def localize_dipole(b_field_map_ut: np.ndarray, pitch_mm: float = 2.5,
                    z_guess_mm: float = 15.0,
                    background_ut: Optional[Sequence[float]] = None,
                    ) -> Optional[dict]:
    """Fit a point-magnetic-dipole source to a ``(grid, grid, 3)`` B-vector map.

    Recovers ``(x0, y0, z0, mx, my, mz)`` by nonlinear least-squares against the
    analytic dipole field, with an optional uniform background subtracted first.
    Returns ``None`` if no dipole is detectable above the map's noise floor (the
    fit does not converge or the recovered moment is ~0). Otherwise a dict with
    the source position (mm), moment (A·m²), and the RMS residual (µT).
    """
    from scipy.optimize import least_squares
    b = np.asarray(b_field_map_ut, dtype=float)
    grid = b.shape[0]
    if background_ut is not None:
        b = b - np.asarray(background_ut, dtype=float)
    # pixel centres (mm), z=0 plane
    pts = np.array([
        [(ix - (grid - 1) / 2.0) * pitch_mm, (iy - (grid - 1) / 2.0) * pitch_mm]
        for iy in range(grid) for ix in range(grid)
    ])
    measured = b.reshape(grid * grid, 3)

    def resid(p):
        x0, y0, z0, mx, my, mz = p
        if z0 <= 0.1:                      # source must be below the plane
            return np.full(measured.size, 1e3)
        return (_dipole_field(pts, x0, y0, z0, [mx, my, mz]) - measured).ravel()

    # initial guess: the dipole sits below the peak-|B| pixel, at the z guess,
    # with a moment from the on-axis peak relation peak ≈ (mu0/4pi) 2m/z^3.
    norms = np.linalg.norm(measured, axis=1)
    peak = int(np.argmax(norms))
    x_init = float(pts[peak, 0])
    y_init = float(pts[peak, 1])
    peak_t = max(float(norms[peak]) * 1e-6, 1e-9)         # µT -> T
    z_g = max(z_guess_mm, 1.0) * 1e-3
    m_init = peak_t * (z_g ** 3) / (2.0 * MU0_4PI)        # A·m²
    p0 = [x_init, y_init, z_guess_mm, 0.0, 0.0, m_init]
    # keep the fit on/near the chip (lm wanders off-chip into a far local min
    # with a compensating huge moment). trf with bounds is robust here.
    ext = float((grid - 1) * pitch_mm)              # chip half-extent + margin
    lo = [-ext - pitch_mm, -ext - pitch_mm, 0.1, -10.0 * max(m_init, 1e-9),
          -10.0 * max(m_init, 1e-9), -10.0 * max(m_init, 1e-9)]
    hi = [ext + pitch_mm, ext + pitch_mm, 5.0 * z_guess_mm,
          10.0 * max(m_init, 1e-9), 10.0 * max(m_init, 1e-9),
          10.0 * max(m_init, 1e-9)]
    try:
        sol = least_squares(resid, p0, method="trf", bounds=(lo, hi),
                            max_nfev=4000)
    except Exception:  # noqa: BLE001 - a non-convergent fit is a "no dipole" result
        return None
    x0, y0, z0, mx, my, mz = sol.x
    moment_mag = math.sqrt(mx * mx + my * my + mz * mz)
    rms = float(np.sqrt(np.mean(sol.fun ** 2)))
    # reject a ~zero moment fit (no dipole above noise)
    if moment_mag < 1e-9:
        return None
    return {
        "dipole_x_mm": round(float(x0), 4),
        "dipole_y_mm": round(float(y0), 4),
        "dipole_z_mm": round(float(max(z0, 0.0)), 4),
        "dipole_moment_am2": [round(float(mx), 8), round(float(my), 8),
                              round(float(mz), 8)],
        "fit_residual_ut": round(rms, 6),
    }


def localize_dipole_from_splits(signal_splits_mhz: np.ndarray,
                                baseline_splits_mhz: np.ndarray,
                                sign_factors: np.ndarray,
                                pitch_mm: float = 2.5,
                                grid: int = 8,
                                gamma_hz_per_t: float = 28.0e9,
                                z_guess_mm: float = 15.0,
                                ) -> Optional[dict]:
    """Fit a point dipole to the **raw doublet splits** (the actual sign-
    ambiguity-free observables), in differential mode.

    ``signal_splits_mhz`` and ``baseline_splits_mhz`` are ``(grid, grid, 4)``
    arrays of the four NV⟨111⟩ doublet splits in MHz (signal = background +
    dipole, baseline = background only). ``sign_factors`` is the same shape and
    holds ``sgn(B_bg · n_i)`` per pixel/axis — the background fixes the polarity
    of each split's perturbation. Their difference is the dipole perturbation in
    split space; because the background fixes the sign of each ``B·n_i``, the
    differential split is linear in the dipole field
    (``Δs_i ≈ (2γ/1e6) (B_dip·n_i) sgn(B_bg·n_i)``), so the fit is a smooth
    nonlinear least-squares in ``(x0, y0, z0, mx, my, mz)`` — no sign-ambiguity
    search, no lossy vector reconstruction. This is the robust localizer; the
    tensor image (:func:`tensor_gradient_map`) visualizes the reconstructed
    vectors, but the source parameters come from the splits.
    """
    from scipy.optimize import least_squares
    sig = np.asarray(signal_splits_mhz, dtype=float)
    base = np.asarray(baseline_splits_mhz, dtype=float)
    signs = np.asarray(sign_factors, dtype=float)
    if sig.shape != base.shape or sig.shape[2] != 4:
        return None
    diff = (sig - base) * signs                        # polarity-corrected MHz
    pts = np.array([
        [(ix - (grid - 1) / 2.0) * pitch_mm, (iy - (grid - 1) / 2.0) * pitch_mm]
        for iy in range(grid) for ix in range(grid)
    ])
    axes = np.asarray(_NV_AXES)                        # (4, 3)
    measured = diff.reshape(grid * grid, 4)            # MHz

    def resid(p):
        x0, y0, z0, mx, my, mz = p
        if z0 <= 0.1:
            return np.full(measured.size, 1e3)
        b_dip_ut = _dipole_field(pts, x0, y0, z0, [mx, my, mz])  # (n, 3) µT
        b_dot_n = b_dip_ut @ axes.T                     # (n, 4) µT
        pred_mhz = 2.0 * gamma_hz_per_t * (b_dot_n * 1e-6) / 1e6
        return (pred_mhz - measured).ravel()

    # init: peak-|diff| pixel, z guess, moment from the peak split perturbation
    peak = int(np.argmax(np.linalg.norm(measured, axis=1)))
    x_init, y_init = float(pts[peak, 0]), float(pts[peak, 1])
    peak_split_mhz = max(float(np.max(np.abs(measured[peak]))), 1e-6)
    z_g = max(z_guess_mm, 1.0) * 1e-3
    # split ~ (2gamma/1e6) * (B_dip.n) ; B_dip_peak ~ 2*m*mu0_4pi/z^3 (on-axis)
    m_init = peak_split_mhz * 1e6 * (z_g ** 3) / (2.0 * gamma_hz_per_t * 2.0 * MU0_4PI)
    p0 = [x_init, y_init, z_guess_mm, 0.0, 0.0, m_init]
    try:
        sol = least_squares(resid, p0, method="lm", max_nfev=8000)
    except Exception:  # noqa: BLE001 - non-convergent fit = "no dipole"
        return None
    x0, y0, z0, mx, my, mz = sol.x
    moment_mag = math.sqrt(mx * mx + my * my + mz * mz)
    rms = float(np.sqrt(np.mean(sol.fun ** 2)))
    if moment_mag < 1e-9:
        return None
    return {
        "dipole_x_mm": round(float(x0), 4),
        "dipole_y_mm": round(float(y0), 4),
        "dipole_z_mm": round(float(max(z0, 0.0)), 4),
        "dipole_moment_am2": [round(float(mx), 8), round(float(my), 8),
                              round(float(mz), 8)],
        "fit_residual_mhz": round(rms, 6),
    }


def tensor_gradient_sweep(profile: DefectProfile | str = "NV",
                           b_field_map_ut: Optional[np.ndarray] = None,
                           baseline_map_ut: Optional[np.ndarray] = None,
                           pitch_mm: float = 2.5,
                           n_avg: int = 500,
                           seed: int = 42,
                           fit_dipole: bool = True,
                           differential: bool = True,
                           ) -> TensorGradientResult:
    """Run a tensor-gradient imaging sweep: measure the 64-pixel B-vector map
    (reusing the Invention-1 4-axis reconstruction), compute the per-pixel
    gradient tensor, and optionally fit a dipole source.

    ``b_field_map_ut`` is the ``(8, 8, 3)`` true B-field in µT (e.g. from
    :func:`qssc.magnetometry.synthetic_field_map`); when ``None`` a default
    gradient + dipole map is synthesized. ``n_avg`` is the per-pixel averaging
    (Invention 7, 1/√N noise).

    ``differential`` (default True) is the physically correct mode for imaging a
    small anomaly against a large background: a baseline map is measured first,
    then each pixel's signal is reconstructed locked to the baseline's sign
    branch (``reference_t``) and the two are subtracted — so the per-pixel sign
    ambiguity in the 4-axis reconstruction cancels and the dipole perturbation
    surfaces as a smooth tensor image. ``baseline_map_ut`` is the baseline field
    (default: the synthetic map with the dipole removed); in differential mode
    it is subtracted from the measured signal before the tensor + dipole fit.
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    rng = np.random.default_rng(seed)
    grid = 8
    if b_field_map_ut is None:
        # a resolvable source: ~2e-3 A·m² at 10 mm clears the per-pixel ~45 µT
        # linewidth floor (a weaker dipole is below the floor and images as zero).
        # Built with the full-vector dipole model so the splits fitter's forward
        # model is self-consistent with the data; a 50 µT Bz DC background fixes
        # the 4-axis sign branch (differential lock).
        pts = np.array([[(ix - (grid - 1) / 2.0) * pitch_mm,
                         (iy - (grid - 1) / 2.0) * pitch_mm]
                        for iy in range(grid) for ix in range(grid)])
        bg_ut = np.array([0.0, 0.0, 50.0])
        b_field_map_ut = (_dipole_field(pts, x0=2.0, y0=-1.0, z0=10.0,
                                        moment_am2=[0.0, 0.0, 2e-3])
                          .reshape(grid, grid, 3) + bg_ut)
    b_true = np.asarray(b_field_map_ut, dtype=float)
    grid = b_true.shape[0]
    lw = prof.odmr_linewidth_hz * 1e-6

    # baseline = the background with the dipole removed when differential and no
    # explicit baseline is given (same DC background, no dipole).
    if differential and baseline_map_ut is None:
        baseline_map_ut = np.broadcast_to(
            np.array([0.0, 0.0, 50.0]), (grid, grid, 3)).copy()
    base_map = (np.asarray(baseline_map_ut, dtype=float)
                if baseline_map_ut is not None else None)

    # measure each pixel (4-axis reconstruction + shot noise). In differential
    # mode, measure the baseline first and lock the signal's sign branch to it,
    # then subtract -> the dipole perturbation (smooth, sign-consistent). In
    # absolute mode, lock every pixel to the centre pixel's reconstruction so
    # the map is sign-consistent (a single global sign branch). The raw doublet
    # splits are also collected (sign-ambiguity-free observables) for the robust
    # dipole localizer. measure_pixel takes/expects Tesla; the field maps are in
    # µT, so convert in and back out.
    measured_map = np.zeros_like(b_true)
    sig_splits = np.zeros((grid, grid, 4))
    base_splits = np.zeros((grid, grid, 4))
    if not differential:
        _, ref_t, _ = measure_pixel(prof, b_true[grid // 2, grid // 2] * 1e-6,
                                    n_avg, lw, rng)
    for iy in range(grid):
        for ix in range(grid):
            b_sig_t = b_true[iy, ix] * 1e-6
            if differential and base_map is not None:
                s_base, b_base_t, _ = measure_pixel(
                    prof, base_map[iy, ix] * 1e-6, n_avg, lw, rng)
                s_sig, b_rec_t, _ = measure_pixel(
                    prof, b_sig_t, n_avg, lw, rng, reference_t=b_base_t)
                b_rec_t = b_rec_t - b_base_t          # perturbation (T)
                base_splits[iy, ix] = s_base
                sig_splits[iy, ix] = s_sig
            else:
                s_sig, b_rec_t, _ = measure_pixel(
                    prof, b_sig_t, n_avg, lw, rng, reference_t=ref_t)
                sig_splits[iy, ix] = s_sig
            measured_map[iy, ix] = b_rec_t * 1e6      # T -> µT

    pixels = tensor_gradient_map(measured_map, pitch_mm)
    result = TensorGradientResult(
        profile=prof.name, grid=grid, n_pixels=grid * grid,
        pitch_mm=pitch_mm, pixels=pixels)

    if fit_dipole:
        # robust path: fit the raw doublet splits (sign-ambiguity-free). Falls
        # back to the reconstructed-vector fit outside differential mode.
        fit = None
        if differential and base_map is not None:
            axes = np.asarray(_NV_AXES)                # (4, 3)
            # sgn(B_bg . n_i) per pixel/axis fixes the split-perturbation polarity
            sign_factors = np.sign(base_map @ axes.T)  # (grid, grid, 4)
            sign_factors[sign_factors == 0.0] = 1.0
            fit = localize_dipole_from_splits(
                sig_splits, base_splits, sign_factors, pitch_mm=pitch_mm,
                grid=grid, gamma_hz_per_t=prof.gyromagnetic_ratio,
                z_guess_mm=10.0)
        if fit is None:
            fit = localize_dipole(measured_map, pitch_mm=pitch_mm,
                                  z_guess_mm=10.0, background_ut=None)
        if fit is not None:
            result.dipole_x_mm = fit["dipole_x_mm"]
            result.dipole_y_mm = fit["dipole_y_mm"]
            result.dipole_z_mm = fit["dipole_z_mm"]
            result.dipole_moment_am2 = fit["dipole_moment_am2"]
            result.fit_residual_ut = fit.get("fit_residual_ut")
            result.fit_residual_mhz = fit.get("fit_residual_mhz")
    return result