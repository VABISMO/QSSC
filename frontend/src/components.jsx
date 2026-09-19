import React, { useState, useEffect, useMemo, useRef } from 'react'
import { deriveMeasurement } from './derive.js'

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
// OdmrChart — SVG line chart of the ODMR contrast vs frequency.
// ---------------------------------------------------------------------------
export function OdmrChart({ data, error, caption }) {
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
        {caption || <>Resonance dip ≈ <span className="mono" style={{ color: 'var(--brand)' }}>{data.resonance_ghz} GHz</span>
        {' · '}D = {data.d_ghz} GHz (pink dashed)</>}
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

// ---------------------------------------------------------------------------
// OpticalChainDiagram — SVG flow of the on-chip readout chain: the physics of
// every chip component the simulator checks, RECALCULATED for the current
// profile + temperature (each execution). CQD ASE emitter -> SU-8/PDMS
// waveguide -> defect crystal (spin host / qubit: pump in, PL out) -> long-pass
// filter -> 2D photodetector -> TIA -> 4x AD7616 (16-bit parallel) -> 1 kHz lock-in.
//
// The per-stage numbers are computed client-side from `profile` + `temperature`
// (mirroring qssc/backend.py::optical_readout) so the diagram is always live
// and updates on every run even while the public API is stale. If the API
// serves `optical_readout`, those server values override per-field (server is
// then computing the same temperature/profile-dependent formulas).
//
// Temperature dependence (teaching model, anchored at 300 K):
//   readout noise ~ N0 * sqrt(T/300)        (graphene dark current + TIA thermal)
//   ODMR contrast ~ C0 * clamp(1 + 0.5*(300-T)/300, 0.5, 1.6)  (spin polarisation)
//   ASE gain      ~ 1.15 * (1 + 0.10*(300-T)/300)              (semiconductor gain)
//   WG loss       ~ 0.5 * (0.7 + 0.3*T/300) dB/cm              (polymer loss)
// dip = BRIGHT * contrast ; SNR = dip / noise. Cooling raises contrast and
// lowers noise, so the lock-in SNR climbs sharply — the readout improves cold.
// ---------------------------------------------------------------------------
const ADC_MAX = 65535, BRIGHT_CODE = 51200, LOCKIN_MOD_HZ = 1000

// per-profile optical-stack config (source of truth mirrors backend.py).
// The defect crystal is the central quantum element (pump in -> PL out, ODMR
// contrast modulates the PL), so it carries the spin-host identity + live T2.
const OPT_PROFILE = {
  'NV': {
    wl: 532, pumpMw: 3.36, forwardV: 2.8, etaEqe: 0.12, driveMa: 10,
    etaEw: 0.05, etaWd: 0.05, wgLoss: 0.5, wgLen: 0.3,
    filter: '650 nm long-pass', filtT: 0.92, filterPasses: 'PL 637–800 nm',
    detector: 'graphene/MoS₂', resp: 0.10, contrast0: 0.05, linewidthMHz: 5,
    dGhz: 2.87, crystal: 'NV-diamond', crystalIcon: '💎', plRange: '637–800 nm',
    t2UsRoom: 1800, readoutNs: 800, readoutErr: 0.08,
  },
  'hBN-VB': {
    wl: 532, pumpMw: 3.36, forwardV: 2.8, etaEqe: 0.12, driveMa: 10,
    etaEw: 0.05, etaWd: 0.05, wgLoss: 0.5, wgLen: 0.3,
    filter: '650 nm long-pass', filtT: 0.92, filterPasses: 'PL 820–860 nm',
    detector: 'graphene/MoS₂', resp: 0.10, contrast0: 0.05, linewidthMHz: 5,
    dGhz: 3.46, crystal: 'hBN V_B⁻', crystalIcon: '⬡', plRange: '820–860 nm',
    t2UsRoom: 2, readoutNs: 800, readoutErr: 0.12,
  },
  'SiC-VSi': {
    wl: 916, pumpMw: 3.17, forwardV: 3.3, etaEqe: 0.08, driveMa: 12,
    etaEw: 0.04, etaWd: 0.04, wgLoss: 0.6, wgLen: 0.3,
    filter: '850 nm long-pass', filtT: 0.90, filterPasses: 'PL 890–940 nm',
    detector: 'few-layer MoS₂', resp: 0.08, contrast0: 0.03, linewidthMHz: 1.5,
    dGhz: 0.07, crystal: 'SiC V_Si', crystalIcon: '🔷', plRange: '890–940 nm',
    t2UsRoom: 40, readoutNs: 800, readoutErr: 0.10,
  },
}

function computeOpticalChain(profile, temperature) {
  const p = OPT_PROFILE[profile] || OPT_PROFILE['NV']
  const T = Math.max(0.001, temperature)
  const noise = 40 * Math.sqrt(T / 300)                                  // counts
  const contrast = p.contrast0 * Math.min(1.6, Math.max(0.5, 1 + 0.5 * (300 - T) / 300))
  const aseGain = 1.15 * Math.min(1.25, 1 + 0.10 * (300 - T) / 300)
  const wgLoss = p.wgLoss * (0.7 + 0.3 * T / 300)
  const wgProp = Math.pow(10, -(wgLoss * p.wgLen) / 10)
  const etaEnd = p.etaEw * wgProp * p.etaWd * p.filtT
  const afterAse = p.pumpMw * aseGain
  const afterWg = afterAse * p.etaEw * wgProp * 1000                      // µW pump at crystal
  const atDet = afterAse * etaEnd * 1000                                  // µW PL at detector
  const bright = BRIGHT_CODE
  const dark = Math.round(bright * (1 - contrast))
  const dip = Math.round(bright - dark)
  const snr = dip / noise
  const iBright = (bright / ADC_MAX)                                     // µA (1 MΩ, 1 Vref)
  return { ...p, noise, contrast, aseGain, wgProp, etaEnd, afterAse, afterWg,
    atDet, bright, dark, dip, snr, iBright, T }
}

function fmtT2(us) {
  if (us == null) return ''
  return us >= 1000 ? `${(us / 1000).toFixed(2)} ms` : `${us.toFixed(1)} µs`
}

export function OpticalChainDiagram({ data, infoData, profile, temperature, activeSites }) {
  const o = data || {}
  const em = o.emitter || {}, wg = o.waveguide || {}, fl = o.filter || {},
        de = o.detector || {}, rd = o.readout || {}, lb = o.link_budget || {}
  // live client-side computation (always available); server `data` overrides
  // per-field when the API has served optical_readout.
  const c = computeOpticalChain(profile, temperature)
  const wl = em.wavelength_nm ?? c.wl
  const pump = em.pump_optical_power_mw ?? c.pumpMw
  const afterAse = em.after_ase_mw ?? +c.afterAse.toFixed(2)
  const wgProp = wg.propagation_efficiency ?? +c.wgProp.toFixed(3)
  const afterWg = lb.after_waveguide_uw ?? +c.afterWg.toFixed(1)
  const filterLabel = (fl.type ?? c.filter)
  const filtT = fl.transmission ?? c.filtT
  const filterPasses = fl.passes ?? c.filterPasses
  const detLabel = (de.type ?? c.detector).replace(' photodetector array', '')
  const resp = de.responsivity_a_per_w ?? c.resp
  const atDet = lb.at_detector_uw ?? +c.atDet.toFixed(2)
  const iBright = rd.photocurrent_bright_ua ?? +c.iBright.toFixed(3)
  const tiaMohm = (de.transimpedance_ohm ?? 1e6) / 1e6
  const vBright = iBright * tiaMohm
  const adcBits = de.adc_bits ?? 16
  const bright = rd.bright_code ?? c.bright
  const dark = rd.dark_code ?? c.dark
  const dip = rd.dip_counts ?? c.dip
  const snr = rd.single_read_snr_sigma ?? +c.snr.toFixed(1)
  const contrastPct = (((rd.odmr_contrast ?? c.contrast) * 100)).toFixed(1)
  const etaEnd = ((lb.end_to_end_efficiency ?? c.etaEnd) * 100).toFixed(2)
  const noiseCounts = Math.round(rd.readout_noise_counts ?? c.noise)
  const T = Math.round(c.T)
  // live spin-host values from /api/backend/info (served even while the
  // optical_readout field is stale); fall back to profile defaults.
  const dGhz = infoData?.d_ghz ?? c.dGhz
  const t2Us = infoData?.t2_us ?? c.t2UsRoom
  const dLabel = dGhz < 1 ? `2|D| ${Math.round(dGhz * 1000)} MHz` : `D ${dGhz} GHz`

  const stages = [
    { icon: '💡', t: 'CQD ASE emitter', v: [`${wl} nm`, `${pump} mW pump`] },
    { icon: '〜', t: 'SU-8 / PDMS', t2: 'waveguide', v: [`ηew·ηwd ${(c.etaEw * c.etaWd * 100).toFixed(0)}%`, `prop ${(wgProp * 100).toFixed(1)}%`] },
    { icon: c.crystalIcon, t: c.crystal, accent: true, v: [dLabel, `T₂ ${fmtT2(t2Us)}`] },
    { icon: '◆', t: filterLabel.length > 16 ? filterLabel.slice(0, 14) + '…' : filterLabel, v: [`T ${(filtT * 100).toFixed(0)}%`, filterPasses] },
    { icon: '◼', t: detLabel, v: [`R ${resp} A/W`, `${iBright} µA`] },
    { icon: '⚡', t: 'TIA', v: [`${tiaMohm} MΩ`, `→ ${vBright.toFixed(2)} V`] },
    { icon: '▣', t: 'AD7616', v: [`${adcBits}-bit`, `${bright}/${dark}`] },
    { icon: '◎', t: '1 kHz lock-in', v: [`SNR ${snr}σ`, `dip ${dip} c`] },
  ]
  // 8 stages -> 7 arrows. The crystal arrow is the pump->PL transduction
  // (wavelength, not a power level); the rest are the link-budget powers.
  const powers = [
    `${afterAse} mW`, `${afterWg} µW pump`, `PL ${c.plRange}`,
    `${atDet} µW`, `${iBright} µA`, `${vBright.toFixed(2)} V`, `${bright} code`,
  ]

  const SX = 16, W = 100, GAP = 36, STEP = W + GAP
  const x = i => SX + i * STEP
  const cx = i => x(i) + W / 2
  const rectY = 44, rectH = 92, arrowY = 90
  const VB_W = SX + 8 * W + 7 * GAP + SX   // 16 + 800 + 252 + 16 = 1084
  return (
    <div className="optchain-wrap">
      <svg viewBox={`0 0 ${VB_W} 246`} className="optchain" role="img"
           aria-label={`Optical readout chain at ${T} K: CQD ASE emitter through ${c.crystal} crystal to 1 kHz lock-in`}>
        <defs>
          <linearGradient id="oc-flow" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0" stopColor="#22d3ee" /><stop offset="1" stopColor="#6366f1" />
          </linearGradient>
          <linearGradient id="oc-stage" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#1a2444" /><stop offset="1" stopColor="#141c38" />
          </linearGradient>
          <linearGradient id="oc-crystal" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="rgba(244,114,182,0.22)" />
            <stop offset="1" stopColor="rgba(99,102,241,0.10)" />
          </linearGradient>
          <linearGradient id="oc-result" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0" stopColor="rgba(244,114,182,0.16)" />
            <stop offset="1" stopColor="rgba(34,211,238,0.16)" />
          </linearGradient>
          <marker id="oc-ah" markerWidth="9" markerHeight="9" refX="7" refY="3.2" orient="auto">
            <path d="M0,0 L7,3.2 L0,6.4 Z" fill="#6366f1" />
          </marker>
        </defs>

        {/* arrows + annotations */}
        {stages.slice(0, -1).map((_, i) => {
          const x1 = x(i) + W, x2 = x(i + 1)
          const mid = (x1 + x2) / 2
          const isPL = i === 2
          return (
            <g key={`a${i}`}>
              <line x1={x1 + 2} y1={arrowY} x2={x2 - 4} y2={arrowY}
                    stroke="url(#oc-flow)" strokeWidth="2.2" markerEnd="url(#oc-ah)" />
              <text x={mid} y={156} textAnchor="middle" fontSize={isPL ? 9.5 : 10.5}
                    fill={isPL ? '#f472b6' : '#22d3ee'} fontWeight="600">
                {powers[i]}
              </text>
            </g>
          )
        })}

        {/* stages */}
        {stages.map((s, i) => (
          <g key={s.t + i}>
            <rect x={x(i)} y={rectY} width={W} height={rectH} rx="11"
                  fill={s.accent ? 'url(#oc-crystal)' : 'url(#oc-stage)'}
                  stroke={s.accent ? '#f472b6' : '#263056'} strokeWidth={s.accent ? 1.4 : 1} />
            <text x={cx(i)} y={34} textAnchor="middle" fontSize="19">{s.icon}</text>
            <text x={cx(i)} y={s.t2 ? 60 : 66} textAnchor="middle" fontSize="11"
                  fill={s.accent ? '#f472b6' : '#e6ecff'} fontWeight="700">
              {s.t}
            </text>
            {s.t2 && (
              <text x={cx(i)} y={73} textAnchor="middle" fontSize="11" fill="#e6ecff" fontWeight="600">
                {s.t2}
              </text>
            )}
            <text x={cx(i)} y={s.t2 ? 92 : 89} textAnchor="middle" fontSize="10.5" fill="#22d3ee">{s.v[0]}</text>
            <text x={cx(i)} y={s.t2 ? 108 : 106} textAnchor="middle" fontSize="9.5" fill="#94a3c4">{s.v[1]}</text>
          </g>
        ))}

        {/* result headline bar */}
        <rect x={SX} y={172} width={VB_W - 2 * SX} height={62} rx="12"
              fill="url(#oc-result)" stroke="#f472b6" strokeWidth="1.2" />
        <text x={VB_W / 2} y={199} textAnchor="middle" fontSize="14" fill="#e6ecff" fontWeight="700">
          {`@ ${T} K · ${c.crystal} ODMR dip `}
          <tspan fill="#f472b6" fontWeight="700">{`${dip} c (${contrastPct}%)`}</tspan>
          {` → SNR `}
          <tspan fill="#34d399" fontWeight="700">{`${snr}σ`}</tspan>
        </text>
        <text x={VB_W / 2} y={220} textAnchor="middle" fontSize="11" fill="#94a3c4">
          {`bright ${bright} / dark ${dark} · noise ${noiseCounts} c · pump→detector η ${etaEnd}%${activeSites ? ` · ${activeSites}/64 sites active` : ''}`}
        </text>
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// RunPhysics — the REAL per-run physics of every chip component, calculated
// from the circuit that just ran (its width, shots) + the selected defect +
// temperature. Unlike the chip-level BackendV2 params (fixed for a profile/T),
// these change with the circuit: a 2-qubit Bell run lights 2 CQD pixels and
// reads 2 detector electrodes; a 9-qubit Shor run lights 9, burns more pump
// energy, takes longer to read out, and the N-shot-averaged lock-in SNR scales
// as sqrt(shots). Computed client-side so it is always live.
// ---------------------------------------------------------------------------
function computeRunPhysics(result, profile, temperature, infoData) {
  if (!result || !result.num_qubits) return null
  const c = computeOpticalChain(profile, temperature)
  const nActive = result.num_qubits
  const nMeasured = Math.min(result.num_clbits || result.num_qubits, result.num_qubits)
  const shots = result.shots || 0
  // real per-run measured readout surfaced by the backend (read_states over
  // the 64-site grid, the displayed bitstring, mean detector photocurrent).
  // null when absent (e.g. a run that produced no optical readout).
  const opt = result.optical || null
  const measPhotocurrent = opt && typeof opt.photocurrent_mean === 'number'
    ? Math.round(opt.photocurrent_mean) : null
  const measBitstring = opt && opt.displayed_bitstring ? opt.displayed_bitstring : null
  const readoutNs = c.readoutNs
  const elecPwrMw = c.driveMa * c.forwardV            // mW electrical per pixel
  // optical pump energy: mW * ns = pJ ; -> nJ
  const optNjPerSite = (c.pumpMw * readoutNs) / 1000
  const optNjPerShot = nMeasured * optNjPerSite
  const totalOptUj = (optNjPerShot * shots) / 1000    // nJ -> uJ
  const readoutMs = (nMeasured * readoutNs * shots) / 1e6   // ns -> ms (sequential)
  const iBrightUa = +c.iBright.toFixed(3)
  const iDarkUa = +(c.iBright * (1 - c.contrast)).toFixed(3)
  const readoutErr = infoData?.readout_error ?? c.readoutErr
  const fidelity = 1 - readoutErr
  const avgSnr = c.snr * Math.sqrt(Math.max(shots, 1))   // N-shot averaging
  // ---- per-metric pass/fail vs the chip's operating range ----
  // green = inside spec / works; amber = marginal; red = out of spec / fails
  const snrStatus = c.snr >= 3 ? 'ok' : c.snr >= 2 ? 'warn' : 'bad'      // 3sigma = confident single-shot ODMR dip
  const fidStatus = fidelity >= 0.9 ? 'ok' : fidelity >= 0.8 ? 'warn' : 'bad'
  const driveStatus = (c.driveMa >= 2 && c.driveMa <= 20) ? 'ok' : 'bad'  // CQD pixel 2-20 mA spec
  const wgStatus = c.wgProp >= 0.8 ? 'ok' : c.wgProp >= 0.5 ? 'warn' : 'bad'
  const contrastPct = +(c.contrast * 100).toFixed(1)
  const contrastStatus = c.contrast >= 0.04 ? 'ok' : c.contrast >= 0.02 ? 'warn' : 'bad'
  // does the circuit fit the 8x8 = 64-site chip? (physical-execution gate)
  const gridStatus = nActive <= 64 ? 'ok' : 'bad'
  // overall = worst meaningful signal (single-read SNR dominates: if the dip
  // doesn't resolve above noise in one shot, the readout doesn't work at this T)
  const worst = (arr) => arr.includes('bad') ? 'bad' : arr.includes('warn') ? 'warn' : 'ok'
  const overall = worst([snrStatus, fidStatus, driveStatus, wgStatus, contrastStatus, gridStatus])
  return { nActive, nMeasured, shots, driveMa: c.driveMa, forwardV: c.forwardV,
    elecPwrMw, pumpMw: c.pumpMw, optNjPerSite, optNjPerShot, totalOptUj,
    readoutMs, iBrightUa, iDarkUa, bright: c.bright, dark: c.dark, dip: c.dip,
    noise: +c.noise.toFixed(0), contrastPct, snr: +c.snr.toFixed(1), avgSnr: Math.round(avgSnr),
    wgProp: c.wgProp, fidelity, readoutErr, T: Math.round(c.T), crystal: c.crystal,
    measPhotocurrent, measBitstring,
    snrStatus, fidStatus, driveStatus, wgStatus, contrastStatus, gridStatus, overall }
}

const CHIP_LABEL = { ok: '✓ in spec', warn: '⚠ marginal', bad: '✗ out of spec' }
const CHIP_LABEL_SHORT = { ok: 'OK', warn: 'marginal', bad: 'FAIL' }

export function RunPhysics({ result, profile, temperature, infoData }) {
  const p = computeRunPhysics(result, profile, temperature, infoData)
  if (!p) {
    return (
      <div className="card runphysics-card">
        <h2><span className="dot" />Execution component physics</h2>
        <div className="hint">Run a circuit — the CQD emitter array, waveguides, photodetectors and lock-in readout physics are calculated for the actual circuit that runs, with a green/amber/red verdict on whether each value is inside the chip's operating spec.</div>
        <div className="muted" style={{ marginTop: 6 }}>No circuit run yet.</div>
      </div>
    )
  }
  const pct = v => (v * 100).toFixed(1)
  const stat = (k, v, u, status, spec) => (
    <div className="stat">
      <div className="k">{k}</div>
      <div className="v">{v}{u != null && <small> {u}</small>}</div>
      {status === 'info' && <span className="chip info">descriptive</span>}
      {status && status !== 'info' && <span className={`chip ${status}`}>{CHIP_LABEL_SHORT[status]} · {spec}</span>}
    </div>
  )
  const overallTxt = p.overall === 'ok' ? '✓ Readout works at this temperature'
    : p.overall === 'warn' ? '⚠ Readout marginal — cool the chip'
    : '✗ Readout too noisy — does not work at this temperature'
  const overallWhy = p.overall === 'ok'
    ? 'single-shot ODMR dip resolves ≥3σ above detector noise; fidelity ≥90%'
    : p.overall === 'warn'
    ? 'single-shot SNR is 2–3σ or fidelity 80–90% — readout borderline; cooling raises contrast & cuts noise'
    : 'single-shot SNR <2σ or fidelity <80% — the ODMR dip does not resolve; lower the temperature or pick a brighter defect'
  return (
    <div className="card runphysics-card">
      <h2><span className="dot" />Execution component physics <span className="muted" style={{ fontWeight: 400 }}>· {p.crystal} @ {p.T} K · {p.shots} shots · {p.nActive}q circuit</span></h2>
      <div className="hint">Calculated for the circuit that just ran — each value is checked against the chip's operating spec.</div>
      <div className={`verdict ${p.overall}`} style={{ marginTop: 10 }}>
        <div className="verdict-h"><span className="verdict-tag">Readout feasibility</span>{overallTxt}</div>
        <div className="verdict-body">{overallWhy}</div>
      </div>
      <div className="stats" style={{ marginTop: 10 }}>
        {stat('Lock-in SNR', `${p.snr}σ`, `single · ${p.avgSnr}σ / ${p.shots} shots`, p.snrStatus, '≥3σ single-shot')}
        {stat('Readout fidelity', pct(p.fidelity), '%', p.fidStatus, '≥90%')}
        {stat('ODMR contrast', `${p.contrastPct}`, '%', p.contrastStatus, '≥4% dip')}
        {stat('ADC codes', `${p.bright}/${p.dark}`, `dip ${p.dip}c · noise ${p.noise}c`, 'info')}
        {stat('Drive / pixel', `${p.driveMa} mA · ${p.forwardV} V`, `→ ${p.elecPwrMw} mW`, p.driveStatus, '2–20 mA')}
        {stat('Photocurrent', `${p.iBrightUa}`, `bright / ${p.iDarkUa} dark µA`, 'info')}
        {p.measPhotocurrent != null
          ? stat('Measured readout', `${p.measPhotocurrent}`, `code · ${p.measBitstring ? `"${p.measBitstring.slice(0, 12)}${p.measBitstring.length > 12 ? '…' : ''}"` : 'mean det. code'}`, 'info')
          : null}
        {stat('Waveguide paths', `${p.nMeasured}`, `η ${(p.wgProp * 100).toFixed(1)}%`, p.wgStatus, 'η ≥80%')}
        {stat('CQD pixels fired', `${p.nMeasured}`, '/shot', p.gridStatus, 'fits 8×8 grid')}
        {stat('Detector electrodes', `${p.nMeasured}`, '/shot', p.gridStatus, 'fits 8×8 grid')}
        {stat('Pump energy', p.totalOptUj >= 1000 ? (p.totalOptUj / 1000).toFixed(2) : p.totalOptUj.toFixed(2), p.totalOptUj >= 1000 ? 'mJ optical' : 'µJ optical', 'info')}
        {stat('Readout time', p.readoutMs >= 1000 ? (p.readoutMs / 1000).toFixed(2) : p.readoutMs.toFixed(2), p.readoutMs >= 1000 ? 's sequential' : 'ms sequential', 'info')}
      </div>
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
// SignalLab — per-pixel analog signal visualizer. For every pixel in the 8×8
// array it shows the FIRED emitter drive (CQD pump gate) vs the RECEIVED
// detector photocurrent (AD7616 codes), with per-pixel KPIs and array-level
// graphs. An animated 8×8 grid pulses with the received photocurrent as a
// scrubable time cursor sweeps the pulse sequence; click a pixel for its
// sent-vs-received overlay.
// ---------------------------------------------------------------------------
const SIG_ADC_MAX = 65535

function KpiTile({ k, v, unit, accent }) {
  return (
    <div className="stat" style={{ minWidth: 96 }}>
      <div className="k">{k}</div>
      <div className="v" style={accent ? { color: accent } : undefined}>{v}<small>{unit}</small></div>
    </div>
  )
}

// mini sparkline of a pixel's received trace (for the grid tile tooltip/preview)
function MiniSpark({ recv, color }) {
  const W = 100, H = 26
  const lo = Math.min(...recv), hi = Math.max(...recv)
  const span = hi - lo || 1
  const line = recv.map((v, i) => `${i ? 'L' : 'M'}${((i / (recv.length - 1)) * W).toFixed(1)} ${(H - ((v - lo) / span) * H).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: H, display: 'block', opacity: 0.9 }}>
      <path d={line} fill="none" stroke={color} strokeWidth="1.4" />
    </svg>
  )
}

export function SignalLab({ data, error }) {
  const [sel, setSel] = useState(0)
  const [ti, setTi] = useState(0)
  const [playing, setPlaying] = useState(false)
  const raf = useRef(null)

  // auto-advance the time cursor while playing
  useEffect(() => {
    if (!playing || !data) return
    let last = performance.now()
    const tick = (now) => {
      if (now - last > 90) {
        setTi(p => (p + 1) % data.n_points)
        last = now
      }
      raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [playing, data])

  // reset cursor + selection when the data changes (new sequence / params)
  useEffect(() => { setTi(0); setSel(0) }, [data])

  const derived = useMemo(() => {
    if (!data || !data.received) return null
    const { received, fired, pixels, n_pixels } = data
    let recvMax = 1
    for (let i = 0; i < n_pixels; i++) for (let j = 0; j < received[i].length; j++)
      if (received[i][j] > recvMax) recvMax = received[i][j]
    // per-pixel totals for the scatter
    const totals = pixels.map((_, i) => {
      let f = 0, r = 0
      for (let j = 0; j < received[i].length; j++) { f += fired[i][j]; r += received[i][j] }
      return { f, r }
    })
    // SNR histogram buckets
    const snrs = pixels.map(p => p.snr)
    const sMax = Math.max(1, ...snrs), sMin = Math.min(0, ...snrs)
    const nB = 8, bins = new Array(nB).fill(0)
    for (const s of snrs) {
      let b = Math.floor(((s - sMin) / (sMax - sMin || 1)) * nB)
      if (b >= nB) b = nB - 1; if (b < 0) b = 0
      bins[b]++
    }
    return { recvMax, totals, bins, sMax, sMin }
  }, [data])

  if (error) return <div className="error">{error}</div>
  if (!data || !data.received || !derived) return <div className="muted">Loading per-pixel signal…</div>
  const { received, fired, pixels, summary, time_ns, n_points, n_pixels } = data
  const { recvMax, totals, bins, sMax } = derived
  const cols = 8
  const rows = Math.ceil(n_pixels / cols)
  const seqLabel = { ramsey: 'Ramsey fringes', odmr: 'CW ODMR dip', pulse: 'Spin readout decay' }[data.sequence] || data.sequence
  const selPx = pixels[sel] || pixels[0]

  // ---- selected-pixel fired vs received overlay (dual axis) ----
  const OW = 580, OH = 240, OP = 38
  const t0 = time_ns[0], t1 = time_ns[time_ns.length - 1]
  const ox = v => OP + ((v - t0) / (t1 - t0 || 1)) * (OW - 2 * OP)
  const oRecvY = v => OH - OP - (v / SIG_ADC_MAX) * (OH - 2 * OP)            // left axis: codes 0..65535
  const oFireY = v => OH - OP - v * (OH - 2 * OP)                        // right axis: 0..1
  const recvLine = received[sel].map((v, i) => `${i ? 'L' : 'M'}${ox(time_ns[i]).toFixed(1)} ${oRecvY(v).toFixed(1)}`).join(' ')
  const fireLine = fired[sel].map((v, i) => `${i ? 'L' : 'M'}${ox(time_ns[i]).toFixed(1)} ${oFireY(v).toFixed(1)}`).join(' ')
  const curX = ox(time_ns[ti])

  // ---- per-pixel amplitude bars ----
  const BW = 560, BH = 150, BP = 28
  const ampMax = Math.max(1, ...pixels.map(p => p.amplitude))
  const barW = (BW - 2 * BP) / n_pixels

  // ---- scatter ----
  const SW = 360, SH = 200, SP = 34
  const fMax = Math.max(1, ...totals.map(t => t.f))
  const rMaxT = Math.max(1, ...totals.map(t => t.r))
  const scx = v => SP + (v / fMax) * (SW - 2 * SP)
  const scy = v => SH - SP - (v / rMaxT) * (SH - 2 * SP)

  // ---- SNR histogram ----
  const HW = 320, HH = 200, HP = 30
  const binMax = Math.max(1, ...bins)
  const binW = (HW - 2 * HP) / bins.length

  return (
    <div>
      {/* KPI summary */}
      <div className="stats" style={{ flexWrap: 'wrap', marginBottom: 14 }}>
        <KpiTile k="Alive pixels" v={summary.alive} unit={`/${n_pixels}`} accent="var(--brand)" />
        <KpiTile k="Dead pixels" v={summary.dead} unit={`/${n_pixels}`} accent="#f87171" />
        <KpiTile k="Mean SNR" v={summary.mean_snr} accent="var(--brand)" />
        <KpiTile k="Mean amplitude" v={Math.round(summary.mean_amplitude).toLocaleString()} unit=" codes" />
        <KpiTile k="Mean dip depth" v={summary.mean_dip_depth} />
        <KpiTile k="Σ photocurrent" v={Math.round(summary.total_photocurrent).toLocaleString()} unit=" codes" />
      </div>

      <div className="grid cols-2" style={{ alignItems: 'start' }}>
        {/* LEFT: animated 8×8 grid + scrubber */}
        <div>
          <div style={{ fontSize: 12.5, marginBottom: 6, color: 'var(--brand)' }}>
            Per-pixel received photocurrent · {seqLabel} · t = <span className="mono">{(time_ns[ti] / 1e3).toFixed(1)} µs</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 4, maxWidth: 420 }}>
            {pixels.map((p, i) => {
              const v = p.alive ? received[i][ti] / recvMax : 0
              const isSel = i === sel
              return (
                <div key={i}
                  onClick={() => setSel(i)}
                  title={`pixel ${i} (r${p.row} c${p.col}) · ${p.alive ? p.n_nv + ' NV · SNR ' + p.snr : 'dead'}`}
                  style={{
                    aspectRatio: '1 / 1',
                    position: 'relative',
                    cursor: 'pointer',
                    display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
                    fontSize: 9.5, fontFamily: 'var(--mono, monospace)',
                    color: p.alive ? 'rgba(0,0,0,0.55)' : 'var(--dim)',
                    background: p.alive
                      ? `rgba(52, 211, 153, ${0.16 + 0.84 * v})`
                      : 'rgba(255,255,255,0.04)',
                    border: isSel ? '2px solid var(--brand)' : '1px solid var(--border, rgba(255,255,255,0.1))',
                    borderRadius: 5,
                    boxShadow: isSel ? '0 0 0 2px rgba(52,211,153,0.25)' : 'none',
                    transition: 'background 80ms linear',
                    padding: 2,
                  }}>
                  {p.alive ? Math.round(received[i][ti]) : '·'}
                </div>
              )
            })}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10, maxWidth: 420 }}>
            <button className="pill" onClick={() => setPlaying(p => !p)}
              style={{ padding: '4px 12px', fontSize: 12.5 }}>
              {playing ? '⏸ Pause' : '▶ Play'}
            </button>
            <input type="range" min={0} max={n_points - 1} value={ti}
              onChange={e => { setPlaying(false); setTi(+e.target.value) }}
              style={{ flex: 1 }} />
          </div>
          <div className="muted" style={{ fontSize: 11.5, marginTop: 6, maxWidth: 420 }}>
            Click a pixel to inspect its fired-vs-received overlay →. Green intensity = instantaneous AD7616 code; dead pixels (no diamond) stay dark.
          </div>
        </div>

        {/* RIGHT: selected-pixel fired vs received overlay */}
        <div className="card" style={{ padding: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
            <strong>Pixel {sel} (r{selPx.row} c{selPx.col}) · {selPx.alive ? `${selPx.n_nv} NV` : 'dead'}</strong>
            <span className="muted" style={{ fontSize: 11.5 }}>{data.source}</span>
          </div>
          <div className="chart-wrap">
            <svg className="chart" viewBox={`0 0 ${OW} ${OH}`} preserveAspectRatio="none">
              {[0, .25, .5, .75, 1].map((f, i) => {
                const y = OP + f * (OH - 2 * OP)
                return <line key={i} className="gridline" x1={OP} y1={y} x2={OW - OP} y2={y} />
              })}
              <line className="axis" x1={OP} y1={OH - OP} x2={OW - OP} y2={OH - OP} />
              <line className="axis" x1={OP} y1={OP} x2={OP} y2={OH - OP} />
              {/* fired drive (step), behind */}
              <path d={fireLine} fill="none" stroke="#f59e0b" strokeWidth="1.6" opacity="0.85" />
              {/* received photocurrent (front) */}
              <path d={recvLine} fill="none" stroke="url(#slrecv)" strokeWidth="2" />
              {/* time cursor */}
              <line x1={curX} y1={OP} x2={curX} y2={OH - OP} stroke="var(--brand)" strokeWidth="1" opacity="0.5" strokeDasharray="3 3" />
              <defs>
                <linearGradient id="slrecv" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0" stopColor="#34d399" />
                  <stop offset="1" stopColor="#22d3ee" />
                </linearGradient>
              </defs>
              <text className="axtext" x={6} y={OH / 2} transform={`rotate(-90 12 ${OH / 2})`}>recv (codes)</text>
              <text className="axtext" x={OW - 6} y={OH / 2} transform={`rotate(90 ${OW - 12} ${OH / 2})`}>fired (0..1)</text>
              <text className="axtext" x={OW / 2} y={OH - 4} textAnchor="middle">time (ns)</text>
            </svg>
          </div>
          {/* selected-pixel KPIs */}
          <div className="stats" style={{ flexWrap: 'wrap', marginTop: 10, gap: 8 }}>
            <KpiTile k="Amplitude" v={Math.round(selPx.amplitude).toLocaleString()} unit=" codes" />
            <KpiTile k="SNR" v={selPx.snr} accent="var(--brand)" />
            <KpiTile k="Dip depth" v={selPx.dip_depth} />
            <KpiTile k="On/off" v={selPx.on_off_ratio} />
            <KpiTile k="Noise σ" v={Math.round(selPx.noise_floor)} unit=" codes" />
            <KpiTile k="Lag" v={selPx.phase_lag_ns} unit=" ns" />
          </div>
          <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>
            <span style={{ color: '#f59e0b' }}>━ fired</span> (CQD pump gate, DRIVE_PATTERN) · <span style={{ color: '#34d399' }}>━ received</span> (graphene/MoS₂ → TIA → AD7616 photocurrent). The received trace follows the {seqLabel.toLowerCase()} with the pixel's NV-population amplitude.
          </div>
        </div>
      </div>

      {/* array-level graphs */}
      <div className="grid cols-2" style={{ marginTop: 16, alignItems: 'start' }}>
        {/* per-pixel amplitude bars */}
        <div className="card" style={{ padding: 12 }}>
          <div style={{ fontSize: 12.5, marginBottom: 6, color: 'var(--brand)' }}>Per-pixel received amplitude (codes)</div>
          <svg viewBox={`0 0 ${BW} ${BH}`} preserveAspectRatio="none" style={{ width: '100%', height: BH }}>
            <line className="axis" x1={BP} y1={BH - BP} x2={BW - BP} y2={BH - BP} />
            <line className="axis" x1={BP} y1={BP} x2={BP} y2={BH - BP} />
            {pixels.map((p, i) => {
              const h = (p.amplitude / ampMax) * (BH - 2 * BP)
              const x = BP + i * barW
              return <rect key={i} x={x} y={BH - BP - h} width={Math.max(1, barW - 1)} height={h}
                fill={p.alive ? '#34d399' : '#475569'} opacity={i === sel ? 1 : 0.55} />
            })}
            <text className="axtext" x={BW / 2} y={BH - 2} textAnchor="middle">pixel index 0..63</text>
          </svg>
          <div className="muted" style={{ fontSize: 11.5, marginTop: 4 }}>Brighter pixels (larger microdiamond aggregate → more NV) swing higher; dead pixels sit at the noise floor.</div>
        </div>

        {/* SNR histogram */}
        <div className="card" style={{ padding: 12 }}>
          <div style={{ fontSize: 12.5, marginBottom: 6, color: 'var(--brand)' }}>SNR distribution (64 pixels)</div>
          <svg viewBox={`0 0 ${HW} ${HH}`} preserveAspectRatio="none" style={{ width: '100%', height: HH }}>
            <line className="axis" x1={HP} y1={HH - HP} x2={HW - HP} y2={HH - HP} />
            <line className="axis" x1={HP} y1={HP} x2={HP} y2={HH - HP} />
            {bins.map((b, i) => {
              const h = (b / binMax) * (HH - 2 * HP)
              const x = HP + i * binW
              return <g key={i}>
                <rect x={x} y={HH - HP - h} width={Math.max(1, binW - 2)} height={h} fill="#22d3ee" opacity="0.7" />
                <text className="axtext" x={x + binW / 2} y={HH - HP + 12} textAnchor="middle" fontSize="9">{i === bins.length - 1 ? sMax.toFixed(0) : ''}</text>
              </g>
            })}
            <text className="axtext" x={HW / 2} y={HH - 2} textAnchor="middle">SNR</text>
          </svg>
          <div className="muted" style={{ fontSize: 11.5, marginTop: 4 }}>Mean SNR ≈ {summary.mean_snr}; the spread reflects per-pixel NV-population + shot-noise variation.</div>
        </div>

        {/* fired-vs-received scatter */}
        <div className="card" style={{ padding: 12 }}>
          <div style={{ fontSize: 12.5, marginBottom: 6, color: 'var(--brand)' }}>Fired vs received energy (per pixel)</div>
          <svg viewBox={`0 0 ${SW} ${SH}`} preserveAspectRatio="none" style={{ width: '100%', height: SH }}>
            <line className="axis" x1={SP} y1={SH - SP} x2={SW - SP} y2={SH - SP} />
            <line className="axis" x1={SP} y1={SP} x2={SP} y2={SH - SP} />
            {totals.map((t, i) => (
              <circle key={i} cx={scx(t.f)} cy={scy(t.r)} r={i === sel ? 4.5 : 3}
                fill={pixels[i].alive ? '#34d399' : '#f87171'} opacity={i === sel ? 1 : 0.6}
                stroke={i === sel ? 'var(--brand)' : 'none'} onClick={() => setSel(i)} style={{ cursor: 'pointer' }} />
            ))}
            <text className="axtext" x={SW / 2} y={SH - 2} textAnchor="middle">Σ fired (drive)</text>
            <text className="axtext" x={8} y={SH / 2} transform={`rotate(-90 12 ${SH / 2})`}>Σ received (codes)</text>
          </svg>
          <div className="muted" style={{ fontSize: 11.5, marginTop: 4 }}>Each dot is a pixel; alive (green) cluster above the dead (red) noise-floor cluster.</div>
        </div>

        {/* dead-pixel map */}
        <div className="card" style={{ padding: 12 }}>
          <div style={{ fontSize: 12.5, marginBottom: 6, color: 'var(--brand)' }}>Array health map ({summary.alive} alive · {summary.dead} dead)</div>
          <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 4, maxWidth: 300 }}>
            {pixels.map((p, i) => (
              <div key={i} title={`pixel ${i}: ${p.alive ? 'alive' : 'no diamond detected'}`}
                style={{
                  aspectRatio: '1 / 1', display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 10, fontFamily: 'var(--mono, monospace)',
                  color: p.alive ? 'rgba(0,0,0,0.6)' : '#fca5a5',
                  background: p.alive ? 'rgba(52,211,153,0.55)' : 'rgba(248,113,113,0.25)',
                  border: '1px solid var(--border, rgba(255,255,255,0.1))', borderRadius: 4,
                }}>{p.alive ? '' : '✕'}</div>
            ))}
          </div>
          <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>Green = detector saw a microdiamond (armed + read); red ✕ = empty hole (stage-1 scan below threshold).</div>
        </div>
      </div>
    </div>
  )
}

// which is shown in a modal -- the map only exists once the calibration has
// actually swept each pin's ODMR dip, never before.
// ---------------------------------------------------------------------------
// 8×8 per-pixel qubit-count heatmap for the calibration result. Each cell is
// one chip pixel (row-major, qubit_id 0..63); the fill intensity encodes the
// MEASURED qubit count for that pixel (0 = no diamond detected -> dark cell).
function QubitPixelGrid({ counts, cols = 8 }) {
  const max = Math.max(1, ...counts)
  const rows = Math.ceil(counts.length / cols)
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 12.5, marginBottom: 6, color: 'var(--brand)' }}>
        Per-pixel qubit scan ({counts.length} pixels · {cols}×{rows} grid)
      </div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${cols}, 1fr)`,
        gap: 3,
        maxWidth: 360,
      }}>
        {counts.map((n, i) => {
          const f = n / max
          const empty = n === 0
          return (
            <div key={i} title={`pixel ${i} (r${Math.floor(i / cols)} c${i % cols}): ${n} qubit${n === 1 ? '' : 's'}`}
              style={{
                aspectRatio: '1 / 1',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontFamily: 'var(--mono, monospace)',
                color: empty ? 'var(--muted)' : 'var(--bg, #061018)',
                background: empty
                  ? 'var(--surface-2, rgba(255,255,255,0.04))'
                  : `rgba(44, 138, 74, ${0.25 + 0.75 * f})`,
                border: '1px solid var(--border, rgba(255,255,255,0.1))',
                borderRadius: 4,
              }}>
              {n}
            </div>
          )
        })}
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
        0 = no diamond detected · green intensity ∝ qubits resolved at the applied gradient
      </div>
    </div>
  )
}

export function CalibrationPanel({ run, running }) {
  // MCP4921 DAC code (0..4095); 1803 ≈ 2.2 A addressing gradient
  const [gradCode, setGradCode] = useState(1803)
  const gradOn = gradCode > 0
  // DAC code -> coil current (I = 5V * code/4096 / 1Ω) and spread (0.98 MHz/µm at 2.2 A)
  const coilA = gradOn ? (5.0 * gradCode / 4096) : 0
  const spreadMhz = gradOn ? (coilA / 2.2) * 0.98 * 200 : 0
  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'flex-end', marginBottom: 8 }}>
        <label className="muted" style={{ fontSize: 12.5 }}>
          Gradient DAC code (0..4095)
          <input type="range" min="0" max="4095" step="1" value={gradCode}
            onChange={e => setGradCode(+e.target.value)} style={{ display: 'block', width: 220 }} />
          <span className="mono" style={{ color: gradOn ? 'var(--brand)' : 'var(--muted)' }}>
            {gradCode} {gradOn ? `· ${coilA.toFixed(2)} A · ~${spreadMhz.toFixed(0)} MHz spread` : '· gradient off (ensemble)'}
          </span>
        </label>
        <button onClick={() => run({ gradient_code: gradCode })}
          disabled={running} className="ghost" type="button">
          {running ? <><span className="spinner" /> Calibrating…</> : 'Run calibration'}
        </button>
      </div>
    </div>
  )
}

// Single-pixel calibration results — measured register width, the 4-stage
// breakdown, and the per-pin resonance-map modal. Right-hand card of the
// spectroscopy grid; App.jsx renders the ODMR spectrum card directly below it.
// The form (CalibrationPanel) is the left-hand card.
export function CalibrationResults({ result, err }) {
  const [resOpen, setResOpen] = useState(false)
  const perPin = result && result.per_pin
  const dFit = result && result.d_fit_ghz
  const qsum = result && result.qubit_summary
  return (
    <div className="card">
      <h2><span className="dot" />Calibration results</h2>
      {err && <div className="error" style={{ marginTop: 10 }}>{err}</div>}
      {!result && !err && (
        <div className="empty">Run the calibration to see the measured register width and per-pin resonance map.</div>
      )}
      {qsum && (
        <div className="qubit-summary" style={{
          marginTop: 10, padding: '10px 12px', borderRadius: 8,
          background: 'var(--surface-2, rgba(255,255,255,0.04))',
          border: '1px solid var(--border, rgba(255,255,255,0.1))',
        }}>
          <div style={{ fontSize: 13, marginBottom: 6, color: 'var(--brand)' }}>
            Qubits found (measured from the ODMR spectrum{qsum.gradient_applied ? ` at gradient ${qsum.gradient_code}` : ', no gradient'})
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 13 }}>
            <span><b className="mono" style={{ fontSize: 18, color: 'var(--ok, #2c8a4a)' }}>{result.register_width}</b> qubits/pixel (register width)</span>
            <span>avg <b className="mono">{qsum.avg_qubits_per_pixel}</b>/pixel</span>
            <span><b className="mono" style={{ fontSize: 22, color: 'var(--brand)', textShadow: '0 0 12px rgba(34,211,238,0.45)' }}>{qsum.total_qubits}</b> total across {qsum.n_pixels} pixels</span>
            <span>mode: <span className={`tag ${qsum.pixel_mode === 'register' ? 'ok' : 'bad'}`}>{qsum.pixel_mode}</span></span>
            {qsum.gradient_applied && (
              <span className="muted">coil {qsum.coil_current_a} A · spread {qsum.gradient_spread_mhz} MHz</span>
            )}
          </div>
          {Array.isArray(qsum.qubits_per_pixel) && qsum.qubits_per_pixel.length > 0 && (
            <QubitPixelGrid counts={qsum.qubits_per_pixel} cols={8} />
          )}
        </div>
      )}
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
                {i === 3 && (
                  <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>
                    pins measured:{' '}
                    <span className="mono" style={{ color: 'var(--ok)' }}>{st.pins_measured}</span>
                    {' · '}detuning spread {st.detuning_spread_mhz} MHz
                    {' · '}fit σ {st.meas_sigma_mhz} MHz
                    {' · '}measured T₂ {st.t2_mean_us} µs / T₂* {st.t2_star_mean_us} µs
                    {st.source && st.source !== 'simulated' && <span style={{
                      color: 'var(--warn, #b8860b)' }}>
                      {' · '}T₂/T₂* {st.source}</span>}
                  </div>
                )}
                {i === 2 && st.register_width != null && (
                  <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
                    register width N ={' '}
                    <span className="mono" style={{ color: 'var(--brand)' }}>{st.register_width}</span>
                    {' '}· {st.register_width <= 1
                      ? <span style={{ color: 'var(--warn, #b8860b)' }}>ensemble (bulk → 1 line)</span>
                      : <span style={{ color: 'var(--ok)' }}>{st.register_width} resolved lines → register</span>}
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            At {result.temperature_k} K → T₁ {result.t1_ms} ms · T₂ {result.t2_us} µs
            {result.source && <span> · <span className="mono" style={{
              color: result.source === 'hardware' ? 'var(--ok, #2c8a4a)' : 'var(--brand)' }}>
              {result.source === 'hardware' ? 'hardware I/O' : 'simulator'}
            </span></span>}
          </div>
          {result.register_width != null && (
            <div style={{ marginTop: 10, padding: '8px 10px',
              border: '1px solid var(--border)', borderRadius: 8,
              background: 'var(--surface, rgba(0,0,0,0.02))' }}>
              <span className="muted" style={{ fontSize: 11.5 }}>Measured register width</span>{' '}
              <span className="mono" style={{ color: 'var(--brand)', fontWeight: 600 }}>
                N = {result.register_width}
              </span>
              {' · '}
              <span className="mono" style={{ fontSize: 12 }}>
                {result.pixel_mode_recommendation === 'ensemble'
                  ? '→ ensemble mode (bulk ensemble → 1 line per pixel)'
                  : `→ register mode (${result.register_width} resolved ODMR lines)`}
              </span>
              <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                Applied to the pixel-mode selector above{result.line_freqs_hz
                  ? ` · ${result.line_freqs_hz.length} line centres sent to the backend`
                  : ''}.
              </div>
            </div>
          )}
          {perPin && perPin.length > 0 && (
            <button type="button" className="ghost" style={{ marginTop: 12 }}
              onClick={() => setResOpen(true)}>
              View per-pin resonance map ({perPin.length} pins)
            </button>
          )}
        </div>
      )}
      {resOpen && (
        <div className="resonance-overlay" onClick={() => setResOpen(false)}>
          <div className="resonance-modal" onClick={(e) => e.stopPropagation()}>
            <button className="meas-help-close" type="button"
              onClick={() => setResOpen(false)} aria-label="Close">×</button>
            <h2><span className="dot" />Per-pin resonance map (calibrated)</h2>
            <QubitFrequencyTable sites={perPin} dGhz={dFit} />
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Verdict — the physical-feasibility assessment of the latest run.
// ---------------------------------------------------------------------------
export function Verdict({ assessment }) {
  if (!assessment) return null
  const ok = assessment.within_coherence
  return (
    <div className={`verdict ${ok ? 'ok' : 'bad'}`}>
      <div className="verdict-h">
        <span className="verdict-tag">Coherence budget</span>
        {ok ? '✓ Within coherence window' : '✗ Exceeds coherence budget'}
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
// NoiseBudget — the per-run noise budget (base readout error, prime SNR
// factor, effective readout error after the coprime denoiser). Lets the user
// compare prime readout ON vs OFF on the same circuit: with prime on the
// effective readout error is the base error divided by the SNR factor.
// ---------------------------------------------------------------------------
export function NoiseBudget({ noise }) {
  if (!noise) return null
  const pct = v => (v * 100).toFixed(2) + '%'
  const on = noise.prime_readout
  return (
    <div className={`verdict ${on ? 'ok' : ''}`} style={{ marginTop: 12 }}>
      <div className="verdict-h">
        <span className="verdict-tag">Noise budget</span>
        {on
          ? `Prime denoise ON — ${noise.prime_snr_factor}× readout SNR`
          : 'Prime denoise OFF'}
      </div>
      <div className="verdict-body">
        Effective readout error <b className="mono" style={{ color: 'var(--brand)' }}>{pct(noise.effective_readout_error)}</b>
        {on && <> (base {pct(noise.readout_error)} ÷ {noise.prime_snr_factor}×)</>}
        {' — '}1q depol <b className="mono">{noise.depol_1q}</b>{' · '}
        2q depol <b className="mono">{noise.depol_2q}</b>{' · '}
        T₂ <b className="mono">{noise.t2_us} µs</b>
      </div>
      <div className="verdict-meta">
        <span>base readout <b className="mono">{pct(noise.readout_error)}</b></span>
        <span>effective readout <b className="mono">{pct(noise.effective_readout_error)}</b></span>
        <span>prime channels <b className="mono">{noise.n_symbols}</b> / {noise.n_noise_bins} bins</span>
        <span>SNR factor <b className="mono">{noise.prime_snr_factor}×</b></span>
        <span>mode <b className="mono">{noise.pixel_mode}</b></span>
      </div>
    </div>
  )
}
// ---------------------------------------------------------------------------
// CompassRose — ODMR compass: heading + elevation needle on a rose, with the
// 4 NV <111> axis doublet splits around it.  physics from /api/compass.
// ---------------------------------------------------------------------------
export function CompassRose({ data, error, loading }) {
  if (error) return <div className="error">{error}</div>
  if (loading) return <div className="muted"><span className="spinner" /> sweeping ODMR…</div>
  if (!data) return <div className="muted">Press “Measure” to sweep the 4 NV &lt;111&gt; axes.</div>
  const R = 92, cx = 110, cy = 110
  const heading = data.heading_deg || 0
  const elev = data.elevation_deg || 0
  const resolved = data.resolved_axes || 0
  // needle direction in the plane (heading), length scaled by elevation
  const rad = (heading * Math.PI) / 180
  const len = R * 0.78 * Math.cos((elev * Math.PI) / 180)
  const nx = cx + len * Math.sin(rad), ny = cy - len * Math.cos(rad)
  const bUv = (data.b_reconstructed_t || 0) * 1e6
  const ticks = [0, 45, 90, 135, 180, 225, 270, 315]
  // 4 axis doublet bars around the rose (one per NV <111> axis)
  const splits = data.splits_mhz || []
  const maxSplit = Math.max(1, ...splits)
  const axes = splits.map((s, i) => {
    const a = (i * 90 + 45) * Math.PI / 180
    const r0 = R + 8, r1 = r0 + 10 + 26 * (s / maxSplit)
    return { i, s, x1: cx + r0 * Math.cos(a), y1: cy + r0 * Math.sin(a),
             x2: cx + r1 * Math.cos(a), y2: cy + r1 * Math.sin(a) }
  })
  return (
    <div>
      <div className="chart-wrap" style={{ textAlign: 'center' }}>
        <svg viewBox="0 0 220 220" className="compass-rose" style={{ maxWidth: 240 }}>
          <defs>
            <radialGradient id="rose-bg" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#0c1430" /><stop offset="100%" stopColor="#070d22" />
            </radialGradient>
            <linearGradient id="needle" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#f472b6" /><stop offset="100%" stopColor="#22d3ee" />
            </linearGradient>
          </defs>
          <circle cx={cx} cy={cy} r={R} fill="url(#rose-bg)" stroke="#263056" strokeWidth="1.5" />
          <circle cx={cx} cy={cy} r={R * 0.62} fill="none" stroke="#1a2342" strokeWidth="0.8" />
          {ticks.map(deg => {
            const a = (deg * Math.PI) / 180
            const x1 = cx + (R - 8) * Math.sin(a), y1 = cy - (R - 8) * Math.cos(a)
            const x2 = cx + R * Math.sin(a), y2 = cy - R * Math.cos(a)
            const lx = cx + (R - 18) * Math.sin(a), ly = cy - (R - 18) * Math.cos(a)
            const lbl = deg === 0 ? 'N' : deg === 90 ? 'E' : deg === 180 ? 'S' : deg === 270 ? 'W' : `${deg}`
            return (
              <g key={deg}>
                <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#3a4a78" strokeWidth={deg % 90 === 0 ? 1.6 : 0.9} />
                <text x={lx} y={ly + 3} textAnchor="middle" className="axtext"
                  style={{ fill: deg % 90 === 0 ? 'var(--brand)' : 'var(--dim)', fontSize: deg % 90 === 0 ? 11 : 9 }}>{lbl}</text>
              </g>
            )
          })}
          {/* 4 NV <111> doublet bars */}
          {axes.map(ax => (
            <line key={ax.i} x1={ax.x1} y1={ax.y1} x2={ax.x2} y2={ax.y2}
              stroke={ax.s > (data.sensitivity_ut ? 2.5 : 2.5) ? 'var(--ok)' : '#3a4a78'}
              strokeWidth="2.5" strokeLinecap="round" opacity={ax.s > 0.3 ? 0.9 : 0.3} />
          ))}
          {/* needle */}
          <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="url(#needle)" strokeWidth="3" strokeLinecap="round" />
          <circle cx={nx} cy={ny} r="4" fill="#f472b6" />
          <circle cx={cx} cy={cy} r="3.5" fill="#22d3ee" />
        </svg>
      </div>
      <div className="stats" style={{ marginTop: 8 }}>
        <div className="stat"><div className="k">|B|</div><div className="v">{bUv.toFixed(1)}<small> µT</small></div></div>
        <div className="stat"><div className="k">Heading</div><div className="v">{heading.toFixed(1)}<small>°</small></div></div>
        <div className="stat"><div className="k">Elevation</div><div className="v">{elev.toFixed(1)}<small>°</small></div></div>
        <div className="stat"><div className="k">Axes resolved</div><div className="v">{resolved}<small>/4</small></div></div>
        <div className="stat"><div className="k">Sensitivity</div><div className="v">{(data.sensitivity_ut || 0).toFixed(2)}<small> µT</small></div></div>
        <div className="stat"><div className="k">Averages</div><div className="v">{data.n_avg || 0}</div></div>
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>{data.note}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// GyroPanel — Ramsey gyroscope: fringe trace + rate gauge vs Earth-rate.
// ---------------------------------------------------------------------------
export function GyroPanel({ data, error, loading }) {
  if (error) return <div className="error">{error}</div>
  if (loading) return <div className="muted"><span className="spinner" /> running Ramsey sequence…</div>
  if (!data) return <div className="muted">Press “Measure” to run the Ramsey π/2–T–π/2 sequence.</div>
  const tr = data.trace || {}
  const tUs = tr.time_us || [], sig = tr.signal || []
  const W = 320, H = 150, P = 28
  const tMax = Math.max(1, ...tUs), tMin = Math.min(0, ...tUs)
  const sx = i => P + (i / Math.max(1, sig.length - 1)) * (W - 2 * P)
  const sy = v => P + (1 - (v - 0) / 1.1) * (H - 2 * P)
  const path = sig.map((v, i) => `${i === 0 ? 'M' : 'L'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')
  const measured = data.measured_rate_dps || 0
  const noise = data.rate_noise_dps || 0
  const earth = 15 / 3600
  const grade = data.rate_grade
  // gauge: map rate onto a -90..90 deg arc, clipped to noise floor band
  const gR = 60, gcx = 80, gcy = 80
  const rateAng = Math.max(-90, Math.min(90, measured * 3))
  const garc = (Math.max(-90, Math.min(90, measured * 3)) + 90) * Math.PI / 180
  const gnx = gcx + gR * 0.8 * Math.sin((rateAng * Math.PI) / 180)
  const gny = gcy - gR * 0.8 * Math.cos((rateAng * Math.PI) / 180)
  return (
    <div>
      <div className="row" style={{ gap: 12, alignItems: 'stretch' }}>
        <svg viewBox="0 0 170 170" style={{ width: 150, flex: '0 0 150px' }}>
          <defs>
            <linearGradient id="gyro-needle" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={grade ? '#fbbf24' : '#34d399'} /><stop offset="100%" stopColor="#22d3ee" />
            </linearGradient>
          </defs>
          <path d={`M ${gcx - gR} ${gcy} A ${gR} ${gR} 0 0 1 ${gcx + gR} ${gcy}`} fill="none" stroke="#1a2342" strokeWidth="2" />
          <path d={`M ${gcx - gR * 0.8} ${gcy} A ${gR * 0.8} ${gR * 0.8} 0 0 1 ${gcx + gR * 0.8} ${gcy}`} fill="none" stroke="#263056" strokeWidth="0.8" />
          {/* earth-rate band (tiny, near 0) */}
          {(() => {
            const e0 = (Math.max(-90, Math.min(90, earth * 1e4 * 3)) + 90) * Math.PI / 180
            return <line x1={gcx + gR * 0.5 * Math.sin(e0)} y1={gcy - gR * 0.5 * Math.cos(e0)}
              x2={gcx + gR * 0.95 * Math.sin(e0)} y2={gcy - gR * 0.95 * Math.cos(e0)} stroke="#34d399" strokeWidth="1" opacity="0.5" />
          })()}
          <line x1={gcx} y1={gcy} x2={gnx} y2={gny} stroke="url(#gyro-needle)" strokeWidth="3" strokeLinecap="round" />
          <circle cx={gcx} cy={gcy} r="3.5" fill="#22d3ee" />
          <text x={gcx} y={gcy + 22} textAnchor="middle" className="axtext">0</text>
          <text x={gcx - gR - 4} y={gcy + 4} textAnchor="middle" className="axtext">−</text>
          <text x={gcx + gR + 4} y={gcy + 4} textAnchor="middle" className="axtext">+</text>
          <text x={gcx} y={gcy + 40} textAnchor="middle" className="axtext" style={{ fill: grade ? 'var(--warn)' : 'var(--ok)', fontSize: 11 }}>
            {grade ? 'RATE-GRADE' : 'INERTIAL'}
          </text>
        </svg>
        <div className="chart-wrap" style={{ flex: 1 }}>
          <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
            {[0, 0.25, 0.5, 0.75, 1].map((f, i) => (
              <line key={i} className="gridline" x1={P} y1={P + f * (H - 2 * P)} x2={W - P} y2={P + f * (H - 2 * P)} />
            ))}
            <line className="axis" x1={P} y1={H - P} x2={W - P} y2={H - P} />
            <line className="axis" x1={P} y1={P} x2={P} y2={H - P} />
            <path d={path} fill="none" stroke="var(--brand)" strokeWidth="1.5" />
            <text className="axtext" x={W / 2} y={H - 4} textAnchor="middle">free precession t (µs)</text>
            <text className="axtext" x={8} y={H / 2} transform={`rotate(-90 10 ${H / 2})`}>PL</text>
          </svg>
        </div>
      </div>
      <div className="stats" style={{ marginTop: 8 }}>
        <div className="stat"><div className="k">Applied rate</div><div className="v">{(data.rotation_rate_dps || 0).toFixed(2)}<small> °/s</small></div></div>
        <div className="stat"><div className="k">Measured</div><div className="v">{measured.toFixed(2)}<small> °/s</small></div></div>
        <div className="stat"><div className="k">Noise floor</div><div className="v">{noise.toExponential(1)}<small> °/s</small></div></div>
        <div className="stat"><div className="k">T₂ window</div><div className="v">{(data.t2_us || 0).toFixed(1)}<small> µs</small></div></div>
        <div className="stat"><div className="k">Free prec.</div><div className="v">{(data.free_precession_us || 0).toFixed(1)}<small> µs</small></div></div>
        <div className="stat"><div className="k">Phase noise</div><div className="v">{(data.phase_noise_rad || 0).toFixed(3)}<small> rad</small></div></div>
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>{data.note}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// MagnetoImage — 8x8 per-pixel |B·n| heatmap (the magneto-optic image) + the
// representative ODMR dip.  physics from /api/magneto.
// ---------------------------------------------------------------------------
export function MagnetoImage({ data, error, loading }) {
  if (error) return <div className="error">{error}</div>
  if (loading) return <div className="muted"><span className="spinner" /> reading 64-pixel array…</div>
  if (!data) return <div className="muted">Press “Image” to read the 64-pixel magneto-optic array.</div>
  const px = data.pixels || []
  const bMax = Math.max(1e-6, ...px.map(p => p.b_proj_ut))
  // heatmap color: low = deep indigo, high = cyan->pink
  const heat = v => {
    const f = Math.min(1, Math.max(0, v / bMax))
    // indigo (99,102,241) -> cyan (34,211,238) -> pink (244,114,182)
    if (f < 0.5) {
      const t = f / 0.5
      const r = Math.round(99 + (34 - 99) * t), g = Math.round(102 + (211 - 102) * t), b = Math.round(241 + (238 - 241) * t)
      return `rgb(${r},${g},${b})`
    }
    const t = (f - 0.5) / 0.5
    const r = Math.round(34 + (244 - 34) * t), g = Math.round(211 + (114 - 211) * t), b = Math.round(238 + (182 - 238) * t)
    return `rgb(${r},${g},${b})`
  }
  const dip = data.dip_curve || {}
  const fG = dip.frequencies_ghz || [], ct = dip.contrast || []
  const W = 240, H = 130, P = 26
  const fMin = Math.min(...fG), fMax = Math.max(...fG)
  const cMin = Math.min(...ct), cMax = Math.max(...ct)
  const dx = i => P + (i / Math.max(1, ct.length - 1)) * (W - 2 * P)
  const dy = v => P + (1 - (v - cMin) / Math.max(1e-9, cMax - cMin)) * (H - 2 * P)
  const dpath = ct.map((v, i) => `${i === 0 ? 'M' : 'L'}${dx(i).toFixed(1)},${dy(v).toFixed(1)}`).join(' ')
  return (
    <div>
      <div className="row" style={{ gap: 12, alignItems: 'flex-start' }}>
        <div style={{ flex: '0 0 auto' }}>
          <div className="magneto-grid">
            {px.map(p => (
              <div key={p.pixel} className="magneto-cell" style={{ background: heat(p.b_proj_ut) }}
                title={`pixel (${p.x},${p.y}) · ${p.b_proj_ut.toFixed(2)} µT · split ${p.split_mhz.toFixed(2)} MHz`} />
            ))}
          </div>
          <div className="magneto-bar">
            <span>0</span>
            <div className="magneto-bar-fill" />
            <span>{bMax.toFixed(0)} µT</span>
          </div>
        </div>
        <div className="chart-wrap" style={{ flex: 1 }}>
          <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
            {[0, 0.5, 1].map((f, i) => (
              <line key={i} className="gridline" x1={P} y1={P + f * (H - 2 * P)} x2={W - P} y2={P + f * (H - 2 * P)} />
            ))}
            <line className="axis" x1={P} y1={H - P} x2={W - P} y2={H - P} />
            <line className="axis" x1={P} y1={P} x2={P} y2={H - P} />
            {dpath && <path d={dpath} fill="none" stroke="var(--accent)" strokeWidth="1.5" />}
            <text className="axtext" x={W / 2} y={H - 4} textAnchor="middle">freq (GHz)</text>
            <text className="axtext" x={8} y={H / 2} transform={`rotate(-90 10 ${H / 2})`}>PL dip</text>
          </svg>
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>Centre-pixel ODMR dip (D ± γB doublet).</div>
        </div>
      </div>
      <div className="stats" style={{ marginTop: 8 }}>
        <div className="stat"><div className="k">Pixels</div><div className="v">{data.n_pixels}</div></div>
        <div className="stat"><div className="k">Grid</div><div className="v">{data.grid}×{data.grid}</div></div>
        <div className="stat"><div className="k">Peak |B·n|</div><div className="v">{bMax.toFixed(1)}<small> µT</small></div></div>
        <div className="stat"><div className="k">Sensitivity</div><div className="v">{(data.sensitivity_ut || 0).toFixed(2)}<small> µT</small></div></div>
        <div className="stat"><div className="k">Linewidth</div><div className="v">{(data.linewidth_mhz || 0).toFixed(1)}<small> MHz</small></div></div>
        <div className="stat"><div className="k">Averages</div><div className="v">{data.n_avg}</div></div>
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>{data.note}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SensorField — generic N×N heatmap for the contactless field-imaging sensors
// (ferromagnetic / geomagnetic nav / temperature / current). Reusable: takes a
// grid of cells + vmin/vmax + unit + stats + optional position markers.
// ---------------------------------------------------------------------------
export function SensorField({ data, error, loading, promptText }) {
  if (error) return <div className="error">{error}</div>
  if (loading) return <div className="muted"><span className="spinner" /> reading array…</div>
  if (!data) return <div className="muted">{promptText || 'Press Measure to read the array.'}</div>
  const grid = data.grid || 8
  const cells = data.cells || []
  const vmin = data.vmin ?? 0, vmax = data.vmax ?? 1
  const span = Math.max(1e-9, vmax - vmin)
  const unit = data.unit || ''
  const heat = v => {
    const f = Math.min(1, Math.max(0, (v - vmin) / span))
    if (f < 0.5) {
      const t = f / 0.5
      const r = Math.round(99 + (34 - 99) * t), g = Math.round(102 + (211 - 102) * t), b = Math.round(241 + (238 - 241) * t)
      return `rgb(${r},${g},${b})`
    }
    const t = (f - 0.5) / 0.5
    const r = Math.round(34 + (244 - 34) * t), g = Math.round(211 + (114 - 211) * t), b = Math.round(238 + (182 - 238) * t)
    return `rgb(${r},${g},${b})`
  }
  const markers = data.markers || []
  const stats = data.stats || []
  return (
    <div>
      <div className="row" style={{ gap: 12, alignItems: 'flex-start' }}>
        <div style={{ flex: '0 0 auto' }}>
          <div style={{ position: 'relative', width: 188, height: 188 }}>
            <div className="field-grid" style={{ gridTemplateColumns: `repeat(${grid}, 1fr)`, width: '100%', height: '100%' }}>
              {cells.map(c => (
                <div key={`${c.x}-${c.y}`} className="field-cell" style={{ background: heat(c.value) }}
                  title={`(${c.x},${c.y}) · ${c.value.toFixed(2)} ${unit}`} />
              ))}
            </div>
            {markers.map((m, i) => (
              <div key={i} className={`field-marker ${m.kind}`}
                title={m.kind === 'true' ? 'true position' : 'matched position'}
                style={{ left: `${((m.x + 0.5) / grid) * 100}%`, top: `${((m.y + 0.5) / grid) * 100}%` }} />
            ))}
          </div>
          <div className="magneto-bar">
            <span>{vmin.toFixed(2)}</span>
            <div className="magneto-bar-fill" />
            <span>{vmax.toFixed(2)} {unit}</span>
          </div>
        </div>
        {markers.length > 0 && (
          <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.7 }}>
            <span className="field-marker-legend true" /> true position<br />
            <span className="field-marker-legend matched" /> matched position
          </div>
        )}
      </div>
      <div className="stats" style={{ marginTop: 8 }}>
        {stats.map((s, i) => (
          <div className="stat" key={i}><div className="k">{s.k}</div>
            <div className="v">{s.v}{s.unit ? <small> {s.unit}</small> : null}</div></div>
        ))}
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>{data.note}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// TensorPanel — 64-pixel tensor gradiometer. Shows the recovered
// |B| image (the dipole perturbation after baseline subtraction) with the
// FITTED dipole position marker, plus the 3x2 per-pixel Jacobian summary and
// the point-dipole source fit (position + moment + residual).
// ---------------------------------------------------------------------------
export function TensorPanel({ data, error, loading }) {
  if (error) return <div className="error">{error}</div>
  if (loading) return <div className="muted"><span className="spinner" /> sweeping tensor…</div>
  if (!data) return <div className="muted">Press “Image tensor” to run the 64-pixel tensor gradiometer + dipole fit.</div>
  const grid = data.grid || 8
  const img = data.b_mag_image_ut || []
  const vmin = data.b_mag_min_ut ?? 0, vmax = Math.max(data.b_mag_max_ut ?? 1, 1e-6)
  const span = Math.max(1e-9, vmax - vmin)
  const heat = v => {
    const f = Math.min(1, Math.max(0, (v - vmin) / span))
    if (f < 0.5) { const t = f / 0.5
      return `rgb(${Math.round(99 + (34 - 99) * t)},${Math.round(102 + (211 - 102) * t)},${Math.round(241 + (238 - 241) * t)})` }
    const t = (f - 0.5) / 0.5
    return `rgb(${Math.round(34 + (244 - 34) * t)},${Math.round(211 + (114 - 211) * t)},${Math.round(238 + (182 - 238) * t)})`
  }
  const pitch = data.pitch_mm || 2.5
  const d = data.dipole
  let mx = null, my = null
  if (d) {
    mx = (d.dipole_x_mm / pitch + (grid - 1) / 2.0 + 0.5) / grid
    my = (d.dipole_y_mm / pitch + (grid - 1) / 2.0 + 0.5) / grid
  }
  const fmt = v => (v == null ? '—' : (Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(3)))
  const stats = [
    { k: 'Pixels > floor', v: `${data.n_resolved_pixels}/${grid * grid}` },
    { k: 'Sensitivity', v: fmt(data.sensitivity_ut), unit: 'µT' },
    { k: 'Mode', v: `${data.mode}${data.differential ? '+diff' : ''}` },
    { k: 'Averages', v: String(data.n_avg) },
    { k: 'Linewidth', v: fmt(data.linewidth_mhz), unit: 'MHz' },
    { k: '|B| peak', v: fmt(data.b_mag_max_ut), unit: 'µT' },
  ]
  return (
    <div>
      <div className="row" style={{ gap: 12, alignItems: 'flex-start' }}>
        <div style={{ flex: '0 0 auto' }}>
          <div style={{ position: 'relative', width: 188, height: 188 }}>
            <div className="field-grid" style={{ gridTemplateColumns: `repeat(${grid}, 1fr)`, width: '100%', height: '100%' }}>
              {img.map((v, i) => {
                const x = i % grid, y = Math.floor(i / grid)
                return <div key={i} className="field-cell" style={{ background: heat(v) }}
                  title={`(${x},${y}) · ${v.toFixed(2)} µT`} />
              })}
            </div>
            {d && mx > 0 && mx < 1 && my > 0 && my < 1 && (
              <div className="field-marker true" title={`fitted dipole (${fmt(d.dipole_x_mm)},${fmt(d.dipole_y_mm)},${fmt(d.dipole_z_mm)}) mm`}
                style={{ left: `${mx * 100}%`, top: `${my * 100}%` }} />
            )}
          </div>
          <div className="magneto-bar">
            <span>{vmin.toFixed(2)}</span>
            <div className="magneto-bar-fill" />
            <span>{vmax.toFixed(2)} µT</span>
          </div>
        </div>
        <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.7 }}>
          <span className="field-marker-legend true" /> fitted dipole source<br />
          {d ? (<>pos <b>{fmt(d.dipole_x_mm)}, {fmt(d.dipole_y_mm)}, {fmt(d.dipole_z_mm)}</b> mm<br />
            moment <b>[{d.dipole_moment_am2.map(fmt).join(', ')}]</b> A·m²<br />
            residual <b>{fmt(d.fit_residual_mhz)}</b> MHz</>)
            : 'no dipole above the noise floor'}
        </div>
      </div>
      <div className="stats" style={{ marginTop: 8 }}>
        {stats.map((s, i) => (
          <div className="stat" key={i}><div className="k">{s.k}</div>
            <div className="v">{s.v}{s.unit ? <small> {s.unit}</small> : null}</div></div>
        ))}
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>{data.note}</div>
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

// ---- 64-parallel computing (Invention 4) + per-pixel array calibration -----
// Two cards: (1) calibrate the 64-pixel array's measured register width
// (Invention 4+5 — gradient makes N ODMR lines resolvable per pixel;
// source=sim synthesizes spectra, source=real drives RUN_ALL_ODMR), and
// (2) run one circuit per pixel across the array and aggregate. Calibrate
// first, then Run with use_calibration on so each pixel runs at its own
// measured width (narrow pixels are SKIPPED, never truncated).
function _ParPixelGrid({ widths, cols = 8 }) {
  const max = Math.max(1, ...(widths || []))
  const n = (widths || []).length
  if (!n) return null
  return (
    <div>
      <div style={{ fontSize: 12.5, marginBottom: 6, color: 'var(--brand)' }}>
        Per-pixel register width ({n} pixels · {cols}×{Math.ceil(n / cols)} grid)
      </div>
      <div style={{
        display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`,
        gap: 3, maxWidth: 360,
      }}>
        {(widths || []).map((w, i) => {
          const f = w / max
          const empty = w === 0
          return (
            <div key={i} title={`pixel ${i}: width ${w}`}
              style={{
                aspectRatio: '1 / 1',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontFamily: 'var(--mono, monospace)',
                color: empty ? 'var(--muted)' : 'var(--bg, #061018)',
                background: empty
                  ? 'var(--surface-2, rgba(255,255,255,0.04))'
                  : `rgba(34, 211, 238, ${0.22 + 0.78 * f})`,
                border: '1px solid var(--border, rgba(255,255,255,0.1))',
                borderRadius: 4,
              }}>{w}</div>
          )
        })}
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
        0 = no NV detected · cyan intensity ∝ measured register width
      </div>
    </div>
  )
}

// 64-parallel computing (Invention 4+5). The single ParallelPanel was split
// into two independent cards so they can drop into the SAME UI slots the
// single-pixel path uses — the run card replaces "Run a quantum algorithm" and
// the calibrate card replaces "Calibration process" when the 64-parallel
// switch is ON. Block positions stay identical between the two modes (the user
// insisted the layout must not move, only the minimal 64 content swaps in).
// Shared state (gradient, pixel size, mode, pixel count …) is lifted to
// App.jsx and passed to both cards so a calibrate + a run stay consistent.

// A labelled range slider with a live numeric readout and min/max end labels.
// Used for every physically-bounded calibration parameter so the user drags
// within the possible range instead of typing a number the hardware can't
// deliver. `fmt` controls how the value + the two end labels render.
function RangeSlider({ label, value, min, max, step, onChange, disabled, unit, fmt }) {
  const f = fmt || ((v) => v)
  return (
    <div className="adv-slider" style={disabled ? { opacity: .45 } : undefined}>
      <div className="adv-slider-head">
        <span className="adv-slider-label">{label}</span>
        <span className="adv-slider-val">{f(value)}{unit ? ` ${unit}` : ''}</span>
      </div>
      <input className="adv-slider-bar" type="range" min={min} max={max} step={step}
        value={value} disabled={disabled}
        onChange={(e) => onChange(+e.target.value)} />
      <div className="adv-slider-mm">
        <span>{f(min)}</span><span>{f(max)}</span>
      </div>
    </div>
  )
}

export function ParallelCalibCard({
  grad, setGrad, pixelSize, setPixelSize, source,
  profile, temperature, seed,
  onCalibrate, calibRunning,
  antiNoisePresets,
}) {
  // Resolution + readout knobs that change the per-pixel qubit count and the
  // frequencies the calibration measures. Local state (only this card sends
  // them). Defaults reproduce the prior 1-shot / no-resolution behaviour so
  // nothing changes until the user opens the Advanced section and turns one on.
  const [advOpen, setAdvOpen] = useState(false)
  const [integrationShots, setIntegrationShots] = useState(1)        // lock-in averaging
  const [subPixel, setSubPixel] = useState(false)                   // sub-pixel centroiding
  const [sigmaSheet, setSigmaSheet] = useState(1.0)                 // charge-sharing sheet spread
  const [superresOn, setSuperresOn] = useState(false)               // super-resolution ODMR
  const [superresFactor, setSuperresFactor] = useState(0.6)         // OMP acceptance fraction
  const [bayesianOn, setBayesianOn] = useState(false)               // Bayesian readout
  const [bayesianConf, setBayesianConf] = useState(0.95)            // credible level
  const [noiseEnv, setNoiseEnv] = useState('none')                  // environmental noise model
  const [antiNoisePreset, setAntiNoisePreset] = useState('off')     // anti-noise filter stack preset
  const [maxNv, setMaxNv] = useState(300)                           // NVs per microdiamond
  const [nPoints, setNPoints] = useState(400)                       // sweep points (real path)
  // On-board gradient coil: MCP4921 12-bit DAC, I_coil = 5 V * code/4096 / 1 Ω,
  // gradient = (I/2.2 A) * 0.98 MHz/µm. Full-scale code 4095 → 2.23 MHz/µm.
  const GRAD_COIL_MAX = 2.23
  const LW_MHZ = 5                       // NV ODMR linewidth (intrinsic)
  // Physically-possible input limits (mirror qssc/array_calibration.py so the
  // UI can never offer a value the hardware/physics cannot deliver):
  //   gradient ≤ coil max (2.23 MHz/µm)
  //   microdiamond 10–1000 µm (must fit the 2.5 mm die pixel pitch)
  //   NV count ≤ volume ceiling (300 NV @ 200 µm, scales as size³)
  const PIXEL_SIZE_MIN = 10
  const PIXEL_SIZE_MAX = 1000
  const NV_ANCHOR_COUNT = 300            // NV ...
  const NV_ANCHOR_SIZE = 200             // ...at a 200 µm microdiamond (<10 ppm)
  const nvCeiling = (d) => Math.max(1, Math.round(NV_ANCHOR_COUNT * Math.pow(Math.max(1, d) / NV_ANCHOR_SIZE, 3)))
  const resolvableCap = Math.max(1, Math.floor(grad * pixelSize / LW_MHZ))
  const achievableCap = Math.min(resolvableCap, maxNv)   // what the calibration can actually reach
  const dacCode = Math.min(4095, Math.round(grad * 1838))
  // One-click qubit presets: fill every parameter to a physically-possible,
  // sensible combination so a user who doesn't know the limits can just pick a
  // target. Inputs stay editable afterwards.
  const QUBIT_PRESETS = {
    low:    { grad: 0.5,  pixelSize: 100, maxNv: 38,  shots: 1,  sr: false, label: 'Low · ≈ 8 qubits/pixel' },
    medium: { grad: 1.0,  pixelSize: 200, maxNv: 300, shots: 16, sr: false, label: 'Medium · ≈ 30 qubits/pixel' },
    high:   { grad: 2.23, pixelSize: 200, maxNv: 300, shots: 64, sr: true,  label: 'High · ≈ 70 qubits/pixel (coil max)' },
  }
  const applyPreset = (p) => {
    setGrad(p.grad); setPixelSize(p.pixelSize); setMaxNv(p.maxNv)
    setIntegrationShots(p.shots); setSuperresOn(p.sr)
  }
  const body = () => {
    const b = {
      profile, gradient_mhz_per_um: grad, pixel_size_um: pixelSize,
      seed, temperature_k: temperature, source,
      integration_shots: integrationShots,
      max_nv_per_microdiamond: maxNv, n_points: nPoints,
      noise_env: noiseEnv,
    }
    if (antiNoisePreset !== 'off' && antiNoisePresets && antiNoisePresets[antiNoisePreset]) {
      b.anti_noise = antiNoisePresets[antiNoisePreset]
    }
    if (subPixel) { b.sub_pixel = true; b.sigma_sheet_mm = sigmaSheet }
    if (superresOn) b.superres = superresFactor
    if (bayesianOn) b.bayesian = bayesianConf
    return b
  }
  return (
    <div className="card">
      <h2><span className="dot" />Calibrate the 64-pixel array</h2>
      <div className="hint">Measure each pixel's register width N from the ODMR line count.</div>
      {/* One-click qubit target presets — fill every parameter to a physically
          possible combination so a user who doesn't know the limits can just
          pick a target. Inputs stay editable afterwards. */}
      <div style={{ marginTop: 10 }}>
        <div className="hint" style={{ marginBottom: 6 }}>Qubit target preset — fills the parameters, then stay editable:</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {Object.entries(QUBIT_PRESETS).map(([k, p]) => (
            <button key={k} type="button" className="ghost"
              onClick={() => applyPreset(p)}
              style={{ padding: '6px 12px', fontSize: 12.5, borderRadius: 7,
                border: '1px solid var(--border, rgba(255,255,255,0.16))' }}>
              {p.label}
            </button>
          ))}
        </div>
      </div>
      <div className="row" style={{ alignItems: 'flex-start', marginTop: 10, gap: 16, flexWrap: 'wrap' }}>
        <div className="field" style={{ minWidth: 220, flex: '1 1 240px' }}>
          <RangeSlider label="Gradient (MHz/µm) · coil max" value={grad}
            min={0} max={GRAD_COIL_MAX} step={0.01}
            fmt={(v) => v.toFixed(2)}
            onChange={(v) => setGrad(v)} />
        </div>
        <div className="field" style={{ minWidth: 220, flex: '1 1 240px' }}>
          <RangeSlider label="Microdiamond size (µm)" value={pixelSize}
            min={PIXEL_SIZE_MIN} max={PIXEL_SIZE_MAX} step={1}
            onChange={(v) => {
              setPixelSize(v)
              if (maxNv > nvCeiling(v)) setMaxNv(nvCeiling(v))   // can't exceed the stone's NV capacity
            }} />
        </div>
        <button onClick={() => onCalibrate(body())}
          disabled={calibRunning} className="ghost" type="button" style={{ height: 36, marginTop: 4 }}>
          {calibRunning ? <><span className="spinner" /> Calibrating…</> : 'Calibrate array'}
        </button>
      </div>
      <div className="hint" style={{ marginTop: 6, fontSize: 11.5 }}>
        Field gradient spreads each pixel's NV resonances across{' '}
        <b>{(grad * pixelSize).toFixed(0)} MHz</b> →{' '}
        <b>≈ {achievableCap} addressable lines/pixel</b>{' '}
        (gradient resolution {resolvableCap} ÷ 5 MHz linewidth, capped by{' '}
        {maxNv} NV present; stone holds ≤ {nvCeiling(pixelSize)} NV at this size).{' '}
        Coil DAC code ≈ {dacCode} / 4095 — the on-board MCP4921 driver tops out at{' '}
        {GRAD_COIL_MAX} MHz/µm. All inputs are clamped to physically possible values.
      </div>
      <button type="button" className="ghost" aria-expanded={advOpen}
        onClick={() => setAdvOpen(o => !o)}
        style={{ marginTop: 8, fontSize: 12.5, padding: '4px 10px' }}>
        {advOpen ? '▾' : '▸'} Advanced readout &amp; resolution
      </button>
      {advOpen && (
        <div className="adv-panel" style={{ marginTop: 8 }}>
          <div className="hint" style={{ marginBottom: 10 }}>
            These options change the per-pixel qubit count the calibration measures
            (and the frequencies shown in the resonance map). Defaults reproduce the
            standard single-shot measurement. Every value is clamped to its physical limit.
          </div>
          <div className="adv-grid">
            <div className="adv-group">
              <div className="adv-group-title">Readout &amp; averaging</div>
              <RangeSlider label="Lock-in shots" value={integrationShots}
                min={1} max={10000} step={1}
                onChange={(v) => setIntegrationShots(v)} />
              <div className="field">
                <label>Noise environment</label>
                <select value={noiseEnv} onChange={e => setNoiseEnv(e.target.value)}>
                  <option value="none">none</option>
                  <option value="lab">lab</option>
                  <option value="quiet">quiet</option>
                  <option value="noisy">noisy</option>
                </select>
              </div>
              <div className="field">
                <label>Anti-noise stack</label>
                <select value={antiNoisePreset} onChange={e => setAntiNoisePreset(e.target.value)}>
                  <option value="off">off</option>
                  <option value="passive">passive</option>
                  <option value="active">active</option>
                  <option value="full">full (XY8 DD)</option>
                </select>
              </div>
            </div>
            <div className="adv-group">
              <div className="adv-group-title">Microdiamond model</div>
              <RangeSlider label={`Max NV / microdiamond · ≤ ${nvCeiling(pixelSize)}`} value={maxNv}
                min={1} max={nvCeiling(pixelSize)} step={1}
                onChange={(v) => setMaxNv(v)} />
              <RangeSlider label="Sweep points (real)" value={nPoints}
                min={32} max={4096} step={16}
                onChange={(v) => setNPoints(v)} />
            </div>
            <div className="adv-group">
              <div className="adv-group-title">Resolution enhancement</div>
              <div className="adv-toggle">
                <label className="adv-switch">
                  <input type="checkbox" id="sp" checked={subPixel} onChange={e => setSubPixel(e.target.checked)} />
                  <span>Sub-pixel detector imaging</span>
                </label>
                <RangeSlider label="Charge sheet σ" value={sigmaSheet}
                  min={0.01} max={10} step={0.1} unit="mm" fmt={(v) => v.toFixed(2)}
                  disabled={!subPixel} onChange={(v) => setSigmaSheet(v)} />
              </div>
              <div className="adv-toggle">
                <label className="adv-switch">
                  <input type="checkbox" id="sr" checked={superresOn} onChange={e => setSuperresOn(e.target.checked)} />
                  <span>Super-resolution ODMR</span>
                </label>
                <RangeSlider label="Recovery factor" value={superresFactor}
                  min={0} max={1} step={0.05} fmt={(v) => v.toFixed(2)}
                  disabled={!superresOn} onChange={(v) => setSuperresFactor(v)} />
              </div>
              <div className="adv-toggle">
                <label className="adv-switch">
                  <input type="checkbox" id="by" checked={bayesianOn} onChange={e => setBayesianOn(e.target.checked)} />
                  <span>Statistical readout</span>
                </label>
                <RangeSlider label="Confidence" value={bayesianConf}
                  min={0.01} max={0.99} step={0.01} fmt={(v) => v.toFixed(2)}
                  disabled={!bayesianOn} onChange={(v) => setBayesianConf(v)} />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// Calibration results for the 64-pixel array — measured per-pixel register
// widths, summary stats, and the per-pixel resonance-map modal. This is the
// RIGHT-hand card of the spectroscopy grid; App.jsx renders the ODMR spectrum
// card directly below it in the same column. The form (ParallelCalibCard) is
// the LEFT-hand card. Splitting form from results lets the page's own two-
// column grid place them side by side without a nested split inside one cell.
export function ParallelCalibResults({ calibResult, calibErr }) {
  const calib = calibResult && !calibResult.error ? calibResult : null
  const calibErrTxt = calibResult && calibResult.error ? calibResult.error : (calibErr || null)
  const [resOpen, setResOpen] = useState(false)
  return (
    <div className="card">
      <h2><span className="dot" />Calibration results</h2>
      {calibErrTxt && <div className="error" style={{ marginTop: 10 }}>{calibErrTxt}</div>}
      {calib ? (
        <div style={{ marginTop: 14, padding: '10px 12px', borderRadius: 8,
          background: 'var(--surface-2, rgba(255,255,255,0.04))',
          border: '1px solid var(--border, rgba(255,255,255,0.1))' }}>
          <div style={{ fontSize: 13, marginBottom: 8, color: 'var(--brand)' }}>
            Array calibration · <span className="mono">{calib.source}</span> · {calib.n_pixels} pixels
            {calib.integration_shots && calib.integration_shots > 1 ? ` · ${calib.integration_shots} shots` : ''}
            {calib.sub_pixel_factor ? ` · sub-pixel ×${calib.sub_pixel_factor}` : ''}
            {calib.readout_mode ? ` · ${calib.readout_mode}` : ''}
          </div>
          <div className="stats" style={{ marginBottom: 10 }}>
            <div className="stat"><div className="k">Best uniform N</div><div className="v">{calib.best_uniform_width}</div></div>
            <div className="stat"><div className="k">Pixels @ N</div><div className="v">{calib.best_uniform_pixels}</div></div>
            <div className="stat"><div className="k">Total qubits</div><div className="v" style={{ color: 'var(--ok, #2c8a4a)' }}>{calib.total_working_qubits}</div></div>
            <div className="stat"><div className="k">Width range</div><div className="v">{calib.min_width}–{calib.max_width}</div></div>
          </div>
          {(calib.spatial_channels || calib.effective_noise_rel != null || calib.filter_gain) && (
            <div className="stats" style={{ marginBottom: 10 }}>
              {calib.spatial_channels != null && (
                <div className="stat"><div className="k">Spatial channels</div><div className="v">{calib.spatial_channels}</div></div>
              )}
              {calib.effective_noise_rel != null && (
                <div className="stat"><div className="k">Effective readout noise</div>
                  <div className="v">{(calib.effective_noise_rel * 100).toFixed(2)}%</div></div>
              )}
              {calib.noise_env && calib.noise_env !== 'none' && (
                <div className="stat"><div className="k">Noise env</div><div className="v">{calib.noise_env}</div></div>
              )}
              {calib.anti_noise && (
                <div className="stat"><div className="k">Anti-noise</div><div className="v">on</div></div>
              )}
              {calib.gradient_code != null && (
                <div className="stat"><div className="k">Coil DAC code</div>
                  <div className="v">{calib.gradient_code}/4095{calib.gradient_applied_ok === false ? ' (not applied)' : ''}</div></div>
              )}
            </div>
          )}
          {calib.filter_gain && (
            <div className="hint" style={{ fontSize: 11.5, marginBottom: 8 }}>
              Anti-noise filter gain:{' '}
              {Object.entries(calib.filter_gain).map(([k, v]) => `${k}=${typeof v === 'number' ? v.toFixed(2) : v}`).join(' · ')}
            </div>
          )}
          {calib.physics_limits && (
            <div className="hint" style={{ fontSize: 11.5 }}>
              Physics limits — gradient ≤ {calib.physics_limits.gradient_max_mhz_per_um} MHz/µm ·
              microdiamond {calib.physics_limits.pixel_size_min_um}–{calib.physics_limits.pixel_size_max_um} µm ·
              NV ceiling {calib.physics_limits.nv_ceiling} · linewidth {calib.physics_limits.linewidth_mhz} MHz
            </div>
          )}
          <_ParPixelGrid widths={calib.per_pixel_widths} />
          {Array.isArray(calib.pixels) && calib.pixels.length > 0 && (
            <button type="button" className="ghost" style={{ marginTop: 12 }}
              onClick={() => setResOpen(true)}>
              View per-pixel resonance map ({calib.pixels.length} pixels · {calib.pixels.reduce((s, p) => s + (p.odmr_lines ? p.odmr_lines.length : 0), 0)} lines)
            </button>
          )}
        </div>
      ) : (
        <div className="empty">Calibrate the array to see per-pixel results.</div>
      )}
      {resOpen && calib && Array.isArray(calib.pixels) && (
        <div className="resonance-overlay" onClick={() => setResOpen(false)}>
          <div className="resonance-modal" onClick={(e) => e.stopPropagation()}>
            <button className="meas-help-close" type="button"
              onClick={() => setResOpen(false)} aria-label="Close">×</button>
            <h2><span className="dot" />Per-pixel resonance map (64-pixel array · calibrated)</h2>
            <ParallelPixelFreqTable pixels={calib.pixels} dGhz={calib.d_fit_ghz} />
          </div>
        </div>
      )}
    </div>
  )
}

export function ParallelRunCard({
  mode, setMode, nPixels, setNPixels, useCalib, setUseCalib,
  nShor, setNShor, numQubits, setNumQubits, grad, pixelSize,
  profile, temperature, seed, shots, noiseMode, pixelMode, primeReadout,
  onRun, runRunning, runResult, runErr, calib,
}) {
  const run = runResult && !runResult.error ? runResult : null
  const runErrTxt = runResult && runResult.error ? runResult.error : (runErr || null)
  const buildBody = () => {
    const body = {
      profile, temperature_k: temperature, seed, shots, noise_mode: noiseMode,
      mode, n_pixels: nPixels, use_calibration: useCalib,
      pixel_mode: pixelMode, prime_readout: primeReadout,
      gradient_mhz_per_um: grad, pixel_size_um: pixelSize,
    }
    if (mode === 'shor') body.n = nShor
    if (mode === 'sample') body.num_qubits = numQubits
    return body
  }
  return (
    <div className="card">
      <h2><span className="dot" />Run 64-parallel</h2>
      <div className="hint">64 independent pixel processors at 300 K (cross-pixel coupling falls as 1/r³ and is negligible at the 2.5 mm pitch). Chip throughput is 64× a single pixel, not one 640-qubit circuit. With calibration on, each pixel runs at its measured register width and narrow pixels are skipped — never truncated, since a truncated Shor returns wrong factors.</div>
      <div className="row" style={{ alignItems: 'flex-end', marginTop: 10 }}>
        <div className="field">
          <label>Mode</label>
          <select value={mode} onChange={e => setMode(e.target.value)}>
            <option value="sample">sample — same circuit, 64× shots</option>
            <option value="shor">shor — coprime bases race to factor N</option>
          </select>
        </div>
        <div className="field">
          <label>Pixels (1..64)</label>
          <input type="number" min="1" max="64" step="1" value={nPixels}
            onChange={e => setNPixels(Math.min(64, Math.max(1, +e.target.value || 1)))} />
        </div>
        {mode === 'shor' ? (
          <div className="field">
            <label>Integer N</label>
            <input type="number" min="2" max="63" step="1" value={nShor}
              onChange={e => setNShor(+e.target.value)} />
          </div>
        ) : (
          <div className="field">
            <label>Qubits/pixel</label>
            <input type="number" min="1" max="64" step="1" value={numQubits}
              onChange={e => setNumQubits(+e.target.value)} />
          </div>
        )}
      </div>
      <div className="row" style={{ alignItems: 'center', marginTop: 8 }}>
        <div className="field" style={{ flex: 0 }}>
          <label>Use calibration</label>
          <button type="button" className={`seg ${useCalib ? 'on' : ''}`}
            style={{ minWidth: 86, justifyContent: 'center', height: 36 }}
            onClick={() => setUseCalib(v => !v)} aria-pressed={useCalib}>
            {useCalib ? '✓ ON' : 'OFF'}
          </button>
        </div>
        <button onClick={() => onRun(buildBody())} disabled={runRunning}
          style={{ height: 36, marginLeft: 'auto' }}>
          {runRunning ? <><span className="spinner" /> Running…</> : 'Run 64-parallel'}
        </button>
      </div>
      {useCalib && !calib && (
        <div className="muted" style={{ fontSize: 11.5, marginTop: 8, color: 'var(--warn, #b8860b)' }}>
          Calibration is on but no array calibration is cached — calibrate first, or the run falls back to the uniform register.
        </div>
      )}
      {runErrTxt && <div className="error" style={{ marginTop: 10 }}>{runErrTxt}</div>}
      {run && (
        <div style={{ marginTop: 14 }}>
          <div className="kv">
            <span className="k">Mode</span><span className="v">{run.mode}</span>
            <span className="k">Pixels run</span><span className="v">{run.n_run != null ? run.n_run : run.n_pixels}</span>
            <span className="k">Throughput</span><span className="v">{run.throughput_x}×</span>
            <span className="k">Calibrated</span><span className="v">{run.calibrated ? 'yes' : 'no'}</span>
            {run.shots && <><span className="k">Shots</span><span className="v">{run.shots}</span></>}
            <span className="k">Wall time</span><span className="v">{run.elapsed_ms} ms</span>
          </div>
          {run.skipped_pixels != null && (
            <div className="expected" style={{ marginTop: 10 }}>
              Skipped {run.skipped_pixels} narrow pixel(s) · width range {run.min_width}–{run.max_width}
              {run.best_uniform_width != null && ` · best uniform N=${run.best_uniform_width} on ${run.best_uniform_pixels} pixels`}
            </div>
          )}
          {run.mode === 'shor' && (
            <div className={`expected ${run.success ? '' : 'bad'}`} style={{ marginTop: 10 }}>
              {run.success
                ? `✓ Factors of ${run.n}: ${run.factors[0]} × ${run.factors[1]} (pixel ${run.winner_pixel}, base a=${run.winner_base})`
                : `✗ No pixel found the period for N=${run.n} — decoherence at ${run.temperature_k} K (cool the chip or raise shots).`}
            </div>
          )}
          {run.mode === 'shor' && run.per_pixel && (
            <div style={{ marginTop: 10, maxHeight: 220, overflowY: 'auto',
              border: '1px solid var(--border, rgba(255,255,255,0.1))', borderRadius: 6, padding: 8 }}>
              <div className="mono" style={{ fontSize: 11.5, lineHeight: 1.7 }}>
                {run.per_pixel.filter(p => !p.skipped).slice(0, 24).map(p => (
                  <div key={p.pixel}>
                    <span className="muted">px{String(p.pixel).padStart(2, '0')}</span>{' '}
                    a={p.a} r={p.period != null ? p.period : '—'}{' '}
                    {p.factors ? `→ ${p.factors[0]}×${p.factors[1]}` : '—'}{' '}
                    {p.success ? '✓' : ''}
                  </div>
                ))}
                {run.per_pixel.some(p => p.skipped) && (
                  <div className="muted" style={{ marginTop: 4 }}>
                    + {run.per_pixel.filter(p => p.skipped).length} skipped (width &lt; circuit)
                  </div>
                )}
              </div>
            </div>
          )}
          {run.noise && <NoiseBudget noise={run.noise} />}
        </div>
      )}
    </div>
  )
}
