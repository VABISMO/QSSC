import React from 'react'

// ---------------------------------------------------------------------------
// QubitGrid — 8x8 on-chip graphene/MoS₂ photodetector / CQD-emitter site grid;
// cells light up for the measured bitstring of the most recent run (lock-in
// readout, no camera).
// ---------------------------------------------------------------------------
export function QubitGrid({ sites, counts, numClbits }) {
  // Per-qubit P(measured |1⟩) across ALL counts. Qiskit bitstrings are
  // MSB-first, so classical bit i maps to string index (numClbits-1-i). A cell
  // lights with intensity = that bit's measured-1 probability, so an entangled
  // Bell/GHZ run visibly lights its measured qubits even when the single top
  // bitstring is all-zeros (the old "light the top bitstring" rendering went
  // dark for any run whose top outcome was |0...0⟩).
  const total = counts ? Object.values(counts).reduce((s, c) => s + (+c), 0) : 0
  const p1 = Array.from({ length: 64 }, () => 0)
  if (counts && total > 0 && numClbits) {
    for (const [bs, c] of Object.entries(counts)) {
      const n = +c
      for (let i = 0; i < numClbits && i < 64; i++) {
        const s = numClbits - 1 - i
        if (s >= 0 && s < bs.length && bs[s] === '1') p1[i] += n
      }
    }
    for (let i = 0; i < 64; i++) p1[i] = total ? p1[i] / total : 0
  }
  const cells = Array.from({ length: 64 }, (_, idx) => {
    const r = Math.floor(idx / 8), c = idx % 8
    const site = sites && sites[idx]
    const measured = !!(numClbits && idx < numClbits)
    return { idx, r, c, prob: p1[idx], measured, usable: site ? site.intensity > 100 : false }
  })
  return (
    <div>
      <div className="qgrid">
        {cells.map(cell => {
          const lit = cell.prob > 0
          // intensity (measured-|1⟩ probability) is encoded in the BACKGROUND
          // alpha via the --lit CSS var (0.2..1.0), NOT in whole-cell opacity
          // -- opacity would dim the number too and make it unreadable at every
          // intensity. The number itself stays full-opacity with a black
          // outline (styles.css) so it reads on both the dark floor and the
          // bright gradient.
          const style = lit ? { '--lit': (0.2 + 0.8 * cell.prob).toFixed(3) } : undefined
          return (
            <div key={cell.idx}
                 className={`qcell ${lit ? 'on' : ''} ${cell.measured ? 'measured' : ''} ${cell.usable ? 'usable' : ''}`}
                 style={style}
                 title={`q${cell.idx} (${cell.r},${cell.c})${cell.measured ? ` · P(|1⟩)=${(cell.prob * 100).toFixed(0)}%` : ''}`}>{cell.idx}</div>
          )
        })}
      </div>
      <div className="qgrid-legend">
        <span><span className="sw" style={{ background: 'var(--grad)' }} />|1⟩ readout (P)</span>
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
// OpticalChainDiagram — SVG flow of the on-chip readout chain: the physics of
// every chip component the simulator checks, RECALCULATED for the current
// profile + temperature (each execution). CQD ASE emitter -> SU-8/PDMS
// waveguide -> defect crystal (spin host / qubit: pump in, PL out) -> long-pass
// filter -> 2D photodetector -> TIA -> XADC -> 1 kHz lock-in.
//
// The per-stage numbers are computed client-side from `profile` + `temperature`
// (mirroring qssc/backend.py::optical_readout) so the diagram is always live
// and updates on every run even while the public API is stale. If the API
// serves `optical_readout`, those server values override per-field (server is
// then computing the same temperature/profile-dependent formulas).
//
// Temperature dependence (honest teaching model, anchored at 300 K):
//   readout noise ~ N0 * sqrt(T/300)        (graphene dark current + TIA thermal)
//   ODMR contrast ~ C0 * clamp(1 + 0.5*(300-T)/300, 0.5, 1.6)  (spin polarisation)
//   ASE gain      ~ 1.15 * (1 + 0.10*(300-T)/300)              (semiconductor gain)
//   WG loss       ~ 0.5 * (0.7 + 0.3*T/300) dB/cm              (polymer loss)
// dip = BRIGHT * contrast ; SNR = dip / noise. Cooling raises contrast and
// lowers noise, so the lock-in SNR climbs sharply — the readout improves cold.
// ---------------------------------------------------------------------------
const ADC_MAX = 4095, BRIGHT_CODE = 3200, LOCKIN_MOD_HZ = 1000

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
  const adcBits = de.adc_bits ?? 12
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
    { icon: '▣', t: 'XADC', v: [`${adcBits}-bit`, `${bright}/${dark}`] },
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