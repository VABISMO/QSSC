import React, { useEffect, useState, useCallback, useRef } from 'react'
import { api } from './api.js'
import { deriveMeasurement } from './derive.js'
import {
  QubitGrid, OdmrChart, BarCounts,
  SignalChart, CalibrationPanel, CalibrationResults, Verdict, NoiseBudget,
  OpticalChainDiagram, RunPhysics, MeasurementHelp,
  CompassRose, GyroPanel, MagnetoImage, SensorField, TensorPanel,
  ParallelCalibCard, ParallelCalibResults, SignalLab,
} from './components.jsx'
import {
  FALLBACK_PROTOCOL, PROFILES, ALGOS, ALGO_LABELS, MATH_OPS,
  NOISE_MODES, ANTI_NOISE_PRESETS, SEQS, QUBIT_CFG, CUSTOM_EXAMPLES,
} from './constants.js'
import { bitLen, derivedQubits, downloadPem } from './utils.js'
import { useAsync } from './hooks/useAsync.js'
import { CircuitContext } from './circuitContext.js'
import FpgaTab from './tabs/FpgaTab.jsx'
import SensorsTab from './tabs/SensorsTab.jsx'
import CodeTab from './tabs/CodeTab.jsx'
import HistoryTab from './tabs/HistoryTab.jsx'
import HardwareTab from './tabs/HardwareTab.jsx'

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
  // `primeReadout` toggles the prime-coprime readout denoiser (READ_LOCKINP,
  //   default on). `noiseEnv` + `antiNoisePreset` are the environmental noise
  //   model + the classical anti-noise filter stack (thermal servo / vibration
  //   isolation / EMI shield / feed-forward / common-mode / dynamical
  //   decoupling). A noisy lab raises the readout error; the filter stack
  //   attenuates the environmental channels back down. Both run on existing
  //   firmware (RUN_SEQUENCE pulse engine) — no PCB change.
  const [pixelMode, setPixelMode] = useState('register')
  const [nNvPerPixel, setNNvPerPixel] = useState(null)
  const [registerLineFreqsHz, setRegisterLineFreqsHz] = useState(null)
  const [primeReadout, setPrimeReadout] = useState(true)
  const [noiseEnv, setNoiseEnv] = useState('none')
  const [antiNoisePreset, setAntiNoisePreset] = useState('off')
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
  // tensor gradiometer — 64-pixel B-vector map -> 3x2 Jacobian + dipole fit
  const [tensor, setTensor] = useState(null); const [tensorLoading, setTensorLoading] = useState(false); const [tensorErr, setTensorErr] = useState(null)
  const [tensorMoment, setTensorMoment] = useState(0.002)
  const [tensorX, setTensorX] = useState(2); const [tensorY, setTensorY] = useState(-1); const [tensorZ, setTensorZ] = useState(10)
  const [tensorBg, setTensorBg] = useState(50); const [tensorAvg, setTensorAvg] = useState(500)

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
  const runTensor = useCallback(() => {
    setTensorLoading(true); setTensorErr(null)
    api.tensor({ profile, source: fpgaMode, n_avg: tensorAvg,
      base_bz_ut: tensorBg, dipole_moment_am2: tensorMoment,
      dipole_x_mm: tensorX, dipole_y_mm: tensorY, dipole_z_mm: tensorZ, seed })
      .then(setTensor).catch(e => setTensorErr(e.message)).finally(() => setTensorLoading(false))
  }, [profile, fpgaMode, tensorAvg, tensorBg, tensorMoment, tensorX, tensorY, tensorZ, seed])

  // Probe the real FPGA whenever Hardware-real mode is entered, so the
  // indicator shows a green "FPGA connected — QSSC v1 on /dev/ttyUSB0" or a
  // red "No FPGA detected" without the operator having to run a circuit first.
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
  // nShorDraft is the raw text of the N input so the operator can clear & retype
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
  // Run-token guards: each poller captures its token at enqueue time and bails
  // out of a stale in-flight poll if a newer Run started. Without this, a
  // double-click on Run starts a new job (clearing the pending setTimeout) but
  // the PREVIOUS in-flight api.job(OLD) can still resolve and call setJob /
  // setResult with the OLD job's data, clobbering the NEW run's UI state.
  const circuitRunToken = useRef(0)
  const parRunToken = useRef(0)
  const arrCalibRunToken = useRef(0)
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

  // ---- 64-parallel computing + per-pixel array calibration ----
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
  // 1.75 MHz/µm → ≈70 addressable lines/pixel at 200 µm (within the on-board
  // coil's 2.23 MHz/µm full-scale; the gradient is the lever for per-pixel N).
  const [parGrad, setParGrad] = useState(1.75)
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
  const signalArr = useAsync(() => api.signalArray({ profile, temperature_k: temperature, sequence: seq, detuning_mhz: detuning, points: 140, seed, hardware: hwMode === 'hardware' }), [profile, temperature, seq, detuning, seed, hwMode])

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
  // the operator clear the field and retype without a snap-back).
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
    const pixel = { pixel_mode: pixelMode, prime_readout: primeReadout,
                    noise_env: noiseEnv }
    if (nNvPerPixel != null) pixel.n_nv_per_pixel = nNvPerPixel
    if (registerLineFreqsHz) pixel.register_line_freqs_hz = registerLineFreqsHz
    // Anti-noise filter stack preset (off = no filter dict sent -> back-compat).
    // The stack attenuates the environmental channels modelled by noise_env; it
    // only has something to filter when noise_env is lab/quiet/noisy, so the
    // visible effect is largest in a noisy lab (filter restores the readout).
    if (antiNoisePreset !== 'off') pixel.anti_noise = ANTI_NOISE_PRESETS[antiNoisePreset]
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
    const myRun = ++circuitRunToken.current
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
      // 1000 polls = 600 s, matching the backend JOB_TIMEOUT_S so the
      // 16-qubit parallel Shor band (N=16..31) finishes inside the window
      // instead of the UI giving up first.
      const MAX_POLLS = 1000
      const poll = async (n = 0) => {
        try {
          const rec = await api.job(jobId)
          if (myRun !== circuitRunToken.current) return  // a newer Run started; drop this stale poll
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
    const myRun = ++arrCalibRunToken.current
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
          if (myRun !== arrCalibRunToken.current) return  // a newer calibration started; drop this stale poll
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
    const myRun = ++parRunToken.current
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
          if (myRun !== parRunToken.current) return  // a newer parallel run started; drop this stale poll
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

  const loadFromHistory = async (rec) => {
    // Invalidate any in-flight circuit / parallel / calibration poll BEFORE
    // loading the history row. Without this, a prior runCircuit poll whose
    // `myRun === circuitRunToken.current` check still passes would fire its
    // setResult/setJob AFTER this load and clobber the loaded history result
    // (audit #132). Bumping the tokens makes those polls see a newer "run"
    // and drop themselves on the next iteration.
    circuitRunToken.current++
    parRunToken.current++
    arrCalibRunToken.current++
    // Fetch the FULL job record -- list_jobs excludes `result`/`code`, so the
    // list row only carries the summary fields. Without this the loaded
    // result is null and the Hardware tab shows nothing.
    let full = rec
    if (rec && rec.job_id) {
      try { full = await api.job(rec.job_id) } catch (e) { full = rec }
    }
    const r = (full && full.result) || null
    // Restore the algorithm BEFORE the derivation runs. deriveMeasurement uses
    // the current `algo` state, so a loaded Shor result rendered with the
    // dropdown still on 'bell' would show the bell derivation over Shor counts
    // (audit #104). summary.algorithm (set at enqueue) is the source of
    // truth; only restore it for single-circuit algos -- parallel-* /
    // calibrate-array records carry a parRun / calibration, not a circuit
    // result, so the dropdown is left untouched for those.
    const savedAlgo = (r && r.algorithm) || (full && full.algorithm)
    if (savedAlgo && ALGOS.includes(savedAlgo)) setAlgo(savedAlgo)
    setResult(r)
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

  // ---- CircuitContext value: expose all state + handlers to tab/panel components ----
  const ctx = {
    mainTab, setMainTab,
    fpga, fpgaErr, fpgaLoading, fpgaBusy, fpgaReply, fpgaLog,
    emIdx, setEmIdx, emDur, setEmDur, emLvl, setEmLvl,
    detSite, setDetSite,
    mwQ, setMwQ, mwFreq, setMwFreq, mwAmp, setMwAmp,
    mwDur, setMwDur, mwPhase, setMwPhase,
    rawCmd, setRawCmd,
    cmdRefOpen, setCmdRefOpen,
    qrng, qrngErr, qrngLoading, qrngBytes, setQrngBytes, qrngCN, setQrngCN, runQrng,
    hwMode, setHwMode, fpgaMode, probe,
    profile, setProfile, temperature, setTemperature, seed, setSeed,
    noiseMode, setNoiseMode,
    pixelMode, setPixelMode, nNvPerPixel, registerLineFreqsHz,
    primeReadout, setPrimeReadout, noiseEnv, setNoiseEnv,
    antiNoisePreset, setAntiNoisePreset,
    parallelMode, setParallelMode,
    circuitsSub, setCircuitsSub, sensorsSub, setSensorsSub, modes,
    compass, compassLoading, compassErr,
    compassBx, setCompassBx, compassBy, setCompassBy, compassBz, setCompassBz,
    compassAvg, setCompassAvg, runCompass,
    gyro, gyroLoading, gyroErr, gyroRate, setGyroRate, gyroAvg, setGyroAvg, runGyro,
    mag, magLoading, magErr, magAvg, setMagAvg, magGrad, setMagGrad, runMag,
    ferro, ferroLoading, ferroErr,
    ferroMoment, setFerroMoment, ferroDist, setFerroDist,
    ferroOffX, setFerroOffX, ferroOffY, setFerroOffY, runFerro,
    nav, navLoading, navErr,
    navPosX, setNavPosX, navPosY, setNavPosY, navAnom, setNavAnom, runNav,
    thermal, thermalLoading, thermalErr,
    thermalBase, setThermalBase, thermalHot, setThermalHot,
    thermalHX, setThermalHX, thermalHY, setThermalHY, runThermal,
    current, currentLoading, currentErr,
    currentI, setCurrentI, currentDepth, setCurrentDepth, currentOff, setCurrentOff, runCurrent,
    fieldAvg, setFieldAvg,
    tensor, tensorLoading, tensorErr,
    tensorMoment, setTensorMoment, tensorX, setTensorX, tensorY, setTensorY,
    tensorZ, setTensorZ, tensorBg, setTensorBg, tensorAvg, setTensorAvg, runTensor,
    algo, setAlgo, shots, setShots, nShor, nShorDraft, setNShorDraft,
    numQubits, setNumQubits, bbEve, setBbEve,
    mathOp, setMathOp, mathA, setMathA, mathB, setMathB,
    groverMarked, setGroverMarked, qftX, setQftX,
    qrBytes, setQrBytes, qrCN, setQrCN,
    customCode, setCustomCode,
    job, result, runErr,
    seq, setSeq, detuning, setDetuning,
    calRun, calRunning, calErr,
    parRun, parRunning, parJob, parErr,
    arrCalib, arrCalibRunning, arrCalibErr,
    parGrad, setParGrad, parPixelSize, setParPixelSize,
    parNPixels, setParNPixels, parUseCalib, setParUseCalib,
    history, historyLoading,
    scripts, scriptsLoading, edName, setEdName, edCode, setEdCode, edId, scriptMsg,
    health, info, odmr, cal, signal, signalArr,
    running, runCounts, runMarginal, displayCounts, runAssessment, runBits, runRecovered, meas,
    parAgg, parWinnerMarginal, parSourceCounts, parTopCounts,
    parDisplayCounts, parShots, parRecovered, parMeas,
    refreshFpga, sendFpga,
    refreshHistory, refreshScripts,
    saveScript, newScript, editScript, removeScript, loadScriptToRunner,
    buildBody, buildParallelBody, runCircuit, runCalibration, calibrateArray, runParallel,
    loadFromHistory,
  }

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

      <CircuitContext.Provider value={ctx}>
        {mainTab === 'fpga' ? <FpgaTab />
          : mainTab === 'sensors' ? <SensorsTab />
          : mainTab === 'code' ? <CodeTab />
          : mainTab === 'history' ? <HistoryTab />
          : <HardwareTab />}
      </CircuitContext.Provider>
    </div>
  )
}
