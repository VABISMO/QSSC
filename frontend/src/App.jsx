import React, { useEffect, useState, useCallback, useRef } from 'react'
import { api } from './api.js'
import {
  QubitGrid, OdmrChart, BarCounts,
  SignalChart, CalibrationPanel, Verdict,
  OpticalChainDiagram, RunPhysics,
  CompassRose, GyroPanel, MagnetoImage, SensorField,
} from './components.jsx'

// Fallback firmware command list (all 17 verbs). Used in the "Reply & readout"
// empty state when the API isn't running or is a stale build serving fewer
// commands, so every command we added is always visible. The live API list
// (fpga.protocol) takes precedence when it serves the full set.
const FALLBACK_PROTOCOL = [
  { cmd: 'DRIVE_EMITTER', args: 'index duration_ms', desc: 'pump one CQD emitter at full grayscale (gs=4095)' },
  { cmd: 'SET_EMITTER', args: 'index level(0..4095)', desc: "arm one channel's 12-bit grayscale level" },
  { cmd: 'DRIVE_PATTERN', args: 'duration_ms', desc: 'fire ALL armed channels in parallel' },
  { cmd: 'CLEAR_EMITTERS', args: '—', desc: 'disarm all channels (gs=0)' },
  { cmd: 'APPLY_PULSE', args: 'qubit freq_khz amp duration_ns phase_mrad', desc: 'program the ADF4351 microwave pulse' },
  { cmd: 'READ_DETECTOR', args: 'site', desc: 'one AD7616 16-bit photocurrent sample' },
  { cmd: 'READ_DETECTORS', args: '—', desc: 'all 64 detector channels in one parallel frame' },
  { cmd: 'CLEAR_DETECTORS', args: '—', desc: 'no-op on the ADC (no counters to clear)' },
  { cmd: 'CALIBRATE', args: '—', desc: 'emitter→site→detector sweep, builds the readout LUT' },
  { cmd: 'READ_RNG', args: 'n_bytes', desc: 'harvest n_bytes of von-Neumann-debiased AD7616 LSB-noise entropy (qrng_entropy.v)' },
  { cmd: 'ODMR_SWEEP', args: 'qubit f_start_khz f_stop_khz n_points', desc: 'sweep the ADF4351 across the NV resonance, one AD7616 sample per point' },
  { cmd: 'READ_COMPASS', args: '[n_avg]', desc: '4-axis ODMR doublet fit → ambient B-vector + heading (compass_measure)' },
  { cmd: 'READ_GYRO', args: 'T_ns', desc: 'Ramsey π/2–T–π/2 → rotation rate (gyro_measure)' },
  { cmd: 'READ_LOCKIN', args: 'ref_khz n_cycles', desc: 'lock-in accumulator: on-minus-off differential over n cycles (lockin_integrated_readout)' },
  { cmd: 'RUN_SEQUENCE', args: 'kind n_windows', desc: 'pulse-sequence engine stub: ramsey / hahn / cpmg → n readout codes (#58 RTL pending)' },
  { cmd: 'STATUS', args: '—', desc: 'board self-status: PLL lock, ADC busy, rail voltages, die temperature' },
  { cmd: 'RESET', args: '—', desc: 'soft reset of the TLC5947 / ADF4351 / ADC peripheral state' },
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
  { id: 'real', label: 'Real backend (full physics)' },
  { id: 'phenomenological', label: 'Simulator (fast approx.)' },
  { id: 'none', label: 'Ideal (no noise)' },
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
  bell:    { min: 2, max: 20, step: 1, def: 4,  label: 'Qubits n',         hint: 'GHZ chain on n qubits (H + CNOT ladder)' },
  qrand:   { min: 2, max: 20, step: 1, def: 8,  label: 'Qubits n',         hint: 'n qubits prepared in |+⟩ → von-Neumann-extracted entropy' },
  qft:     { min: 2, max: 20, step: 1, def: 4,  label: 'Qubits n',         hint: 'QFT of an n-qubit computational-basis state' },
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
// We keep BOTH fields visible & editable: editing N derives the qubit count, and
// editing the qubit count snaps N to a semiprime of the matching bit-length.
const bitLen = (n) => (n <= 1 ? 1 : Math.floor(Math.log2(n)) + 1)
const SHOR_GOOD_N = { 4: 15, 5: 21, 6: 35, 7: 77, 8: 143, 9: 323, 10: 527, 11: 649 }
const derivedQubits = (algo, n) => algo === 'qftnos' ? 2 * bitLen(n) + 1 : 3 * bitLen(n) + 1
const qubitsToN = (algo, q) => {
  const l = algo === 'qftnos' ? Math.round((q - 1) / 2) : Math.round((q - 1) / 3)
  const lb = Math.max(2, l)
  return SHOR_GOOD_N[lb] || ((1 << (lb - 1)) + 1)   // smallest lb-bit integer
}

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
  // hardware QRNG (ADC-noise entropy -> Ed25519 keypair + cert)
  const [qrng, setQrng] = useState(null)
  const [qrngErr, setQrngErr] = useState(null)
  const [qrngLoading, setQrngLoading] = useState(false)
  const [qrngBytes, setQrngBytes] = useState(32)         // entropy bytes (>=32 for Ed25519 seed)
  const [qrngCN, setQrngCN] = useState('QSSC-hw-qrng')
  // FPGA environment selector shown at the top of the FPGA tab:
  // 'sim' = simulator (averages freely -> cleaner readout), 'real' = hardware
  // chain (realistic shot noise at the requested averages). Both drive the
  // same ADF4351 -> J3 -> A1 + 64-channel AD7616 physics; only the noise floor
  // differs. Toggling it re-runs the sensor captures.
  const [fpgaMode, setFpgaMode] = useState('sim')

  const refreshFpga = useCallback(() => {
    setFpgaLoading(true); setFpgaErr(null)
    api.fpgaInfo().then(setFpga).catch(e => setFpgaErr(e.message)).finally(() => setFpgaLoading(false))
  }, [])
  useEffect(() => { if (mainTab === 'fpga' && !fpga) refreshFpga() }, [mainTab, fpga, refreshFpga])

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
    api.gyro({ profile, mode: fpgaMode, rate_dps: gyroRate, temperature_k: temperature, n_avg: gyroAvg, seed })
      .then(setGyro).catch(e => setGyroErr(e.message))
      .finally(() => setGyroLoading(false))
  }, [profile, fpgaMode, gyroRate, temperature, gyroAvg, seed])
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
  // clear any pending status-poll timeout on unmount so it can't fire a
  // setState on the unmounted component.
  useEffect(() => () => { if (pollRef.current) clearTimeout(pollRef.current) }, [])

  // ---- analog signal + calibration state ----
  const [seq, setSeq] = useState('ramsey')
  const [detuning, setDetuning] = useState(2)
  const [calRun, setCalRun] = useState(null)
  const [calRunning, setCalRunning] = useState(false)
  const [calErr, setCalErr] = useState(null)

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
  const signal = useAsync(() => api.signal({ profile, temperature_k: temperature, sequence: seq, detuning_mhz: detuning, points: 220 }), [profile, temperature, seq, detuning])

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
    if (algo === 'custom') {
      return { code: customCode, profile, temperature_k: temperature, seed, shots, noise_mode: noiseMode }
    }
    const body = { algorithm: algo, profile, temperature_k: temperature, seed, shots, noise_mode: noiseMode }
    if (algo === 'shor' || algo === 'period' || algo === 'qftnos') body.n = nShor
    if (algo === 'math') { body.op = mathOp; body.a = mathA; body.b = mathB; body.bits = numQubits }
    if (algo === 'grover') { body.n_items = numQubits; body.marked = groverMarked }
    if (algo === 'qft') { body.num_qubits = numQubits; body.x = qftX }
    if (algo === 'qrand') { body.num_qubits = numQubits; body.num_bytes = qrBytes; body.common_name = qrCN }
    if (algo === 'bell') { body.num_qubits = numQubits }
    if (algo === 'bb84') { body.num_bits = numQubits; body.eavesdrop = bbEve }
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
      const MAX_POLLS = 200
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

  const runCalibration = async () => {
    setCalRunning(true); setCalErr(null)
    try {
      const r = await api.calibrationRun({ profile, temperature_k: temperature, seed })
      setCalRun(r)
    } catch (e) { setCalErr(e.message) }
    finally { setCalRunning(false) }
  }

  const loadFromHistory = (rec) => {
    setResult(rec.result || null)
    setRunErr(null)
    setJob(null)
    setMainTab('hardware')
  }

  const running = job && job.status !== 'finished' && job.status !== 'failed'

  const runCounts = result && (result.counts || (result.result && result.result.counts))
  const runAssessment = result && result.assessment
  const runBits = result && result.num_clbits

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
          {/* environment selector — drives the sensor captures in the Sensors tab too */}
          <div className="seg-toggle" role="group" aria-label="FPGA environment">
            <span className="seg-label">Environment</span>
            <button className={`seg ${fpgaMode === 'sim' ? 'on' : ''}`} onClick={() => setFpgaMode('sim')}>
              <span className="seg-ic">◈</span> Simulation
            </button>
            <button className={`seg ${fpgaMode === 'real' ? 'on' : ''}`} onClick={() => setFpgaMode('real')}>
              <span className="seg-ic">⌬</span> Hardware
            </button>
            <span className="seg-note">
              {fpgaMode === 'sim'
                ? 'simulator — averages freely, cleaner readout (no shot-noise floor)'
                : 'ADF4351 + 64-ch AD7616 chain — realistic linewidth + shot noise'}
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
              {fpga?.resources?.map(r => (
                <div className="bar-row" key={r.name}>
                  <span className="muted mono" style={{ fontSize: 11.5 }}>{r.label}</span>
                  <div className="bar-track"><div className="bar-fill" style={{ width: `${Math.min(100, r.pct)}%` }} /></div>
                  <span className="mono" style={{ fontSize: 11, textAlign: 'right' }}>{r.pct}%</span>
                </div>
              ))}
              {fpga?.resources && (
                <div className="legend" style={{ marginTop: 12 }}>
                  {fpga.resources.map(r => (
                    <div key={r.name}><b>{r.used}</b> / {r.available} {r.label}</div>
                  ))}
                </div>
              )}
              {fpga?.quality && (
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

          {/* PROTOCOL REFERENCE */}
          <div className="section-title">Command protocol reference</div>
          <div className="card">
            <h2><span className="dot" />Firmware command set <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>(fpga/rtl/command_parser.v ↔ qssc.simulator)</span></h2>
            <div className="hint">The Python FPGAConnection and the RTL command parser implement exactly these verbs. SET_THRESHOLD was removed by the AD7616 pivot → ERR.</div>
            <div className="fpga-proto">
              <div className="fpga-proto-hd">Command</div><div className="fpga-proto-hd">Args</div><div className="fpga-proto-hd">Reply</div><div className="fpga-proto-hd">Description</div>
              {fpga?.protocol?.map(p => (
                <React.Fragment key={p.cmd}>
                  <div className="mono fpga-proto-cmd">{p.cmd}</div>
                  <div className="mono fpga-proto-args">{p.args}</div>
                  <div className="mono fpga-proto-reply">{p.reply}</div>
                  <div className="muted fpga-proto-desc">{p.desc}</div>
                </React.Fragment>
              ))}
            </div>
          </div>

          {/* HARDWARE QRNG — physical entropy from the AD7616 noise floor */}
          <div className="section-title">Hardware QRNG — physical entropy from the AD7616 noise floor</div>
          <div className="card">
            <h2><span className="dot" />Quantum RNG <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>(qrng_entropy.v · READ_RNG)</span></h2>
            <div className="hint">
              A <b>dark</b> AD7616 channel's LSB is dominated by Johnson + shot noise. The FPGA pairs successive samples through a von-Neumann extractor (01→0, 10→1, 00/11 discarded) and packs debiased bits into 32-bit words. The 32-byte seed drives an Ed25519 keypair + self-signed X.509 device cert.
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
            <button className={`seg ${fpgaMode === 'sim' ? 'on' : ''}`} onClick={() => setFpgaMode('sim')}>
              <span className="seg-ic">◈</span> Simulation
            </button>
            <button className={`seg ${fpgaMode === 'real' ? 'on' : ''}`} onClick={() => setFpgaMode('real')}>
              <span className="seg-ic">⌬</span> Hardware
            </button>
            <span className="seg-note">
              {fpgaMode === 'sim'
                ? 'simulator — averages freely, cleaner readout (no shot-noise floor)'
                : 'ADF4351 + 64-ch AD7616 chain — realistic linewidth + shot noise'}
            </span>
          </div>

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
              <h2><span className="dot" />Ramsey gyroscope</h2>
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

          {/* =================== CONTACTLESS FIELD IMAGING =================== */}
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
          {/* ============================= GLOBAL CONTROLS ============================= */}
          <div className="card" style={{ marginBottom: 18 }}>
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
              <div className="field">
                <label>Execution mode</label>
                <select value={noiseMode} onChange={e => setNoiseMode(e.target.value)}>
                  {NOISE_MODES.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                </select>
              </div>
              <div className="field">
                <label>RNG seed</label>
                <input type="number" value={seed} onChange={e => setSeed(+e.target.value)} />
              </div>
            </div>
            {/* Selected defect summary — one horizontal line below the form
                selectors (label + value inline, not a stacked field). */}
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
              <span className="muted" style={{ fontSize: '11.5px' }}>Selected defect</span>
              <span className="mono" style={{ color: 'var(--brand)' }}>
                {info.data ? `${info.data.defect} · D ≈ ${info.data.d_ghz} GHz` : 'loading…'}
              </span>
            </div>
          </div>

          {/* ============================= CIRCUIT RUNNER ============================= */}
          <div className="section-title">Circuit Runner</div>
          <div className="grid cols-2">
          <div className="card">
              <h2><span className="dot" />Run a quantum algorithm</h2>
              <div className="hint">Enqueued as an async Redis/RQ job; the dashboard polls and updates when it is ready. Custom circuit loads by default.</div>
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

              {QUBIT_CFG[algo] && (
                <div className="row">
                  <div className="field">
                    <label>{QUBIT_CFG[algo].label}</label>
                    <input type="number" min={QUBIT_CFG[algo].min} max={QUBIT_CFG[algo].max}
                           step={QUBIT_CFG[algo].step} value={numQubits}
                           onChange={e => setNumQubits(+e.target.value)} />
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
                    <input type="number" min={2} value={nShor} onChange={e => {
                      const n = Math.max(2, Math.floor(+e.target.value) || 2)
                      setNShor(n); setNumQubits(derivedQubits(algo, n))
                    }} />
                  </div>
                  <div className="field">
                    <label>Circuit qubits n</label>
                    <input type="number" min={2} value={numQubits} onChange={e => {
                      const q = Math.max(2, Math.floor(+e.target.value) || 2)
                      setNumQubits(q); setNShor(qubitsToN(algo, q))
                    }} />
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

              <button onClick={runCircuit} disabled={running} style={{ marginTop: 6 }}>
                {running ? <><span className="spinner" /> {job ? `Job ${job.status}…` : 'Running…'}</> : 'Run circuit'}
              </button>
              {job && <span className="muted mono" style={{ marginLeft: 12, fontSize: 12 }}>job {job.job_id} · {job.status}</span>}
              {runErr && <div className="error" style={{ marginTop: 10 }}>{runErr}</div>}
              {result && (
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
            </div>

            {/* measurement column (right): histogram + qubit grid */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <div className="card histcard">
                <h2><span className="dot" />Measurement histogram</h2>
                <div className="hint">Top bitstrings from the most recent run (most-probable first).</div>
                {runCounts
                  ? <BarCounts counts={runCounts} />
                  : runErr
                    ? <div className="muted">Last run failed — see the error above.</div>
                    : <div className="muted">No run yet — execute a circuit on the left.</div>}
              </div>

              <div className="card">
                <h2><span className="dot" />Qubit grid (8×8)</h2>
                <div className="hint">Auto-aligned defect sites; each measured qubit lights up by its measured-|1⟩ probability across all shots.</div>
                {cal.data && cal.data.sites && (
                  <QubitGrid
                    sites={cal.data.sites}
                    counts={runCounts}
                    numClbits={runBits}
                  />
                )}
                {cal.loading && <div className="muted"><span className="spinner" /> calibrating…</div>}
                {cal.error && <div className="error">{cal.error}</div>}
              </div>
            </div>
          </div>

          {/* ============================= BACKEND PHYSICS ============================= */}
          <div className="section-title">Backend physics</div>
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
                  <div className="stat"><div className="k">Sites</div><div className="v">{info.data.site_count}</div></div>
                </div>
              )}
              {info.loading && <div className="muted"><span className="spinner" /> loading backend target…</div>}
              {runAssessment && <div style={{ marginTop: 12 }}><Verdict assessment={runAssessment} /></div>}
            </div>

            <RunPhysics result={result} profile={profile} temperature={temperature} infoData={info.data} />

            <div className="card optchain-card" style={{ gridColumn: '1 / -1' }}>
              <h2><span className="dot" />Optical readout chain</h2>
              <div className="hint">The physics of every chip component, recalculated for the selected defect &amp; temperature: CQD ASE emitter → SU-8/PDMS waveguide → <b>defect crystal</b> (the spin host / qubit — pump in, photoluminescence out) → long-pass filter → 2D photodetector → TIA → 4× AD7616 (16-bit parallel) → 1 kHz lock-in. Cooling cuts detector noise (∝√T) and raises ODMR contrast, so the lock-in SNR climbs — change the temperature or defect above and watch the dip, noise and SNR update.</div>
              <OpticalChainDiagram data={info.data && info.data.optical_readout} infoData={info.data} profile={profile} temperature={temperature} activeSites={result?.num_qubits} />
            </div>
          </div>

          {/* ============================= SPECTROSCOPY + CALIBRATION ============================= */}
          <div className="section-title">Spectroscopy &amp; calibration</div>
          <div className="grid cols-2">
            <div className="card">
              <h2><span className="dot" />ODMR spectrum</h2>
              <div className="hint">Lock-in ODMR — the on-chip graphene/MoS₂ detector array's photocurrent dips at D ± γB (resonant MW flips the spin dark), with hyperfine replicas.</div>
              <OdmrChart data={odmr.data} error={odmr.error} />
            </div>
            <div className="card">
              <h2><span className="dot" />Calibration process</h2>
              <CalibrationPanel
                profile={profile} temperature={temperature} seed={seed}
                run={runCalibration} running={calRunning} result={calRun} err={calErr}
              />
            </div>
          </div>

          {/* ============================= ANALOG SIGNAL ============================= */}
          <div className="section-title">Detected signal</div>
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

          <footer style={{ marginTop: 30, color: 'var(--dim)', fontSize: 12.5, textAlign: 'center' }}>
            QSSC — Quantum Solid State Chip · v2.0.0 · NV-diamond / hBN V_B⁻ / SiC V_Si ·
            Qiskit BackendV2 + Artix-7 FPGA control stack · async jobs (Redis/RQ) · history (MongoDB)
          </footer>
        </>
      )}
    </div>
  )
}