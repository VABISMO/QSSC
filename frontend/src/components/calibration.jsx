// Single-pixel calibration panel + results (stage table, per-pixel qubit scan
// via the shared PixelGrid). Extracted from components.jsx.

import React, { useState } from 'react'
import { PixelGrid } from './ui.jsx'
import { QubitFrequencyTable } from './quantum.jsx'


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
            <PixelGrid values={qsum.qubits_per_pixel} cols={8} variant="qubits" />
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

