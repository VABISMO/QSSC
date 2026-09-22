import React from 'react'
import { useCircuit } from '../circuitContext.js'
import { SignalLab, SignalChart } from '../components.jsx'
import { SEQS } from '../constants.js'

export default function AnalogSignal() {
  const {
    seq, setSeq, detuning, setDetuning,
    signalArr, signal,
  } = useCircuit()
  return (
    <>
          {/* ============================= ANALOG SIGNAL ============================= */}
          <div className="card">
            <h2><span className="dot" />Per-pixel analog signal lab</h2>
            <div className="hint">Fired emitter drive vs received detector photocurrent for every pixel in the 8×8 array, with per-pixel KPIs and array-level graphs. The animated grid pulses with the AD7616 photocurrent as the time cursor sweeps the pulse sequence — click a pixel for its sent-vs-received overlay.</div>
            <div className="row" style={{ marginBottom: 12 }}>
              <div className="field">
                <label>Sequence</label>
                <select value={seq} onChange={e => setSeq(e.target.value)}>
                  {SEQS.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
                </select>
              </div>
              <div className="field">
                <label>Detuning (MHz)</label>
                <input type="number" step="any" value={detuning} onChange={e => setDetuning(+e.target.value)} />
              </div>
            </div>
            <SignalLab data={signalArr.data} error={signalArr.error} />
          </div>
          <div className="card" style={{ marginTop: 16 }}>
            <h2><span className="dot" />Single-pixel reference trace</h2>
            <div className="hint">The normalized photoluminescence of pixel 0 — the same pulse sequence, as a single oscilloscope-style trace (T₂ + readout contrast only, no per-pixel scaling).</div>
            <SignalChart data={signal.data} error={signal.error} />
          </div>
    </>
  )
}