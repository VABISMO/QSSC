import React from 'react'
import { useCircuit } from '../circuitContext.js'
import { BarCounts, MeasurementHelp, QubitGrid } from '../components.jsx'
import {
  ALGOS, ALGO_LABELS, MATH_OPS, QUBIT_CFG, CUSTOM_EXAMPLES,
} from '../constants.js'
import { derivedQubits, downloadPem } from '../utils.js'

export default function CircuitRunner() {
  const {
    parallelMode,
    algo, setAlgo, numQubits, setNumQubits,
    nShor, nShorDraft, setNShorDraft,
    shots, setShots,
    parNPixels, setParNPixels, parUseCalib, setParUseCalib,
    mathOp, setMathOp, mathA, setMathA, mathB, setMathB,
    groverMarked, setGroverMarked,
    qftX, setQftX,
    qrBytes, setQrBytes, qrCN, setQrCN,
    bbEve, setBbEve,
    customCode, setCustomCode,
    scripts,
    runParallel, buildParallelBody, runCircuit,
    parRunning, running, parJob, job,
    parErr, runErr,
    result, parRun,
    displayCounts, parDisplayCounts, parShots, runMarginal,
    meas, parMeas,
    runRecovered, parRecovered, runBits,
    cal,
  } = useCircuit()
  return (
    <>
          {/* ============================= CIRCUIT RUNNER (single-pixel path) ============================= */}
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
                             // the operator typing a larger value, and a too-large
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
                        N&gt;15 (16 qubits) races 4 coprime bases across pixels and
                        finishes in ~1.5 min (measured ~81 s, 4 px @ ~20 s/px on the
                        real run_array path via a single clean rq worker; base 2 wins
                        for N=21, factors [7,3]). At 300 K real mode it may decohere to
                        no factors (the real T2 physics); lower the Temperature or use
                        ideal mode to factor it.
                        N&gt;31 (19 qubits) races 4 bases, ~1.5-2 min (single attempt).
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
    </>
  )
}