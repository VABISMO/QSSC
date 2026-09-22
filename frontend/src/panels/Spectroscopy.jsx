import React from 'react'
import { useCircuit } from '../circuitContext.js'
import {
  OdmrChart, CalibrationPanel, CalibrationResults,
  ParallelCalibCard, ParallelCalibResults,
} from '../components.jsx'
import { ANTI_NOISE_PRESETS } from '../constants.js'

export default function Spectroscopy() {
  const {
    parallelMode,
    runCalibration, calRunning, calRun, calErr,
    odmr,
    parGrad, setParGrad, parPixelSize, setParPixelSize,
    hwMode, profile, temperature, seed,
    calibrateArray, arrCalibRunning,
    arrCalib, arrCalibErr,
  } = useCircuit()
  return (
    <>
          {/* ============================= SPECTROSCOPY + CALIBRATION ============================= */}
          {/* 64-parallel OFF = single-pixel: ODMR left, calibration right.
              64-parallel ON = 64-array: FORM left, RESULTS + ODMR spectrum
              stacked right (ODMR below the resonance-map button). Uses the
              page's own two-column grid — no nested split inside one cell. */}
          {!parallelMode ? (
          <div className="grid cols-2">
            {/* LEFT column: the calibration form */}
            <div className="card">
              <h2><span className="dot" />Calibration process</h2>
              <div className="hint">Set the gradient DAC code, then run the 4-stage calibration. Results and the ODMR spectrum appear on the right.</div>
              <CalibrationPanel
                run={runCalibration} running={calRunning}
              />
            </div>
            {/* RIGHT column: measured results, then the ODMR spectrum below */}
            <div>
              <CalibrationResults result={calRun} err={calErr} />
              <div className="card" style={{ marginTop: 16 }}>
                <h2><span className="dot" />ODMR spectrum</h2>
                <div className="hint">Lock-in ODMR — the on-chip graphene/MoS₂ detector array's photocurrent dips at D ± γB (resonant MW flips the spin dark), with hyperfine replicas.</div>
                <OdmrChart data={odmr.data} error={odmr.error} />
              </div>
            </div>
          </div>
          ) : (
          <div className="grid cols-2">
            {/* LEFT column: the calibration form */}
            <ParallelCalibCard
              grad={parGrad} setGrad={setParGrad}
              pixelSize={parPixelSize} setPixelSize={setParPixelSize}
              source={hwMode === 'hardware' ? 'real' : 'sim'}
              profile={profile} temperature={temperature} seed={seed}
              onCalibrate={calibrateArray} calibRunning={arrCalibRunning}
              antiNoisePresets={ANTI_NOISE_PRESETS}
            />
            {/* RIGHT column: measured results, then the ODMR spectrum below */}
            <div>
              <ParallelCalibResults calibResult={arrCalib} calibErr={arrCalibErr} />
              <div className="card" style={{ marginTop: 16 }}>
                <h2><span className="dot" />ODMR spectrum</h2>
                <div className="hint">Lock-in ODMR — the on-chip graphene/MoS₂ detector array's photocurrent dips at D ± γB (resonant MW flips the spin dark), with hyperfine replicas.</div>
                <OdmrChart data={odmr.data} error={odmr.error} />
              </div>
            </div>
          </div>
          )}
    </>
  )
}