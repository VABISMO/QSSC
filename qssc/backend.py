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
from .simulator import (
    QubitSite, SimulatedFPGA, auto_align,
    ASE_GAIN, BRIGHT_CODE, BG_CODE, READOUT_NOISE,
    LOCKIN_MOD_HZ, ODMR_CONTRAST,
    ADC_BITS, ADC_MAX, DETECT_THRESHOLD, N_SITES,
)

logger = logging.getLogger(__name__)

# Classical simulation ceiling = the full 64-site chip. Circuits up to 28
# qubits use Aer's statevector method (exact, O(2^n) memory -- 2^28 ~ 4 GB).
# Wider circuits (29..64) use the matrix_product_state (MPS) method, which is
# O(n * chi^2) instead of O(2^n): a 2^64 statevector is exabytes (impossible),
# but low-entanglement circuits (H/product states, GHZ, QFT) keep a small MPS
# bond dimension chi and simulate 64 qubits in under a second. High-entanglement
# circuits (Shor mod-exp, multipliers) blow chi up and are capped separately by
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
    * ``duration_ns`` — pulse duration in ns. The firmware arms an RF-switch
                        gate timer (100 MHz -> 10 ns/tick) for exactly
                        ``duration_ns`` after the ADF4351 locks, so the pulse
                        width is faithful to the request (the iverilog
                        testbench verifies a 500 ns APPLY_PULSE gates mw_gate
                        for exactly 50 clk cycles).
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
        # real control/readout timings from the defect profile
        self._g1q = prof.gate_1q_ns * 1e-9
        self._g2q = prof.gate_2q_ns * 1e-9
        self._readout_t = prof.readout_ns * 1e-9
        self._init_t = prof.init_ns * 1e-9
        # Per-qubit transition frequency: each site carries its own detuned
        # frequency (strain + local B + 14N hyperfine shift around the zero-
        # field splitting), so the QubitProperties report the REAL per-qubit
        # frequency -- the individual microwave-addressing fingerprint -- not a
        # single uniform 2.87 GHz for every qubit.
        qprops = [QubitProperties(t1=s.t1, t2=s.t2, frequency=s.frequency)
                  for s in self.sites]
        # Nearest-neighbour dipole coupling: real NV magnetic dipole-dipole
        # falls as 1/r**3, so only grid-adjacent (Manhattan-distance-1) sites
        # couple -- not all-to-all. This coupling map is the physical topology
        # (truthful Target metadata). Execution transpilation still targets
        # AerSimulator (all-to-all) so NO SWAP routing overhead is inserted:
        # the defect-spin compute route uses directly-coupled qubit sets, not
        # a SWAP-routed surface code, and adding ~3-5x SWAP 2q-gate overhead
        # would push NV's depolarization budget below the postprocess cutoff
        # (wrong physics -- NV computes Shor-15 at 300 K; see
        # qssc-spin-gate-not-photon). The 2q noise model below still noised
        # every executed 2q gate (all active pairs) so a logical cx on non-
        # neighbour qubits is never silently noiseless.
        coupling = CouplingMap(self._neighbor_pairs())
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
    def _neighbor_pairs(self) -> list:
        """Directed (i, j) coupling edges for the grid: Manhattan-distance-1
        neighbours over all sites (the 8x8 grid is always complete, so dead /
        unusable sites remain topology nodes -- a real dead NV site is still a
        physical grid location, just non-participating). Real NV magnetic
        dipole-dipole falls as 1/r**3, so only nearest neighbours couple."""
        pairs = []
        n = len(self.sites)
        for i in range(n):
            si = self.sites[i]
            for j in range(i + 1, n):
                sj = self.sites[j]
                if abs(si.row - sj.row) + abs(si.col - sj.col) == 1:
                    pairs.append((i, j))
                    pairs.append((j, i))
        return pairs

    def _build_target(self):
        sq = {(i,): InstructionProperties(duration=self._g1q,
                                          error=self.profile.depol_1q)
              for i in range(self._n_qubits)}
        for g in (HGate(), XGate(), YGate(), SGate(), TGate(),
                  RZGate(__import__("qiskit").circuit.Parameter("theta")),
                  RYGate(__import__("qiskit").circuit.Parameter("theta"))):
            self._target.add_instruction(g, sq)
        # 2q gates exist only on grid-neighbour pairs (the physical dipole
        # coupling topology from _neighbor_pairs), so the Target truthfully
        # declares where a native 2q gate lives. SwapGate is added so a real
        # transpiler routing against this Target can insert SWAPs on neighbour
        # edges (execution here still routes via AerSimulator, so no SWAP
        # overhead is paid -- see the __init__ coupling comment).
        nbr = set(self._neighbor_pairs())
        tq = {(i, j): InstructionProperties(duration=self._g2q,
                                            error=self.profile.depol_2q)
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
                    # Both qubits sit idle for the full 2q gate duration, so the
                    # 2q thermal channel must be built from g2q (not the 1q
                    # therm1) for the control qubit too -- previously the control
                    # was relaxed over g1q, under-relaxing it by gate_2q/gate_1q
                    # (~8x on NV/hBN, ~7.5x on SiC).
                    therm_q = thermal_relaxation_error(t1, t2, g2q)
                    ej = thermal_relaxation_error(s2.t1 or self._t1,
                                                  s2.t2 or self._t2, g2q)
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
        _, _, n_compiled, _, _, _ = self._decomposed_cost(circuit)
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
        return n1q, n2q, n_compiled, quantum_time_s, est_2q, est_1q

    def _signal_survival(self, circuit: QuantumCircuit) -> tuple:
        """Shared signal-retention estimate used by :meth:`assess_circuit` and
        the phenomenological blend so the two paths cannot disagree.

        ``signal_retention = coherence_survival * depolarization_survival``
        where ``coherence_survival = exp(-t_circuit/T2)`` (spin T2 dephasing
        over the decomposed quantum-evolution time) and
        ``depolarization_survival = (1-depol_1q)**est_1q * (1-depol_2q)**est_2q``
        (cumulative per-gate drive depolarization, temperature-independent).
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
        hom_visibility, signal_retention, t_circuit_s, est_1q, est_2q)``.
        """
        _, _, _, t_circuit, est_2q, est_1q = self._decomposed_cost(circuit)
        t2 = max(self._t2, 1e-15)
        coherence_survival = math.exp(-t_circuit / t2)
        d1 = self.profile.depol_1q
        d2 = self.profile.depol_2q
        depol_survival = ((1.0 - d1) ** max(est_1q, 0)
                         * (1.0 - d2) ** max(est_2q, 0))
        signal_retention = coherence_survival * depol_survival
        # Optical HOM-visibility budget for the REMOTE photon-entanglement
        # channel -- descriptive only, NOT part of the on-chip spin-gate
        # signal_retention. V=1 (no photon channel) when the profile has no
        # optical model or at cryo (transform-limited ideal); V~2e-7 at 300 K NV.
        optical_survival = self._hom_v ** max(est_2q, 0)
        return (coherence_survival, depol_survival, optical_survival,
                self._hom_v, signal_retention, t_circuit, est_1q, est_2q)

    def assess_circuit(self, circuit: QuantumCircuit) -> dict:
        """Physical-feasibility assessment of a circuit on this chip.

        Compares the circuit's estimated *decomposed* execution time against the
        coherence budget T2, and reports whether the algorithm fits within the
        coherence window — the key limitation of a 300 K spin-defect chip.
        """
        depth = max(circuit.depth(), 1)
        n1q, n2q, n_compiled, quantum_time_s, est_2q, est_1q = self._decomposed_cost(
            circuit)
        duration_s = quantum_time_s + self._readout_t
        crit_path_s = depth * self._g1q + max(n2q, 0) * (self._g2q - self._g1q)
        t2 = self._t2
        budget_gates = int(t2 / self._g1q) if self._g1q > 0 else 0
        damping = 1.0 - math.exp(-duration_s / max(t2, 1e-15))
        within = duration_s <= t2
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
            err_per_cl[cl] = float(self.sites[pq].readout_err) if pq < len(self.sites) else self.profile.readout_error
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
                    ro = float(self.sites[pq].readout_err) if pq < len(self.sites) else self.profile.readout_error
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
                ro = float(self.sites[pq].readout_err) if pq < len(self.sites) else self.profile.readout_error
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
            "Nearest-neighbour dipole coupling (grid Manhattan-distance-1) is "
            "modelled -- real NV magnetic dipole-dipole falls as 1/r**3, so "
            "only adjacent sites couple; real hardware routes via the ADF4351 "
            "PLL with per-site microwave pulses, each addressed by the qubit's "
            "detuned transition frequency (no native 2-qubit gate between "
            "arbitrary distant sites without mediating modes).",
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