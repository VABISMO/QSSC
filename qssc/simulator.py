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
from typing import Dict, List, Optional

import numpy as np

from .defects import DefectProfile, get_profile, coherence_at_temperature
from .noise_filters import (
    AntiNoiseConfig,
    NoiseEnvironment,
    effective_noise_sigma,
    effective_t2,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# On-chip readout constants (CQD ASE emitter + graphene/MoS2 detector + lock-in)
# ---------------------------------------------------------------------------
# Defined in .simulator_constants (split out so simulator_response /
# simulator_site can import them without a cycle back into this module).
# Re-exported here so `from qssc.simulator import N_SITES` etc. keep resolving.
from .simulator_constants import (  # noqa: F401
    ADC_BITS, ADC_MAX, ASE_GAIN, BG_CODE, BRIGHT_CODE, DARK_CODE,
    DETECT_THRESHOLD, LOCKIN_MOD_HZ, N_NV_PER_PIXEL, N_SITES,
    ODMR_CONTRAST, ODMR_LINEWIDTH_HZ, READOUT_NOISE, SITE_FAB_YIELD,
)

# ---------------------------------------------------------------------------
# On-chip readout primitives + qubit-site calibration + ODMR model
# ---------------------------------------------------------------------------
# Split into simulator_response (CQD pulse / detector response / XY8) and
# simulator_site (QubitSite / auto_align / simulate_odmr). Re-exported here so
# `from qssc.simulator import X` keeps resolving for every consumer.
from .simulator_response import (  # noqa: F401
    estimate_coherence_xy8, make_detector_response, simulate_cqd_pulse,
    xy8_decoupling,
)
from .simulator_site import (  # noqa: F401
    QubitSite, auto_align, resolvable_register_width, simulate_odmr,
)


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
                 seed: Optional[int] = None,
                 sub_pixel=None,
                 integration_shots: int = 1,
                 gradient_mhz_per_um: float = 1.0,
                 pixel_size_um: float = 200.0,
                 max_nv_per_microdiamond: int = 300,
                 contrast_single: float = 0.03,
                 noise_env: Optional[NoiseEnvironment] = None,
                 anti_noise: Optional[AntiNoiseConfig] = None):
        self.profile = get_profile(profile) if isinstance(profile, str) else profile
        self.temperature_k = temperature_k
        self.noise = noise
        self.rng = np.random.default_rng(seed)
        # Optional charge-sharing centroiding model. When set,
        # _run_all_odmr spreads each pixel's ODMR dip to neighbouring pads via
        # the sheet's charge-share kernel (a real spatial image per frequency
        # point, instead of 64 independent channels). Lazy-typed to avoid a
        # circular import with qssc.detector_centroid (which imports simulator
        # constants). See qssc.detector_centroid.SubPixelModel.
        self.sub_pixel = sub_pixel
        # N-shot lock-in averaging -> per-point noise drops by
        # sqrt(N) (uncorrelated thermal/Johnson readout noise). 1 = no averaging.
        self.integration_shots = max(1, int(integration_shots))
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
        # Classical anti-noise filter stack. ``noise_env`` models the
        # environmental channels (temp drift, vibration, EMI, pressure) and
        # ``anti_noise`` the filter stack that attenuates them (thermal servo,
        # vibration iso, EMI shield, sensor feed-forward, common-mode rejection)
        # plus an optional dynamical-decoupling sequence. DD refocuses low-
        # frequency dephasing (T2*) -> extends T2 (capped at 2*T1, the relaxation
        # limit; DD cannot refocus T1 / fast Markovian noise). Defaults (None) ->
        # unchanged T2 and unchanged readout noise (bit-identical to the prior
        # model); see qssc.noise_filters.
        self.noise_env = noise_env
        self.anti_noise = anti_noise
        if anti_noise is not None and (anti_noise.dd_sequence or "none") != "none":
            self.t2 = effective_t2(self.t2, anti_noise.dd_sequence,
                                   anti_noise.dd_n_blocks, self.t1)
        # Ambient field + rotation the sensor is subjected to (the physical
        # quantity the on-chip compass/gyro read back). Defaults: a 500 uT
        # field (a nearby magnet -- the NV 5 MHz linewidth cannot resolve
        # Earth's ~50 uT field, whose ~1.6 MHz doublet split sits below the
        # line; compass_measure's note says so) and a 100 deg/s fast spin
        # (the as-built microdiamond sensor is rate-grade, not inertial-grade,
        # so it responds to fast rotation, not Earth-rate). READ_COMPASS /
        # READ_GYRO measure these; the host can change them to simulate a
        # different environment.
        self.b_vector_t: List[float] = [500e-6, 0.0, -200e-6]
        self.rotation_rate_dps: float = 100.0
        # Residual detuning spread (Hz) driving the Hahn-echo / CPMG fringe.
        # A static detuning is refocused by a single Hahn pi pulse, but a
        # detuning SPREAD (inhomogeneous / fluctuating across the ensemble)
        # leaves a residual oscillation in the echo amplitude; for CPMG the
        # finite pi-pulse spacing similarly leaves a residual. This is the
        # physical quantity the Hahn/CPMG trace fringe encodes -- NOT the
        # Ramsey rotation rate (which Hahn refocuses). 20 kHz is a modest
        # spread that produces a visible, fittable fringe over a us-T2 window.
        self.detuning_hz: float = 20e3
        # per-pixel ODMR resonance detuning (drawn lazily by _run_all_odmr to
        # model the gradient spreading the 64 pixel resonances).
        self._pixel_detun_hz: Optional[np.ndarray] = None
        # Few-NV COMPUTE regime (field gradient + the per-pixel register model):
        # each pixel holds one microdiamond of ~100-300 NVs whose resonances the
        # field gradient spreads across the pixel into resolvable, linewidth-
        # spaced bins -- DISTINCT frequency-addressed lines (the 2009
        # Neumann/Taminiau regime), NOT one ensemble dip. Drawn lazily and held
        # so repeated sweeps see a consistent spectrum. ``_pixel_lines[q]`` is a
        # (n_lines,) array of dip fractions (codes fraction of BRIGHT) and
        # ``_pixel_line_freqs[q]`` the matching (n_lines,) resonance offsets (Hz
        # from D). A bin holding k NVs saturates: contrast = 1-(1-c)^k.
        self.gradient_mhz_per_um = float(gradient_mhz_per_um)
        self.pixel_size_um = float(pixel_size_um)
        self.max_nv_per_microdiamond = int(max_nv_per_microdiamond)
        self.contrast_single = float(contrast_single)
        self._pixel_line_freqs: Optional[list[np.ndarray]] = None
        self._pixel_line_contrasts: Optional[list[np.ndarray]] = None

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
                # thermal/Johnson + ADC quantization noise — a dark channel has
                # no photocurrent, hence no shot noise) so the extractor's work
                # shows up in the returned stats. Reply:
                # "<hex> <raw_bits> <kept> <extract_rate>".
                n_bytes = int(parts[1]) if len(parts) > 1 else 32
                if n_bytes <= 0 or n_bytes > 4096:
                    return "ERR"
                return self._harvest_entropy(n_bytes)
            if head == "ODMR_SWEEP":
                # ODMR_SWEEP <qubit> <f_start_khz> <f_stop_khz> <n_points>:
                # the foundational sensing primitive -- sweep the ADF4351 across
                # the NV resonance and sample one AD7616 channel per point.
                # Returns n_points "freq_khz code" pairs (flat, count-prefixed).
                qubit = int(parts[1])
                f_start = int(parts[2]); f_stop = int(parts[3])
                n_points = int(parts[4])
                if not (0 <= qubit < self.N_SITES) or n_points <= 0 or n_points > 4096:
                    return "ERR"
                return self._odmr_sweep(qubit, f_start, f_stop, n_points)
            if head == "RUN_ALL_ODMR":
                # RUN_ALL_ODMR <f_start_khz> <f_stop_khz> <n_points>: the
                # 64-parallel calibration primitive. Sweep the
                # ADF4351 across the NV resonance and sample ALL 64 AD7616
                # channels per point -- one ODMR spectrum per pixel from a
                # single command. Returns n_points lines of 64 space-separated
                # 16-bit codes, then OK (the host reshapes to (n,64) and runs
                # decompose_odmr per column to count each pixel's resolved
                # lines = its calibrated register width).
                f_start = int(parts[1]); f_stop = int(parts[2])
                n_points = int(parts[3])
                if n_points <= 0 or n_points > 4096:
                    return "ERR"
                return self._run_all_odmr(f_start, f_stop, n_points)
            if head == "READ_COMPASS":
                # READ_COMPASS [n_avg]: 4-axis ODMR doublet fit -> ambient
                # B-vector + heading. Mirrors qssc.sensors.compass_measure.
                n_avg = int(parts[1]) if len(parts) > 1 else 1000
                if n_avg <= 0:
                    return "ERR"
                return self._read_compass(n_avg)
            if head == "READ_GYRO":
                # READ_GYRO <T_ns>: Ramsey pi/2-T-pi/2 -> rotation rate (dps).
                # T_ns is the free-precession window. Mirrors gyro_measure.
                t_ns = int(parts[1])
                if t_ns <= 0:
                    return "ERR"
                return self._read_gyro(t_ns)
            if head == "READ_LOCKIN":
                # READ_LOCKIN <ref_khz> <n_cycles>: lock-in accumulator result
                # (on-minus-off differential over n_cycles modulation cycles).
                # Mirrors lockin_integrated_readout + the RTL lock-in accumulator
                # (READ_LOCKIN verb, PR3a). Reply: "diff sigma snr_single snr_int".
                ref_khz = int(parts[1]); n_cycles = int(parts[2])
                if n_cycles <= 0 or n_cycles > 10000:
                    return "ERR"
                return self._read_lockin(ref_khz, n_cycles)
            if head == "READ_LOCKINP":
                # READ_LOCKINP <site> <n_samples> <prime_idx>: prime-period
                # cosine/sine IQ lock-in. prime_idx (0..15) selects a system
                # prime p; the sim generates n_samples modulated detector codes
                # and MACs them against the EXACTLY-period-p per-prime cos/sin
                # ROM (the same ROM the RTL holds), replying "<I> <Q> <n>".
                if len(parts) < 4:
                    return "ERR"
                try:
                    site = int(parts[1]); n_samples = int(parts[2]); pidx = int(parts[3])
                except ValueError:
                    return "ERR"
                if not (0 <= site < N_SITES) or not (1 <= n_samples <= 100000) \
                        or not (0 <= pidx < 16):
                    return "ERR"
                return self._read_lockinp_iq(site, n_samples, pidx)
            if head == "RUN_SEQUENCE":
                # RUN_SEQUENCE <kind> <n_windows>: sim-side model of the RTL
                # pulse-sequence engine (RUN_SEQUENCE verb, PR3a). kind in
                # {ramsey, hahn, cpmg}; returns n_windows readout codes (one
                # per optical read window).
                kind = parts[1] if len(parts) > 1 else "ramsey"
                n_windows = int(parts[2]) if len(parts) > 2 else 8
                if kind not in ("ramsey", "hahn", "cpmg") or n_windows <= 0 or n_windows > 1024:
                    return "ERR"
                return self._run_sequence(kind, n_windows)
            if head == "STATUS":
                # Self-status: PLL lock, ADC busy, rail voltages, board temp.
                return self._status()
            if head == "RESET":
                # Soft reset of the TLC5947/ADF4351/ADC peripheral state.
                self.emitter_gs = {}
                self.sim_states = {}
                self.lut = {}
                return "OK"
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
        # A resonant π pulse flips the spin population for +X (phase 0) and -X
        # (phase π) alike -- a 2-level readout is insensitive to the XY axis of a
        # π rotation, so both must flip. (apply_pulse defaults phase=0.0 and the
        # real-ODMR sweep calls it with no phase, so phase==0 is the common +X
        # case and MUST flip, not no-op.)
        if abs(abs(phase_rad) - math.pi) < 1e-3 or abs(phase_rad) < 1e-3:
            self.sim_states[q] = 1 - self.sim_states[q]
        elif abs(phase_rad) > 1e-3:                  # off-axis RZ/RY: stochastic error
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
        # Anti-noise stack sigma (defaults -> READOUT_NOISE).
        nsigma = effective_noise_sigma(READOUT_NOISE, self.noise_env,
                                       self.anti_noise, 1)
        code = level + int(self.rng.normal(0, nsigma))
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
        AD7616 channel is a biased but independent bit stream (thermal/Johnson
        + ADC quantization noise, p1 ~ 0.52); pairing successive bits and
        keeping only 01 / 10 removes the bias. The extracted block must pass
        the :func:`qssc.crypto.entropy_health` sanity gate (issue #46) before
        it leaves the device — a degenerate/correlated stream replies ``ERR``
        instead of becoming key material. Returns
        ``"<hex> <raw_bits> <kept> <extract_rate>"``.
        """
        from .crypto import von_neumann_extract, bits_to_bytes, entropy_health
        need_bits = n_bytes * 8
        p1 = 0.52
        # von-Neumann keeps ~2*p1*(1-p1) of pairs (~50% for p1=0.52), i.e.
        # ~25% of raw LSBs, so ~6x raw is comfortably enough for one batch.
        # Real hardware keeps harvesting until a healthy block is in hand, so
        # retry the harvest+health gate up to N times: a healthy OS-entropy
        # stream passes on the first attempt, while a degenerate/correlated
        # stream (the case the issue-#46 gate exists for) exhausts the budget
        # and replies ERR. This makes a valid READ_RNG reliable without
        # weakening the gate.
        for _attempt in range(64):
            bits: list[int] = []
            raw_total = 0
            kept_total = 0
            while len(bits) < need_bits:
                raw = [1 if self.rng.random() < p1 else 0
                       for _ in range(need_bits * 6)]
                # huge `need` so the extractor processes every pair (no early stop)
                more, _st = von_neumann_extract(raw, 1 << 30)
                raw_total += len(raw)
                kept_total += len(more)
                bits.extend(more)
            health = entropy_health(bits[:need_bits])
            if health["passed"]:
                out = bits_to_bytes(bits[:need_bits])
                rate = (kept_total / raw_total) if raw_total else 0.0
                return f"{out.hex()} {raw_total} {kept_total} {round(rate, 4)}"
        return "ERR"

    def _odmr_sweep(self, qubit: int, f_start_khz: int, f_stop_khz: int,
                    n_points: int) -> str:
        """Sweep the ADF4351 across the NV resonance, one AD7616 sample per point.

        Models the ODMR lineshape the compass/thermometry fit on top of: a
        Gaussian PL dip at D (``self.sim_freq``) with the profile linewidth,
        plus photocurrent noise. Returns ``"<n> f0 c0 f1 c1 ..."``
        (freq_khz, 16-bit code) -- the same flat space-separated form as
        READ_DETECTORS, count-prefixed so the host parser knows the arity.
        """
        f0 = self.sim_freq                     # D, Hz
        lw = self.profile.odmr_linewidth_hz    # FWHM, Hz
        fs = np.linspace(f_start_khz * 1e3, f_stop_khz * 1e3, n_points)
        # Per-point noise sigma after the anti-noise stack (defaults
        # -> READOUT_NOISE, bit-identical). Single sweep -> integration_shots=1.
        nsigma = effective_noise_sigma(READOUT_NOISE, self.noise_env,
                                       self.anti_noise, 1)
        # Gaussian dip (FWHM = lw, matching simulate_odmr/odmr_decompose which
        # model the ODMR line as a Gaussian): PL = bright * (1 - contrast * G(f)).
        # FWHM-matched to the former Lorentzian so the on-resonance contrast
        # and the line width are unchanged; the wings now match the resolver
        # PSF (Lorentzian wings were biasing CLEAN on overlapping Case-D lines).
        sigma = lw / (2.0 * math.sqrt(2.0 * math.log(2.0)))  # FWHM lw -> sigma
        gauss = np.exp(-((fs - f0) ** 2) / (2.0 * sigma ** 2))
        codes = BRIGHT_CODE * (1.0 - ODMR_CONTRAST * gauss)
        codes += self.rng.normal(0, nsigma, size=n_points)
        codes = np.clip(codes, 0, ADC_MAX).astype(int)
        # make the addressed channel reflect its pumped/bright state off-resonance
        if self.emitter_gs.get(qubit, 0) <= 0:
            codes = BG_CODE + self.rng.normal(0, nsigma, size=n_points).astype(int)
            codes = np.clip(codes, 0, ADC_MAX)
        flat = [str(n_points)]
        for fk, ck in zip(fs, codes):
            flat.append(f"{int(fk / 1e3)}")      # back to kHz
            flat.append(str(int(ck)))
        return " ".join(flat)

    def _draw_pixel_lines(self) -> None:
        """Draw each pixel's few-NV COMPUTE register (lazily, once).

        One microdiamond per pixel carries ``[max_nv/3, max_nv]`` NVs (100-300 at
        the default 300 cap). The field gradient spreads their resonances across
        the within-pixel spread (``gradient * pixel_size``) into resolvable,
        linewidth-spaced bins; a bin holding ``k`` NVs is one ODMR line of
        contrast ``1-(1-c)**k`` (NV contrasts add and saturate). These are
        DISTINCT frequency-addressed lines -- the 2009 Neumann/Taminiau regime
        (several qubits + 2q gates from one stone), NOT the 10^12-NV ensemble
        sensing stone that collapses to one line. Held across sweeps so the
        spectrum is consistent (the calibration decomposes it into lines).
        """
        prof = self.profile
        lw = prof.odmr_linewidth_hz                       # FWHM, Hz
        spread_hz = self.gradient_mhz_per_um * 1e6 * self.pixel_size_um
        n_bins = max(1, int(round(spread_hz / lw))) if self.gradient_mhz_per_um > 0 else 1
        c = self.contrast_single
        if self.gradient_mhz_per_um <= 0.0 or n_bins <= 1:
            # no gradient -> every NV piles onto one ensemble line at the pixel
            # centre (one resolvable qubit, the sensing/ensemble regime).
            self._pixel_line_freqs = [np.array([0.0]) for _ in range(self.N_SITES)]
            self._pixel_line_contrasts = [np.array([c]) for _ in range(self.N_SITES)]
            return
        start = -spread_hz / 2.0 + lw / 2.0
        bin_centres = start + np.arange(n_bins) * lw      # Hz, relative to pixel centre
        lo = max(1, self.max_nv_per_microdiamond // 3)
        self._pixel_line_freqs = []
        self._pixel_line_contrasts = []
        for _ in range(self.N_SITES):
            n_nv = int(self.rng.integers(lo, self.max_nv_per_microdiamond + 1))
            picks = self.rng.integers(0, n_bins, size=n_nv)
            occ = np.bincount(picks, minlength=n_bins)
            mask = occ > 0
            self._pixel_line_freqs.append(bin_centres[mask].copy())
            self._pixel_line_contrasts.append(1.0 - (1.0 - c) ** occ[mask])

    def _run_all_odmr(self, f_start_khz: int, f_stop_khz: int,
                      n_points: int) -> str:
        """Sweep the ADF4351 and sample all 64 detectors per point.

        Each pixel's ODMR response is a Gaussian PL dip at D + a per-pixel
        detuning (the field gradient spreads the 64 pixel resonances, so the 64
        columns dip at slightly different frequencies -- the raw form
        :func:`qssc.odmr_decompose.decompose_odmr` fits per pixel to count its
        resolved lines). Returns ``n_points`` lines of 64 space-separated 16-bit
        codes, then a final ``OK`` -- the same shape the RTL
        ``RUN_ALL_ODMR`` handler emits, so the host parser is identical for sim
        and real hardware.

        With sub-pixel centroiding (``self.sub_pixel`` set): each pixel's dip spreads to its
        neighbours via the graphene/MoS2 sheet charge-share kernel, so the 64
        codes per point are a real spatial image (charge-sharing centroiding
        input) instead of 64 independent channels.

        With N-shot lock-in (``self.integration_shots > 1``): per-point readout noise
        drops by ``sqrt(N)`` (N-shot lock-in averaging of uncorrelated
        thermal/Johnson noise).
        """
        f0 = self.sim_freq                     # D, Hz
        lw = self.profile.odmr_linewidth_hz    # FWHM, Hz
        fs = np.linspace(f_start_khz * 1e3, f_stop_khz * 1e3, n_points)
        # per-pixel centre detuning (cross-pixel gradient offset): drawn once,
        # held so repeated sweeps are consistent. Spread ~ a few linewidths.
        if self._pixel_detun_hz is None:
            self._pixel_detun_hz = self.rng.normal(
                0.0, max(lw * 2.0, 1.0), self.N_SITES)
        detun = self._pixel_detun_hz
        # per-pixel few-NV line set (the within-pixel gradient spread -> distinct
        # frequency-addressed NV lines). Drawn once, held across sweeps.
        if self._pixel_line_freqs is None:
            self._draw_pixel_lines()
        line_freqs = self._pixel_line_freqs       # list of (n_lines,) Hz rel. centre
        line_contrasts = self._pixel_line_contrasts  # list of (n_lines,) dip frac
        # Averaged noise is 1/sqrt(N) of the single-shot noise.
        # The classical anti-noise stack (thermal servo, vibration
        # iso, EMI shield, feed-forward, common-mode) attenuates the environmental
        # channels on top of the 1/sqrt(N) floor. Defaults (no env) -> the bare
        # READOUT_NOISE/sqrt(N) (bit-identical to the prior model).
        noise = effective_noise_sigma(READOUT_NOISE, self.noise_env,
                                      self.anti_noise, self.integration_shots)
        # Precompute the 64x64 charge-share weight matrix (spot q's
        # footprint across pads p) so a pixel's dip spills to its neighbours.
        W = None
        if self.sub_pixel is not None:
            from .detector_centroid import charge_share_weights, pad_grid_centers
            centers = pad_grid_centers(self.sub_pixel.pad_pitch_mm)
            W = np.vstack([charge_share_weights(tuple(centers[q]), centers,
                                                self.sub_pixel.sigma_sheet_mm)
                           for q in range(self.N_SITES)])  # (64, 64): W[q, p]
        # Gaussian line (FWHM = lw), matching _odmr_sweep / simulate_odmr /
        # odmr_decompose so the sim-FPGA ODMR shape matches the resolver PSF.
        sigma = lw / (2.0 * math.sqrt(2.0 * math.log(2.0)))  # FWHM lw -> sigma
        s2 = 2.0 * sigma ** 2

        def _pixel_dip_frac(q: int, fi: float) -> float:
            """Total ODMR dip fraction (of BRIGHT) for pixel q at frequency fi:
            sum of its NV-line Gaussians, each weighted by its bin contrast."""
            lf = f0 + detun[q] + line_freqs[q]      # absolute line freqs (Hz)
            gauss = np.exp(-((fi - lf) ** 2) / s2)  # (n_lines,) peak-1 Gaussian
            return float(np.sum(line_contrasts[q] * gauss))

        lines = []
        for fi in fs:
            if W is None:
                codes = []
                for q in range(self.N_SITES):
                    if self.emitter_gs.get(q, 0) <= 0:
                        c = BG_CODE + self.rng.normal(0, noise)
                    else:
                        dip_frac = min(_pixel_dip_frac(q, fi), 1.0)
                        c = BRIGHT_CODE * (1.0 - dip_frac) + self.rng.normal(0, noise)
                    codes.append(int(min(max(int(c), 0), ADC_MAX)))
            else:
                # forward-model: each pixel q's dip depth, then spread to pads.
                dip_q = np.zeros(self.N_SITES)
                for q in range(self.N_SITES):
                    if self.emitter_gs.get(q, 0) <= 0:
                        continue
                    dip_q[q] = BRIGHT_CODE * min(_pixel_dip_frac(q, fi), 1.0)
                # pad p collects the sum of its neighbours' spread dips
                pad_dip = W.T @ dip_q            # (64,) pad-space dip
                base = np.where(
                    [self.emitter_gs.get(q, 0) > 0 for q in range(self.N_SITES)],
                    float(BRIGHT_CODE), float(BG_CODE))
                codes = base - pad_dip + self.rng.normal(0, noise, self.N_SITES)
                codes = [int(min(max(int(c), 0), ADC_MAX)) for c in codes]
            lines.append(" ".join(str(c) for c in codes))
        return "\n".join(lines) + "\nOK"

    def run_all_odmr(self, f_start_khz: int, f_stop_khz: int,
                     n_points: int) -> np.ndarray:
        """Host helper: ``RUN_ALL_ODMR`` -> ``(n_points, 64)`` int32 code matrix.

        Row ``i`` is the 64-pixel photocurrent at frequency point ``i``. The
        host runs :func:`qssc.odmr_decompose.decompose_odmr` per column (pixel)
        to count each pixel's resolved ODMR lines = its calibrated register
        width. Mirrors :meth:`qssc.backend.FPGAConnection.run_all_odmr`.
        """
        reply = self.send_command(
            f"RUN_ALL_ODMR {int(f_start_khz)} {int(f_stop_khz)} {int(n_points)}")
        if reply == "ERR":
            raise IOError("RUN_ALL_ODMR rejected (bad args)")
        toks = reply.split()
        if toks and toks[-1] == "OK":
            toks = toks[:-1]
        codes = [int(t) for t in toks]
        want = n_points * self.N_SITES
        if len(codes) != want:
            raise IOError(
                f"RUN_ALL_ODMR arity: got {len(codes)}, want {want}")
        return np.array(codes, dtype=np.int32).reshape(n_points, self.N_SITES)

    def _read_compass(self, n_avg: int) -> str:
        """4-axis ODMR doublet fit -> ambient B-vector + heading.

        Thin wrapper over :func:`qssc.sensors.compass_measure` so the simulator
        and the ``/api/compass`` endpoint share one physics model. Reply:
        ``"<B_nT> <heading_deg> <elevation_deg> <sensitivity_ut> <resolved>"``.
        """
        from .sensors import compass_measure
        res = compass_measure(self.profile, tuple(self.b_vector_t), n_avg, rng=self.rng)
        b_nt = res.b_reconstructed_t * 1e9
        return (f"{b_nt:.1f} {res.heading_deg:.2f} {res.elevation_deg:.2f} "
                f"{res.sensitivity_ut:.4f} {res.resolved_axes}")

    def _read_gyro(self, t_ns: int) -> str:
        """Ramsey pi/2-T-pi/2 -> rotation rate (dps).

        Wraps :func:`qssc.sensors.gyro_measure`; T_ns is the free-precession
        window. Reply: ``"<rate_dps> <noise_dps> <t2_us> <rate_grade>"``.
        """
        from .sensors import gyro_measure
        res = gyro_measure(self.profile, self.temperature_k,
                           rotation_rate_dps=self.rotation_rate_dps,
                           free_precession_us=t_ns / 1000.0, rng=self.rng)
        grade = 1 if res.rate_grade else 0
        return (f"{res.measured_rate_dps:.5f} {res.rate_noise_dps:.5f} "
                f"{res.t2_us:.4f} {grade}")

    def _read_lockin(self, ref_khz: int, n_cycles: int) -> str:
        """Lock-in accumulator: on-minus-off differential over n_cycles.

        Wraps :func:`qssc.sensors.lockin_integrated_readout`; ``ref_khz`` is the
        modulation reference (1 kHz typical). Reply:
        ``"<diff> <sigma> <snr_single> <snr_int>"``.
        """
        from .sensors import lockin_integrated_readout
        res = lockin_integrated_readout(BRIGHT_CODE, ODMR_CONTRAST,
                                        n_cycles, READOUT_NOISE, rng=self.rng)
        return (f"{res['differential_code']:.1f} {res['sigma_code']:.1f} "
                f"{res['single_frame_snr']:.2f} {res['integrated_snr']:.2f}")

    def _read_lockinp_iq(self, site: int, n_samples: int, prime_idx: int) -> str:
        """Sim-side model of the RTL ``READ_LOCKINP`` IQ demodulator.

        Generates ``n_samples`` detector codes whose PL is sinusoidally
        modulated at the prime period ``p = PRIME_SYMBOLS[prime_idx]`` (the
        prime channel this NV is driven on), then MACs them against the per-
        prime cos/sin ROM -- the SAME ROM the RTL holds -- and replies
        ``"<I> <Q> <n>"``. The modulation amplitude is the ODMR contrast depth
        (``0.5 * ODMR_CONTRAST * BRIGHT_CODE``); the demod recovers it as
        ``amplitude_code`` in the host result. Per-sample noise at
        ``READOUT_NOISE``; ``site`` selects the channel (the sim modulation
        depth is site-independent, matching the constant-ADC testbench model).
        """
        from .prime_readout import lockin_iq_demod
        from .prime_readout import PRIME_SYMBOLS
        p = PRIME_SYMBOLS[int(prime_idx)]
        baseline = 0.5 * (BRIGHT_CODE + DARK_CODE)
        amp = 0.5 * ODMR_CONTRAST * BRIGHT_CODE           # modulation depth (codes)
        k = np.arange(int(n_samples))
        codes = baseline + amp * np.cos(2.0 * np.pi * k / p)
        codes += self.rng.normal(0, READOUT_NOISE, size=int(n_samples))
        codes = np.clip(codes, 0, ADC_MAX).astype(np.int64)
        I, Q, n = lockin_iq_demod(codes, int(prime_idx))
        return f"{I} {Q} {n}"

    def _run_sequence(self, kind: str, n_windows: int,
                      t_step_ns: int = 0) -> str:
        """Sim-side model of the RTL pulse-sequence engine (RUN_SEQUENCE, PR3a).

        Models the optical readout of ``n_windows`` windows for a Ramsey / Hahn
        echo / CPMG sequence as a decaying fringe of photocurrent codes, so the
        host can exercise the on-host fit path without a physical board. Reply:
        ``"<n> c0 c1 ... cN-1"``.

        When ``t_step_ns > 0`` the window grid is ``arange(n) * t_step_ns`` so
        it matches the host fit's time axis (``backend_fits._fit_t2_from_codes``
        uses the same ``arange(n) * t_step_ns``); otherwise it falls back to a
        fixed ``linspace(0, 2*T2, n)`` grid. The Ramsey fringe carries the
        ``exp(-t/T2)`` decay envelope (the profile has no separate T2*, so
        decaying Ramsey at T2 is the conservative choice; real T2* < T2).

        The fringe frequency is tied to a PHYSICAL input quantity, not a
        hardcoded rad/us constant: Ramsey precesses at
        ``omega = radians(rotation_rate_dps)`` (the Sagnac relation, exactly as
        :func:`qssc.sensors.gyro_measure` models it), so the host FFT fit on
        these codes recovers the set rotation rate. Hahn / CPMG refocus a
        STATIC detuning, so their fringe is the residual detuning SPREAD
        (``self.detuning_hz``), not the rotation rate.
        """
        t2_us = self.t2 * 1e6
        if t_step_ns and t_step_ns > 0:
            ts = np.arange(n_windows) * (t_step_ns / 1000.0)   # ns -> us
        else:
            # window times spread over ~2*T2 so the decay is visible
            ts = np.linspace(0, 2 * t2_us, n_windows)
        t_s = ts * 1e-6                                         # us -> s
        if kind == "ramsey":
            omega = math.radians(float(self.rotation_rate_dps))  # rad/s (Sagnac)
            fringe = 0.5 + 0.5 * np.cos(omega * t_s) * np.exp(-ts / t2_us)
        elif kind == "hahn":
            domega = 2.0 * math.pi * float(self.detuning_hz)     # rad/s residual spread
            fringe = 0.5 + 0.5 * np.cos(domega * t_s) * np.exp(-ts / t2_us)
        else:  # cpmg
            domega = 2.0 * math.pi * float(self.detuning_hz)
            fringe = 0.5 + 0.5 * np.cos(domega * t_s) * np.exp(-(ts / t2_us) ** 2)
        codes = BRIGHT_CODE * fringe + self.rng.normal(0, READOUT_NOISE, size=n_windows)
        codes = np.clip(codes, 0, ADC_MAX).astype(int)
        return " ".join([str(n_windows)] + [str(int(c)) for c in codes])

    # ---- PR3b structured host methods (mirror FPGAConnection) ------------
    # Same signatures + return shapes as qssc.backend.FPGAConnection so the
    # /api endpoints call one method regardless of mode. The simulator path
    # uses the qssc.sensors physics directly (the wire-format round trip is
    # pinned separately in test_core.py); the real path fits raw ADC codes.

    def run_sequence(self, site: int, kind, n_windows: int,
                     t_step_ns: int = 0):
        """Ramsey/Hahn/CPMG -> n readout codes (int array).

        Mirrors ``FPGAConnection.run_sequence``; the simulator models the
        fringe from its own T2. ``t_step_ns`` sets the window grid so the
        sim's time axis matches the host fit
        (:func:`qssc.backend_fits._fit_t2_from_codes`); when 0 the grid
        falls back to a fixed ``linspace(0, 2*T2, n)``.
        """
        from .backend import _seq_kind_num
        kind_num = _seq_kind_num(kind)
        names = {0: "ramsey", 1: "hahn", 2: "cpmg"}
        reply = self._run_sequence(names[kind_num], int(n_windows),
                                   int(t_step_ns or 0))
        toks = reply.split()
        return np.array([int(t) for t in toks[1:1 + int(toks[0])]], dtype=np.int32)

    def read_lockin(self, site: int, n_cycles: int, ref_khz: int = 1000) -> dict:
        """Lock-in accumulator -> differential + SNR (lockin_integrated_readout shape)."""
        from .sensors import lockin_integrated_readout
        return lockin_integrated_readout(BRIGHT_CODE, ODMR_CONTRAST,
                                         int(n_cycles), READOUT_NOISE, rng=self.rng)

    def read_lockinp(self, site: int, n_samples: int, prime_idx: int) -> dict:
        """Prime-period IQ lock-in -> amplitude + SNR (mirrors FPGAConnection).

        ``prime_idx`` (0..15) selects the prime channel ``p =
        PRIME_SYMBOLS[prime_idx]``. The sim generates ``n_samples`` codes whose
        PL is modulated at period ``p`` and MACs them against the per-prime
        cos/sin ROM (the SAME ROM the RTL holds), then returns the IQ result
        dict (see :func:`qssc.prime_readout.lockin_iq_result`): recovered
        ``amplitude_code``, ``phase_rad``, integrated ``sigma_code`` and
        ``snr``. Same dict shape as :meth:`FPGAConnection.read_lockinp`.
        """
        from .prime_readout import lockin_iq_result
        if not (0 <= int(site) < self.N_SITES) or not (1 <= int(n_samples) <= 100000):
            raise ValueError("site/n_samples out of range")
        if not (0 <= int(prime_idx) < 16):
            raise ValueError("prime_idx must be 0..15")
        reply = self._read_lockinp_iq(int(site), int(n_samples), int(prime_idx))
        I, Q, n = (int(t) for t in reply.split())
        return lockin_iq_result(I, Q, n, int(prime_idx), float(READOUT_NOISE))

    def read_gyro(self, site: int, t_ns: int, n_windows: int = 64) -> dict:
        """Ramsey pi/2-T-pi/2 -> rotation rate (gyro_measure dict shape)."""
        from dataclasses import asdict
        from .sensors import gyro_measure
        res = gyro_measure(self.profile, self.temperature_k,
                           rotation_rate_dps=self.rotation_rate_dps,
                           free_precession_us=t_ns / 1000.0, rng=self.rng)
        return asdict(res)

    def _status(self) -> str:
        """Board self-status: PLL lock, ADC busy, rails, board temperature.

        The rails (+3V3/+5V/+12V_EMIT) and die temperature are modelled with
        small jitter around their nominal values; PLL locks once CALIBRATE or
        an ODMR sweep has run (``last_mw_freq_khz`` set), else reports unlocked.
        """
        locked = 1 if self.last_mw_freq_khz is not None else 0
        v33 = 3300 + int(self.rng.normal(0, 8))
        v5 = 4980 + int(self.rng.normal(0, 12))
        v12 = 12050 + int(self.rng.normal(0, 25))
        temp = 42 + int(self.rng.normal(0, 1))
        return (f"PLL_LOCKED {locked} ADC_BUSY 0 VCC3V3_mv {v33} "
                f"VCC5V_mv {v5} VCC12V_mv {v12} TEMP_C {temp}")
