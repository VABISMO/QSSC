// NV-magnetometer sensor panels: compass, gyro, magneto image, sensor field,
// tensor gradiometer. Extracted from components.jsx.

import React from 'react'

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
