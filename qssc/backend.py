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
import time
from typing import Dict, List, Optional, Sequence

import numpy as np

from qiskit import QuantumCircuit, transpile
from qiskit.circuit import Measure
from qiskit.circuit.library.standard_gates import (
    HGate, XGate, YGate, SGate, TGate, RZGate, RYGate, CXGate, CZGate, CPhaseGate,
)
from qiskit.providers import BackendV2, Options, QubitProperties
from qiskit.providers import JobStatus
from qiskit.transpiler import Target, InstructionProperties, CouplingMap
from qiskit_aer import AerSimulator
from qiskit_aer.noise import (
    NoiseModel, thermal_relaxation_error, depolarizing_error, ReadoutError,
)

from .defects import DefectProfile, get_profile, coherence_at_temperature
from .simulator import (
    QubitSite, SimulatedFPGA, auto_align,
    ASE_GAIN, BRIGHT_CODE, BG_CODE, READOUT_NOISE,
    LOCKIN_MOD_HZ, ODMR_CONTRAST,
    ADC_BITS, ADC_MAX, DETECT_THRESHOLD, N_SITES,
)

logger = logging.getLogger(__name__)

# Classical simulation ceiling. The chip has 64 physical detector sites (the
# 8x8 grid), but a quantum circuit is simulated classically by Aer as a
# statevector / density matrix, which is O(2^n) in memory. AerSimulator's
# default target tops out at 28 qubits (a 2^28 statevector ~4 GB); on the free
# Render worker the practical safe width is lower (see API cap). Circuits
# wider than this cannot be exactly simulated on any classical machine and
# are rejected with a clear error instead of silently returning fake
# all-zero counts. The grid still shows all 64 sites; only the *simulated*
# subset that was measured lights up.
SIM_MAX_QUBITS = 28


# ---------------------------------------------------------------------------
# Hardware abstractions (real serial + simulator share one interface)
# ---------------------------------------------------------------------------

class FPGAConnection:
    """Thin wrapper over a pyserial link to the Artix-7 board.

    pyserial is imported lazily so the package works without it installed.
    """

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 115200):
        import serial  # local import
        self.ser = serial.Serial(port, baudrate, timeout=5)
        time.sleep(2)

    def send_command(self, command: str) -> str:
        self.ser.write((command + "\n").encode())
        return self.ser.readline().decode().strip()

    def close(self):
        self.ser.close()


class EmitterController:
    """Drive the 64-channel on-chip CQD ASE/LED emitter array over the FPGA UART link.

    The FPGA drives 3x TLC5947 constant-current LED drivers over a 4-wire SPI;
    each of the 64 channels has its own 12-bit grayscale word, so multiple
    emitters light simultaneously at independent intensities. The wire commands
    (matching ``fpga/rtl/command_parser.v`` exactly)::

        DRIVE_EMITTER <index> <duration_ms>   pump one channel at full (gs=4095)
        SET_EMITTER <index> <level 0..4095>   arm one channel's grayscale
        DRIVE_PATTERN <duration_ms>           fire ALL armed channels in parallel
        CLEAR_EMITTERS                        disarm all channels (gs=0)

    There is no 8x8 row/col crossbar: every channel is independent and addressed
    by a single linear index 0..63. ``drive`` is the single-channel convenience
    wrapper; ``set_emitter`` + ``drive_pattern`` is the parallel multi-emitter
    path used by the per-run optical readout.
    """

    N_SITES = N_SITES

    def __init__(self, fpga):
        self.fpga = fpga

    def drive(self, index: int, duration_ms: int = 50):
        if not (0 <= index < self.N_SITES):
            raise ValueError("emitter index out of range")
        return self.fpga.send_command(f"DRIVE_EMITTER {index} {duration_ms}")

    def set_emitter(self, index: int, level: int):
        """Arm one channel's 12-bit grayscale (0..4095); fires on DRIVE_PATTERN."""
        if not (0 <= index < self.N_SITES):
            raise ValueError("emitter index out of range")
        if not (0 <= level <= 4095):
            raise ValueError("emitter level out of range 0..4095")
        return self.fpga.send_command(f"SET_EMITTER {index} {level}")

    def drive_pattern(self, duration_ms: int = 50):
        """Fire every armed (gs>0) channel simultaneously for duration_ms."""
        return self.fpga.send_command(f"DRIVE_PATTERN {duration_ms}")

    def clear_emitters(self):
        """Disarm all channels (gs=0)."""
        return self.fpga.send_command("CLEAR_EMITTERS")


class DetectorInterface:
    """Read the on-chip graphene/MoS2 photodetector array over the FPGA UART link.

    Each electrode's TIA output is digitised in parallel by 4x AD7616 16-bit
    simultaneous-sampling ADCs. ``READ_DETECTOR <site>`` returns one 16-bit
    sample (0..65535); ``READ_DETECTORS`` streams all 64 samples
    (space-separated) in a single reply -- the 4 ADCs sample together, so all
    64 channels are captured simultaneously, not scanned.
    """

    N_SITES = N_SITES

    def __init__(self, fpga):
        self.fpga = fpga

    def read(self, site: int) -> int:
        reply = self.fpga.send_command(f"READ_DETECTOR {int(site)}")
        return int(reply)

    def read_all(self) -> np.ndarray:
        reply = self.fpga.send_command("READ_DETECTORS")
        codes = [int(tok) for tok in reply.split()]
        return np.array(codes, dtype=np.int32)

    def read_odmr(self, site: int) -> int:
        """Convenience: single-site 16-bit sample (alias of ``read``)."""
        return self.read(site)


class MicrowaveController:
    """Microwave pulse driver over the FPGA UART link.

    The wire protocol matches ``fpga/rtl/command_parser.v`` and
    ``hardware/HARDWARE_DESIGN.md`` §5 exactly::

        APPLY_PULSE <qubit:int> <freq_khz:int> <amp:int> <duration_ns:int> <phase:int>\\n

    All five fields are decimal integers — the firmware parser rejects decimal
    points and underscores, so a float GHz value or a ``r_c`` two-qubit token
    would be answered ``ERR``.  This class keeps the Python-facing API in the
    natural Qiskit units (GHz / fraction / radians) and converts to the integer
    wire format on the way out, so the *same call* drives the
    :class:`~qssc.simulator.SimulatedFPGA` and a real Artix-7 board.

    Field encoding on the wire:

    * ``qubit``       — site index 0..63 (single int; two-qubit gates are not a
                        hardware primitive — see ``QSSCBackend.limitations()``).
    * ``freq_khz``    — RF frequency in kHz, e.g. ``3460000`` for 3.46 GHz.
    * ``amp``         — ADF4351 output power index 0..3 (``amp`` fraction × 3).
    * ``duration_ns`` — pulse duration in ns (parsed; reserved by firmware for
                        a future RF-gate extension).
    * ``phase``       — pulse phase in **milliradians** (0 = +X, 3142 ≈ π).
    """

    def __init__(self, fpga, ref_hz: float = 25e6):
        self.fpga = fpga
        self.ref_hz = ref_hz

    def apply_pulse(self, qubit, freq_ghz: float, amp: float,
                    duration_ns: int, phase: float = 0.0):
        if not isinstance(qubit, (int, np.integer)):
            raise ValueError(
                "qubit must be a single site index (int 0..63); two-qubit "
                "gates are not a hardware primitive — see QSSCBackend.limitations()")
        if not (0.0 <= amp <= 1.0):
            raise ValueError("amp out of [0,1]")
        freq_khz = int(round(freq_ghz * 1e6))            # GHz  -> kHz (int)
        amp_idx = int(round(max(0.0, min(1.0, amp)) * 3))  # [0,1] -> 0..3 power index
        phase_mrad = int(round(phase * 1000))            # radians -> milliradians (int)
        return self.fpga.send_command(
            f"APPLY_PULSE {int(qubit)} {freq_khz} {amp_idx} {int(duration_ns)} {phase_mrad}"
        )


def make_connection(port: Optional[str] = None, *, profile="NV",
                    temperature_k: float = 300.0, seed: Optional[int] = None):
    """Return a real or simulated FPGA connection.

    ``port=None`` (or ``--mock``) yields a :class:`SimulatedFPGA`; otherwise a
    real :class:`FPGAConnection` is opened on ``port``.
    """
    if port is None:
        return SimulatedFPGA(profile=profile, temperature_k=temperature_k, seed=seed)
    return FPGAConnection(port)


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
                 noise_mode: str = "real"):
        """noise_mode: 'real' (default, full Aer thermal+depolarizing+readout
        noise from the defect physics) | 'phenomenological' (fast analytic
        approximation) | 'none' (ideal). 'aer' is accepted as an alias of 'real'.
        """
        super().__init__(name="qssc_simulator")
        prof = get_profile(profile) if isinstance(profile, str) else profile
        self.profile = prof
        self.temperature_k = temperature_k
        self.hardware = hardware
        self.shots = shots
        self.seed = seed
        if sites is None:
            sites = auto_align(prof, grid=8, temperature_k=temperature_k,
                               rng=np.random.default_rng(seed))
        self.sites = list(sites)
        self._n_qubits = len(self.sites)
        t1, t2 = coherence_at_temperature(prof, temperature_k)
        self._t1, self._t2 = t1, t2
        # real control/readout timings from the defect profile
        self._g1q = prof.gate_1q_ns * 1e-9
        self._g2q = prof.gate_2q_ns * 1e-9
        self._readout_t = prof.readout_ns * 1e-9
        self._init_t = prof.init_ns * 1e-9
        qprops = [QubitProperties(t1=s.t1, t2=s.t2, frequency=prof.zero_field_splitting)
                  for s in self.sites]
        coupling = CouplingMap([(i, j) for i in range(self._n_qubits)
                                for j in range(self._n_qubits) if i != j])
        self._target = Target(dt=1e-9, qubit_properties=qprops,
                              num_qubits=self._n_qubits, coupling_map=coupling)
        self._build_target()
        self._seed = seed
        self.noise_mode = noise_mode
        self._options = self._default_options()
        self._job_counter = 0
        # above this many active qubits a full density-matrix noise run is
        # infeasible (O(4^n) memory); we fall back to the analytic decoherence
        # limit computed from the SAME real T2/gate-time parameters.
        self._dm_qubit_limit = 10

    # -- target / noise ----------------------------------------------------
    def _build_target(self):
        sq = {(i,): InstructionProperties(duration=self._g1q,
                                          error=self.profile.depol_1q)
              for i in range(self._n_qubits)}
        for g in (HGate(), XGate(), YGate(), SGate(), TGate(),
                  RZGate(__import__("qiskit").circuit.Parameter("theta")),
                  RYGate(__import__("qiskit").circuit.Parameter("theta"))):
            self._target.add_instruction(g, sq)
        tq = {(i, j): InstructionProperties(duration=self._g2q,
                                            error=self.profile.depol_2q)
              for i in range(self._n_qubits) for j in range(self._n_qubits) if i != j}
        for g in (CXGate(), CZGate(),
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
        dep1, dep2 = self.profile.depol_1q, self.profile.depol_2q
        one_q_gates = ["x", "y", "h", "rz", "ry", "s", "t", "sx"]
        for q in active:
            s = self.sites[q]
            t1 = s.t1 or self._t1
            t2 = s.t2 or self._t2
            ro = float(s.readout_err)
            try:
                therm1 = thermal_relaxation_error(t1, t2, g1q)
                dep1err = depolarizing_error(dep1, 1)
                nm.add_quantum_error(therm1.compose(dep1err),
                                     one_q_gates, [q])
                for j in active:
                    if j == q:
                        continue
                    s2 = self.sites[j]
                    ej = thermal_relaxation_error(s2.t1 or self._t1,
                                                  s2.t2 or self._t2, g2q)
                    # BOTH qubits relax over the *two-qubit* gate duration;
                    # therm1 above is over g1q and would under-relax the
                    # control by ~g2q/g1q (~8x for these profiles). (#7)
                    therm_q_2q = thermal_relaxation_error(t1, t2, g2q)
                    two_q_therm = therm_q_2q.tensor(ej)
                    dep2err = depolarizing_error(dep2, 2)
                    nm.add_quantum_error(two_q_therm.compose(dep2err),
                                         ["cx", "cz", "cp", "swap"], [q, j])
            except Exception as exc:  # pragma: no cover
                logger.debug("real noise build skipped pair %s,%s: %s", q, q, exc)
            nm.add_readout_error(ReadoutError([[1 - ro, ro], [ro, 1 - ro]]), [q])
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

    # -- execution ---------------------------------------------------------
    def _execute(self, circuit: QuantumCircuit, options: dict):
        shots = int(options.get("shots", self.shots))
        job_id = f"qssc-{self._job_counter:04d}"
        mode = options.get("noise_mode", self.noise_mode)
        if circuit.num_qubits > SIM_MAX_QUBITS:
            raise ValueError(
                f"circuit uses {circuit.num_qubits} qubits, but the classical "
                f"simulator is bounded at {SIM_MAX_QUBITS} (a 2^{circuit.num_qubits} "
                f"statevector cannot be held in memory). The chip has 64 physical "
                f"sites; the simulator runs circuits on a classical subset. Use a "
                f"smaller register or a lower N.")
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
        """Fast ideal statevector simulation via Aer (no noise model).

        Raises on failure rather than silently returning fake all-zero
        counts -- a circuit that Aer cannot simulate (too wide, unsupported
        op...) must surface as a real error, not a fabricated |0...0>
        result that would mislead the dashboard into showing "everything
        measured zero". The width guard in :meth:`_execute` rejects
        over-wide circuits first; this catch-all only fires for unexpected
        Aer failures.
        """
        aer = AerSimulator(seed_simulator=self._seed)
        t_circ = transpile(circuit, aer, optimization_level=1)
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
        _, _, n_compiled, _, _ = self._decomposed_cost(circuit)
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

        Returns ``(n_1q, n_2q, n_compiled_blocks, quantum_time_s)`` where
        ``quantum_time_s`` is the estimated duration of the quantum evolution
        (1q + 2q gate times, no readout). Compiled high-level blocks (Shor's
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
        # `factor` is the cost of a reference width-4 instance, so each block's
        # cost is scaled by (w/4)^2 -- this is why increasing the number of
        # qubits/bits makes the operation slower (longer decomposed duration)
        # and pushes it further past the T2 coherence window.
        block_factor = {
            "unitary": 256, "c-unitary": 256,          # mod-mult / controlled-U
            "IQFT": 80, "QFT": 80, "qft": 80,          # QFT on control register
            "rgqft_multiplier": 128, "CDKMRippleCarryAdder": 96,
            "DraperQFTAdder": 64, "PhaseEstimation": 160,
        }
        n_compiled = 0
        decomp_2q = 0
        for inst in circuit.data:
            name = inst.operation.name
            if name in one_q or name in two_q or name in ("measure", "barrier"):
                continue
            factor = block_factor.get(name, 64)  # generic compiled block
            # block width (qubits): scale cost quadratically with width.
            try:
                w = int(inst.operation.num_qubits)
            except Exception:  # noqa: BLE001
                w = len(inst.qubits)
            w = max(2, w)
            n_compiled += 1
            decomp_2q += factor * (w * w) / 16.0
        est_2q = n2q + decomp_2q
        est_1q = n1q + est_2q * 2
        quantum_time_s = est_1q * self._g1q + est_2q * self._g2q
        return n1q, n2q, n_compiled, quantum_time_s, est_2q

    def assess_circuit(self, circuit: QuantumCircuit) -> dict:
        """Physical-feasibility assessment of a circuit on this chip.

        Compares the circuit's estimated *decomposed* execution time against the
        coherence budget T2, and reports whether the algorithm fits within the
        coherence window — the key limitation of a 300 K spin-defect chip.
        """
        depth = max(circuit.depth(), 1)
        n1q, n2q, n_compiled, quantum_time_s, est_2q = self._decomposed_cost(circuit)
        duration_s = quantum_time_s + self._readout_t
        crit_path_s = depth * self._g1q + max(n2q, 0) * (self._g2q - self._g1q)
        t2 = self._t2
        budget_gates = int(t2 / self._g1q) if self._g1q > 0 else 0
        damping = 1.0 - math.exp(-duration_s / max(t2, 1e-15))
        within = duration_s <= t2
        if n_compiled > 0 and not within:
            verdict = ("contains compiled blocks (modular exponentiation / QFT / "
                       "arithmetic) whose decomposed depth (~%g 2q gates, ~%g us) "
                       "far exceeds the room/coherence budget — NOT physically "
                       "executable at this temperature without cooling or QEC"
                       % (est_2q, round(quantum_time_s * 1e6, 1)))
        elif within:
            verdict = ("fits within the T2 coherence window — physically "
                       "executable at this temperature")
        elif damping < 0.5:
            verdict = ("partially decohered at this temperature — result is "
                       "T2-limited; cool the chip for higher fidelity")
        else:
            verdict = ("exceeds the room/coherence budget — not physically "
                       "executable at this temperature without cooling or "
                       "quantum error correction")
        return {
            "depth": depth,
            "n_1q": n1q, "n_2q": n2q,
            "n_compiled_blocks": n_compiled,
            "estimated_2q_decomposed": est_2q,
            "logical_duration_us": round(crit_path_s * 1e6, 4),
            "duration_ns": round(duration_s * 1e9, 1),
            "duration_us": round(duration_s * 1e6, 4),
            "t2_us": round(t2 * 1e6, 4),
            "coherence_budget_gates": budget_gates,
            "decoherence_fraction": round(damping, 4),
            "within_coherence": within,
            "verdict": verdict,
        }

    def _apply_phenomenological_noise(self, circuit: QuantumCircuit,
                                      ideal_counts: dict, shots: int) -> dict:
        """Fast, tunable noise applied to the ideal measurement distribution.

        Three effects, all derived from the defect profile and the per-site
        properties:

        * **Readout error** -- each measured bit of each shot is flipped with
          probability ``site.readout_err`` (optical fluorescence readout).
        * **Coherence damping** -- the circuit duration is estimated from its
          depth and gate times; the distribution is blended with the maximally
          mixed distribution by ``(1 - exp(-t_circuit / T2))``, modelling T2
          dephasing over the sequence.
        * **Cumulative gate depolarization** -- every basis gate carries the
          profile's ``depol_1q`` / ``depol_2q`` channel in the "real"
          density-matrix path; the analytic path composes the same
          probabilities over the decomposed gate counts and folds them into
          the same uniform blend, so deep circuits do not come out
          under-noised relative to the "real" mode (#7).

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
        err_per_cl = np.zeros(nbits)
        for cl, pq in meas_map.items():
            err_per_cl[cl] = float(self.sites[pq].readout_err) if pq < len(self.sites) else self.profile.readout_error
        # bit i (from LSB) corresponds to classical bit cl = nbits-1-i
        flip = rng.random((shots, nbits)) < err_per_cl[::-1][None, :]
        noisy_int = np.copy(drawn)
        for i in range(nbits):
            noisy_int = np.where(flip[:, i], np.bitwise_xor(noisy_int, 1 << i), noisy_int)
        # --- coherence damping: blend with uniform --------------------------
        # Use the SAME decomposed quantum-evolution time as assess_circuit, so
        # the analytic limit agrees with the physical-feasibility verdict (a
        # Shor run that "is NOT physically executable at 300 K" must decohere to
        # a near-uniform distribution here, not recover its factors).
        n1q_raw, _, _, t_circuit, est_2q = self._decomposed_cost(circuit)
        t2 = self._t2
        damping = 1.0 - math.exp(-t_circuit / max(t2, 1e-12))
        # Cumulative per-gate depolarization (#7): the DM path composes a
        # depolarizing channel onto EVERY basis gate, but this analytic path
        # carried only T2 damping -- deep circuits came out under-noised
        # relative to the "real" mode. Compose the per-gate probabilities the
        # same way the channels compose (independent identity survivals):
        #   identity = (1-lam1)^est_1q * (1-lam2)^est_2q
        # and fold the result into the same uniform blend (a fully depolarized
        # register and a fully dephased one both end uniform over the readout).
        # qiskit's depolarizing_error(p, n) mixes to I/2^n with probability
        # lam = p * 4^n / (4^n - 1), so convert the profile params the same
        # way the DM path's channels do.
        est_1q = n1q_raw + est_2q * 2          # mirrors _decomposed_cost
        dep1, dep2 = self.profile.depol_1q, self.profile.depol_2q
        lam1 = dep1 * 4.0 / 3.0                # 1q: p * 4 / 3
        lam2 = dep2 * 16.0 / 15.0              # 2q: p * 16 / 15
        identity = (1.0 - lam1) ** max(int(est_1q), 0) * (1.0 - lam2) ** max(int(est_2q), 0)
        depol_mix = 1.0 - identity
        # combine the two uniform-mixing channels: 1-(1-a)(1-b)
        p_uniform = 1.0 - (1.0 - damping) * (1.0 - depol_mix)
        uniform = rng.integers(0, 2 ** nbits, size=shots)
        final_int = np.where(rng.random(shots) < p_uniform, uniform, noisy_int)
        # --- back to counts --------------------------------------------------
        unique, cnts = np.unique(final_int, return_counts=True)
        counts = {format(int(v), f"0{nbits}b"): int(c) for v, c in zip(unique, cnts)}
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
                if str_idx < len(bitstring) and pq < self._n_qubits:
                    bit = int(bitstring[str_idx] == "1")
                    det = lut.get(pq, pq)
                    # Arm this channel (SET_EMITTER) and set the spin state to
                    # the measured bit. All armed channels fire together below.
                    emitter.set_emitter(pq, 4095)
                    self.hardware.sim_states[pq] = bit
                    measured_pqs.append((pq, det))
                    measured_pqset.add(pq)
            # Fire every armed emitter in parallel, then read all 64 detectors
            # in one parallel reply -- the 4x AD7616 sample simultaneously.
            emitter.drive_pattern(50)
            codes = detector.read_all()
            states = proc.process(codes, lut, num_qubits=self._n_qubits)
            # electrodes that were never armed read the background floor (below
            # threshold), which process() would threshold as state 1 (dark).
            # Mark them -1 so "not read this run" is distinguishable from "read
            # as |1>".
            for i in range(self._n_qubits):
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
            "All-to-all microwave coupling is modelled; real hardware routes "
            "via the ADF4351 PLL with per-site pulses (no native 2-qubit gate "
            "between arbitrary distant sites without mediating modes).",
            f"Optical readout fidelity ~{self.profile.readout_error*100:.0f}% "
            "error; on-chip CQD pump + graphene/MoS2 detector + lock-in readout "
            "(no camera).",
            "No mid-circuit measurement / fast feedback at the defect level "
            "(readout is optical, µs-scale) — error-correcting codes needing "
            "fast feedback are not supported by this control stack.",
        ]
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
                "pixels": self._n_qubits, "grid": "64 parallel channels",
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
                "electrodes": self._n_qubits,
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
            "num_qubits": self._n_qubits,
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
            "sites": [s.as_dict() for s in self.sites],
        }