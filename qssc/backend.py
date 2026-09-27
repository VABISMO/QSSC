# qssc/backend.py
"""Qiskit backend for the Quantum Solid State Chip (QSSC).

:class:`QSSCBackend` is a fully working ``BackendV2`` that runs any Qiskit
circuit through ``qiskit-aer`` using a noise model derived from the chosen
:class:`~qssc.defects.DefectProfile` (T1, T2 and optical readout error).  The
backend is explicit about what it does: the *quantum* result is produced by a
statevector/noise simulation, and the FPGA + on-chip readout stack is
responsible for the *physical readout visualisation* on the 64-channel CQD
emitter / graphene-detector array.

When ``hardware`` is provided (a real or simulated FPGA connection) the backend
also drives the on-chip readout path -- arming the CQD emitter channel for each
measured qubit, firing all armed channels **in parallel** via DRIVE_PATTERN, and
reading back all 64 graphene detector electrodes' 16-bit AD7616 samples in one
parallel READ_DETECTORS reply -- so the physical readout matches the simulated
bitstring. There is no camera, no analog MUX, no serial pixel scan: the chip
reads out directly on-chip and in parallel (protocol §3).
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Sequence

import numpy as np

from qiskit import QuantumCircuit, transpile
from qiskit.circuit import Measure
from qiskit.circuit.library.standard_gates import (
    HGate, XGate, YGate, SGate, TGate, RZGate, RYGate, CXGate, CZGate, CPhaseGate,
    SwapGate,
)
from qiskit.providers import BackendV2, Options, QubitProperties
from qiskit.providers import JobStatus
from qiskit.transpiler import Target, InstructionProperties, CouplingMap
from qiskit_aer import AerSimulator
from qiskit_aer.noise import (
    NoiseModel, thermal_relaxation_error, depolarizing_error, ReadoutError,
)

from .defects import (DefectProfile, get_profile, coherence_at_temperature,
                      optical_coherence_at_temperature)
from .dipole import cz_gate_time_ns, dipole_gate_fidelity
from .nuclear import (nuclear_swap_time_s, nuclear_gate_fidelity,
                      nuclear_memory_advantage)
from .simulator import (
    QubitSite, SimulatedFPGA, auto_align,
    ASE_GAIN, BRIGHT_CODE, BG_CODE, READOUT_NOISE,
    LOCKIN_MOD_HZ, ODMR_CONTRAST,
    ADC_BITS, ADC_MAX, DETECT_THRESHOLD, N_SITES,
    N_NV_PER_PIXEL,
)
from .noise_filters import (AntiNoiseConfig, NoiseEnvironment,
                            effective_t2, effective_noise_sigma)

logger = logging.getLogger(__name__)

# Classical simulation ceiling. The *circuit* model simulates ONE pixel's
# ~10-NV register (N_NV_PER_PIXEL); the *hardware* chip is 64 independent
# pixels (N_SITES). 64x10 = 640 exceeds the 63-qubit MPS cap, so the 64
# pixels are parallel processors at 300 K, NOT one 640-qubit machine; the
# cross-pixel 2q bus is optical/cryo-only (not simulated, descriptive).
# Within one register circuits up to 28 qubits use Aer's statevector method
# (exact, O(2^n) memory -- 2^28 ~ 4 GB). Wider circuits (29..64) use the
# matrix_product_state (MPS) method, which is O(n * chi^2) instead of O(2^n):
# a 2^64 statevector is exabytes (impossible), but low-entanglement circuits
# (H/product states, GHZ, QFT) keep a small MPS bond dimension chi and
# simulate 64 qubits in under a second. High-entanglement circuits (Shor
# mod-exp, multipliers) blow chi up and are capped separately by
# the runner's N / register caps (they also decohere at 300 K). Circuits wider
# than 64 (more than the physical chip) are rejected with a clear error.
SIM_MAX_QUBITS = 64
# statevector width ceiling (above this Aer's statevector method runs out of
# memory; switch to MPS).
SV_MAX_QUBITS = 28
# basis gates for MPS transpilation. We transpile against an explicit basis
# (not the AerSimulator Target) because the MPS Target's coupling map caps at
# 63 qubits -- transpiling basis-only with no coupling map is all-to-all and
# reaches the full 64.
_MPS_BASIS = ["u", "u1", "u2", "u3", "cx", "x", "h", "s", "t",
              "rz", "ry", "rx", "measure", "reset"]


# ---------------------------------------------------------------------------
# Hardware abstractions (real serial + simulator share one interface)
# ---------------------------------------------------------------------------
# Split into backend_fpga (FPGAConnection + make_connection + NoFPGADetected),
# backend_controllers (Emitter/Detector/Microwave) and backend_fits (host-side
# fits on raw ADC codes). Re-exported here so `from qssc.backend import X` keeps
# resolving for every consumer (api, runner, tests, __init__).
from .backend_fpga import (
    NoFPGADetected, FPGA_ID_TOKEN, FPGAConnection, make_connection,
)
from .backend_controllers import (
    EmitterController, DetectorInterface, MicrowaveController,
)
from .backend_fits import (
    _seq_kind_num, _log_linear_slope, _fit_t2_from_codes,
    _fit_gyro_from_codes, _gyro_result_dict,
)

# Explicit re-export list: every name below stays importable from
# ``qssc.backend`` after the split. ``__all__`` also marks the pure re-exports
# (not referenced elsewhere in this module) as intentional, so ruff F401
# does not flag them.
__all__ = [
    # defined in this module
    "QSSCBackend", "QSSCJob", "QSSCResult",
    "CalibrationManager", "QubitReadoutProcessor",
    "SIM_MAX_QUBITS", "SV_MAX_QUBITS",
    # re-exported from backend_fpga
    "NoFPGADetected", "FPGA_ID_TOKEN", "FPGAConnection", "make_connection",
    # re-exported from backend_controllers
    "EmitterController", "DetectorInterface", "MicrowaveController",
    # re-exported from backend_fits (private but consumed cross-module)
    "_fit_t2_from_codes", "_fit_gyro_from_codes",
    "_seq_kind_num", "_log_linear_slope", "_gyro_result_dict",
]

# ---------------------------------------------------------------------------
# Calibration + on-chip readout
# ---------------------------------------------------------------------------

class CalibrationManager:
    """Build the emitter-channel -> qubit-site -> detector-electrode LUT.

    On-chip calibration (protocol §5.6): drive each CQD emitter channel in
    turn, read all 64 graphene detector electrodes in parallel, and register
    the electrode whose 16-bit AD7616 sample responds above threshold. No
    camera, no alignment jig -- the software discovers the coupling purely from
    the detector response. On the 1:1-laid-out chip the mapping is the
    identity, but the sweep is the method that establishes it.
    """

    N_SITES = N_SITES

    def __init__(self, emitter: EmitterController, detector: DetectorInterface,
                 threshold: int = DETECT_THRESHOLD):
        self.emitter = emitter
        self.detector = detector
        self.threshold = threshold
        # emitter-channel index -> detector-electrode index
        self.mapping: Dict[int, int] = {}

    def perform_calibration(self):
        for index in range(self.N_SITES):
            self.emitter.drive(index, 50)
            codes = self.detector.read_all()
            best = int(np.argmax(codes))
            if codes[best] > self.threshold:
                self.mapping[index] = best
        return self.mapping

    @classmethod
    def default_detector_lut(cls) -> Dict[int, int]:
        """Deterministic identity LUT used when no physical calibration is run."""
        return {i: i for i in range(cls.N_SITES)}


class QubitReadoutProcessor:
    """Threshold graphene-detector 16-bit AD7616 samples into qubit states.

    A pumped site in the bright ms=0 spin state (state 0) reports a high
    16-bit sample; a resonant microwave pulse flips it to the dark ms=+-1
    state (state 1) and the sample drops to the ODMR-dip level. We threshold
    midway between the bright and dark levels to recover the bit.
    """

    # mirrors qssc.simulator constants (ensemble ODMR: dark = bright*(1-contrast))
    BRIGHT_CODE = 51200
    DARK_CODE = 48640                       # BRIGHT * (1 - ODMR_CONTRAST), the ~5% dip

    def __init__(self, bright: int = BRIGHT_CODE, dark: int = DARK_CODE):
        self.bright = bright
        self.dark = dark
        self.threshold = 0.5 * (bright + dark)

    def process(self, codes: np.ndarray, lut: Dict[int, int],
                num_qubits: int = N_SITES) -> List[int]:
        states = [0] * num_qubits
        for i in range(num_qubits):
            det = lut.get(i)
            if det is None or det >= len(codes):
                continue
            states[i] = 1 if int(codes[det]) < self.threshold else 0
        return states


# ---------------------------------------------------------------------------
# Qiskit job + backend
# ---------------------------------------------------------------------------

class QSSCJob:
    """Minimal synchronous job object compatible with the Qiskit result flow."""

    def __init__(self, backend, job_id: str, circuit, options: dict):
        self._backend = backend
        self._job_id = job_id
        self._circuit = circuit
        self._options = options
        self._result = None
        self._status = JobStatus.QUEUED

    def result(self):
        if self._result is None:
            self._result = self._backend._execute(self._circuit, self._options)
            self._status = JobStatus.DONE
        return self._result

    def status(self):
        return self._status

    def submit(self):
        self._status = JobStatus.RUNNING

    def cancel(self):
        return False

    @property
    def job_id(self):
        return self._job_id


class QSSCResult:
    """Lightweight result container with a Qiskit-compatible ``get_counts``.

    We avoid hand-building ``qiskit.result.Result`` (whose internal schema
    varies across Qiskit versions) and expose the two access patterns used by
    the CLI and the REST API: ``result.get_counts()`` and
    ``result.results[0]['data']['counts']``.
    """

    def __init__(self, counts: dict, shots: int, job_id: str = "qssc",
                 optical: Optional[dict] = None, ideal_counts: Optional[dict] = None,
                 assessment: Optional[dict] = None, noise_mode: str = "real",
                 approximate: bool = False):
        self._counts = dict(counts)
        self.shots = shots
        self.job_id = job_id
        self.optical = optical
        self.ideal_counts = ideal_counts
        self.assessment = assessment or {}
        self.noise_mode = noise_mode
        self.approximate = approximate
        self.backend_name = "qssc_simulator"
        self.success = True
        self.results = [{
            "name": "qssc", "shots": shots, "success": True,
            "status": "DONE", "data": {"counts": dict(counts)},
        }]

    def get_counts(self, circuit=None) -> dict:
        return dict(self._counts)


class QSSCBackend(BackendV2):
    """A Qiskit BackendV2 modelling the QSSC spin-defect chip.

    Parameters
    ----------
    profile : defect profile (NV-diamond by default) -- sets T1/T2/readout error.
    sites : list of :class:`QubitSite` (from :func:`auto_align`). If ``None`` the
        backend builds a synthetic grid.
    hardware : optional FPGA connection (real or simulated). When provided the
        on-chip readout is driven to visualise the result on the physical grid.
    shots : default number of shots.
    seed : RNG seed for Aer (reproducible demos).
    """

    def __init__(self, profile: DefectProfile | str = "NV",
                 sites: Optional[Sequence[QubitSite]] = None,
                 hardware=None, shots: int = 1024, seed: int = 42,
                 temperature_k: float = 300.0,
                 noise_mode: str = "real",
                 pixel_mode: str = "register",
                 n_nv_per_pixel: int = N_NV_PER_PIXEL,
                 register_nv_spacing_nm: float = 2.75,
                 gradient_mhz_per_um: float = 0.0,
                 pixel_size_um: float = 200.0,
                 nuclear_memory: bool = False,
                 register_line_freqs_hz: Optional[Sequence[float]] = None,
                 prime_readout: bool = False,
                 n_noise_bins: int = 64,
                 register_widths_per_pixel: Optional[Sequence[int]] = None,
                 register_line_freqs_per_pixel: Optional[Sequence[Sequence[float]]] = None,
                 anti_noise: Optional[AntiNoiseConfig] = None,
                 noise_env: Optional[NoiseEnvironment] = None,
                 integration_shots: int = 1):
        """noise_mode: 'real' (default, full Aer thermal+depolarizing+readout
        noise from the defect physics) | 'phenomenological' (fast analytic
        approximation) | 'none' (ideal). 'aer' is accepted as an alias of 'real'.

        ``pixel_mode`` selects the circuit-qubit model and only affects the
        auto-build branch (``sites is None``); explicit ``sites`` always keep
        the historical all-to-all coupling so existing tests are untouched:

        * ``"register"`` (default): ``self.sites`` is ONE pixel's N-NV register
          (all-to-all dipole coupled, the 300 K on-chip 2q gate). N is a
          MEASURED quantity -- when ``register_line_freqs_hz`` (the resolved ODMR
          line centres from :func:`qssc.odmr_decompose.decompose_odmr`) is given,
          N = len(register_line_freqs_hz) and the NVs sit on that measured
          frequency grid; otherwise N defaults to ``n_nv_per_pixel`` on the
          synthetic 6 MHz grid (a sim-only fallback, not a physics assertion).
          A field gradient (HW-fits on the qssc-grad variant) caps
          N at the resolvable count (Case D: separation > linewidth).
        * ``"ensemble"``: the 64 chip pixels ARE the circuit qubits (one
          ensemble qubit per pixel -- the bulk-microdiamond collapse to a single
          ODMR line). Cross-pixel 2q is zero at the 2.5 mm pitch, so the
          coupling map is empty and 2q circuits are rejected (switch to
          register mode for within-pixel 2q).

        The 64-pixel *hardware* die (``N_SITES``) is the physical readout layer
        and stays 64 -- 64 independent parallel processors at 300 K (64 x
        n_nv_per_pixel > the 63-qubit MPS cap, so the circuit models ONE pixel's
        register). Cross-pixel 2q is optical-bus / cryo-only and is descriptive
        here (not simulated). ``register_nv_spacing_nm`` is the design NV-NV
        separation inside one microdiamond; the 2q gate time comes from the
        dipole-dipole CZ at that spacing (qssc.dipole), ~200 ns at 2.75 nm --
        matching the defect profile's gate_2q_ns target. ``register_line_freqs_hz``
        are the MEASURED resolvable ODMR line centres from the calibration's
        ``decompose_odmr``; when supplied they override both ``n_nv_per_pixel``
        (the measured line count IS the register width N) and the synthetic
        6 MHz grid -- closing the calibration -> circuit loop. None falls back
        to the nominal width + uniform grid.

        ``register_widths_per_pixel`` (calibrated 64-parallel): a
        per-pixel list of MEASURED register widths (from
        :func:`qssc.array_calibration.calibrate_array`). When provided, the
        backend builds one independent register per pixel
        (``self._pixel_registers``) and :meth:`run_array` runs each pixel at its
        own calibrated width, SKIPPING pixels whose width is narrower than the
        circuit (a truncated Shor run gives wrong factors, so a narrow pixel is
        never run with a too-wide circuit — it is skipped and reported). When
        ``None`` (default) the backend uses the single uniform ``n_nv_per_pixel``
        register for every pixel — full back-compat with the existing path.

        ``register_line_freqs_per_pixel`` closes the calibration -> circuit loop
        for the 64-parallel path: a per-pixel list of the MEASURED ODMR line
        centres (Hz, from the cached array calibration's ``odmr_lines``). When
        provided alongside ``register_widths_per_pixel``, each pixel's register
        sits on its own MEASURED frequency grid (pixel i's NVs at
        ``register_line_freqs_per_pixel[i]``) instead of ``auto_align``'s
        synthetic 6 MHz grid — so the qubits per pixel and their frequencies the
        frontend shows are the measured ones, not a synthetic guess. ``None``
        (default) keeps the synthetic-grid fallback (back-compat).
        """
        if pixel_mode not in ("register", "ensemble"):
            raise ValueError(
                f"pixel_mode must be 'register' or 'ensemble', got {pixel_mode!r}")
        super().__init__(name="qssc_simulator")
        prof = get_profile(profile) if isinstance(profile, str) else profile
        self.profile = prof
        self.temperature_k = temperature_k
        self.hardware = hardware
        self.shots = shots
        self.seed = seed
        self.pixel_mode = pixel_mode
        self.n_nv_per_pixel = int(n_nv_per_pixel)
        self.register_nv_spacing_nm = float(register_nv_spacing_nm)
        self.gradient_mhz_per_um = float(gradient_mhz_per_um)
        self.pixel_size_um = float(pixel_size_um)
        # Measured-N override (the truth, from decompose_odmr): the resolved
        # ODMR line count beats any requested n_nv_per_pixel. register mode
        # then places the NVs on the measured frequency grid, not the
        # synthetic 6 MHz grid. Stored as a list for describe()/auto_align.
        self._register_line_freqs_hz: Optional[List[float]] = (
            list(register_line_freqs_hz) if register_line_freqs_hz is not None
            else None)
        if self._register_line_freqs_hz is not None:
            self.n_nv_per_pixel = len(self._register_line_freqs_hz)
        # Prime-coprime readout denoiser: when on, the lock-in
        # matched-filter rejects the OUT-of-band noise fraction (the energy
        # outside the prime frequency bins), improving the readout SNR by the
        # factor prime_noise_rejection = n_noise_bins / n_symbols. The EFFECTIVE
        # per-site readout error is the base optical-fluorescence misassignment
        # rate divided by that factor (clamped to [0, base]) -- this is the
        # band-rejection gain only; in-band noise inside a prime channel's
        # linewidth still passes, so it is a lower bound on the improvement, not
        # a free lunch. n_symbols = the number of prime address channels in use
        # (min of the register width and the 16 prime symbols); n_noise_bins is
        # the analysis-window bin count (default 64). Stored here so the real
        # Aer noise model, the phenomenological readout path, and the
        # noise_summary() report all agree on one effective readout error.
        from qssc.prime_readout import prime_noise_rejection, PRIME_SYMBOLS
        self._prime_readout = bool(prime_readout)
        self._n_noise_bins = max(1, int(n_noise_bins))
        self._n_prime_symbols = 1
        if self._register_line_freqs_hz is not None:
            self._n_prime_symbols = max(1, len(self._register_line_freqs_hz))
        else:
            self._n_prime_symbols = max(1, min(self.n_nv_per_pixel,
                                               len(PRIME_SYMBOLS)))
        self._prime_snr = (
            prime_noise_rejection(self._n_prime_symbols, self._n_noise_bins)
            if self._prime_readout else 1.0)
        if sites is None:
            if pixel_mode == "ensemble":
                # Ensemble mode: the 64 chip pixels ARE the circuit qubits (one
                # ensemble qubit per pixel -- the bulk-microdiamond ODMR
                # collapse to a single line). No within-pixel 2q (cross-pixel
                # 1/r**3 -> 0 at the 2.5 mm pitch); the coupling map is empty.
                sites = auto_align(prof, grid=8, register=False,
                                   temperature_k=temperature_k,
                                   rng=np.random.default_rng(seed))
                self._register_mode = False
                self._ensemble_mode = True
                self._chip_sites = list(sites)
                self.n_nv_per_pixel = 1
            else:
                # Register mode (the production / API path): self.sites is ONE
                # pixel's N-NV register (the circuit qubits, all-to-all dipole
                # coupled); the 64-pixel hardware die is a SEPARATE chip-view
                # site list (self._chip_sites) used by describe() / optical
                # readout. N is the MEASURED resolvable ODMR line count when
                # register_line_freqs_hz is given (the truth from
                # decompose_odmr); otherwise the default/cap n_nv_per_pixel on
                # the synthetic 6 MHz grid. A field gradient (HW-fits on the qssc-grad variant) caps the register width at
                # the resolvable count.
                sites = auto_align(prof, n_nv_per_pixel=self.n_nv_per_pixel,
                                   register=True, temperature_k=temperature_k,
                                   rng=np.random.default_rng(seed),
                                   gradient_mhz_per_um=gradient_mhz_per_um,
                                   pixel_size_um=pixel_size_um,
                                   register_line_freqs_hz=self._register_line_freqs_hz)
                self._register_mode = True
                self._ensemble_mode = False
                # the gradient / measured-N may have capped the register below
                # n_nv_per_pixel; re-read the actual width so describe()/Target
                # stay truthful.
                self.n_nv_per_pixel = len(sites)
                self._chip_sites = auto_align(
                    prof, grid=8, register=False, temperature_k=temperature_k,
                    rng=np.random.default_rng(seed))
        else:
            # Explicit sites (tests / custom configs): the given sites ARE both
            # the register and the chip view (back-compat -- a 16-site backend
            # reports 16 qubits and a 16-pixel readout). pixel_mode is ignored
            # here so every existing test that builds with explicit sites keeps
            # the historical all-to-all coupling untouched.
            self._register_mode = False
            self._ensemble_mode = False
            self._chip_sites = list(sites)
            self.n_nv_per_pixel = len(sites)
        self.sites = list(sites)
        self._n_qubits = len(self.sites)
        # Nuclear-spin memory: when enabled, append one 13C memory
        # qubit per electron NV (paired by index). The 13C carries nuclear_t2
        # (seconds, vs the electron's ~1.8 ms) so a circuit can STORE a state on
        # its 13C (SWAP i -> i+n_electron) and RECALL it (SWAP back) ~500x past
        # the electron coherence window. Default off keeps the register at the
        # electron width (the audit invariant; nuclear memory is opt-in).
        self._nuclear_memory = bool(nuclear_memory) and prof.nuclear_t2_s > 0.0
        # Nuclear-spin memory is a per-NV-register concept (each electron NV
        # pairs with a 13C); ensemble mode has one ensemble qubit per pixel and
        # no within-pixel register, so it is forced off (with a warning) --
        # nuclear memory is a needs-HW invention tracked in GitHub issues.
        if self._nuclear_memory and self._ensemble_mode:
            logger.warning(
                "nuclear_memory is a per-NV-register feature and is ignored in "
                "ensemble mode (no within-pixel register); disabling.")
            self._nuclear_memory = False
        self._n_electron = self._n_qubits
        self._nuclear_t2 = prof.nuclear_t2_s
        self._nuclear_swap_t = nuclear_swap_time_s(prof.nuclear_hyperfine_hz)
        if self._nuclear_memory and self._register_mode:
            self.sites = self.sites + self._nuclear_sites(self.sites)
            self._n_qubits = len(self.sites)
            self.n_nv_per_pixel = self._n_electron  # electron width unchanged
        # Calibrated 64-parallel: build one independent register
        # per pixel at its MEASURED width. run_array swaps these in per pixel
        # and skips pixels narrower than the circuit. Each pixel's register is
        # drawn with its own seed so the per-pixel site positions/frequencies
        # vary (the pixels are physically independent); the gradient caps each
        # register at its calibrated width (<= resolvable_register_width). When
        # register_widths_per_pixel is None this stays None and run_array uses
        # the single uniform register above (full back-compat).
        self._pixel_registers: Optional[dict[int, list[QubitSite]]] = None
        self._pixel_widths: Optional[list[int]] = None
        self._pixel_line_freqs: Optional[list[Optional[List[float]]]] = None
        if register_widths_per_pixel is not None and self._register_mode:
            widths = [max(1, int(w)) for w in register_widths_per_pixel]
            self._pixel_widths = widths
            self._pixel_registers = {}
            # Per-pixel MEASURED ODMR line centres (Hz), one list per pixel from
            # the cached array calibration. When present, each pixel's register
            # sits on its measured frequency grid (closing the calibration ->
            # circuit loop); the synthetic 6 MHz grid is the fallback.
            self._pixel_line_freqs = (
                [list(f) if f else None for f in register_line_freqs_per_pixel]
                if register_line_freqs_per_pixel is not None else None)
            for i, w in enumerate(widths):
                pf = (self._pixel_line_freqs[i]
                      if self._pixel_line_freqs is not None
                      and i < len(self._pixel_line_freqs) else None)
                # auto_align uses len(register_line_freqs_hz) as N when present
                # (the measured line count IS the width); w is the fallback when
                # this pixel has no measured lines (synthetic 6 MHz grid).
                self._pixel_registers[i] = auto_align(
                    prof, n_nv_per_pixel=w, register=True,
                    temperature_k=temperature_k,
                    rng=np.random.default_rng(seed + 1000 + i),
                    gradient_mhz_per_um=gradient_mhz_per_um,
                    pixel_size_um=pixel_size_um,
                    register_line_freqs_hz=pf)
        t1, t2 = coherence_at_temperature(prof, temperature_k)
        self._t1, self._t2 = t1, t2
        # Classical anti-noise stack: when a dynamical-decoupling
        # sequence is configured, extend the spin T2 used for coherence survival
        # and the dipole/thermal error models. DD refocuses low-frequency
        # dephasing (T2*->T2) but is hard-capped at 2*T1 (energy relaxation is
        # irreversible). anti_noise=None -> no change (back-compat).
        self.anti_noise = anti_noise
        if anti_noise is not None and (anti_noise.dd_sequence or "none") != "none":
            self._t2 = effective_t2(self._t2, anti_noise.dd_sequence,
                                    anti_noise.dd_n_blocks, self._t1)
        # Environmental noise + filter stack on the readout floor.
        # noise_env models the lab environment (temperature drift, vibration,
        # EMI, pressure); the anti_noise filter stack attenuates each channel.
        # Both enter the EFFECTIVE readout error (see _effective_readout_err):
        # a noisy lab worsens the fluorescence misassignment rate, and the
        # filter stack (thermal servo / vibration isolation / EMI shield /
        # feed-forward / common-mode) restores it. noise_env=None and the
        # all-off filter -> eff_noise == READOUT_NOISE -> bit-identical readout.
        self._noise_env = noise_env
        self._integration_shots = max(1, int(integration_shots))
        eff = effective_noise_sigma(READOUT_NOISE, noise_env, anti_noise,
                                    self._integration_shots)
        self._readout_noise_factor = eff / READOUT_NOISE
        # Optical-coherence material property (photon indistinguishability):
        # T2,opt + HOM visibility V at this temperature, reported descriptively by
        # assess_circuit. V=1.0 (no optical model) when optical_t1_ns<=0. At 300 K
        # NV V~2e-7; at cryo V->1 (transform-limited ideal). NOTE: this does NOT
        # gate the on-chip 2q gate, which is a ~200 ns spin (microwave/dipole)
        # gate governed by spin T2 -- room-temp spin entanglement works (Dolde
        # 2013, Neumann 2008). The HOM budget only matters for remote
        # photon-mediated entanglement between separate chips.
        self._t2_opt, self._hom_v = optical_coherence_at_temperature(
            prof, temperature_k)
        # real control/readout timings from the defect profile. The 1q gate
        # time comes from the profile (microwave pi-pulse); the 2q gate time
        # comes from the within-pixel NV-NV dipole-dipole CZ at the register's
        # design spacing (qssc.dipole.cz_gate_time_ns), which is the 300 K
        # on-chip 2q gate (Neumann 2008 / Dolde 2013). At 2.75 nm this is
        # ~200 ns, matching the profile's gate_2q_ns target -- the dipole
        # module grounds that target in 1/r**3 physics rather than asserting
        # it. (Cross-pixel 2q is optical/cryo-only; not this gate.)
        self._g1q = prof.gate_1q_ns * 1e-9
        self._g2q = cz_gate_time_ns(self.register_nv_spacing_nm) * 1e-9
        # Temperature-scaled 2q depolarization: 2q gate fidelity improves at
        # cryogenic T (phonon / charge noise fall). depol_2q is the 300 K value,
        # depol_2q_cryo_floor the asymptotic cryo limit; the effective depol at
        # this temperature is floor + (room-floor)*(T/300)**exp, -> room at 300 K
        # and -> floor as T -> 0. cryo_floor 0 = flat (back-compat). Used by the
        # noise model, the Target error, and the signal_retention estimate so
        # the three cannot disagree. See defects.py DefectProfile.depol_2q_*.
        floor = prof.depol_2q_cryo_floor
        if floor > 0.0 and temperature_k < 300.0:
            self._depol_2q = floor + (prof.depol_2q - floor) * (
                temperature_k / 300.0) ** prof.depol_temp_exponent
        else:
            self._depol_2q = prof.depol_2q
        # Cross-check (assert-only, no behaviour change): the dipole CZ
        # fidelity at the register spacing / T2 / depol should track the
        # profile's 2q-gate fidelity. We log the dipole-grounded number so the
        # 1/r**3 physics behind self._g2q / self._depol_2q is visible, not
        # asserted away.
        _fid = dipole_gate_fidelity(self.register_nv_spacing_nm, self._t2,
                                    self._depol_2q)
        logger.debug("dipole CZ fidelity @%.2f nm, T2=%.3g s: %.4f",
                     self.register_nv_spacing_nm, self._t2, _fid)
        if self._nuclear_memory:
            # Cross-check the electron-13C SWAP fidelity (hyperfine-grounded, not
            # asserted). Nuclear T2 ~1 s over the ~250 ns swap -> ~99.98%.
            _nuc_fid = nuclear_gate_fidelity(self._nuclear_t2,
                                             self._nuclear_swap_t, self._depol_2q)
            logger.debug("nuclear SWAP fidelity @A=%.3g Hz, 13C T2=%.3g s: %.4f",
                         prof.nuclear_hyperfine_hz, self._nuclear_t2, _nuc_fid)
        # nuclear memory depol reuses the 2q depol (the electron-nuclear gate
        # shares the drive-crosstalk budget of the electron-electron gate).
        self._depol_1q = prof.depol_1q
        self._readout_t = prof.readout_ns * 1e-9
        self._init_t = prof.init_ns * 1e-9
        # Per-qubit transition frequency: each site carries its own detuned
        # frequency (strain + local B + 14N hyperfine shift around the zero-
        # field splitting), so the QubitProperties report the REAL per-qubit
        # frequency -- the individual microwave-addressing fingerprint -- not a
        # single uniform 2.87 GHz for every qubit.
        self._build_target_topology()
        self._seed = seed
        self.noise_mode = noise_mode
        self._options = self._default_options()
        self._job_counter = 0
        # above this many active qubits a full density-matrix noise run is
        # infeasible (O(4^n) memory); we fall back to the analytic decoherence
        # limit computed from the SAME real T2/gate-time parameters.
        self._dm_qubit_limit = 10

    def _build_target_topology(self):
        """(Re)build the Target + coupling map + qubit properties from
        ``self.sites``. Called from __init__ and from _ensure_register when the
        register is grown to fit a wider circuit."""
        qprops = [QubitProperties(t1=s.t1, t2=s.t2, frequency=s.frequency)
                  for s in self.sites]
        # Within-pixel all-to-all dipole coupling: the circuit qubits are ONE
        # pixel's ~10-NV register -- all in one microdiamond, a few nm apart, so
        # every pair is dipole-coupled (complete graph, the physical 300 K 2q
        # gate; Neumann 2008 / Dolde 2013). The coupling map is the truthful
        # Target topology. Execution transpilation still targets AerSimulator
        # (all-to-all) so NO SWAP routing overhead is inserted: the defect-spin
        # compute route uses directly-coupled qubit sets, not a SWAP-routed
        # surface code, and adding ~3-5x SWAP 2q-gate overhead would push NV's
        # depolarization budget below the postprocess cutoff (wrong physics --
        # NV computes Shor-15 at 300 K; see qssc-spin-gate-not-photon). Cross-
        # pixel 2q is NOT in this coupling map: 1/r**3 -> zero at the 2.5 mm
        # pixel pitch, so the 64 pixels are independent at 300 K (the cross-
        # pixel bus is optical / cryo-only, descriptive). The 2q noise model
        # below noised every within-register pair so a logical cx on a non-
        # coupled pair is never silently noiseless.
        coupling = CouplingMap(self._neighbor_pairs())
        self._target = Target(dt=1e-9, qubit_properties=qprops,
                              num_qubits=self._n_qubits, coupling_map=coupling)
        self._build_target()

    def _set_register(self, electron_sites: Sequence[QubitSite]):
        """Set the active register to the given electron-NV sites (ONE pixel's
        register), append nuclear memory if enabled, and rebuild the Target /
        coupling map so the noise model and topology track the active pixel.
        Shared by :meth:`_ensure_register` (grow to fit a wider circuit) and
        :meth:`run_array` (per-pixel calibrated-width swap). The caller passes
        the ELECTRON sites; the paired 13C nuclear sites are appended here so
        the electron<->nuclear index pairing (i <-> i + n_electron) stays
        consistent regardless of how the register was chosen.
        """
        self.sites = list(electron_sites)
        self._n_electron = len(self.sites)
        if self._nuclear_memory and self._register_mode:
            # keep one 13C memory qubit per electron NV, paired by index
            self.sites = self.sites + self._nuclear_sites(self.sites)
        self._n_qubits = len(self.sites)
        self.n_nv_per_pixel = self._n_electron
        self._build_target_topology()

    def _ensure_register(self, n_qubits: int):
        """Grow the per-pixel NV register to at least ``n_qubits`` so a circuit
        wider than the default register is fully covered by the noise model and
        coupling map (every circuit qubit is a real NV in the register, all-to-
        all dipole-coupled). Capped at SIM_MAX_QUBITS (64 NVs in one pixel is the
        upper bound). No-op if the register is already wide enough, or if the
        backend was built with explicit sites (those define a fixed register).
        """
        if not self._register_mode:
            return
        n = min(int(n_qubits), SIM_MAX_QUBITS)
        if n <= self._n_qubits:
            return
        prof = self.profile
        sites = auto_align(prof, n_nv_per_pixel=n, register=True,
                           temperature_k=self.temperature_k,
                           rng=np.random.default_rng(self._seed),
                           gradient_mhz_per_um=self.gradient_mhz_per_um,
                           pixel_size_um=self.pixel_size_um,
                           register_line_freqs_hz=self._register_line_freqs_hz)
        self._set_register(sites)

    def _nuclear_sites(self, electron_sites: Sequence[QubitSite]) -> list:
        """Build the paired 13C nuclear memory qubits (one per electron NV).

        Each 13C is paired with electron NV ``i`` at index ``n_electron + i``.
        It carries the nuclear T2 (seconds) -- the long coherence that lets a
        STORED state survive ~500x past the electron window. Its frequency is
        the NV-13C hyperfine line (the RF addressing channel); readout is via
        the NV electron, so the readout error matches the electron's.
        """
        prof = self.profile
        if prof.nuclear_t2_s <= 0.0:
            return []
        n_t2 = prof.nuclear_t2_s
        n_t1 = 2.0 * n_t2  # relaxation-limited, as in qssc.nuclear
        n_e = len(electron_sites)
        out: list = []
        for i, e in enumerate(electron_sites):
            out.append(QubitSite(
                qubit_id=n_e + i, row=e.row, col=e.col,
                x_um=e.x_um, y_um=e.y_um, intensity=float(BRIGHT_CODE),
                t1=n_t1, t2=n_t2, readout_err=e.readout_err,
                frequency=prof.nuclear_hyperfine_hz, usable=True,
                pixel_id=e.pixel_id, nv_index=e.nv_index,
            ))
        return out

    # -- target / noise ----------------------------------------------------
    def _neighbor_pairs(self) -> list:
        """Directed (i, j) coupling edges for the within-pixel NV register.

        The circuit qubits are ONE microdiamond's ~10-NV register: all the NVs
        sit a few nm apart, so the magnetic dipole-dipole interaction (1/r**3)
        couples every pair -- the topology is the complete graph K_n (all-to-
        all), not a grid. There are no cross-pixel edges here: the circuit
        models a single pixel, and 1/r**3 -> zero at the 2.5 mm pixel pitch
        anyway, so the 64 pixels are independent at 300 K (cross-pixel 2q is
        the cryo optical bus, descriptive only).

        ``ensemble`` mode (auto-build, ``sites is None``) has no within-pixel
        register -- the 64 chip pixels are the circuit qubits and cross-pixel
        2q is zero at the 2.5 mm pitch -- so the coupling map is empty and 2q
        circuits are rejected in :meth:`_execute` (switch to register mode for
        within-pixel 2q). Explicit ``sites`` (tests / custom configs) keep the
        historical all-to-all coupling regardless of ``pixel_mode`` so existing
        tests are untouched.

        With nuclear-spin memory enabled, each electron NV ``i`` is also paired
        with its 13C memory qubit at ``n_electron + i`` (an electron-nuclear
        SWAP edge). 13C-13C pairs are NOT coupled (nuclear-nuclear dipole is
        negligible at this spacing)."""
        if self._ensemble_mode:
            # ensemble mode: one qubit per pixel, no within-pixel 2q at 300 K
            return []
        pairs = []
        n_e = self._n_electron
        n = len(self.sites)
        # electron-electron all-to-all (within-pixel dipole register)
        for i in range(n_e):
            for j in range(i + 1, n_e):
                pairs.append((i, j))
                pairs.append((j, i))
        # electron-13C memory pairs (one per NV, paired by index)
        if self._nuclear_memory:
            for i in range(n_e):
                if n_e + i < n:
                    pairs.append((i, n_e + i))
                    pairs.append((n_e + i, i))
        return pairs

    def _build_target(self):
        sq = {(i,): InstructionProperties(duration=self._g1q,
                                          error=self.profile.depol_1q)
              for i in range(self._n_qubits)}
        for g in (HGate(), XGate(), YGate(), SGate(), TGate(),
                  RZGate(__import__("qiskit").circuit.Parameter("theta")),
                  RYGate(__import__("qiskit").circuit.Parameter("theta"))):
            self._target.add_instruction(g, sq)
        # 2q gates exist only on within-register pairs (the all-to-all dipole
        # coupling topology from _neighbor_pairs -- the complete graph K_n of
        # the pixel's NV register), so the Target truthfully declares where a
        # native 2q gate lives. SwapGate is added so a real transpiler routing
        # against this Target can insert SWAPs on register edges (execution here
        # still routes via AerSimulator, so no SWAP overhead is paid -- see the
        # __init__ coupling comment).
        nbr = set(self._neighbor_pairs())
        tq = {(i, j): InstructionProperties(duration=self._g2q,
                                            error=self._depol_2q)
              for (i, j) in nbr}
        for g in (CXGate(), CZGate(), SwapGate(),
                  CPhaseGate(__import__("qiskit").circuit.Parameter("theta"))):
            self._target.add_instruction(g, tq)
        self._target.add_instruction(
            Measure(),
            {(i,): InstructionProperties(duration=self._readout_t,
                                         error=self.profile.readout_error)
             for i in range(self._n_qubits)})

    def _active_qubits(self, circuit: QuantumCircuit) -> list:
        active = sorted({circuit.find_bit(q).index
                         for instr in circuit.data
                         for q in instr.qubits
                         if instr.operation.name not in ("barrier",)})
        return [q for q in active if q < self._n_qubits]

    def _effective_readout_err(self, q: int) -> float:
        """Per-site optical readout error AFTER the anti-noise stack and the
        prime-coprime denoiser.

        The base rate is the site's fluorescence misassignment probability
        (``site.readout_err``). Two denoise layers compose on it:

        1. **Environmental noise factor** (``_readout_noise_factor``): a noisy
           lab raises the readout noise floor above READOUT_NOISE, which raises
           the misassignment rate by that factor; the anti-noise filter stack
           (thermal servo / vibration isolation / EMI shield / feed-forward /
           common-mode / DD) attenuates the environmental channels back down.
           Factor == 1.0 (noise_env=None, filters off) -> bit-identical.
        2. **Prime-coprime denoiser**: with prime readout on, the coprime
           matched filter rejects the out-of-band noise fraction, so the
           effective error is then divided by the prime SNR factor.

        The result is clamped to ``[0, base * 4]`` (a noisy unfiltered lab can
        worsen readout, but not beyond 4x the base rate). With both layers off
        this collapses to ``base`` -- every existing noise realization is
        bit-identical.
        """
        if q < len(self.sites):
            base = float(self.sites[q].readout_err)
        else:
            base = float(self.profile.readout_error)
        err = base * self._readout_noise_factor
        if self._prime_readout and self._prime_snr > 1.0:
            err = err / self._prime_snr
        return float(np.clip(err, 0.0, base * 4.0))

    def noise_summary(self) -> dict:
        """Per-run noise budget for the frontend's prime-on vs prime-off
        comparison. Reports the mean base readout error, the prime SNR
        improvement factor, and the EFFECTIVE readout error after the prime
        denoiser (equal to the base when prime readout is off). Also carries
        the 1q/2q depolarizing probabilities and the spin T2 so the panel
        shows the full error budget of the run that just executed.
        """
        ros = [float(s.readout_err) for s in self.sites]
        base_ro = (float(np.mean(ros)) if ros
                   else float(self.profile.readout_error))
        env_ro = float(np.clip(base_ro * self._readout_noise_factor, 0.0,
                               base_ro * 4.0))
        eff_ro = (float(np.clip(env_ro / self._prime_snr, 0.0, env_ro))
                  if self._prime_readout and self._prime_snr > 1.0
                  else env_ro)
        return {
            "readout_error": round(base_ro, 6),
            "effective_readout_error": round(eff_ro, 6),
            "depol_1q": float(self._depol_1q),
            "depol_2q": float(self._depol_2q),
            "t2_us": round(self._t2 * 1e6, 4),
            "prime_readout": bool(self._prime_readout),
            "prime_snr_factor": round(float(self._prime_snr), 4),
            "n_symbols": int(self._n_prime_symbols),
            "n_noise_bins": int(self._n_noise_bins),
            "noise_env_factor": round(float(self._readout_noise_factor), 4),
            "anti_noise": (self.anti_noise.to_dict()
                           if self.anti_noise is not None else None),
            "pixel_mode": self.pixel_mode,
        }

    def _build_real_noise_model(self, circuit: QuantumCircuit) -> NoiseModel:
        """Full physics noise model scoped to the circuit's active qubits.

        Per single-qubit gate: thermal relaxation (T1,T2,gate_1q) composed with
        a depolarizing channel (drive crosstalk). Per two-qubit gate: the tensor
        thermal relaxation of both qubits over gate_2q composed with a 2-qubit
        depolarizing channel. Measurements carry the optical readout error.
        Every parameter comes from the defect profile / site coherence.
        """
        active = self._active_qubits(circuit)
        nm = NoiseModel()
        g1q, g2q = self._g1q, self._g2q
        dep1, dep2 = self._depol_1q, self._depol_2q
        one_q_gates = ["x", "y", "h", "rz", "ry", "s", "t", "sx"]
        # Scope the 2q noise to the within-register dipole coupling edges: a
        # 2q gate is only physically native on a coupled pair (one microdiamond's
        # all-to-all NV register). With the single-register circuit model every
        # active pair is within-register, but gating on the coupling set keeps
        # this correct if the model ever grows multiple pixels. Cross-pixel
        # (non-edge) pairs never receive a silent 2q gate.
        coupled = set(self._neighbor_pairs())
        for q in active:
            s = self.sites[q]
            t1 = s.t1 or self._t1
            t2 = s.t2 or self._t2
            ro = self._effective_readout_err(q)
            try:
                therm1 = thermal_relaxation_error(t1, t2, g1q)
                dep1err = depolarizing_error(dep1, 1)
                nm.add_quantum_error(therm1.compose(dep1err),
                                     one_q_gates, [q])
                for j in active:
                    if j == q:
                        continue
                    if (q, j) not in coupled:
                        continue
                    s2 = self.sites[j]
                    # Both qubits sit idle for the full 2q gate duration, so the
                    # 2q thermal channel must be built from the 2q gate time (not
                    # the 1q therm1) for the control qubit too -- previously the
                    # control was relaxed over g1q, under-relaxing it by
                    # gate_2q/gate_1q (~8x on NV/hBN, ~7.5x on SiC).
                    # An electron-13C SWAP uses the hyperfine-coupling time
                    # (nuclear_swap, ~250 ns), not the electron dipole g2q; the
                    # 13C site already carries nuclear T2 (seconds) so its
                    # thermal channel is correspondingly slow.
                    is_en = (self._nuclear_memory
                             and ((q < self._n_electron) != (j < self._n_electron)))
                    g2q_pair = self._nuclear_swap_t if is_en else g2q
                    therm_q = thermal_relaxation_error(t1, t2, g2q_pair)
                    ej = thermal_relaxation_error(s2.t1 or self._t1,
                                                  s2.t2 or self._t2, g2q_pair)
                    two_q_therm = therm_q.tensor(ej)
                    dep2err = depolarizing_error(dep2, 2)
                    # On-chip 2q gate is a SPIN gate (microwave / magnetic
                    # dipole, ~200 ns) -- entanglement mediated by the spin
                    # interaction, not by emitted photons, so its fidelity is
                    # governed by the spin T1/T2 thermal channel + the drive
                    # depolarization (dep2) alone. Room-temperature spin
                    # entanglement is documented for NV (Neumann 2008, Dolde
                    # 2013). The optical HOM-visibility budget is a separate
                    # *descriptive* property (see assess_circuit /
                    # optical_coherence_at_temperature) relevant to remote
                    # photon-mediated entanglement, NOT to this on-chip gate.
                    two_q_err = two_q_therm.compose(dep2err)
                    nm.add_quantum_error(two_q_err,
                                         ["cx", "cz", "cp", "swap"], [q, j])
            except Exception as exc:  # pragma: no cover
                logger.debug("real noise build skipped pair %s,%s: %s", q, j, exc)
            # Asymmetric readout: NV spin readout distinguishes the BRIGHT
            # state (ms=0, high fluorescence) from the DARK states (ms=+-1,
            # the ODMR dip), and the two misassignment rates differ -- the
            # bright state is easier to identify than the dark. readout_asymmetry
            # splits the symmetric per-site readout error ro into
            #   p_bright_to_dark = ro*(1-asym)  (true ms=0 read as ms=+-1)
            #   p_dark_to_bright = ro*(1+asym)  (true ms=+-1 read as ms=0)
            # ReadoutError matrix row i = true state i, col j = recorded j:
            #   [[P(0|0), P(1|0)], [P(0|1), P(1|1)]]  with state 0 = bright.
            asym = float(self.profile.readout_asymmetry)
            p_b2d = float(np.clip(ro * (1.0 - asym), 0.0, 1.0))
            p_d2b = float(np.clip(ro * (1.0 + asym), 0.0, 1.0))
            nm.add_readout_error(
                ReadoutError([[1.0 - p_b2d, p_b2d], [p_d2b, 1.0 - p_d2b]]), [q])
        return nm

    # -- BackendV2 API -----------------------------------------------------
    @property
    def target(self):
        return self._target

    @property
    def max_circuits(self):
        return 1

    @property
    def backend_name(self):
        return "qssc_simulator"

    @classmethod
    def _default_options(cls):
        return Options()

    def run(self, run_input, **options) -> QSSCJob:
        circuits = [run_input] if isinstance(run_input, QuantumCircuit) else list(run_input)
        self._job_counter += 1
        job_id = f"qssc-{self._job_counter:04d}"
        merged = {**self._options.__dict__, **options}
        return QSSCJob(self, job_id, circuits[0], merged)

    def run_array(self, circuits, shots: int | None = None,
                  max_pixels: int = N_SITES) -> dict:
        """Run one circuit per pixel across the 64-pixel array IN PARALLEL and
        aggregate (distributed computing).

        The 64 die pixels are INDEPENDENT ~10-NV processors at 300 K (1/r**3
        -> zero at the 2.5 mm pitch, so there is no cross-pixel coupling): the
        chip's compute throughput is 64x a single pixel, not one big 640-qubit
        machine. ``run_array`` runs up to ``max_pixels`` (default 64) circuits
        -- one per pixel -- each an independent noise realization (per-pixel
        seed offset), and returns per-pixel counts plus the aggregate.

        Calibrated per-pixel width: when the backend was built
        with ``register_widths_per_pixel`` (a measured width per pixel from
        :func:`qssc.array_calibration.calibrate_array`), each pixel runs at its
        OWN width. A circuit WIDER than a pixel's calibrated width is SKIPPED
        for that pixel (reported in ``skipped_pixels``) — it is never truncated,
        because a truncated Shor run returns wrong factors (does not work). A
        pixel only runs when the circuit fits; this maximizes the number of
        qubits that actually produce correct results. When no calibration is
        loaded every pixel uses the single uniform register (back-compat).

        Use cases (register mode):
          * parallel Shor: 64 coprime bases ``a`` (one per pixel) -- whichever
            pixel finds the period wins; the rest fail silently.
          * parallel VQE: 64 parameter points evaluated at once.
          * parallel sampling: the same circuit on all 64 pixels with
            independent seeds = 64x the shots in one wall-clock step.

        In ``ensemble`` mode this DEGENERATES to replica sampling: the 64 chip
        pixels are the circuit qubits (one ensemble qubit per pixel), and a
        single global MW antenna drives them all identically -- there is no
        per-pixel MW to run *different* circuits on different pixels. So
        ``run_array`` in ensemble mode is 64x-replica sampling of ONE circuit
        (independent seeds = 64x the shots), NOT 64 independent processors.
        True independent-per-pixel compute uses the field gradient (Invention 5,
        qssc-grad) to shift each pixel's NV resonance to a distinct frequency,
        so a single global MW antenna addresses pixels by frequency -- no
        per-pixel antenna needed (see the closed issue #77); the 64-channel
        *readout* is still parallel (READ_DETECTORS reads all 64 at once).

        Returns ``{"per_pixel": [...], "aggregate_counts": {...}, "n_pixels":
        n, "throughput_x": n_run}``. With a calibration loaded, also includes
        ``per_pixel_widths``, ``skipped_pixels`` (list of pixel ids), ``n_run``,
        ``min_width``, ``max_width``.
        """
        circs = list(circuits)
        if not circs:
            return {"per_pixel": [], "aggregate_counts": {},
                "n_pixels": 0, "throughput_x": 0}
        n = min(len(circs), int(max_pixels))
        circs = circs[:n]
        base_seed = self._seed
        per_pixel = []
        skipped: list[int] = []
        calibrated = self._pixel_registers is not None
        # save the default (uniform) register's electron sites so the per-pixel
        # swap can restore it after the loop (the active register is shared
        # state; run_array must leave it as it found it).
        if calibrated:
            saved_electron = list(self.sites[:self._n_electron])
            widths = self._pixel_widths or []
        for i, qc in enumerate(circs):
            if calibrated and i < len(widths):
                w = widths[i]
                if qc.num_qubits > w:
                    # SKIP narrow pixels — never truncate (truncation breaks
                    # Shor / any algorithm relying on the full register).
                    per_pixel.append({
                        "skipped": True,
                        "reason": "circuit wider than pixel register",
                        "pixel_id": i,
                        "pixel_width": w,
                        "circuit_width": qc.num_qubits,
                    })
                    skipped.append(i)
                    continue
                # swap in this pixel's calibrated register; _execute's
                # _ensure_register sees width >= circuit width and is a no-op.
                self._set_register(self._pixel_registers[i])
            # independent noise realization per pixel (pixels are physically
            # independent; the seed offset makes the rng draws independent).
            self._seed = base_seed + i
            self._job_counter += 1
            opts = {"shots": shots} if shots is not None else {}
            per_pixel.append(self._execute(qc, opts))
        # restore the default register + seed
        if calibrated:
            self._set_register(saved_electron)
        self._seed = base_seed
        n_run = n - len(skipped)
        # aggregate counts only over pixels that actually ran (not skipped)
        agg: dict = {}
        for r in per_pixel:
            if isinstance(r, dict) and r.get("skipped"):
                continue
            for k, v in r.get_counts().items():
                agg[k] = agg.get(k, 0) + int(v)
        out = {"per_pixel": per_pixel, "aggregate_counts": agg,
            "n_pixels": n, "throughput_x": n_run, "n_run": n_run}
        if calibrated:
            pw = [w for w in widths[:n]] if widths else []
            out["per_pixel_widths"] = pw
            out["skipped_pixels"] = skipped
            out["min_width"] = min(pw) if pw else 0
            out["max_width"] = max(pw) if pw else 0
            # Per-pixel MEASURED ODMR line centres (GHz) -- the qubit frequencies
            # each pixel's register sits on (None = synthetic 6 MHz grid
            # fallback). Surfaced so the frontend's per-pixel qubits+freqs modal
            # shows the measured frequencies the circuit was run on.
            lf = self._pixel_line_freqs or []
            out["per_pixel_odmr_lines"] = [
                ([round(f / 1e9, 6) for f in lf[i]] if i < len(lf) and lf[i]
                 else None)
                for i in range(n)
            ]
        return out

    # -- execution ---------------------------------------------------------
    def _execute(self, circuit: QuantumCircuit, options: dict):
        shots = int(options.get("shots", self.shots))
        job_id = f"qssc-{self._job_counter:04d}"
        mode = options.get("noise_mode", self.noise_mode)
        if circuit.num_qubits > SIM_MAX_QUBITS:
            raise ValueError(
                f"circuit uses {circuit.num_qubits} qubits, but the chip has "
                f"{SIM_MAX_QUBITS} physical sites -- the circuit is wider than "
                f"the detector grid. Use a smaller register or a lower N.")
        # Ensemble mode has no within-pixel 2q gate (cross-pixel 1/r**3 -> 0 at
        # the 2.5 mm pitch). Aer ignores the backend Target coupling map, so a
        # 2q circuit would otherwise execute as silent noiseless CXs -- reject
        # it with a clear message instead. 1q-only circuits run fine (64 chip
        # pixels). Register mode (the default) keeps the all-to-all dipole 2q.
        if self._ensemble_mode:
            _, n_2q, n_compiled, _, _, _, _ = self._decomposed_cost(circuit)
            if n_2q > 0 or n_compiled > 0:
                raise ValueError(
                    "ensemble mode has no within-pixel 2q gate at 300 K (the 64 "
                    "chip pixels are independent at the 2.5 mm pitch); switch "
                    "to register mode for within-pixel dipole 2q gates.")
        # Grow the per-pixel NV register to cover this circuit so every qubit
        # is a real (coupled, noised) NV in the register, not a silently
        # noiseless index past the default register width.
        self._ensure_register(circuit.num_qubits)
        ideal_counts = self._run_ideal(circuit, shots)
        assessment = self.assess_circuit(circuit)
        approximate = False

        if mode in ("none", "ideal"):
            counts = ideal_counts
        elif mode in ("phenomenological", "approx"):
            counts = self._apply_phenomenological_noise(circuit, ideal_counts, shots)
        else:  # "real" (default) and "aer" alias
            counts, approximate = self._run_real(circuit, shots, assessment)

        optical = None
        if self.hardware is not None:
            optical = self._drive_optical_readout(circuit, counts, shots)
        return QSSCResult(counts, shots, job_id=job_id, optical=optical,
                          ideal_counts=ideal_counts, assessment=assessment,
                          noise_mode=mode, approximate=approximate)

    def _run_ideal(self, circuit: QuantumCircuit, shots: int) -> dict:
        """Ideal simulation via Aer (no noise model).

        Circuits up to ``SV_MAX_QUBITS`` (28) use the statevector method --
        exact and fastest. Wider circuits (29..64) use the matrix_product_state
        (MPS) method, which is O(n * chi^2) instead of O(2^n): a 2^64
        statevector is exabytes (impossible), but a low-entanglement circuit
        (H/product states, GHZ, QFT) keeps a small MPS bond dimension and
        simulates 64 qubits in under a second. MPS is transpiled against an
        explicit basis-gate set (not the AerSimulator Target, whose MPS coupling
        map caps at 63 qubits) so the full 64-site chip width is reachable.

        Raises on failure rather than silently returning fake all-zero
        counts -- a circuit that Aer cannot simulate (too wide, unsupported
        op, bond dimension blew up on a high-entanglement circuit...) must
        surface as a real error, not a fabricated |0...0> result that would
        mislead the dashboard into showing "everything measured zero". The
        width guard in :meth:`_execute` rejects over-wide circuits first;
        this catch-all only fires for unexpected Aer failures.
        """
        n = circuit.num_qubits
        if n <= SV_MAX_QUBITS:
            aer = AerSimulator(seed_simulator=self._seed)
            t_circ = transpile(circuit, aer, optimization_level=1)
            return aer.run(t_circ, shots=shots).result().get_counts()
        # wide circuit -> matrix_product_state, transpiled basis-only (no
        # Target coupling-map cap) so 64 qubits is reachable.
        aer = AerSimulator(method="matrix_product_state",
                           seed_simulator=self._seed)
        t_circ = transpile(circuit, basis_gates=_MPS_BASIS,
                           optimization_level=1, layout_method="trivial")
        return aer.run(t_circ, shots=shots).result().get_counts()

    def _run_real(self, circuit: QuantumCircuit, shots: int,
                  assessment: dict) -> tuple:
        """Real-physics noisy run.

        For circuits with <= ``_dm_qubit_limit`` active qubits *and no opaque
        compiled blocks* we run the full Aer density-matrix simulation with the
        thermal+depolarizing+readout noise model built from the defect's real
        T1/T2 and gate times. For larger circuits a density-matrix run is
        O(4^n) and infeasible in the browser; we instead apply the **analytic
        decoherence limit** computed from the *same* real T2 and gate-time
        parameters (readout error + T2 damping). The ``approximate`` flag is
        set so callers can label it.

        We also fall back to the analytic decoherence limit when the circuit
        contains opaque compiled blocks (Shor/QFT-NOS controlled mod-mult,
        multipliers, QFT adders...): the real noise model only attaches
        thermal/depolarizing error to *named basis gates*, so an opaque
        ``UnitaryGate`` / ``c-unitary`` would pass through the DM simulator
        essentially un-noised and give an *optimistic* result that contradicts
        ``assess_circuit`` (which counts the block's decomposed 2q cost). To
        keep the run and the assessment consistent, compiled-block circuits
        use the phenomenological decoherence path regardless of qubit count.
        Either way the result reflects the actual chip physics, not a generic
        placeholder — and for circuits that exceed the room-temperature
        coherence budget the physical outcome is decoherence to a near-uniform
        distribution.
        """
        n_active = len(self._active_qubits(circuit))
        _, _, n_compiled, _, _, _, _ = self._decomposed_cost(circuit)
        use_dm = (n_active <= self._dm_qubit_limit) and (n_compiled == 0)
        if use_dm:
            try:
                nm = self._build_real_noise_model(circuit)
                aer = AerSimulator(seed_simulator=self._seed, noise_model=nm,
                                   method="density_matrix")
                t_circ = transpile(circuit, aer, optimization_level=1)
                counts = aer.run(t_circ, shots=shots).result().get_counts()
                return counts, False
            except Exception as exc:  # pragma: no cover
                logger.warning("real DM run failed, falling back: %s", exc)
        # analytic decoherence limit (real parameters) for large circuits, or
        # for circuits whose compiled blocks the DM noise model cannot noise
        ideal = self._run_ideal(circuit, shots)
        counts = self._apply_phenomenological_noise(circuit, ideal, shots)
        return counts, True

    def _decomposed_cost(self, circuit: QuantumCircuit) -> tuple:
        """Estimate the *decomposed* (basis-gate) cost of a circuit.

        Returns ``(n_1q, n_2q, n_compiled_blocks, quantum_time_s, est_2q,
        est_1q, crit_path_s)`` where ``quantum_time_s`` is the estimated
        SERIAL duration of the quantum evolution (every decomposed gate
        summed, 1q + 2q gate times, no readout) and ``crit_path_s`` is the
        CRITICAL-PATH (wall-clock) duration -- coherence decays over the
        circuit DEPTH, not the serial gate sum, because gates on disjoint
        qubit pairs run in parallel on the all-to-all register. Compiled
        high-level blocks (Shor's
        controlled modular exponentiation, QFT, multipliers) have a tiny logical
        depth but decompose to many 2-qubit gates; we estimate that cost so the
        coherence physics is faithful.
        """
        ops = circuit.count_ops()
        one_q = {"h", "x", "y", "z", "s", "t", "sx", "rx", "ry", "rz", "u",
                 "u1", "u2", "u3"}
        two_q = {"cx", "cz", "cp", "swap", "ecr", "iswap"}
        n1q = sum(int(ops.get(g, 0)) for g in one_q)
        n2q = sum(int(ops.get(g, 0)) for g in two_q)
        # compiled blocks -> estimated decomposed 2-qubit gate count. These
        # high-level blocks (modular exponentiation, QFT, multipliers, adders,
        # phase estimation) have a tiny logical depth but decompose to a 2q-gate
        # count that scales **quadratically** with the block's register width w
        # (an n-bit multiplier/QFT/adder is O(n^2) entangling gates). The stored
        # `factor` is the 2q-gate count of a reference width-4 instance of an
        # *efficient* arithmetic construction, so each block's cost is scaled by
        # (w/4)^2. This models a REAL Shor / arithmetic circuit (Beauregard /
        # Vedral-Barenco-Ekert O(n^2) mod-exp, QFT n(n-1)/2), NOT the exponential
        # CNOT count Qiskit produces when it naively decomposes a generic
        # UnitaryGate -- the Aer sim still runs that exact unitary, but the
        # coherence/depolarization physics must reflect the real gate count.
        block_factor = {
            "unitary": 32, "c-unitary": 32,            # mod-mult / controlled-U, ~2n^2
            "IQFT": 8, "QFT": 8, "qft": 8,             # QFT on control register, ~n^2/2
            "rgqft_multiplier": 32, "CDKMRippleCarryAdder": 16,
            "DraperQFTAdder": 8, "PhaseEstimation": 40,
        }
        n_compiled = 0
        decomp_2q = 0
        for inst in circuit.data:
            name = inst.operation.name
            if name in one_q or name in two_q or name in ("measure", "barrier"):
                continue
            factor = block_factor.get(name, 8)  # generic compiled block
            # block width (qubits): scale cost quadratically with width.
            try:
                w = int(inst.operation.num_qubits)
            except Exception as exc:  # noqa: BLE001
                w = len(inst.qubits)
                logger.debug("operation num_qubits unavailable for %r: %s",
                             name, exc)
            w = max(2, w)
            n_compiled += 1
            decomp_2q += factor * (w * w) / 16.0
        est_2q = n2q + decomp_2q
        est_1q = n1q + est_2q * 2
        quantum_time_s = est_1q * self._g1q + est_2q * self._g2q
        # Critical-path (wall-clock) time: coherence decays over the circuit
        # DEPTH, not the serial gate sum -- gates on disjoint qubits run in
        # parallel on the all-to-all register, so a QFT (log-depth, n(n-1)/2
        # gates) or parallel arithmetic decoheres by ~depth, not ~n^2. For
        # explicit 1q/2q gates the depth is qiskit's circuit.depth(); for
        # compiled blocks (mod-exp / QFT / multipliers) the logical depth is
        # tiny but the DECOMPOSED depth is O(n) for O(n^2) gates, i.e.
        # ~sqrt(decomp_2q) layers -- use that as a floor so a Shor mod-exp
        # block (~10^3 2q gates, ~sqrt ~30 layers) is timed at its real
        # depth, not as a single layer. The serial quantum_time_s is kept
        # for the depolarization product (cumulative per-gate depol IS
        # serial-correct).
        depth = max(circuit.depth(), 1)
        decomp_depth_floor = (math.sqrt(max(est_2q, 0))
                              if est_2q > 0 else 0.0)
        crit_path_s = max(depth * self._g1q
                          + max(n2q, 0) * (self._g2q - self._g1q),
                          decomp_depth_floor * self._g2q)
        return (n1q, n2q, n_compiled, quantum_time_s,
                est_2q, est_1q, crit_path_s)

    def _signal_survival(self, circuit: QuantumCircuit) -> tuple:
        """Shared signal-retention estimate used by :meth:`assess_circuit` and
        the phenomenological blend so the two paths cannot disagree.

        ``signal_retention = coherence_survival * depolarization_survival``
        where ``coherence_survival = exp(-t_crit/T2)`` (spin T2 dephasing over
        the circuit's CRITICAL-PATH / wall-clock time, not the serial gate
        sum -- gates on disjoint qubits run in parallel on the all-to-all
        register, so coherence decays over the depth) and
        ``depolarization_survival = (1-depol_1q)**est_1q * (1-depol_2q)**est_2q``
        (cumulative per-gate drive depolarization, temperature-independent,
        and SERIAL-correct -- depolarization accumulates per gate applied).
        This is the on-chip *spin*-gate budget: the QSSC 2-qubit gate is a
        ~200 ns microwave / magnetic-dipole spin gate, so its fidelity is set
        by the spin T2 + depolarization, NOT by optical photon
        indistinguishability. Room-temperature spin entanglement is documented
        for NV (Neumann 2008, Dolde 2013); NV (T2 ~ 1.8 ms) computes at 300 K,
        hBN/SiC (T2 ~ us) do not. ``est_1q``/``est_2q`` are the *decomposed*
        gate counts from :meth:`_decomposed_cost` (compiled blocks expanded to
        their 2q-gate cost), so a Shor run whose mod-exp block hides ~10^3
        entangling gates is damped by all of them, not just its logical depth.

        The optical HOM-visibility budget (``optical_survival = V_HOM**est_2q``,
        ``hom_visibility``) is returned too, but ONLY as a descriptive material
        property for the remote photon-mediated-entanglement channel -- it is
        NOT multiplied into ``signal_retention`` because it does not govern the
        on-chip spin gate.

        Returns ``(coherence_survival, depolarization_survival, optical_survival,
        hom_visibility, signal_retention, crit_path_s, est_1q, est_2q)``.
        """
        _, _, _, _, est_2q, est_1q, crit_path_s = self._decomposed_cost(circuit)
        t2 = max(self._t2, 1e-15)
        coherence_survival = math.exp(-crit_path_s / t2)
        d1 = self._depol_1q
        d2 = self._depol_2q
        depol_survival = ((1.0 - d1) ** max(est_1q, 0)
                         * (1.0 - d2) ** max(est_2q, 0))
        signal_retention = coherence_survival * depol_survival
        # Optical HOM-visibility budget for the REMOTE photon-entanglement
        # channel -- descriptive only, NOT part of the on-chip spin-gate
        # signal_retention. V=1 (no photon channel) when the profile has no
        # optical model or at cryo (transform-limited ideal); V~2e-7 at 300 K NV.
        optical_survival = self._hom_v ** max(est_2q, 0)
        return (coherence_survival, depol_survival, optical_survival,
                self._hom_v, signal_retention, crit_path_s, est_1q, est_2q)

    def assess_circuit(self, circuit: QuantumCircuit) -> dict:
        """Physical-feasibility assessment of a circuit on this chip.

        Compares the circuit's estimated *decomposed* execution time against the
        coherence budget T2, and reports whether the algorithm fits within the
        coherence window — the key limitation of a 300 K spin-defect chip.
        """
        depth = max(circuit.depth(), 1)
        n1q, n2q, n_compiled, quantum_time_s, est_2q, est_1q, crit_path_s = (
            self._decomposed_cost(circuit))
        duration_s = quantum_time_s + self._readout_t
        t2 = self._t2
        budget_gates = int(t2 / self._g1q) if self._g1q > 0 else 0
        # Coherence decays over the circuit DEPTH (critical-path / wall-clock
        # time), not the serial gate sum -- gates on disjoint qubits run in
        # parallel on the all-to-all register. within_coherence and the
        # decoherence fraction are depth-based; the serial quantum_time_s is
        # retained for the depolarization product (cumulative per-gate depol
        # IS serial-correct) and for the duration_ns/duration_us report.
        damping = 1.0 - math.exp(-crit_path_s / max(t2, 1e-15))
        within = crit_path_s <= t2
        # Shared retention estimate (spin coherence + cumulative per-gate
        # depolarization) -- the phenomenological blend uses the same helper,
        # so assessment and run cannot disagree by construction. The on-chip
        # 2q gate is a ~200 ns spin (microwave / dipole) gate, so
        # signal_retention is the SPIN budget only; the optical HOM budget is
        # reported separately as a descriptive material property.
        (coherence_survival, depol_survival, optical_survival, hom_v,
         signal_retention, _, _, _) = self._signal_survival(circuit)
        # Optical HOM-visibility budget for the REMOTE photon-entanglement
        # channel -- descriptive only, reported as a material property. It does
        # NOT gate the on-chip spin 2q gate, so it never drives the verdict.
        # 1q-only circuits (QRNG/BB84/sensing) have no 2q gate either way.
        needs_2q = est_2q > 0
        within_optical = (not needs_2q) or (hom_v >= 0.5)
        if n_compiled > 0 and not within:
            verdict = ("contains compiled blocks (modular exponentiation / QFT / "
                       "arithmetic) whose decomposed depth (~%g 2q gates, ~%g us) "
                       "far exceeds the spin-T2 coherence budget — NOT physically "
                       "executable at this temperature without cooling or QEC "
                       "(signal retention %.2f%%: coherence %.2f%% x depolarization "
                       "%.2f%% over ~%g decomposed 2q gates)"
                       % (est_2q, round(quantum_time_s * 1e6, 1),
                          signal_retention * 100, coherence_survival * 100,
                          depol_survival * 100, est_2q))
        elif within:
            verdict = ("fits within the T2 coherence window — physically "
                       "executable at this temperature (signal retention %.2f%%)"
                       % (signal_retention * 100))
        elif damping < 0.5:
            verdict = ("partially decohered at this temperature — result is "
                       "T2-limited; cool the chip for higher fidelity "
                       "(signal retention %.2f%%)" % (signal_retention * 100))
        else:
            verdict = ("exceeds the room/coherence budget — not physically "
                       "executable at this temperature without cooling or "
                       "quantum error correction (signal retention %.2f%%)"
                       % (signal_retention * 100))
        return {
            "depth": depth,
            "n_1q": n1q, "n_2q": n2q,
            "n_compiled_blocks": n_compiled,
            "estimated_2q_decomposed": est_2q,
            "estimated_1q_decomposed": est_1q,
            "logical_duration_us": round(crit_path_s * 1e6, 4),
            "duration_ns": round(duration_s * 1e9, 1),
            "duration_us": round(duration_s * 1e6, 4),
            "t2_us": round(t2 * 1e6, 4),
            "coherence_budget_gates": budget_gates,
            "decoherence_fraction": round(damping, 4),
            "coherence_survival": round(coherence_survival, 4),
            "depolarization_survival": round(depol_survival, 4),
            "optical_survival": float(optical_survival),
            "hom_visibility": float(hom_v),
            "optical_t2_us": round(self._t2_opt * 1e6, 6),
            "signal_retention": round(signal_retention, 4),
            "within_coherence": within,
            "within_optical_coherence": within_optical,
            "verdict": verdict,
            # Anti-noise stack echo (None when unset -> back-compat).
            "anti_noise": (self.anti_noise.to_dict()
                           if self.anti_noise is not None else None),
        }

    def _apply_phenomenological_noise(self, circuit: QuantumCircuit,
                                      ideal_counts: dict, shots: int) -> dict:
        """Fast, tunable noise applied to the ideal measurement distribution.

        Two effects, both derived from the defect profile and the per-site
        properties:

        * **Readout error** -- each measured bit of each shot is flipped with
          probability ``site.readout_err`` (optical fluorescence readout).
        * **Signal-retention damping** -- the distribution is blended with the
          maximally mixed distribution by ``(1 - signal_retention)`` where
          ``signal_retention = exp(-t_circuit/T2) * (1-depol_1q)**est_1q *
          (1-depol_2q)**est_2q`` (T2 dephasing over the decomposed evolution time
          AND cumulative per-gate depolarization over the decomposed gate
          counts). The shared :meth:`_signal_survival` helper keeps this
          identical to :meth:`assess_circuit`'s reported retention.

        This runs in milliseconds regardless of qubit count, in contrast to a
        full density-matrix noise simulation.
        """
        if not ideal_counts or circuit.num_clbits == 0:
            return dict(ideal_counts)
        rng = np.random.default_rng(self._seed)
        # --- readout error: resample shots and bit-flip ---------------------
        nbits = circuit.num_clbits
        states = np.fromiter(ideal_counts, dtype=object)
        probs = np.array([ideal_counts[s] for s in states], dtype=float)
        probs /= probs.sum()
        # map bitstring -> int (qiskit orders bits MSB-first in the string)
        ints = np.array([int(s.replace(" ", ""), 2) for s in states])
        drawn = rng.choice(ints, size=shots, p=probs)
        # measured qubit -> readout error
        meas_map = {}
        for instr in circuit.data:
            if instr.operation.name == "measure":
                cl = circuit.find_bit(instr.clbits[0]).index
                pq = circuit.find_bit(instr.qubits[0]).index
                meas_map[cl] = min(pq, len(self.sites) - 1)
        # Readout error. Symmetric readout (readout_asymmetry == 0, the ensemble
        # sensor case) uses a single flip probability per clbit -- bit-identical
        # to the original single-draw path (same rng consumption -> same noise
        # realization, so symmetric profiles are unaffected). Asymmetric readout
        # (the single-shot NV case: ms=0 bright vs ms=+-1 dark) flips a 0->1
        # and a 1->0 at DIFFERENT rates, applied state-conditionally with two
        # draws per bit -- matching the ReadoutError in _build_real_noise_model
        # so this analytic path cannot disagree with the qiskit noise-model path.
        asym = float(self.profile.readout_asymmetry)
        err_per_cl = np.zeros(nbits)
        for cl, pq in meas_map.items():
            err_per_cl[cl] = self._effective_readout_err(pq)
        # Two execution paths. The int-packed path (nbits < 64) preserves the
        # exact rng stream every existing test anchors against (e.g. SiC
        # symmetric mul==12). The bit-matrix path (nbits >= 64) is width-
        # agnostic: a 64-bit bitstring overflows int64 (2^64-1 > int64 max) so
        # numpy's >>/xor ufuncs and rng.integers(0, 2**nbits) fail -- operate on
        # a (shots, nbits) uint8 bit matrix instead.
        if nbits < 64:
            noisy_int = np.copy(drawn)
            if asym == 0.0:
                # Symmetric: one flip prob per clbit (back-compat rng stream).
                flip = rng.random((shots, nbits)) < err_per_cl[::-1][None, :]
                for i in range(nbits):
                    noisy_int = np.where(flip[:, i], np.bitwise_xor(noisy_int, 1 << i), noisy_int)
            else:
                # Asymmetric: p_bright_to_dark = ro*(1-asym), p_dark_to_bright =
                # ro*(1+asym), applied per the current (true) bit value.
                p0to1 = np.zeros(nbits)   # true 0 (bright) -> read 1 (dark)
                p1to0 = np.zeros(nbits)   # true 1 (dark)   -> read 0 (bright)
                for cl, pq in meas_map.items():
                    ro = self._effective_readout_err(pq)
                    p0to1[cl] = float(np.clip(ro * (1.0 - asym), 0.0, 1.0))
                    p1to0[cl] = float(np.clip(ro * (1.0 + asym), 0.0, 1.0))
                p0to1_ord = p0to1[::-1]   # bit i (LSB) <-> cl = nbits-1-i
                p1to0_ord = p1to0[::-1]
                for i in range(nbits):
                    bit_i = (noisy_int >> i) & 1
                    flip = np.where(bit_i == 0,
                                    rng.random(shots) < p0to1_ord[i],
                                    rng.random(shots) < p1to0_ord[i])
                    noisy_int = np.where(flip, np.bitwise_xor(noisy_int, 1 << i), noisy_int)
            # --- signal-retention damping: blend with uniform ----------------
            # Use the SAME _signal_survival estimate as assess_circuit (coherence
            # * cumulative per-gate depolarization over the decomposed gate
            # counts), so the analytic limit agrees with the physical-feasibility
            # verdict: a Shor run whose assessment reports a near-destroyed
            # signal must decohere to a near-uniform distribution here, not
            # recover its factors.
            _, _, _, _, signal_retention, _, _, _ = self._signal_survival(circuit)
            damping = 1.0 - signal_retention
            uniform = rng.integers(0, 2 ** nbits, size=shots)
            final_int = np.where(rng.random(shots) < damping, uniform, noisy_int)
            unique, cnts = np.unique(final_int, return_counts=True)
            counts = {format(int(v), f"0{nbits}b"): int(c) for v, c in zip(unique, cnts)}
            return counts
        # --- wide path (nbits >= 64): bit-matrix ----------------------------
        drawn_list = [int(v) for v in drawn]
        bits = np.zeros((shots, nbits), dtype=np.uint8)
        for i in range(nbits):
            bits[:, i] = [(d >> i) & 1 for d in drawn_list]   # bit i (clbit i)
        if asym == 0.0:
            flip = rng.random((shots, nbits)) < err_per_cl[::-1][None, :]
            bits = np.where(flip, 1 - bits, bits).astype(np.uint8)
        else:
            p0to1 = np.zeros(nbits)
            p1to0 = np.zeros(nbits)
            for cl, pq in meas_map.items():
                ro = self._effective_readout_err(pq)
                p0to1[cl] = float(np.clip(ro * (1.0 - asym), 0.0, 1.0))
                p1to0[cl] = float(np.clip(ro * (1.0 + asym), 0.0, 1.0))
            p0to1_ord = p0to1[::-1]
            p1to0_ord = p1to0[::-1]
            for i in range(nbits):
                is_zero = bits[:, i] == 0
                fz = rng.random(shots) < p0to1_ord[i]
                fo = rng.random(shots) < p1to0_ord[i]
                flip = np.where(is_zero, fz, fo)
                bits[flip, i] = 1 - bits[flip, i]
        _, _, _, _, signal_retention, _, _, _ = self._signal_survival(circuit)
        damping = 1.0 - signal_retention
        uniform_bits = rng.integers(0, 2, size=(shots, nbits), dtype=np.uint8)
        damped = rng.random(shots) < damping
        bits = np.where(damped[:, None], uniform_bits, bits)
        # repack to qiskit MSB-first bitstrings (clbit i -> string index nbits-1-i)
        counts = {}
        for row in bits:
            bs = ''.join(str(int(b)) for b in row[::-1])
            counts[bs] = counts.get(bs, 0) + 1
        return counts

    def _drive_optical_readout(self, circuit: QuantumCircuit, counts: dict, shots: int) -> dict:
        """Drive the on-chip readout to display the most-likely measured bitstring.

        The 64 CQD channels are independent: we ARM every measured qubit's
        emitter (SET_EMITTER) at full grayscale, set each coupled spin to the
        measured bit (0 = bright ms=0 high PL, 1 = dark ms=+-1 ODMR dip the
        circuit's MW pulses drove), then fire ALL armed emitters SIMULTANEOUSLY
        with a single DRIVE_PATTERN and read all 64 detector electrodes in one
        parallel READ_DETECTORS reply. This is the real hardware parallelism --
        no pixel-by-pixel serial scan, no camera, no image. The 16-bit AD7616
        samples are thresholded to recover the physical readout state per site.
        """
        try:
            emitter = EmitterController(self.hardware)
            detector = DetectorInterface(self.hardware)
            proc = QubitReadoutProcessor()
            lut = CalibrationManager.default_detector_lut()
            # The readout drives the 64-pixel CHIP die (self._chip_sites), not
            # the circuit register: each circuit qubit i maps to pixel i, and
            # the unmeasured pixels read -1. The register (self._n_qubits) is
            # the physics model; the detector array is the chip view.
            n_chip = len(self._chip_sites)
            bitstring = max(counts, key=counts.get) if counts else "0" * circuit.num_clbits
            # map measured classical bits -> qubit indices (hardware channels)
            meas_map = []
            for instr in circuit.data:
                if instr.operation.name == "measure":
                    cl = circuit.find_bit(instr.clbits[0]).index
                    pq = circuit.find_bit(instr.qubits[0]).index
                    meas_map.append((cl, pq))
            measured_pqs: list = []
            measured_pqset: set = set()
            for cl, pq in meas_map:
                str_idx = (circuit.num_clbits - 1) - cl
                if str_idx < len(bitstring) and pq < n_chip:
                    bit = int(bitstring[str_idx] == "1")
                    det = lut.get(pq, pq)
                    # Arm this channel (SET_EMITTER). All armed channels fire
                    # together below. The spin state per site is set two ways:
                    #  - SimulatedFPGA: poke sim_states so its photocurrent model
                    #    returns the bright/dark code for the measured bit.
                    #  - real FPGAConnection: the physical spin state is set by
                    #    the real microwave pulses (APPLY_PULSE) the circuit
                    #    drove; the readout here reads real AD7616 codes and
                    #    QubitReadoutProcessor thresholds them. Do NOT poke a
                    #    sim_states dict -- a real connection has none.
                    emitter.set_emitter(pq, 4095)
                    if isinstance(self.hardware, SimulatedFPGA):
                        self.hardware.sim_states[pq] = bit
                    measured_pqs.append((pq, det))
                    measured_pqset.add(pq)
            # Fire every armed emitter in parallel, then read all 64 detectors
            # in one parallel reply -- the 4x AD7616 sample simultaneously.
            emitter.drive_pattern(50)
            codes = detector.read_all()
            states = proc.process(codes, lut, num_qubits=n_chip)
            # electrodes that were never armed read the background floor (below
            # threshold), which process() would threshold as state 1 (dark).
            # Mark them -1 so "not read this run" is distinguishable from "read
            # as |1>".
            for i in range(n_chip):
                if i not in measured_pqset:
                    states[i] = -1
            measured_dets = [det for _, det in measured_pqs]
            photocurrent_mean = (float(np.mean([codes[d] for d in measured_dets]))
                                 if measured_dets else 0.0)
            # Disarm all channels so the next run starts clean.
            emitter.clear_emitters()
            return {"displayed_bitstring": bitstring, "read_states": states,
                    "photocurrent_mean": photocurrent_mean}
        except Exception as exc:  # pragma: no cover - hardware path
            logger.warning("on-chip readout skipped: %s", exc)
            return {"error": str(exc)}

    # -- introspection -----------------------------------------------------
    def limitations(self) -> list:
        """Paper-grounded limitations of running on this chip at the
        current temperature. Surfaced in the dashboard so users see what the
        physics does and does not support."""
        t2_us = self._t2 * 1e6
        budget = int(self._t2 / self._g1q) if self._g1q > 0 else 0
        lim = [
            f"T2 = {t2_us:.2f} us at {self.temperature_k:.0f} K limits circuits "
            f"to ~{budget} single-qubit gates before decoherence.",
            "Deep algorithms (Shor, large multipliers) with depth >> T2/gate "
            "are NOT physically executable at room temperature — they need "
            "cryogenic cooling or quantum error correction.",
            "Within-pixel all-to-all dipole coupling is modelled -- the ~10 NVs "
            "in one microdiamond are a few nm apart, so every pair couples "
            "(1/r**3) and the 300 K on-chip 2q gate is a ~200 ns CZ (Neumann "
            "2008 / Dolde 2013); real hardware routes via the ADF4351 PLL with "
            "per-NV microwave pulses, each addressed by its detuned ODMR "
            "frequency. At 300 K the 64 pixels are INDEPENDENT (1/r**3 -> "
            "zero at the 2.5 mm pitch); cross-pixel 2q is the cryo optical bus, "
            "descriptive here (640 qubits > the 63-qubit MPS cap).",
            f"Optical readout fidelity ~{self.profile.readout_error*100:.0f}% "
            "error; on-chip CQD pump + graphene/MoS2 detector + lock-in readout "
            "(no camera).",
            "No mid-circuit measurement / fast feedback at the defect level "
            "(readout is optical, µs-scale) — error-correcting codes needing "
            "fast feedback are not supported by this control stack.",
        ]
        if self._ensemble_mode:
            lim.append(
                "Ensemble mode: the 64 chip pixels are the circuit qubits (one "
                "ensemble qubit per pixel -- the bulk-microdiamond ODMR line "
                "collapse). There is NO within-pixel 2q gate at 300 K (cross-pixel "
                "1/r**3 -> 0 at the 2.5 mm pitch); 2q circuits are rejected. "
                "Switch to register mode for within-pixel dipole 2q. The "
                "register width N is a MEASURED quantity (decompose_odmr on the "
                "ODMR spectrum), not a hardcoded count.")
        else:
            lim.append(
                "Register mode: the circuit qubits are one pixel's N-NV "
                "register (within-pixel all-to-all dipole, the ~200 ns CZ at the "
                "design spacing). N is the MEASURED resolvable ODMR line count "
                "(decompose_odmr), capped at the sim width; the 64 pixels are "
                "independent parallel processors at 300 K.")
        if self.gradient_mhz_per_um > 0.0:
            lim.append(
                f"Field gradient {self.gradient_mhz_per_um:g} MHz/um across the "
                f"{self.pixel_size_um:g} um pixel sets the register width to "
                f"{self.n_nv_per_pixel} resolvable NVs (Case D: line spacing > "
                f"linewidth {self.profile.odmr_linewidth_hz*1e-6:g} MHz). The "
                f"width is a MEASURED quantity from the ODMR spectrum, not a "
                f"fixed count; a stronger gradient resolves more ensemble qubits.")
        if self._nuclear_memory:
            adv = nuclear_memory_advantage(self._t2, self._nuclear_t2)
            lim.append(
                f"Nuclear-spin memory ENABLED: each NV is paired with a 13C "
                f"memory qubit (T2 ~{self._nuclear_t2:g} s vs the electron's "
                f"{self._t2*1e3:.3g} s, a ~{adv:.0f}x coherence stretch). "
                f"STORE on the 13C (SWAP i -> i+{self._n_electron}), RECALL back; "
                f"the 13C T2 is NOT quoted by the supplier -- a literature anchor "
                f"for an engineered single-13C (Reimer 2016).")
        if self.temperature_k >= 250:
            lim.append("Operating at ~300 K: convenient but coherence-limited. "
                       "Cool to 77 K / 4 K to extend T2 by ~2x / ~8x and unlock "
                       "deeper circuits.")
        return lim

    def fpga_compatibility(self) -> dict:
        """Check the quantum timings against the Artix-7 control FPGA.

        The FPGA clock (100 MHz => 10 ns tick) must be fine enough to synthesize
        the microwave pi-pulses and sample readout within the coherence window.
        """
        fpga_clock_mhz = 100.0
        tick_ns = 1e9 / (fpga_clock_mhz * 1e6)
        g1q = self.profile.gate_1q_ns
        g2q = self.profile.gate_2q_ns
        pulse_resolvable = g1q >= 2 * tick_ns  # at least 2 ticks per pulse
        readout_samplable = self.profile.readout_ns >= tick_ns
        within_window = (g1q + g2q) * 1e-9 < self._t2
        compatible = pulse_resolvable and readout_samplable and within_window
        return {
            "fpga": "Artix-7 XC7A35T",
            "fpga_clock_mhz": fpga_clock_mhz,
            "fpga_tick_ns": tick_ns,
            "mw_source": "ADF4351 PLL (0.035–4.4 GHz)",
            "gate_1q_ns": g1q,
            "gate_2q_ns": g2q,
            "readout_ns": self.profile.readout_ns,
            "pulse_resolvable": pulse_resolvable,
            "readout_samplable": readout_samplable,
            "within_coherence_window": within_window,
            "compatible": compatible,
            "note": ("FPGA tick (10 ns) resolves the ~%g ns microwave pulses "
                     "and the readout window fits inside T2 — the control "
                     "stack can drive the chip at this temperature."
                     % g1q) if compatible else
                    ("WARNING: a timing constraint is violated at this "
                     "temperature — see pulse_resolvable / within_coherence_window."),
        }

    def optical_readout(self) -> dict:
        """End-to-end on-chip optical readout chain budget, **recalculated for
        the backend's defect profile and temperature**.

        CQD ASE emitter -> SU-8/PDMS waveguide -> long-pass filter ->
        2D photodetector -> TIA -> 4x AD7616 16-bit parallel ADC ->
        1 kHz lock-in (host-side). The readout figures (bright/dark codes, dip,
        noise, lock-in SNR) are anchored to the *same* simulator constants the
        ``SimulatedFPGA`` uses (``BRIGHT_CODE`` is the saturated bright level),
        while the detector noise, ODMR contrast, ASE gain and waveguide loss are
        **temperature-dependent** so the panel reflects the current run, not a
        fixed snapshot. Cooling cuts the graphene dark-current/thermal noise
        (~sqrt(T)) and raises the ensemble ODMR contrast (spin polarisation),
        so the lock-in SNR climbs sharply at low T -- the readout improves cold.

        Per-profile stack: NV/hBN use a green 532 nm CQD pump + 650 nm long-pass
        + graphene/MoS2 detector; SiC V_Si uses a NIR CIS-QD pump (~916 nm) +
        850 nm long-pass + few-layer MoS2 detector (see HARDWARE_DESIGN.md §7-8
        and the three-platform fabrication protocols)."""
        T = max(0.001, float(self.temperature_k))
        nir = self.profile.excitation_wavelength_nm >= 700.0  # SiC V_Si path
        # --- per-profile optical stack --------------------------------------
        if nir:
            drive_ma, forward_v, eta_eqe = 12.0, 3.3, 0.08    # CIS-QD NIR LED
            eta_ew, eta_wd = 0.04, 0.04                        # harder NIR coupling
            wg_loss_db_cm_300, filt_t = 0.6, 0.90              # 850 nm long-pass
            filt_type, filt_blocks, filt_passes = (
                "850 nm long-pass", "NIR pump ~916 nm", "SiC PL 890-940 nm")
            det_type, responsivity = "few-layer MoS2 photodetector array", 0.08
            contrast0, linewidth_hz = 0.03, self.profile.odmr_linewidth_hz  # V_Si ensemble
        else:
            drive_ma, forward_v, eta_eqe = 10.0, 2.8, 0.12    # green CQD LED
            eta_ew, eta_wd = 0.05, 0.05
            wg_loss_db_cm_300, filt_t = 0.5, 0.92            # 650 nm long-pass
            filt_type, filt_blocks, filt_passes = (
                "650 nm long-pass", "green pump 520-540 nm", "defect PL >650 nm")
            det_type, responsivity = "graphene/MoS2 photodetector array", 0.10
            contrast0, linewidth_hz = ODMR_CONTRAST, self.profile.odmr_linewidth_hz
        # --- temperature dependence (anchored at 300 K) ---------------------
        set_resistor_ohm = 4870          # TLC5947 R_IREF (sets constant-current)
        # optical pump power out of the CQD pixel: drive_ma * forward_v is the
        # electrical input in mW (mA * V), * eta_eqe is the wall-plug -> optical
        # efficiency. (The old `* 1e-3 * 1000` here was a no-op that obscured units.)
        pump_popt_mw = drive_ma * forward_v * eta_eqe
        noise_counts = READOUT_NOISE * math.sqrt(T / 300.0)
        contrast = contrast0 * min(1.6, max(0.5, 1.0 + 0.5 * (300.0 - T) / 300.0))
        ase_gain = ASE_GAIN * min(1.25, 1.0 + 0.10 * (300.0 - T) / 300.0)
        wg_loss_db_cm = wg_loss_db_cm_300 * (0.7 + 0.3 * T / 300.0)
        wg_len_cm = 0.3                                     # ~3 mm site->detector
        eta_wg_prop = 10 ** (-(wg_loss_db_cm * wg_len_cm) / 10.0)
        eta_end = eta_ew * eta_wg_prop * eta_wd * filt_t
        p_at_det_mw = pump_popt_mw * ase_gain * eta_end
        # --- detector + TIA + XADC (BRIGHT_CODE anchored) ------------------
        tia_rf = 1.0e6
        adc_vref = 1.0
        i_bright_anchored_ua = (BRIGHT_CODE / ADC_MAX) * adc_vref / tia_rf * 1e6
        i_dark_anchored_ua = i_bright_anchored_ua * (1.0 - contrast)
        i_dip_na = (i_bright_anchored_ua - i_dark_anchored_ua) * 1e3
        dip_counts = int(round(BRIGHT_CODE * contrast))
        dark_code = int(round(BRIGHT_CODE * (1.0 - contrast)))
        snr_sigma = dip_counts / noise_counts
        return {
            "temperature_k": round(T, 3),
            "emitter": {
                "type": "CQD ASE/LED array (ITO/PEDOT:PSS/CQD-PVA/ZnO/Ag)",
                "pixels": len(self._chip_sites),
                "n_nv_per_pixel": self.n_nv_per_pixel,
                "grid": "64 parallel channels",
                "driver": "3x TLC5947 constant-current (12-bit PWM, 4-wire SPI)",
                "wavelength_nm": self.profile.excitation_wavelength_nm,
                "drive_ma": drive_ma, "forward_v": forward_v,
                "set_resistor_ohm": set_resistor_ohm,
                "wall_plug_efficiency": eta_eqe,
                "pump_optical_power_mw": round(pump_popt_mw, 2),
                "ase_gain": round(ase_gain, 3),
                "after_ase_mw": round(pump_popt_mw * ase_gain, 2),
            },
            "waveguide": {
                "material": "SU-8/PDMS polymer",
                "length_mm": wg_len_cm * 10,
                "loss_db_cm": round(wg_loss_db_cm, 3),
                "emitter_coupling": eta_ew,
                "detector_coupling": eta_wd,
                "propagation_efficiency": round(eta_wg_prop, 3),
            },
            "filter": {
                "type": filt_type,
                "transmission": filt_t,
                "blocks": filt_blocks,
                "passes": filt_passes,
            },
            "detector": {
                "type": det_type,
                "electrodes": len(self._chip_sites),
                "bias_v": 0.5,
                "responsivity_a_per_w": responsivity,
                "nep_nw_per_sqrt_hz": 50.0,
                "tia": "TLV9004 class (64 parallel channels)",
                "transimpedance_ohm": int(tia_rf),
                "adc": "4x AD7616 parallel 16-bit simultaneous-sampling",
                "adc_bits": ADC_BITS, "adc_max": ADC_MAX,
                "adc_vref_v": adc_vref,
                "range_v": "+/-5 bipolar (HW-pin-strapped)",
            },
            "readout": {
                "lockin_mod_hz": LOCKIN_MOD_HZ,
                "odmr_contrast": round(contrast, 4),
                "odmr_linewidth_hz": linewidth_hz,
                "bright_code": BRIGHT_CODE,
                "dark_code": dark_code,
                "bg_code": BG_CODE,
                "dip_counts": dip_counts,
                "readout_noise_counts": round(noise_counts, 2),
                "single_read_snr_sigma": round(snr_sigma, 2),
                "dynamic_range_pct": round(100.0 * BRIGHT_CODE / ADC_MAX, 1),
                "detect_threshold": DETECT_THRESHOLD,
                "photocurrent_bright_ua": round(i_bright_anchored_ua, 3),
                "photocurrent_dark_ua": round(i_dark_anchored_ua, 3),
                "photocurrent_dip_na": round(i_dip_na, 2),
            },
            "link_budget": {
                "pump_optical_power_mw": round(pump_popt_mw, 2),
                "after_ase_mw": round(pump_popt_mw * ase_gain, 2),
                "after_waveguide_uw": round(pump_popt_mw * ase_gain * eta_ew * eta_wg_prop * 1e3, 2),
                "after_filter_uw": round(p_at_det_mw * 1e3, 2),
                "at_detector_uw": round(p_at_det_mw * 1e3, 2),
                "end_to_end_efficiency": round(eta_end, 5),
                "reconciles_with": f"simulator BRIGHT_CODE={BRIGHT_CODE}",
            },
        }

    def describe(self) -> dict:
        budget = int(self._t2 / self._g1q) if self._g1q > 0 else 0
        return {
            "backend": "qssc_simulator",
            "defect": self.profile.name,
            "temperature_k": self.temperature_k,
            # circuit register width (one pixel's ~10-NV register) vs the
            # 64-pixel hardware die: the circuit models one register, the 64
            # pixels are independent parallel processors at 300 K (cross-pixel
            # 2q = cryo optical bus, descriptive).
            "pixel_mode": self.pixel_mode,
            # within-pixel 2q status: register mode has the dipole CZ; ensemble
            # mode has none at 300 K (cross-pixel 1/r**3 -> 0 at 2.5 mm pitch).
            "within_pixel_2q": (
                "dipole CZ @ %.2f nm" % self.register_nv_spacing_nm
                if not self._ensemble_mode
                else "none at 300 K (bulk ensemble, cross-pixel 1/r**3 -> 0)"),
            "register_n_measured": self._register_line_freqs_hz is not None,
            "num_qubits": self._n_qubits,
            "pixels": len(self._chip_sites),
            "n_nv_per_pixel": self.n_nv_per_pixel,
            "gradient_mhz_per_um": self.gradient_mhz_per_um,
            "pixel_size_um": self.pixel_size_um,
            "nuclear_memory": self._nuclear_memory,
            "n_electron_qubits": self._n_electron,
            "nuclear_t2_s": self._nuclear_t2 if self._nuclear_memory else 0.0,
            "nuclear_swap_ns": round(self._nuclear_swap_t * 1e9, 1)
                              if self._nuclear_memory else 0.0,
            "nuclear_memory_advantage": round(
                nuclear_memory_advantage(self._t2, self._nuclear_t2), 1)
                if self._nuclear_memory else 0.0,
            "t1_ms": round(self._t1 * 1e3, 3),
            "t2_us": round(self._t2 * 1e6, 3),
            "readout_error": self.profile.readout_error,
            "d_ghz": self.profile.d_ghz,
            "shots": self.shots,
            "optical_readout": self.optical_readout(),
            # real control/readout timings
            "gate_1q_ns": self.profile.gate_1q_ns,
            "gate_2q_ns": self.profile.gate_2q_ns,
            "readout_ns": self.profile.readout_ns,
            "init_ns": self.profile.init_ns,
            "depol_1q": self.profile.depol_1q,
            "depol_2q": self.profile.depol_2q,
            # physics budget + limitations + FPGA compat + citations
            "coherence_budget_gates": budget,
            "supported_gates": ["h", "x", "y", "s", "t", "rx", "ry", "rz",
                                "cx", "cz", "cp", "measure"],
            "fpga": self.fpga_compatibility(),
            "limitations": self.limitations(),
            "references": list(self.profile.references),
            # sites = the 64-pixel CHIP view (the physical die, one entry per
            # pixel, pixel_id == qubit_id, nv_index 0) -- what the Calibrate tab
            # and the 64-channel readout see. The circuit register NVs are the
            # ``num_qubits`` (~10) above.
            "sites": [self._site_view(s) for s in self._chip_sites],
            # Calibrated per-pixel register drill-down. When a
            # calibration is loaded, each pixel's MEASURED register width (the
            # resolvable ODMR line count under the gradient) is reported; the
            # uniform fallback reports the single n_nv_per_pixel for all 64.
            "per_pixel_widths": (list(self._pixel_widths)
                                 if self._pixel_widths is not None
                                 else [self.n_nv_per_pixel] * len(self._chip_sites)),
            "calibrated": self._pixel_registers is not None,
        }

    def _site_view(self, s) -> dict:
        """Per-qubit view for /api/backend/info: the resonance frequency
        (zero-field splitting + per-site strain/B/hyperfine detuning) and the
        detuning from the nominal d_ghz, which is how each qubit is individually
        addressed by a narrow microwave pulse (the addressing mechanism)."""
        d = s.as_dict()
        zfs = self.profile.zero_field_splitting
        d["resonance_ghz"] = round(s.frequency / 1e9, 6)
        d["detuning_mhz"] = round((s.frequency - zfs) / 1e6, 4)
        return d