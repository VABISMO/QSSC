import React from 'react'
import { useCircuit } from '../circuitContext.js'
import GlobalControls from '../panels/GlobalControls.jsx'
import CircuitRunner from '../panels/CircuitRunner.jsx'
import BackendPhysics from '../panels/BackendPhysics.jsx'
import Spectroscopy from '../panels/Spectroscopy.jsx'
import AnalogSignal from '../panels/AnalogSignal.jsx'

export default function HardwareTab() {
  const { circuitsSub, setCircuitsSub } = useCircuit()
  return (
    <>
          {/* ============================= HARDWARE (default) ============================= */}
          {/* Visual subtabs within Circuits (replaces the section-title
              timeline): Circuit runner / Backend physics / Spectroscopy &
              calibration / Detected signal / Settings. Corporate line icons
              per tab. The Settings tab (last) holds the global controls card
              (64-parallel toggle, environment, defect platform, temperature,
              execution mode, RNG seed, pixel mode, prime denoise). */}
          <nav className="subtabs" role="tablist" aria-label="Circuits sections">
            {[
              { k: 'runner', label: 'Circuit runner', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="7" y="7" width="10" height="10" rx="1.5" /><path d="M10 7V4M14 7V4M10 20v-3M14 20v-3M7 10H4M7 14H4M20 10h-3M20 14h-3" /></svg> },
              { k: 'physics', label: 'Backend physics', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" /><ellipse cx="12" cy="12" rx="9" ry="3.6" /><ellipse cx="12" cy="12" rx="9" ry="3.6" transform="rotate(60 12 12)" /><ellipse cx="12" cy="12" rx="9" ry="3.6" transform="rotate(120 12 12)" /></svg> },
              { k: 'spectro', label: 'Spectroscopy & calibration', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3 9h6l2 7 2-12 2 6h6" /></svg> },
              { k: 'signal', label: 'Detected signal', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12h4V6h6v12h6V9h2" /></svg> },
              { k: 'settings', label: 'Settings', icon: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg> },
            ].map(t => (
              <button key={t.k} role="tab" type="button" aria-selected={circuitsSub === t.k}
                className={`subtab ${circuitsSub === t.k ? 'on' : ''}`} onClick={() => setCircuitsSub(t.k)}>
                <span className="subtab-ic">{t.icon}</span>
                <span className="subtab-lb">{t.label}</span>
              </button>
            ))}
          </nav>
          {circuitsSub === 'settings' && <GlobalControls />}

          {circuitsSub === 'runner' && <CircuitRunner />}

          {circuitsSub === 'physics' && <BackendPhysics />}

          {circuitsSub === 'spectro' && <Spectroscopy />}

          {circuitsSub === 'signal' && <AnalogSignal />}

          <footer style={{ marginTop: 30, color: 'var(--dim)', fontSize: 12.5, textAlign: 'center' }}>
            QSSC — Quantum Solid State Chip · v2.0.0 · NV-diamond / hBN V_B⁻ / SiC V_Si ·
            Qiskit BackendV2 + Artix-7 FPGA control stack · async jobs (Redis/RQ) · history (MongoDB)
          </footer>
    </>
  )
}