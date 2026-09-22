import React from 'react'
import { useCircuit } from '../circuitContext.js'
import { CompassRose, GyroPanel, MagnetoImage, SensorField, TensorPanel } from '../components.jsx'

export default function SensorsTab() {
  const {
    hwMode, setHwMode,
    sensorsSub, setSensorsSub,
    compass, compassLoading, compassErr,
    compassBx, setCompassBx, compassBy, setCompassBy, compassBz, setCompassBz,
    compassAvg, setCompassAvg, runCompass,
    gyro, gyroLoading, gyroErr, gyroRate, setGyroRate, gyroAvg, setGyroAvg, runGyro,
    mag, magLoading, magErr, magAvg, setMagAvg, magGrad, setMagGrad, runMag,
    ferro, ferroLoading, ferroErr,
    ferroMoment, setFerroMoment, ferroDist, setFerroDist,
    ferroOffX, setFerroOffX, ferroOffY, setFerroOffY, runFerro,
    nav, navLoading, navErr,
    navPosX, setNavPosX, navPosY, setNavPosY, navAnom, setNavAnom, runNav,
    thermal, thermalLoading, thermalErr,
    thermalBase, setThermalBase, thermalHot, setThermalHot,
    thermalHX, setThermalHX, thermalHY, setThermalHY, runThermal,
    current, currentLoading, currentErr,
    currentI, setCurrentI, currentDepth, setCurrentDepth, currentOff, setCurrentOff, runCurrent,
    fieldAvg, setFieldAvg,
    tensor, tensorLoading, tensorErr,
    tensorMoment, setTensorMoment, tensorX, setTensorX, tensorY, setTensorY,
    tensorZ, setTensorZ, tensorBg, setTensorBg, tensorAvg, setTensorAvg, runTensor,
  } = useCircuit()
  return (
    <>
          {/* ============================= SENSORS (on-chip quantum sensors) ============================= */}
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
                : 'real ADF4351 + 64-ch AD7616 chain — compass from live ODMR; gyro / lock-in / sequence measured (RUN_SEQUENCE + on-host fit)'}
            </span>
          </div>

          {/* Visual subtabs within Sensors (replaces the section-title
              timeline): Quantum sensors (compass/gyro/magneto) / Field
              imaging (ferro/nav/thermal/current). Corporate line icons. */}
          <nav className="subtabs" role="tablist" aria-label="Sensors sections">
            {[
              { k: 'quantum', label: 'Quantum sensors', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M15.5 8.5l-2 5-5 2 2-5 5-2z" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /></svg> },
              { k: 'field', label: 'Field imaging', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M5 8a3.5 3.5 0 017 0v8a3.5 3.5 0 11-7 0V8z" /><path d="M12 8a3.5 3.5 0 017 0v8a3.5 3.5 0 11-7 0V8z" /></svg> },
            ].map(t => (
              <button key={t.k} role="tab" type="button" aria-selected={sensorsSub === t.k}
                className={`subtab ${sensorsSub === t.k ? 'on' : ''}`} onClick={() => setSensorsSub(t.k)}>
                <span className="subtab-ic">{t.icon}</span>
                <span className="subtab-lb">{t.label}</span>
              </button>
            ))}
          </nav>
          {sensorsSub === 'quantum' && (<>
          <div className="section-title">On-chip quantum sensors — compass · gyroscope · magneto-optic image</div>
          <div className="hint" style={{ marginBottom: 14, color: 'var(--dim)', fontSize: 12.5 }}>
            The same ADF4351 → J3 → A1 MW chain and 64-channel AD7616 readout run as quantum sensors.
            Sweep the 4 NV &lt;111&gt; axes for the ODMR compass (B-vector → heading), run a Ramsey
            π/2–T–π/2 for the gyroscope, and image the per-pixel field across the 8×8 array.
            Opening this tab auto-captures an initial reading on all three; toggle the environment
            above (or press Measure) to re-run.
          </div>
          <div className="sensor-grid">
            <div className="card">
              <h2><span className="dot" />ODMR compass (magnetometer)</h2>
              <div className="hint">Zeeman doublet split per &lt;111&gt; axis reconstructs the ambient B-vector → heading + elevation. Default is a ~188 µT lab field (resolves ≥3 axes at the NV ~5 MHz linewidth); dial to Earth's field to see the linewidth limit.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Bx (µT)</label><input type="number" step="any" value={compassBx} onChange={e => setCompassBx(+e.target.value)} /></div>
                <div className="field"><label>By (µT)</label><input type="number" step="any" value={compassBy} onChange={e => setCompassBy(+e.target.value)} /></div>
                <div className="field"><label>Bz (µT)</label><input type="number" step="any" value={compassBz} onChange={e => setCompassBz(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={compassAvg} onChange={e => setCompassAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runCompass} disabled={compassLoading} style={{ alignSelf: 'flex-end' }}>
                  {compassLoading ? 'Sweeping…' : 'Measure'}
                </button>
              </div>
              <CompassRose data={compass} error={compassErr} loading={compassLoading} />
            </div>

            <div className="card">
              <h2><span className="dot" />Ramsey gyroscope{hwMode === 'hardware' && <span className="pill" style={{ marginLeft: 8, fontSize: 10.5, color: 'var(--good)' }}>measured · RUN_SEQUENCE + on-host fit</span>}</h2>
              <div className="hint">π/2–T–π/2; the accumulated phase Ω·T is the rotation rate.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Rate (°/s)</label><input type="number" step="any" value={gyroRate} onChange={e => setGyroRate(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={gyroAvg} onChange={e => setGyroAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runGyro} disabled={gyroLoading} style={{ alignSelf: 'flex-end' }}>
                  {gyroLoading ? 'Running…' : 'Measure'}
                </button>
              </div>
              <GyroPanel data={gyro} error={gyroErr} loading={gyroLoading} />
            </div>

            <div className="card" style={{ gridColumn: '1 / -1' }}>
              <h2><span className="dot" />Magneto-optic image (64-pixel array)</h2>
              <div className="hint">The FPGA returns all 64 AD7616 samples in one parallel frame; each pixel's ODMR doublet gives |B·n| → an 8×8 field image. Tune the gradient + averages and watch the sensitivity and structure resolve.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Gradient (µT/mm)</label><input type="number" step="any" value={magGrad} onChange={e => setMagGrad(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={magAvg} onChange={e => setMagAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runMag} disabled={magLoading} style={{ alignSelf: 'flex-end' }}>
                  {magLoading ? 'Reading array…' : 'Image'}
                </button>
              </div>
              <MagnetoImage data={mag} error={magErr} loading={magLoading} />
            </div>
          </div>
          </>)}

          {/* =================== CONTACTLESS FIELD IMAGING =================== */}
          {sensorsSub === 'field' && (<>
          <div className="section-title">Contactless field imaging — tensor gradiometer · ferromagnetic · geomagnetic nav · temperature · current</div>
          <div className="hint" style={{ marginBottom: 14, color: 'var(--dim)', fontSize: 12.5 }}>
            Magnetic fields penetrate fog, smoke, water, tissue, packaging and non-magnetic walls — so the same
            64-pixel array images the source through whatever is in the way. Each card models a different source:
            a tensor-gradient dipole fit, a ferromagnetic dipole (object detection), the crustal
            anomaly field (GPS-denied navigation), a temperature field (NV D-shift thermometry), or a current (Ampère).
          </div>
          <div className="card">
            <h2><span className="dot" />Tensor gradiometer + dipole localization</h2>
            <div className="hint">Each pixel recovers the full B-VECTOR from its 4 NV ⟨111⟩ Zeeman doublets; the 8×8 map's per-pixel 3×2 gradient tensor (Jacobian) is the tensor image. A baseline sweep is subtracted (differential mode) so a weak dipole surfaces against the DC background, and the source is fit to the raw doublet splits — sign-ambiguity-free. ● fitted source.</div>
            <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
              <div className="field"><label>Moment (A·m²)</label><input type="number" step="any" value={tensorMoment} onChange={e => setTensorMoment(+e.target.value)} /></div>
              <div className="field"><label>Source X (mm)</label><input type="number" step="any" value={tensorX} onChange={e => setTensorX(+e.target.value)} /></div>
              <div className="field"><label>Source Y (mm)</label><input type="number" step="any" value={tensorY} onChange={e => setTensorY(+e.target.value)} /></div>
              <div className="field"><label>Depth Z (mm)</label><input type="number" step="any" value={tensorZ} onChange={e => setTensorZ(+e.target.value)} /></div>
              <div className="field"><label>Bz bias (µT)</label><input type="number" step="any" value={tensorBg} onChange={e => setTensorBg(+e.target.value)} /></div>
              <div className="field"><label>Averages</label><input type="number" value={tensorAvg} onChange={e => setTensorAvg(+e.target.value)} /></div>
              <button className="btn" onClick={runTensor} disabled={tensorLoading} style={{ alignSelf: 'flex-end' }}>{tensorLoading ? 'Sweeping…' : 'Image tensor'}</button>
            </div>
            <TensorPanel data={tensor} error={tensorErr} loading={tensorLoading} />
          </div>
          <div className="sensor-grid">
            <div className="card">
              <h2><span className="dot" />Ferromagnetic detection (see through walls)</h2>
              <div className="hint">A ferromagnetic object is a magnetic dipole; the array images its field through packaging, walls, tissue, soil. Detection fires when the peak clears the noise floor; bearing + distance locate it.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Moment (A·m²)</label><input type="number" step="any" value={ferroMoment} onChange={e => setFerroMoment(+e.target.value)} /></div>
                <div className="field"><label>Distance (mm)</label><input type="number" step="any" value={ferroDist} onChange={e => setFerroDist(+e.target.value)} /></div>
                <div className="field"><label>Offset X (mm)</label><input type="number" step="any" value={ferroOffX} onChange={e => setFerroOffX(+e.target.value)} /></div>
                <div className="field"><label>Offset Y (mm)</label><input type="number" step="any" value={ferroOffY} onChange={e => setFerroOffY(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={fieldAvg} onChange={e => setFieldAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runFerro} disabled={ferroLoading} style={{ alignSelf: 'flex-end' }}>{ferroLoading ? 'Sweeping…' : 'Measure'}</button>
              </div>
              <SensorField data={ferro} error={ferroErr} loading={ferroLoading} promptText="Press Measure to image the dipole field." />
            </div>

            <div className="card">
              <h2><span className="dot" />Geomagnetic navigation (GPS-denied)</h2>
              <div className="hint">Match the local crustal-magnetic anomaly against a stored map to estimate position — works underwater / underground / indoors where GPS and optical both fail. ● true position, ◆ matched.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Pos X</label><input type="number" step="any" value={navPosX} onChange={e => setNavPosX(+e.target.value)} /></div>
                <div className="field"><label>Pos Y</label><input type="number" step="any" value={navPosY} onChange={e => setNavPosY(+e.target.value)} /></div>
                <div className="field"><label>Anomaly (µT)</label><input type="number" step="any" value={navAnom} onChange={e => setNavAnom(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={fieldAvg} onChange={e => setFieldAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runNav} disabled={navLoading} style={{ alignSelf: 'flex-end' }}>{navLoading ? 'Matching…' : 'Navigate'}</button>
              </div>
              <SensorField data={nav} error={navErr} loading={navLoading} promptText="Press Navigate to match the anomaly map." />
            </div>

            <div className="card">
              <h2><span className="dot" />Temperature imaging (NV thermometry)</h2>
              <div className="hint">The NV D resonance shifts with temperature, so the array is a contactless thermal image — a chip hotspot or a surface's map read through the diamond. Resolution scales with averages.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Base (K)</label><input type="number" step="any" value={thermalBase} onChange={e => setThermalBase(+e.target.value)} /></div>
                <div className="field"><label>Hotspot (K)</label><input type="number" step="any" value={thermalHot} onChange={e => setThermalHot(+e.target.value)} /></div>
                <div className="field"><label>Hotspot X</label><input type="number" step="any" value={thermalHX} onChange={e => setThermalHX(+e.target.value)} /></div>
                <div className="field"><label>Hotspot Y</label><input type="number" step="any" value={thermalHY} onChange={e => setThermalHY(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={fieldAvg} onChange={e => setFieldAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runThermal} disabled={thermalLoading} style={{ alignSelf: 'flex-end' }}>{thermalLoading ? 'Reading…' : 'Measure'}</button>
              </div>
              <SensorField data={thermal} error={thermalErr} loading={thermalLoading} promptText="Press Measure to image the temperature field." />
            </div>

            <div className="card">
              <h2><span className="dot" />Current imaging (Ampère, through enclosures)</h2>
              <div className="hint">Every current makes a B field (µ₀I / 2πr); the array images it contactlessly — short-circuit localization, battery-cell current signatures, chip power-rail mapping, all through the enclosure.</div>
              <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div className="field"><label>Current (A)</label><input type="number" step="any" value={currentI} onChange={e => setCurrentI(+e.target.value)} /></div>
                <div className="field"><label>Depth (mm)</label><input type="number" step="any" value={currentDepth} onChange={e => setCurrentDepth(+e.target.value)} /></div>
                <div className="field"><label>Wire offset (mm)</label><input type="number" step="any" value={currentOff} onChange={e => setCurrentOff(+e.target.value)} /></div>
                <div className="field"><label>Averages</label><input type="number" value={fieldAvg} onChange={e => setFieldAvg(+e.target.value)} /></div>
                <button className="btn" onClick={runCurrent} disabled={currentLoading} style={{ alignSelf: 'flex-end' }}>{currentLoading ? 'Reading…' : 'Measure'}</button>
              </div>
              <SensorField data={current} error={currentErr} loading={currentLoading} promptText="Press Measure to image the current field." />
            </div>
          </div>
          </>)}
    </>
  )
}