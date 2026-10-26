"""Analog signal endpoints: single-trace + per-pixel array lab.

``signal`` models one time-resolved PL trace; ``signal_array`` is the per-pixel
fired-vs-received lab across the 8x8 array. Both stay plain callables so tests
can invoke ``signal_array(...)`` directly with kwargs.
"""
from __future__ import annotations

import os
import math

import numpy as np
from fastapi import APIRouter
from ._guards import no_fpga_response

from qssc import (
    get_profile, coherence_at_temperature,
    auto_align, NoFPGADetected,
)
from qssc.simulator import BRIGHT_CODE, BG_CODE, READOUT_NOISE, ADC_MAX
from qssc.array_calibration import nv_ceiling_for_size
from qssc.backend_fits import _fit_t2_from_codes

from ._guards import _clamp_points, connect_fpga, GRAD_PIXEL_UM

router = APIRouter()


@router.get("/api/signal")
def signal(profile: str = "NV", temperature_k: float = 300.0,
           b_field: float = 1e-3, detuning_mhz: float = 2.0,
           sequence: str = "ramsey", points: int = 240, seed: int = 42,
           hardware: bool = False):
    """Analog detected-photoluminescence trace during a pulse sequence.

    Models the time-resolved fluorescence the on-chip 2D photodetector array
    would record, with real defect coherence (T2) and readout contrast:

    * ``ramsey``  -- PL(t) = A + B·cos(2π·Δ·t)·exp(-t/T2)  (Ramsey fringes)
    * ``odmr``    -- CW ODMR dip when the microwave is on resonance
    * ``pulse``   -- spin initialisation + readout decay curve

    With ``hardware=True`` the trace is measured on a real board: the host
    runs ``RUN_SEQUENCE`` (ramsey/hahn/cpmg) and the returned ADC codes are
    normalised to PL. ``odmr``/``pulse`` (CW / non-pulsed) have no
    ``RUN_SEQUENCE`` equivalent and fall back to the model trace. No board ->
    ``no-fpga-detected`` body.

    Returns the time axis (ns), the detected signal (normalised PL) and the
    physics parameters used, so the dashboard can draw an oscilloscope-style
    analog trace grounded in the chip's real T2/readout.
    """
    prof = get_profile(profile)
    t1, t2 = coherence_at_temperature(prof, temperature_k)
    rng = np.random.default_rng(seed)
    seq = sequence.lower()
    readout_ns = prof.readout_ns
    points = _clamp_points(points)

    # Real-hardware path: run the pulse-sequence engine and normalise the ADC
    # codes to PL. Only the pulsed sequences (ramsey/hahn/cpmg) map to a
    # RUN_SEQUENCE kind; odmr/pulse stay on the model trace.
    if hardware and seq in ("ramsey", "hahn", "cpmg"):
        port = os.getenv("QSSC_FPGA_PORT", "").strip()
        if not port:
            return no_fpga_response(
                "messageNo FPGA detected — connect the board and set QSSC_FPGA_PORT, or switch to Simulator.")
        try:
            fpga = connect_fpga(port=port, profile=profile,
                                temperature_k=temperature_k, seed=seed)
        except NoFPGADetected as exc:
            return no_fpga_response(str(exc))
        kind = {"ramsey": 0, "hahn": 1, "cpmg": 2}[seq]
        n = max(8, min(int(points), 1024))
        # window the sequence over ~6*T2 so the decay is visible, matching the
        # model trace span.
        span_ns = max(6 * t2 * 1e9, 4000)
        t_step = max(10, int(round(span_ns / n)))
        try:
            codes = fpga.run_sequence(0, kind, n, t_step)
        finally:
            fpga.close()
        t = np.arange(n) * t_step
        lo, hi = float(codes.min()), float(codes.max())
        sig = ((codes - lo) / (hi - lo)) if hi > lo else np.zeros(n)
        t2_fit = _fit_t2_from_codes(codes, t_step)
        return {
            "profile": prof.short, "sequence": seq,
            "temperature_k": temperature_k,
            "t2_us": round(t2_fit, 4) if math.isfinite(t2_fit) else round(t2 * 1e6, 4),
            "readout_ns": readout_ns,
            "d_ghz": prof.d_ghz, "detuning_mhz": detuning_mhz,
            "time_ns": [round(float(x), 1) for x in t],
            "signal": [round(float(x), 5) for x in sig],
            "source": "hardware",
        }

    if seq == "odmr":
        # total sweep window a few T2 long
        span_ns = max(8 * t2 * 1e9, 4000)
        t = np.linspace(0, span_ns, points)
        # contrast dips while mw is near resonance, recovers off-resonance
        lor = 1.0 / (1.0 + ((detuning_mhz) / 3.0) ** 2)
        sig = 1.0 - 0.35 * lor * np.exp(-t / (t2 * 1e9))
    elif seq == "pulse":
        span_ns = max(6 * readout_ns, 4000)
        t = np.linspace(0, span_ns, int(points))
        sig = (0.85 * np.exp(-t / readout_ns) + 0.15)
    else:  # ramsey
        span_ns = max(6 * t2 * 1e9, 4000)
        t = np.linspace(0, span_ns, int(points))
        sig = (0.5 + 0.45 * np.cos(2 * np.pi * detuning_mhz * t / 1e3)
               * np.exp(-t / (t2 * 1e9)))
    # detector shot noise + readout contrast
    noise = rng.normal(0, 0.012, size=sig.shape)
    sig = np.clip(sig + noise, 0, 1.05)
    return {
        "profile": prof.short, "sequence": seq,
        "temperature_k": temperature_k,
        "t2_us": round(t2 * 1e6, 4), "readout_ns": readout_ns,
        "d_ghz": prof.d_ghz, "detuning_mhz": detuning_mhz,
        "time_ns": [round(float(x), 1) for x in t],
        "signal": [round(float(x), 5) for x in sig],
    }


@router.get("/api/signal/array")
def signal_array(profile: str = "NV", temperature_k: float = 300.0,
                 b_field: float = 1e-3, detuning_mhz: float = 2.0,
                 sequence: str = "ramsey", points: int = 180, seed: int = 42,
                 grid: int = 8, hardware: bool = False):
    """Per-pixel analog signal lab: FIRED emitter drive vs RECEIVED detector
    photocurrent for every pixel in the 8x8 array, plus per-pixel KPIs.

    Returns two time-aligned traces per pixel on a shared time axis:

    * ``fired``    -- the CQD emitter pump drive envelope (the DRIVE_PATTERN
      gate, 0..1) the FPGA arms for that pixel's channel. A dead pixel (no
      diamond detected by the stage-1 scan) is not armed -> 0 everywhere.
    * ``received`` -- the time-resolved photoluminescence the on-chip
      graphene/MoS2 + TIA + AD7616 chain records for that pixel, in 16-bit ADC
      codes (BG_CODE..BRIGHT_CODE). The trace follows the selected pulse
      sequence (ramsey fringes / CW ODMR dip / spin-readout decay) with the
      defect's real T2 + readout contrast, scaled per pixel by its NV
      population: a pixel loaded with a larger microdiamond aggregate holds
      more NVs -> brighter PL -> a taller trace. Dead pixels report only the
      detector background floor + shot noise.

    Per-pixel KPIs (amplitude, mean/peak photocurrent, noise floor, SNR, dip
    depth, drive->response phase lag, on/off ratio, NV count, alive) + an array
    summary feed the dashboard's per-pixel visualizer + KPI tiles, replacing
    the single scalar trace. The per-pixel NV population uses the SAME grounded
    ``nv_ceiling_for_size`` model as the calibration grid (300 NV at 200 um,
    scaling as size^3), so a dim/NV-limited pixel here is the same pixel that
    shows fewer qubits in the calibration grid.
    """
    prof = get_profile(profile)
    t1, t2 = coherence_at_temperature(prof, temperature_k)
    rng = np.random.default_rng(seed)
    seq = sequence.lower()
    readout_ns = prof.readout_ns
    grid = max(1, min(int(grid), 16))
    n_px = grid * grid
    points = max(32, min(int(points), 256))

    # Hardware mode without a board returns no-fpga-detected (no silent sim).
    # A real per-pixel trace needs 64 serial RUN_SEQUENCE round trips and is not
    # wired here; even with QSSC_FPGA_PORT set this endpoint returns the SIM
    # model (it never opens a board connection), so the source label must say
    # "simulated" -- stamping it "hardware" when no board was touched would
    # mislead an operator debugging a dead channel (audit #103).
    if hardware:
        port = os.getenv("QSSC_FPGA_PORT", "").strip()
        if not port:
            return no_fpga_response(
                "messageNo FPGA detected — connect the board and set QSSC_FPGA_PORT, or switch to Simulator.")

    # Base normalized PL trace (0..1) for the selected sequence -- the same
    # model as /api/signal. The spin dynamics are a defect+gradient property,
    # shared across pixels; per-pixel variation is the NV-population scaling
    # and independent detector noise.
    if seq == "odmr":
        span_ns = max(8 * t2 * 1e9, 4000)
        t = np.linspace(0, span_ns, points)
        lor = 1.0 / (1.0 + (detuning_mhz / 3.0) ** 2)
        pl = 1.0 - 0.35 * lor * np.exp(-t / (t2 * 1e9))
    elif seq == "pulse":
        span_ns = max(6 * readout_ns, 4000)
        t = np.linspace(0, span_ns, points)
        pl = 0.85 * np.exp(-t / readout_ns) + 0.15
    else:  # ramsey
        span_ns = max(6 * t2 * 1e9, 4000)
        t = np.linspace(0, span_ns, points)
        pl = (0.5 + 0.45 * np.cos(2 * np.pi * detuning_mhz * t / 1e3)
              * np.exp(-t / (t2 * 1e9)))
    pl = np.clip(pl, 0.0, 1.0)
    dt_ns = float(t[1] - t[0]) if len(t) > 1 else 1.0

    # Emitter drive envelope (DRIVE_PATTERN gate): periodic pump windows the
    # FPGA arms per channel -- the readout windows where the detector samples
    # PL. 4 gates over the span, 30% duty.
    n_gates = 4
    duty = 0.30
    gate_period = span_ns / n_gates
    gate = ((t % gate_period) < (duty * gate_period)).astype(float)
    gate_on = gate > 0.5
    gate_off = ~gate_on

    # Per-pixel alive mask + NV population (same grounded model as the
    # calibration grid). auto_align gives the stage-1 detector presence scan;
    # the NV population scales the PL amplitude per pixel.
    sites = auto_align(prof, grid=grid, temperature_k=temperature_k, rng=rng)
    nv_ceiling = nv_ceiling_for_size(GRAD_PIXEL_UM)
    fired, received, pixels = [], [], []
    snrs, amps, dips, mean_codes = [], [], [], []
    for idx, s in enumerate(sites):
        r, c = divmod(idx, grid)
        if s.usable:
            d_um = float(rng.uniform(0.25 * GRAD_PIXEL_UM, GRAD_PIXEL_UM))
            n_nv = max(1, int(round(nv_ceiling_for_size(d_um))))
            bright = float(np.clip(n_nv / nv_ceiling, 0.05, 1.0))
            recv = (BG_CODE + bright * (BRIGHT_CODE - BG_CODE) * pl
                    + rng.normal(0, READOUT_NOISE, size=pl.shape))
            f_env = gate
        else:
            n_nv = 0
            bright = 0.0
            recv = BG_CODE + rng.normal(0, READOUT_NOISE, size=pl.shape)
            f_env = np.zeros_like(gate)
        recv = np.clip(recv, 0, ADC_MAX)
        fired.append([round(float(x), 3) for x in f_env])
        received.append([int(round(float(x))) for x in recv])
        # ---- per-pixel KPIs ----
        amp = float(recv.max() - recv.min())
        mean_c = float(recv.mean())
        peak = float(recv.max())
        # noise floor: std of the quiet (gate-off) half; fall back to full std.
        noise_floor = float(np.std(recv[gate_off])) if gate_off.any() else float(np.std(recv))
        snr = amp / noise_floor if noise_floor > 0 else 0.0
        dip = amp / peak if peak > 0 else 0.0
        # drive -> response phase lag: cross-correlation of the gate and the
        # photocurrent (the PL response lags the pump by the readout time). A
        # dead pixel (drive all-zero) has no lag.
        if not f_env.any() or recv.std() <= 0:
            phase_lag = 0.0
        else:
            fr = f_env - f_env.mean()
            rr = recv - recv.mean()
            denom = math.sqrt(float(np.sum(fr * fr) * np.sum(rr * rr)))
            if denom > 0:
                # local response window only (±5% of the trace about zero lag) --
                # the photocurrent follows the pump within the readout time, so
                # the meaningful lag is small; searching the whole span just
                # aliases the gate/fringe periodicity.
                xcorr = np.correlate(fr, rr, mode="full") / denom
                center = len(t) - 1
                W = max(1, int(0.05 * len(t)))
                lo, hi = max(0, center - W), min(len(xcorr), center + W + 1)
                lag_idx = int(lo + np.argmax(np.abs(xcorr[lo:hi]))) - center
                phase_lag = float(abs(lag_idx) * dt_ns)
            else:
                phase_lag = 0.0
        on = float(recv[gate_on].mean()) if gate_on.any() else 0.0
        off = float(recv[gate_off].mean()) if gate_off.any() else 0.0
        on_off = on / off if off > 0 else 0.0
        pixels.append({
            "pixel_id": idx, "row": r, "col": c, "alive": bool(s.usable),
            "n_nv": int(n_nv),
            "amplitude": round(amp, 1), "mean": round(mean_c, 1),
            "peak": round(peak, 1), "noise_floor": round(noise_floor, 1),
            "snr": round(snr, 2), "dip_depth": round(dip, 3),
            "phase_lag_ns": round(phase_lag, 1), "on_off_ratio": round(on_off, 3),
        })
        if s.usable:
            snrs.append(snr); amps.append(amp); dips.append(dip); mean_codes.append(mean_c)
    alive_count = int(sum(1 for s in sites if s.usable))
    summary = {
        "alive": alive_count, "dead": n_px - alive_count,
        "mean_snr": round(float(np.mean(snrs)), 2) if snrs else 0.0,
        "mean_amplitude": round(float(np.mean(amps)), 1) if amps else 0.0,
        "mean_dip_depth": round(float(np.mean(dips)), 3) if dips else 0.0,
        "total_photocurrent": round(float(np.sum(mean_codes)), 1) if mean_codes else 0.0,
    }
    return {
        "profile": prof.short, "sequence": seq,
        "temperature_k": temperature_k, "t2_us": round(t2 * 1e6, 4),
        "d_ghz": prof.d_ghz, "detuning_mhz": detuning_mhz,
        "n_pixels": n_px, "n_points": int(points),
        "time_ns": [round(float(x), 1) for x in t],
        "gate_duty": duty, "n_gates": n_gates,
        "fired": fired, "received": received,
        "pixels": pixels, "summary": summary,
        # The 64-serial RUN_SEQUENCE hardware path is not wired here, so this
        # endpoint always returns the sim model -- never claim "hardware"
        # when no board was touched (audit #103).
        "source": "simulated",
    }