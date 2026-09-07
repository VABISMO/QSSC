# qssc/simulator.py
"""Physics + hardware simulator for the Quantum Solid State Chip (QSSC).

This module is the room-temperature software twin of the physical chip.  It
models the *on-chip* readout path the fabrication protocol actually describes:

* **64 electrically-pumped CQD ASE/LED emitters** (green ~520-540 nm), each on
  its own TLC5947 constant-current channel -- driven **in parallel** (multiple
  emitters light simultaneously at independent 12-bit grayscale levels),
* **64 graphene/MoS2 photodetector electrodes** (one per site), each with its
  own TIA, digitised **in parallel** by 4x AD7616 16-bit simultaneous-sampling
  ADCs -- no analog MUX, no serial scan, no XADC, and
* the spin-defect Hamiltonian (ODMR with hyperfine structure) + the
  dynamical-decoupling toolbox.

There is **no camera and no VCSEL/galvo microscopy rig** -- the chip reads out
directly on-chip.  A software pulse-capture sweep (drive each emitter channel,
sample every detector electrode, find the modulated one) builds the
emitter-channel -> qubit-site -> detector-electrode lookup table; that is the
calibration flow, not image-based defect detection.

The wire protocol mirrors ``fpga/rtl/command_parser.v`` exactly so the *same*
Python control code drives the simulator and the real Artix-7 board:
``DRIVE_EMITTER <index> <ms>``, ``SET_EMITTER <index> <level>``,
``DRIVE_PATTERN <ms>``, ``CLEAR_EMITTERS``, ``READ_DETECTOR <site>``,
``READ_DETECTORS``, ``CLEAR_DETECTORS``, ``APPLY_PULSE ...``, ``CALIBRATE``.

Everything is parametrised by a :class:`~qssc.defects.DefectProfile`.  The
default profile is **NV-diamond** (the only defect family here that is
compute-capable at 300 K).

NumPy is the only hard requirement; the readout model is pure NumPy.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

import numpy as np

from .defects import DefectProfile, get_profile, coherence_at_temperature

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# On-chip readout constants (CQD ASE emitter + graphene/MoS2 detector + lock-in)
# ---------------------------------------------------------------------------
# 64 parallel channels: 64 emitters (3x TLC5947) + 64 detectors (4x AD7616).
# The die is a 128-pad part -- 64 emitter pads on the bottom edge, 64 detector
# pads on the top edge -- addressed by a single linear index 0..63 (no 8x8
# row/col crossbar; every channel is independent and simultaneous).
N_SITES = 64
# The AD7616 is a 16-bit bipolar +/-5V ADC. Detector samples are 16-bit
# (0..65535), the digitised TIA output -- NOT 12-bit XADC codes. The 1 kHz
# lock-in demodulation that turns these raw samples into a PL/ODMR signal is
# done host-side (mod_sync is the reference); the FPGA delivers raw samples.
ADC_BITS = 16
ADC_MAX = (1 << ADC_BITS) - 1            # 65535
# Each detector electrode sits under one emitter channel and reads a *small
# ensemble* of defects (not a single isolated spin), so the on-resonance PL dip
# is the ensemble ODMR contrast, not the large single-NV spin-readout contrast.
# Real NV-ensemble ODMR at 300 K is 3-8%; 5% is the representative mid-range and matches
# the CW spectrum model (simulate_odmr, contrast_depth=0.03, same order).
ODMR_CONTRAST = 0.05                      # ensemble ODMR dip fraction at resonance
ODMR_LINEWIDTH_HZ = 5.0e6                 # ODMR linewidth (~5 MHz; matches simulate_odmr)
# Bright/dark/background are 16-bit AD7616 codes, scaled from the original
# 12-bit XADC values by *16 (3200->51200, 3040->48640, 100->1600) so the
# fractional saturation, ODMR dip fraction and lock-in SNR are preserved.
BRIGHT_CODE = 51200                       # pumped ensemble, spin ms=0 (bright) -> full PL
DARK_CODE = 48640                         # pumped ensemble at resonance -> BRIGHT*(1-ODMR_CONTRAST)
BG_CODE = 1600                            # unpumped electrode -> detector background floor
# Post-lock-in photocurrent noise (ADC counts). The 1 kHz lock-in averages many
# MW-modulation cycles, suppressing broadband noise so the small 5% dip (~2560
# counts) is resolved at ~4 sigma in a single read. The per-qubit *quantum*
# readout error (profile.readout_error, ~8% for NV) is the spin
# initialisation/projection imperfection, injected separately in the Qiskit
# noise model -- it is decoupled from this detector SNR, as on real hardware.
READOUT_NOISE = 640                       # std dev of lock-in photocurrent noise (16-bit counts)
ASE_GAIN = 1.15                            # ASE concentrates the CQD emission into the waveguide
LOCKIN_MOD_HZ = 1000.0                    # MW modulation frequency for the lock-in readout
DETECT_THRESHOLD = 16000                  # lock-in amplitude above which a site counts as coupled
SITE_FAB_YIELD = 0.9                      # fraction of channels the fabrication step wires correctly


# ---------------------------------------------------------------------------
# Simulated FPGA connection (mirrors the UART protocol of the real firmware)
# ---------------------------------------------------------------------------

class SimulatedFPGA:
    """In-process stand-in for the Artix-7 control board.

    Understands the same text commands as ``fpga/rtl/command_parser.v`` -- the
    parallel on-chip readout protocol (no camera, no analog MUX):

    * ``DRIVE_EMITTER <index> <duration_ms>``  -- pump one CQD emitter at full
    * ``SET_EMITTER <index> <level 0..4095>``  -- arm one emitter's grayscale
    * ``DRIVE_PATTERN <duration_ms>``          -- fire ALL armed emitters in parallel
    * ``CLEAR_EMITTERS``                       -- disarm all emitters (gs=0)
    * ``APPLY_PULSE <qubit> <freq_khz> <amp> <duration_ns> <phase>``  -- program the ADF4351
    * ``READ_DETECTOR <site>``  -- read one detector's 16-bit AD7616 intensity sample
    * ``READ_DETECTORS``       -- stream all 64 detectors' 16-bit samples in parallel
    * ``CLEAR_DETECTORS``      -- no-op on the ADC (no counters), replies OK
    * ``CALIBRATE``            -- run the emitter->site->detector sweep, build the LUT

    The ``APPLY_PULSE`` fields are **decimal integers** matching the firmware
    parser and ``hardware/HARDWARE_DESIGN.md`` §5: ``freq_khz`` in kHz,
    ``amp`` 0..3, ``phase`` in milliradians. Keeping the wire format identical
    means the same Python control code drives the simulator and the real
    hardware -- :class:`~qssc.backend.MicrowaveController` emits exactly this
    integer form.

    Parallelism: ``SET_EMITTER`` arms per-channel 12-bit grayscale (the TLC5947
    holds the level), and ``DRIVE_PATTERN`` fires every armed channel
    *simultaneously* -- multiple emitters light at once at independent
    intensities, exactly as the 3x TLC5947 constant-current chain does on the
    real board. ``READ_DETECTORS`` returns all 64 AD7616 samples in one reply
    (the 4 ADCs sample simultaneously). There is no 8x8 row/col crossbar and no
    serial pixel-by-pixel scan.

    Readout physics: a pumped site in the bright ms=0 spin state (state 0)
    reports a high detector photocurrent; a resonant microwave pulse (X gate,
    phase ~= pi) flips it to the dark ms=+-1 state (state 1) and the
    photocurrent drops -- that dip *is* the ODMR signal the lock-in recovers.
    """

    N_SITES = N_SITES

    def __init__(self, profile: DefectProfile | str = "NV",
                 temperature_k: float = 300.0, noise: float = 0.1,
                 seed: Optional[int] = None):
        self.profile = get_profile(profile) if isinstance(profile, str) else profile
        self.temperature_k = temperature_k
        self.noise = noise
        self.rng = np.random.default_rng(seed)
        # per-channel 12-bit grayscale (TLC5947 gs register); 0 = off. A channel
        # "pumps" its coupled detector when gs > 0 (DRIVE_EMITTER sets full,
        # SET_EMITTER arms an arbitrary level, DRIVE_PATTERN fires all armed).
        self.emitter_gs: Dict[int, int] = {}
        self.sim_states: Dict[int, int] = {}
        self.sim_freq = self.profile.zero_field_splitting
        self.last_mw_freq_khz: Optional[int] = None
        # emitter channel index -> detector electrode index, discovered by CALIBRATE
        self.lut: Dict[int, int] = {}
        t1, t2 = coherence_at_temperature(self.profile, temperature_k)
        self.t1, self.t2 = t1, t2

    # -- command interface -------------------------------------------------
    def send_command(self, command: str) -> str:
        parts = command.split()
        head = parts[0]
        try:
            if head == "DRIVE_EMITTER":
                # DRIVE_EMITTER <index> <duration_ms>: pump ONE emitter at full
                # (gs=4095) for duration_ms. Matches the firmware emitter_array
                # cmd_drive: only the addressed channel is lit during the pump
                # window (every other channel is off), and the channel is cleared
                # again when the window ends. The host reads the detector during
                # that window, so model this as a single-channel pump that
                # REPLACES the active set (not additive). For parallel multi-
                # emitter firing use SET_EMITTER + DRIVE_PATTERN instead.
                index = int(parts[1])
                if not (0 <= index < self.N_SITES):
                    return "ERR"
                if len(parts) > 2 and int(parts[2]) < 0:
                    return "ERR"
                self.emitter_gs = {index: 4095}
                # optical pump polarises the NV spin into the bright ms=0 state (0)
                self.sim_states[index] = 0
                return "OK"
            if head == "SET_EMITTER":
                # SET_EMITTER <index> <level 0..4095>: arm one channel's grayscale.
                index = int(parts[1])
                level = int(parts[2])
                if not (0 <= index < self.N_SITES) or not (0 <= level <= 4095):
                    return "ERR"
                self.emitter_gs[index] = level
                if level > 0:
                    self.sim_states.setdefault(index, 0)
                return "OK"
            if head == "DRIVE_PATTERN":
                # DRIVE_PATTERN <duration_ms>: fire every armed (gs>0) channel in
                # parallel. The gs registers are already set; this just gates the
                # pump on. Negative duration is invalid.
                if len(parts) > 1 and int(parts[1]) < 0:
                    return "ERR"
                return "OK"
            if head == "CLEAR_EMITTERS":
                # Disarm all channels (gs=0) -- matches the TLC5947 all-zero load.
                self.emitter_gs = {}
                return "OK"
            if head == "APPLY_PULSE":
                # Canonical wire format (matches command_parser.v and
                # HARDWARE_DESIGN.md §5): five decimal integers.
                #   APPLY_PULSE <qubit> <freq_khz> <amp> <duration_ns> <phase>
                # freq_khz -> GHz, phase (milliradians) -> radians for the
                # physics model below.
                qubit = int(parts[1])
                freq_khz = int(parts[2])
                amp = int(parts[3])
                dur = int(parts[4])
                phase_mrad = int(parts[5])
                freq_ghz = freq_khz / 1e6
                phase_rad = phase_mrad / 1000.0
                self.last_mw_freq_khz = freq_khz
                return self._apply_pulse(qubit, freq_ghz, amp, dur, phase_rad)
            if head == "READ_DETECTOR":
                site = int(parts[1])
                if not (0 <= site < self.N_SITES):
                    return "ERR"
                return str(self._photocurrent_code(site))
            if head == "READ_DETECTORS":
                codes = [self._photocurrent_code(q) for q in range(self.N_SITES)]
                return " ".join(str(c) for c in codes)
            if head == "CLEAR_DETECTORS":
                # No-op on the ADC (no counters to zero) -- matches command_parser.v.
                return "OK"
            if head == "CALIBRATE":
                self._calibrate()
                return "OK"
            if head == "READ_RNG":
                # READ_RNG <n_bytes>: harvest n_bytes of von-Neumann-debiased
                # entropy from the AD7616 LSB noise floor (a dark detector
                # channel). Mirrors fpga/rtl/qrng_entropy.v: successive ADC
                # LSBs are paired, 01->0 / 10->1 kept, 00/11 discarded, packed
                # MSB-first. The raw LSB stream is mildly biased (p1 ~ 0.52,
                # thermal + shot noise) so the extractor's work shows up in the
                # returned stats. Reply: "<hex> <raw_bits> <kept> <extract_rate>".
                n_bytes = int(parts[1]) if len(parts) > 1 else 32
                if n_bytes <= 0 or n_bytes > 4096:
                    return "ERR"
                return self._harvest_entropy(n_bytes)
            return "ERR"
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("sim command failed: %s (%s)", command, exc)
            return "ERR"

    def _apply_pulse(self, qubit, freq_ghz, amp, dur, phase_rad) -> str:
        # Reject if the drive is outside the ODMR linewidth of D (the pulse
        # can only flip the spin within ~one linewidth). freq_ghz is GHz;
        # self.sim_freq (D) is Hz. The old window was noise*D = 287 MHz, far
        # wider than the real ~5 MHz NV ODMR line -- a pulse 200 MHz off would
        # wrongly have flipped the spin. self.noise is reserved for the
        # stochastic RZ/RY gate error below, not the resonance acceptance.
        if abs(freq_ghz * 1e9 - self.sim_freq) > self.profile.odmr_linewidth_hz:
            return "ERR"
        # amp is the ADF4351 output-power index 0..3 (HARDWARE_DESIGN.md §5).
        # Reject an out-of-range raw-UART value even though backend.apply_pulse
        # already clamps it -- defense in depth for the wire protocol.
        if not 0 <= amp <= 3:
            return "ERR"
        q = qubit
        self.sim_states.setdefault(q, 0)
        # phase arrives in radians (converted from integer milliradians on the
        # wire). Tolerance ~1 mrad matches the integer-mrad quantisation.
        if abs(phase_rad - math.pi) < 1e-3:          # X gate (phase ~= pi) flips the spin
            self.sim_states[q] = 1 - self.sim_states[q]
        elif abs(phase_rad) > 1e-3:                  # RZ/RY: stochastic error
            if self.rng.random() < self.noise:
                self.sim_states[q] = 1 - self.sim_states[q]
        return "OK"

    def _photocurrent_code(self, site: int) -> int:
        """16-bit AD7616 sample of one detector's lock-in photocurrent."""
        if self.emitter_gs.get(site, 0) <= 0:
            level = BG_CODE                       # no pump -> no PL, background floor
        else:
            state = self.sim_states.get(site, 0)
            # bright ms=0 (state 0) -> high PL; dark ms=+-1 (state 1) -> ODMR dip
            level = BRIGHT_CODE if state == 0 else DARK_CODE
        code = level + int(self.rng.normal(0, READOUT_NOISE))
        return int(min(max(code, 0), ADC_MAX))

    def _calibrate(self) -> None:
        """Emitter->detector sweep: drive each channel, read all electrodes,
        pick the modulated one. On this 1:1-laid-out sim the mapping is the
        identity, but the sweep is the *method* by which the software discovers
        it without an alignment jig (protocol §5.6)."""
        self.lut = {}
        saved_gs = dict(self.emitter_gs)
        try:
            for index in range(self.N_SITES):
                self.emitter_gs = {index: 4095}
                self.sim_states[index] = 0
                codes = [self._photocurrent_code(q) for q in range(self.N_SITES)]
                best = int(np.argmax(codes))
                if codes[best] > DETECT_THRESHOLD:
                    self.lut[index] = best
        finally:
            self.emitter_gs = saved_gs

    def close(self):  # pragma: no cover
        pass

    def _harvest_entropy(self, n_bytes: int) -> str:
        """Von-Neumann-debiased ADC LSB noise -> n_bytes hex + extraction stats.

        Models ``fpga/rtl/qrng_entropy.v`` in software: the LSB of a dark
        AD7616 channel is a biased but independent bit stream (thermal + shot
        noise, p1 ~ 0.52); pairing successive bits and keeping only 01 / 10
        removes the bias. Returns ``"<hex> <raw_bits> <kept> <extract_rate>"``.
        """
        from .crypto import von_neumann_extract, bits_to_bytes
        need_bits = n_bytes * 8
        p1 = 0.52
        # von-Neumann keeps ~2*p1*(1-p1) of pairs (~50% for p1=0.52), i.e.
        # ~25% of raw LSBs, so ~6x raw is comfortably enough for one batch.
        bits: list[int] = []
        raw_total = 0
        kept_total = 0
        while len(bits) < need_bits:
            raw = [1 if self.rng.random() < p1 else 0 for _ in range(need_bits * 6)]
            # huge `need` so the extractor processes every pair (no early stop)
            more, _st = von_neumann_extract(raw, 1 << 30)
            raw_total += len(raw)
            kept_total += len(more)
            bits.extend(more)
        out = bits_to_bytes(bits[:need_bits])
        rate = (kept_total / raw_total) if raw_total else 0.0
        return f"{out.hex()} {raw_total} {kept_total} {round(rate, 4)}"


# ---------------------------------------------------------------------------
# On-chip readout primitives
# ---------------------------------------------------------------------------

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


@dataclass
class QubitSite:
    qubit_id: int
    row: int
    col: int
    x_um: float
    y_um: float
    intensity: float
    t1: float          # s
    t2: float          # s
    readout_err: float
    usable: bool = True

    def as_dict(self) -> dict:
        d = asdict(self)
        d["t1_ms"] = self.t1 * 1e3
        d["t2_us"] = self.t2 * 1e6
        return d


def auto_align(profile: DefectProfile | str = "NV",
                grid: int = 8, site_pitch_um: float = 2.5,
                temperature_k: float = 300.0, max_retries: int = 5,
                rng: Optional[np.random.Generator] = None,
                progress=None) -> List[QubitSite]:
    """Calibrate the chip and register ``grid**2`` qubit sites.

    Mirrors the on-chip calibration flow of the real hardware (protocol §5.6):
    drive each CQD emitter pixel, sample every graphene detector electrode, and
    register the electrode that responds above the lock-in threshold -- the
    emitter-pixel -> qubit-site -> detector-electrode LUT. No camera, no
    micron-alignment jig: the software discovers the mapping purely from the
    lock-in photocurrent response, driven by the simulator physics so it is
    deterministic and fast.

    ``site_pitch_um`` is the centre-to-centre pitch of one emitter/detector
    pixel. A site is registered as usable when it actually contains defects
    *and* the fabrication step succeeded: the per-site yield is
    ``SITE_FAB_YIELD * (1 - exp(-density * site_area))`` -- a Poisson "at least
    one defect in the pixel" factor times a fabrication-yield factor. This
    replaces the old ``galvo_area`` camera-era leftover, whose
    ``density * galvo_area`` always clipped to 1.0 and made the
    ``usable=False`` branch dead. With the Poisson model the not-usable branch
    is live: e.g. at 300 K an NV site (5/um^2 over 6.25 um^2) is ~always
    populated but still loses ~10% to fab yield, while a sparse hBN V_B- site
    (0.30/um^2) is empty ~15% of the time.

    Returns ``grid**2`` :class:`QubitSite` in index order; sites that fail the
    yield draw are kept as ``usable=False`` placeholders so the grid is always
    complete.
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    rng = rng or np.random.default_rng()
    t1, t2 = coherence_at_temperature(prof, temperature_k)
    n_needed = grid * grid
    site_area_um2 = site_pitch_um * site_pitch_um
    # Poisson ">=1 defect in the pixel" times a fabrication-yield factor.
    p_defect = 1.0 - math.exp(-prof.defect_density_per_um2 * site_area_um2)
    p_usable = float(np.clip(SITE_FAB_YIELD * p_defect, 0.0, 1.0))
    task = None
    if progress is not None:
        task = progress.add_task("Scanning on-chip detector array...", total=max_retries)
    sites: List[QubitSite] = []
    for idx in range(n_needed):
        r, c = divmod(idx, grid)
        coupled = rng.random() < p_usable
        if coupled:
            code = int(make_detector_response({idx}, prof, rng=rng, n_sites=n_needed)[idx])
            # readout error anchored to the profile nominal value, mildly modulated
            # by site brightness (brighter emitters read out a little more
            # reliably). Bounded 0.7x..1.3x nominal so a dim site can never
            # collapse the error far below the physical readout floor.
            bright = float(np.clip((code - BG_CODE) / max(BRIGHT_CODE - BG_CODE, 1), 0.0, 1.0))
            err = prof.readout_error * (1.3 - 0.6 * bright)
            x_um = (c + 0.5) * site_pitch_um
            y_um = (r + 0.5) * site_pitch_um
            sites.append(QubitSite(
                qubit_id=idx, row=r, col=c,
                x_um=x_um, y_um=y_um, intensity=float(code),
                t1=t1, t2=t2, readout_err=err,
                usable=code > DETECT_THRESHOLD and t2 > 1e-6,
            ))
        else:
            sites.append(QubitSite(qubit_id=idx, row=r, col=c,
                                   x_um=0.0, y_um=0.0, intensity=0.0,
                                   t1=t1, t2=t2, readout_err=prof.readout_error,
                                   usable=False))
    if progress is not None and task is not None:
        progress.update(task, completed=max_retries)
    return sites[:n_needed]


# ---------------------------------------------------------------------------
# ODMR and coherence
# ---------------------------------------------------------------------------

def simulate_odmr(profile: DefectProfile | str = "NV",
                  freqs_ghz: Optional[np.ndarray] = None,
                  b_field_t: float = 1e-3, linewidth_mhz: float = 5.0,
                  contrast_depth: float = 0.03) -> Tuple[np.ndarray, np.ndarray]:
    """Analytic ODMR spectrum with hyperfine replicas.

    Builds the dip pattern for a spin-1 defect coupled to ``n_equivalent_nuclei``
    equivalent nuclei of spin ``nuclear_spin``.  The hyperfine fingerprint is the
    n-fold convolution of the single-nucleus line set: each nucleus contributes
    ``2I+1`` sublevels at ``A*mi + Q*mi**2``, so n equivalent nuclei produce
    ``(2I+1)**n`` (weighted, often degenerate) lines.

    * V_B- in hBN: 3 equivalent 14N (I=1) -> the characteristic **7-line** pattern
      (degeneracies 1:3:6:7:6:3:1, spacing A/h ~ 47 MHz).  This is the V_B-
      fingerprint (Gottscholl 2020).
    * NV in diamond: 1 14N -> 3 lines (current behaviour, preserved).
    * V_Si in SiC: 1 29Si (I=1/2) -> 2 lines.

    The two electron spin transitions (ms=0 <-> +-1) are Zeeman-split by gamma*B.
    Returns frequencies (GHz) and contrast (0..1, dips), with the deepest line at
    ``contrast_depth``.
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    if freqs_ghz is None:
        f0 = prof.d_ghz
        freqs_ghz = np.linspace(f0 - 0.08, f0 + 0.08, 400)
    D = prof.zero_field_splitting
    ge = prof.gyromagnetic_ratio
    A = prof.hyperfine_coupling
    Q = prof.quadrupole_splitting
    I = prof.nuclear_spin
    n = max(1, int(getattr(prof, "n_equivalent_nuclei", 1)))
    # two electron transitions shifted by the Zeeman term
    transitions = [D + ge * b_field_t, D - ge * b_field_t]

    # single-nucleus sublevels m_i in {-I, -I+1, ..., I}
    n_sub = int(round(2 * I)) + 1
    sub = [-I + k for k in range(n_sub)]

    # build the n-fold convolution of hyperfine offsets; cap the combinatorial
    # blow-up (our profiles use n<=3 -> <=27 combos, well within the guard).
    n_combos = n_sub ** n
    if n_combos > 512:  # pragma: no cover - defensive, no profile hits this
        offsets = [A * mi + Q * mi * mi for mi in sub]
    else:
        from itertools import product as _product
        offsets = []
        for combo in _product(sub, repeat=n):
            m_tot = sum(combo)
            q_tot = sum(mi * mi for mi in combo)
            offsets.append(A * m_tot + Q * q_tot)
    # degeneracy of the most-populated line (amplitude normalisation so the
    # deepest dip == contrast_depth and relative intensities are correct,
    # e.g. 1:3:6:7:6:3:1 for V_B-).
    from collections import Counter as _Counter
    _c = _Counter(round(o, 3) for o in offsets)
    d_max = max(_c.values()) if _c else 1

    contrast = np.ones_like(freqs_ghz)
    sigma = linewidth_mhz * 1e6
    f_hz = freqs_ghz * 1e9
    for ft in transitions:
        for off in offsets:
            centre = ft + off
            amp = contrast_depth / d_max
            dip = amp * np.exp(-((f_hz - centre) ** 2) / (2 * sigma ** 2))
            contrast = contrast - dip
    contrast = np.clip(contrast, 0.0, 1.0)
    return freqs_ghz, contrast


def xy8_decoupling(n_blocks: int = 1) -> List[str]:
    """Return the XY8 pulse sequence (8 pulses per block) as gate labels."""
    block = ["X", "Y", "X", "Y", "Y", "X", "Y", "X"]
    return block * max(1, int(n_blocks))


def estimate_coherence_xy8(t2: float, n_blocks: int = 1, tau_s: float = 1e-6) -> float:
    """Toy estimator of the extended coherence under XY8 (exponential gain)."""
    return t2 * (2 ** n_blocks) * (1 - math.exp(-tau_s / max(t2, 1e-12)))