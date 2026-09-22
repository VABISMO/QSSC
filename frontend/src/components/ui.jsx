// Shared UI primitives: the per-pixel heatmap grid (unified from the old
// QubitPixelGrid + _ParPixelGrid) and the labelled range slider. Extracted
// from components.jsx.

import React from 'react'

// Per-pixel numeric heatmap. Unifies the two near-identical grids that used to
// live inline in CalibrationResults (QubitPixelGrid, green, per-pixel qubit
// count) and ParallelCalibResults (_ParPixelGrid, cyan, per-pixel register
// width). `variant` selects the title, colour, alpha curve, tooltip text and
// legend; the grid layout, cell shape and empty-cell handling are shared.
// `values` is an array of non-negative numbers; an empty array renders null.
const PIXEL_VARIANTS = {
  qubits: {
    title: 'Per-pixel qubit scan',
    colorRgb: '44, 138, 74',
    alphaBase: 0.25, alphaScale: 0.75,
    marginTop: 12,
    legend: '0 = no diamond detected · green intensity ∝ qubits resolved at the applied gradient',
    tooltip: (val, i, cols) => `pixel ${i} (r${Math.floor(i / cols)} c${i % cols}): ${val} qubit${val === 1 ? '' : 's'}`,
  },
  width: {
    title: 'Per-pixel register width',
    colorRgb: '34, 211, 238',
    alphaBase: 0.22, alphaScale: 0.78,
    marginTop: 0,
    legend: '0 = no NV detected · cyan intensity ∝ measured register width',
    tooltip: (val, i) => `pixel ${i}: width ${val}`,
  },
}

export function PixelGrid({ values, cols = 8, variant = 'qubits' }) {
  const cfg = PIXEL_VARIANTS[variant] || PIXEL_VARIANTS.qubits
  const arr = values || []
  const n = arr.length
  if (!n) return null
  const max = Math.max(1, ...arr)
  const rows = Math.ceil(n / cols)
  return (
    <div style={{ marginTop: cfg.marginTop }}>
      <div style={{ fontSize: 12.5, marginBottom: 6, color: 'var(--brand)' }}>
        {cfg.title} ({n} pixels · {cols}×{rows} grid)
      </div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${cols}, 1fr)`,
        gap: 3,
        maxWidth: 360,
      }}>
        {arr.map((val, i) => {
          const f = val / max
          const empty = val === 0
          return (
            <div key={i} title={cfg.tooltip(val, i, cols)}
              style={{
                aspectRatio: '1 / 1',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontFamily: 'var(--mono, monospace)',
                color: empty ? 'var(--muted)' : 'var(--bg, #061018)',
                background: empty
                  ? 'var(--surface-2, rgba(255,255,255,0.04))'
                  : `rgba(${cfg.colorRgb}, ${cfg.alphaBase + cfg.alphaScale * f})`,
                border: '1px solid var(--border, rgba(255,255,255,0.1))',
                borderRadius: 4,
              }}>{val}</div>
          )
        })}
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>{cfg.legend}</div>
    </div>
  )
}

// A labelled range slider with a live numeric readout and min/max end labels.
// Used for every physically-bounded calibration parameter so the operator drags
// within the possible range instead of typing a number the hardware can't
// deliver. `fmt` controls how the value + the two end labels render.
export function RangeSlider({ label, value, min, max, step, onChange, disabled, unit, fmt }) {
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