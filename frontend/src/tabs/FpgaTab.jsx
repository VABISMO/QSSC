import React from 'react'
import { useCircuit } from '../circuitContext.js'
import { FALLBACK_PROTOCOL } from '../constants.js'

export default function FpgaTab() {
  const {
    hwMode, setHwMode,
    fpga, fpgaLoading, fpgaErr, fpgaBusy, sendFpga,
    emIdx, setEmIdx, emDur, setEmDur, emLvl, setEmLvl,
    detSite, setDetSite,
    mwQ, setMwQ, mwFreq, setMwFreq, mwAmp, setMwAmp,
    mwDur, setMwDur, mwPhase, setMwPhase,
    rawCmd, setRawCmd,
    cmdRefOpen, setCmdRefOpen,
    fpgaReply, fpgaLog,
    qrng, qrngErr, qrngLoading, qrngBytes, setQrngBytes, qrngCN, setQrngCN, runQrng,
  } = useCircuit()
  return (
    <>
          {/* ============================= FPGA (control + metrics) ============================= */}
          {/* environment selector — mirrors the top-level Simulator / Hardware-real switch */}
          <div className="seg-toggle" role="group" aria-label="FPGA environment">
            <span className="seg-label">Environment</span>
            <button className={`seg ${hwMode === 'simulator' ? 'on' : ''}`} onClick={() => setHwMode('simulator')}>
              <span className="seg-ic">◈</span> Simulator
            </button>
            <button className={`seg hw ${hwMode === 'hardware' ? 'on' : ''}`} onClick={() => setHwMode('hardware')}>
              <span className="seg-ic">⌬</span> Hardware
            </button>
            <span className="seg-note">
              {hwMode === 'simulator'
                ? 'Aer + SimulatedFPGA — averages freely, cleaner readout (no shot-noise floor)'
                : 'real ADF4351 + 64-ch AD7616 chain — live emitter / MW / detector / QRNG I/O'}
            </span>
          </div>

          <div className="stats" style={{ marginBottom: 18 }}>
            <div className="stat"><div className="k">Target FPGA</div><div className="v">XC7A35T<small> · CSG324</small></div></div>
            <div className="stat"><div className="k">Synthesis</div><div className="v">{fpga?.status ?? '…'}<small> · yosys</small></div></div>
            <div className="stat"><div className="k">Logic cells</div><div className="v">{fpga?.total_cells ?? '—'}</div></div>
            <div className="stat"><div className="k">RTL sim</div><div className="v">{fpga?.sim?.status === 'pass' ? 'PASS' : '—'}<small> · {fpga?.sim?.scenarios} tests</small></div></div>
          </div>

          <div className="grid cols-2">
            {/* LEFT: resource utilisation + quality gates */}
            <div className="card">
              <h2><span className="dot" />Resource utilisation vs XC7A35T</h2>
              <div className="hint">yosys synth_xilinx estimate (no P&amp;R / no timing). The 64 detector channels read fully in parallel — no analog MUX, no XADC.</div>
              {fpgaLoading && <div className="muted"><span className="spinner" /> loading synthesis report…</div>}
              {fpgaErr && <div className="error">{fpgaErr}</div>}
              {!fpgaLoading && !fpgaErr && fpga?.status === 'NO_SYNTHESIS' && (
                <div className="muted" style={{ padding: '10px 0' }}>
                  No synthesis run found — run <span className="mono">fpga/synth/run_synth.sh</span> (yosys) to populate the resource estimate. The RTL module list + command protocol below remain available.
                </div>
              )}
              {!fpgaLoading && !fpgaErr && fpga?.status === 'PARSE_ERROR' && (
                <div className="error" style={{ marginBottom: 8 }}>Could not parse <span className="mono">fpga/synth/synth.log</span> — re-run run_synth.sh.</div>
              )}
              {fpga?.resources?.map(r => (
                <div className="bar-row" key={r.name}>
                  <span className="muted mono" style={{ fontSize: 11.5 }}>{r.label}</span>
                  <div className="bar-track"><div className="bar-fill" style={{ width: `${Math.min(100, r.pct)}%` }} /></div>
                  <span className="mono" style={{ fontSize: 11, textAlign: 'right' }}>{r.pct}%</span>
                </div>
              ))}
              {fpga?.resources?.length > 0 && (
                <div className="legend" style={{ marginTop: 12 }}>
                  {fpga.resources.map(r => (
                    <div key={r.name}><b>{r.used}</b> / {r.available} {r.label}</div>
                  ))}
                </div>
              )}
              {fpga?.resources?.length > 0 && fpga?.quality && (
                <div className="row" style={{ marginTop: 14, marginBottom: 0 }}>
                  <div className="stat" style={{ flex: 1, padding: '9px 10px' }}>
                    <div className="k">Latches</div><span className={`chip ${fpga.quality.latches ? 'bad' : 'ok'}`}>{fpga.quality.latches} {fpga.quality.latches ? '⚠' : '✓'}</span>
                  </div>
                  <div className="stat" style={{ flex: 1, padding: '9px 10px' }}>
                    <div className="k">Blackboxes</div><span className={`chip ${fpga.quality.blackboxes ? 'bad' : 'ok'}`}>{fpga.quality.blackboxes} {fpga.quality.blackboxes ? '⚠' : '✓'}</span>
                  </div>
                  <div className="stat" style={{ flex: 1, padding: '9px 10px' }}>
                    <div className="k">Width warns</div><span className={`chip ${fpga.quality.width ? 'warn' : 'ok'}`}>{fpga.quality.width} {fpga.quality.width ? '⚠' : '✓'}</span>
                  </div>
                  <div className="stat" style={{ flex: 1, padding: '9px 10px' }}>
                    <div className="k">Fits XC7A35T</div><span className={`chip ${fpga.quality.fits ? 'ok' : 'bad'}`}>{fpga.quality.fits ? 'yes ✓' : 'no ⚠'}</span>
                  </div>
                </div>
              )}
            </div>

            {/* RIGHT: RTL modules */}
            <div className="card">
              <h2><span className="dot" />RTL modules <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>({fpga?.modules?.length ?? 0})</span></h2>
              <div className="hint">fpga/rtl/*.v — top <span className="mono">top_level</span> wires UART ↔ command parser ↔ SPI / ADC / emitter / MW.</div>
              <div className="fpga-modlist">
                {fpga?.modules?.map(m => (
                  <div className="fpga-mod" key={m.file}>
                    <span className="mono fpga-modname">{m.file}</span>
                    <span className="muted">{m.desc}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* CONTROL PANEL */}
          <div className="section-title">FPGA control — UART command protocol</div>
          <div className="grid cols-2">
            <div className="card">
              <h2><span className="dot" />Command the FPGA {fpgaBusy ? <span className="spinner" style={{ marginLeft: 8 }} /> : null}</h2>
              <div className="hint">Drives the SAME SimulatedFPGA the Qiskit backend uses (or a real board if QSSC_FPGA_PORT is set). Stateful: CALIBRATE builds the LUT, SET_EMITTER arms, READ reads. Localhost-only.</div>

              <div className="fpga-quickrow">
                <button type="button" onClick={() => sendFpga('READ_DETECTORS')}>READ_DETECTORS</button>
                <button type="button" onClick={() => sendFpga('CALIBRATE')}>CALIBRATE</button>
                <button type="button" onClick={() => sendFpga('CLEAR_EMITTERS')}>CLEAR_EMITTERS</button>
                <button type="button" onClick={() => sendFpga('CLEAR_DETECTORS')}>CLEAR_DETECTORS</button>
              </div>

              <div className="fpga-fieldset">
                <div className="fpga-legend">Emitter array · 3× TLC5947, 64ch</div>
                <div className="row" style={{ marginBottom: 8 }}>
                  <div className="field" style={{ flex: '0 0 70px' }}><label>index 0..63</label><input type="number" min="0" max="63" value={emIdx} onChange={e => setEmIdx(+e.target.value)} /></div>
                  <div className="field" style={{ flex: '0 0 90px' }}><label>duration ms</label><input type="number" min="0" value={emDur} onChange={e => setEmDur(+e.target.value)} /></div>
                  <div className="field" style={{ flex: '0 0 100px' }}><label>level 0..4095</label><input type="number" min="0" max="4095" value={emLvl} onChange={e => setEmLvl(+e.target.value)} /></div>
                </div>
                <div className="fpga-quickrow">
                  <button type="button" onClick={() => sendFpga(`DRIVE_EMITTER ${emIdx} ${emDur}`)}>DRIVE_EMITTER</button>
                  <button type="button" onClick={() => sendFpga(`SET_EMITTER ${emIdx} ${emLvl}`)}>SET_EMITTER</button>
                  <button type="button" onClick={() => sendFpga(`DRIVE_PATTERN ${emDur}`)}>DRIVE_PATTERN</button>
                </div>
              </div>

              <div className="fpga-fieldset">
                <div className="fpga-legend">Detector readout · 4× AD7616, 16-bit</div>
                <div className="row" style={{ marginBottom: 0 }}>
                  <div className="field" style={{ flex: '0 0 90px' }}><label>site 0..63</label><input type="number" min="0" max="63" value={detSite} onChange={e => setDetSite(+e.target.value)} /></div>
                  <div className="field" style={{ justifyContent: 'flex-end' }}><label>&nbsp;</label>
                    <button type="button" onClick={() => sendFpga(`READ_DETECTOR ${detSite}`)}>READ_DETECTOR</button>
                  </div>
                </div>
              </div>

              <div className="fpga-fieldset">
                <div className="fpga-legend">Microwave pulse · ADF4351 PLL</div>
                <div className="row" style={{ marginBottom: 8 }}>
                  <div className="field" style={{ flex: '0 0 70px' }}><label>qubit</label><input type="number" min="0" max="63" value={mwQ} onChange={e => setMwQ(+e.target.value)} /></div>
                  <div className="field" style={{ flex: 1 }}><label>freq kHz</label><input type="number" min="0" value={mwFreq} onChange={e => setMwFreq(+e.target.value)} /></div>
                  <div className="field" style={{ flex: '0 0 70px' }}><label>amp</label><input type="number" min="0" value={mwAmp} onChange={e => setMwAmp(+e.target.value)} /></div>
                </div>
                <div className="row" style={{ marginBottom: 8 }}>
                  <div className="field" style={{ flex: '0 0 110px' }}><label>duration ns</label><input type="number" min="0" value={mwDur} onChange={e => setMwDur(+e.target.value)} /></div>
                  <div className="field" style={{ flex: '0 0 110px' }}><label>phase mrad</label><input type="number" value={mwPhase} onChange={e => setMwPhase(+e.target.value)} /></div>
                </div>
                <div className="fpga-quickrow">
                  <button type="button" onClick={() => sendFpga(`APPLY_PULSE ${mwQ} ${mwFreq} ${mwAmp} ${mwDur} ${mwPhase}`)}>APPLY_PULSE</button>
                </div>
              </div>

              <div className="fpga-fieldset">
                <div className="fpga-legend">Raw command</div>
                <div className="row" style={{ marginBottom: 0 }}>
                  <div className="field" style={{ flex: 1 }}><label>command line</label>
                    <input type="text" value={rawCmd} placeholder="e.g. READ_DETECTOR 3" onChange={e => setRawCmd(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') sendFpga(rawCmd.trim()) }} />
                  </div>
                  <div className="field" style={{ justifyContent: 'flex-end' }}><label>&nbsp;</label>
                    <button type="button" onClick={() => sendFpga(rawCmd.trim())}>Send</button>
                  </div>
                </div>
              </div>

              <div className="fpga-quickrow" style={{ marginTop: 2 }}>
                <button type="button" className="ghost" onClick={() => setCmdRefOpen(true)}>Command reference</button>
              </div>
            </div>

            {/* RIGHT: reply + detector heatmap + log */}
            <div className="card">
              <h2><span className="dot" />Reply &amp; readout</h2>
              <div className="hint">Last reply highlighted; the full command log is below.</div>
              {!fpgaReply && (
                <div className="fpga-emptyhelp">
                  <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>Available firmware commands — click one to run it, or type it in the Raw command box.</div>
                  <div className="fpga-helpscroll">
                  {(fpga?.protocol?.length >= FALLBACK_PROTOCOL.length ? fpga.protocol : FALLBACK_PROTOCOL).map(p => (
                    <button key={p.cmd} type="button" className="fpga-helpcmd" onClick={() => sendFpga(p.args === '—' ? p.cmd : `${p.cmd} ${p.cmd === 'READ_RNG' ? '32' : p.cmd === 'READ_DETECTOR' ? '0' : p.cmd === 'DRIVE_EMITTER' ? '0 50' : p.cmd === 'SET_EMITTER' ? '0 4095' : p.cmd === 'DRIVE_PATTERN' ? '50' : p.cmd === 'APPLY_PULSE' ? '0 2870000 63 200 0' : p.cmd === 'ODMR_SWEEP' ? '0 2860000 2880000 64' : p.cmd === 'READ_COMPASS' ? '5000' : p.cmd === 'READ_GYRO' ? '1000000' : p.cmd === 'READ_LOCKIN' ? '1000 500' : p.cmd === 'RUN_SEQUENCE' ? 'ramsey 8' : ''}`.trim())}>
                      <span className="mono fpga-helpcmd-name">{p.cmd}{p.args !== '—' ? <span className="muted"> {p.args}</span> : null}</span>
                      <span className="fpga-helpcmd-desc">{p.desc}</span>
                    </button>
                  ))}
                  </div>
                </div>
              )}
              {fpgaReply && (
                <>
                  <div className={`verdict ${fpgaReply.ok ? 'ok' : 'bad'}`} style={{ marginBottom: 12 }}>
                    <div className="verdict-h">{fpgaReply.ok ? 'OK ✓' : 'ERR'}<span className="verdict-tag">{fpgaReply.command.split(' ')[0]}</span></div>
                    <div className="verdict-body">
                      <div className="mono" style={{ fontSize: 12 }}>&gt; {fpgaReply.command}</div>
                      <div className="mono" style={{ fontSize: 12, color: fpgaReply.ok ? 'var(--ok)' : 'var(--err)' }}>← {fpgaReply.reply}</div>
                    </div>
                  </div>
                  {fpgaReply.command.startsWith('READ_DETECTORS') && (() => {
                    const vals = fpgaReply.reply.split(/\s+/).filter(Boolean).map(Number)
                    if (vals.length < 64) return null
                    const mx = Math.max(...vals, 1)
                    return (
                      <div className="fpga-heatmap">
                        <div className="fpga-hm-title">64-channel AD7616 frame · 8×8 (normalised to {mx})</div>
                        <div className="fpga-hm-grid">
                          {Array.from({ length: 8 }, (_, row) => (
                            <div className="fpga-hm-row" key={row}>
                              {vals.slice(row * 8, row * 8 + 8).map((v, i) => (
                                <div className="fpga-hm-cell" key={i} title={`ch ${row * 8 + i}: ${v}`}
                                  style={{ background: `rgba(34,211,238,${(v / mx).toFixed(3)})` }}>
                                  <span>{v}</span>
                                </div>
                              ))}
                            </div>
                          ))}
                        </div>
                      </div>
                    )
                  })()}
                </>
              )}
              <div className="fpga-log">
                <div className="muted" style={{ fontSize: 11.5, marginBottom: 6 }}>Command log · most recent first</div>
                {fpgaLog.length === 0 && <div className="muted">—</div>}
                {fpgaLog.map((l, i) => (
                  <div className="fpga-logrow" key={i}>
                    <span className="mono fpga-logt">{l.t}</span>
                    <span className={`fpga-logok ${l.ok ? 'ok' : 'bad'}`}>{l.ok ? '✓' : '✗'}</span>
                    <span className="mono fpga-logcmd">{l.command}</span>
                    <span className="mono muted fpga-logreply">{l.reply.length > 64 ? l.reply.slice(0, 64) + '…' : l.reply}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* COMMAND-REFERENCE MODAL (replaces the inline protocol-reference section) */}
          {cmdRefOpen && (() => {
            const proto = (fpga?.protocol?.length >= FALLBACK_PROTOCOL.length ? fpga.protocol : FALLBACK_PROTOCOL)
            return (
              <div className="cmd-ref-overlay" onClick={() => setCmdRefOpen(false)} role="dialog" aria-modal="true" aria-label="Firmware command set">
                <div className="cmd-ref-modal" onClick={e => e.stopPropagation()}>
                  <button type="button" className="cmd-ref-close" onClick={() => setCmdRefOpen(false)} aria-label="Close command reference">✕</button>
                  <h2><span className="dot" />Firmware command set <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>(fpga/rtl/command_parser.v ↔ qssc.simulator)</span></h2>
                  <div className="hint">The Python FPGAConnection and the RTL command parser implement exactly these verbs. SET_THRESHOLD was removed by the AD7616 pivot → ERR.</div>
                  <div className="fpga-proto">
                    <div className="fpga-proto-hd">Command</div><div className="fpga-proto-hd">Args</div><div className="fpga-proto-hd">Reply</div><div className="fpga-proto-hd">Description</div>
                    {proto.map(p => (
                      <React.Fragment key={p.cmd}>
                        <div className="mono fpga-proto-cmd">{p.cmd}</div>
                        <div className="mono fpga-proto-args">{p.args}</div>
                        <div className="mono fpga-proto-reply">{p.reply}</div>
                        <div className="muted fpga-proto-desc">{p.desc}</div>
                      </React.Fragment>
                    ))}
                  </div>
                </div>
              </div>
            )
          })()}

          {/* HARDWARE QRNG — physical entropy from the AD7616 noise floor */}
          <div className="section-title">Hardware QRNG — physical entropy from the AD7616 noise floor</div>
          <div className="card">
            <h2><span className="dot" />Quantum RNG <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>(qrng_entropy.v · READ_RNG)</span></h2>
            <div className="hint">
              A <b>dark</b> AD7616 channel's LSB is dominated by thermal/Johnson + ADC quantization noise (a dark channel has no photocurrent, hence no shot noise). The FPGA pairs successive samples through a von-Neumann extractor (01→0, 10→1, 00/11 discarded) and packs debiased bits into 32-bit words. The 32-byte seed drives an Ed25519 keypair + self-signed X.509 device cert.
            </div>
            <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
              <div className="field"><label>Entropy bytes</label>
                <select value={qrngBytes} onChange={e => setQrngBytes(+e.target.value)}>
                  <option value={32}>32 (Ed25519 seed)</option>
                  <option value={64}>64</option>
                  <option value={128}>128</option>
                </select>
              </div>
              <div className="field" style={{ flex: 1, minWidth: 180 }}><label>Cert common name</label>
                <input type="text" value={qrngCN} onChange={e => setQrngCN(e.target.value)} />
              </div>
              <div className="field" style={{ justifyContent: 'flex-end' }}><label>&nbsp;</label>
                <button className="btn" onClick={runQrng} disabled={qrngLoading}>
                  {qrngLoading ? <><span className="spinner" /> Harvesting…</> : 'Generate hardware key'}
                </button>
              </div>
            </div>
            {qrngErr && <div className="error">{qrngErr}</div>}
            {qrng && (
              <div className="fpga-qrng-out">
                <div className="row" style={{ marginBottom: 8, gap: 10, flexWrap: 'wrap' }}>
                  <div className="stat" style={{ flex: '0 0 auto', padding: '8px 10px' }}><div className="k">Source</div><div className="v" style={{ fontSize: 12 }}>{qrng.source}</div></div>
                  <div className="stat" style={{ flex: '0 0 auto', padding: '8px 10px' }}><div className="k">Algorithm</div><div className="v">{qrng.algorithm}</div></div>
                  <div className="stat" style={{ flex: '0 0 auto', padding: '8px 10px' }}><div className="k">Serial</div><div className="v mono" style={{ fontSize: 11 }}>{qrng.serial}</div></div>
                  <div className="stat" style={{ flex: '0 0 auto', padding: '8px 10px' }}><div className="k">Extract rate</div><div className="v">{(qrng.extraction?.extract_rate * 100).toFixed(1)}%<small> · {qrng.extraction?.kept}/{qrng.extraction?.raw_bits} bits</small></div></div>
                </div>
                <div className="muted" style={{ fontSize: 11, margin: '6px 0 3px' }}>Entropy (hex, {qrng.n_bytes} B)</div>
                <pre className="mono qrng-hex">{qrng.entropy_hex}</pre>
                <details style={{ marginTop: 8 }}>
                  <summary className="muted" style={{ cursor: 'pointer', fontSize: 12 }}>Ed25519 public key + X.509 certificate (PEM)</summary>
                  <pre className="mono qrng-pem">{qrng.public_key_pem + '\n' + qrng.certificate_pem}</pre>
                </details>
              </div>
            )}
          </div>
    </>
  )
}