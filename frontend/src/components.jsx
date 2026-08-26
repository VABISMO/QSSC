import React from 'react'

// ---------------------------------------------------------------------------
// QubitGrid — 8x8 on-chip graphene/MoS₂ photodetector / CQD-emitter site grid;
// cells light up for the measured bitstring of the most recent run (lock-in
// readout, no camera).
// ---------------------------------------------------------------------------
export function QubitGrid({ sites, bitstring, numClbits }) {
  const cells = Array.from({ length: 64 }, (_, idx) => {
    const r = Math.floor(idx / 8), c = idx % 8
    const site = sites && sites[idx]
    // qiskit bitstring: index 0 is the most-significant (last measured) bit;
    // classical bit cl maps to string index (numClbits-1-cl). We light a cell
    // when its measured classical bit is '1'.
    let on = false
    if (bitstring && numClbits) {
      const strIdx = numClbits - 1 - idx
      if (strIdx >= 0 && strIdx < bitstring.length) on = bitstring[strIdx] === '1'
    }
    return { idx, r, c, on, usable: site ? site.intensity > 100 : false }
  })
  return (
    <div>
      <div className="qgrid">
        {cells.map(cell => (
          <div key={cell.idx}
               className={`qcell ${cell.on ? 'on' : ''} ${cell.usable ? 'usable' : ''}`}
               title={`q${cell.idx} (${cell.r},${cell.c})`}>{cell.idx}</div>
        ))}
      </div>
      <div className="qgrid-legend">
        <span><span className="sw" style={{ background: 'var(--grad)' }} />|1⟩ readout</span>
        <span><span className="sw" style={{ background: '#0c1430', border: '1px solid #263056' }} />defect site</span>
        <span><span className="sw" style={{ background: '#0c1430', border: '1px solid #1d2748' }} />dark</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// OdmrChart — SVG line chart of the ODMR contrast vs frequency.
// ---------------------------------------------------------------------------
export function OdmrChart({ data, error }) {
  if (error) return <div className="error">{error}</div>
  if (!data || !data.frequencies_ghz) return <div className="muted">Loading spectrum…</div>
  const f = data.frequencies_ghz
  const c = data.contrast
  const W = 520, H = 220, P = 34
  const fx = v => P + ((v - f[0]) / (f[f.length - 1] - f[0])) * (W - 2 * P)
  const cMin = Math.min(...c), cMax = Math.max(...c)
  const cy = v => H - P - ((v - cMin) / (cMax - cMin || 1)) * (H - 2 * P)
  const path = f.map((v, i) => `${i ? 'L' : 'M'}${fx(v).toFixed(1)} ${cy(c[i]).toFixed(1)}`).join(' ')
  const yTicks = 3, xTicks = 5
  return (
    <div className="chart-wrap">
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        {[...Array(yTicks + 1)].map((_, i) => {
          const y = P + (i / yTicks) * (H - 2 * P)
          return <line key={i} className="gridline" x1={P} y1={y} x2={W - P} y2={y} />
        })}
        {[...Array(xTicks + 1)].map((_, i) => {
          const x = P + (i / xTicks) * (W - 2 * P)
          const fv = f[0] + (i / xTicks) * (f[f.length - 1] - f[0])
          return (
            <g key={i}>
              <line className="gridline" x1={x} y1={P} x2={x} y2={H - P} />
              <text className="axtext" x={x} y={H - P + 14} textAnchor="middle">{fv.toFixed(3)}</text>
            </g>
          )
        })}
        <line className="axis" x1={P} y1={H - P} x2={W - P} y2={H - P} />
        <line className="axis" x1={P} y1={P} x2={P} y2={H - P} />
        <line stroke="#f472b6" strokeWidth="1" strokeDasharray="4 3"
              x1={fx(data.d_ghz)} y1={P} x2={fx(data.d_ghz)} y2={H - P} />
        <path d={path} fill="none" stroke="url(#odmrgrad)" strokeWidth="2" />
        <defs>
          <linearGradient id="odmrgrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#22d3ee" />
            <stop offset="1" stopColor="#6366f1" />
          </linearGradient>
        </defs>
        <text className="axtext" x={6} y={H / 2} transform={`rotate(-90 12 ${H / 2})`}>contrast</text>
        <text className="axtext" x={W / 2} y={H - 4} textAnchor="middle">frequency (GHz)</text>
      </svg>
      <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
        Resonance dip ≈ <span className="mono" style={{ color: 'var(--brand)' }}>{data.resonance_ghz} GHz</span>
        {' · '}D = {data.d_ghz} GHz (pink dashed)
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// BarCounts — horizontal bar chart of measurement counts.
// ---------------------------------------------------------------------------
export function BarCounts({ counts, max = 8 }) {
  if (!counts) return null
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, max)
  const top = Math.max(...entries.map(e => e[1]), 1)
  return (
    <div className="counts">
      {entries.map(([state, n]) => (
        <div className="bar-row" key={state}>
          <span className="mono">{state}</span>
          <div className="bar-track"><div className="bar-fill" style={{ width: `${(n / top) * 100}%` }} /></div>
          <span className="mono" style={{ textAlign: 'right', color: 'var(--muted)' }}>{n}</span>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ChipStack — schematic of the van der Waals heterostructure layers.
// ---------------------------------------------------------------------------
export function ChipStack() {
  const layers = [
    { name: 'Encapsulation (PMMA / epoxy)', nm: '~1 µm', c: '#1f2a52' },
    { name: 'On-chip 2D photodetector array (graphene / MoS₂)', nm: '36×36', c: '#2a3a6e' },
    { name: 'CVD diamond spin-defect host (NV⁻, D≈2.87 GHz, T₂≈1.8 ms)', nm: '~0.5 mm', c: '#34407d' },
    { name: 'Polymer waveguides (SU-8 / PDMS, n≈1.5)', nm: '2–5 µm', c: '#3b4a8f' },
    { name: 'CQD emitter array (PEDOT:PSS / CQD / ZnO p-i-n)', nm: '~0.2 µm', c: '#43549f' },
    { name: 'ITO-coated glass substrate + Cu contacts', nm: '1–5 cm²', c: '#1b2348' },
    { name: 'Peltier (TEC1-12706) + liquid cooling stack', nm: 'thermal', c: '#16203f' },
  ]
  return (
    <div className="stack">
      {layers.map((l, i) => (
        <div className="layer" key={i} style={{ background: l.c }}>
          <span>{l.name}</span>
          <span className="nm">{l.nm}</span>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// SignalChart — oscilloscope-style analog trace of the detected photoluminescence
// during a pulse sequence (Ramsey / ODMR / pulse), from /api/signal.
// ---------------------------------------------------------------------------
export function SignalChart({ data, error }) {
  if (error) return <div className="error">{error}</div>
  if (!data || !data.time_ns) return <div className="muted">Loading signal…</div>
  const t = data.time_ns, s = data.signal
  const W = 520, H = 200, P = 32
  const tx = v => P + ((v - t[0]) / (t[t.length - 1] - t[0] || 1)) * (W - 2 * P)
  const sy = v => H - P - ((v - 0) / (1.1 - 0)) * (H - 2 * P)
  const line = s.map((v, i) => `${i ? 'L' : 'M'}${tx(t[i]).toFixed(1)} ${sy(v).toFixed(1)}`).join(' ')
  const area = `${line} L${tx(t[t.length - 1]).toFixed(1)} ${H - P} L${tx(t[0]).toFixed(1)} ${H - P} Z`
  const seqLabel = { ramsey: 'Ramsey fringes', odmr: 'CW ODMR dip', pulse: 'Spin readout decay' }[data.sequence] || data.sequence
  return (
    <div className="chart-wrap">
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        {[0, .25, .5, .75, 1].map((f, i) => {
          const y = P + f * (H - 2 * P)
          return <line key={i} className="gridline" x1={P} y1={y} x2={W - P} y2={y} />
        })}
        <line className="axis" x1={P} y1={H - P} x2={W - P} y2={H - P} />
        <line className="axis" x1={P} y1={P} x2={P} y2={H - P} />
        <path d={area} fill="url(#sigarea)" opacity="0.25" />
        <path d={line} fill="none" stroke="url(#siggrad)" strokeWidth="2" />
        <defs>
          <linearGradient id="siggrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#34d399" />
            <stop offset="1" stopColor="#22d3ee" />
          </linearGradient>
          <linearGradient id="sigarea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#22d3ee" />
            <stop offset="1" stopColor="#0b1020" />
          </linearGradient>
        </defs>
        <text className="axtext" x={6} y={H / 2} transform={`rotate(-90 12 ${H / 2})`}>PL (norm.)</text>
        <text className="axtext" x={W / 2} y={H - 4} textAnchor="middle">time (ns)</text>
      </svg>
      <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
        {seqLabel} · T₂ ≈ <span className="mono" style={{ color: 'var(--brand)' }}>{data.t2_us} µs</span>
        {' · '}readout {data.readout_ns} ns{data.detuning_mhz != null ? ` · detuning ${data.detuning_mhz} MHz` : ''}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// CalibrationPanel — runs the 3-stage VABISMO-style calibration on demand.
// ---------------------------------------------------------------------------
export function CalibrationPanel({ profile, temperature, seed, run, running, result, err }) {
  return (
    <div>
      <div className="hint" style={{ marginBottom: 10 }}>
        Full calibration: (1) scan the on-chip detector array for defect photocurrent (lock-in),
        (2) auto-align the qubit grid, (3) sweep the microwave frequency and fit the zero-field
        splitting D + linewidth.
      </div>
      <button onClick={run} disabled={running} className="ghost" type="button">
        {running ? <><span className="spinner" /> Calibrating…</> : 'Run calibration'}
      </button>
      {err && <div className="error" style={{ marginTop: 10 }}>{err}</div>}
      {result && result.stages && (
        <div style={{ marginTop: 14 }}>
          <div className="calgrid">
            {result.stages.map((st, i) => (
              <div className="calstage" key={i}>
                <div className="calstage-h">{st.name}</div>
                {i === 0 && (
                  <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>
                    detector array {st.frame_shape.join('×')} · sites detected:{' '}
                    <span className="mono" style={{ color: 'var(--ok)' }}>{st.defects_detected}</span>
                    {' · '}photocurrent mean {st.frame_mean} · max {st.frame_max}
                  </div>
                )}
                {i === 1 && (
                  <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>
                    sites aligned:{' '}
                    <span className="mono" style={{ color: 'var(--ok)' }}>{st.sites_aligned}</span>
                  </div>
                )}
                {i === 2 && (
                  <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>
                    D fit ≈ <span className="mono" style={{ color: 'var(--brand)' }}>{st.d_fit_ghz} GHz</span>
                    {' '}vs expected {st.d_expected_ghz} GHz · FWHM {st.linewidth_fwhm_ghz} GHz
                    {' · '}{st.fit_ok
                      ? <span style={{ color: 'var(--ok)' }}>✓ fit OK</span>
                      : <span style={{ color: 'var(--err)' }}>✗ fit off</span>}
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            At {result.temperature_k} K → T₁ {result.t1_ms} ms · T₂ {result.t2_us} µs
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Verdict — the honest physical-feasibility assessment of the latest run.
// ---------------------------------------------------------------------------
export function Verdict({ assessment, approximate }) {
  if (!assessment) return null
  const ok = assessment.within_coherence
  return (
    <div className={`verdict ${ok ? 'ok' : 'bad'}`}>
      <div className="verdict-h">
        {ok ? '✓ Within coherence window' : '✗ Exceeds coherence budget'}
        {approximate && <span className="tag approx">analytic limit (large circuit)</span>}
      </div>
      <div className="verdict-body">{assessment.verdict}</div>
      <div className="verdict-meta">
        <span>decomposed ≈ <b className="mono">{assessment.duration_us} µs</b></span>
        <span>T₂ ≈ <b className="mono">{assessment.t2_us} µs</b></span>
        <span>~<b className="mono">{assessment.estimated_2q_decomposed}</b> 2q gates</span>
        <span>decoherence <b className="mono">{(assessment.decoherence_fraction * 100).toFixed(0)}%</b></span>
        <span>budget <b className="mono">{assessment.coherence_budget_gates}</b> 1q gates</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// PhysicsDetails — limitations, FPGA compatibility, references, real timings.
// ---------------------------------------------------------------------------
export function PhysicsDetails({ info }) {
  if (!info) return null
  const fpga = info.fpga || {}
  const lims = info.limitations || []
  const refs = info.references || []
  return (
    <div className="phys">
      <div className="phys-row">
        <span><b className="mono">{info.gate_1q_ns}</b> ns 1q</span>
        <span><b className="mono">{info.gate_2q_ns}</b> ns 2q</span>
        <span><b className="mono">{info.readout_ns}</b> ns readout</span>
        <span><b className="mono">{info.init_ns}</b> ns init</span>
      </div>
      <div className={`fpga-box ${fpga.compatible ? 'ok' : 'bad'}`}>
        <div className="fpga-h">FPGA · {fpga.fpga} @ {fpga.fpga_clock_mhz} MHz · {fpga.mw_source}</div>
        <div className="fpga-grid">
          <span>tick <b className="mono">{fpga.fpga_tick_ns} ns</b></span>
          <span>pulse resolvable <b className="mono">{String(fpga.pulse_resolvable)}</b></span>
          <span>readout samplable <b className="mono">{String(fpga.readout_samplable)}</b></span>
          <span>within coherence <b className="mono">{String(fpga.within_coherence_window)}</b></span>
          <span className={fpga.compatible ? 'okt' : 'badt'}>→ {fpga.compatible ? 'compatible' : 'NOT compatible'}</span>
        </div>
        {fpga.note && <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>{fpga.note}</div>}
      </div>
      {lims.length > 0 && (
        <div className="lim">
          <div className="lim-h">Limitations (honest, paper-grounded)</div>
          <ul>{lims.map((l, i) => <li key={i}>{l}</li>)}</ul>
        </div>
      )}
      {refs.length > 0 && (
        <details className="refs">
          <summary>References ({refs.length})</summary>
          <ul>{refs.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </details>
      )}
    </div>
  )
}