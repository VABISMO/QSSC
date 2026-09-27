// 64-parallel computing cards: calibrate, results, run. Share RangeSlider and
// the per-pixel PixelGrid from ui.jsx. Extracted from components.jsx.

import React, { useState } from 'react'
import { PixelGrid, RangeSlider } from './ui.jsx'
import { NoiseBudget } from './physics.jsx'


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
          <PixelGrid values={calib.per_pixel_widths} variant="width" />
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

