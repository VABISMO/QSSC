import React, { useEffect, useState, useCallback, useRef } from 'react'
import { api } from './api.js'
import {
  QubitGrid, OdmrChart, BarCounts,
  SignalChart, CalibrationPanel, Verdict,
  OpticalChainDiagram, RunPhysics,
} from './components.jsx'

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
  bell:    { min: 2, max: 16, step: 1, def: 4,  label: 'Qubits n',         hint: 'GHZ chain on n qubits (H + CNOT ladder)' },
  qrand:   { min: 2, max: 16, step: 1, def: 8,  label: 'Qubits n',         hint: 'n qubits prepared in |+⟩ → von-Neumann-extracted entropy' },
  qft:     { min: 2, max: 8,  step: 1, def: 4,  label: 'Qubits n',         hint: 'QFT of an n-qubit computational-basis state' },
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

  // ---- global control state ----
  const [profile, setProfile] = useState('NV')
  // Peltier liquid-cooling setpoint: 2-stage TEC + NTC + PID holds the chip at
  // 295 K +/- 0.1 K (see docs/FABRICATION_PROTOCOL_Diamond.md, protocol.py).
  const [temperature, setTemperature] = useState(295)
  const [seed, setSeed] = useState(42)
  const [noiseMode, setNoiseMode] = useState('real')

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
          <span className="mt-ic">⬡</span> Hardware
        </div>
        <div className={`maintab ${mainTab === 'code' ? 'active' : ''}`} onClick={() => { setMainTab('code'); refreshScripts() }}>
          <span className="mt-ic">{'</>'}</span> Code
        </div>
        <div className={`maintab ${mainTab === 'history' ? 'active' : ''}`} onClick={() => { setMainTab('history'); refreshHistory() }}>
          <span className="mt-ic">◷</span> History
        </div>
      </div>

      {mainTab === 'code' ? (
        /* ============================= CODE (scripts) ============================= */
        <div className="grid cols-2">
          <div className="card">
            <h2><span className="dot" />Script editor</h2>
            <div className="hint">Save Qiskit scripts (Python snippet or OpenQASM 2). Saved scripts appear as loadable entries in the Hardware circuit runner.</div>
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
              <div className="hint">The physics of every chip component, recalculated for the selected defect &amp; temperature: CQD ASE emitter → SU-8/PDMS waveguide → <b>defect crystal</b> (the spin host / qubit — pump in, photoluminescence out) → long-pass filter → 2D photodetector → TIA → Artix-7 XADC → 1 kHz lock-in. Cooling cuts detector noise (∝√T) and raises ODMR contrast, so the lock-in SNR climbs — change the temperature or defect above and watch the dip, noise and SNR update.</div>
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