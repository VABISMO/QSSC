"""Classical anti-noise filter stack for the 300 K QSSC readout.

This module models the **separable, classical** environmental noise channels at
the chip (temperature drift, vibration, EMI, pressure) and the **filter stack**
that attenuates each of them. It is the legitimate, room-temperature, buildable
version of "treat temperature/pressure as a signal and filter it out": every
channel here is a classical perturbation at a separable frequency/space, so a
physical filter (active thermal servo, mechanical isolation, mu-metal shield,
sensor feed-forward, differential common-mode rejection) can reject it.

What this module does NOT model -- and deliberately cannot -- is the **quantum
thermal occupation** n_th of a magnon/spin bus mode.  That is an equilibrium
population set by Bose-Einstein statistics at the bath temperature, it occupies
the *same* mode/frequency as the signal, and the second law forbids passively
filtering it out -- only cooling (cryo) changes it.  That physics lives in a
separate cryo invention; the filters here operate strictly on the classical
noise budget that gates the *readout SNR* and the NV *T2 dephasing* window.

All channel sigmas are expressed in 16-bit ADC counts on the ``BRIGHT_CODE``
axis (the same axis as ``qssc.simulator.READOUT_NOISE``), so the stack composes
directly with the existing Invention-7 ``1/sqrt(N)`` lock-in averaging.  The
defaults (``NoiseEnvironment`` unset / ``AntiNoiseConfig`` all-off) reproduce
the prior noise model bit-for-bit: ``effective_noise_sigma`` collapses to
``base_counts / sqrt(N)`` and ``effective_t2`` returns the base ``T2``.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Environmental noise channels (the classical perturbations to filter)
# ---------------------------------------------------------------------------
@dataclass
class NoiseEnvironment:
    """The classical environmental noise channels at the chip (300 K lab).

    These are separable, filterable perturbations -- NOT the quantum n_th of the
    magnon bus (temperature-bound, out of scope).  All sigmas are in 16-bit ADC
    counts on the ``BRIGHT_CODE`` axis (same axis as ``READOUT_NOISE``).  The
    defaults are lab-grade estimates and are **model inputs, NOT datasheet
    constants** -- they characterise the enclosure environment, not the AD7616.
    Use the ``lab`` / ``quiet`` / ``noisy`` presets for a quick choice.
    """

    temp_drift_counts: float = 40.0     # slow baseline drift from ~50 mK T swings
    temp_drift_tau_s: float = 10.0      # drift correlation time (slow, seconds)
    vibration_counts: float = 120.0     # microphonics on the optical path (10-100 Hz)
    vibration_hz: float = 30.0          # dominant vibration band
    emi_counts: float = 80.0            # EMI pickup at LOCKIN_MOD_HZ harmonics
    emi_hz: float = 1000.0              # lock-in modulation / mains harmonics
    pressure_counts: float = 20.0       # enclosure / sheet-contact pressure (minor)

    @classmethod
    def lab(cls) -> "NoiseEnvironment":
        """A typical uncharacterised lab bench (the defaults)."""
        return cls()

    @classmethod
    def quiet_lab(cls) -> "NoiseEnvironment":
        """A stabilised enclosure: servoed T, isolated table, shielded box."""
        return cls(temp_drift_counts=10.0, vibration_counts=30.0,
                   emi_counts=20.0, pressure_counts=5.0)

    @classmethod
    def noisy_lab(cls) -> "NoiseEnvironment":
        """A harsh bench: HVAC draught, vacuum pump vibration, unshielded."""
        return cls(temp_drift_counts=120.0, vibration_counts=400.0,
                   emi_counts=250.0, pressure_counts=60.0)

    def to_dict(self) -> dict:
        return asdict(self)


def noise_environment_preset(name: Optional[str]) -> Optional[NoiseEnvironment]:
    """Resolve a preset name to a :class:`NoiseEnvironment` (``None`` for off).

    ``"lab"`` (default), ``"quiet"``, ``"noisy"``; ``"none"``/``""`` -> ``None``
    so no environmental channels are added (bit-identical to the prior model).
    """
    # None -> "lab" (the documented default); "" -> off (no env, back-compat).
    n = ("lab" if name is None else name).strip().lower()
    if n in ("none", "off", ""):
        return None
    if n == "quiet":
        return NoiseEnvironment.quiet_lab()
    if n == "noisy":
        return NoiseEnvironment.noisy_lab()
    return NoiseEnvironment.lab()


# ---------------------------------------------------------------------------
# The filter stack
# ---------------------------------------------------------------------------
@dataclass
class AntiNoiseConfig:
    """The classical anti-noise filter stack.

    Each field is an attenuation on its matching environmental channel.  The
    defaults are all **OFF** (gain 0, residual 1, no feed-forward, no DD) so an
    unset / empty config is bit-identical to the prior noise model (back-compat).
    Everything is 300 K and host-side or passive -- no cryo, no new RTL, no PCB
    respin.  The stack composes multiplicatively per channel and additively (in
    quadrature) across channels with the ``1/sqrt(N)`` readout floor.
    """

    thermal_servo_gain: float = 0.0        # active T servo: temp_drift *= 1/(1+gain)
    vibration_iso_residual: float = 1.0    # mechanical isolation residual (1=none, 0.1=good)
    emi_shield_residual: float = 1.0       # mu-metal + twisted-pair residual (1=none, 0.05=good)
    feed_forward_channels: tuple[str, ...] = field(default_factory=tuple)
    # sensor channels measured and subtracted ("thermal","current","compass",...)
    feed_forward_rejection: float = 0.0    # correlated-drift cancellation fraction (0=off, 0.9=good)
    common_mode_reject: bool = False       # differential across-pad slow-drift rejection
    dd_sequence: str = "none"              # "none"|"hahn"|"cpmg"|"xy8" (extends T2, see effective_t2)
    dd_n_blocks: int = 1                   # number of refocusing blocks / pi pulses

    def to_dict(self) -> dict:
        d = asdict(self)
        # tuples serialize as lists for JSON
        d["feed_forward_channels"] = list(self.feed_forward_channels)
        return d

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "AntiNoiseConfig":
        if not d:
            return cls()
        return cls(
            thermal_servo_gain=float(d.get("thermal_servo_gain", 0.0)),
            vibration_iso_residual=float(d.get("vibration_iso_residual", 1.0)),
            emi_shield_residual=float(d.get("emi_shield_residual", 1.0)),
            feed_forward_channels=tuple(d.get("feed_forward_channels", []) or ()),
            feed_forward_rejection=float(d.get("feed_forward_rejection", 0.0)),
            common_mode_reject=bool(d.get("common_mode_reject", False)),
            dd_sequence=str(d.get("dd_sequence", "none")),
            dd_n_blocks=int(d.get("dd_n_blocks", 1)),
        )

    def is_default(self) -> bool:
        """True when every filter is OFF (env channels pass through unattenuated
        and no DD) -- the caller can skip the stack and use the bare floor."""
        return (self.thermal_servo_gain == 0.0
                and self.vibration_iso_residual == 1.0
                and self.emi_shield_residual == 1.0
                and self.feed_forward_rejection == 0.0
                and not self.common_mode_reject
                and (self.dd_sequence or "none") == "none")


# ---------------------------------------------------------------------------
# Effective noise sigma (composes the stack on the readout floor)
# ---------------------------------------------------------------------------
def effective_noise_sigma(base_counts: float,
                          env: Optional[NoiseEnvironment],
                          filters: Optional[AntiNoiseConfig],
                          integration_shots: int = 1) -> float:
    """Effective per-point noise sigma (ADC counts) after the filter stack.

    The uncorrelated Johnson/readout floor (``base_counts``) averages down by
    ``1/sqrt(N)``.  The **fast** environmental channels (vibration,
    EMI) are also uncorrelated shot-to-shot at the lock-in cadence, so they
    average down by ``1/sqrt(N)`` too, on top of their iso/shield attenuation.
    The **slow** channels (temperature drift, pressure) are correlated across
    shots and are NOT reduced by ``1/sqrt(N)`` -- the thermal servo, feed-forward
    and common-mode rejection are the levers for them (integration alone cannot
    remove a slow drift).  This is why the servo and integration are
    complementary, not redundant.

    Returns the total sigma in ADC counts (same axis as ``base_counts``).  When
    ``env`` is ``None`` (no environmental model), collapses to
    ``base_counts / sqrt(N)`` (bit-identical to the prior model).  When ``env``
    is set but ``filters`` is ``None`` / all-off, the environmental channels pass
    through **unattenuated** (a noisy lab with no filters = the full noise) --
    only an explicit filter attenuates its channel.
    """
    n = max(1, int(integration_shots))
    sqrt_n = math.sqrt(n)
    floor = base_counts / sqrt_n
    if env is None:
        return floor
    if filters is None:
        filters = AntiNoiseConfig()  # pass-through: every channel at full strength

    ff = max(0.0, min(1.0, filters.feed_forward_rejection))
    keep_ff = 1.0 - ff                 # fraction of correlated drift remaining after feed-forward
    cm = 0.5 if filters.common_mode_reject else 1.0   # differential halves common-mode drift

    # slow, correlated across shots -> NOT divided by sqrt(N)
    temp = env.temp_drift_counts / (1.0 + max(0.0, filters.thermal_servo_gain))
    temp *= keep_ff * cm
    pressure = env.pressure_counts * keep_ff * cm

    # Vibration (dominant band ``env.vibration_hz``, ~30 Hz -> ~33 ms period)
    # is correlated over one vibration period. At the lock-in cadence
    # (``env.emi_hz``, ~1 kHz -> 1 ms/shot) an integration shorter than one
    # vibration period (~33 shots) sees CORRELATED per-shot vibration, so it
    # does NOT average down by 1/sqrt(N) -- treat it as correlated (like
    # temp/pressure) below that cutoff. EMI is at the modulation band itself,
    # well above the vibration band, so it stays uncorrelated shot-to-shot
    # (audit #138).
    vib_raw = env.vibration_counts * max(0.0, filters.vibration_iso_residual) * keep_ff
    vib_period_s = 1.0 / max(env.vibration_hz, 1e-3)
    shot_period_s = 1.0 / max(env.emi_hz, 1e-3)
    vib = vib_raw if n * shot_period_s < vib_period_s else vib_raw / sqrt_n
    emi = env.emi_counts * max(0.0, filters.emi_shield_residual) * keep_ff / sqrt_n

    return math.sqrt(floor * floor + temp * temp + pressure * pressure
                     + vib * vib + emi * emi)


def filter_gain_breakdown(env: Optional[NoiseEnvironment],
                          filters: Optional[AntiNoiseConfig]) -> dict:
    """Per-channel residual fraction (0 = fully rejected, 1 = unfiltered) for
    display in the noise budget / frontend.  Empty when no environment is set;
    with an environment and all-off filters, every channel reads 1.0 (pass-
    through)."""
    if env is None:
        return {}
    if filters is None:
        filters = AntiNoiseConfig()
    ff = max(0.0, min(1.0, filters.feed_forward_rejection))
    keep_ff = 1.0 - ff
    cm = 0.5 if filters.common_mode_reject else 1.0
    return {
        "temperature": round(
            (1.0 / (1.0 + max(0.0, filters.thermal_servo_gain))) * keep_ff * cm, 4),
        "vibration": round(max(0.0, filters.vibration_iso_residual) * keep_ff, 4),
        "emi": round(max(0.0, filters.emi_shield_residual) * keep_ff, 4),
        "pressure": round(keep_ff * cm, 4),
        "feed_forward": round(ff, 4),
        "dd_sequence": filters.dd_sequence,
    }


# ---------------------------------------------------------------------------
# Dynamical decoupling (extends T2 by refocusing low-frequency dephasing)
# ---------------------------------------------------------------------------
def effective_t2(base_t2_s: float, dd_sequence: str, n_blocks: int,
                 t1_s: Optional[float]) -> float:
    """Effective T2 under a dynamical-decoupling sequence.

    DD refocuses **low-frequency** dephasing (the T2* -> T2 recovery): a train of
    pi pulses swaps the slow noise accumulation, so the coherence lifetime grows
    as ``hahn ~ 2x``, ``cpmg ~ 2*n``, ``xy8 ~ 2^n`` (the existing XY8 model).

    DD **cannot** refocus energy relaxation (T1) or fast Markovian noise -- those
    are irreversible and set the hard cap.  The cap is ``2*T1`` (the relaxation-
    limited bound used by :func:`qssc.defects.coherence_at_temperature`), so the
    returned T2 never exceeds ``2*T1`` however many pulses are applied.  This is
    the physical reason DD is a 300 K lever for dephasing but NOT for the magnon
    thermal bath (which causes T1 relaxation at the qubit frequency).

    ``dd_sequence="none"`` or ``n_blocks<=0`` -> ``base_t2_s`` unchanged.
    """
    seq = (dd_sequence or "none").strip().lower()
    if seq == "none" or n_blocks <= 0:
        return base_t2_s
    if seq == "hahn":
        ext = base_t2_s * 2.0
    elif seq == "cpmg":
        ext = base_t2_s * 2.0 * int(n_blocks)
    elif seq == "xy8":
        ext = base_t2_s * (2.0 ** int(n_blocks))
    else:
        return base_t2_s
    if t1_s and t1_s > 0:
        ext = min(ext, 2.0 * t1_s)
    return ext


def hahn_echo() -> list[str]:
    """Hahn echo pulse labels: a single refocusing pi pulse (XY axis pair kept
    simple for the label stream; the pulse engine expands phases)."""
    return ["X", "Y", "X"]


def cpmg_decoupling(n_blocks: int = 1) -> list[str]:
    """CPMG pulse labels: ``n_blocks`` pi pulses on the Y axis (equally spaced)."""
    return ["Y"] * max(1, int(n_blocks))