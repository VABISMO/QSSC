# qssc/simulator_response.py
"""On-chip readout response + dynamical-decoupling helpers.

Extracted from :mod:`qssc.simulator` (pure move, zero behavior change):
the CQD emitter pump record, the parallel per-electrode detector photocurrent
model, and the XY8 decoupling toolbox. The shared readout constants live in
:mod:`qssc.simulator_constants`; ``qssc.simulator`` re-exports every name here.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from .defects import DefectProfile, get_profile
from .simulator_constants import (
    ADC_MAX, ASE_GAIN, BG_CODE, BRIGHT_CODE, DARK_CODE, N_SITES, READOUT_NOISE,
)


def simulate_cqd_pulse(intensity: float = 1.0, duration: float = 1e-6,
                      ase_gain: float = ASE_GAIN, rng: Optional[np.random.Generator] = None) -> dict:
    """Return a noisy CQD emitter pump record (intensity, duration, ADC value).

    Models an electrically-pumped CQD ASE/LED channel: even a green LED-level pump
    polarises the NV spin, and the ``ase_gain`` factor represents the
    amplified-spontaneous-emission edge coupling that concentrates the emission
    spectrally/into the waveguide (the protocol is explicit that this is ASE, not
    a laser). ``adc`` is a 16-bit AD7616 code (0..65535).
    """
    rng = rng or np.random.default_rng()
    inten = float(np.clip(rng.normal(intensity, 0.05 * intensity), 0, 2)) * ase_gain
    dur = float(max(duration + rng.normal(0, 0.1e-6), 1e-9))
    return {"intensity": inten, "duration_s": dur, "adc": int(min(inten * ADC_MAX / 2.0, ADC_MAX)),
            "ase_gain": ase_gain}


def make_detector_response(active_emitters: set, profile: DefectProfile | str = "NV",
                           mw_freq_khz: Optional[int] = None, n_sites: int = N_SITES,
                           rng: Optional[np.random.Generator] = None) -> np.ndarray:
    """Per-electrode graphene/MoS2 detector photocurrent (16-bit AD7616 codes).

    Models the on-chip parallel readout: an electrode whose emitter channel is
    armed (in ``active_emitters``, a set of integer channel indices 0..63)
    reports high photocurrent when the defect spin is in the bright ms=0 state
    (state 0) and a reduced photocurrent (the ODMR dip) when a resonant
    microwave drive (``mw_freq_khz`` within the linewidth of D) flips it to the
    dark ms=+-1 state. Electrodes with no active emitter report only the
    detector background floor. Returns an ``int32`` array of ``n_sites`` codes.
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    rng = rng or np.random.default_rng()
    n = n_sites
    on_resonance = False
    if mw_freq_khz is not None:
        # on-resonance when the MW drive sits within one ODMR linewidth of D
        # (the old 2%*D = 57 MHz window was ~11x too wide vs the real ~5 MHz line)
        on_resonance = abs(mw_freq_khz * 1e3 - prof.zero_field_splitting) < prof.odmr_linewidth_hz
    codes = np.empty(n, dtype=np.int32)
    for q in range(n):
        if q not in active_emitters:
            level = BG_CODE
        else:
            level = BRIGHT_CODE if not on_resonance else DARK_CODE
        code = level + int(rng.normal(0, READOUT_NOISE))
        codes[q] = min(max(code, 0), ADC_MAX)
    return codes


def xy8_decoupling(n_blocks: int = 1) -> List[str]:
    """Return the XY8 pulse sequence (8 pulses per block) as gate labels."""
    block = ["X", "Y", "X", "Y", "Y", "X", "Y", "X"]
    return block * max(1, int(n_blocks))


def estimate_coherence_xy8(t2: float, n_blocks: int = 1, tau_s: float = 1e-6) -> float:
    """Estimate the extended coherence under XY8 decoupling.

    Standard XY8 refocuses low-frequency dephasing noise, extending the
    coherence by ~``T2 * 2**n_blocks`` (the exponential gain per XY8 block).
    The prior ``* (1 - exp(-tau/t2))`` factor modelled the AC-sensing SIGNAL
    AMPLITUDE (which vanishes as tau -> 0), not the T2 extension -- it
    conflated sensitivity with coherence (audit #119). The active noise path
    (``noise_filters.effective_t2``) already uses the correct ``2**n`` gain;
    this standalone estimator now matches it. ``tau_s`` is retained in the
    signature for API compatibility but no longer scales the result.
    """
    _ = tau_s  # retained for API compatibility; AC-sensing tau factor dropped
    return t2 * (2 ** n_blocks)