import React from 'react'
import { useCircuit } from '../circuitContext.js'
import { Verdict, NoiseBudget, RunPhysics, OpticalChainDiagram } from '../components.jsx'

export default function BackendPhysics() {
  const {
    info, parallelMode, runAssessment, parRun, result,
    profile, temperature,
  } = useCircuit()
  return (
    <>
          {/* ============================= BACKEND PHYSICS ============================= */}
          <div className="grid cols-2">
            <div className="card">
              <h2><span className="dot" />Backend physics</h2>
              <div className="hint">Live parameters from the Qiskit BackendV2 target at the selected temperature.</div>
              {info.error && <div className="error">{info.error}</div>}
              {info.data && (
                <div className="stats">
                  <div className="stat"><div className="k">D splitting</div><div className="v">{info.data.d_ghz}<small> GHz</small></div></div>
                  <div className="stat"><div className="k">Coherence T₂</div><div className="v">{(info.data.t2_us).toFixed(2)}<small> µs</small></div></div>
                  <div className="stat"><div className="k">Relaxation T₁</div><div className="v">{(info.data.t1_ms).toFixed(1)}<small> ms</small></div></div>
                  <div className="stat"><div className="k">Readout err</div><div className="v">{(info.data.readout_error * 100).toFixed(1)}<small> %</small></div></div>
                  <div className="stat"><div className="k">1q gate</div><div className="v">{info.data.gate_1q_ns}<small> ns</small></div></div>
                  <div className="stat"><div className="k">2q gate</div><div className="v">{info.data.gate_2q_ns}<small> ns</small></div></div>
                  <div className="stat"><div className="k">Qubits</div><div className="v">{info.data.num_qubits}</div></div>
                  <div className="stat"><div className="k">Pixels</div><div className="v">{info.data.site_count ?? info.data.pixels}</div></div>
                  <div className="stat"><div className="k">NVs/pixel</div><div className="v">{info.data.n_nv_per_pixel ?? '–'}</div></div>
                </div>
              )}
              {info.loading && <div className="muted"><span className="spinner" /> loading backend target…</div>}
              {!parallelMode && runAssessment && <div style={{ marginTop: 12 }}><Verdict assessment={runAssessment} /></div>}
              {(parallelMode ? parRun && parRun.noise : result && result.noise) && <NoiseBudget noise={parallelMode ? parRun.noise : result.noise} />}
            </div>

            <RunPhysics result={parallelMode ? parRun : result} profile={profile} temperature={temperature} infoData={info.data} />

            <div className="card optchain-card" style={{ gridColumn: '1 / -1' }}>
              <h2><span className="dot" />Optical readout chain</h2>
              <div className="hint">The physics of every chip component, recalculated for the selected defect &amp; temperature: CQD ASE emitter → SU-8/PDMS waveguide → <b>defect crystal</b> (the spin host / qubit — pump in, photoluminescence out) → long-pass filter → 2D photodetector → TIA → 4× AD7616 (16-bit parallel) → 1 kHz lock-in. Cooling cuts detector noise (∝√T) and raises ODMR contrast, so the lock-in SNR climbs — change the temperature or defect above and watch the dip, noise and SNR update.</div>
              <OpticalChainDiagram data={info.data && info.data.optical_readout} infoData={info.data} profile={profile} temperature={temperature} activeSites={(parallelMode ? parRun : result)?.num_qubits} />
            </div>
          </div>
    </>
  )
}