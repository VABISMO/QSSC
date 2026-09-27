# qssc/backend_fits.py
"""Host-side physics fits on raw ADC codes returned by the RTL pulse engine.

The firmware pulse engine (``fpga/rtl/command_parser.v``, PR3a/PR3b) returns
RAW 16-bit ADC codes / raw on-off accumulations; these numpy-only helpers fit
the physics the /api endpoints need (Ramsey fringe rate -> rotation rate,
fringe envelope decay -> T2). No scipy dependency: an FFT rate estimate plus a
log-linear envelope fit is enough, and degrades gracefully (nan / 0) when the
codes are flat or the fit is ill-conditioned.
"""
from __future__ import annotations

import math

import numpy as np

from .simulator import READOUT_NOISE

# ---- host-side fits on raw ADC codes (PR3b) -------------------------------
# The RTL pulse engine returns raw readout codes; these numpy-only helpers fit
# the physics the /api endpoints need (Ramsey fringe rate -> rotation rate,
# fringe envelope decay -> T2). No scipy dependency: an FFT rate estimate plus
# a log-linear envelope fit is enough for the on-host fit, and degrades
# gracefully (nan / 0) when the codes are flat or the fit is ill-conditioned.

_SEQ_KIND_NAMES = {"ramsey": 0, "hahn": 1, "cpmg": 2}


def _seq_kind_num(kind) -> int:
    """Normalise a sequence kind to its 0/1/2 RTL code (int or name)."""
    if isinstance(kind, (int, np.integer)):
        k = int(kind)
    else:
        k = _SEQ_KIND_NAMES.get(str(kind).lower(), -1)
    if k not in (0, 1, 2):
        raise ValueError(f"kind must be 0=Ramsey/1=Hahn/2=CPMG, got {kind!r}")
    return k


def _log_linear_slope(x: np.ndarray, y: np.ndarray) -> float:
    """Least-squares slope of log(y) vs x (y > 0). Returns nan if undefined."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    mask = y > 1e-9
    if int(mask.sum()) < 2:
        return float("nan")
    lx = np.log(y[mask])
    xs = x[mask]
    slope = float(np.polyfit(xs, lx, 1)[0]) if np.ptp(xs) > 0 else float("nan")
    return slope


def _fit_t2_from_codes(codes: np.ndarray, t_step_ns: int) -> float:
    """Fit T2 (us) from the decay envelope of a Ramsey/Hahn fringe.

    Models ``c(t) = A + B * fringe(t) * exp(-t/T2)``: takes the upper envelope
    of the detrended codes (local maxima of |c - mean|) and fits
    ``log(amp) = a - t/T2``. Returns T2 in microseconds, or ``nan`` when the
    codes are flat / the envelope is not decaying (coherence not the limiter
    within the sampled window).
    """
    codes = np.asarray(codes, dtype=float)
    n = codes.size
    if n < 4 or t_step_ns <= 0:
        return float("nan")
    t_ns = np.arange(n) * float(t_step_ns)
    y = codes - codes.mean()
    amp = np.abs(y)
    if amp.max() < 1.0:                       # flat -> no fringe -> no decay
        return float("nan")
    peaks = [i for i in range(1, n - 1)
             if amp[i] >= amp[i - 1] and amp[i] >= amp[i + 1]]
    if len(peaks) < 2:                        # too few maxima -> use all samples
        peaks = list(range(n))
    slope = _log_linear_slope(t_ns[peaks], amp[peaks])
    if not math.isfinite(slope) or slope >= 0:
        return float("nan")
    t2_ns = -1.0 / slope
    return float(t2_ns / 1000.0)              # ns -> us


def _fit_gyro_from_codes(codes: np.ndarray, t_step_ns: int,
                         t_ns: int) -> dict:
    """Ramsey fringe -> rotation rate (dps) + T2 (us) from raw ADC codes.

    FFT on the detrended codes gives the fringe frequency; the Sagnac relation
    makes the rotation rate equal to the precession frequency
    (``rate_dps = degrees(omega)``). T2 comes from the envelope decay
    (:func:`_fit_t2_from_codes`). Returns a dict shaped like
    :func:`qssc.sensors.gyro_measure` so the GyroPanel frontend is unchanged.
    """
    codes = np.asarray(codes, dtype=float)
    n = codes.size
    t_s = (np.arange(n) * float(t_step_ns)) * 1e-9
    T_s = max(float(t_s[-1]) if n else 0.0, 1e-9)
    y = codes - codes.mean()
    sigma = float(y.std())
    t2_us = _fit_t2_from_codes(codes, t_step_ns)
    # normalised PL trace for the GyroPanel fringe chart (0..1)
    lo, hi = float(codes.min()), float(codes.max())
    sig = ((codes - lo) / (hi - lo)) if hi > lo else np.zeros(n)
    trace = {"time_us": [round(float(x), 3) for x in (np.arange(n) * t_step_ns / 1000.0)],
             "signal": [round(float(x), 5) for x in sig]}
    if n < 4 or t_step_ns <= 0 or sigma < 1.0:
        # Flat / too-short trace: no fringe is resolvable. Report zero rate
        # with a noise floor set by the readout noise over the precession
        # window (rate-grade: Earth-rate is below this floor for short T2).
        rate_noise = math.degrees(float(READOUT_NOISE) / T_s)
        return _gyro_result_dict(0.0, rate_noise, t2_us, T_s * 1e6, n, trace)
    win = np.hanning(n)
    # NOTE: a single-axis Ramsey fringe 0.5+0.5*cos(omega*t)*exp(-t/T2) is EVEN
    # in t, so its FFT is real/even and the SIGN of omega is physically
    # unrecoverable from one trace (rfftfreq returns only non-negative freqs and
    # np.abs discards the phase, which for a cosine is 0/pi anyway -- it carries
    # no sign). rate_dps below is therefore |rate|; recovering direction needs a
    # quadrature / second-axis channel, not a sign extracted from this spectrum.
    Y = np.abs(np.fft.rfft(y * win))
    freqs = np.fft.rfftfreq(n, d=float(t_step_ns) * 1e-9)   # Hz
    peak = 1 + int(np.argmax(Y[1:])) if Y.size > 1 else 0
    f0_hz = float(freqs[peak]) if peak < freqs.size else 0.0
    omega = 2.0 * math.pi * f0_hz                            # rad/s (>= 0)
    rate_dps = math.degrees(omega)                           # Sagnac |rate|
    # Hanning coherent gain is 0.5 (sum(window)/n), so the windowed FFT peak is
    # half the rectangular-window peak -> divide by n/4 (not n/2) to recover the
    # true fringe amplitude. This only feeds rate_noise (the uncertainty), not
    # the rate; the old n/2 under-estimated fringe_amp ~2x -> rate_noise ~2x
    # conservative.
    fringe_amp = float(Y[peak]) / (n / 4.0) if Y.size > 1 else sigma
    phase_noise = sigma / max(fringe_amp, 1.0) / math.sqrt(n)
    rate_noise = math.degrees(phase_noise / T_s)
    return _gyro_result_dict(rate_dps, rate_noise, t2_us, T_s * 1e6, n, trace)


def _gyro_result_dict(rate_dps: float, rate_noise_dps: float,
                      t2_us: float, free_precession_us: float,
                      n_avg: int, trace: dict) -> dict:
    """Shape a gyro result dict to match qssc.sensors.GyroResult (asdict)."""
    earth_rate = 15.0 / 3600.0                               # deg/s
    grade = bool(rate_noise_dps > earth_rate)
    return {
        "profile": "hardware",
        "temperature_k": 0.0,                                # not measured here
        "t2_us": round(t2_us, 4) if math.isfinite(t2_us) else None,
        "rotation_rate_dps": round(rate_dps, 6),
        "measured_rate_dps": round(rate_dps, 6),
        "free_precession_us": round(free_precession_us, 3),
        "n_averages": int(n_avg),
        "phase_noise_rad": round(math.radians(rate_noise_dps), 6),
        "rate_noise_dps": round(rate_noise_dps, 4),
        "rate_grade": grade,
        "trace": trace,
        "note": ("Ramsey pi/2-T-pi/2 run via RUN_SEQUENCE; rate from FFT of "
                 "the on-chip ADC codes, T2 from the fringe envelope decay."),
    }