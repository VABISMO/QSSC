import React, { useState } from 'react'
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
                 title={`q${cell.idx} (${cell.r},${cell.c}) · ~10-NV register${cell.measured ? ` · ${cell.signal ? 'signal' : 'top'} bit=${lit ? 1 : 0} · confidence ${(cell.confidence * 100).toFixed(0)}%` : ''}`}>{cell.idx}</div>
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
        is the per-pixel register width N resolved by the field gradient (Invention 5: the ODMR line count at the
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
// CalibrationPanel — runs the 4-stage VABISMO-style calibration on demand.
// Stage 4 (per-pin resonance sweep) populates the per-pin resonance map,
// which is shown in a modal -- the map only exists once the calibration has
// actually swept each pin's ODMR dip, never before.
// ---------------------------------------------------------------------------
export function CalibrationPanel({ profile, temperature, seed, run, running, result, err }) {
  const [resOpen, setResOpen] = useState(false)
  const [gradCode, setGradCode] = useState(1803)  // MCP4921 DAC code (0..4095); 1803 ≈ 2.2 A addressing gradient
  const perPin = result && result.per_pin
  const dFit = result && result.d_fit_ghz
  const qsum = result && result.qubit_summary
  const gradOn = gradCode > 0
  // DAC code -> coil current (I = 5V * code/4096 / 1Ω) and spread (0.98 MHz/µm at 2.2 A)
  const coilA = gradOn ? (5.0 * gradCode / 4096) : 0
  const spreadMhz = gradOn ? (coilA / 2.2) * 0.98 * 200 : 0
  return (
    <div>
      <div className="hint" style={{ marginBottom: 10 }}>
        Full calibration: (1) scan the on-chip detector array for defect photocurrent (lock-in),
        (2) auto-align the qubit grid, (3) sweep the microwave frequency and fit the zero-field
        splitting D + linewidth, (4) sweep each pin's ODMR dip individually + fit Ramsey T₂* and
        Hahn-echo T₂ → the per-pin resonance / addressing map and the measured coherence budget.
        Invention 5 field gradient: the MCP4921 DAC sets the coil current before the ODMR sweep,
        spreading the ensemble line into N resolvable lines — the per-pixel qubit count N becomes
        a <b>measured</b> function of the applied gradient.
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'flex-end', marginBottom: 8 }}>
        <label className="muted" style={{ fontSize: 12.5 }}>
          Gradient DAC code (0..4095)
          <input type="range" min="0" max="4095" step="1" value={gradCode}
            onChange={e => setGradCode(+e.target.value)} style={{ display: 'block', width: 220 }} />
          <span className="mono" style={{ color: gradOn ? 'var(--brand)' : 'var(--muted)' }}>
            {gradCode} {gradOn ? `· ${coilA.toFixed(2)} A · ~${spreadMhz.toFixed(0)} MHz spread` : '· gradient off (ensemble)'}
          </span>
        </label>
        <button onClick={() => run({ gradient_code: gradCode, n_nv_per_pixel: 10 })}
          disabled={running} className="ghost" type="button">
          {running ? <><span className="spinner" /> Calibrating…</> : 'Run calibration'}
        </button>
      </div>
      {err && <div className="error" style={{ marginTop: 10 }}>{err}</div>}
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
            <span><b className="mono" style={{ fontSize: 18, color: 'var(--ok, #2c8a4a)' }}>{qsum.qubits_per_pixel}</b> qubits/pixel</span>
            <span>avg <b className="mono">{qsum.avg_qubits_per_pixel}</b>/pixel</span>
            <span><b className="mono" style={{ fontSize: 18 }}>{qsum.total_qubits}</b> total across {qsum.n_pixels} pixels</span>
            <span>mode: <span className={`tag ${qsum.pixel_mode === 'register' ? 'ok' : 'bad'}`}>{qsum.pixel_mode}</span></span>
            {qsum.gradient_applied && (
              <span className="muted">coil {qsum.coil_current_a} A · spread {qsum.gradient_spread_mhz} MHz</span>
            )}
          </div>
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
// MeasurementHelp — "?" button + modal that shows, for the algorithm that just
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
