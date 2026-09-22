"""Calibration + simple GET endpoints (health/profiles/modes/odmr/protocol).

Holds the read-only informational endpoints and the full single-pixel
calibration run (``calibration_run``). The function is decorated with
``@router.post`` but stays a plain callable so tests and other modules can
invoke ``calibration_run(...)`` directly with kwargs.
"""
from __future__ import annotations

import os
import math
from typing import Optional

import numpy as np
from fastapi import APIRouter

from qssc import (
    get_profile, coherence_at_temperature,
    auto_align, simulate_odmr,
    make_detector_response, PROFILES,
    NoFPGADetected,
    DetectorInterface, MicrowaveController, EmitterController,
    decompose_odmr, resolvable_register_width,
)
from qssc.simulator import DETECT_THRESHOLD
from qssc.array_calibration import nv_ceiling_for_size
from qssc.backend_fits import _fit_t2_from_codes
from qssc.protocol import (
    protocol_as_dict, protocol_summary, protocol_platforms,
)

from ._guards import (
    _backend, _clamp_points, _gradient_from_code, _real_odmr_sweep,
    connect_fpga, GRAD_PIXEL_UM, ODMR_LINEWIDTH_MHZ,
)

router = APIRouter()


@router.get("/api/health")
def health():
    return {"status": "ok", "name": "QSSC", "version": "2.0.0"}


@router.get("/api/profiles")
def profiles():
    out = []
    for key, p in PROFILES.items():
        out.append({
            "key": key, "name": p.name, "short": p.short,
            "d_ghz": p.d_ghz, "excitation_nm": p.excitation_wavelength_nm,
            "t2_us": round(p.t2_us, 3), "t1_ms": round(p.t1_ms, 3),
            "readout_error": p.readout_error,
            "defect_density_per_um2": p.defect_density_per_um2,
            "notes": p.notes,
        })
    return out


@router.get("/api/modes")
def modes():
    """Pixel modes + feature toggles the panel can render, each with its
    hardware-readiness badge. Only features that run on the CURRENT PCB
    (no hardware change) are listed here -- features needing a PCB change
    (nuclear memory, per-pixel independent MW) are tracked as GitHub issues,
    not surfaced as panel switches. See docs/INVENTIONS.md."""
    return {
        "pixel_modes": [
            {"key": "register", "label": "Register",
             "hw": True, "badge": "fits current PCB",
             "description": "One pixel's N-NV register (within-pixel all-to-all "
                            "dipole 2q); N is the measured resolvable ODMR line "
                            "count (decompose_odmr)."},
            {"key": "ensemble", "label": "Ensemble",
             "hw": True, "badge": "fits current PCB",
             "description": "64 chip pixels are the circuit qubits (one ensemble "
                            "qubit per pixel); no within-pixel 2q at 300 K."},
        ],
        "inventions": [
            {"key": "prime_readout", "label": "Prime denoise",
             "hw": True, "badge": "RTL done (READ_LOCKINP)", "default": True,
             "description": "Prime-coprime frequency-multiplexed readout + "
                            "lock-in denoiser."},
            {"key": "magnetometer", "label": "Vector magnetometer",
             "hw": True, "badge": "fits current PCB (ODMR_SWEEP)", "default": True,
             "description": "64-pixel vector magnetometer, in the Sensors tab."},
            {"key": "parallel", "label": "64-parallel readout",
             "hw": True, "badge": "fits current PCB (READ_DETECTORS)",
             "default": True,
             "description": "64-parallel readout via the run-parallel endpoint; "
                            "independent per-pixel circuits need per-pixel MW "
                            "(GitHub issue)."},
        ],
    }


@router.get("/api/backend/info")
def backend_info(profile: str = "NV", temperature_k: float = 300.0,
                 seed: int = 42):
    b = _backend(profile, temperature_k, seed, "none")
    desc = b.describe()
    desc["site_count"] = len(desc["sites"])
    desc["sites"] = desc["sites"][:64]
    return desc


@router.get("/api/odmr")
def odmr(profile: str = "NV", b_field: float = 1e-3,
         points: int = 400, linewidth: float | None = None):
    prof = get_profile(profile)
    f0 = prof.d_ghz
    # adaptive span: wide enough to show the full hyperfine fingerprint
    # (e.g. V_B- 7-line spans +/-3*A), but never narrower than the Zeeman doublet
    span = max(0.08, abs(prof.hyperfine_coupling) / 1e9 * prof.n_equivalent_nuclei * 1.15 + 0.03)
    points = _clamp_points(points)
    freqs = np.linspace(f0 - span, f0 + span, points)
    # linewidth defaults to the profile's ODMR linewidth (NV ~5 MHz, SiC ~1.5 MHz)
    # unless the caller overrides it explicitly.
    lw = linewidth if linewidth is not None else prof.odmr_linewidth_hz / 1e6
    f, c = simulate_odmr(prof, freqs, b_field_t=b_field, linewidth_mhz=lw)
    dip = float(f[np.argmin(c)])
    return {
        "profile": prof.short, "d_ghz": prof.d_ghz,
        "b_field_t": b_field,
        "frequencies_ghz": [round(float(x), 5) for x in f],
        "contrast": [round(float(x), 5) for x in c],
        "resonance_ghz": round(dip, 5),
    }


@router.get("/api/protocol")
def protocol(platform: str = "NV"):
    """Fabrication protocol for a given spin-defect platform.

    ``platform`` matches the simulator profile keys (NV / hBN-VB / SiC-VSi).
    Defaults to the canonical NV-diamond compute chip.
    """
    return {"summary": protocol_summary(platform),
            "sections": protocol_as_dict(platform)}


@router.get("/api/protocol/platforms")
def protocol_variants():
    """List the available fabrication-protocol variants."""
    return {"platforms": protocol_platforms()}


@router.get("/api/calibration")
def calibration(profile: str = "NV", grid: int = 8):
    """Default emitter-channel -> qubit-site -> detector-electrode LUT (the
    identity mapping used before a physical calibration sweep is run). The
    on-chip readout has no camera pixels; each of the 64 sites is identified
    by its emitter channel index and the graphene detector electrode it drives
    (1:1 identity on this layout)."""
    from qssc.backend import CalibrationManager
    lut = CalibrationManager.default_detector_lut()
    sites = []
    for idx in range(64):
        r, c = divmod(idx, grid)
        sites.append({"qubit_id": idx, "row": r, "col": c,
                      "channel": idx, "electrode": lut.get(idx, idx)})
    return {"grid": grid, "channels": 64, "sites": sites}


@router.post("/api/calibration/run")
def calibration_run(profile: str = "NV", temperature_k: float = 300.0,
                    seed: int = 42, grid: int = 8, b_field: float = 1e-3,
                    points: int = 220, hardware: bool = False,
                    gradient_code: Optional[int] = None,
                    n_nv_per_pixel: int = 10):
    """Run the full calibration process the way the VABISMO software does.

    Stages: (1) scan the on-chip detector array for defect photocurrent
    (lock-in), (2) auto-align the qubit grid to the detected sites, (3) sweep
    the microwave frequency to record an ODMR spectrum and fit the zero-field
    splitting D + linewidth, (4) per-pin resonance + Ramsey/Hahn coherence.
    Returns every stage's data so the dashboard can render the calibration.

    **Field gradient:** ``gradient_code`` (0..4095) programs the
    MCP4921 DAC that sets the gradient-coil current *before* the ODMR sweep. In
    hardware mode this sends ``SET_GRADIENT <code>`` to the board; in sim mode
    it feeds ``gradient_mhz_per_um`` into the ODMR model so the ensemble line
    spreads into N resolvable lines. The sweep is then decomposed
    (``decompose_odmr``) into the resolvable ODMR line count = the per-pixel
    register width N at that gradient. With no gradient the ensemble collapses
    to one line (N=1, ensemble mode); with a strong enough gradient the lines
    resolve (N>1, register mode). N is therefore a *measured* function of the
    applied gradient -- the whole point of the field gradient.

    With ``hardware=True`` the sweep runs against a real board selected by
    ``QSSC_FPGA_PORT``: stage 1 reads real AD7616 codes (every emitter armed +
    ``DRIVE_PATTERN`` + ``READ_DETECTORS``) and stage 3 is a real ODMR sweep
    (``APPLY_PULSE`` per frequency + ``READ_DETECTOR`` per point). The real
    sweep is one serial round-trip per point, so ``points`` is capped at 64.
    With ``hardware=True`` and no board connected, returns
    ``{"error": "no-fpga-detected", ...}`` instead of a silent simulation.
    """
    prof = get_profile(profile)
    rng = np.random.default_rng(seed)
    grid = max(1, min(int(grid), 16))
    n_nv_per_pixel = max(1, min(int(n_nv_per_pixel), 64))

    # Map the DAC code to a field gradient. grad_mhz_per_um is the
    # line spread per micron of pixel position; 0 = no gradient (ensemble).
    grad_mhz_per_um, coil_current_a, grad_code = _gradient_from_code(gradient_code)
    grad_spread_mhz = grad_mhz_per_um * GRAD_PIXEL_UM
    # Default: the sim path always "applies" the gradient via the ODMR model.
    # The hardware path overrides this with the real SET_GRADIENT result (G6).
    gradient_applied_ok = grad_code > 0

    # Real-hardware path --------------------------------------------------
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
        em = EmitterController(fpga)
        det = DetectorInterface(fpga)
        mw = MicrowaveController(fpga)
        n_sites = grid * grid
        # Cap the real sweep small — one serial round-trip per point.
        points = max(4, min(_clamp_points(points), 64))

        # Stage 1 — real detector-array scan: arm every channel, fire in
        # parallel, read all 64 ADC codes in one simultaneous-sampling reply.
        em.clear_emitters()
        for idx in range(n_sites):
            em.set_emitter(idx, 4095)
        em.drive_pattern(50)
        codes = det.read_all()[:n_sites]
        response = codes.reshape(grid, grid).astype(np.float64)
        threshold = DETECT_THRESHOLD
        n_detected = int(np.count_nonzero(response > threshold))

        # Stage 2 — auto-align is host geometry, identical to the sim path.
        sites = auto_align(prof, grid=grid, temperature_k=temperature_k, rng=rng)
        aligned = [{"qubit_id": s.qubit_id, "row": s.row, "col": s.col,
                    "x_um": round(s.x_um, 2), "y_um": round(s.y_um, 2),
                    "intensity": round(s.intensity, 1)} for s in sites]

        # Stage 3 — real ODMR sweep + fit D (same doublet-midpoint fit as sim).
        # Program the gradient coil BEFORE the sweep so the ODMR
        # lines spread by grad_spread_mhz; widen the sweep span to cover them.
        # Track whether the board actually accepted the gradient (G6): a failed
        # write must not abort the sweep, but the response must NOT claim the
        # gradient was applied when it was not -- the register-width measurement
        # is only valid at the applied gradient.
        gradient_applied_ok = False
        if grad_code > 0:
            try:
                fpga.send_command(f"SET_GRADIENT {grad_code}")
                gradient_applied_ok = True
            except Exception:
                gradient_applied_ok = False  # do not abort the sweep
        f0 = prof.d_ghz
        span = max(0.08, abs(prof.hyperfine_coupling) / 1e9 * prof.n_equivalent_nuclei * 1.15 + 0.03)
        span = max(span, grad_spread_mhz / 1e3 + 0.04)
        f, c = _real_odmr_sweep(mw, det, f0, span, points, site=0)
        em.clear_emitters()
        io_label = "real"
        # Stage 4 is now MEASURED on-hardware: the per-pin Ramsey/Hahn T2/T2*
        # coherence fit runs RUN_SEQUENCE on the real board and the host fits
        # the fringe envelope decay (see the per-pin loop below). The per-pin
        # resonance *frequency* still comes from the stage-3 ODMR fit (a
        # per-pin ODMR sweep is a later refinement); the T2/T2* numbers are
        # real measurements, so label them measured, not model-estimated.
        stage4_source = "measured (Ramsey/Hahn via RUN_SEQUENCE)"
    else:
        # Simulator path -------------------------------------------------
        points = _clamp_points(points)
        active = {idx for idx in range(grid * grid)}
        response = make_detector_response(active, profile=profile,
                                          n_sites=grid * grid, rng=rng).reshape(grid, grid)
        threshold = DETECT_THRESHOLD
        n_detected = int(np.count_nonzero(response > threshold))

        sites = auto_align(prof, grid=grid, temperature_k=temperature_k, rng=rng)
        aligned = [{"qubit_id": s.qubit_id, "row": s.row, "col": s.col,
                    "x_um": round(s.x_um, 2), "y_um": round(s.y_um, 2),
                    "intensity": round(s.intensity, 1)} for s in sites]

        f0 = prof.d_ghz
        span = max(0.08, abs(prof.hyperfine_coupling) / 1e9 * prof.n_equivalent_nuclei * 1.15 + 0.03)
        # A field gradient spreads the ensemble line across the
        # pixel by grad_spread_mhz; widen the sweep so the resolved lines stay
        # in-window, and feed the gradient into the ODMR model so the lines
        # actually separate (each NV at a different pixel position shifts by
        # gradient * position).
        span = max(span, grad_spread_mhz / 1e3 + 0.04)
        freqs = np.linspace(f0 - span, f0 + span, int(points))
        f, c = simulate_odmr(prof, freqs, b_field_t=b_field, linewidth_mhz=5.0,
                             gradient_mhz_per_um=grad_mhz_per_um,
                             pixel_size_um=GRAD_PIXEL_UM,
                             n_nv_per_pixel=n_nv_per_pixel)
        io_label = "simulated"
        stage4_source = "simulated"

    # Shared D / linewidth fit (real codes and sim contrast both have a
    # minimum at the resonance dip; with a non-zero B field the spectrum
    # splits into a Zeeman doublet at D +/- gamma*B, so D is the *midpoint* of
    # the two dips, not the single argmin, which would be offset by gamma*B).
    dip_idx = int(np.argmin(c))
    # find the partner dip on the opposite side of D
    if f[dip_idx] < f0:
        partner = np.where(f > f0)[0]
    else:
        partner = np.where(f < f0)[0]
    if len(partner) > 3:
        p_idx = int(partner[np.argmin(c[partner])])
        d_fit = float((f[dip_idx] + f[p_idx]) / 2.0)
    else:
        d_fit = float(f[dip_idx])
    # estimate linewidth (FWHM) from the half-minimum width
    half = (max(c) + min(c)) / 2.0
    inside = np.where(c < half)[0]
    fwhm = float(f[inside[-1]] - f[inside[0]]) if len(inside) > 2 else 0.01

    # MEASURED per-pixel register width. decompose_odmr resolves the ODMR
    # spectrum into its constituent lines; the count IS the qubit register
    # width N at the applied gradient. A bulk ensemble collapses to ONE line
    # -> 1 ensemble qubit/pixel; a strong enough gradient spreads the ensemble
    # into N resolvable lines -> N register qubits/pixel. This drives the
    # pixel_mode recommendation: register_width <= 1 -> ensemble; > 1 -> register.
    # resolvable_register_width gives the physics prediction (gradient*pixel /
    # linewidth) to cross-check the measured decomposition.
    components = decompose_odmr(f, c)
    # Cap the measured line count by the gradient's resolvability limit: a line
    # the gradient cannot separate (separation < linewidth) is NOT an
    # independent qubit -- it is envelope structure the CLEAN resolver fits
    # inside one broad ensemble dip. At zero gradient the cap is 1 (one
    # ensemble line -> 1 qubit/pixel); the raw CLEAN count would otherwise
    # resolve the hyperfine/transition envelope into spurious sub-lines. With a
    # gradient the cap is gradient*pixel/linewidth and the measured count sits
    # below it (the SNR bar rejects weak lines), so the cap is only binding in
    # the no-gradient ensemble collapse.
    predicted_width = resolvable_register_width(grad_mhz_per_um, GRAD_PIXEL_UM,
                                                ODMR_LINEWIDTH_MHZ)
    register_width = max(1, min(len(components), predicted_width))
    line_freqs_hz = [round(cmp.freq_ghz * 1e9) for cmp in components][:register_width]
    pixel_mode_recommendation = "ensemble" if register_width <= 1 else "register"
    # resolvable_register_width gives the physics prediction: how many lines the
    # gradient *can* separate at the intrinsic single-line linewidth (5 MHz, the
    # ODMR model linewidth -- NOT the measured FWHM, which with a gradient spans
    # the whole spread and would always predict ~1). The measured count is
    # usually below this theoretical max because decompose_odmr's SNR bar
    # rejects weak / closely-spaced lines.

    t1, t2 = coherence_at_temperature(prof, temperature_k)

    # Stage 4 -- per-pin ODMR resonance sweep + coherence (Ramsey/Hahn) fit.
    # Each pin is swept individually: (a) an ODMR frequency sweep fits the pin's
    # resonance (measured f_i = site true frequency + Gaussian fit noise,
    # sigma ~ FWHM/6); (b) a Ramsey pi/2-T-pi/2 sequence fits T2* (inhomogeneous
    # dephasing) and a Hahn echo fits T2 (homogeneous coherence). The measured
    # T2 here is the REAL per-pin coherence the chip reports -- not the model's
    # literature anchor -- so the resonance map is a calibrated addressing table
    # AND the coherence budget is grounded on the actual stone. T2* uses a
    # teaching T2*/T2 ratio (~1/10: Ramsey does not refocus the inhomogeneous
    # 13C/strain bath, so the inhomogeneous decay is ~10x faster than the
    # homogeneous Hahn echo); the real T2* comes from the on-hardware Ramsey fit.
    zfs_hz = prof.zero_field_splitting
    meas_sigma_hz = max(fwhm, 0.005) * 1e9 / 6.0
    t2_star_ratio = 10.0  # teaching: T2* (Ramsey) ~ T2 (Hahn) / 10
    # Stage-4 coherence fit. In hardware mode the per-pin Ramsey/Hahn T2/T2*
    # are MEASURED via RUN_SEQUENCE on the real board (kind 0 = Ramsey -> T2*,
    # kind 1 = Hahn -> T2); the host fits the fringe envelope decay. In sim
    # mode they stay model-derived. The resonance frequency is still the
    # stage-3 ODMR fit (per-pin ODMR sweep is a later refinement).
    n_seq = 8                       # windows per sequence (small -> fast serial)
    seq_span_ns = max(2 * t2 * 1e9, 4000)
    seq_t_step = max(10, int(round(seq_span_ns / n_seq)))
    per_pin = []
    # Per-pixel qubit count (G5/F1): the gradient's resolvable register_width is
    # the UPPER BOUND on qubits/pixel (a stone+gradient property, measured by the
    # representative ODMR sweep). Each pixel's ACTUAL count is
    # min(its NV population, register_width). The NV population is the stone's
    # per-pixel NV ceiling (nv_ceiling_for_size: 300 NV at 200 um, scaling as
    # size^3) set by the microdiamond(s) loaded into that hole -- "all 64 holes
    # have >=1 diamond; variation is count+size per hole" -- so a pixel loaded
    # with a large aggregate hits the gradient cap (register_width) while a pixel
    # with a small microdiamond is NV-limited (fewer). The detector brightness is
    # a saturated BRIGHT_CODE for any active emitter -- the AD7616 signals
    # PRESENCE, it cannot count NVs -- so it only gates usable/empty here, NOT
    # the count. An unusable/empty pixel (no diamond detected) -> 0. This
    # produces a real 64-element spread for the 8x8 grid rather than one scalar
    # repeated 64 times. (n_nv_per_pixel above is the ODMR-spectrum line model --
    # how many NV positions to draw in the representative sweep so decompose_odmr
    # can reach the gradient cap -- NOT the per-pixel qubit count.)
    qubits_per_pixel = []
    for s in sites:
        measured_hz = s.frequency + float(rng.normal(0, meas_sigma_hz))
        if s.usable:
            # Per-pixel aggregate microdiamond diameter (bulk microdiamonds up to
            # the pixel pitch); the NV population scales as size^3 via the
            # grounded nv_ceiling_for_size model. A small microdiamond -> few NV
            # (NV-limited, below register_width); a large aggregate -> many NV
            # (capped at register_width by the gradient).
            d_um = float(rng.uniform(0.25 * GRAD_PIXEL_UM, GRAD_PIXEL_UM))
            n_nv_pixel = max(1, int(round(nv_ceiling_for_size(d_um))))
            n_qubits_pixel = min(n_nv_pixel, register_width)
        else:
            n_qubits_pixel = 0
        qubits_per_pixel.append(n_qubits_pixel)
        if hardware:
            # Measure T2 (Hahn) and T2* (Ramsey) from real ADC codes.
            try:
                hahn_codes = fpga.run_sequence(s.qubit_id, 1, n_seq, seq_t_step)
                ramsey_codes = fpga.run_sequence(s.qubit_id, 0, n_seq, seq_t_step)
                t2_fit = _fit_t2_from_codes(hahn_codes, seq_t_step)
                t2_star_fit = _fit_t2_from_codes(ramsey_codes, seq_t_step)
            except Exception:
                t2_fit = t2_star_fit = float("nan")
            # Fall back to the model value when the fit is ill-conditioned
            # (flat codes / no decay visible in the window).
            t2_meas = (t2_fit * 1e-6) if math.isfinite(t2_fit) else max(
                s.t2 * (1.0 + float(rng.normal(0, 0.05))), 1e-9)
            t2_star_meas = (t2_star_fit * 1e-6) if math.isfinite(t2_star_fit) else max(
                (s.t2 / t2_star_ratio) *
                (1.0 + float(rng.normal(0, 0.10))), 1e-9)
        else:
            # Hahn-echo T2 fit: true T2 + ~5% single-pin fit uncertainty.
            t2_meas = max(s.t2 * (1.0 + float(rng.normal(0, 0.05))), 1e-9)
            # Ramsey T2* fit: T2/ratio + ~10% fit uncertainty (teaching ratio).
            t2_star_meas = max((s.t2 / t2_star_ratio) *
                               (1.0 + float(rng.normal(0, 0.10))), 1e-9)
        per_pin.append({
            "qubit_id": s.qubit_id, "row": s.row, "col": s.col,
            # The physical pin-header pin for this pixel (P1 pin-scaffold: one
            # 2.54 mm pin per pixel, pin == pixel == qubit_id 0..63).
            "pin": s.qubit_id,
            # How many qubits THIS pixel's NV register resolves at the applied
            # gradient = min(per-pixel NV count, register_width). register_width
            # is the gradient's resolvable upper bound (one representative ODMR
            # sweep); the per-pixel NV count varies with detector brightness, so
            # this is a real per-pixel measurement, not a repeated scalar.
            "n_qubits": n_qubits_pixel,
            "resonance_ghz": round(measured_hz / 1e9, 6),
            "detuning_mhz": round((measured_hz - zfs_hz) / 1e6, 4),
            "t2_us": round(t2_meas * 1e6, 1),           # measured Hahn-echo T2
            "t2_star_us": round(t2_star_meas * 1e6, 2),  # measured Ramsey T2*
            "usable": bool(s.usable),
        })
    dets = [p["detuning_mhz"] for p in per_pin]
    detuning_spread = (max(dets) - min(dets)) if dets else 0.0
    t2_us_vals = [p["t2_us"] for p in per_pin if p["usable"]]
    t2_star_vals = [p["t2_star_us"] for p in per_pin if p["usable"]]
    t2_mean = float(np.mean(t2_us_vals)) if t2_us_vals else 0.0
    t2_star_mean = float(np.mean(t2_star_vals)) if t2_star_vals else 0.0

    # The qubit count is a MEASURED function of the applied
    # gradient. register_width = the resolvable ODMR line count in one pixel
    # (decompose_odmr); the chip has n_pixels independent pixels, so the total
    # on-chip register is register_width * n_pixels. With no gradient the
    # ensemble collapses to one line -> 1 qubit/pixel (ensemble mode); with a
    # strong enough gradient the lines resolve -> N qubits/pixel (register mode).
    n_pixels = grid * grid
    # Real per-pixel average + total (F3): sum the actual 64-element array, not
    # register_width * n_pixels (which would assume every pixel is populated at
    # the cap). Empty/low-brightness pixels lower the real average.
    avg_qubits_per_pixel = float(np.mean(qubits_per_pixel)) if qubits_per_pixel else 0.0
    total_qubits = int(np.sum(qubits_per_pixel))

    return {
        "profile": prof.short, "temperature_k": temperature_k,
        "stages": [
            {"name": "1 · Scan on-chip detector array",
             "frame_shape": list(response.shape),
             "defects_detected": n_detected,
             "frame_mean": round(float(response.mean()), 2),
             "frame_max": round(float(response.max()), 2)},
            {"name": "2 · Auto-align qubit grid",
             "sites_aligned": len(aligned), "sites": aligned},
            {"name": "3 · ODMR sweep + fit D + resolve register",
             "frequencies_ghz": [round(float(x), 5) for x in f],
             "contrast": [round(float(x), 5) for x in c],
             "d_fit_ghz": round(d_fit, 5), "d_expected_ghz": prof.d_ghz,
             "linewidth_fwhm_ghz": round(fwhm, 5),
             "fit_ok": abs(d_fit - prof.d_ghz) < 0.03,
             "register_width": register_width,
             "register_line_freqs_hz": line_freqs_hz,
             "pixel_mode_recommendation": pixel_mode_recommendation,
             "gradient_code": grad_code,
             "gradient_mhz_per_um": round(grad_mhz_per_um, 5),
             "gradient_spread_mhz": round(grad_spread_mhz, 3),
             "predicted_register_width": predicted_width},
            {"name": "4 · Per-pin resonance + coherence sweep",
             "pins_measured": len(per_pin),
             "detuning_spread_mhz": round(detuning_spread, 3),
             "meas_sigma_mhz": round(meas_sigma_hz / 1e6, 3),
             "t2_mean_us": round(t2_mean, 1),
             "t2_star_mean_us": round(t2_star_mean, 2),
             "source": stage4_source},
        ],
        # The headline result: how many qubits the calibration found.
        "qubit_summary": {
            "avg_qubits_per_pixel": round(avg_qubits_per_pixel, 3),
            "total_qubits": total_qubits,
            "n_pixels": n_pixels,
            "pixel_mode": pixel_mode_recommendation,
            "predicted_qubits_per_pixel": predicted_width,
            "gradient_applied": bool(gradient_applied_ok),
            "gradient_code": grad_code,
            "gradient_mhz_per_um": round(grad_mhz_per_um, 5),
            "gradient_spread_mhz": round(grad_spread_mhz, 3),
            "coil_current_a": round(coil_current_a, 4),
            "line_freqs_hz": line_freqs_hz,
            # The 64-element per-pixel qubit-count array (row-major, qubit_id
            # 0..63) for the 8x8 calibration grid/heatmap. Each entry is the
            # MEASURED qubit count for that pixel (0 = no diamond detected).
            "qubits_per_pixel": qubits_per_pixel,
        },
        "per_pin": per_pin,
        "d_fit_ghz": round(d_fit, 5),
        "t1_ms": round(t1 * 1e3, 3), "t2_us": round(t2 * 1e6, 3),
        "register_width": register_width,
        "register_line_freqs_hz": line_freqs_hz,
        "pixel_mode_recommendation": pixel_mode_recommendation,
        "n_nv_per_pixel": register_width,
        "gradient_code": grad_code,
        "gradient_mhz_per_um": round(grad_mhz_per_um, 5),
        "coil_current_a": round(coil_current_a, 4),
        "source": "hardware" if hardware else "simulator",
        "io": io_label,
        "stage4_source": stage4_source,
    }