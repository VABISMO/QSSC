// ODMR spectrum chart. Extracted from components.jsx.

import React from 'react'

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

