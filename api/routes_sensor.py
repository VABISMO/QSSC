"""Contactless field-imaging sensor endpoints — every one reads the same
64-pixel NV array through a different physical source: a ferromagnetic dipole,
the Earth's crustal anomaly field, a temperature field (NV D-shift), or a
current (Ampere). Magnetic fields penetrate fog/walls/tissue/packaging, so
these all "see" through non-magnetic media. Each returns a generic shape
(grid + cells + vmin/vmax + unit + stats + optional markers) consumed by the
SensorField heatmap component in the frontend.
"""
from __future__ import annotations

import os
import math
from typing import Optional

import numpy as np
from fastapi import APIRouter

from qssc import (
    get_profile, coherence_at_temperature,
    simulate_odmr, NoFPGADetected,
    compass_measure, gyro_measure,
    magnetometer_sweep, synthetic_field_map,
    b_vector_map_from_array_calibration,
    tensor_gradient_sweep, tensor_gradient_map, localize_dipole,
)
from qssc.array_calibration import calibrate_array_from_codes

from ._guards import connect_fpga

router = APIRouter()


def _sensor_as_dict(obj) -> dict:
    from dataclasses import asdict, is_dataclass
    return asdict(obj) if is_dataclass(obj) else dict(obj)


def _sensor_mode_n_avg(n_avg: int, mode: str) -> int:
    """Average count scaled by the FPGA environment selector.

    ``mode='sim'`` — the simulator is not shot-noise-limited, so it averages
    freely and returns a cleaner readout (n_avg x8); ``mode='real'`` — real
    hardware is shot-noise-limited, so the requested average count stands and
    the linewidth + shot noise are realistic. Both environments resolve the
    same physics, only the noise floor differs.
    """
    n = int(n_avg)
    return n * 8 if mode == "sim" else n


# 64 pixels span the 20 mm die -> 2.5 mm pixel pitch (per the canonical die).
_SENSOR_PIX_PITCH_MM = 2.5


def _sensor_pixel_xy_mm(ix: int, iy: int, grid: int = 8):
    """Centre coordinates (mm) of pixel (ix, iy) on a grid x grid array."""
    return ((ix - (grid - 1) / 2.0) * _SENSOR_PIX_PITCH_MM,
            (iy - (grid - 1) / 2.0) * _SENSOR_PIX_PITCH_MM)


def _sensor_field_noise_ut(profile: str, mode: str, n_avg: int = 500):
    """Field noise floor (uT) for the given environment + averages, plus the
    effective average count and the ODMR linewidth in MHz. This is the
    linewidth-limited sensitivity shared by every field-imaging sensor."""
    prof = get_profile(profile)
    lw = prof.odmr_linewidth_hz / 1e6
    gamma = prof.gyromagnetic_ratio
    n_eff = _sensor_mode_n_avg(n_avg, mode)
    noise = (lw * 1e6) / (2.0 * gamma) * 1e6 / np.sqrt(max(1, n_eff))
    return float(noise), n_eff, float(lw)


# NV D-resonance temperature coefficient used by the thermometry model. This is
# a well-established material constant of the NV centre, NOT a supplier-quoted
# device spec on this chip's datasheet.
_NV_D_TEMP_COEFF_HZ_PER_K = -74.0e3


@router.get("/api/compass")
def api_compass(
    profile: str = "NV",
    bx: float = 150.0, by: float = 50.0, bz: float = -100.0,  # uT (lab field)
    n_avg: int = 1000,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """ODMR compass: 4-axis <111> doublet split -> B-vector + heading.

    bx/by/bz are the ambient field in microtesla. Default ~188 uT lab field
    resolves >=3 NV <111> doublets at the ~5 MHz linewidth; dial to Earth's
    field (~25-65 uT) to see the linewidth limit (doublets unresolved).
    mode='sim' averages freely (cleaner); 'real' is shot-noise-limited.
    """
    rng = np.random.default_rng(seed)
    B = (float(bx) * 1e-6, float(by) * 1e-6, float(bz) * 1e-6)
    out = _sensor_as_dict(compass_measure(profile, b_vector_t=B,
                                          n_avg=_sensor_mode_n_avg(n_avg, mode),
                                          rng=rng))
    out["mode"] = mode
    return out


@router.get("/api/gyro")
def api_gyro(
    profile: str = "NV",
    rate_dps: float = 1.0,
    temperature_k: float = 300.0,
    free_precession_us: Optional[float] = None,
    n_avg: int = 100,
    mode: str = "real",
    seed: Optional[int] = None,
    hardware: bool = False,
):
    """Ramsey pi/2-T-pi/2 gyroscope: rotation rate -> accumulated phase.

    mode='sim' averages freely (cleaner phase readout); 'real' is
    shot-noise-limited at the requested averages. With ``hardware=True`` the
    Ramsey fringe is measured on a real board: the host runs ``RUN_SEQUENCE``
    (Ramsey) via the FPGA connection and fits the rotation rate from the ADC
    codes (no ``READ_GYRO`` RTL verb). No board -> ``no-fpga-detected`` body.
    """
    if hardware:
        port = os.getenv("QSSC_FPGA_PORT", "").strip()
        if not port:
            return {"error": "no-fpga-detected",
                    "message": "No FPGA detected — connect the board and set "
                               "QSSC_FPGA_PORT, or switch to Simulator."}
        try:
            fpga = connect_fpga(port=port, profile=profile,
                                temperature_k=temperature_k, seed=seed)
        except NoFPGADetected as exc:
            return {"error": "no-fpga-detected", "message": str(exc)}
        try:
            # free-precession window T (us -> ns); default ~T2 of the profile.
            t_us = float(free_precession_us) if free_precession_us else (
                coherence_at_temperature(get_profile(profile), temperature_k)[1] * 1e6)
            n_win = max(8, min(int(n_avg), 1024))
            out = fpga.read_gyro(0, int(round(t_us * 1000.0)), n_win)
        finally:
            fpga.close()
        out["mode"] = "hardware"
        return out
    rng = np.random.default_rng(seed)
    out = _sensor_as_dict(gyro_measure(
        profile, temperature_k=float(temperature_k),
        rotation_rate_dps=float(rate_dps),
        free_precession_us=free_precession_us,
        n_averages=_sensor_mode_n_avg(n_avg, mode), rng=rng))
    out["mode"] = mode
    return out


@router.get("/api/magneto")
def api_magneto(
    profile: str = "NV",
    n_avg: int = 500,
    gradient_ut_mm: float = 8.0,
    base_bx_ut: float = 25.0,
    base_bz_ut: float = -40.0,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """Magneto-optic image: per-pixel |B.n| across the 8x8 array.

    Models a spatially-varying field (linear gradient + dipole offset) sampled
    by each of the 64 detector pixels through its ODMR doublet -- the physics
    model of the parallel 64-channel AD7616 frame readout -> 8x8 field image.
    mode='sim' averages freely (cleaner per-pixel readout); 'real' is
    shot-noise-limited at the requested averages.
    """
    prof = get_profile(profile)
    rng = np.random.default_rng(seed)
    gamma = prof.gyromagnetic_ratio
    lw_mhz = prof.odmr_linewidth_hz / 1e6
    n_eff = _sensor_mode_n_avg(n_avg, mode)
    split_sigma_mhz = lw_mhz / np.sqrt(max(1, n_eff))
    sens_ut = (lw_mhz * 1e6) / (2.0 * gamma) * 1e6 / np.sqrt(max(1, n_eff))
    n_axis = np.array([1, 1, 1]) / np.sqrt(3.0)
    grid = 8
    pixels = []
    src = np.array([2.0, 5.0])
    for iy in range(grid):
        for ix in range(grid):
            p = np.array([float(ix), float(iy)])
            dipole = 12.0 / (np.linalg.norm(p - src) + 1.2)
            bx = base_bx_ut + gradient_ut_mm * ix + dipole * 0.4
            by = gradient_ut_mm * 0.3 * iy
            bz = base_bz_ut + dipole
            B = np.array([bx, by, bz]) * 1e-6
            bpar = float(B @ n_axis)
            split_true = 2.0 * gamma * abs(bpar) / 1e6
            split_meas = max(0.0, split_true + rng.normal(0.0, split_sigma_mhz))
            contrast = 0.05 * np.exp(-(split_true / (3.0 * lw_mhz)) ** 2)
            pixels.append({"pixel": iy * grid + ix, "x": ix, "y": iy,
                           "b_proj_ut": round(abs(bpar) * 1e6, 3),
                           "split_mhz": round(split_meas, 4),
                           "contrast": round(float(contrast), 5)})
    centre_B = np.array([base_bx_ut + gradient_ut_mm * 3.5,
                         gradient_ut_mm * 0.3 * 3.5,
                         base_bz_ut + 12.0 / (np.linalg.norm(
                             np.array([3.5, 3.5]) - src) + 1.2)]) * 1e-6
    f, c = simulate_odmr(prof, freqs_ghz=None,
                         b_field_t=abs(float(centre_B @ n_axis)),
                         linewidth_mhz=lw_mhz, contrast_depth=0.05)
    return {
        "profile": prof.short, "grid": grid, "n_pixels": grid * grid,
        "n_avg": n_eff, "mode": mode, "linewidth_mhz": float(lw_mhz),
        "sensitivity_ut": round(float(sens_ut), 4), "pixels": pixels,
        "dip_curve": {"frequencies_ghz": [round(float(x), 5) for x in f],
                      "contrast": [round(float(x), 5) for x in c]},
        "note": (f"Per-pixel |B.n| over the 8x8 graphene/MoS2 array "
                 f"(n_axis=[1,1,1]/sqrt3); field = {gradient_ut_mm:g} uT/mm "
                 f"gradient + dipole offset; linewidth {lw_mhz:g} MHz, {n_eff} "
                 f"averages ({mode} environment) -> sensitivity {sens_ut:.2f} uT. "
                 f"The FPGA returns all 64 AD7616 samples in one parallel frame."),
    }


@router.get("/api/sensor/magneto")
def api_sensor_magneto(
    profile: str = "NV",
    gradient_ut_mm: float = 8.0,
    base_bx_ut: float = 25.0,
    base_by_ut: float = 0.0,
    base_bz_ut: float = -40.0,
    dipole_moment_am2: float = 0.02,
    dipole_x_mm: float = 2.0,
    dipole_y_mm: float = 5.0,
    dipole_z_mm: float = 15.0,
    n_avg: int = 500,
    mode: str = "real",
    differential: bool = False,
    average: bool = True,
    seed: Optional[int] = None,
):
    """64-pixel VECTOR magnetometer.

    Unlike the simple /api/magneto |B.n| projection, each of the 64 pixels
    recovers the full B-VECTOR from its 4 NV <111> doublets (4-axis
    least-squares reconstruction, the compass physics run independently per
    pixel). Returns an 8x8 |B| image + per-pin B-vectors + the array mean/std.

    ``differential`` subtracts the linear-gradient baseline (base_bx/by/bz +
    gradient) so a weak dipole source shows up against the strong DC background
    that would otherwise swamp it. ``average`` reports the array mean +/- std
    (the single-number field reading). ``mode='sim'`` averages freely;
    ``mode='real'`` is shot-noise-limited at the linewidth floor.
    """
    rng = np.random.default_rng(seed)
    fmap = synthetic_field_map(
        gradient_ut_mm=gradient_ut_mm, base_bx_ut=base_bx_ut,
        base_by_ut=base_by_ut, base_bz_ut=base_bz_ut,
        dipole_moment_am2=dipole_moment_am2,
        dipole_x_mm=dipole_x_mm, dipole_y_mm=dipole_y_mm,
        dipole_z_mm=dipole_z_mm)
    baseline = None
    if differential:
        # the baseline is the smooth gradient without the dipole perturbation
        baseline = synthetic_field_map(
            gradient_ut_mm=gradient_ut_mm, base_bx_ut=base_bx_ut,
            base_by_ut=base_by_ut, base_bz_ut=base_bz_ut,
            dipole_moment_am2=0.0)
    res = magnetometer_sweep(
        profile=profile, b_field_map_ut=fmap, baseline_map_ut=baseline,
        n_avg=n_avg, mode=mode, differential=differential, average=average,
        rng=rng)
    return {
        "profile": res.profile, "grid": res.grid, "n_pixels": res.n_pixels,
        "mode": res.mode, "n_avg": res.n_avg, "linewidth_mhz": res.linewidth_mhz,
        "sensitivity_ut": res.sensitivity_ut, "differential": res.differential,
        "average": res.average,
        "pixels": res.pixels, "image": res.image,
        "image_min_ut": res.image_min_ut, "image_max_ut": res.image_max_ut,
        "mean_b_mag_ut": res.mean_b_mag_ut, "mean_b_vector_ut": res.mean_b_vector_ut,
        "std_b_mag_ut": res.std_b_mag_ut, "stats": res.stats, "note": res.note,
    }


@router.get("/api/sensor/tensor")
def api_sensor_tensor(
    profile: str = "NV",
    source: str = "real",
    pitch_mm: float = 2.5,
    z_guess_mm: float = 15.0,
    n_avg: int = 500,
    gradient_ut_mm: float = 0.0,
    base_bx_ut: float = 0.0,
    base_by_ut: float = 0.0,
    base_bz_ut: float = 50.0,
    dipole_moment_am2: float = 0.002,
    dipole_x_mm: float = 2.0,
    dipole_y_mm: float = -1.0,
    dipole_z_mm: float = 10.0,
    differential: bool = True,
    seed: Optional[int] = None,
):
    """64-pixel TENSOR gradiometer + dipole localization.

    The per-pixel 3x2 B-gradient Jacobian (µT/mm) of the 8x8 B-VECTOR map,
    plus a point-dipole source fit. The B-vector map is the RECOVERED field
    each pixel measures from its 4 NV <111> Zeeman doublets (the same 4-axis
    reconstruction as the vector magnetometer):

    * ``source='real'`` (default): the 4-axis doublet reconstruction is
      shot-noise-limited at the linewidth floor for ``n_avg`` averages -- the
      real-hardware noise model (the actual board runs ``RUN_ALL_ODMR`` under a
      uniform B-field and the host inverts each pixel's resolved doublets to a
      B-vector via :func:`qssc.b_vector_from_odmr_lines`; the simulator models
      the same doublets with the linewidth-limited shot noise here).
    * ``source='sim'``: averages freely (n_avg x8, cleaner readout) -- the
      ideal-noise demo mode.

    ``differential`` (default) is the physically correct mode for imaging a
    small anomaly against a large background: a baseline map is measured first,
    each pixel's signal is reconstructed locked to the baseline's sign branch
    and the two subtracted, so the per-pixel 4-axis sign ambiguity cancels and
    the dipole perturbation surfaces as a smooth tensor image. The dipole
    source is fit to the raw doublet SPLITS (the sign-ambiguity-free
    observables), not the reconstructed vectors -- the robust localizer
    (:func:`qssc.localize_dipole_from_splits`).
    """
    grid = 8
    prof = get_profile(profile)
    lw = prof.odmr_linewidth_hz / 1e6
    # sim mode averages freely (n_avg x8); real is shot-noise-limited (n_avg).
    n_eff = int(n_avg) * (8 if source == "sim" else 1)
    sens_ut = (lw * 1e6) / (2.0 * prof.gyromagnetic_ratio) * 1e6 / math.sqrt(
        max(1, n_eff))

    if source == "real":
        # Real-hardware path: the board runs RUN_ALL_ODMR (one simultaneous-
        # sampling sweep of all 64 pixels), the host decomposes each pixel's
        # column into its ODMR lines, inverts the 4 NV <111> doublets to a
        # per-pixel B-vector (b_vector_map_from_array_calibration), and the
        # tensor gradient + dipole fit run on the MEASURED map. No synthetic
        # field is injected -- the dipole_moment_am2/dipole_*_mm scenario knobs
        # are sim-only; the real path images whatever field is actually on the
        # bench. The addressing gradient coil is NOT programmed here (it would
        # add the register-width gradient and swamp the ambient Zeeman split
        # the vector reconstruction reads); the ambient/Earth field splits the
        # doublets. differential is honoured via the dipole localizer's
        # background fit (a second sweep with the anomaly removed is not
        # possible on a fixed bench), not a baseline subtraction.
        port = os.getenv("QSSC_FPGA_PORT", "").strip()
        if not port:
            return {"error": "no-fpga-detected",
                    "message": ("No FPGA detected — connect the board and set "
                                "QSSC_FPGA_PORT, or use source=sim.")}
        try:
            fpga = connect_fpga(port=port, profile=profile,
                                temperature_k=300.0,
                                seed=int(seed if seed is not None else 42))
        except NoFPGADetected as exc:
            return {"error": "no-fpga-detected", "message": str(exc)}
        try:
            if not hasattr(fpga, "run_all_odmr"):
                return {"error": "real tensor needs a board with run_all_odmr "
                                 "(set QSSC_FPGA_PORT or use source=sim)"}
            # Sweep span: cover the Zeeman doublet of the ambient + bias field. A
            # split > linewidth (5 MHz) needs |B.n_i| > ~90 µT; to resolve the 4 NV
            # <111> axes as DISTINCT doublets (the vector reconstruction needs >= 3
            # separated splits) a ~1 mT bias is typical, so ±50 MHz around D covers
            # |B.n_i| up to ~1.8 mT (gamma=28 GHz/T) and keeps the lines clear of the
            # sweep edges (the bright-baseline estimator samples the outer 10%).
            d_khz = int(prof.zero_field_splitting / 1e3)
            span_khz = max(int(prof.odmr_linewidth_hz / 1e3) * 6, 50000)
            f_start = d_khz - span_khz
            f_stop = d_khz + span_khz
            n = 256  # sweep points (one serial RUN_ALL_ODMR round-trip per point)
            # N-shot lock-in integration: average n_eff independent sweeps so the
            # per-point readout noise drops by sqrt(n_eff) (dimmer doublets clear
            # the linewidth floor and more pixels reconstruct a B-vector).
            n_shots = max(1, int(n_avg))
            acc = None
            for _ in range(n_shots):
                m = np.asarray(fpga.run_all_odmr(f_start, f_stop, n), dtype=float)
                acc = m if acc is None else acc + m
            matrix = (acc / n_shots).astype(np.int32)
            freqs_ghz = np.linspace(f_start * 1e3, f_stop * 1e3, n) / 1e9
            calib = calibrate_array_from_codes(
                matrix, freqs_ghz, prof, gradient_mhz_per_um=0.0,
                pixel_size_um=200.0, seed=int(seed if seed is not None else 42),
                temperature_k=300.0, integration_shots=n_shots,
                cap_to_resolvable=False)
            # (8,8,3) B-vector map in Tesla -> µT (the tensor gradiometer input).
            fmap_ut = b_vector_map_from_array_calibration(calib, prof) * 1e6
            pixels = tensor_gradient_map(fmap_ut, pitch_mm)
            fit = localize_dipole(fmap_ut, pitch_mm=pitch_mm,
                                  z_guess_mm=z_guess_mm, background_ut=None)
            res_profile = prof.name
            res_grid = grid
            res_n_pixels = grid * grid
            res_pitch_mm = pitch_mm
            io_note = ("Real: RUN_ALL_ODMR -> decompose per pixel -> 4-axis "
                       "B-vector -> tensor gradient + dipole fit on the measured "
                       "map (no synthetic field). Differential via background fit.")
        finally:
            # Every other hardware route closes its FPGA handle in a finally
            # (api_gyro / api_signal / fpga command). connect_fpga returns a NEW
            # FPGAConnection per call (not the runner cache), so without this the
            # /dev/ttyUSB0 handle leaks on every request AND on any mid-sweep
            # exception -- the OS then refuses a second open of the same UART and
            # the next hardware call fails with a spurious NoFPGADetected.
            fpga.close()
    else:
        fmap = synthetic_field_map(
            gradient_ut_mm=gradient_ut_mm, base_bx_ut=base_bx_ut,
            base_by_ut=base_by_ut, base_bz_ut=base_bz_ut,
            dipole_moment_am2=dipole_moment_am2,
            dipole_x_mm=dipole_x_mm, dipole_y_mm=dipole_y_mm,
            dipole_z_mm=dipole_z_mm)
        baseline = (synthetic_field_map(
            gradient_ut_mm=gradient_ut_mm, base_bx_ut=base_bx_ut,
            base_by_ut=base_by_ut, base_bz_ut=base_bz_ut,
            dipole_moment_am2=0.0) if differential else None)
        res = tensor_gradient_sweep(
            profile=profile, b_field_map_ut=fmap, baseline_map_ut=baseline,
            pitch_mm=pitch_mm, n_avg=n_eff, seed=(seed if seed is not None else 42),
            fit_dipole=True, differential=differential)
        # rebuild the (grid,grid,3) recovered B-vector map from the tensor pixels.
        fmap_ut = np.zeros((grid, grid, 3))
        for p in res.pixels:
            fmap_ut[p.y, p.x] = np.array(p.b_vector_ut, dtype=float)
        pixels = res.pixels
        fit = None
        if res.dipole_moment_am2 is not None:
            fit = {"dipole_x_mm": res.dipole_x_mm,
                   "dipole_y_mm": res.dipole_y_mm,
                   "dipole_z_mm": res.dipole_z_mm,
                   "dipole_moment_am2": res.dipole_moment_am2,
                   "fit_residual_ut": res.fit_residual_ut,
                   "fit_residual_mhz": res.fit_residual_mhz}
        res_profile = res.profile
        res_grid = res.grid
        res_n_pixels = res.n_pixels
        res_pitch_mm = res.pitch_mm
        io_note = (f"Sim: 64-pixel tensor gradiometer, each pixel's 4 NV <111> "
                   f"doublets -> B-vector (linewidth {lw:g} MHz, {n_eff} avg -> "
                   f"{sens_ut:.3f} µT). "
                   + ("Differential: baseline subtracted, dipole fit on raw "
                      "splits (sign-ambiguity-free). " if differential else ""))

    bmag = np.linalg.norm(fmap_ut, axis=2)
    n_resolved = int(np.count_nonzero(bmag > sens_ut))
    dipole = fit
    mode = "sim" if source == "sim" else "real"
    return {
        "profile": res_profile, "source": source, "grid": res_grid,
        "n_pixels": res_n_pixels, "pitch_mm": res_pitch_mm,
        "mode": mode, "n_avg": n_eff, "differential": differential,
        "linewidth_mhz": float(lw), "sensitivity_ut": round(sens_ut, 4),
        "n_resolved_pixels": n_resolved,
        "note": io_note + f" {n_resolved}/{grid*grid} pixels above the noise floor.",
        "b_vector_map_ut": [[round(float(v), 6) for v in fmap_ut[iy, ix]]
                            for iy in range(grid) for ix in range(grid)],
        "b_mag_image_ut": [round(float(v), 6) for v in bmag.ravel()],
        "b_mag_min_ut": round(float(bmag.min()), 6),
        "b_mag_max_ut": round(float(bmag.max()), 6),
        "tensor_pixels": [
            {"pixel": p.pixel, "x": p.x, "y": p.y,
             "b_vector_ut": p.b_vector_ut,
             "jacobian_ut_mm": p.jacobian_ut_mm}
            for p in pixels],
        "dipole": dipole,
        "mean_b_vector_ut": [round(float(v), 3) for v in
                             np.mean(fmap_ut.reshape(-1, 3), axis=0)],
        "std_b_mag_ut": round(float(np.std(bmag)), 3),
    }


def _field_cells(grid, value_fn):
    cells = []
    for iy in range(grid):
        for ix in range(grid):
            cells.append({"x": ix, "y": iy, "value": round(float(value_fn(ix, iy)), 4)})
    return cells


@router.get("/api/ferro")
def api_ferro(
    profile: str = "NV",
    moment_am2: float = 0.02,
    distance_mm: float = 15.0,
    offset_x_mm: float = 4.0,
    offset_y_mm: float = 3.0,
    n_avg: int = 500,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """Ferromagnetic-object detection: a magnetic dipole (the object) at a
    distance is imaged by the 8x8 array through non-magnetic media. The peak
    field + bearing reconstruct the object's location; detection fires when the
    peak clears the mode-dependent noise floor. Vertical-dipole model.
    """
    rng = np.random.default_rng(seed)
    noise_ut, n_eff, _lw = _sensor_field_noise_ut(profile, mode, n_avg)
    M = float(moment_am2)
    d = float(distance_mm) * 1e-3
    ox = float(offset_x_mm) * 1e-3
    oy = float(offset_y_mm) * 1e-3
    grid = 8

    def b(ix, iy):
        px, py = _sensor_pixel_xy_mm(ix, iy)
        rx, ry = px * 1e-3 - ox, py * 1e-3 - oy
        r2 = rx * rx + ry * ry + d * d
        bz = 1e-7 * M * (2 * d * d - rx * rx - ry * ry) / (r2 ** 2.5)  # T
        return bz * 1e6 + rng.normal(0.0, noise_ut)  # uT

    cells = _field_cells(grid, b)
    vals = [c["value"] for c in cells]
    peak = max(vals, key=abs)
    bearing = (float(np.degrees(np.arctan2(oy, ox))) + 360.0) % 360.0
    detected = abs(peak) > noise_ut
    return {
        "profile": get_profile(profile).short, "mode": mode, "grid": grid,
        "unit": "µT", "vmin": round(min(vals), 3), "vmax": round(max(vals), 3),
        "cells": cells, "noise_ut": round(noise_ut, 4), "n_avg": n_eff,
        "detected": bool(detected), "peak_ut": round(float(peak), 3),
        "bearing_deg": round(bearing, 1), "distance_mm": float(distance_mm),
        "moment_am2": M,
        "stats": [
            {"k": "Peak |B|", "v": f"{abs(peak):.2f}", "unit": "µT"},
            {"k": "Noise floor", "v": f"{noise_ut:.3f}", "unit": "µT"},
            {"k": "Detected", "v": "yes" if detected else "no"},
            {"k": "Bearing", "v": f"{bearing:.1f}", "unit": "°"},
            {"k": "Distance", "v": f"{distance_mm:.1f}", "unit": "mm"},
            {"k": "Moment", "v": f"{M:.3f}", "unit": "A·m²"},
            {"k": "Averages", "v": str(n_eff)},
        ],
        "note": (f"Vertical-dipole model of a ferromagnetic object {distance_mm:g} mm "
                 f"from the array; the field penetrates packaging/walls/tissue. "
                 f"Detection fires when the peak clears the {noise_ut:.3f} µT "
                 f"{mode} noise floor."),
    }


@router.get("/api/nav")
def api_nav(
    profile: str = "NV",
    pos_x: float = 12.0,
    pos_y: float = 10.0,
    map_size: int = 24,
    anomaly_ut: float = 2.0,
    n_avg: int = 500,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """Geomagnetic anomaly navigation: match the local crustal-field anomaly
    against a stored anomaly map to estimate position -- works where GPS and
    optical both fail (underwater / underground / indoors). The as-built micro-
    diamond (µT floor) cannot resolve sub-µT crustal anomalies; bulk-CVD (nT)
    shrinks the match error.
    """
    rng = np.random.default_rng(seed)
    noise_ut, n_eff, _lw = _sensor_field_noise_ut(profile, mode, n_avg)
    grid = int(map_size)
    # deterministic anomaly map: a handful of 2-D Gaussians (seeded separately
    # so the map is stable across re-measures at different positions)
    prng = np.random.default_rng((seed or 0) + 1)
    peaks = [(prng.uniform(0, grid), prng.uniform(0, grid),
              prng.uniform(0.5, 1.5), prng.uniform(1.5, 3.5)) for _ in range(6)]

    def anomaly(x, y):
        v = 0.0
        for cx, cy, amp, sigma in peaks:
            v += amp * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sigma ** 2))
        return float(v * anomaly_ut)

    amap = np.array([[anomaly(x, y) for x in range(grid)] for y in range(grid)])
    px, py = float(pos_x), float(pos_y)
    x0 = min(max(int(px), 0), grid - 2)
    y0 = min(max(int(py), 0), grid - 2)
    fx, fy = px - x0, py - y0
    true_val = (amap[y0, x0] * (1 - fx) * (1 - fy) + amap[y0, x0 + 1] * fx * (1 - fy) +
                amap[y0 + 1, x0] * (1 - fx) * fy + amap[y0 + 1, x0 + 1] * fx * fy)
    meas = float(true_val) + rng.normal(0.0, noise_ut)
    matched = np.unravel_index(int(np.argmin(np.abs(amap - meas))), amap.shape)
    my, mx = int(matched[0]), int(matched[1])
    err_km = float(np.hypot(px - mx, py - my))  # 1 cell = 1 km for the demo
    cells = [{"x": x, "y": y, "value": round(float(amap[y, x]), 4)}
             for y in range(grid) for x in range(grid)]
    return {
        "profile": get_profile(profile).short, "mode": mode, "grid": grid,
        "unit": "µT", "vmin": round(float(amap.min()), 3),
        "vmax": round(float(amap.max()), 3), "cells": cells,
        "measured_ut": round(meas, 4), "noise_ut": round(noise_ut, 4),
        "n_avg": n_eff, "true_pos": [px, py], "matched_pos": [float(mx), float(my)],
        "error_km": round(err_km, 2),
        "markers": [{"x": px, "y": py, "kind": "true"},
                    {"x": float(mx), "y": float(my), "kind": "matched"}],
        "stats": [
            {"k": "Measured", "v": f"{meas:.3f}", "unit": "µT"},
            {"k": "Noise floor", "v": f"{noise_ut:.3f}", "unit": "µT"},
            {"k": "Match error", "v": f"{err_km:.2f}", "unit": "km"},
            {"k": "True pos", "v": f"({px:.1f},{py:.1f})"},
            {"k": "Matched", "v": f"({mx},{my})"},
            {"k": "Averages", "v": str(n_eff)},
        ],
        "note": (f"Crustal-anomaly map matching ({anomaly_ut:g} µT relief); the {mode} "
                 f"noise floor is {noise_ut:.3f} µT. As-built µT floor cannot resolve "
                 f"sub-µT crustal anomalies; bulk-CVD (nT) shrinks the match error."),
    }


@router.get("/api/thermal")
def api_thermal(
    profile: str = "NV",
    base_temp_k: float = 300.0,
    hotspot_k: float = 330.0,
    hotspot_x: float = 5.0,
    hotspot_y: float = 4.0,
    n_avg: int = 500,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """NV thermometry: the D zero-field splitting shifts with temperature, so
    the 8x8 array is a contactless temperature image (a chip hotspot, a
    surface's thermal map read through the diamond). Uses the modeled NV D
    temperature coefficient (a material constant, not a supplier-quoted spec).
    """
    rng = np.random.default_rng(seed)
    prof = get_profile(profile)
    lw = prof.odmr_linewidth_hz / 1e6
    n_eff = _sensor_mode_n_avg(n_avg, mode)
    freq_noise_hz = lw * 1e6 / np.sqrt(max(1, n_eff))
    temp_noise = abs(freq_noise_hz / _NV_D_TEMP_COEFF_HZ_PER_K)
    hx, hy = float(hotspot_x), float(hotspot_y)
    sigma = 1.6
    grid = 8

    def t(ix, iy):
        t_true = base_temp_k + (hotspot_k - base_temp_k) * np.exp(
            -((ix - hx) ** 2 + (iy - hy) ** 2) / (2 * sigma ** 2))
        df = _NV_D_TEMP_COEFF_HZ_PER_K * (t_true - base_temp_k)
        t_meas = base_temp_k + (df + rng.normal(0.0, freq_noise_hz)) / _NV_D_TEMP_COEFF_HZ_PER_K
        return t_meas - 273.15  # Celsius

    cells = _field_cells(grid, t)
    vals = [c["value"] for c in cells]
    peak = max(vals)
    detected = (hotspot_k - base_temp_k) > temp_noise
    return {
        "profile": prof.short, "mode": mode, "grid": grid,
        "unit": "°C", "vmin": round(min(vals), 2), "vmax": round(max(vals), 2),
        "cells": cells, "temp_noise_k": round(temp_noise, 3), "n_avg": n_eff,
        "detected": bool(detected),
        "stats": [
            {"k": "Base", "v": f"{base_temp_k - 273.15:.1f}", "unit": "°C"},
            {"k": "Hotspot", "v": f"{hotspot_k - 273.15:.1f}", "unit": "°C"},
            {"k": "Peak", "v": f"{peak:.2f}", "unit": "°C"},
            {"k": "Temp noise", "v": f"{temp_noise:.2f}", "unit": "K"},
            {"k": "Detected", "v": "yes" if detected else "no"},
            {"k": "Averages", "v": str(n_eff)},
        ],
        "note": (f"NV D-shift thermometry (modeled coefficient "
                 f"{int(_NV_D_TEMP_COEFF_HZ_PER_K / 1e3)} kHz/K, not a supplier-quoted "
                 f"spec); {n_eff} averages -> {temp_noise:.2f} K resolution. "
                 f"Contactless temperature image through the diamond."),
    }


@router.get("/api/current")
def api_current(
    profile: str = "NV",
    current_a: float = 0.5,
    wire_offset_mm: float = 0.0,
    depth_mm: float = 3.0,
    n_avg: int = 500,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """Current imaging (Ampere): a current in a wire/trace produces a B field
    the array images contactlessly -- short-circuit localization, battery-cell
    current signatures, chip power-rail mapping, all through the enclosure.
    Straight-wire model: |B| = µ0 I / (2π r).
    """
    rng = np.random.default_rng(seed)
    noise_ut, n_eff, _lw = _sensor_field_noise_ut(profile, mode, n_avg)
    I = float(current_a)
    ox = float(wire_offset_mm) * 1e-3
    depth = float(depth_mm) * 1e-3
    grid = 8

    def b(ix, iy):
        px, _py = _sensor_pixel_xy_mm(ix, iy)
        r = max(float(np.hypot(px * 1e-3 - ox, depth)), 1e-6)
        return (2e-7 * I / r) * 1e6 + rng.normal(0.0, noise_ut)  # uT

    cells = _field_cells(grid, b)
    vals = [c["value"] for c in cells]
    peak = max(vals, key=abs)
    detected = abs(peak) > noise_ut
    return {
        "profile": get_profile(profile).short, "mode": mode, "grid": grid,
        "unit": "µT", "vmin": round(min(vals), 3), "vmax": round(max(vals), 3),
        "cells": cells, "noise_ut": round(noise_ut, 4), "n_avg": n_eff,
        "detected": bool(detected), "peak_ut": round(float(peak), 3),
        "current_a": I,
        "stats": [
            {"k": "Current", "v": f"{I:.2f}", "unit": "A"},
            {"k": "Peak |B|", "v": f"{abs(peak):.2f}", "unit": "µT"},
            {"k": "Noise floor", "v": f"{noise_ut:.3f}", "unit": "µT"},
            {"k": "Depth", "v": f"{depth_mm:.1f}", "unit": "mm"},
            {"k": "Detected", "v": "yes" if detected else "no"},
            {"k": "Averages", "v": str(n_eff)},
        ],
        "note": (f"Ampere field of a straight wire {depth_mm:g} mm below the array "
                 f"carrying {I:g} A; imaged contactlessly through encapsulation. "
                 f"Detection fires above the {noise_ut:.3f} µT {mode} floor."),
    }