"""Quantum sensor functions for the QSSC chip: lock-in readout, ODMR compass
(magnetometer) and Ramsey precession gyroscope.

These reuse the defect-profile ODMR model in :mod:`qssc.simulator` and the
coherence model in :mod:`qssc.defects`.  They are *physics models* of what the
hardware measures through the J3 SMA -> A1 loop antenna (microwave) and the
64-channel TIA + 4x AD7616 (optical readout) chain described in
HARDWARE_DESIGN.md.

Grounding rules (see memory: datasheet-grounded-physics):
  * Numbers come from the DefectProfile (gyromagnetic_ratio, odmr_linewidth_hz,
    t2_us, readout_ns) — no fabricated sensitivity specs.
  * The as-built sensor is a spin-coated microdiamond *sensor array* with
    T2 ~ us-tens-of-us (sensor grade, not compute grade).  The compass
    (CW ODMR magnetometry) is the strong use case; the gyroscope is rate-grade
    with this sensor and inertial-grade only with a bulk-CVD or Sagnac upgrade.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from .defects import DefectProfile, get_profile, coherence_at_temperature
from .simulator import simulate_odmr
from .simulator_constants import READOUT_NOISE, BRIGHT_CODE


# ---------------------------------------------------------------------------
# Lock-in integrated readout
# ---------------------------------------------------------------------------

def lockin_integrated_readout(
    bright_code: float,
    contrast: float,
    n_frames: int,
    noise_counts: float,
    rng: np.random.Generator,
) -> dict:
    """Model the lock-in-integrated readout that replaces 'keep last frame'.

    The ADC free-runs at ~10 kfps (one 64-ch frame every ~100 us, see
    adc_reader.v).  During a ``duration_ms`` exposure the emitter is modulated
    on/off at the 1 kHz lock-in (mod_sync); the correct readout integrates the
    on-minus-off differential over *all* frames in the window, not the single
    last frame the legacy READ_DETECTORS returns.

    SNR scales as sqrt(N_frames) vs the single-frame snapshot, so a 50 ms
    exposure (~500 frames) gives ~22x better state discrimination.

    Returns the integrated differential code, its sigma, the single-frame SNR
    and the integrated SNR so the caller can show the improvement.
    """
    n_frames = max(1, int(n_frames))
    bright = float(bright_code)
    dark = bright * (1.0 - float(contrast))
    # per-frame on (bright) and off (dark) photocurrent codes with shot noise
    on = rng.normal(bright, noise_counts, size=n_frames)
    off = rng.normal(dark, noise_counts, size=n_frames)
    diff = on - off                      # per-frame lock-in differential
    integrated = float(diff.mean())       # averaged differential (the readout)
    sigma = float(diff.std(ddof=1) / math.sqrt(n_frames)) if n_frames > 1 else noise_counts
    single_sigma = noise_counts
    return {
        "n_frames": n_frames,
        "differential_code": round(integrated, 2),
        "sigma_code": round(sigma, 2),
        "single_frame_snr": round(abs(integrated) / single_sigma, 2) if single_sigma else 0.0,
        "integrated_snr": round(abs(integrated) / sigma, 2) if sigma else 0.0,
        "snr_gain": round(math.sqrt(n_frames), 2),
        "bright_code": round(bright, 2),
        "dark_code": round(dark, 2),
    }


# ---------------------------------------------------------------------------
# Compass: 4-axis ODMR magnetometer -> B-vector -> heading
# ---------------------------------------------------------------------------

# NV diamond has four crystallographic <111> axes.  Each orientation projects
# the ambient field B onto its own axis; the two ms=0<->+-1 transitions split
# by 2*gamma*|B.n_i|.  Measuring all four doublets reconstructs the full B
# vector (the basis is redundant in 3D -> least squares).
_NV_AXES = np.array([
    [1, 1, 1],
    [1, -1, -1],
    [-1, 1, -1],
    [-1, -1, 1],
], dtype=float) / math.sqrt(3.0)


@dataclass
class CompassResult:
    profile: str
    b_field_t: float
    b_reconstructed_t: float
    heading_deg: float
    elevation_deg: float
    splits_mhz: list[float]
    sensitivity_ut: float
    n_avg: int
    resolved_axes: int = 0
    dip_curves: list[dict] = field(default_factory=list)
    note: str = ""


def compass_measure(
    profile: DefectProfile | str = "NV",
    b_vector_t: Sequence[float] = (50e-6, 0.0, -20e-6),
    n_avg: int = 1000,
    linewidth_mhz: float | None = None,
    points: int = 300,
    *, rng: np.random.Generator,
) -> CompassResult:
    """ODMR compass: sweep the MW (ADF4351 via J3 -> A1 antenna) across the
    NV resonance for each of the 4 <111> orientations, fit the Zeeman doublet
    splitting per axis, and reconstruct the ambient B-vector + heading.

    Physics:
      * transition frequencies f = D +/- gamma * (B . n_i)
      * doublet split per axis  = 2 * gamma * |B . n_i|
      * sensitivity (single shot) ~ FWHM / (2*gamma); N averages -> /sqrt(N).

    Earth's field (~25-65 uT) gives a ~1.4-3.6 MHz split, comparable to the
    NV inhomogeneous linewidth (~5 MHz): a single shot is marginal, so the
    compass needs averaging (n_avg) to resolve Earth-rate fields.  This is the
    linewidth limit set by the sensor's own FWHM, not a fabricated sensitivity.
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    B = np.asarray(b_vector_t, dtype=float)
    b_mag = float(np.linalg.norm(B))
    gamma = prof.gyromagnetic_ratio            # Hz/T (NV 28 GHz/T)
    lw = (linewidth_mhz if linewidth_mhz is not None
          else prof.odmr_linewidth_hz / 1e6)   # MHz FWHM
    # single-shot field sensitivity: dF/(2*gamma).  Averaging improves 1/sqrt(N).
    sens_single_ut = (lw * 1e6) / (2.0 * gamma) * 1e6   # T -> uT
    sens_ut = sens_single_ut / math.sqrt(max(1, n_avg))

    splits_mhz: list[float] = []
    dip_curves: list[dict] = []
    # single-shot split resolution ~ linewidth (MHz); averaging -> /sqrt(N)
    split_sigma_mhz = lw / math.sqrt(max(1, n_avg))
    for i, n in enumerate(_NV_AXES):
        Bpar = float(B @ n)                       # signed projection
        # The ODMR curve (for the frontend) -- real hyperfine+Zeeman shape.
        f, c = simulate_odmr(prof, freqs_ghz=None, b_field_t=abs(Bpar),
                             linewidth_mhz=lw, contrast_depth=0.05)
        if i < 2:  # keep 2 of 4 curves for the frontend (payload size)
            dip_curves.append({
                "axis": i,
                "frequencies_ghz": [round(float(x), 5) for x in f],
                "contrast": [round(float(x), 5) for x in c],
            })
        # Measured split = true Zeeman split (2*gamma*|B.n|) + linewidth-limited
        # noise. A real fit recovers this when split >> linewidth; below it the
        # doublet is unresolved (split within noise of 0).
        split_true_mhz = 2.0 * gamma * abs(Bpar) / 1e6
        split_meas = split_true_mhz + rng.normal(0.0, split_sigma_mhz)
        splits_mhz.append(round(max(0.0, split_meas), 4))

    # Reconstruct B-vector from the RESOLVED axes (split > linewidth/2, i.e. the
    # doublet is distinguishable from the noise/linewidth floor).
    resolve_thr = lw / 2.0
    resolved = [i for i, s in enumerate(splits_mhz) if s > resolve_thr]
    b_rec_mag = 0.0
    heading = 0.0
    elevation = 0.0
    if len(resolved) >= 3:
        A = _NV_AXES[resolved]                            # (k,3) unit axes
        b_measured = np.array([splits_mhz[i] for i in resolved]) * 1e6 / (2.0 * gamma)
        best_x, best_resid = None, np.inf
        for mask in range(1 << len(resolved)):
            signs = np.array([1.0 if (mask >> k) & 1 else -1.0
                              for k in range(len(resolved))])
            sol, *_ = np.linalg.lstsq(A, signs * b_measured, rcond=None)
            resid = float(np.linalg.norm(A @ sol - signs * b_measured))
            if resid < best_resid:
                best_resid, best_x = resid, sol
        Brec = best_x if best_x is not None else np.zeros(3)
        b_rec_mag = float(np.linalg.norm(Brec))
        if b_rec_mag > 0:
            heading = math.degrees(math.atan2(Brec[1], Brec[0]))
            elevation = math.degrees(math.asin(np.clip(Brec[2] / b_rec_mag, -1, 1)))
    resolved_count = len(resolved)

    return CompassResult(
        profile=prof.short,
        b_field_t=b_mag,
        b_reconstructed_t=b_rec_mag,
        heading_deg=round(heading, 2),
        elevation_deg=round(elevation, 2),
        splits_mhz=splits_mhz,
        sensitivity_ut=round(sens_ut, 4),
        n_avg=n_avg,
        resolved_axes=resolved_count,
        dip_curves=dip_curves,
        note=(f"{resolved_count}/4 NV <111> axes resolved (split > linewidth "
              f"{lw:g} MHz). ODMR linewidth {lw:g} MHz -> single-shot "
              f"sensitivity {sens_single_ut:.1f} uT; {n_avg} averages -> "
              f"{sens_ut:.2f} uT. Earth's field (~25-65 uT) needs averaging at "
              f"this linewidth. MW delivered via J3 SMA -> A1 loop antenna."),
    )


def _fit_doublet_split(freqs_ghz: np.ndarray, contrast: np.ndarray,
                       D_hz: float, linewidth_mhz: float) -> tuple[float, float, float]:
    """Return (split_MHz, f_dip1_GHz, f_dip2_GHz) from an ODMR spectrum.

    Robustly finds the deepest minimum on each side of D (the ms=0<->+1 and
    ms=0<->-1 transitions).  When the Zeeman split is below the linewidth the
    two dips merge into one broad dip straddling D; we detect that (both sides
    minimia sit within linewidth/2 of D) and report split=0 = UNRESOLVED.
    """
    D_ghz = D_hz / 1e9
    half_lw_ghz = (linewidth_mhz / 1e3) / 2.0
    c = np.asarray(contrast)
    f = np.asarray(freqs_ghz)
    left = np.where(f < D_ghz)[0]
    right = np.where(f >= D_ghz)[0]
    if len(left) == 0 or len(right) == 0:
        i0 = int(np.argmin(c))
        return 0.0, float(f[i0]), float(f[i0])
    iL = int(left[np.argmin(c[left])])
    iR = int(right[np.argmin(c[right])])
    # unresolved: both minima within half-linewidth of D (one broad merged dip)
    if abs(f[iL] - D_ghz) < half_lw_ghz and abs(f[iR] - D_ghz) < half_lw_ghz:
        return 0.0, float(f[iL]), float(f[iR])
    split_mhz = abs(f[iR] - f[iL]) * 1e3
    return round(split_mhz, 4), float(f[iL]), float(f[iR])


# ---------------------------------------------------------------------------
# Gyroscope: Ramsey precession, T2-limited, rate-grade
# ---------------------------------------------------------------------------

@dataclass
class GyroResult:
    profile: str
    temperature_k: float
    t2_us: float
    rotation_rate_dps: float
    measured_rate_dps: float
    free_precession_us: float
    n_averages: int
    phase_noise_rad: float
    rate_noise_dps: float
    rate_grade: bool
    trace: dict = field(default_factory=dict)
    note: str = ""


def gyro_measure(
    profile: DefectProfile | str = "NV",
    temperature_k: float = 300.0,
    rotation_rate_dps: float = 1.0,
    free_precession_us: float | None = None,
    n_averages: int = 100,
    points: int = 200,
    sensor_t2_us: float | None = None,
    *, rng: np.random.Generator,
) -> GyroResult:
    """Ramsey-precession gyroscope model.

    Sequence (the pulse_seq engine on the FPGA): pi/2 -- free precession T --
    pi/2 -- optical readout.  A rotation of the sensor at rate Omega shifts the
    accumulated phase by phi = Omega * T (Sagnac/precession term, geometry
    factor folded into the effective rate).  The Ramsey fringe is
    PL(t) = A + B cos(Omega*t) exp(-t/T2).

    T2 limit: the as-built microdiamond sensor array has T2 ~ us-tens-of-us
    (sensor grade).  Over T2 = 10 us, Earth-rate rotation (~4e-3 deg/s)
    accumulates ~1e-7 deg of phase -- not measurable without enormous averaging
    or a Sagnac loop.  So this is RATE-GRADE with the current sensor: it
    responds to fast rotation / vibration, not inertial-grade heading.  No
    fabricated sensitivity number is given; the T2 coherence window is reported
    and the caller can see the phase-per-T2 directly.
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    _t1, t2 = coherence_at_temperature(prof, temperature_k)
    # The profile T2 is the BULK compute-grade coherence (NV 12C-CVD ~1.8 ms).
    # The as-built spin-coated microdiamond SENSOR array is sensor-grade
    # (T2 ~ us-tens-of-us, per the audit-#15 scoping) -- not quoted by a
    # supplier. ``sensor_t2_us`` lets the caller override the coherence used
    # for the Ramsey envelope + effective fringe amplitude so a short
    # sensor-grade interrogation (free_precession_us=10) decays at the real
    # sensor T2, not the bulk 1.8 ms (audit #134 -- the prior code used the
    # profile T2 for both, making rate_noise ~2.6x optimistic at T=10 us).
    # The T2 actually used is reported in GyroResult.t2_us.
    t2_us_profile = t2 * 1e6
    if sensor_t2_us is not None:
        t2_us = float(sensor_t2_us)
        t2_eff = t2_us * 1e-6
    else:
        t2_us = t2_us_profile
        t2_eff = t2
    # free-precession window: default to the full coherence window (T2),
    # capped at T2 since coherence is gone past it. The caller can shorten it
    # to model the sensor-grade array (us T2) vs the bulk compute grade.
    T_us = float(free_precession_us if free_precession_us is not None
                 else t2_us)
    T_s = T_us * 1e-6
    omega = math.radians(abs(float(rotation_rate_dps)))   # |rate|: a single-axis Ramsey fringe cos(Omega*t) is EVEN in Omega, so the sign of the rotation rate is physically unrecoverable from one trace (audit #149). The companion FFT fit path (backend_fits._fit_gyro_from_codes) also returns |rate|; this keeps the two gyro paths consistent.
    # phase accumulated over the free-precession window
    phase = omega * T_s
    # Ramsey trace: PL vs free-precession time
    t_us = np.linspace(0, T_us, points)
    t_s = t_us * 1e-6
    envelope = np.exp(-t_s / max(t2_eff, 1e-9))
    fringe_amp = 0.45
    signal = 0.5 + fringe_amp * np.cos(omega * t_s) * envelope
    # per-shot noise on the normalised PL readout: the datasheet-grounded
    # ensemble readout noise READOUT_NOISE/BRIGHT_CODE = 640/51200 = 1.25%
    # (NOT a fabricated 1%), reduced by 1/sqrt(n_averages) for N-shot
    # lock-in averaging of uncorrelated noise (audit #116).
    pl_noise = (READOUT_NOISE / BRIGHT_CODE) / math.sqrt(max(1, n_averages))
    noise = rng.normal(0, pl_noise, size=signal.shape)
    signal = np.clip(signal + noise, 0, 1.05)
    # phase noise from the Ramsey fringe: dphi ~ pl_noise / fringe_amp_eff.
    # Use the EFFECTIVE fringe amplitude at measurement time T (the envelope
    # exp(-T/T2) has shrunk the fringe from 0.45 to 0.45*exp(-T/T2)); the
    # prior code used the initial 0.45, making rate_noise ~2.6x optimistic
    # at T=T2 (audit #116).
    fringe_amp_eff = max(fringe_amp * math.exp(-T_us / max(t2_us, 1e-9)), 1e-9)
    phase_noise = pl_noise / fringe_amp_eff
    measured = math.degrees(phase / max(T_s, 1e-9)) if T_s > 0 else 0.0
    # rate noise = phase_noise / T (dividing the tiny accumulated phase by the
    # small free-precession window amplifies noise -- this is the fundamental
    # rate-grade limit: short T2 -> poor rotation sensitivity).
    rate_noise_dps = math.degrees(phase_noise / max(T_s, 1e-9))
    measured_noisy = measured + rate_noise_dps * rng.standard_normal()
    # rate-grade: Earth-rate (15 deg/hr = 4.17e-3 deg/s) below the noise floor
    earth_rate = 15.0 / 3600.0
    rate_grade = rate_noise_dps > earth_rate

    return GyroResult(
        profile=prof.short,
        temperature_k=temperature_k,
        t2_us=round(t2_us, 4),
        rotation_rate_dps=rotation_rate_dps,
        measured_rate_dps=round(measured_noisy, 6),
        free_precession_us=round(T_us, 3),
        n_averages=n_averages,
        phase_noise_rad=round(phase_noise, 6),
        rate_noise_dps=round(rate_noise_dps, 4),
        rate_grade=bool(rate_grade),
        trace={
            "time_us": [round(float(x), 3) for x in t_us],
            "signal": [round(float(x), 5) for x in signal],
            "phase_accumulated_rad": round(phase, 6),
        },
        note=(f"Ramsey pi/2-T-pi/2 over T={T_us:.1f} us (T2={t2_us:.1f} us). "
              f"Phase accumulated = Omega*T = {phase:.3e} rad; rate noise floor "
              f"{rate_noise_dps:.2e} deg/s. "
              f"Default uses the profile (compute-grade) T2~{t2_us_profile:.1f} us; "
              f"pass sensor_t2_us for the as-built sensor-grade array "
              f"(T2~us-tens-of-us) so the envelope decays at the real sensor T2, "
              f"or a shorter free_precession_us. RATE-GRADE either way: Earth-rate "
              f"({earth_rate:.2e} deg/s) is below the floor and needs bulk-CVD "
              f"T2~ms or a Sagnac loop for inertial-grade. No supplier sensitivity spec."),
    )