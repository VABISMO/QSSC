// Analog signal charts + the per-pixel signal lab (fired vs received). Extracted
// from components.jsx.

import React, { useState, useEffect, useMemo, useRef } from 'react'

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

