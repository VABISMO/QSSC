import React from 'react'
import { useCircuit } from '../circuitContext.js'

export default function HistoryTab() {
  const {
    history, historyLoading, refreshHistory, loadFromHistory,
  } = useCircuit()
  return (
    <>
        {/* ============================= HISTORIAL ============================= */}
        <div className="card">
          <div className="row" style={{ alignItems: 'center', marginBottom: 14 }}>
            <h2 style={{ margin: 0 }}><span className="dot" />Execution history</h2>
            <button className="ghost" type="button" onClick={refreshHistory} style={{ marginLeft: 'auto' }}>
              {historyLoading ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
          <div className="hint" style={{ marginBottom: 12 }}>
            Every run lands in the job history — MongoDB when it is configured, the in-memory store otherwise (this deploy keeps history in memory). Click a row to load its result back into the Hardware tab.
          </div>
          {history.length === 0 && !historyLoading && <div className="muted">No executions yet — run a circuit in the Hardware tab.</div>}
          <div className="histlist">
            {history.map(h => (
              <div className="histrow" key={h.job_id} onClick={() => loadFromHistory(h)}>
                <div className="hist-main">
                  <span className={`hstatus ${h.status}`}>{h.status}</span>
                  <span className="mono halgo">{h.algorithm}</span>
                  <span className="muted">{h.profile}</span>
                  <span className="muted">{h.temperature_k} K</span>
                  <span className="muted">{h.shots} shots</span>
                  <span className="muted">{h.noise_mode}</span>
                </div>
                <div className="hist-time muted mono">{h.created_at ? new Date(h.created_at).toLocaleTimeString() : ''}</div>
              </div>
            ))}
          </div>
        </div>
    </>
  )
}