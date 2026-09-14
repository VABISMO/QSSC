import React, { useEffect, useState, useCallback, useRef } from 'react'
import { api } from './api.js'
import { deriveMeasurement } from './derive.js'
import {
  QubitGrid, OdmrChart, BarCounts,
  SignalChart, CalibrationPanel, Verdict, NoiseBudget,
  OpticalChainDiagram, RunPhysics, MeasurementHelp,
  CompassRose, GyroPanel, MagnetoImage, SensorField,
  ParallelCalibCard,
} from './components.jsx'

// Fallback firmware command list (all 17 verbs). Used in the "Reply & readout"
// empty state when the API isn't running or is a stale build serving fewer
// commands, so every command we added is always visible. The live API list
// (fpga.protocol) takes precedence when it serves the full set.
const FALLBACK_PROTOCOL = [
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
  { cmd: 'READ_COMPASS', args: '[n_avg]', reply: '<B_nT> <heading°> <elev°> <sens_µT> <axes> / ERR', desc: '4-axis ODMR doublet fit → ambient B-vector + heading (compass_measure)' },
  { cmd: 'READ_GYRO', args: 'T_ns', reply: '<rate_dps> <noise_dps> <t2_µs> <grade> / ERR', desc: 'Ramsey π/2–T–π/2 → rotation rate (sim convenience verb; hardware runs RUN_SEQUENCE + on-host fit)' },
  { cmd: 'READ_LOCKIN', args: 'ref_khz n_cycles', reply: '<diff> <sigma> <snr1> <snrN> / ERR', desc: 'lock-in accumulator: on-minus-off differential over n cycles (sim; hardware READ_LOCKIN <site> <n> → raw on/off sums, host computes SNR)' },
  { cmd: 'RUN_SEQUENCE', args: 'kind n_windows', reply: '<n> c0 c1 … / ERR', desc: 'pulse-sequence engine: ramsey / hahn / cpmg → n readout codes (sim form; hardware RUN_SEQUENCE <site> <kind> <n> <t_step>)' },
  { cmd: 'STATUS', args: '—', reply: 'PLL_LOCKED … VCC3V3_mv … TEMP_C …', desc: 'board self-status: PLL lock, ADC busy, rail voltages, die temperature' },
  { cmd: 'RESET', args: '—', reply: 'OK', desc: 'soft reset of the TLC5947 / ADF4351 / ADC peripheral state' },
]

const PROFILES = [
  { id: 'NV', label: 'Diamond · NV centre (default / compute)' },
  { id: 'hBN-VB', label: 'hBN · V_B⁻ (boron-vacancy · sensor)' },
  { id: 'SiC-VSi', label: 'SiC · V_Si (silicon vacancy · sensor)' },
]
const ALGOS = ['custom', 'shor', 'period', 'bell', 'math', 'grover', 'qft', 'qftnos', 'qrand', 'bb84']
const ALGO_LABELS = {
  custom: 'Custom circuit', shor: 'Shor (factoring)', period: 'Period finding',
  bell: 'Bell pair', math: 'Arithmetic', grover: 'Grover search',
  qft: 'QFT — Quantum Fourier Transform',
  qftnos: 'QFT-NOS — lightweight QFT factoring',
  qrand: 'Quantum RNG → crypto keys / cert',
  bb84: 'BB84 — quantum key distribution',
}
const MATH_OPS = ['add', 'sub', 'mul', 'sqrt', 'log']
const NOISE_MODES = [
  { id: 'real', label: 'Simulator (full physics)' },
  { id: 'phenomenological', label: 'Simulator (fast approx.)' },
  { id: 'none', label: 'Simulator (ideal, no noise)' },
]
const SEQS = [
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
const QUBIT_CFG = {
  custom:  null,                                  // custom circuit defines its own width
  bell:    { min: 2, max: 64, step: 1, def: 4,  label: 'Qubits n',         hint: 'GHZ chain on n qubits (H + CNOT ladder) — one pixel’s NV register, MPS for n>28' },
  qrand:   { min: 2, max: 64, step: 1, def: 8,  label: 'Qubits n',         hint: 'n qubits prepared in |+⟩ → von-Neumann-extracted entropy — one pixel’s NV register, MPS for n>28' },
  qft:     { min: 2, max: 64, step: 1, def: 4,  label: 'Qubits n',         hint: 'QFT of an n-qubit computational-basis state — one pixel’s NV register, MPS for n>28' },
  grover:  { min: 2, max: 8,  step: 1, def: 3,  label: 'Search qubits n',  hint: 'search space of 2ⁿ items' },
  math:    { min: 2, max: 6,  step: 1, def: 4,  label: 'Word qubits n',    hint: 'n-bit arithmetic register' },
  // shor / period / qftnos: the qubit count is a CONSEQUENCE of the integer N
  // being factored (shor/period = 3·bitlen(N)+1, qftnos = 2·bitlen(N)+1), so the
  // user edits N directly and the circuit width is shown as a derived readout
  // (no universal qubit field for these — see the dedicated N row below).
  shor:    null,
  period:  null,
  qftnos:  null,
  bb84:    { min: 16, max: 2048, step: 16, def: 256, label: 'Rounds (1 qubit each)', hint: 'BB84 prepare-and-measure rounds' },
}

// helpers for shor / period / qftnos: the circuit width is a function of the
// integer N being factored (shor/period = 3·bitlen(N)+1, qftnos = 2·bitlen(N)+1).
// We keep N editable and show the qubit count as a READ-ONLY derived readout
// (shor/period = 3·bitlen(N)+1, qftnos = 2·bitlen(N)+1). Editing the qubit
// count no longer silently snaps N to a semiprime -- that bidirectional
// coupling made "inputs change by themselves" when the user touched the
// qubits field.
const bitLen = (n) => (n <= 1 ? 1 : Math.floor(Math.log2(n)) + 1)
const derivedQubits = (algo, n) => algo === 'qftnos' ? 2 * bitLen(n) + 1 : 3 * bitLen(n) + 1

const downloadPem = (filename, text) => {
  const blob = new Blob([text], { type: 'application/x-pem-file' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}
const CUSTOM_EXAMPLES = {
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

function useAsync(fn, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  // Guard against the stale-response race: when deps change (e.g. the user
  // switches profile) a new fetch starts while the old one may still be in
  // flight. Tag each invocation; ignore resolutions from any but the latest
  // so a slow old request can never overwrite the fresh data. The unmount
  // effect sets the id to -1 so a late resolution after unmount is dropped.
  const reqId = useRef(0)
  const run = useCallback(() => {
    const id = ++reqId.current
    setLoading(true); setError(null)
    Promise.resolve(fn())
      .then(d => { if (id === reqId.current) setData(d) })
      .catch(e => { if (id === reqId.current) setError(e.message) })
      .finally(() => { if (id === reqId.current) setLoading(false) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(run, [run])
  useEffect(() => () => { reqId.current = -1 }, [])
  return { data, error, loading, reload: run }
}

export default function App() {
  // ---- top-level tabs: Hardware (default) / History ----
  const [mainTab, setMainTab] = useState('hardware')

  // ---- FPGA tab: synthesis metrics + live command protocol ----
  const [fpga, setFpga] = useState(null)
  const [fpgaErr, setFpgaErr] = useState(null)
  const [fpgaLoading, setFpgaLoading] = useState(false)
  const [fpgaBusy, setFpgaBusy] = useState(false)
  const [fpgaReply, setFpgaReply] = useState(null)      // last {command, reply, ok}
  const [fpgaLog, setFpgaLog] = useState([])            // most-recent-first command log
  // parameterized command inputs (defaults match the NV centre: D≈2.87 GHz)
  const [emIdx, setEmIdx] = useState(0)
  const [emDur, setEmDur] = useState(50)
  const [emLvl, setEmLvl] = useState(4095)
  const [detSite, setDetSite] = useState(0)
  const [mwQ, setMwQ] = useState(0)
  const [mwFreq, setMwFreq] = useState(2870000)         // kHz (NV zero-field splitting ≈ 2.87 GHz)
  const [mwAmp, setMwAmp] = useState(63)
  const [mwDur, setMwDur] = useState(200)              // ns
  const [mwPhase, setMwPhase] = useState(0)            // mrad
  const [rawCmd, setRawCmd] = useState('')
  // command-reference modal (FPGA section) — the full firmware-command table
  // opens in a modal triggered by the "Command reference" button under the
  // Raw command fieldset, instead of being rendered inline as a section.
  const [cmdRefOpen, setCmdRefOpen] = useState(false)
  // hardware QRNG (ADC-noise entropy -> Ed25519 keypair + cert)
  const [qrng, setQrng] = useState(null)
  const [qrngErr, setQrngErr] = useState(null)
  const [qrngLoading, setQrngLoading] = useState(false)
  const [qrngBytes, setQrngBytes] = useState(32)         // entropy bytes (>=32 for Ed25519 seed)
  const [qrngCN, setQrngCN] = useState('QSSC-hw-qrng')
  // Top-level execution environment, shown on every tab:
  // 'simulator'  — Aer runs the circuit under the chip's physics; all on-chip
  //                I/O is modelled by SimulatedFPGA (today's default path).
  // 'hardware'   — a real Artix-7 board (QSSC_FPGA_PORT) drives the emitters,
  //                microwave, AD7616 readout and QRNG; Aer still computes the
  //                gate-circuit bitstring (the chip is a sensor array, not a
  //                gate-model QC). No board connected -> a clear "no FPGA
  //                detected" banner, never a silent simulation.
  // `fpgaMode` is the derived 'sim'/'real' token the sensor endpoints take.
  const [hwMode, setHwMode] = useState('simulator')
  const fpgaMode = hwMode === 'hardware' ? 'real' : 'sim'
  const [probe, setProbe] = useState({ loading: false, data: null })

  const refreshFpga = useCallback(() => {
    setFpgaLoading(true); setFpgaErr(null)
    api.fpgaInfo().then(setFpga).catch(e => setFpgaErr(e.message)).finally(() => setFpgaLoading(false))
  }, [])
  useEffect(() => { if (mainTab === 'fpga' && !fpga) refreshFpga() }, [mainTab, fpga, refreshFpga])

  // Close the command-reference modal on Escape (overlay click + ✕ also close it).
  useEffect(() => {
    if (!cmdRefOpen) return
    const onKey = (e) => { if (e.key === 'Escape') setCmdRefOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [cmdRefOpen])

  const sendFpga = useCallback((command) => {
    if (!command || fpgaBusy) return
    setFpgaBusy(true)
    api.fpgaCommand(command)
      .then(r => { setFpgaReply(r); setFpgaLog(l => [{ ...r, t: new Date().toLocaleTimeString() }, ...l].slice(0, 40)) })
      .catch(e => setFpgaReply({ command, reply: 'ERROR: ' + e.message, ok: false }))
      .finally(() => setFpgaBusy(false))
  }, [fpgaBusy])

  // Hardware QRNG: harvest ADC-noise entropy via the READ_RNG firmware verb,
  // derive an Ed25519 keypair + self-signed cert on the server.
  const runQrng = useCallback(() => {
    setQrngLoading(true); setQrngErr(null)
    api.fpgaQrng({ n_bytes: qrngBytes, common_name: qrngCN })
      .then(setQrng).catch(e => setQrngErr(e.message)).finally(() => setQrngLoading(false))
  }, [qrngBytes, qrngCN])

  // ---- global control state (declared early so sensor handlers below can
  //      reference profile / temperature / seed in their useCallback deps) ----
  const [profile, setProfile] = useState('NV')
  // Peltier liquid-cooling setpoint: 2-stage TEC + NTC + PID holds the chip at
  // 295 K +/- 0.1 K (see docs/FABRICATION_PROTOCOL_Diamond.md, protocol.py).
  const [temperature, setTemperature] = useState(295)
  const [seed, setSeed] = useState(42)
  const [noiseMode, setNoiseMode] = useState('real')

  // ---- pixel_mode selector (the "multiqbit mode" panel switch) ----
  // `register` (default): the circuit qubits are one pixel's N-NV register
  //   (within-pixel all-to-all dipole 2q); N is the MEASURED resolvable ODMR
  //   line count when the calibration has run, else the nominal default.
  // `ensemble`: the 64 chip pixels ARE the circuit qubits (one ensemble qubit
  //   per pixel — the bulk-microdiamond ODMR collapse); no within-pixel 2q at
  //   300 K, so 2q circuits are rejected. Both modes run on the existing
  //   firmware (ODMR_SWEEP + READ_DETECTORS), no PCB change.
  // `nNvPerPixel` is null until the calibration measures the register width
  // (decompose_odmr → line count); `registerLineFreqsHz` carries the measured
  //   line centres so the backend places NVs on the measured frequency grid.
  // `primeReadout` toggles the prime-coprime denoiser (READ_LOCKINP, default on).
  const [pixelMode, setPixelMode] = useState('register')
  const [nNvPerPixel, setNNvPerPixel] = useState(null)
  const [registerLineFreqsHz, setRegisterLineFreqsHz] = useState(null)
  const [primeReadout, setPrimeReadout] = useState(true)
  // `parallelMode` is the single "64-parallel ON/OFF" switch. OFF → the
  // single-pixel path (Circuit Runner + per-pixel ODMR calibration). ON → the
  // 64-pixel array path (array calibration + run-parallel). Exactly one Run
  // and one Calibrate are visible at any time; the toggle swaps which.
  const [parallelMode, setParallelMode] = useState(false)
  // Visual subtabs within the Circuits tab (replaces the vertical timeline of
  // section-titles): Circuit runner / Backend physics / Spectroscopy &
  // calibration / Detected signal. Default = the runner (the primary action).
  const [circuitsSub, setCircuitsSub] = useState('runner')
  // Visual subtabs within the Sensors tab (replaces the vertical timeline of
  // section-titles): Quantum sensors (compass/gyro/magneto) / Field imaging
  // (ferro/nav/thermal/current). Default = the on-chip quantum sensors.
  const [sensorsSub, setSensorsSub] = useState('quantum')
  const [modes, setModes] = useState(null)
  useEffect(() => { api.modes().then(setModes).catch(() => {}) }, [])

  // ---- FPGA tab: on-chip quantum sensors (compass / gyro / magneto-optic) ----
  // On-demand measurements (button click), not auto-fetched — active MW sweeps.
  const [compass, setCompass] = useState(null)
  const [compassLoading, setCompassLoading] = useState(false)
  const [compassErr, setCompassErr] = useState(null)
  const [compassBx, setCompassBx] = useState(150)
  const [compassBy, setCompassBy] = useState(50)
  const [compassBz, setCompassBz] = useState(-100)
  const [compassAvg, setCompassAvg] = useState(1000)
  const [gyro, setGyro] = useState(null)
  const [gyroLoading, setGyroLoading] = useState(false)
  const [gyroErr, setGyroErr] = useState(null)
  const [gyroRate, setGyroRate] = useState(10)
  const [gyroAvg, setGyroAvg] = useState(100)
  const [mag, setMag] = useState(null)
  const [magLoading, setMagLoading] = useState(false)
  const [magErr, setMagErr] = useState(null)
  const [magAvg, setMagAvg] = useState(500)
  const [magGrad, setMagGrad] = useState(8)

  // ---- contactless field-imaging sensors (ferro / nav / thermal / current) ----
  const [ferro, setFerro] = useState(null); const [ferroLoading, setFerroLoading] = useState(false); const [ferroErr, setFerroErr] = useState(null)
  const [ferroMoment, setFerroMoment] = useState(0.02)
  const [ferroDist, setFerroDist] = useState(15)
  const [ferroOffX, setFerroOffX] = useState(4)
  const [ferroOffY, setFerroOffY] = useState(3)
  const [nav, setNav] = useState(null); const [navLoading, setNavLoading] = useState(false); const [navErr, setNavErr] = useState(null)
  const [navPosX, setNavPosX] = useState(12); const [navPosY, setNavPosY] = useState(10); const [navAnom, setNavAnom] = useState(2)
  const [thermal, setThermal] = useState(null); const [thermalLoading, setThermalLoading] = useState(false); const [thermalErr, setThermalErr] = useState(null)
  const [thermalBase, setThermalBase] = useState(300); const [thermalHot, setThermalHot] = useState(330)
  const [thermalHX, setThermalHX] = useState(5); const [thermalHY, setThermalHY] = useState(4)
  const [current, setCurrent] = useState(null); const [currentLoading, setCurrentLoading] = useState(false); const [currentErr, setCurrentErr] = useState(null)
  const [currentI, setCurrentI] = useState(0.5); const [currentDepth, setCurrentDepth] = useState(3); const [currentOff, setCurrentOff] = useState(0)
  // shared averages for the four field-imaging sensors
  const [fieldAvg, setFieldAvg] = useState(500)

  const runCompass = useCallback(() => {
    setCompassLoading(true); setCompassErr(null)
    api.compass({ profile, mode: fpgaMode, bx: compassBx, by: compassBy, bz: compassBz, n_avg: compassAvg, seed })
      .then(setCompass).catch(e => setCompassErr(e.message))
      .finally(() => setCompassLoading(false))
  }, [profile, fpgaMode, compassBx, compassBy, compassBz, compassAvg, seed])
  const runGyro = useCallback(() => {
    setGyroLoading(true); setGyroErr(null)
    api.gyro({ profile, mode: fpgaMode, rate_dps: gyroRate, temperature_k: temperature, n_avg: gyroAvg, seed, hardware: hwMode === 'hardware' })
      .then(setGyro).catch(e => setGyroErr(e.message))
      .finally(() => setGyroLoading(false))
  }, [profile, fpgaMode, gyroRate, temperature, gyroAvg, seed, hwMode])
  const runMag = useCallback(() => {
    setMagLoading(true); setMagErr(null)
    api.magneto({ profile, mode: fpgaMode, n_avg: magAvg, gradient_ut_mm: magGrad, seed })
      .then(setMag).catch(e => setMagErr(e.message))
      .finally(() => setMagLoading(false))
  }, [profile, fpgaMode, magAvg, magGrad, seed])

  const runFerro = useCallback(() => {
    setFerroLoading(true); setFerroErr(null)
    api.ferro({ profile, mode: fpgaMode, moment_am2: ferroMoment, distance_mm: ferroDist,
      offset_x_mm: ferroOffX, offset_y_mm: ferroOffY, n_avg: fieldAvg, seed })
      .then(setFerro).catch(e => setFerroErr(e.message)).finally(() => setFerroLoading(false))
  }, [profile, fpgaMode, ferroMoment, ferroDist, ferroOffX, ferroOffY, fieldAvg, seed])
  const runNav = useCallback(() => {
    setNavLoading(true); setNavErr(null)
    api.nav({ profile, mode: fpgaMode, pos_x: navPosX, pos_y: navPosY, anomaly_ut: navAnom, n_avg: fieldAvg, seed })
      .then(setNav).catch(e => setNavErr(e.message)).finally(() => setNavLoading(false))
  }, [profile, fpgaMode, navPosX, navPosY, navAnom, fieldAvg, seed])
  const runThermal = useCallback(() => {
    setThermalLoading(true); setThermalErr(null)
    api.thermal({ profile, mode: fpgaMode, base_temp_k: thermalBase, hotspot_k: thermalHot,
      hotspot_x: thermalHX, hotspot_y: thermalHY, n_avg: fieldAvg, seed })
      .then(setThermal).catch(e => setThermalErr(e.message)).finally(() => setThermalLoading(false))
  }, [profile, fpgaMode, thermalBase, thermalHot, thermalHX, thermalHY, fieldAvg, seed])
  const runCurrent = useCallback(() => {
    setCurrentLoading(true); setCurrentErr(null)
    api.current({ profile, mode: fpgaMode, current_a: currentI, wire_offset_mm: currentOff,
      depth_mm: currentDepth, n_avg: fieldAvg, seed })
      .then(setCurrent).catch(e => setCurrentErr(e.message)).finally(() => setCurrentLoading(false))
  }, [profile, fpgaMode, currentI, currentOff, currentDepth, fieldAvg, seed])

  // Probe the real FPGA whenever Hardware-real mode is entered, so the
  // indicator shows a green "FPGA connected — QSSC v1 on /dev/ttyUSB0" or a
  // red "No FPGA detected" without the user having to run a circuit first.
  useEffect(() => {
    if (hwMode !== 'hardware') { setProbe({ loading: false, data: null }); return }
    let alive = true
    setProbe({ loading: true, data: null })
    api.fpgaProbe()
      .then(d => { if (alive) setProbe({ loading: false, data: d }) })
      .catch(() => { if (alive) setProbe({ loading: false, data: { connected: false, port: null, id: null } }) })
    return () => { alive = false }
  }, [hwMode])

  // Auto-capture an initial reading whenever the Sensors tab is opened and
  // whenever the sim/real environment is toggled, so every sensor shows data
  // immediately (not just on the Measure buttons). The loading guards stop
  // overlapping in-flight calls.
  useEffect(() => {
    if (mainTab !== 'sensors') return
    if (!compassLoading) runCompass()
    if (!gyroLoading) runGyro()
    if (!magLoading) runMag()
    if (!ferroLoading) runFerro()
    if (!navLoading) runNav()
    if (!thermalLoading) runThermal()
    if (!currentLoading) runCurrent()
    // re-run only on tab enter or environment toggle
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mainTab, fpgaMode])

  // ---- circuit runner state ----
  const [algo, setAlgo] = useState('custom')
  const [shots, setShots] = useState(1024)
  const [nShor, setNShor] = useState(15)
  // nShorDraft is the raw text of the N input so the user can clear & retype
  // freely (a controlled number snaps empty -> 0 -> 2 and fights typing). The
  // effect below parses+clamps it to the backend's N ceiling and derives the
  // circuit width; nShor (the clamped number) is what buildBody sends.
  const [nShorDraft, setNShorDraft] = useState('15')
  const [numQubits, setNumQubits] = useState(4)   // universal qubit count (all algos)
  const [bbEve, setBbEve] = useState(false)
  const [mathOp, setMathOp] = useState('mul')
  const [mathA, setMathA] = useState(3)
  const [mathB, setMathB] = useState(5)
  const [groverMarked, setGroverMarked] = useState(5)
  const [qftX, setQftX] = useState(1)
  const [qrBytes, setQrBytes] = useState(32)
  const [qrCN, setQrCN] = useState('QSSC-quantum-device')
  const [customCode, setCustomCode] = useState(CUSTOM_EXAMPLES.ghz)
  const [job, setJob] = useState(null)        // {job_id, status}
  const [result, setResult] = useState(null)  // finished result dict
  const [runErr, setRunErr] = useState(null)
  const pollRef = useRef(null)
  const parPollRef = useRef(null)
  const arrCalibPollRef = useRef(null)
  // clear any pending status-poll timeout on unmount so it can't fire a
  // setState on the unmounted component.
  useEffect(() => () => {
    if (pollRef.current) clearTimeout(pollRef.current)
    if (parPollRef.current) clearTimeout(parPollRef.current)
    if (arrCalibPollRef.current) clearTimeout(arrCalibPollRef.current)
  }, [])

  // ---- analog signal + calibration state ----
  const [seq, setSeq] = useState('ramsey')
  const [detuning, setDetuning] = useState(2)
  const [calRun, setCalRun] = useState(null)
  const [calRunning, setCalRunning] = useState(false)
  const [calErr, setCalErr] = useState(null)

  // ---- 64-parallel computing (Invention 4) + per-pixel array calibration ----
  // Calibrate the array first (measured per-pixel register width), then run
  // one circuit per pixel with use_calibration on. The calibration cache is
  // persisted server-side (hardware/calibration/array_calibration.json) so the
  // run-parallel endpoint loads it automatically when use_calibration=true.
  const [parRun, setParRun] = useState(null)
  const [parRunning, setParRunning] = useState(false)
  const [parJob, setParJob] = useState(null)
  const [parErr, setParErr] = useState(null)
  const [arrCalib, setArrCalib] = useState(null)
  const [arrCalibRunning, setArrCalibRunning] = useState(false)
  const [arrCalibErr, setArrCalibErr] = useState(null)
  // Shared 64-parallel card state (lifted here so the run card + the calibrate
  // card — now placed in the Circuit Runner + Spectroscopy slots — stay in
  // sync: the run body carries gradient/pixel-size, and useCalib gates the run
  // on the cached calibration).
  const [parGrad, setParGrad] = useState(0.5)
  const [parPixelSize, setParPixelSize] = useState(200)
  const [parNPixels, setParNPixels] = useState(64)
  const [parUseCalib, setParUseCalib] = useState(true)

  // ---- history ----
  const [history, setHistory] = useState([])
  const [historyLoading, setHistoryLoading] = useState(false)

  // ---- saved scripts (Code tab) ----
  const [scripts, setScripts] = useState([])
  const [scriptsLoading, setScriptsLoading] = useState(false)
  const [edName, setEdName] = useState('')
  const [edCode, setEdCode] = useState(CUSTOM_EXAMPLES.ghz)
  const [edId, setEdId] = useState(null)
  const [scriptMsg, setScriptMsg] = useState(null)

  // ---- derived data ----
  const health = useAsync(api.health, [])
  const info = useAsync(() => api.backendInfo(profile, temperature, seed), [profile, temperature, seed])
  const odmr = useAsync(() => api.odmr(profile, 1e-3, 160), [profile])
  const cal = useAsync(() => api.calibration(profile), [profile])
  const signal = useAsync(() => api.signal({ profile, temperature_k: temperature, sequence: seq, detuning_mhz: detuning, points: 220, hardware: hwMode === 'hardware' }), [profile, temperature, seq, detuning, hwMode])

  const refreshHistory = useCallback(() => {
    setHistoryLoading(true)
    api.jobs(60).then(setHistory).catch(() => {}).finally(() => setHistoryLoading(false))
  }, [])
  useEffect(refreshHistory, [refreshHistory])

  const refreshScripts = useCallback(() => {
    setScriptsLoading(true)
    api.scripts().then(setScripts).catch(() => {}).finally(() => setScriptsLoading(false))
  }, [])
  useEffect(refreshScripts, [refreshScripts])

  // Parse the free-text N input (nShorDraft) into the clamped integer the
  // backend accepts, and derive the circuit width. Guarded to shor/period/
  // qftnos so a non-factoring algo's qubit count (from QUBIT_CFG) is not
  // clobbered. Empty / non-numeric draft -> keep the last valid nShor (lets
  // the user clear the field and retype without a snap-back).
  useEffect(() => {
    if (algo !== 'shor' && algo !== 'period' && algo !== 'qftnos') return
    const v = parseInt(nShorDraft, 10)
    if (Number.isNaN(v)) return
    // shor/period = 3*bitlen(N)+1 <= 19 -> N <= 63; qftnos = 2*bitlen(N)+1 <= 19
    // -> N <= 511. Clamp to the algo's cap so a prime the clamp would otherwise
    // land on (e.g. typing 39 used to clamp to 31, which is prime -> "n must be
    // a composite integer > 1") is never the reason a composite is rejected.
    const cap = algo === 'qftnos' ? 511 : 63
    const clamped = Math.min(cap, Math.max(2, v))
    setNShor(clamped)
    setNumQubits(derivedQubits(algo, clamped))
  }, [nShorDraft, algo])

  const saveScript = async () => {
    setScriptMsg(null)
    if (!edCode.trim()) { setScriptMsg('Code is empty'); return }
    try {
      const rec = await api.saveScript({ name: edName || 'untitled', code: edCode, id: edId })
      setEdId(rec.id)
      setScriptMsg('Saved ✓')
      refreshScripts()
    } catch (e) { setScriptMsg(e.message) }
  }
  const newScript = () => { setEdId(null); setEdName(''); setEdCode(CUSTOM_EXAMPLES.ghz); setScriptMsg(null) }
  const editScript = (s) => { setEdId(s.id); setEdName(s.name); setEdCode(s.code); setScriptMsg(null); setMainTab('code') }
  const removeScript = async (id) => {
    try { await api.deleteScript(id); if (edId === id) newScript(); refreshScripts() }
    catch (e) { setScriptMsg(e.message) }
  }
  const loadScriptToRunner = (s) => { setAlgo('custom'); setCustomCode(s.code); setMainTab('hardware') }

  const buildBody = () => {
    const hardware = hwMode === 'hardware'
    // pixel_mode + measured register width + prime-denoise toggle flow into
    // every circuit body (register: one pixel's N-NV register with dipole 2q;
    // ensemble: 64 chip pixels, no within-pixel 2q). nNvPerPixel is null until
    // the calibration measures the ODMR line count, so the backend uses its
    // nominal default when unset.
    const pixel = { pixel_mode: pixelMode, prime_readout: primeReadout }
    if (nNvPerPixel != null) pixel.n_nv_per_pixel = nNvPerPixel
    if (registerLineFreqsHz) pixel.register_line_freqs_hz = registerLineFreqsHz
    if (algo === 'custom') {
      return { code: customCode, profile, temperature_k: temperature, seed, shots, noise_mode: noiseMode, hardware, ...pixel }
    }
    const body = { algorithm: algo, profile, temperature_k: temperature, shots, noise_mode: noiseMode, hardware, ...pixel }
    // qrand: do NOT send the fixed seed -- the backend uses OS entropy when no
    // seed is present (issue #45), so each UI run yields fresh quantum entropy
    // and a new Ed25519 key. Sending seed=42 made every default qrand run
    // return the identical histogram / identical private key.
    if (algo !== 'qrand') body.seed = seed
    if (algo === 'shor' || algo === 'period' || algo === 'qftnos') body.n = nShor
    if (algo === 'math') { body.op = mathOp; body.a = mathA; body.b = mathB; body.bits = numQubits }
    if (algo === 'grover') { body.n_items = numQubits; body.marked = groverMarked }
    if (algo === 'qft') { body.num_qubits = numQubits; body.x = qftX }
    if (algo === 'qrand') { body.num_qubits = numQubits; body.num_bytes = qrBytes; body.common_name = qrCN }
    if (algo === 'bell') { body.num_qubits = numQubits }
    if (algo === 'bb84') { body.num_bits = numQubits; body.eavesdrop = bbEve }
    return body
  }

  // 64-parallel request body. Reuses buildBody() so the SAME algorithm menu
  // drives both modes (the run card stays in place; only Pixels + Use-
  // calibration are added in 64 mode). shor/period/qftnos -> the coprime-base
  // race (mode='shor'); everything else -> mode='sample' building that
  // algorithm's circuit per pixel on the backend.
  const buildParallelBody = () => {
    const base = buildBody()
    const shorFamily = algo === 'shor' || algo === 'period' || algo === 'qftnos'
    const body = { ...base,
      n_pixels: parNPixels, use_calibration: parUseCalib,
      gradient_mhz_per_um: parGrad, pixel_size_um: parPixelSize }
    if (shorFamily) {
      body.mode = 'shor'
      body.n = nShor
    } else {
      body.mode = 'sample'
      body.algorithm = algo
    }
    return body
  }

  const runCircuit = async () => {
    if (pollRef.current) clearTimeout(pollRef.current)
    setRunErr(null); setResult(null)
    setJob({ status: 'queued' })
    try {
      const enq = algo === 'custom'
        ? await api.runCustom(buildBody())
        : await api.run(buildBody())
      if (enq.error) { setRunErr(enq.error); setJob(null); return }
      const jobId = enq.job_id
      setJob({ job_id: jobId, status: enq.status })
      // Poll the job status until it finishes or fails. Cap the poll count so a
      // stuck/unknown-status job (or a transient fetch error) cannot poll
      // forever: after MAX_POLLS * 600 ms the dashboard gives up with a timeout
      // error instead of spinning indefinitely. A transient fetch error
      // (e.g. a 404 while the job record is still being written) retries up to
      // the same cap rather than aborting the whole run on the first blip.
      // 300 polls = 180 s, matching the backend JOB_TIMEOUT_S so the 19-qubit
      // Shor band (N=32..63, a single ~115 s attempt) finishes inside the window
      // instead of the UI giving up first.
      const MAX_POLLS = 300
      const poll = async (n = 0) => {
        try {
          const rec = await api.job(jobId)
          setJob({ job_id: jobId, status: rec.status })
          if (rec.status === 'finished') {
            setResult(rec.result || {})
            setJob(null)
            refreshHistory()
          } else if (rec.status === 'failed') {
            setRunErr((rec.result && rec.result.error) || 'run failed')
            setResult(rec.result || null)
            setJob(null)
            refreshHistory()
          } else if (n >= MAX_POLLS) {
            setRunErr(`job ${jobId} did not finish after ${Math.round(MAX_POLLS * 0.6)}s (last status: ${rec.status})`)
            setJob(null)
            refreshHistory()
          } else {
            pollRef.current = setTimeout(() => poll(n + 1), 600)
          }
        } catch (e) {
          if (n >= MAX_POLLS) { setRunErr(e.message); setJob(null) }
          else pollRef.current = setTimeout(() => poll(n + 1), 600)
        }
      }
      poll()
    } catch (e) { setRunErr(e.message); setJob(null) }
  }

  const runCalibration = async (opts = {}) => {
    setCalRunning(true); setCalErr(null)
    try {
      const r = await api.calibrationRun({
        profile, temperature_k: temperature, seed,
        hardware: hwMode === 'hardware',
        gradient_code: opts.gradient_code ?? null,
        n_nv_per_pixel: opts.n_nv_per_pixel ?? 10,
      })
      setCalRun(r)
      // The calibration measures the register width N (decompose_odmr on the
      // ODMR sweep → resolvable line count). Apply the open loop: set the
      // pixel mode + register N + measured line centres from the response so
      // the next circuit run uses the measured truth, not the nominal default.
      if (r && r.register_width != null) {
        setNNvPerPixel(r.register_width)
        if (r.line_freqs_hz) setRegisterLineFreqsHz(r.line_freqs_hz)
        if (r.pixel_mode_recommendation) setPixelMode(r.pixel_mode_recommendation)
      }
    } catch (e) { setCalErr(e.message) }
    finally { setCalRunning(false) }
  }

  // 64-parallel: calibrate the array's per-pixel register width, then run one
  // circuit per pixel. Both are enqueued as async RQ jobs (the sim calibrate
  // path runs decompose_odmr on 64 spectra ~30 s, and a 64-pixel Shor run is
  // ~50 s of serial Qiskit — both would block the single-worker event loop if
  // run inline), so the dashboard polls GET /api/jobs/{id} until finished.
  const calibrateArray = async (body) => {
    if (arrCalibPollRef.current) clearTimeout(arrCalibPollRef.current)
    setArrCalibRunning(true); setArrCalibErr(null); setArrCalib(null)
    try {
      const enq = await api.calibrateArray(body)
      if (enq.error) { setArrCalibErr(enq.error); setArrCalibRunning(false); return }
      const jobId = enq.job_id
      // 64 decompose_odmr fits can take ~30 s; allow up to 300 polls (180 s)
      // to match the single-pixel run window.
      const MAX_POLLS = 300
      const poll = async (n = 0) => {
        try {
          const rec = await api.job(jobId)
          if (rec.status === 'finished') {
            setArrCalib(rec.result || {})
            setArrCalibRunning(false)
          } else if (rec.status === 'failed') {
            setArrCalibErr((rec.result && rec.result.error) || 'calibration failed')
            setArrCalib(rec.result || null)
            setArrCalibRunning(false)
          } else if (n >= MAX_POLLS) {
            setArrCalibErr(`calibration job ${jobId} did not finish after ${Math.round(MAX_POLLS * 0.6)}s`)
            setArrCalibRunning(false)
          } else {
            arrCalibPollRef.current = setTimeout(() => poll(n + 1), 600)
          }
        } catch (e) {
          if (n >= MAX_POLLS) { setArrCalibErr(e.message); setArrCalibRunning(false) }
          else arrCalibPollRef.current = setTimeout(() => poll(n + 1), 600)
        }
      }
      poll()
    } catch (e) { setArrCalibErr(e.message); setArrCalibRunning(false) }
  }

  const runParallel = async (body) => {
    if (parPollRef.current) clearTimeout(parPollRef.current)
    setParRunning(true); setParErr(null); setParRun(null); setParJob({ status: 'queued' })
    try {
      const enq = await api.runParallel(body)
      if (enq.error) { setParErr(enq.error); setParRunning(false); setParJob(null); return }
      const jobId = enq.job_id
      setParJob({ job_id: jobId, status: enq.status })
      // A 64-pixel Shor run is ~50 s of serial Qiskit; allow up to 300 polls
      // (180 s) to match the single-pixel run window.
      const MAX_POLLS = 300
      const poll = async (n = 0) => {
        try {
          const rec = await api.job(jobId)
          setParJob({ job_id: jobId, status: rec.status })
          if (rec.status === 'finished') {
            setParRun(rec.result || {})
            setParRunning(false)
          } else if (rec.status === 'failed') {
            setParErr((rec.result && rec.result.error) || 'run failed')
            setParRun(rec.result || null)
            setParRunning(false)
          } else if (n >= MAX_POLLS) {
            setParErr(`parallel job ${jobId} did not finish after ${Math.round(MAX_POLLS * 0.6)}s`)
            setParRunning(false)
          } else {
            parPollRef.current = setTimeout(() => poll(n + 1), 600)
          }
        } catch (e) {
          if (n >= MAX_POLLS) { setParErr(e.message); setParRunning(false) }
          else parPollRef.current = setTimeout(() => poll(n + 1), 600)
        }
      }
      poll()
    } catch (e) { setParErr(e.message); setParRunning(false); setParJob(null) }
  }

  const loadFromHistory = (rec) => {
    setResult(rec.result || null)
    setRunErr(null)
    setJob(null)
    setMainTab('hardware')
  }

  const running = job && job.status !== 'finished' && job.status !== 'failed'

  const runCounts = result && (result.counts || (result.result && result.result.counts))
  // For period-finding (shor/period/qftnos) the backend also returns
  // marginal_counts: the full measured-bitstring distribution collapsed onto
  // the counting register, keyed by each counting value's *representative*
  // full bitstring. Its bars sum to shots, concentrate each phase peak into one
  // bar (instead of smearing it across the measured-ancilla states), and the
  // recovered-signal bar count then equals recovered.count. Prefer it for the
  // histogram + grid when present; fall back to the raw full-bitstring counts
  // for math / ideal / anything that does not marginalize.
  const runMarginal = result && (result.marginal_counts || (result.result && result.result.marginal_counts))
  const displayCounts = runMarginal || runCounts
  const runAssessment = result && result.assessment
  const runBits = result && result.num_clbits
  const runRecovered = result && (result.recovered || (result.result && result.result.recovered))
  // Per-algorithm "which bar is the result + how it's derived" marker, shared
  // by the histogram (signal bars) and the MeasurementHelp derivation modal.
  const meas = result ? deriveMeasurement(algo, result, displayCounts, runBits) : null
  // 64-parallel histogram + result marker — same marker + derivation treatment
  // as the single-pixel measurement so both modes look identical. The sample
  // mode shows the aggregate top bitstrings; shor mode marks the winner pixel's
  // recovered factors (→ a×b) on the top aggregate bar (its phase peak).
  const parAgg = parRun && parRun.aggregate_counts
  // Shor RACE: the histogram must show the WINNER pixel's counting-register
  // marginal counts (where its recovered phase peak is a top bar), NOT the
  // 64-pixel full-bitstring aggregate — otherwise the winner's peak is buried
  // below the top-8 cutoff and the green result marker is invisible. The
  // backend returns per_pixel[winner].recovered.marginal_counts (sums to one
  // pixel's shots). Sample mode keeps the aggregate (its top bitstring IS the
  // result, same as single-pixel).
  const _parWinner = parRun && parRun.mode === 'shor' && parRun.winner_pixel != null
    && parRun.per_pixel && parRun.per_pixel[parRun.winner_pixel]
  const parWinnerMarginal = _parWinner
    && ((_parWinner.recovered || {}).marginal_counts)
  const parSourceCounts = parWinnerMarginal || parAgg
  const parTopCounts = parSourceCounts
    ? Object.entries(parSourceCounts).sort((a, b) => b[1] - a[1]).slice(0, 8) : null
  const parDisplayCounts = parTopCounts && parTopCounts.length
    ? Object.fromEntries(parTopCounts) : null
  // Shor shots = the winner pixel's shots (the marginal is over its runs, so
  // Σ counts == shots and the mismatch flag clears); sample shots = aggregated.
  const parShots = parRun && (parRun.mode === 'shor'
    ? (parRun.shots || shots)
    : (parRun.total_shots || parRun.shots || shots))
  const parRecovered = parRun && parRun.mode === 'shor' && parRun.success
    ? { factors: parRun.factors, period: parRun.period, bitstring: null } : null
  // 64-parallel result marker + derivation — IDENTICAL treatment to the
  // single-pixel measurement for every algorithm. The backend sample mode now
  // postprocesses the aggregate counts (found/marked/uniform/result/...), so
  // deriveMeasurement works on parRun exactly as it does on `result`. shor mode
  // is a 64-pixel RACE; the backend returns the winner pixel's recovered
  // bitstring / counting_qubits / factors / prime_power, so the full single-
  // pixel phase→period→gcd chain is shown with the race context prepended.
  // qrand/bb84/custom have no single "result bar" in the aggregate → a generic
  // 64×-ensemble derivation.
  const parMeas = (() => {
    if (!parRun) return null
    if (parRun.mode === 'shor') {
      // 64-pixel Shor RACE: build the SAME full phase->period->gcd derivation
      // the single-pixel measurement shows (via deriveMeasurement on the winner
      // pixel's recovered bitstring / counting_qubits / factors / prime_power,
      // which the backend now returns), then PREPEND the race-context steps so
      // the modal explains every variable exactly like single-pixel, plus the
      // 64-pixel race that found the winner.
      const full = deriveMeasurement('shor', parRun, parDisplayCounts, parRun.num_clbits)
      const raceSteps = [
        { k: 'Race', v: `${parRun.n_pixels} pixels each run Shor on N=${parRun.n}, each with an independent coprime base a (gcd(a,N)=1)` },
        { k: 'Winner', v: parRun.success
          ? `pixel ${parRun.winner_pixel} (base a=${parRun.winner_base}) was the first to find a usable period r=${parRun.period}`
          : 'no pixel found a period above the noise floor' },
      ]
      if (full) {
        return {
          bars: full.bars, label: full.label, hasResult: full.hasResult,
          title: `64-pixel Shor race — pixel ${parRun.winner_pixel} (base a=${parRun.winner_base}) found the period; then the single-pixel phase→period→gcd chain`,
          steps: [...raceSteps, ...full.steps],
          summary: `Each of the ${parRun.n_pixels} pixels races an independent coprime base a; pixel ${parRun.winner_pixel} (a=${parRun.winner_base}) found period r=${parRun.period}. From there the factor recovery is EXACTLY the single-pixel Shor chain — the marked bar is that winner pixel's measured phase peak, its low counting bits give the phase, continued fractions lift the period, and gcd(a^(r/2)±1, ${parRun.n}) gives the factors. The 64-pixel race raises throughput (64× bases per wall-clock step), not coherence.${full.summary ? ' ' + full.summary : ''}`,
        }
      }
      // no recovered bitstring (decohered): keep the short race verdict
      return {
        bars: [], label: '→ no signal', hasResult: false,
        title: '64-pixel Shor race — no pixel found the period',
        steps: [
          ...raceSteps,
          { k: 'Reason', v: `the circuits decohered at ${parRun.temperature_k} K (T₂ too short); cool the chip or use none/ideal mode` },
        ],
        summary: `None of the ${parRun.n_pixels} pixels found a phase peak above the noise floor, so no period/factors were recovered. The 64-pixel race raises throughput (64× bases in one wall-clock step), not coherence — cooling the chip extends T₂ and is what lets a pixel succeed.`,
      }
    }
    // sample mode: reuse the SAME derivation as the single-pixel measurement.
    const derived = deriveMeasurement(algo, parRun, parDisplayCounts, parRun.num_clbits)
    if (derived) return derived
    // qrand / bb84 / custom / unknown: no single result bar in the aggregate.
    const topBs = parTopCounts && parTopCounts[0] ? parTopCounts[0][0] : null
    return {
      bars: topBs ? [topBs] : [], label: '→ 64× ensemble', hasResult: !!topBs,
      title: `64-pixel ${algo || 'sample'} ensemble — aggregate of ${parRun.n_run || parRun.n_pixels} pixels`,
      steps: [
        { k: 'Mode', v: `same circuit on every pixel, independent seeds → ${(parRun.n_run || parRun.n_pixels)}× shots aggregated` },
        { k: 'Aggregate', v: `${parRun.total_shots || parRun.shots} total counts across ${(parRun.n_run || parRun.n_pixels)} pixels` },
        { k: 'Top bar', v: topBs ? `${topBs} (the most-probable outcome across all pixels)` : '—' },
        { k: 'Result', v: 'the 64× aggregate boosts statistics; no single bar is "the answer" for this protocol' },
      ],
      summary: `Every pixel runs the same ${algo || 'sample'} circuit with an independent noise seed, so the aggregate histogram is the single-pixel distribution with ${(parRun.n_run || parRun.n_pixels)}× the shots — tighter peaks, same outcome statistics. The marked bar is the most-probable aggregate outcome.`,
    }
  })()

  return (
    <div className="app">
      {/* ============================= HEADER ============================= */}
      <header className="header">
        {/* BASE_URL is "/" on a root origin and "/QSSC/" on a GitHub Pages
            project subpath, so this resolves correctly in both deployments
            (a hard-coded "/favicon.svg" would 404 on the /QSSC/ subpath). */}
        <img className="logo" src={`${import.meta.env.BASE_URL}favicon.svg`} alt="QSSC logo" />
        <div className="title">
          <h1><span className="grad">Quantum Solid State Chip</span></h1>
          <div className="sub">Solid-state spin-defect platform — Qiskit backend · FPGA control · fabrication protocol</div>
        </div>
        <div className="badge">
          <span className={`pill ${health.data && health.data.status === 'ok' ? 'ok' : ''}`}>
            {health.loading ? '…' : health.data ? 'API online' : 'API down'}
          </span>
          <span className="pill brand">QSSC v2.0.0</span>
        </div>
      </header>

      {/* ============================= MAIN TABS ============================= */}
      <div className="maintabs">
        <div className={`maintab ${mainTab === 'hardware' ? 'active' : ''}`} onClick={() => setMainTab('hardware')}>
          <span className="mt-ic">⬡</span> Circuits
        </div>
        <div className={`maintab ${mainTab === 'fpga' ? 'active' : ''}`} onClick={() => { setMainTab('fpga'); refreshFpga() }}>
          <span className="mt-ic">⊞</span> FPGA
        </div>
        <div className={`maintab ${mainTab === 'sensors' ? 'active' : ''}`} onClick={() => setMainTab('sensors')}>
          <span className="mt-ic">⊛</span> Sensors
        </div>
        <div className={`maintab ${mainTab === 'code' ? 'active' : ''}`} onClick={() => { setMainTab('code'); refreshScripts() }}>
          <span className="mt-ic">{'</>'}</span> Code
        </div>
        <div className={`maintab ${mainTab === 'history' ? 'active' : ''}`} onClick={() => { setMainTab('history'); refreshHistory() }}>
          <span className="mt-ic">◷</span> History
        </div>
      </div>

      {mainTab === 'fpga' ? (
        /* ============================= FPGA (control + metrics) ============================= */
        <>
          {/* environment selector — mirrors the top-level Simulator / Hardware-real switch */}
          <div className="seg-toggle" role="group" aria-label="FPGA environment">
            <span className="seg-label">Environment</span>
            <button className={`seg ${hwMode === 'simulator' ? 'on' : ''}`} onClick={() => setHwMode('simulator')}>
              <span className="seg-ic">◈</span> Simulator
            </button>
            <button className={`seg hw ${hwMode === 'hardware' ? 'on' : ''}`} onClick={() => setHwMode('hardware')}>
              <span className="seg-ic">⌬</span> Hardware
            </button>
            <span className="seg-note">
              {hwMode === 'simulator'
                ? 'Aer + SimulatedFPGA — averages freely, cleaner readout (no shot-noise floor)'
                : 'real ADF4351 + 64-ch AD7616 chain — live emitter / MW / detector / QRNG I/O'}
            </span>
          </div>

          <div className="stats" style={{ marginBottom: 18 }}>
            <div className="stat"><div className="k">Target FPGA</div><div className="v">XC7A35T<small> · CSG324</small></div></div>
            <div className="stat"><div className="k">Synthesis</div><div className="v">{fpga?.status ?? '…'}<small> · yosys</small></div></div>
            <div className="stat"><div className="k">Logic cells</div><div className="v">{fpga?.total_cells ?? '—'}</div></div>
            <div className="stat"><div className="k">RTL sim</div><div className="v">{fpga?.sim?.status === 'pass' ? 'PASS' : '—'}<small> · {fpga?.sim?.scenarios} tests</small></div></div>
          </div>

          <div className="grid cols-2">
            {/* LEFT: resource utilisation + quality gates */}
            <div className="card">
              <h2><span className="dot" />Resource utilisation vs XC7A35T</h2>
              <div className="hint">yosys synth_xilinx estimate (no P&amp;R / no timing). The 64 detector channels read fully in parallel — no analog MUX, no XADC.</div>
              {fpgaLoading && <div className="muted"><span className="spinner" /> loading synthesis report…</div>}
              {fpgaErr && <div className="error">{fpgaErr}</div>}
              {!fpgaLoading && !fpgaErr && fpga?.status === 'NO_SYNTHESIS' && (
                <div className="muted" style={{ padding: '10px 0' }}>
                  No synthesis run found — run <span className="mono">fpga/synth/run_synth.sh</span> (yosys) to populate the resource estimate. The RTL module list + command protocol below remain available.
                </div>
              )}
              {!fpgaLoading && !fpgaErr && fpga?.status === 'PARSE_ERROR' && (
                <div className="error" style={{ marginBottom: 8 }}>Could not parse <span className="mono">fpga/synth/synth.log</span> — re-run run_synth.sh.</div>
              )}
              {fpga?.resources?.map(r => (
                <div className="bar-row" key={r.name}>
                  <span className="muted mono" style={{ fontSize: 11.5 }}>{r.label}</span>
                  <div className="bar-track"><div className="bar-fill" style={{ width: `${Math.min(100, r.pct)}%` }} /></div>
                  <span className="mono" style={{ fontSize: 11, textAlign: 'right' }}>{r.pct}%</span>
                </div>
              ))}
              {fpga?.resources?.length > 0 && (
                <div className="legend" style={{ marginTop: 12 }}>
                  {fpga.resources.map(r => (
                    <div key={r.name}><b>{r.used}</b> / {r.available} {r.label}</div>
                  ))}
                </div>
              )}
              {fpga?.resources?.length > 0 && fpga?.quality && (
                <div className="row" style={{ marginTop: 14, marginBottom: 0 }}>
                  <div className="stat" style={{ flex: 1, padding: '9px 10px' }}>
                    <div className="k">Latches</div><span className={`chip ${fpga.quality.latches ? 'bad' : 'ok'}`}>{fpga.quality.latches} {fpga.quality.latches ? '⚠' : '✓'}</span>
                  </div>
                  <div className="stat" style={{ flex: 1, padding: '9px 10px' }}>
                    <div className="k">Blackboxes</div><span className={`chip ${fpga.quality.blackboxes ? 'bad' : 'ok'}`}>{fpga.quality.blackboxes} {fpga.quality.blackboxes ? '⚠' : '✓'}</span>
                  </div>
                  <div className="stat" style={{ flex: 1, padding: '9px 10px' }}>
                    <div className="k">Width warns</div><span className={`chip ${fpga.quality.width ? 'warn' : 'ok'}`}>{fpga.quality.width} {fpga.quality.width ? '⚠' : '✓'}</span>
                  </div>
                  <div className="stat" style={{ flex: 1, padding: '9px 10px' }}>
                    <div className="k">Fits XC7A35T</div><span className={`chip ${fpga.quality.fits ? 'ok' : 'bad'}`}>{fpga.quality.fits ? 'yes ✓' : 'no ⚠'}</span>
                  </div>
                </div>
              )}
            </div>

            {/* RIGHT: RTL modules */}
            <div className="card">
              <h2><span className="dot" />RTL modules <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>({fpga?.modules?.length ?? 0})</span></h2>
              <div className="hint">fpga/rtl/*.v — top <span className="mono">top_level</span> wires UART ↔ command parser ↔ SPI / ADC / emitter / MW.</div>
              <div className="fpga-modlist">
                {fpga?.modules?.map(m => (
                  <div className="fpga-mod" key={m.file}>
                    <span className="mono fpga-modname">{m.file}</span>
                    <span className="muted">{m.desc}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* CONTROL PANEL */}
          <div className="section-title">FPGA control — UART command protocol</div>
          <div className="grid cols-2">
            <div className="card">
              <h2><span className="dot" />Command the FPGA {fpgaBusy ? <span className="spinner" style={{ marginLeft: 8 }} /> : null}</h2>
              <div className="hint">Drives the SAME SimulatedFPGA the Qiskit backend uses (or a real board if QSSC_FPGA_PORT is set). Stateful: CALIBRATE builds the LUT, SET_EMITTER arms, READ reads. Localhost-only.</div>

              <div className="fpga-quickrow">
                <button type="button" onClick={() => sendFpga('READ_DETECTORS')}>READ_DETECTORS</button>
                <button type="button" onClick={() => sendFpga('CALIBRATE')}>CALIBRATE</button>
                <button type="button" onClick={() => sendFpga('CLEAR_EMITTERS')}>CLEAR_EMITTERS</button>
                <button type="button" onClick={() => sendFpga('CLEAR_DETECTORS')}>CLEAR_DETECTORS</button>
              </div>

              <div className="fpga-fieldset">
                <div className="fpga-legend">Emitter array · 3× TLC5947, 64ch</div>
                <div className="row" style={{ marginBottom: 8 }}>
                  <div className="field" style={{ flex: '0 0 70px' }}><label>index 0..63</label><input type="number" min="0" max="63" value={emIdx} onChange={e => setEmIdx(+e.target.value)} /></div>
                  <div className="field" style={{ flex: '0 0 90px' }}><label>duration ms</label><input type="number" min="0" value={emDur} onChange={e => setEmDur(+e.target.value)} /></div>
                  <div className="field" style={{ flex: '0 0 100px' }}><label>level 0..4095</label><input type="number" min="0" max="4095" value={emLvl} onChange={e => setEmLvl(+e.target.value)} /></div>
                </div>
                <div className="fpga-quickrow">
                  <button type="button" onClick={() => sendFpga(`DRIVE_EMITTER ${emIdx} ${emDur}`)}>DRIVE_EMITTER</button>
                  <button type="button" onClick={() => sendFpga(`SET_EMITTER ${emIdx} ${emLvl}`)}>SET_EMITTER</button>
                  <button type="button" onClick={() => sendFpga(`DRIVE_PATTERN ${emDur}`)}>DRIVE_PATTERN</button>
                </div>
              </div>

              <div className="fpga-fieldset">
                <div className="fpga-legend">Detector readout · 4× AD7616, 16-bit</div>
                <div className="row" style={{ marginBottom: 0 }}>
                  <div className="field" style={{ flex: '0 0 90px' }}><label>site 0..63</label><input type="number" min="0" max="63" value={detSite} onChange={e => setDetSite(+e.target.value)} /></div>
                  <div className="field" style={{ justifyContent: 'flex-end' }}><label>&nbsp;</label>
                    <button type="button" onClick={() => sendFpga(`READ_DETECTOR ${detSite}`)}>READ_DETECTOR</button>
                  </div>
                </div>
              </div>

              <div className="fpga-fieldset">
                <div className="fpga-legend">Microwave pulse · ADF4351 PLL</div>
                <div className="row" style={{ marginBottom: 8 }}>
                  <div className="field" style={{ flex: '0 0 70px' }}><label>qubit</label><input type="number" min="0" max="63" value={mwQ} onChange={e => setMwQ(+e.target.value)} /></div>
                  <div className="field" style={{ flex: 1 }}><label>freq kHz</label><input type="number" min="0" value={mwFreq} onChange={e => setMwFreq(+e.target.value)} /></div>
                  <div className="field" style={{ flex: '0 0 70px' }}><label>amp</label><input type="number" min="0" value={mwAmp} onChange={e => setMwAmp(+e.target.value)} /></div>
                </div>
                <div className="row" style={{ marginBottom: 8 }}>
                  <div className="field" style={{ flex: '0 0 110px' }}><label>duration ns</label><input type="number" min="0" value={mwDur} onChange={e => setMwDur(+e.target.value)} /></div>
                  <div className="field" style={{ flex: '0 0 110px' }}><label>phase mrad</label><input type="number" value={mwPhase} onChange={e => setMwPhase(+e.target.value)} /></div>
                </div>
                <div className="fpga-quickrow">
                  <button type="button" onClick={() => sendFpga(`APPLY_PULSE ${mwQ} ${mwFreq} ${mwAmp} ${mwDur} ${mwPhase}`)}>APPLY_PULSE</button>
                </div>
              </div>

              <div className="fpga-fieldset">
                <div className="fpga-legend">Raw command</div>
                <div className="row" style={{ marginBottom: 0 }}>
                  <div className="field" style={{ flex: 1 }}><label>command line</label>
                    <input type="text" value={rawCmd} placeholder="e.g. READ_DETECTOR 3" onChange={e => setRawCmd(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') sendFpga(rawCmd.trim()) }} />
                  </div>
                  <div className="field" style={{ justifyContent: 'flex-end' }}><label>&nbsp;</label>
                    <button type="button" onClick={() => sendFpga(rawCmd.trim())}>Send</button>
                  </div>
                </div>
              </div>

              <div className="fpga-quickrow" style={{ marginTop: 2 }}>
                <button type="button" className="ghost" onClick={() => setCmdRefOpen(true)}>Command reference</button>
              </div>
            </div>

            {/* RIGHT: reply + detector heatmap + log */}
            <div className="card">
              <h2><span className="dot" />Reply &amp; readout</h2>
              <div className="hint">Last reply highlighted; the full command log is below.</div>
              {!fpgaReply && (
                <div className="fpga-emptyhelp">
                  <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>Available firmware commands — click one to run it, or type it in the Raw command box.</div>
                  <div className="fpga-helpscroll">
                  {(fpga?.protocol?.length >= FALLBACK_PROTOCOL.length ? fpga.protocol : FALLBACK_PROTOCOL).map(p => (
                    <button key={p.cmd} type="button" className="fpga-helpcmd" onClick={() => sendFpga(p.args === '—' ? p.cmd : `${p.cmd} ${p.cmd === 'READ_RNG' ? '32' : p.cmd === 'READ_DETECTOR' ? '0' : p.cmd === 'DRIVE_EMITTER' ? '0 50' : p.cmd === 'SET_EMITTER' ? '0 4095' : p.cmd === 'DRIVE_PATTERN' ? '50' : p.cmd === 'APPLY_PULSE' ? '0 2870000 63 200 0' : p.cmd === 'ODMR_SWEEP' ? '0 2860000 2880000 64' : p.cmd === 'READ_COMPASS' ? '5000' : p.cmd === 'READ_GYRO' ? '1000000' : p.cmd === 'READ_LOCKIN' ? '1000 500' : p.cmd === 'RUN_SEQUENCE' ? 'ramsey 8' : ''}`.trim())}>
                      <span className="mono fpga-helpcmd-name">{p.cmd}{p.args !== '—' ? <span className="muted"> {p.args}</span> : null}</span>
                      <span className="fpga-helpcmd-desc">{p.desc}</span>
                    </button>
                  ))}
                  </div>
                </div>
              )}
              {fpgaReply && (
                <>
                  <div className={`verdict ${fpgaReply.ok ? 'ok' : 'bad'}`} style={{ marginBottom: 12 }}>
                    <div className="verdict-h">{fpgaReply.ok ? 'OK ✓' : 'ERR'}<span className="verdict-tag">{fpgaReply.command.split(' ')[0]}</span></div>
                    <div className="verdict-body">
                      <div className="mono" style={{ fontSize: 12 }}>&gt; {fpgaReply.command}</div>
                      <div className="mono" style={{ fontSize: 12, color: fpgaReply.ok ? 'var(--ok)' : 'var(--err)' }}>← {fpgaReply.reply}</div>
                    </div>
                  </div>
                  {fpgaReply.command.startsWith('READ_DETECTORS') && (() => {
                    const vals = fpgaReply.reply.split(/\s+/).filter(Boolean).map(Number)
                    if (vals.length < 64) return null
                    const mx = Math.max(...vals, 1)
                    return (
                      <div className="fpga-heatmap">
                        <div className="fpga-hm-title">64-channel AD7616 frame · 8×8 (normalised to {mx})</div>
                        <div className="fpga-hm-grid">
                          {Array.from({ length: 8 }, (_, row) => (
                            <div className="fpga-hm-row" key={row}>
                              {vals.slice(row * 8, row * 8 + 8).map((v, i) => (
                                <div className="fpga-hm-cell" key={i} title={`ch ${row * 8 + i}: ${v}`}
                                  style={{ background: `rgba(34,211,238,${(v / mx).toFixed(3)})` }}>
                                  <span>{v}</span>
                                </div>
                              ))}
                            </div>
                          ))}
                        </div>
                      </div>
                    )
                  })()}
                </>
              )}
              <div className="fpga-log">
                <div className="muted" style={{ fontSize: 11.5, marginBottom: 6 }}>Command log · most recent first</div>
                {fpgaLog.length === 0 && <div className="muted">—</div>}
                {fpgaLog.map((l, i) => (
                  <div className="fpga-logrow" key={i}>
                    <span className="mono fpga-logt">{l.t}</span>
                    <span className={`fpga-logok ${l.ok ? 'ok' : 'bad'}`}>{l.ok ? '✓' : '✗'}</span>
                    <span className="mono fpga-logcmd">{l.command}</span>
                    <span className="mono muted fpga-logreply">{l.reply.length > 64 ? l.reply.slice(0, 64) + '…' : l.reply}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* COMMAND-REFERENCE MODAL (replaces the inline protocol-reference section) */}
          {cmdRefOpen && (() => {
            const proto = (fpga?.protocol?.length >= FALLBACK_PROTOCOL.length ? fpga.protocol : FALLBACK_PROTOCOL)
            return (
              <div className="cmd-ref-overlay" onClick={() => setCmdRefOpen(false)} role="dialog" aria-modal="true" aria-label="Firmware command set">
                <div className="cmd-ref-modal" onClick={e => e.stopPropagation()}>
                  <button type="button" className="cmd-ref-close" onClick={() => setCmdRefOpen(false)} aria-label="Close command reference">✕</button>
                  <h2><span className="dot" />Firmware command set <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>(fpga/rtl/command_parser.v ↔ qssc.simulator)</span></h2>
                  <div className="hint">The Python FPGAConnection and the RTL command parser implement exactly these verbs. SET_THRESHOLD was removed by the AD7616 pivot → ERR.</div>
                  <div className="fpga-proto">
                    <div className="fpga-proto-hd">Command</div><div className="fpga-proto-hd">Args</div><div className="fpga-proto-hd">Reply</div><div className="fpga-proto-hd">Description</div>
                    {proto.map(p => (
                      <React.Fragment key={p.cmd}>
                        <div className="mono fpga-proto-cmd">{p.cmd}</div>
                        <div className="mono fpga-proto-args">{p.args}</div>
                        <div className="mono fpga-proto-reply">{p.reply}</div>
                        <div className="muted fpga-proto-desc">{p.desc}</div>
                      </React.Fragment>
                    ))}
                  </div>
                </div>
              </div>
            )
          })()}

          {/* HARDWARE QRNG — physical entropy from the AD7616 noise floor */}
          <div className="section-title">Hardware QRNG — physical entropy from the AD7616 noise floor</div>
          <div className="card">
            <h2><span className="dot" />Quantum RNG <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>(qrng_entropy.v · READ_RNG)</span></h2>
            <div className="hint">
              A <b>dark</b> AD7616 channel's LSB is dominated by thermal/Johnson + ADC quantization noise (a dark channel has no photocurrent, hence no shot noise). The FPGA pairs successive samples through a von-Neumann extractor (01→0, 10→1, 00/11 discarded) and packs debiased bits into 32-bit words. The 32-byte seed drives an Ed25519 keypair + self-signed X.509 device cert.
            </div>
            <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
              <div className="field"><label>Entropy bytes</label>
                <select value={qrngBytes} onChange={e => setQrngBytes(+e.target.value)}>
                  <option value={32}>32 (Ed25519 seed)</option>
                  <option value={64}>64</option>
                  <option value={128}>128</option>
                </select>
              </div>
              <div className="field" style={{ flex: 1, minWidth: 180 }}><label>Cert common name</label>
                <input type="text" value={qrngCN} onChange={e => setQrngCN(e.target.value)} />
              </div>
              <div className="field" style={{ justifyContent: 'flex-end' }}><label>&nbsp;</label>
                <button className="btn" onClick={runQrng} disabled={qrngLoading}>
                  {qrngLoading ? <><span className="spinner" /> Harvesting…</> : 'Generate hardware key'}
                </button>
              </div>
            </div>
            {qrngErr && <div className="error">{qrngErr}</div>}
            {qrng && (
              <div className="fpga-qrng-out">
                <div className="row" style={{ marginBottom: 8, gap: 10, flexWrap: 'wrap' }}>
                  <div className="stat" style={{ flex: '0 0 auto', padding: '8px 10px' }}><div className="k">Source</div><div className="v" style={{ fontSize: 12 }}>{qrng.source}</div></div>
                  <div className="stat" style={{ flex: '0 0 auto', padding: '8px 10px' }}><div className="k">Algorithm</div><div className="v">{qrng.algorithm}</div></div>
                  <div className="stat" style={{ flex: '0 0 auto', padding: '8px 10px' }}><div className="k">Serial</div><div className="v mono" style={{ fontSize: 11 }}>{qrng.serial}</div></div>
                  <div className="stat" style={{ flex: '0 0 auto', padding: '8px 10px' }}><div className="k">Extract rate</div><div className="v">{(qrng.extraction?.extract_rate * 100).toFixed(1)}%<small> · {qrng.extraction?.kept}/{qrng.extraction?.raw_bits} bits</small></div></div>
                </div>
                <div className="muted" style={{ fontSize: 11, margin: '6px 0 3px' }}>Entropy (hex, {qrng.n_bytes} B)</div>
                <pre className="mono qrng-hex">{qrng.entropy_hex}</pre>
                <details style={{ marginTop: 8 }}>
                  <summary className="muted" style={{ cursor: 'pointer', fontSize: 12 }}>Ed25519 public key + X.509 certificate (PEM)</summary>
                  <pre className="mono qrng-pem">{qrng.public_key_pem}\n{qrng.certificate_pem}</pre>
                </details>
              </div>
            )}
          </div>

        </>
      ) : mainTab === 'sensors' ? (
        /* ============================= SENSORS (on-chip quantum sensors) ============================= */
        <>
          <div className="seg-toggle" role="group" aria-label="FPGA environment">
            <span className="seg-label">Environment</span>
            <button className={`seg ${hwMode === 'simulator' ? 'on' : ''}`} onClick={() => setHwMode('simulator')}>
              <span className="seg-ic">◈</span> Simulator
            </button>
            <button className={`seg hw ${hwMode === 'hardware' ? 'on' : ''}`} onClick={() => setHwMode('hardware')}>
              <span className="seg-ic">⌬</span> Hardware
            </button>
            <span className="seg-note">
              {hwMode === 'simulator'
                ? 'Aer + SimulatedFPGA — averages freely, cleaner readout (no shot-noise floor)'
                : 'real ADF4351 + 64-ch AD7616 chain — compass from live ODMR; gyro / lock-in / sequence measured (RUN_SEQUENCE + on-host fit)'}
            </span>
          </div>

          {/* Visual subtabs within Sensors (replaces the section-title
              timeline): Quantum sensors (compass/gyro/magneto) / Field
              imaging (ferro/nav/thermal/current). Corporate line icons. */}
          <nav className="subtabs" role="tablist" aria-label="Sensors sections">
            {[
              { k: 'quantum', label: 'Quantum sensors', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M15.5 8.5l-2 5-5 2 2-5 5-2z" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /></svg> },
              { k: 'field', label: 'Field imaging', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M5 8a3.5 3.5 0 017 0v8a3.5 3.5 0 11-7 0V8z" /><path d="M12 8a3.5 3.5 0 017 0v8a3.5 3.5 0 11-7 0V8z" /></svg> },
            ].map(t => (
              <button key={t.k} role="tab" type="button" aria-selected={sensorsSub === t.k}
                className={`subtab ${sensorsSub === t.k ? 'on' : ''}`} onClick={() => setSensorsSub(t.k)}>
                <span className="subtab-ic">{t.icon}</span>
                <span className="subtab-lb">{t.label}</span>
              </button>
            ))}
          </nav>
          {sensorsSub === 'quantum' && (<>
          <div className="section-title">On-chip quantum sensors — compass · gyroscope · magneto-optic image</div>
          <div className="hint" style={{ marginBottom: 14, color: 'var(--dim)', fontSize: 12.5 }}>
            The same ADF4351 → J3 → A1 MW chain and 64-channel AD7616 readout run as quantum sensors.
            Sweep the 4 NV &lt;111&gt; axes for the ODMR compass (B-vector → heading), run a Ramsey
            π/2–T–π/2 for the gyroscope, and image the per-pixel field across the 8×8 array.
            Opening this tab auto-captures an initial reading on all three; toggle the environment
            above (or press Measure) to re-run.
          </div>
          <div className="sensor-grid">
            <div className="card">
              <h2><span className="dot" />ODMR compass (magnetometer)</h2>
              <div className="hint">Zeeman doublet split per &lt;111&gt; axis reconstructs the ambient B-vector → heading + elevation. Default is a ~188 µT lab field (resolves ≥3 axes at the NV ~5 MHz linewidth); dial to Earth's field to see the linewidth limit.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Bx (µT)</label><input type="number" step="any" value={compassBx} onChange={e => setCompassBx(+e.target.value)} /></div>
                <div className="field"><label>By (µT)</label><input type="number" step="any" value={compassBy} onChange={e => setCompassBy(+e.target.value)} /></div>
                <div className="field"><label>Bz (µT)</label><input type="number" step="any" value={compassBz} onChange={e => setCompassBz(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={compassAvg} onChange={e => setCompassAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runCompass} disabled={compassLoading} style={{ alignSelf: 'flex-end' }}>
                  {compassLoading ? 'Sweeping…' : 'Measure'}
                </button>
              </div>
              <CompassRose data={compass} error={compassErr} loading={compassLoading} />
            </div>

            <div className="card">
              <h2><span className="dot" />Ramsey gyroscope{hwMode === 'hardware' && <span className="pill" style={{ marginLeft: 8, fontSize: 10.5, color: 'var(--good)' }}>measured · RUN_SEQUENCE + on-host fit</span>}</h2>
              <div className="hint">π/2–T–π/2; the accumulated phase Ω·T is the rotation rate.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Rate (°/s)</label><input type="number" step="any" value={gyroRate} onChange={e => setGyroRate(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={gyroAvg} onChange={e => setGyroAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runGyro} disabled={gyroLoading} style={{ alignSelf: 'flex-end' }}>
                  {gyroLoading ? 'Running…' : 'Measure'}
                </button>
              </div>
              <GyroPanel data={gyro} error={gyroErr} loading={gyroLoading} />
            </div>

            <div className="card" style={{ gridColumn: '1 / -1' }}>
              <h2><span className="dot" />Magneto-optic image (64-pixel array)</h2>
              <div className="hint">The FPGA returns all 64 AD7616 samples in one parallel frame; each pixel's ODMR doublet gives |B·n| → an 8×8 field image. Tune the gradient + averages and watch the sensitivity and structure resolve.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Gradient (µT/mm)</label><input type="number" step="any" value={magGrad} onChange={e => setMagGrad(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={magAvg} onChange={e => setMagAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runMag} disabled={magLoading} style={{ alignSelf: 'flex-end' }}>
                  {magLoading ? 'Reading array…' : 'Image'}
                </button>
              </div>
              <MagnetoImage data={mag} error={magErr} loading={magLoading} />
            </div>
          </div>
          </>)}

          {/* =================== CONTACTLESS FIELD IMAGING =================== */}
          {sensorsSub === 'field' && (<>
          <div className="section-title">Contactless field imaging — ferromagnetic · geomagnetic nav · temperature · current</div>
          <div className="hint" style={{ marginBottom: 14, color: 'var(--dim)', fontSize: 12.5 }}>
            Magnetic fields penetrate fog, smoke, water, tissue, packaging and non-magnetic walls — so the same
            64-pixel array images the source through whatever is in the way. Each card models a different source:
            a ferromagnetic dipole (object detection), the crustal anomaly field (GPS-denied navigation), a
            temperature field (NV D-shift thermometry), or a current (Ampère).
          </div>
          <div className="sensor-grid">
            <div className="card">
              <h2><span className="dot" />Ferromagnetic detection (see through walls)</h2>
              <div className="hint">A ferromagnetic object is a magnetic dipole; the array images its field through packaging, walls, tissue, soil. Detection fires when the peak clears the noise floor; bearing + distance locate it.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Moment (A·m²)</label><input type="number" step="any" value={ferroMoment} onChange={e => setFerroMoment(+e.target.value)} /></div>
                <div className="field"><label>Distance (mm)</label><input type="number" step="any" value={ferroDist} onChange={e => setFerroDist(+e.target.value)} /></div>
                <div className="field"><label>Offset X (mm)</label><input type="number" step="any" value={ferroOffX} onChange={e => setFerroOffX(+e.target.value)} /></div>
                <div className="field"><label>Offset Y (mm)</label><input type="number" step="any" value={ferroOffY} onChange={e => setFerroOffY(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={fieldAvg} onChange={e => setFieldAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runFerro} disabled={ferroLoading} style={{ alignSelf: 'flex-end' }}>{ferroLoading ? 'Sweeping…' : 'Measure'}</button>
              </div>
              <SensorField data={ferro} error={ferroErr} loading={ferroLoading} promptText="Press Measure to image the dipole field." />
            </div>

            <div className="card">
              <h2><span className="dot" />Geomagnetic navigation (GPS-denied)</h2>
              <div className="hint">Match the local crustal-magnetic anomaly against a stored map to estimate position — works underwater / underground / indoors where GPS and optical both fail. ● true position, ◆ matched.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Pos X</label><input type="number" step="any" value={navPosX} onChange={e => setNavPosX(+e.target.value)} /></div>
                <div className="field"><label>Pos Y</label><input type="number" step="any" value={navPosY} onChange={e => setNavPosY(+e.target.value)} /></div>
                <div className="field"><label>Anomaly (µT)</label><input type="number" step="any" value={navAnom} onChange={e => setNavAnom(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={fieldAvg} onChange={e => setFieldAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runNav} disabled={navLoading} style={{ alignSelf: 'flex-end' }}>{navLoading ? 'Matching…' : 'Navigate'}</button>
              </div>
              <SensorField data={nav} error={navErr} loading={navLoading} promptText="Press Navigate to match the anomaly map." />
            </div>

            <div className="card">
              <h2><span className="dot" />Temperature imaging (NV thermometry)</h2>
              <div className="hint">The NV D resonance shifts with temperature, so the array is a contactless thermal image — a chip hotspot or a surface's map read through the diamond. Resolution scales with averages.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Base (K)</label><input type="number" step="any" value={thermalBase} onChange={e => setThermalBase(+e.target.value)} /></div>
                <div className="field"><label>Hotspot (K)</label><input type="number" step="any" value={thermalHot} onChange={e => setThermalHot(+e.target.value)} /></div>
                <div className="field"><label>Hotspot X</label><input type="number" step="any" value={thermalHX} onChange={e => setThermalHX(+e.target.value)} /></div>
                <div className="field"><label>Hotspot Y</label><input type="number" step="any" value={thermalHY} onChange={e => setThermalHY(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={fieldAvg} onChange={e => setFieldAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runThermal} disabled={thermalLoading} style={{ alignSelf: 'flex-end' }}>{thermalLoading ? 'Reading…' : 'Measure'}</button>
              </div>
              <SensorField data={thermal} error={thermalErr} loading={thermalLoading} promptText="Press Measure to image the temperature field." />
            </div>

            <div className="card">
              <h2><span className="dot" />Current imaging (Ampère, through enclosures)</h2>
              <div className="hint">Every current makes a B field (µ₀I / 2πr); the array images it contactlessly — short-circuit localization, battery-cell current signatures, chip power-rail mapping, all through the enclosure.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Current (A)</label><input type="number" step="any" value={currentI} onChange={e => setCurrentI(+e.target.value)} /></div>
                <div className="field"><label>Depth (mm)</label><input type="number" step="any" value={currentDepth} onChange={e => setCurrentDepth(+e.target.value)} /></div>
                <div className="field"><label>Wire offset (mm)</label><input type="number" step="any" value={currentOff} onChange={e => setCurrentOff(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={fieldAvg} onChange={e => setFieldAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runCurrent} disabled={currentLoading} style={{ alignSelf: 'flex-end' }}>{currentLoading ? 'Reading…' : 'Measure'}</button>
              </div>
              <SensorField data={current} error={currentErr} loading={currentLoading} promptText="Press Measure to image the current field." />
            </div>
          </div>
          </>)}
        </>
      ) : mainTab === 'code' ? (
        /* ============================= CODE (scripts) ============================= */
        <div className="grid cols-2">
          <div className="card">
            <h2><span className="dot" />Script editor</h2>
            <div className="hint">Save Qiskit scripts (Python snippet or OpenQASM 2). Saved scripts appear as loadable entries in the Hardware circuit runner. The <b>QSSC backend</b> example below shows the direct Python API (simulator + real hardware) and runs locally — the circuit snippets run in the browser.</div>
            <div className="row">
              <div className="field" style={{ flex: 1 }}>
                <label>Name</label>
                <input type="text" value={edName} placeholder="my-script" onChange={e => setEdName(e.target.value)} />
              </div>
              <div className="field" style={{ justifyContent: 'flex-end' }}>
                <label>&nbsp;</label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="ghost" type="button" onClick={newScript}>New</button>
                  <button type="button" onClick={saveScript}>{edId ? 'Update' : 'Save'}</button>
                </div>
              </div>
            </div>
            <div className="row" style={{ marginTop: 6 }}>
              <div className="field" style={{ flex: 1 }}>
                <label>Load example</label>
                <select defaultValue="" onChange={e => { const k = e.target.value; if (k && CUSTOM_EXAMPLES[k]) { setEdCode(CUSTOM_EXAMPLES[k]); setEdName(k); e.target.value = ''; } }}>
                  <option value="">pick one…</option>
                  <option value="bell">Bell pair — runs in browser</option>
                  <option value="ghz">GHZ — runs in browser</option>
                  <option value="qft">QFT — runs in browser</option>
                  <option value="qasm">OpenQASM 2 — runs in browser</option>
                  <option value="qssc_backend">QSSC backend — simulator + real HW (local Python)</option>
                </select>
              </div>
            </div>
            <textarea className="code-editor" value={edCode} spellCheck={false}
              onChange={e => setEdCode(e.target.value)} rows={20} />
            {scriptMsg && <div className="muted" style={{ marginTop: 8, fontSize: 12.5 }}>{scriptMsg}</div>}
          </div>
          <div className="card">
            <h2><span className="dot" />Saved scripts <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>({scripts.length})</span></h2>
            <div className="hint">Click to edit, or load straight into the Hardware runner.</div>
            {scriptsLoading && <div className="muted"><span className="spinner" /> loading…</div>}
            {!scriptsLoading && scripts.length === 0 && <div className="muted">No scripts saved yet.</div>}
            <div className="histlist">
              {scripts.map(s => (
                <div className="histrow" key={s.id} style={{ cursor: 'default' }}>
                  <div className="hist-main">
                    <span className="halgo" style={{ cursor: 'pointer' }} onClick={() => editScript(s)}>{s.name}</span>
                    <span className="muted mono" style={{ fontSize: 11 }}>{(s.code || '').split('\n')[0].slice(0, 40)}</span>
                  </div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button className="ghost" type="button" style={{ padding: '4px 9px', fontSize: 11 }} onClick={() => loadScriptToRunner(s)}>load</button>
                    <button className="ghost" type="button" style={{ padding: '4px 9px', fontSize: 11 }} onClick={() => editScript(s)}>edit</button>
                    <button className="ghost" type="button" style={{ padding: '4px 9px', fontSize: 11, color: 'var(--err)' }} onClick={() => removeScript(s.id)}>del</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : mainTab === 'history' ? (
        /* ============================= HISTORIAL ============================= */
        <div className="card">
          <div className="row" style={{ alignItems: 'center', marginBottom: 14 }}>
            <h2 style={{ margin: 0 }}><span className="dot" />Execution history</h2>
            <button className="ghost" type="button" onClick={refreshHistory} style={{ marginLeft: 'auto' }}>
              {historyLoading ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
          <div className="hint" style={{ marginBottom: 12 }}>
            Every run is persisted in MongoDB. Click a row to load its result back into the Hardware tab.
          </div>
          {history.length === 0 && !historyLoading && <div className="muted">No executions yet — run a circuit in the Hardware tab.</div>}
          <div className="histlist">
            {history.map(h => (
              <div className="histrow" key={h.job_id} onClick={() => loadFromHistory(h)}>
                <div className="hist-main">
                  <span className={`hstatus ${h.status}`}>{h.status}</span>
                  <span className="mono halgo">{h.algorithm}</span>
                  <span className="muted">{h.profile}</span>
                  <span className="muted">{h.temperature_k} K</span>
                  <span className="muted">{h.shots} shots</span>
                  <span className="muted">{h.noise_mode}</span>
                </div>
                <div className="hist-time muted mono">{h.created_at ? new Date(h.created_at).toLocaleTimeString() : ''}</div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        /* ============================= HARDWARE (default) ============================= */
        <>
          {/* Visual subtabs within Circuits (replaces the section-title
              timeline): Circuit runner / Backend physics / Spectroscopy &
              calibration / Detected signal / Settings. Corporate line icons
              per tab. The Settings tab (last) holds the global controls card
              (64-parallel toggle, environment, defect platform, temperature,
              execution mode, RNG seed, pixel mode, prime denoise). */}
          <nav className="subtabs" role="tablist" aria-label="Circuits sections">
            {[
              { k: 'runner', label: 'Circuit runner', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="7" y="7" width="10" height="10" rx="1.5" /><path d="M10 7V4M14 7V4M10 20v-3M14 20v-3M7 10H4M7 14H4M20 10h-3M20 14h-3" /></svg> },
              { k: 'physics', label: 'Backend physics', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" /><ellipse cx="12" cy="12" rx="9" ry="3.6" /><ellipse cx="12" cy="12" rx="9" ry="3.6" transform="rotate(60 12 12)" /><ellipse cx="12" cy="12" rx="9" ry="3.6" transform="rotate(120 12 12)" /></svg> },
              { k: 'spectro', label: 'Spectroscopy & calibration', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3 9h6l2 7 2-12 2 6h6" /></svg> },
              { k: 'signal', label: 'Detected signal', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12h4V6h6v12h6V9h2" /></svg> },
              { k: 'settings', label: 'Settings', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg> },
            ].map(t => (
              <button key={t.k} role="tab" type="button" aria-selected={circuitsSub === t.k}
                className={`subtab ${circuitsSub === t.k ? 'on' : ''}`} onClick={() => setCircuitsSub(t.k)}>
                <span className="subtab-ic">{t.icon}</span>
                <span className="subtab-lb">{t.label}</span>
              </button>
            ))}
          </nav>
          {circuitsSub === 'settings' && (<>
          {/* ============================= GLOBAL CONTROLS ============================= */}
          <div className="card" style={{ marginBottom: 18 }}>
            {/* Two top-level switches in one row, two columns: the 64-parallel
                ON/OFF toggle (which Run + Calibrate set is shown below) and the
                Simulator / Hardware-real environment. */}
            <div className="row" style={{ marginBottom: 12, alignItems: 'flex-start', gap: 18 }}>
            {/* The single "64-parallel ON/OFF" switch. OFF = single-pixel path
                (Circuit Runner + per-pixel ODMR calibration); ON = 64-pixel
                array path (array calibration + run-parallel). The bottom half
                of this tab swaps accordingly, so there is exactly one Run and
                one Calibrate visible at any time — no duplicated panels. */}
            <div className="seg-toggle" role="group" aria-label="64-parallel mode" style={{ flex: 1, marginBottom: 0 }}>
              <span className="seg-label">64-parallel</span>
              <button className={`seg ${!parallelMode ? 'on' : ''}`} onClick={() => setParallelMode(false)}>
                <span className="seg-ic">◇</span> OFF · single pixel
              </button>
              <button className={`seg ${parallelMode ? 'on' : ''}`} onClick={() => setParallelMode(true)}>
                <span className="seg-ic">▦</span> ON · 64 pixels
              </button>
              <span className="seg-note">
                {parallelMode
                  ? '64-pixel array — calibrate the array, then run one circuit across all pixels (64× throughput).'
                  : 'Single pixel — run one circuit on one pixel, calibrate its ODMR register.'}
              </span>
            </div>
            {/* Top-level Simulator / Hardware-real switch. Green when Hardware
                real is selected. In Hardware-real mode the Aer noise-mode
                selector is hidden (the real board's physics replace it) and a
                probe indicator shows whether a board answered the ID handshake. */}
            <div className="seg-toggle" role="group" aria-label="Execution environment" style={{ flex: 1, marginBottom: 0 }}>
              <span className="seg-label">Environment</span>
              <button className={`seg ${hwMode === 'simulator' ? 'on' : ''}`} onClick={() => setHwMode('simulator')}>
                <span className="seg-ic">◈</span> Simulator
              </button>
              <button className={`seg hw ${hwMode === 'hardware' ? 'on' : ''}`} onClick={() => setHwMode('hardware')}>
                <span className="seg-ic">⌬</span> Hardware
              </button>
              <span className="seg-note">
                {hwMode === 'simulator'
                  ? 'Aer computes the circuit under the chip physics; on-chip I/O modelled by SimulatedFPGA'
                  : probe.loading
                    ? 'probing the board…'
                    : probe.data && probe.data.connected
                      ? `FPGA connected — ${(probe.data.id || 'QSSC').split('\n')[0]} on ${probe.data.port}`
                      : 'No FPGA detected — connect the board and set QSSC_FPGA_PORT, or switch to Simulator'}
              </span>
            </div>
            </div>
            <div className="row" style={{ marginBottom: 0 }}>
              <div className="field">
                <label>Defect platform</label>
                <select value={profile} onChange={e => setProfile(e.target.value)}>
                  {PROFILES.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
                </select>
              </div>
              <div className="field">
                <label>Temperature (K) <span className="muted">· Peltier setpoint 295 K</span></label>
                <input type="number" min="0.001" max="600" step="any" value={temperature} onChange={e => setTemperature(+e.target.value)} />
              </div>
              {hwMode === 'simulator' ? (
                <div className="field">
                  <label>Execution mode</label>
                  <select value={noiseMode} onChange={e => setNoiseMode(e.target.value)}>
                    {NOISE_MODES.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </select>
                </div>
              ) : (
                <div className="field">
                  <label>Execution mode</label>
                  <select disabled value="hardware">
                    <option value="hardware">Hardware (board physics)</option>
                  </select>
                </div>
              )}
              <div className="field">
                <label>RNG seed</label>
                <input type="number" value={seed} onChange={e => setSeed(+e.target.value)} />
              </div>
            </div>
            {/* Pixel mode (the "multiqbit mode" switch) + Prime-denoise toggle.
                Both run on the existing firmware (ODMR_SWEEP + READ_DETECTORS +
                READ_LOCKINP); no PCB change. The HW badge comes from
                /api/modes. In ensemble mode a 2q circuit is rejected by the
                backend (no within-pixel 2q at 300 K), so a hint says so. */}
            <div className="row" style={{ marginBottom: 0, marginTop: 4 }}>
              <div className="field">
                <label>Pixel mode <span className="muted">· multiqbit</span></label>
                <select value={pixelMode} onChange={e => setPixelMode(e.target.value)}>
                  <option value="register">Register — one pixel's N-NV register (dipole 2q)</option>
                  <option value="ensemble">Ensemble — 64 chip pixels (no within-pixel 2q)</option>
                </select>
              </div>
              <div className="field">
                <label>Register width N <span className="muted">· measured</span></label>
                <span className="mono" style={{ color: 'var(--brand)' }}>
                  {nNvPerPixel != null
                    ? `${nNvPerPixel} (calibration)`
                    : `${modes ? 'nominal' : '—'}${pixelMode === 'ensemble' ? ' · 1/pixel' : ''}`}
                </span>
                {modes && modes.pixel_modes && (
                  <span className="pill" style={{ marginLeft: 8, fontSize: 10.5,
                    color: 'var(--good)' }}>HW</span>
                )}
              </div>
              <div className="field">
                <label>Prime denoise <span className="muted">· coprime readout</span></label>
                <button type="button"
                  className={`seg ${primeReadout ? 'on' : ''}`}
                  style={{ minWidth: 86, justifyContent: 'center' }}
                  onClick={() => setPrimeReadout(v => !v)}
                  aria-pressed={primeReadout}>
                  {primeReadout ? '✓ ON' : 'OFF'}
                </button>
              </div>
            </div>
            {pixelMode === 'ensemble' && (
              <div className="muted" style={{ fontSize: 11.5, marginTop: 8,
                color: 'var(--warn, #b8860b)' }}>
                Ensemble mode: the 64 chip pixels are the circuit qubits (one
                ensemble qubit per pixel). Cross-pixel coupling is zero at the
                2.5 mm pitch, so 2q gates are rejected — run 1q circuits, or
                switch to Register for within-pixel dipole 2q.
              </div>
            )}
            {/* Selected defect summary — one horizontal line below the form
                selectors (label + value inline, not a stacked field). */}
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
              <span className="muted" style={{ fontSize: '11.5px' }}>Selected defect</span>
              <span className="mono" style={{ color: 'var(--brand)' }}>
                {info.data ? `${info.data.defect} · D ≈ ${info.data.d_ghz} GHz` : 'loading…'}
              </span>
              {hwMode === 'hardware' && (
                <span className="mono" style={{ marginLeft: 'auto', fontSize: '11.5px',
                  color: probe.data && probe.data.connected ? 'var(--ok, #2c8a4a)' : 'var(--bad, #c0392b)' }}>
                  {probe.loading ? '◌ probing' : probe.data && probe.data.connected ? '● FPGA online' : '○ no FPGA'}
                </span>
              )}
            </div>
          </div>

          </>)}

          {/* ============================= CIRCUIT RUNNER (single-pixel path) ============================= */}
          {circuitsSub === 'runner' && (<>
          <div className="grid cols-2">
          {/* The single-pixel run card stays in place in both modes. In 64-
              parallel mode only a Pixels + Use-calibration row is added and
              Run routes to runParallel; the algorithm menu is identical. */}
          <div className="card">
              <h2><span className="dot" />Run a quantum algorithm{parallelMode && <span className="pill" style={{ marginLeft: 8, fontSize: 10.5 }}>64-pixel parallel</span>}</h2>
              <div className="hint">{parallelMode
                ? 'Runs the chosen algorithm on every pixel (independent seeds) and aggregates; shor/period/qftnos race coprime bases across pixels.'
                : 'Enqueued as an async Redis/RQ job; the dashboard polls and updates when it is ready.'}</div>
              <div className="row">
                <div className="field">
                  <label>Algorithm</label>
                  <select value={algo} onChange={e => {
                    const a = e.target.value; setAlgo(a)
                    const cfg = QUBIT_CFG[a]
                    if (cfg) setNumQubits(cfg.def)
                    else if (a === 'shor' || a === 'period' || a === 'qftnos') setNumQubits(derivedQubits(a, nShor))
                  }}>
                    {ALGOS.map(a => <option key={a} value={a}>{ALGO_LABELS[a] || a}</option>)}
                  </select>
                </div>
                <div className="field">
                  <label>Shots</label>
                  <input type="number" min="64" step="64" value={shots} onChange={e => setShots(+e.target.value)} />
                </div>
              </div>

              {/* 64-parallel only: Pixels + Use-calibration. The only controls
                  added in 64 mode -- everything else (algorithm menu, shots,
                  per-algo fields) is identical to the single-pixel run card. */}
              {parallelMode && (
                <div className="row">
                  <div className="field">
                    <label>Pixels</label>
                    <input type="number" min="1" max="64" value={parNPixels} onChange={e => setParNPixels(Math.max(1, Math.min(64, +e.target.value || 1)))} />
                  </div>
                  <div className="field" style={{ justifyContent: 'flex-end' }}>
                    <label>Use calibration</label>
                    <div style={{ display: 'flex', gap: 8, paddingTop: 9 }}>
                      <button type="button" className={parUseCalib ? '' : 'ghost'} style={{ padding: '5px 12px', fontSize: 12 }}
                        onClick={() => setParUseCalib(true)}>On</button>
                      <button type="button" className={parUseCalib ? 'ghost' : ''} style={{ padding: '5px 12px', fontSize: 12 }}
                        onClick={() => setParUseCalib(false)}>Off</button>
                    </div>
                  </div>
                </div>
              )}

              {QUBIT_CFG[algo] && (
                <div className="row">
                  <div className="field">
                    <label>{QUBIT_CFG[algo].label}</label>
                    <input type="number" min={QUBIT_CFG[algo].min} max={QUBIT_CFG[algo].max}
                           step={QUBIT_CFG[algo].step} value={numQubits}
                           onChange={e => {
                             const cfg = QUBIT_CFG[algo]
                             // enforce the [min,max] clamp: HTML max does not stop
                             // the user typing a larger value, and a too-large
                             // register blows the simulator's qubit cap (mul
                             // uses 4*n qubits -> n=10 = 40q > 28q cap -> the
                             // "nonsensical false error"). math n<=6 = 24q max.
                             const v = +e.target.value || cfg.def
                             setNumQubits(Math.min(cfg.max, Math.max(cfg.min, v)))
                           }} />
                  </div>
                  <div className="field"><label>&nbsp;</label>
                    <div className="muted" style={{ paddingTop: 9 }}>{QUBIT_CFG[algo].hint}</div>
                  </div>
                </div>
              )}
              {(algo === 'shor' || algo === 'period' || algo === 'qftnos') && (
                <div className="row">
                  <div className="field">
                    <label>{algo === 'period' ? 'Integer N (find period)' : 'Integer to factor (N)'}</label>
                    <input type="number" min={2} max={algo === 'qftnos' ? 511 : 63} value={nShorDraft}
                           onChange={e => setNShorDraft(e.target.value)} />
                    {nShor > 15 && (
                      <div className="warn" style={{ fontSize: '11px', marginTop: 4, color: 'var(--warn)' }}>
                        N&gt;15 (16 qubits) runs in ~22-50 s in any mode. At 300 K
                        real mode it decoheres to no factors (the real T2 physics);
                        lower the Temperature or use ideal mode to factor it.
                        N&gt;31 (19 qubits) runs in ~60-90 s (single attempt).
                        N&gt;{algo === 'qftnos' ? 511 : 63} is capped (would hang the API).
                      </div>
                    )}
                  </div>
                  <div className="field">
                    <label>Circuit qubits n</label>
                    <input type="number" min={2} value={numQubits} readOnly
                           style={{ color: 'var(--muted)' }}
                           title="Derived from N — edit N above to change the circuit width" />
                  </div>
                  <div className="field"><label>&nbsp;</label>
                    <div className="muted" style={{ paddingTop: 9 }}>
                      {algo === 'qftnos' ? '2·bitlen(N)+1' : '3·bitlen(N)+1'} → {numQubits} qubits
                    </div>
                  </div>
                </div>
              )}
              {algo === 'math' && (
                <div className="row">
                  <div className="field"><label>Operation</label>
                    <select value={mathOp} onChange={e => setMathOp(e.target.value)}>
                      {MATH_OPS.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </div>
                  <div className="field"><label>Operand a</label>
                    <input type="number" value={mathA} onChange={e => setMathA(+e.target.value)} />
                  </div>
                  <div className="field"><label>Operand b</label>
                    <input type="number" value={mathB} onChange={e => setMathB(+e.target.value)} />
                  </div>
                </div>
              )}
              {algo === 'grover' && (
                <div className="row">
                  <div className="field"><label>Marked index</label>
                    <input type="number" value={groverMarked} onChange={e => setGroverMarked(+e.target.value)} />
                  </div>
                </div>
              )}
              {algo === 'qft' && (
                <div className="row">
                  <div className="field"><label>Input state |x⟩</label>
                    <input type="number" min="0" value={qftX} onChange={e => setQftX(+e.target.value)} />
                  </div>
                </div>
              )}
              {algo === 'qrand' && (
                <div className="row">
                  <div className="field"><label>Key size (bytes)</label>
                    <select value={qrBytes} onChange={e => setQrBytes(+e.target.value)}>
                      <option value={16}>16 B (128-bit)</option>
                      <option value={32}>32 B (256-bit)</option>
                      <option value={64}>64 B (512-bit)</option>
                    </select>
                  </div>
                  <div className="field"><label>Cert common name (CN)</label>
                    <input type="text" value={qrCN} onChange={e => setQrCN(e.target.value)} />
                  </div>
                </div>
              )}
              {algo === 'bb84' && (
                <div className="row">
                  <div className="field" style={{ justifyContent: 'flex-end' }}>
                    <label>Intercept-resend Eve</label>
                    <div style={{ display: 'flex', gap: 8, paddingTop: 9 }}>
                      <button type="button" className={bbEve ? '' : 'ghost'} style={{ padding: '5px 12px', fontSize: 12 }}
                        onClick={() => setBbEve(true)}>Eve ON</button>
                      <button type="button" className={bbEve ? 'ghost' : ''} style={{ padding: '5px 12px', fontSize: 12 }}
                        onClick={() => setBbEve(false)}>Eve OFF</button>
                    </div>
                  </div>
                </div>
              )}
              {algo === 'custom' && (
                <div>
                  <div className="hint" style={{ marginBottom: 8 }}>
                    Write a Qiskit <b>Python snippet</b> (define a <code className="mono">QuantumCircuit</code> named <code className="mono">qc</code>) or <b>OpenQASM 2</b>. Imports limited to <code className="mono">qiskit / numpy / math</code>; max 64 qubits. Runs on the QSSC backend with the selected execution mode.
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                    {Object.keys(CUSTOM_EXAMPLES).map(ex => (
                      <button key={ex} className="ghost" type="button"
                        style={{ padding: '5px 10px', fontSize: 12 }}
                        onClick={() => setCustomCode(CUSTOM_EXAMPLES[ex])}>load: {ex}</button>
                    ))}
                    {scripts.map(sc => (
                      <button key={sc.id} className="ghost" type="button"
                        style={{ padding: '5px 10px', fontSize: 12, borderColor: 'rgba(34,211,238,.4)', color: 'var(--brand)' }}
                        onClick={() => setCustomCode(sc.code)} title="saved script">★ {sc.name}</button>
                    ))}
                  </div>
                  <textarea className="code-editor" value={customCode} spellCheck={false}
                    onChange={e => setCustomCode(e.target.value)} rows={11} />
                </div>
              )}
              {algo === 'bell' && <div className="hint">Bell pair on q0,q1 — entanglement witness.</div>}
              {algo === 'qft' && <div className="hint">QFT of a basis state |x⟩ is an equal-amplitude superposition → measurement is ~uniform across all 2ⁿ states. That uniformity is the QFT signature.</div>}
              {algo === 'qrand' && <div className="hint">Measures |+⟩ⁿ many times → quantum-random bits, von-Neumann-debiased, then seeds an <b>Ed25519</b> keypair + self-signed <b>X.509</b> device certificate. Readout bias scales with temperature; the extractor removes it.</div>}
              {algo === 'qftnos' && <div className="hint">Lightweight QFT-based factoring: reduced counting register (t=l+1 vs Shor's 2l+1) + swapless inverse QFT + precomputed controlled mod-mult, then classical gcd factoring from the period. Far fewer qubits/gates than full Shor.</div>}
              {algo === 'bb84' && <div className="hint">Prepare-and-measure QKD over the QSSC backend (chip readout/decoherence = the channel). Sift matching bases, compute QBER, declare secure iff QBER &lt; 11%. An intercept-resend Eve adds ~25% QBER → detected.</div>}

              <button onClick={parallelMode ? () => runParallel(buildParallelBody()) : runCircuit} disabled={parallelMode ? parRunning : running} style={{ marginTop: 6 }}>
                {(parallelMode ? parRunning : running)
                  ? <><span className="spinner" /> {(parallelMode ? parJob : job) ? `Job ${(parallelMode ? parJob : job).status}…` : 'Running…'}</>
                  : (parallelMode ? 'Run 64-pixel parallel' : 'Run circuit')}
              </button>
              {(parallelMode ? parJob : job) && <span className="muted mono" style={{ marginLeft: 12, fontSize: 12 }}>job {(parallelMode ? parJob : job).job_id} · {(parallelMode ? parJob : job).status}</span>}
              {(parallelMode ? parErr : runErr) && <div className="error" style={{ marginTop: 10 }}>{parallelMode ? parErr : runErr}</div>}
              {result && result.error === 'no-fpga-detected' && (
                <div className="error" style={{ marginTop: 10 }}>
                  No FPGA detected — connect the board and set QSSC_FPGA_PORT, or switch to Simulator.
                  {result.message ? ` (${result.message})` : ''}
                </div>
              )}
              {!parallelMode && result && !result.error && (
                <div style={{ marginTop: 16 }}>
                  <div className="kv">
                    <span className="k">Algorithm</span><span className="v">{result.algorithm}</span>
                    <span className="k">Qubits</span><span className="v">{result.num_qubits}</span>
                    <span className="k">Classical bits</span><span className="v">{result.num_clbits}</span>
                    <span className="k">Depth</span><span className="v">{result.depth}</span>
                    <span className="k">Shots</span><span className="v">{result.shots}</span>
                    <span className="k">Wall time</span><span className="v">{result.elapsed_ms} ms</span>
                    <span className="k">Mode</span><span className="v">{result.noise_mode}{result.approximate ? ' (approx)' : ''}</span>
                  </div>
                  {result.factors && (
                    <div className="expected" style={{ marginTop: 12 }}>
                      Factors of {result.n}: {result.factors[0]} × {result.factors[1]} {result.success ? '✓ correct' : '✗ retry'}
                    </div>
                  )}
                  {result.factors === null && (result.algorithm === 'shor' || result.algorithm === 'qftnos') && (
                    <div className="expected bad" style={{ marginTop: 12 }}>
                      No factors recovered — the circuit decohered at {result.temperature_k} K (cool the chip to extend T₂).
                    </div>
                  )}
                  {result.algorithm === 'qftnos' && result.shor_comparison && (
                    <div className="expected" style={{ marginTop: 12 }}>
                      QFT-NOS: {result.num_qubits} qubits · {result.counting_qubits} counting · depth {result.depth}
                      {' vs '}full Shor: {result.shor_comparison.num_qubits} qubits · depth {result.shor_comparison.depth} · size {result.shor_comparison.size}
                      {' → '}{result.num_qubits < result.shor_comparison.num_qubits ? '✓ fewer qubits' : '≈ same'}
                    </div>
                  )}
                  {result.protocol === 'BB84' && (
                    <div style={{ marginTop: 12 }}>
                      <div className={`expected ${result.secure ? '' : 'bad'}`} style={{ marginBottom: 8 }}>
                        BB84 · {result.sifted_bits} sifted bits (sifting rate {result.sifting_rate})
                        {' · '}QBER = {(result.qber * 100).toFixed(2)}% (threshold {(result.qber_threshold * 100).toFixed(0)}%)
                        {' · '}{result.secure ? '✓ secure' : '✗ insecure'}
                        {result.eavesdrop ? ` · Eve ${result.eve_detected ? 'detected ✓' : 'NOT detected ✗'}` : ''}
                      </div>
                      <div className="kv">
                        <span className="k">Alice key</span><span className="v mono" style={{ wordBreak: 'break-all' }}>{result.alice_key_hex}</span>
                        <span className="k">Bob key</span><span className="v mono" style={{ wordBreak: 'break-all' }}>{result.bob_key_hex}</span>
                        <span className="k">Matching bits</span><span className="v">{result.matches} / {result.sifted_bits}</span>
                      </div>
                    </div>
                  )}
                  {result.period != null && (
                    <div className="expected" style={{ marginTop: 12 }}>Recovered period r = {result.period}</div>
                  )}
                  {result.found != null && (
                    <div className="expected" style={{ marginTop: 12 }}>
                      Grover found index {result.found} (marked {result.marked}, {result.iterations} iter)
                      {' · '}{result.success ? '✓ correct' : '✗ retry'}
                    </div>
                  )}
                  {result.result != null && algo === 'math' && (
                    <div className="expected" style={{ marginTop: 12 }}>
                      {result.op}({result.a}{result.b != null ? `, ${result.b}` : ''}) → {result.result}
                    </div>
                  )}
                  {result.qft === 'forward' && (
                    <div className={`expected ${result.uniform ? '' : 'bad'}`} style={{ marginTop: 12 }}>
                      QFT of |{result.x}⟩ → {result.basis_states} states · {result.uniform ? '✓ uniform' : '✗ non-uniform'}
                      {' '}(χ²/N = {result.uniformity_chi2}, expected ≈1)
                    </div>
                  )}
                  {result.algorithm === 'qrand' && result.entropy_hex && (
                    <div style={{ marginTop: 12 }}>
                      <div className={`expected`} style={{ marginBottom: 8 }}>
                        {result.key_algorithm} keypair + self-signed cert for <b>{result.common_name}</b>
                        {' · '}serial {result.serial}
                        {' · '}bias p₁={result.raw_bias_p1} · extract rate {result.extraction && result.extraction.extract_rate}
                      </div>
                      <div className="kv">
                        <span className="k">Quantum entropy</span><span className="v mono" style={{ wordBreak: 'break-all' }}>{result.entropy_hex}</span>
                        <span className="k">Symmetric key</span><span className="v mono" style={{ wordBreak: 'break-all' }}>{result.symmetric_key_hex}</span>
                      </div>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                        <button className="ghost" type="button" onClick={() => navigator.clipboard && navigator.clipboard.writeText(result.certificate_pem)}>📋 copy cert PEM</button>
                        <button className="ghost" type="button" onClick={() => downloadPem(`${result.common_name || 'qssc'}.crt.pem`, result.certificate_pem)}>⬇ download cert</button>
                        <button className="ghost" type="button" onClick={() => downloadPem(`${result.common_name || 'qssc'}.key.pem`, result.private_key_pem)}>⬇ download private key</button>
                      </div>
                      <pre className="code-editor" style={{ whiteSpace: 'pre-wrap', marginTop: 8, fontSize: 11, maxHeight: 240 }}>{result.certificate_pem}</pre>
                    </div>
                  )}
                </div>
              )}
              {parallelMode && parRun && !parRun.error && (
                <div style={{ marginTop: 16 }}>
                  <div className="kv">
                    <span className="k">Mode</span><span className="v">{parRun.mode}{parRun.calibrated ? ' · calibrated' : ''}</span>
                    <span className="k">Algorithm</span><span className="v">{parRun.algorithm || (parRun.mode === 'shor' ? 'shor' : 'sample')}</span>
                    <span className="k">Pixels</span><span className="v">{parRun.n_pixels}{parRun.n_run != null ? ` (${parRun.n_run} run)` : ''}</span>
                    <span className="k">Qubits</span><span className="v">{parRun.num_qubits ?? '–'}</span>
                    <span className="k">Shots</span><span className="v">{parRun.total_shots || parRun.shots}</span>
                    <span className="k">Throughput</span><span className="v">{parRun.throughput_x}×</span>
                    <span className="k">Wall time</span><span className="v">{parRun.elapsed_ms} ms</span>
                  </div>
                  {parRun.mode === 'shor' && (
                    <div className={`expected ${parRun.success ? '' : 'bad'}`} style={{ marginTop: 12 }}>
                      {parRun.success
                        ? <>Factors of {parRun.n}: {parRun.factors[0]} × {parRun.factors[1]} ✓ — winner pixel {parRun.winner_pixel} (base a={parRun.winner_base})</>
                        : <>No factors recovered across {parRun.n_pixels} pixels — the circuits decohered at {parRun.temperature_k} K (cool the chip to extend T₂).</>}
                    </div>
                  )}
                  {parRun.per_pixel_widths && (
                    <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
                      Per-pixel register widths: min {parRun.min_width ?? '–'} · max {parRun.max_width ?? '–'} · best uniform {parRun.best_uniform_width ?? '–'} ({parRun.best_uniform_pixels ?? '–'} pixels)
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* measurement column (right): histogram + qubit grid — same position
                in both modes; only the counts + result marker swap to the 64
                aggregate when parallelMode is ON. */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <div className="card histcard">
                <h2><span className="dot" />Measurement histogram
                </h2>
                <div className="hint">{parallelMode
                  ? (parRun && parRun.mode === 'shor'
                      ? '64-pixel Shor: the first pixel to find the period wins — its recovered factors mark the result.'
                      : 'Aggregate top bitstrings across all 64 pixels (n_pixels × shots total).')
                  : (runMarginal
                      ? 'Phase-register marginal (ancilla collapsed): bar counts sum to shots, the recovered signal peak is concentrated.'
                      : 'Top bitstrings from the most recent run (most-probable first).')}</div>
                {parallelMode
                  ? (parDisplayCounts
                      ? <BarCounts counts={parDisplayCounts} shots={parShots}
                          signal={parMeas} recovered={parRecovered} />
                      : parErr
                        ? <div className="muted">Last run failed — see the error above.</div>
                        : <div className="muted">No 64-parallel run yet — run a circuit on the left.</div>)
                  : (displayCounts
                      ? <BarCounts counts={displayCounts} shots={shots}
                          signal={meas} recovered={runRecovered} />
                      : runErr
                        ? <div className="muted">Last run failed — see the error above.</div>
                        : <div className="muted">No run yet — execute a circuit on the left.</div>)}
                {/* "How is the result calculated from this measurement?" — same
                    derivation modal in both modes (single-pixel algo or 64-shor). */}
                {parallelMode
                  ? (parMeas && <MeasurementHelp
                      algo={parRun && parRun.mode === 'shor' ? 'shor' : algo}
                      result={parRun} counts={parDisplayCounts}
                      numClbits={parRun && parRun.num_clbits} meas={parMeas} />)
                  : (meas && <MeasurementHelp algo={algo} result={result}
                      counts={displayCounts} numClbits={runBits} meas={meas} />)}
              </div>

              <div className="card">
                <h2><span className="dot" />Qubit grid (8×8)</h2>
                <div className="hint">Auto-aligned defect sites; each measured qubit lights up by its measured-|1⟩ probability across all shots.</div>
                {cal.data && cal.data.sites && (
                  <QubitGrid
                    sites={cal.data.sites}
                    counts={parallelMode ? parDisplayCounts : displayCounts}
                    numClbits={parallelMode ? (parRun && parRun.num_clbits) : runBits}
                    recovered={parallelMode ? parRecovered : runRecovered}
                  />
                )}
                {cal.loading && <div className="muted"><span className="spinner" /> calibrating…</div>}
                {cal.error && <div className="error">{cal.error}</div>}
              </div>
            </div>
          </div>

          {/* ============================= BACKEND PHYSICS ============================= */}
          </>)}
          {circuitsSub === 'physics' && (<>
          <div className="grid cols-2">
            <div className="card">
              <h2><span className="dot" />Backend physics</h2>
              <div className="hint">Live parameters from the Qiskit BackendV2 target at the selected temperature.</div>
              {info.error && <div className="error">{info.error}</div>}
              {info.data && (
                <div className="stats">
                  <div className="stat"><div className="k">D splitting</div><div className="v">{info.data.d_ghz}<small> GHz</small></div></div>
                  <div className="stat"><div className="k">Coherence T₂</div><div className="v">{(info.data.t2_us).toFixed(2)}<small> µs</small></div></div>
                  <div className="stat"><div className="k">Relaxation T₁</div><div className="v">{(info.data.t1_ms).toFixed(1)}<small> ms</small></div></div>
                  <div className="stat"><div className="k">Readout err</div><div className="v">{(info.data.readout_error * 100).toFixed(1)}<small> %</small></div></div>
                  <div className="stat"><div className="k">1q gate</div><div className="v">{info.data.gate_1q_ns}<small> ns</small></div></div>
                  <div className="stat"><div className="k">2q gate</div><div className="v">{info.data.gate_2q_ns}<small> ns</small></div></div>
                  <div className="stat"><div className="k">Qubits</div><div className="v">{info.data.num_qubits}</div></div>
                  <div className="stat"><div className="k">Pixels</div><div className="v">{info.data.site_count ?? info.data.pixels}</div></div>
                  <div className="stat"><div className="k">NVs/pixel</div><div className="v">{info.data.n_nv_per_pixel ?? '–'}</div></div>
                </div>
              )}
              {info.loading && <div className="muted"><span className="spinner" /> loading backend target…</div>}
              {!parallelMode && runAssessment && <div style={{ marginTop: 12 }}><Verdict assessment={runAssessment} /></div>}
              {(parallelMode ? parRun && parRun.noise : result && result.noise) && <NoiseBudget noise={parallelMode ? parRun.noise : result.noise} />}
            </div>

            <RunPhysics result={parallelMode ? parRun : result} profile={profile} temperature={temperature} infoData={info.data} />

            <div className="card optchain-card" style={{ gridColumn: '1 / -1' }}>
              <h2><span className="dot" />Optical readout chain</h2>
              <div className="hint">The physics of every chip component, recalculated for the selected defect &amp; temperature: CQD ASE emitter → SU-8/PDMS waveguide → <b>defect crystal</b> (the spin host / qubit — pump in, photoluminescence out) → long-pass filter → 2D photodetector → TIA → 4× AD7616 (16-bit parallel) → 1 kHz lock-in. Cooling cuts detector noise (∝√T) and raises ODMR contrast, so the lock-in SNR climbs — change the temperature or defect above and watch the dip, noise and SNR update.</div>
              <OpticalChainDiagram data={info.data && info.data.optical_readout} infoData={info.data} profile={profile} temperature={temperature} activeSites={(parallelMode ? parRun : result)?.num_qubits} />
            </div>
          </div>

          {/* ============================= SPECTROSCOPY + CALIBRATION ============================= */}
          </>)}
          {circuitsSub === 'spectro' && (<>
          <div className="grid cols-2">
            <div className="card">
              <h2><span className="dot" />ODMR spectrum</h2>
              <div className="hint">Lock-in ODMR — the on-chip graphene/MoS₂ detector array's photocurrent dips at D ± γB (resonant MW flips the spin dark), with hyperfine replicas.</div>
              <OdmrChart data={odmr.data} error={odmr.error} />
            </div>
            {/* 64-parallel OFF = single-pixel ODMR calibration; ON = 64-array
                calibration in the SAME card slot. One calibrate, same position. */}
            {!parallelMode ? (
            <div className="card">
              <h2><span className="dot" />Calibration process</h2>
              <CalibrationPanel
                profile={profile} temperature={temperature} seed={seed}
                run={runCalibration} running={calRunning} result={calRun} err={calErr}
              />
            </div>
            ) : (
              <ParallelCalibCard
                grad={parGrad} setGrad={setParGrad}
                pixelSize={parPixelSize} setPixelSize={setParPixelSize}
                source={hwMode === 'hardware' ? 'real' : 'sim'}
                profile={profile} temperature={temperature} seed={seed}
                onCalibrate={calibrateArray} calibRunning={arrCalibRunning}
                calibResult={arrCalib} calibErr={arrCalibErr}
              />
            )}
          </div>

          {/* ============================= ANALOG SIGNAL ============================= */}
          </>)}
          {circuitsSub === 'signal' && (<>
          <div className="card">
            <h2><span className="dot" />Analog detected signal</h2>
            <div className="hint">Lock-in-demodulated detector photocurrent the on-chip graphene/MoS₂ array records during a pulse sequence.</div>
            <div className="row" style={{ marginBottom: 10 }}>
              <div className="field">
                <label>Sequence</label>
                <select value={seq} onChange={e => setSeq(e.target.value)}>
                  {SEQS.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
                </select>
              </div>
              <div className="field">
                <label>Detuning (MHz)</label>
                <input type="number" step="any" value={detuning} onChange={e => setDetuning(+e.target.value)} />
              </div>
            </div>
            <SignalChart data={signal.data} error={signal.error} />
          </div>
          </>)}

          <footer style={{ marginTop: 30, color: 'var(--dim)', fontSize: 12.5, textAlign: 'center' }}>
            QSSC — Quantum Solid State Chip · v2.0.0 · NV-diamond / hBN V_B⁻ / SiC V_Si ·
            Qiskit BackendV2 + Artix-7 FPGA control stack · async jobs (Redis/RQ) · history (MongoDB)
          </footer>
        </>
      )}
    </div>
  )
}