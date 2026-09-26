// Circuit-run result display: the 8x8 site grid, frequency tables, count bars,
// and the measurement-derivation help modal. Extracted from components.jsx.

import React, { useState } from 'react'
import { deriveMeasurement } from '../derive.js'

// ---------------------------------------------------------------------------
// QubitGrid — 8x8 on-chip graphene/MoS₂ photodetector / CQD-emitter site grid;
// cells light up for the measured bitstring of the most recent run (lock-in
// readout, no camera).
// ---------------------------------------------------------------------------
export function QubitGrid({ sites, counts, numClbits, recovered }) {
  // Show the measured bitstring of the run. When the run recovered a result
  // (Shor / period-finding: a phase peak that yielded the period / factors via
  // continued fractions), the grid lights the RECOVERED signal bitstring in
  // green -- "the good measurement, not the noise" -- because on a noisy real
  // run the most-counted (top) outcome can be a noise state while the actual
  // signal peak is buried. When no peak was recovered the grid falls back to
  // the top (most-counted) bitstring. Qiskit bitstrings are MSB-first, so bit
  // i (qubit i) maps to string index (numClbits-1-i). The lit intensity encodes
  // the shown state's share of total shots (confidence).
  const total = counts ? Object.values(counts).reduce((s, c) => s + (+c), 0) : 0
  let topBits = null, confidence = 0
  if (counts && total > 0 && numClbits) {
    let topBs = null, topN = -1
    for (const [bs, c] of Object.entries(counts)) {
      if (+c > topN) { topN = +c; topBs = bs }
    }
    confidence = topN / total
    topBits = String(topBs).replace(/ /g, '')
  }
  const recBits = recovered && recovered.bitstring
    ? String(recovered.bitstring).replace(/ /g, '') : null
  const showBits = recBits || topBits
  const bitOn = (i) => {
    if (!showBits) return false
    const s = numClbits - 1 - i
    return s >= 0 && s < showBits.length && showBits[s] === '1'
  }
  const cells = Array.from({ length: 64 }, (_, idx) => {
    const r = Math.floor(idx / 8), c = idx % 8
    const site = sites && sites[idx]
    const measured = !!(numClbits && idx < numClbits)
    const on = measured && bitOn(idx)
    return { idx, r, c, on, measured, signal: !!recBits, confidence, usable: site ? site.intensity > 100 : false }
  })
  return (
    <div>
      <div className="qgrid">
        {cells.map(cell => {
          const lit = cell.on
          const style = lit ? { '--lit': (0.35 + 0.65 * cell.confidence).toFixed(3) } : undefined
          return (
            <div key={cell.idx}
                 className={`qcell ${lit ? 'on' : ''} ${cell.signal ? 'signal' : ''} ${cell.measured ? 'measured' : ''} ${cell.usable ? 'usable' : ''}`}
                 style={style}
                 title={`q${cell.idx} (${cell.r},${cell.c}) · register pixel${cell.measured ? ` · ${cell.signal ? 'signal' : 'top'} bit=${lit ? 1 : 0} · confidence ${(cell.confidence * 100).toFixed(0)}%` : ''}`}>{cell.idx}</div>
          )
        })}
      </div>
      <div className="qgrid-legend">
        {recBits
          ? <span><span className="sw signal-sw" />recovered signal (→ result)</span>
          : <span><span className="sw" style={{ background: 'var(--grad)' }} />|1⟩ in top result</span>}
        <span><span className="sw" style={{ background: '#0c1430', border: '1px solid #263056' }} />defect site</span>
        <span><span className="sw" style={{ background: '#0c1430', border: '1px solid #1d2748' }} />dark</span>
      </div>
      {recovered && recovered.bitstring ? (
        <div className="qgrid-recovered mono">
          signal: phase {recovered.phase} → r={recovered.period}
          {recovered.factors ? ` → ${recovered.factors[0]}×${recovered.factors[1]}` : ''}
        </div>
      ) : null}
    </div>
  )
}

// ---------------------------------------------------------------------------
// QubitFrequencyTable — the per-pin resonance map produced by stage 4 of the
// calibration (a per-pin ODMR sweep that fits each pin's dip). Each of the 64
// die sites (pins) is identified by its measured ms=0↔ms=±1 transition
// frequency: D plus a per-site strain / local-B / hyperfine detuning. That
// detuning IS the individual-microwave-addressing mechanism — a narrow MW
// pulse at f_i hits only qubit i (within the ODMR linewidth). This table only
// exists AFTER calibration; before it runs there is nothing to show.
// Readout is per-site (no camera); T₂ is the coherence budget.
// ---------------------------------------------------------------------------
export function QubitFrequencyTable({ sites, dGhz, loading, error }) {
  if (loading) return <div className="muted"><span className="spinner" /> sweeping per-pin ODMR + Ramsey/Hahn…</div>
  if (error) return <div className="error">{error}</div>
  if (!sites || !sites.length) return <div className="muted">no site data</div>
  const rows = sites
  const detunes = rows.map(s => s.detuning_mhz).filter(v => v != null && !isNaN(v))
  const spread = detunes.length ? Math.max(...detunes) - Math.min(...detunes) : 0
  return (
    <div>
      <div className="hint" style={{ marginBottom: 8 }}>
        Per-pin ODMR + coherence sweep — each pin's dip is fit individually (measured D{dGhz ? `≈${dGhz}` : ''} GHz
        ± a per-site detuning; a narrow MW pulse at f<sub>i</sub> addresses only pin i). The <b>Qubits</b> column
        is the per-pixel register width N resolved by the field gradient (the ODMR line count at the
        applied gradient). T₂ = Hahn-echo coherence (µs), T₂* = Ramsey inhomogeneous dephasing (µs).
        Measured detuning spread: <b>{spread.toFixed(2)} MHz</b>.
      </div>
      <div className="qfreq-scroll">
        <table className="qfreq">
          <thead>
            <tr>
              <th>Pin</th><th>R</th><th>C</th>
              <th>Qubits</th>
              <th>Measured fᵢ (GHz)</th>
              <th>Detuning (MHz)</th>
              <th>T₂ (µs)</th>
              <th>T₂* (µs)</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(s => {
              const det = s.detuning_mhz
              const pos = det > 0
              const nq = s.n_qubits != null ? s.n_qubits : 1
              return (
                <tr key={s.qubit_id} className={s.usable === false ? 'drop' : ''}>
                  <td className="mono">{s.pin != null ? s.pin : s.qubit_id}</td>
                  <td>{s.row}</td>
                  <td>{s.col}</td>
                  <td className="mono" style={{ color: nq > 1 ? 'var(--ok, #2c8a4a)' : 'var(--muted)', fontWeight: 600 }}>{nq}</td>
                  <td className="mono">{(s.resonance_ghz != null ? s.resonance_ghz : s.frequency_ghz).toFixed(6)}</td>
                  <td className={`mono det ${pos ? 'pos' : 'neg'}`}>{det != null ? (det > 0 ? '+' : '') + det.toFixed(3) : '—'}</td>
                  <td className="mono">{(s.t2_us != null ? s.t2_us : 0).toFixed(1)}</td>
                  <td className="mono">{(s.t2_star_us != null ? s.t2_star_us : 0).toFixed(1)}</td>
                  <td>{s.usable === false ? <span className="tag bad">dropout</span> : <span className="tag ok">ok</span>}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ParallelPixelFreqTable — the 64-pixel resonance map produced by the array
// calibration (calibrate_array). Each pixel's decompose_odmr resolves N ODMR
// line centres (GHz); the line count IS that pixel's measured register width.
// One row per pixel (8×8 grid), mirroring the single-pixel QubitFrequencyTable
// but per-pixel (a pixel holds N lines, not one).
// ---------------------------------------------------------------------------
export function ParallelPixelFreqTable({ pixels, dGhz }) {
  if (!pixels || !pixels.length) return <div className="muted">no pixel data</div>
  const cols = 8
  const widths = pixels.map(p => p.register_width || 0)
  const working = widths.filter(w => w > 0).length
  const allLines = pixels.flatMap(p => p.odmr_lines || [])
  const spread = allLines.length ? (Math.max(...allLines) - Math.min(...allLines)) * 1e3 : 0
  return (
    <div>
      <div className="hint" style={{ marginBottom: 8 }}>
        Per-pixel ODMR decomposition — each of the {pixels.length} die pixels is swept and
        <b> decompose_odmr</b> fits its resolvable lines (measured D{dGhz ? `≈${dGhz}` : ''} GHz
        ± a per-pixel strain / local-B / gradient detuning). The <b>Qubits</b> column is the
        pixel's measured register width N (the ODMR line count at the applied gradient).
        {working}/{pixels.length} pixels resolved ≥1 line; aggregate detuning
        spread <b>{spread.toFixed(2)} MHz</b>. A narrow MW pulse at any f<sub>i</sub> addresses
        only that pixel's i-th NV.
      </div>
      <div className="qfreq-scroll">
        <table className="qfreq">
          <thead>
            <tr>
              <th>Pixel</th><th>R</th><th>C</th>
              <th>µdiamonds</th>
              <th>Qubits</th>
              <th>Resolved ODMR line frequencies (GHz)</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {pixels.map(p => {
              const id = p.pixel_id
              const nq = p.register_width || 0
              const lines = p.odmr_lines || []
              return (
                <tr key={id} className={nq === 0 ? 'drop' : ''}>
                  <td className="mono">{id}</td>
                  <td>{Math.floor(id / cols)}</td>
                  <td>{id % cols}</td>
                  <td className="mono">{p.microdiamonds != null ? p.microdiamonds : '–'}</td>
                  <td className="mono" style={{ color: nq > 1 ? 'var(--ok, #2c8a4a)' : nq === 1 ? 'var(--muted)' : 'var(--err)', fontWeight: 600 }}>{nq}</td>
                  <td className="mono" style={{ fontSize: 11.5 }}>
                    {lines.length ? lines.map(f => f.toFixed(6)).join(' · ') : '—'}
                  </td>
                  <td>{nq === 0
                    ? <span className="tag bad">dropout</span>
                    : nq === 1
                      ? <span className="tag">ensemble</span>
                      : <span className="tag ok">register</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// BarCounts — horizontal bar chart of measurement counts.
// ---------------------------------------------------------------------------
export function BarCounts({ counts, max = 8, recovered, shots, signal }) {
  if (!counts) return null
  // `counts` is the FULL counts dict (the backend never truncates), so its
  // values sum to the shot count. The bars below show only the top `max` --
  // for a uniform distribution (e.g. QFT of a basis state) every outcome is
  // counted once, so each top bar is 1 even though Σ across ALL outcomes
  // equals the shots. Surface that total + the unique-outcome count so the
  // displayed counts visibly correspond to the chosen shots.
  const allVals = Object.values(counts).map(v => +v)
  const total = allVals.reduce((s, c) => s + c, 0)
  const unique = allVals.length
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, max)
  const top = Math.max(...entries.map(e => e[1]), 1)
  const clean = (s) => String(s).replace(/ /g, '')
  // Which bars are "the correct result" + the marker label. The `signal`
  // prop (from deriveMeasurement) generalises the Shor-only `recovered` marker
  // to every algorithm: it lists the qiskit bitstring(s) to highlight and the
  // short label to print on them (→ 3×5, → r=4, → found 5, ≈ uniform, …).
  // Fall back to the legacy `recovered` object (shor/period/qftnos) so callers
  // that only pass `recovered` still mark the phase peak.
  const markSet = signal && signal.bars && signal.bars.length
    ? new Set(signal.bars.map(clean)) : null
  const markLabel = signal && signal.label != null ? signal.label
    : (recovered && recovered.factors ? `→ ${recovered.factors[0]}×${recovered.factors[1]}`
       : (recovered && recovered.period != null ? `→ r=${recovered.period}` : null))
  const recBs = !markSet && recovered && recovered.bitstring ? clean(recovered.bitstring) : null
  const shotTotal = shots != null ? +shots : null
  const sumMatches = shotTotal != null && total === shotTotal
  return (
    <div className="counts">
      {/* qiskit bitstrings are MSB-first (leftmost char = q_{n-1}); reverse so
          the displayed string reads q0..q_{n-1} left-to-right, matching the
          QubitGrid (cell idx = qubit idx, q0 top-left). The raw counts keys are
          unchanged -- only the display label is reversed, so a state like
          qiskit "001" (q0=1) shows as "100" and its leftmost bar bit lines up
          with grid cell 0 instead of looking like cell 2. */}
      <div className="counts-axis mono">q0 ← state → qₙ₋₁  (matches grid cell order)</div>
      <div className="counts-summary mono">
        Σ {total.toLocaleString()}{shotTotal != null ? ` / ${shotTotal.toLocaleString()} shots` : ''}
        {shotTotal != null ? (sumMatches ? ' ✓' : ' ✗ mismatch') : ''}
        {' · '}{unique.toLocaleString()} unique outcome{unique === 1 ? '' : 's'}
        {' · top '}{entries.length} shown
      </div>
      {entries.map(([state, n]) => {
        const disp = String(state).replace(/ /g, '').split('').reverse().join('')
        const isRec = markSet ? markSet.has(clean(state)) : (!!recBs && clean(state) === recBs)
        return (
          <div className={`bar-row${isRec ? ' signal' : ''}`} key={state}>
            <span className="mono" title={`qiskit MSB-first: ${state}${isRec ? ' · the correct result' : ''}`}>{disp}</span>
            <div className="bar-track"><div className="bar-fill" style={{ width: `${(n / top) * 100}%` }} /></div>
            <span className="mono" style={{ textAlign: 'right', color: 'var(--muted)' }}>{n}</span>
            {isRec && markLabel ? <span className="bar-sig mono">← {markLabel}</span> : null}
          </div>
        )
      })}
    </div>
  )
}

// ran, exactly how the measured binary becomes the result (the per-algorithm
// derivation chain with the CONCRETE numbers from this run). Pure client-side
// via deriveMeasurement(algo, result, counts). Marked bar(s) in BarCounts share
// the same derivation, so the modal is the "show your work" for the marker.
// ---------------------------------------------------------------------------
const ALGO_NAMES = {
  shor: 'Shor (factoring)', period: 'Period finding', bell: 'Bell / GHZ',
  math: 'Quantum arithmetic', grover: 'Grover search', qft: 'QFT',
  qftnos: 'QFT-NOS (factoring)', qrand: 'Quantum RNG', bb84: 'BB84 QKD',
  custom: 'Custom circuit',
}
export function MeasurementHelp({ algo, result, counts, numClbits, meas }) {
  const [open, setOpen] = useState(false)
  const m = meas || deriveMeasurement(algo, result, counts, numClbits)
  if (!m) return null
  return (
    <>
      <button className="meas-help-btn" type="button"
        onClick={() => setOpen(true)}
        title="How is the result calculated from this measurement?">
        ?<span className="meas-help-txt"> how the binary → result</span>
      </button>
      {open && (
        <div className="meas-help-overlay" onClick={() => setOpen(false)}>
          <div className="meas-help-modal" onClick={(e) => e.stopPropagation()}>
            <button className="meas-help-close" type="button"
              onClick={() => setOpen(false)} aria-label="Close">×</button>
            <h2><span className="dot" />{ALGO_NAMES[algo] || algo} — derivation</h2>
            <div className="hint" style={{ marginBottom: 10 }}>{m.title}</div>
            <ol className="derive-steps">
              {m.steps.map((s, i) => (
                <li key={i}>
                  <span className="derive-k">{s.k}</span>
                  <span className="derive-v mono">{s.v}</span>
                </li>
              ))}
            </ol>
            <p className="derive-summary">{m.summary}</p>
          </div>
        </div>
      )}
    </>
  )
}

