// Static configuration tables for the QSSC dashboard.
//
// Pure data (no React): firmware command fallback list, defect profiles,
// algorithm catalogue, noise/anti-noise mode presets, pulse-sequence list,
// per-algorithm qubit-count configuration, and the custom-circuit example
// snippets. Extracted from App.jsx so the component tree imports data, not a
// 2000-line god-file.

// Fallback firmware command list (all 17 verbs). Used in the "Reply & readout"
// empty state when the API isn't running or is a stale build serving fewer
// commands, so every command we added is always visible. The live API list
// (fpga.protocol) takes precedence when it serves the full set.
export const FALLBACK_PROTOCOL = [
  { cmd: 'DRIVE_EMITTER', args: 'index duration_ms', reply: 'OK / ERR', desc: 'pump one CQD emitter at full grayscale (gs=4095)' },
  { cmd: 'SET_EMITTER', args: 'index level(0..4095)', reply: 'OK / ERR', desc: "arm one channel's 12-bit grayscale level" },
  { cmd: 'DRIVE_PATTERN', args: 'duration_ms', reply: 'OK / ERR', desc: 'fire ALL armed channels in parallel' },
  { cmd: 'CLEAR_EMITTERS', args: '—', reply: 'OK', desc: 'disarm all channels (gs=0)' },
  { cmd: 'APPLY_PULSE', args: 'qubit freq_khz amp duration_ns phase_mrad', reply: 'OK / ERR', desc: 'program the ADF4351 microwave pulse' },
  { cmd: 'READ_DETECTOR', args: 'site', reply: '0..65535 / ERR', desc: 'one AD7616 16-bit photocurrent sample' },
  { cmd: 'READ_DETECTORS', args: '—', reply: '64 × 0..65535', desc: 'all 64 detector channels in one parallel frame' },
  { cmd: 'CLEAR_DETECTORS', args: '—', reply: 'OK', desc: 'no-op on the ADC (no counters to clear)' },
  { cmd: 'CALIBRATE', args: '—', reply: 'OK', desc: 'emitter→site→detector sweep, builds the readout LUT' },
  { cmd: 'READ_RNG', args: 'n_bytes', reply: '<hex> <raw> <kept> <rate> / ERR', desc: 'harvest n_bytes of von-Neumann-debiased AD7616 LSB-noise entropy (qrng_entropy.v)' },
  { cmd: 'ODMR_SWEEP', args: 'qubit f_start_khz f_stop_khz n_points', reply: '<n> f0 c0 f1 c1 … / ERR', desc: 'sweep the ADF4351 across the NV resonance, one AD7616 sample per point' },
  { cmd: 'READ_COMPASS', args: '[n_avg]', reply: '<B_nT> <heading°> <elev°> <sens_µT> <axes> / ERR', desc: '4-axis ODMR doublet fit → ambient B-vector + heading (sim verb; no RTL — hardware mode runs host-side via /api/compass)' },
  { cmd: 'READ_GYRO', args: 'T_ns', reply: '<rate_dps> <noise_dps> <t2_µs> <grade> / ERR', desc: 'Ramsey π/2–T–π/2 → rotation rate (sim convenience verb; hardware runs RUN_SEQUENCE + on-host fit)' },
  { cmd: 'READ_LOCKIN', args: 'ref_khz n_cycles', reply: '<diff> <sigma> <snr1> <snrN> / ERR', desc: 'lock-in accumulator: on-minus-off differential over n cycles (sim; hardware READ_LOCKIN <site> <n> → raw on/off sums, host computes SNR)' },
  { cmd: 'RUN_SEQUENCE', args: 'kind n_windows', reply: '<n> c0 c1 … / ERR', desc: 'pulse-sequence engine: ramsey / hahn / cpmg → n readout codes (sim form; hardware RUN_SEQUENCE <site> <kind> <n> <t_step>)' },
  { cmd: 'STATUS', args: '—', reply: 'PLL_LOCKED … VCC3V3_mv … TEMP_C …', desc: 'board self-status: PLL lock, ADC busy, rail voltages, die temperature' },
  { cmd: 'RESET', args: '—', reply: 'OK', desc: 'soft reset of the TLC5947 / ADF4351 / ADC peripheral state' },
  { cmd: 'ID', args: '—', reply: 'QSSC v1', desc: 'board-present/identity probe — a flashed QSSC board replies QSSC v1 (the host sends it on open to detect a live board)' },
  { cmd: 'RUN_ALL_ODMR', args: 'f_start_khz f_stop_khz n_points', reply: 'n × (64 × 0..65535) + OK / ERR', desc: '64-parallel calibration sweep: ADF4351 sweep + all 64 AD7616 channels per point → one ODMR spectrum per pixel (register width per column)' },
  { cmd: 'READ_LOCKINP', args: 'site n_samples prime_idx', reply: '<I> <Q> <n> / ERR', desc: 'prime-period IQ lock-in (prime_idx 0–15 selects p = 2, 3, 5 … 53); identical wire form in sim and hardware' },
  { cmd: 'SET_GRADIENT', args: 'code(0..4095)', reply: 'OK / ERR', desc: 'program the gradient-coil DAC (MCP4921) — 0..4095 code space, 3.3 V full scale (issue #168-C)' },
]

export const PROFILES = [
  { id: 'NV', label: 'Diamond · NV centre (default / compute)' },
  { id: 'hBN-VB', label: 'hBN · V_B⁻ (boron-vacancy · sensor)' },
  { id: 'SiC-VSi', label: 'SiC · V_Si (silicon vacancy · sensor)' },
]
export const ALGOS = ['custom', 'shor', 'period', 'bell', 'math', 'grover', 'qft', 'qftnos', 'qrand', 'bb84']
export const ALGO_LABELS = {
  custom: 'Custom circuit', shor: 'Shor (factoring)', period: 'Period finding',
  bell: 'Bell pair', math: 'Arithmetic', grover: 'Grover search',
  qft: 'QFT — Quantum Fourier Transform',
  qftnos: 'QFT-NOS — lightweight QFT factoring',
  qrand: 'Quantum RNG → crypto keys / cert',
  bb84: 'BB84 — quantum key distribution',
}
export const MATH_OPS = ['add', 'sub', 'mul', 'sqrt', 'log']
export const NOISE_MODES = [
  { id: 'real', label: 'Simulator (full physics)' },
  { id: 'phenomenological', label: 'Simulator (fast approx.)' },
  { id: 'none', label: 'Simulator (ideal, no noise)' },
]
// Anti-noise filter-stack presets (classical, 300 K, host-side / passive — no
// cryo, no new RTL, no PCB respin). "off" sends no dict (bit-identical back-
// compat). passive = vibration isolation + EMI shield + common-mode; active =
// + thermal servo + feed-forward; full = + XY8 dynamical decoupling (extends
// T2, capped at 2·T1). The stack attenuates the channels modelled by noise_env,
// so it restores the readout error a noisy lab raises.
export const ANTI_NOISE_PRESETS = {
  passive: { vibration_iso_residual: 0.1, emi_shield_residual: 0.05, common_mode_reject: true },
  active: { thermal_servo_gain: 10.0, vibration_iso_residual: 0.1, emi_shield_residual: 0.05,
            feed_forward_rejection: 0.9, common_mode_reject: true,
            feed_forward_channels: ['thermal', 'current'] },
  full: { thermal_servo_gain: 10.0, vibration_iso_residual: 0.1, emi_shield_residual: 0.05,
          feed_forward_rejection: 0.9, common_mode_reject: true, dd_sequence: 'xy8',
          dd_n_blocks: 4, feed_forward_channels: ['thermal', 'current'] },
}
export const SEQS = [
  { id: 'ramsey', label: 'Ramsey fringes' },
  { id: 'odmr', label: 'CW ODMR dip' },
  { id: 'pulse', label: 'Spin readout decay' },
]

// Per-algorithm qubit-count configuration. The number of qubits is editable for
// EVERY algorithm: for shallow algorithms (bell, qrand, qft, grover, math) it is
// the direct circuit width; for shor / period / qftnos the circuit width is a
// function of the integer N being factored (shor/period = 3·bitlen(N)+1,
// qftnos = 2·bitlen(N)+1), so the chosen qubit count is inverted to a composite
// N of the matching bit-length; for bb84 each round uses 1 qubit so "qubits"
// = the number of key-exchange rounds.
export const QUBIT_CFG = {
  custom:  null,                                  // custom circuit defines its own width
  bell:    { min: 2, max: 64, step: 1, def: 4,  label: 'Qubits n',         hint: 'GHZ chain on n qubits (H + CNOT ladder) — one pixel’s NV register, MPS for n>28' },
  qrand:   { min: 2, max: 64, step: 1, def: 8,  label: 'Qubits n',         hint: 'n qubits prepared in |+⟩ → von-Neumann-extracted entropy — one pixel’s NV register, MPS for n>28' },
  qft:     { min: 2, max: 64, step: 1, def: 4,  label: 'Qubits n',         hint: 'QFT of an n-qubit computational-basis state — one pixel’s NV register, MPS for n>28' },
  grover:  { min: 2, max: 8,  step: 1, def: 3,  label: 'Search qubits n',  hint: 'search space of 2ⁿ items' },
  math:    { min: 2, max: 6,  step: 1, def: 4,  label: 'Word qubits n',    hint: 'n-bit arithmetic register' },
  // shor / period / qftnos: the qubit count is a CONSEQUENCE of the integer N
  // being factored (shor/period = 3·bitlen(N)+1, qftnos = 2·bitlen(N)+1), so the
  // operator edits N directly and the circuit width is shown as a derived readout
  // (no universal qubit field for these — see the dedicated N row below).
  shor:    null,
  period:  null,
  qftnos:  null,
  bb84:    { min: 16, max: 2048, step: 16, def: 256, label: 'Rounds (1 qubit each)', hint: 'BB84 prepare-and-measure rounds' },
}

export const CUSTOM_EXAMPLES = {
  bell: `from qiskit import QuantumCircuit
qc = QuantumCircuit(2, 2)
qc.h(0)
qc.cx(0, 1)
qc.measure(range(2), range(2))`,
  ghz: `from qiskit import QuantumCircuit
qc = QuantumCircuit(4, 4)
qc.h(0)
for i in range(3):
    qc.cx(i, i + 1)
qc.measure(range(4), range(4))`,
  qft: `from qiskit import QuantumCircuit
from qiskit.circuit.library import QFT
qc = QuantumCircuit(3, 3)
qc.h(range(3))
qc.append(QFT(3).to_gate(), range(3))
qc.measure(range(3), range(3))`,
  qasm: `OPENQASM 2.0;
qreg q[3];
creg c[3];
h q[0];
cx q[0],q[1];
cx q[1],q[2];
measure q -> c;`,
  qssc_backend: `# QSSC Qiskit backend -- simulator AND real hardware, with comments.
#
# qssc.QSSCBackend is a real Qiskit BackendV2. It models the 64-site spin-defect
# chip: a full Aer thermal + depolarizing + readout noise model built from each
# defect profile's real T1/T2 and gate times. Two ways to run it:
#
#   1. SIMULATOR  -- no hardware: Aer runs the circuit under the chip's physics.
#   2. HARDWARE   -- an Artix-7 FPGA drives the emitters / microwave / detector;
#                    pass hardware=make_connection(port=...). With no physical
#                    board yet, make_connection(port=None) returns a SimulatedFPGA
#                    that exercises the SAME control + optical-readout path.
#
# This is a LOCAL-PYTHON reference (the browser sandbox only builds a 'qc' and
# the dashboard runs it on this backend). Run it yourself:
#   pip install -e ".[api]"   then   python qssc_backend_example.py

from qiskit import QuantumCircuit
from qssc import QSSCBackend, make_connection

# ---- a small circuit: 3-qubit GHZ ------------------------------------------
qc = QuantumCircuit(3, 3)
qc.h(0)
qc.cx(0, 1)
qc.cx(1, 2)
qc.measure(range(3), range(3))

# ============================ 1. SIMULATOR (full physics) ====================
# noise_mode="real"   -> Aer density-matrix run with thermal (T1,T2,gate time) +
#                        depolarizing + optical-readout noise from the NV profile.
#                        "none" = ideal, "phenomenological" = fast analytic approx.
# temperature_k=1e-3  -> 1 mK (cryo): T2 ~ 1.8 ms for 12C-purified NV, the regime
#                        where Shor-15 / QFT recover. 300 = room temperature.
sim = QSSCBackend(profile="NV", temperature_k=1e-3,
                  noise_mode="real", shots=4096, seed=42)
# NOTE: do NOT transpile against the 64-qubit QSSC target -- it expands the
# circuit to the full 64-site register and hits the 28-qubit simulator cap.
# QSSCBackend.run() transpiles to Aer internally, so pass the circuit as-is.
res = sim.run(qc, shots=4096).result()
print("SIM counts   :", res.get_counts())              # e.g. {'000':.., '111':..}
print("SIM retention:", res.assessment["signal_retention"])  # signal survival
print("SIM ideal    :", res.ideal_counts)              # noise-free reference
print("SIM approx   :", res.approximate)               # True = analytic fallback

# ============================ 2. HARDWARE (Artix-7) =========================
# make_connection(port) opens a real FPGA on the serial port. With port=None it
# returns SimulatedFPGA -- same EmitterController / DetectorInterface / microwave
# API, so the code below is identical for the lab board and the simulator.
hw = make_connection(port=None, profile="NV", temperature_k=1e-3)   # sim hardware
# hw = make_connection(port="/dev/ttyUSB0", profile="NV")           # REAL board
hw_backend = QSSCBackend(profile="NV", hardware=hw,
                         noise_mode="real", shots=4096, seed=42)
res_hw = hw_backend.run(qc, shots=4096).result()
print("HW counts    :", res_hw.get_counts())
print("HW optical   :", res_hw.optical)                 # on-chip readout (None
                                                       # unless hardware attached)`,
}