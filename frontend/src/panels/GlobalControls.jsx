import React from 'react'
import { useCircuit } from '../circuitContext.js'
import { PROFILES, NOISE_MODES } from '../constants.js'

export default function GlobalControls() {
  const {
    parallelMode, setParallelMode,
    hwMode, setHwMode, probe,
    profile, setProfile, temperature, setTemperature,
    noiseMode, setNoiseMode, seed, setSeed,
    pixelMode, setPixelMode, nNvPerPixel, modes,
    primeReadout, setPrimeReadout,
    noiseEnv, setNoiseEnv, antiNoisePreset, setAntiNoisePreset,
    info,
  } = useCircuit()
  return (
    <>
          {/* ============================= GLOBAL CONTROLS ============================= */}
          <div className="card" style={{ marginBottom: 18 }}>
            {/* Two top-level switches in one row, two columns: the 64-parallel
                ON/OFF toggle (which Run + Calibrate set is shown below) and the
                Simulator / Hardware-real environment. */}
            <div className="row" style={{ marginBottom: 12, alignItems: 'flex-start', gap: 18 }}>
            {/* The single "64-parallel ON/OFF" switch. OFF = single-pixel path
                (Circuit Runner + per-pixel ODMR calibration); ON = 64-pixel
                array path (array calibration + run-parallel). The bottom half
                of this tab swaps accordingly, so there is exactly one Run and
                one Calibrate visible at any time — no duplicated panels. */}
            <div className="seg-toggle" role="group" aria-label="64-parallel mode" style={{ flex: 1, marginBottom: 0 }}>
              <span className="seg-label">64-parallel</span>
              <button className={`seg ${!parallelMode ? 'on' : ''}`} onClick={() => setParallelMode(false)}>
                <span className="seg-ic">◇</span> OFF · single pixel
              </button>
              <button className={`seg ${parallelMode ? 'on' : ''}`} onClick={() => setParallelMode(true)}>
                <span className="seg-ic">▦</span> ON · 64 pixels
              </button>
              <span className="seg-note">
                {parallelMode
                  ? '64-pixel array — calibrate the array, then run one circuit across all pixels (64× throughput).'
                  : 'Single pixel — run one circuit on one pixel, calibrate its ODMR register.'}
              </span>
            </div>
            {/* Top-level Simulator / Hardware-real switch. Green when Hardware
                real is selected. In Hardware-real mode the Aer noise-mode
                selector is hidden (the real board's physics replace it) and a
                probe indicator shows whether a board answered the ID handshake. */}
            <div className="seg-toggle" role="group" aria-label="Execution environment" style={{ flex: 1, marginBottom: 0 }}>
              <span className="seg-label">Environment</span>
              <button className={`seg ${hwMode === 'simulator' ? 'on' : ''}`} onClick={() => setHwMode('simulator')}>
                <span className="seg-ic">◈</span> Simulator
              </button>
              <button className={`seg hw ${hwMode === 'hardware' ? 'on' : ''}`} onClick={() => setHwMode('hardware')}>
                <span className="seg-ic">⌬</span> Hardware
              </button>
              <span className="seg-note">
                {hwMode === 'simulator'
                  ? 'Aer computes the circuit under the chip physics; on-chip I/O modelled by SimulatedFPGA'
                  : probe.loading
                    ? 'probing the board…'
                    : probe.data && probe.data.connected
                      ? `FPGA connected — ${(probe.data.id || 'QSSC').split('\n')[0]} on ${probe.data.port}`
                      : 'No FPGA detected — connect the board and set QSSC_FPGA_PORT, or switch to Simulator'}
              </span>
            </div>
            </div>
            <div className="row" style={{ marginBottom: 0 }}>
              <div className="field">
                <label>Defect platform</label>
                <select value={profile} onChange={e => setProfile(e.target.value)}>
                  {PROFILES.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
                </select>
              </div>
              <div className="field">
                <label>Temperature (K) <span className="muted">· thermal setpoint 295 K</span></label>
                <input type="number" min="0.001" max="600" step="any" value={temperature} onChange={e => setTemperature(+e.target.value)} />
              </div>
              {hwMode === 'simulator' ? (
                <div className="field">
                  <label>Execution mode</label>
                  <select value={noiseMode} onChange={e => setNoiseMode(e.target.value)}>
                    {NOISE_MODES.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </select>
                </div>
              ) : (
                <div className="field">
                  <label>Execution mode</label>
                  <select disabled value="hardware">
                    <option value="hardware">Hardware (board physics)</option>
                  </select>
                </div>
              )}
              <div className="field">
                <label>RNG seed</label>
                <input type="number" value={seed} onChange={e => setSeed(+e.target.value)} />
              </div>
            </div>
            {/* Pixel mode (the "multiqbit mode" switch) + Prime-denoise toggle.
                Both run on the existing firmware (ODMR_SWEEP + READ_DETECTORS +
                READ_LOCKINP); no PCB change. The HW badge comes from
                /api/modes. In ensemble mode a 2q circuit is rejected by the
                backend (no within-pixel 2q at 300 K), so a hint says so. */}
            <div className="row" style={{ marginBottom: 0, marginTop: 4 }}>
              <div className="field">
                <label>Pixel mode <span className="muted">· multiqbit</span></label>
                <select value={pixelMode} onChange={e => setPixelMode(e.target.value)}>
                  <option value="register">Register — one pixel's N-NV register (dipole 2q)</option>
                  <option value="ensemble">Ensemble — 64 chip pixels (no within-pixel 2q)</option>
                </select>
              </div>
              <div className="field">
                <label>Register width N <span className="muted">· measured</span></label>
                <span className="mono" style={{ color: 'var(--brand)' }}>
                  {nNvPerPixel != null
                    ? `${nNvPerPixel} (calibration)`
                    : `${modes ? 'nominal' : '—'}${pixelMode === 'ensemble' ? ' · 1/pixel' : ''}`}
                </span>
                {modes && modes.pixel_modes && (
                  <span className="pill" style={{ marginLeft: 8, fontSize: 10.5,
                    color: 'var(--good)' }}>HW</span>
                )}
              </div>
              <div className="field">
                <label>Prime denoise <span className="muted">· coprime readout</span></label>
                <button type="button"
                  className={`seg ${primeReadout ? 'on' : ''}`}
                  style={{ minWidth: 86, justifyContent: 'center' }}
                  onClick={() => setPrimeReadout(v => !v)}
                  aria-pressed={primeReadout}>
                  {primeReadout ? '✓ ON' : 'OFF'}
                </button>
              </div>
              <div className="field">
                <label>Noise environment <span className="muted">· lab model</span></label>
                <select value={noiseEnv} onChange={e => setNoiseEnv(e.target.value)}>
                  <option value="none">none</option>
                  <option value="quiet">quiet lab</option>
                  <option value="lab">lab</option>
                  <option value="noisy">noisy lab</option>
                </select>
              </div>
              <div className="field">
                <label>Anti-noise filter <span className="muted">· denoise stack</span></label>
                <select value={antiNoisePreset} onChange={e => setAntiNoisePreset(e.target.value)}>
                  <option value="off">off</option>
                  <option value="passive">passive (iso + shield)</option>
                  <option value="active">active (+ servo + feed-forward)</option>
                  <option value="full">full (+ XY8 decoupling)</option>
                </select>
              </div>
            </div>
            {noiseEnv !== 'none' && (
              <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>
                Noise environment <b>{noiseEnv}</b>: environmental drift (temperature,
                vibration, EMI) raises the readout error. Turn the <b>anti-noise filter</b>{' '}
                on to attenuate those channels back down — the filter only has something
                to remove when an environment is selected, so its effect is largest in a
                noisy lab. Prime denoise stacks on top (coprime matched filter).
              </div>
            )}
            {pixelMode === 'ensemble' && (
              <div className="muted" style={{ fontSize: 11.5, marginTop: 8,
                color: 'var(--warn, #b8860b)' }}>
                Ensemble mode: the 64 chip pixels are the circuit qubits (one
                ensemble qubit per pixel). Cross-pixel coupling is zero at the
                2.5 mm pitch, so 2q gates are rejected — run 1q circuits, or
                switch to Register for within-pixel dipole 2q.
              </div>
            )}
            {/* Selected defect summary — one horizontal line below the form
                selectors (label + value inline, not a stacked field). */}
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
              <span className="muted" style={{ fontSize: '11.5px' }}>Selected defect</span>
              <span className="mono" style={{ color: 'var(--brand)' }}>
                {info.data ? `${info.data.defect} · D ≈ ${info.data.d_ghz} GHz` : 'loading…'}
              </span>
              {hwMode === 'hardware' && (
                <span className="mono" style={{ marginLeft: 'auto', fontSize: '11.5px',
                  color: probe.data && probe.data.connected ? 'var(--ok, #2c8a4a)' : 'var(--bad, #c0392b)' }}>
                  {probe.loading ? '◌ probing' : probe.data && probe.data.connected ? '● FPGA online' : '○ no FPGA'}
                </span>
              )}
            </div>
          </div>
    </>
  )
}