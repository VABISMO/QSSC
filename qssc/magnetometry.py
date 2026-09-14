"""64-pixel vector magnetometer (Invention 1).

The chip's 8x8 detector array is 64 parallel ODMR magnetometers: each pixel
sweeps its NV <111> doublet through the global MW antenna and recovers the
local B-vector from the four axis doublets (the same 4-axis reconstruction as
the single-pixel compass in :mod:`qssc.sensors`, run independently per pixel).
The 64-channel TIA + 4x AD7616 returns all 64 samples in one parallel frame.

Two measurement modes extend the bare per-pixel |B.n| image:

* **differential** -- subtract a stored baseline map (the ambient/lab field),
  so a weak perturbing source (ferromagnetic object, current, anomaly) shows up
  against the strong DC background that otherwise swamps it.
* **average** -- average the recovered field across the array (mean +/- std),
  the single-number readout used for a global field reading / compass.

This is a physics model of what the hardware measures; sensitivity numbers come
from the DefectProfile (gyromagnetic_ratio, odmr_linewidth_hz) -- no fabricated
specs (see memory: datasheet-grounded-physics).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .defects import DefectProfile, get_profile
from .sensors import _NV_AXES  # the four NV <111> unit axes, shared with compass


@dataclass
class PixelMagnetometer:
    """One pixel's recovered vector magnetometer reading."""
    pixel: int
    x: int
    y: int
    b_vector_ut: list  # [bx, by, bz] in microtesla (reconstructed)
    b_mag_ut: float
    splits_mhz: list  # the 4 <111> doublet splits
    resolved_axes: int


@dataclass
class MagnetometerResult:
    """A full 64-pixel magnetometer sweep."""
    profile: str
    grid: int
    n_pixels: int
    mode: str
    n_avg: int
    linewidth_mhz: float
    sensitivity_ut: float
    differential: bool
    average: bool
    pixels: list  # list[PixelMagnetometer]
    image: list  # grid*grid flat |B| image (uT), row-major
    image_min_ut: float
    image_max_ut: float
    # array-average (average mode): mean |B| over the array + vector mean
    mean_b_mag_ut: float
    mean_b_vector_ut: list
    std_b_mag_ut: float
    stats: list = field(default_factory=list)
    note: str = ""


def _recover_b_vector(splits_mhz: Sequence[float], gamma_hz_per_t: float,
                      linewidth_mhz: float,
                      reference_t: Optional[Sequence[float]] = None
                      ) -> tuple[np.ndarray, int]:
    """Reconstruct the B-vector from the 4 <111> doublet splits via the
    least-squares sign-ambiguity search used by the compass (sensors.py).

    Only axes whose split clears the linewidth/2 resolution threshold feed the
    reconstruction; below that the doublet is unresolved (within the noise floor)
    and contributes nothing. Returns (B_reconstructed_T, n_resolved_axes).

    The four <111> axes have a discrete sign ambiguity: the unsigned doublet
    splits |B.n_i| do not uniquely fix B (the tetrahedral symmetry admits up to
    two B-vectors with identical splits for some directions). Single-pixel
    magnetometry accepts the lowest-residual sign combo; but for
    gradiometric / differential magnetometry a ``reference_t`` (the baseline
    reconstruction) locks the branch: among the sign combos whose residual is
    within the linewidth of the best, the one whose solution is CLOSEST to the
    reference is chosen. This is how a real differential magnetometer holds its
    direction: the reference measurement fixes the sign convention, so a small
    perturbation cannot flip the reconstructed vector by ~180 deg.
    """
    resolve_thr = float(linewidth_mhz) / 2.0
    resolved = [i for i, s in enumerate(splits_mhz) if s > resolve_thr]
    if len(resolved) < 3:
        return np.zeros(3), len(resolved)
    A = _NV_AXES[resolved]                              # (k, 3)
    b_measured = np.array([splits_mhz[i] for i in resolved]) * 1e6 / (2.0 * gamma_hz_per_t)
    best_x, best_resid = None, np.inf
    candidates: list = []  # (resid, sol) for the sign-ambiguity tie-break
    for mask in range(1 << len(resolved)):
        signs = np.array([1.0 if (mask >> k) & 1 else -1.0
                          for k in range(len(resolved))])
        sol, *_ = np.linalg.lstsq(A, signs * b_measured, rcond=None)
        resid = float(np.linalg.norm(A @ sol - signs * b_measured))
        candidates.append((resid, sol))
        if resid < best_resid:
            best_resid, best_x = resid, sol
    if reference_t is not None:
        ref = np.asarray(reference_t, dtype=float)
        # tie band: any combo within one linewidth-scale of the best residual is
        # a valid branch; pick the closest to the reference (sign lock).
        tol = max(best_resid * 1e-3, float(linewidth_mhz) * 1e6 / (2.0 * gamma_hz_per_t))
        valid = [(r, s) for r, s in candidates if r <= best_resid + tol]
        if valid:
            best_x = min(valid, key=lambda rs: float(np.linalg.norm(rs[1] - ref)))[1]
    return (best_x if best_x is not None else np.zeros(3)), len(resolved)


def b_vector_from_odmr_lines(lines_ghz: Sequence[float],
                             profile: DefectProfile | str = "NV",
                             linewidth_mhz: Optional[float] = None,
                             reference_t: Optional[Sequence[float]] = None
                             ) -> tuple[np.ndarray, int, list, float]:
    """Reconstruct the B-VECTOR of ONE pixel from its MEASURED ODMR line
    frequencies -- the real-hardware path (Invention 1 real / Invention 19).

    This is the host-side calculation the simulator's :func:`measure_pixel`
    replaces: instead of simulating the 4 <111> doublet splits from a known B,
    it takes the line centres ``decompose_odmr`` resolved from a real
    ``RUN_ALL_ODMR`` sweep and inverts them to B. Each NV <111> axis under a
    field B produces a doublet at D +/- gamma*(B.n_i): two lines symmetric
    about the zero-field splitting D, separated by delta_i = 2*gamma*|B.n_i|.
    So:

    1. detune each line from D (MHz),
    2. pair lines that are mirror-symmetric about D (within linewidth/2) into
       doublets -- the pair's split is ``|f_high - f_low|``,
    3. the (up to 4) distinct splits are the |B.n_i| projections of the 4 axes,
    4. search the axis-assignment (permutations) + sign ambiguity to find the
       B-vector with the lowest least-squares residual (the 4 axes are not
       labelled in a combined ensemble spectrum, so the assignment is unknown;
       the lowest-residual assignment is the physical one).

    A pixel with fewer than 3 resolved doublets (the bulk-ensemble collapse at
    low field -- one line, no splitting) CANNOT fix a 3-component vector and
    returns a zero vector with ``n_resolved_axes`` < 3 (the truthful "field too
    small to reconstruct the vector" result, not a fabricated vector).

    Returns ``(B_T, n_resolved_axes, splits_mhz, residual_T2)``.
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    gamma = prof.gyromagnetic_ratio
    d_hz = prof.zero_field_splitting
    lw = (linewidth_mhz if linewidth_mhz is not None
          else prof.odmr_linewidth_hz / 1e6)
    if not lines_ghz or len(lines_ghz) < 2:
        return np.zeros(3), 0, [], float("inf")
    # detunings from D in MHz
    det = [round((float(f) * 1e9 - d_hz) / 1e6, 4) for f in lines_ghz]
    pair_tol = float(lw) / 2.0
    used = set()
    splits: list[float] = []
    for i, di in enumerate(det):
        if i in used:
            continue
        # find a mirror partner at ~ -di (the other half of the doublet)
        best_j, best_err = -1, pair_tol
        for j in range(i + 1, len(det)):
            if j in used:
                continue
            err = abs(det[j] + di)
            if err < best_err:
                best_err, best_j = err, j
        if best_j >= 0:
            used.add(i); used.add(best_j)
            splits.append(round(abs(det[i] - det[best_j]), 4))  # = 2|di|
        else:
            # unpaired line: a single manifold line (other branch below the
            # detection bar) -> split = 2|di| is the best estimate, but flag it
            # as a half-doublet by using 2|di| only if it clears the threshold.
            if abs(di) > pair_tol:
                splits.append(round(2.0 * abs(di), 4))
    # distinct splits, largest first, capped at 4 (the 4 NV axes)
    splits = sorted(set(splits), reverse=True)[:4]
    n_res = min(len(splits), 4)
    if n_res < 3:
        return np.zeros(3), n_res, splits, float("inf")
    # search axis-assignment (permutations) + sign ambiguity for the
    # lowest-residual B-vector. A_i . B = +/- |B.n_i| -> lstsq over signs.
    from itertools import permutations
    b_proj_t = np.array(splits) * 1e6 / (2.0 * gamma)          # |B.n_i| in T
    best_x, best_resid = np.zeros(3), float("inf")
    for perm in permutations(range(4), n_res):
        A = _NV_AXES[list(perm)]                               # (n_res, 3)
        for mask in range(1 << n_res):
            signs = np.array([1.0 if (mask >> k) & 1 else -1.0
                              for k in range(n_res)])
            sol, *_ = np.linalg.lstsq(A, signs * b_proj_t, rcond=None)
            resid = float(np.linalg.norm(A @ sol - signs * b_proj_t))
            if resid < best_resid:
                best_resid, best_x = resid, sol
    # reference sign-lock (differential mode): pick the branch closest to the
    # baseline among the low-residual candidates.
    if reference_t is not None and best_resid < float("inf"):
        ref = np.asarray(reference_t, dtype=float)
        tol = max(best_resid * 1e-3, float(lw) * 1e6 / (2.0 * gamma))
        candidates = []
        for perm in permutations(range(4), n_res):
            A = _NV_AXES[list(perm)]
            for mask in range(1 << n_res):
                signs = np.array([1.0 if (mask >> k) & 1 else -1.0
                                  for k in range(n_res)])
                sol, *_ = np.linalg.lstsq(A, signs * b_proj_t, rcond=None)
                resid = float(np.linalg.norm(A @ sol - signs * b_proj_t))
                if resid <= best_resid + tol:
                    candidates.append((resid, sol))
        if candidates:
            best_x = min(candidates, key=lambda rs: float(
                np.linalg.norm(rs[1] - ref)))[1]
    # B in Tesla -> the caller scales to µT. Guard against a degenerate zero.
    if not np.any(np.abs(best_x) > 0):
        n_res = 0
    return best_x, n_res, splits, best_resid


def b_vector_map_from_array_calibration(calib, profile: DefectProfile | str = "NV",
                                        linewidth_mhz: Optional[float] = None
                                        ) -> np.ndarray:
    """Build the (grid, grid, 3) per-pixel B-VECTOR map (Tesla) the tensor
    gradiometer (Invention 19) consumes, from a cached
    :class:`qssc.array_calibration.ArrayCalibration`'s per-pixel measured
    ``odmr_lines``. Each pixel's lines are inverted to a B-vector by
    :func:`b_vector_from_odmr_lines`; pixels with too few resolved doublets get
    a zero vector (the ensemble collapse -- field too small to reconstruct).
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    lw = (linewidth_mhz if linewidth_mhz is not None
          else prof.odmr_linewidth_hz / 1e6)
    grid = 8
    fmap = np.zeros((grid, grid, 3))
    for p in calib.pixels:
        lines = p.odmr_lines or []
        b, _n, _s, _r = b_vector_from_odmr_lines(lines, prof, lw)
        r, c = divmod(p.pixel_id, grid)
        if r < grid and c < grid:
            fmap[r, c] = b
    return fmap


def measure_pixel(profile: DefectProfile, b_vector_t: Sequence[float],
                   n_avg: int, linewidth_mhz: float,
                   rng: np.random.Generator,
                   reference_t: Optional[Sequence[float]] = None
                   ) -> tuple[list, np.ndarray, int]:
    """Measure ONE pixel: simulate the 4-axis ODMR doublet splits (with the
    linewidth-limited shot noise for n_avg averages) and reconstruct B.

    ``reference_t`` locks the sign branch to a baseline reconstruction
    (differential/gradiometric mode); see :func:`_recover_b_vector`.

    Returns (splits_mhz, B_reconstructed_T, n_resolved_axes).
    """
    B = np.asarray(b_vector_t, dtype=float)
    gamma = profile.gyromagnetic_ratio
    split_sigma_mhz = float(linewidth_mhz) / math.sqrt(max(1, n_avg))
    splits: list = []
    for n in _NV_AXES:
        bpar = float(B @ n)
        split_true = 2.0 * gamma * abs(bpar) / 1e6       # MHz
        split_meas = max(0.0, split_true + rng.normal(0.0, split_sigma_mhz))
        splits.append(round(float(split_meas), 4))
    b_rec, n_res = _recover_b_vector(splits, gamma, float(linewidth_mhz),
                                    reference_t=reference_t)
    return splits, b_rec, n_res


def magnetometer_sweep(
    profile: DefectProfile | str = "NV",
    b_field_map_ut: Optional[np.ndarray] = None,
    baseline_map_ut: Optional[np.ndarray] = None,
    grid: int = 8,
    n_avg: int = 500,
    mode: str = "real",
    differential: bool = False,
    average: bool = False,
    rng: Optional[np.random.Generator] = None,
) -> MagnetometerResult:
    """Run a 64-pixel vector magnetometer sweep.

    ``b_field_map_ut`` is a (grid, grid, 3) array of per-pixel B-vectors in
    microtesla (the field the array is sampling). If None, a zero field is used
    (the readout then reports the noise floor). ``baseline_map_ut`` is the same
    shape; in differential mode it is subtracted from the measured field before
    reporting, so the output is the perturbation relative to the baseline.

    ``mode='sim'`` averages freely (cleaner readout, n_avg x8); ``mode='real'``
    is shot-noise-limited at the requested averages (the linewidth floor).
    ``differential`` subtracts the baseline. ``average`` reports the array
    mean +/- std (single-number readout) in addition to the per-pixel image.
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    rng = rng or np.random.default_rng()
    lw = prof.odmr_linewidth_hz / 1e6
    n_eff = n_avg * 8 if mode == "sim" else int(n_avg)
    sens_ut = (lw * 1e6) / (2.0 * prof.gyromagnetic_ratio) * 1e6 / math.sqrt(max(1, n_eff))

    if b_field_map_ut is None:
        b_field_map_ut = np.zeros((grid, grid, 3))
    b_field_map_ut = np.asarray(b_field_map_ut, dtype=float)
    if b_field_map_ut.shape != (grid, grid, 3):
        raise ValueError(f"b_field_map_ut must be ({grid},{grid},3), got {b_field_map_ut.shape}")

    pixels: list = []
    image: list = []
    mags_ut: list = []
    vec_sum = np.zeros(3)
    base_map = (np.asarray(baseline_map_ut, dtype=float)
                if baseline_map_ut is not None else None)
    for iy in range(grid):
        for ix in range(grid):
            B_true = b_field_map_ut[iy, ix] * 1e-6        # uT -> T
            if differential and base_map is not None:
                # differential magnetometry: measure the baseline FIRST, then
                # the signal locked to the baseline's sign branch (reference),
                # and subtract. The sign lock is what lets a small perturbation
                # surface against a strong DC background (see _recover_b_vector).
                B_base = base_map[iy, ix] * 1e-6
                _, b_base, _ = measure_pixel(prof, B_base, n_eff, lw, rng)
                splits, b_rec, n_res = measure_pixel(
                    prof, B_true, n_eff, lw, rng,
                    reference_t=b_base)
                b_rec_ut = (b_rec - b_base) * 1e6          # T -> uT, perturbation
                splits = []  # differential: perturbation only, splits N/A
                n_res = 0
            else:
                splits, b_rec, n_res = measure_pixel(prof, B_true, n_eff, lw, rng)
                b_rec_ut = b_rec * 1e6                     # T -> uT
            mag = float(np.linalg.norm(b_rec_ut))
            pixels.append(PixelMagnetometer(
                pixel=iy * grid + ix, x=ix, y=iy,
                b_vector_ut=[round(float(v), 3) for v in b_rec_ut],
                b_mag_ut=round(mag, 3),
                splits_mhz=[round(float(s), 4) for s in splits],
                resolved_axes=n_res,
            ))
            image.append({"x": ix, "y": iy, "value": round(mag, 4)})
            mags_ut.append(mag)
            vec_sum += b_rec_ut

    vals = mags_ut
    mean_mag = float(np.mean(vals)) if vals else 0.0
    std_mag = float(np.std(vals)) if vals else 0.0
    mean_vec = (vec_sum / (grid * grid)) if vals else np.zeros(3)

    stats = [
        {"k": "Mean |B|", "v": f"{mean_mag:.3f}", "unit": "µT"},
        {"k": "Std |B|", "v": f"{std_mag:.3f}", "unit": "µT"},
        {"k": "Peak |B|", "v": f"{max(vals):.3f}", "unit": "µT"},
        {"k": "Min |B|", "v": f"{min(vals):.3f}", "unit": "µT"},
        {"k": "Sensitivity", "v": f"{sens_ut:.4f}", "unit": "µT"},
        {"k": "Averages", "v": str(n_eff)},
        {"k": "Mode", "v": mode + ("+diff" if differential else "") +
                         ("+avg" if average else "")},
    ]
    note = (f"64-pixel vector magnetometer: each pixel recovers the B-vector "
            f"from its 4 NV <111> doublets (linewidth {lw:g} MHz, {n_eff} "
            f"averages -> {sens_ut:.3f} µT sensitivity). "
            f"{'Differential mode: baseline subtracted (perturbation only). ' if differential else ''}"
            f"{'Array-average reported. ' if average else ''}"
            f"All 64 AD7616 samples return in one parallel frame.")

    return MagnetometerResult(
        profile=prof.short, grid=grid, n_pixels=grid * grid, mode=mode,
        n_avg=n_eff, linewidth_mhz=float(lw), sensitivity_ut=round(sens_ut, 4),
        differential=bool(differential), average=bool(average),
        pixels=[_pixel_dict(p) for p in pixels], image=image,
        image_min_ut=round(min(vals), 3), image_max_ut=round(max(vals), 3),
        mean_b_mag_ut=round(mean_mag, 3),
        mean_b_vector_ut=[round(float(v), 3) for v in mean_vec],
        std_b_mag_ut=round(std_mag, 3), stats=stats, note=note,
    )


def _pixel_dict(p: PixelMagnetometer) -> dict:
    return {
        "pixel": p.pixel, "x": p.x, "y": p.y,
        "b_vector_ut": p.b_vector_ut, "b_mag_ut": p.b_mag_ut,
        "splits_mhz": p.splits_mhz, "resolved_axes": p.resolved_axes,
    }


def synthetic_field_map(grid: int = 8,
                        gradient_ut_mm: float = 8.0,
                        base_bx_ut: float = 25.0,
                        base_by_ut: float = 0.0,
                        base_bz_ut: float = -40.0,
                        dipole_moment_am2: float = 0.0,
                        dipole_x_mm: float = 2.0,
                        dipole_y_mm: float = 5.0,
                        dipole_z_mm: float = 15.0,
                        pitch_mm: float = 2.5) -> np.ndarray:
    """Build a (grid, grid, 3) B-vector field map (microtesla) for the array.

    A linear gradient + an optional vertical magnetic dipole offset, sampled
    at each pixel centre. This is the synthetic source the magnetometer images;
    real hardware would feed the measured per-pixel field directly. The dipole
    uses the same full-vector forward model
    (:func:`qssc.tensor_gradiometry._dipole_field`) the dipole localizer fits,
    so a synthetic demo field is self-consistent with the fit.
    """
    from .tensor_gradiometry import _dipole_field
    pts = np.array([
        [(ix - (grid - 1) / 2.0) * pitch_mm, (iy - (grid - 1) / 2.0) * pitch_mm]
        for iy in range(grid) for ix in range(grid)
    ])
    out = np.zeros((grid * grid, 3))
    out[:, 0] = base_bx_ut + gradient_ut_mm * np.array([ix for iy in range(grid) for ix in range(grid)])
    out[:, 1] = base_by_ut + gradient_ut_mm * 0.3 * np.array([iy for iy in range(grid) for ix in range(grid)])
    out[:, 2] = base_bz_ut
    if dipole_moment_am2 != 0.0:
        out += _dipole_field(pts, dipole_x_mm, dipole_y_mm, dipole_z_mm,
                            [0.0, 0.0, dipole_moment_am2])
    return out.reshape(grid, grid, 3)
    return out