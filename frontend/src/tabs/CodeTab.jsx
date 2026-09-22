import React from 'react'
import { useCircuit } from '../circuitContext.js'
import { CUSTOM_EXAMPLES } from '../constants.js'

export default function CodeTab() {
  const {
    edName, setEdName, edCode, setEdCode, edId, scriptMsg,
    scripts, scriptsLoading,
    saveScript, newScript, editScript, removeScript, loadScriptToRunner,
  } = useCircuit()
  return (
    <>
        {/* ============================= CODE (scripts) ============================= */}
        <div className="grid cols-2">
          <div className="card">
            <h2><span className="dot" />Script editor</h2>
            <div className="hint">Save Qiskit scripts (Python snippet or OpenQASM 2). Saved scripts appear as loadable entries in the Hardware circuit runner. The <b>QSSC backend</b> example below shows the direct Python API (simulator + real hardware) and runs locally — the circuit snippets run in the browser.</div>
            <div className="row">
              <div className="field" style={{ flex: 1 }}>
                <label>Name</label>
                <input type="text" value={edName} placeholder="my-script" onChange={e => setEdName(e.target.value)} />
              </div>
              <div className="field" style={{ justifyContent: 'flex-end' }}>
                <label>&nbsp;</label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="ghost" type="button" onClick={newScript}>New</button>
                  <button type="button" onClick={saveScript}>{edId ? 'Update' : 'Save'}</button>
                </div>
              </div>
            </div>
            <div className="row" style={{ marginTop: 6 }}>
              <div className="field" style={{ flex: 1 }}>
                <label>Load example</label>
                <select defaultValue="" onChange={e => { const k = e.target.value; if (k && CUSTOM_EXAMPLES[k]) { setEdCode(CUSTOM_EXAMPLES[k]); setEdName(k); e.target.value = ''; } }}>
                  <option value="">pick one…</option>
                  <option value="bell">Bell pair — runs in browser</option>
                  <option value="ghz">GHZ — runs in browser</option>
                  <option value="qft">QFT — runs in browser</option>
                  <option value="qasm">OpenQASM 2 — runs in browser</option>
                  <option value="qssc_backend">QSSC backend — simulator + real HW (local Python)</option>
                </select>
              </div>
            </div>
            <textarea className="code-editor" value={edCode} spellCheck={false}
              onChange={e => setEdCode(e.target.value)} rows={20} />
            {scriptMsg && <div className="muted" style={{ marginTop: 8, fontSize: 12.5 }}>{scriptMsg}</div>}
          </div>
          <div className="card">
            <h2><span className="dot" />Saved scripts <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>({scripts.length})</span></h2>
            <div className="hint">Click to edit, or load straight into the Hardware runner.</div>
            {scriptsLoading && <div className="muted"><span className="spinner" /> loading…</div>}
            {!scriptsLoading && scripts.length === 0 && <div className="muted">No scripts saved yet.</div>}
            <div className="histlist">
              {scripts.map(s => (
                <div className="histrow" key={s.id} style={{ cursor: 'default' }}>
                  <div className="hist-main">
                    <span className="halgo" style={{ cursor: 'pointer' }} onClick={() => editScript(s)}>{s.name}</span>
                    <span className="muted mono" style={{ fontSize: 11 }}>{(s.code || '').split('\n')[0].slice(0, 40)}</span>
                  </div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button className="ghost" type="button" style={{ padding: '4px 9px', fontSize: 11 }} onClick={() => loadScriptToRunner(s)}>load</button>
                    <button className="ghost" type="button" style={{ padding: '4px 9px', fontSize: 11 }} onClick={() => editScript(s)}>edit</button>
                    <button className="ghost" type="button" style={{ padding: '4px 9px', fontSize: 11, color: 'var(--err)' }} onClick={() => removeScript(s.id)}>del</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
    </>
  )
}