# api/main.py
"""FastAPI backend for the Quantum Solid State Chip (QSSC) dashboard.

Exposes the simulator, the Qiskit backend, the ODMR model and the fabrication
protocol as a small REST API consumed by the React frontend.  Run with:

    uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from qssc import (
    get_profile, coherence_at_temperature,
    auto_align, simulate_odmr, QSSCBackend,
    make_camera_frame, detect_defects, PROFILES,
)
from qssc.protocol import protocol_as_dict, protocol_summary
from api import jobs

app = FastAPI(
    title="QSSC — Quantum Solid State Chip (room temperature)",
    version="2.0.0",
    description="Simulator, Qiskit backend and fabrication-protocol API for a "
                "room-temperature hBN spin-defect quantum chip.",
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    algorithm: str = "shor"          # shor | period | bell | grover | math
    profile: str = "hBN-VB"
    temperature_k: float = 300.0
    shots: int = 2048
    seed: int = 42
    noise_mode: str = "real"   # real (default, full physics) | phenomenological | none
    # algorithm-specific
    n: int = 15                      # shor / period
    a: Optional[int] = None          # base (auto if None)
    b: Optional[int] = None          # math operand b (mapped to num2)
    bits: Optional[int] = None       # math word size (accepted, derived if None)
    op: str = "add"                  # math op
    num1: float = 2.0
    num2: Optional[float] = None
    num_qubits: int = 2              # bell / qft / qrand
    n_items: Optional[int] = None    # grover: search space = 2^n_items
    marked: Optional[int] = None     # grover: marked index
    iterations: int = 1              # grover
    # qft
    x: int = 1                       # qft: input computational-basis state |x>
    # qrand (quantum RNG -> cryptographic key + self-signed cert)
    num_bytes: int = 32              # qrand: key size in bytes (16..64)
    common_name: str = "QSSC-quantum-device"  # qrand: certificate CN
    # bb84 quantum key distribution
    num_bits: int = 128             # bb84: number of qubits/rounds to exchange
    eavesdrop: bool = False         # bb84: simulate intercept-resend Eve


class CustomCircuitRequest(BaseModel):
    code: str                        # OpenQASM 2 or Python snippet (defines `qc`)
    profile: str = "hBN-VB"
    temperature_k: float = 300.0
    shots: int = 2048
    seed: int = 42
    noise_mode: str = "real"   # real (default, full physics) | phenomenological | none
    max_qubits: int = 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _backend(profile: str, temperature_k: float, seed: int, noise_mode: str,
             grid: int = 8) -> QSSCBackend:
    sites = auto_align(profile=profile, grid=grid, temperature_k=temperature_k,
                       rng=np.random.default_rng(seed))
    return QSSCBackend(profile=profile, sites=sites, seed=seed,
                       temperature_k=temperature_k, noise_mode=noise_mode)


def _req_dict(model) -> dict:
    """Pydantic model -> plain dict (dropping unset None noise fields)."""
    d = model.model_dump()
    return {k: v for k, v in d.items() if v is not None}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "name": "QSSC", "version": "2.0.0"}


@app.get("/api/profiles")
def profiles():
    out = []
    for key, p in PROFILES.items():
        out.append({
            "key": key, "name": p.name, "short": p.short,
            "d_ghz": p.d_ghz, "excitation_nm": p.excitation_wavelength_nm,
            "t2_us": round(p.t2_us, 3), "t1_ms": round(p.t1_ms, 3),
            "readout_error": p.readout_error,
            "defect_density_per_um2": p.defect_density_per_um2,
            "notes": p.notes,
        })
    return out


@app.get("/api/backend/info")
def backend_info(profile: str = "hBN-VB", temperature_k: float = 300.0,
                 seed: int = 42):
    b = _backend(profile, temperature_k, seed, "none")
    desc = b.describe()
    desc["site_count"] = len(desc["sites"])
    desc["sites"] = desc["sites"][:64]
    return desc


@app.get("/api/odmr")
def odmr(profile: str = "hBN-VB", b_field: float = 1e-3,
         points: int = 400, linewidth: float = 5.0):
    prof = get_profile(profile)
    f0 = prof.d_ghz
    # adaptive span: wide enough to show the full hyperfine fingerprint
    # (e.g. V_B- 7-line spans +/-3*A), but never narrower than the Zeeman doublet
    span = max(0.08, abs(prof.hyperfine_coupling) / 1e9 * prof.n_equivalent_nuclei * 1.15 + 0.03)
    freqs = np.linspace(f0 - span, f0 + span, int(points))
    f, c = simulate_odmr(prof, freqs, b_field_t=b_field, linewidth_mhz=linewidth)
    dip = float(f[np.argmin(c)])
    return {
        "profile": prof.short, "d_ghz": prof.d_ghz,
        "b_field_t": b_field,
        "frequencies_ghz": [round(float(x), 5) for x in f],
        "contrast": [round(float(x), 5) for x in c],
        "resonance_ghz": round(dip, 5),
    }


@app.get("/api/protocol")
def protocol():
    return {"summary": protocol_summary(), "sections": protocol_as_dict()}


@app.get("/api/calibration")
def calibration(profile: str = "hBN-VB", grid: int = 8):
    from qssc.backend import CalibrationManager
    mapping = CalibrationManager.default_grid_mapping()
    sites = []
    for idx in range(grid * grid):
        r, c = divmod(idx, grid)
        x, y = mapping[(r, c)]
        sites.append({"qubit_id": idx, "row": r, "col": c, "pixel_x": x, "pixel_y": y})
    return {"grid": grid, "sites": sites}


@app.get("/api/signal")
def signal(profile: str = "hBN-VB", temperature_k: float = 300.0,
           b_field: float = 1e-3, detuning_mhz: float = 2.0,
           sequence: str = "ramsey", points: int = 240, seed: int = 42):
    """Analog detected-photoluminescence trace during a pulse sequence.

    Models the time-resolved fluorescence the on-chip 2D photodetector array
    would record, with real defect coherence (T2) and readout contrast:

    * ``ramsey``  -- PL(t) = A + B·cos(2π·Δ·t)·exp(-t/T2)  (Ramsey fringes)
    * ``odmr``    -- CW ODMR dip when the microwave is on resonance
    * ``pulse``   -- spin initialisation + readout decay curve

    Returns the time axis (ns), the detected signal (normalised PL) and the
    physics parameters used, so the dashboard can draw an oscilloscope-style
    analog trace grounded in the chip's real T2/readout.
    """
    prof = get_profile(profile)
    t1, t2 = coherence_at_temperature(prof, temperature_k)
    rng = np.random.default_rng(seed)
    seq = sequence.lower()
    readout_ns = prof.readout_ns
    if seq == "odmr":
        # total sweep window a few T2 long
        span_ns = max(8 * t2 * 1e9, 4000)
        t = np.linspace(0, span_ns, int(points))
        d_ghz = prof.d_ghz
        on_res = d_ghz + detuning_mhz / 1e3
        # contrast dips while mw is near resonance, recovers off-resonance
        lor = 1.0 / (1.0 + ((detuning_mhz) / 3.0) ** 2)
        sig = 1.0 - 0.35 * lor * np.exp(-t / (t2 * 1e9))
    elif seq == "pulse":
        span_ns = max(6 * readout_ns, 4000)
        t = np.linspace(0, span_ns, int(points))
        sig = (0.85 * np.exp(-t / readout_ns) + 0.15)
    else:  # ramsey
        span_ns = max(6 * t2 * 1e9, 4000)
        t = np.linspace(0, span_ns, int(points))
        sig = (0.5 + 0.45 * np.cos(2 * np.pi * detuning_mhz * t / 1e3)
               * np.exp(-t / (t2 * 1e9)))
    # detector shot noise + readout contrast
    noise = rng.normal(0, 0.012, size=sig.shape)
    sig = np.clip(sig + noise, 0, 1.05)
    return {
        "profile": prof.short, "sequence": seq,
        "temperature_k": temperature_k,
        "t2_us": round(t2 * 1e6, 4), "readout_ns": readout_ns,
        "d_ghz": prof.d_ghz, "detuning_mhz": detuning_mhz,
        "time_ns": [round(float(x), 1) for x in t],
        "signal": [round(float(x), 5) for x in sig],
    }


@app.post("/api/calibration/run")
def calibration_run(profile: str = "hBN-VB", temperature_k: float = 300.0,
                    seed: int = 42, grid: int = 8, b_field: float = 1e-3,
                    points: int = 220):
    """Run the full calibration process the way the VABISMO software does.

    Stages: (1) capture a fluorescence frame and detect defects, (2) auto-align
    the qubit grid to the detected sites, (3) sweep the microwave frequency to
    record an ODMR spectrum and fit the zero-field splitting D + linewidth.
    Returns every stage's data so the dashboard can render the calibration.
    """
    prof = get_profile(profile)
    rng = np.random.default_rng(seed)

    # Stage 1 — defect detection on a simulated camera frame
    shape = (96, 128)
    centres = [{"x": c * 16 + 8, "y": r * 12 + 6, "intensity": 170.0}
               for r in range(grid) for c in range(grid)]
    frame = make_camera_frame(centres, shape=shape,
                              fluorescence_factor=prof.fluorescence_factor)
    defects = detect_defects(frame, threshold=50)
    n_detected = len(defects)

    # Stage 2 — auto-align grid
    sites = auto_align(prof, grid=grid, temperature_k=temperature_k, rng=rng)
    aligned = [{"qubit_id": s.qubit_id, "row": s.row, "col": s.col,
                "x_um": round(s.x_um, 2), "y_um": round(s.y_um, 2),
                "intensity": round(s.intensity, 1)} for s in sites]

    # Stage 3 — ODMR sweep + fit D. With a non-zero B field the spectrum
    # splits into a Zeeman doublet at D +/- gamma*B; the zero-field splitting
    # D is the *midpoint* of the two dips, not the single argmin (which would
    # be offset by gamma*B and mis-flag the fit).
    f0 = prof.d_ghz
    span = max(0.08, abs(prof.hyperfine_coupling) / 1e9 * prof.n_equivalent_nuclei * 1.15 + 0.03)
    freqs = np.linspace(f0 - span, f0 + span, int(points))
    f, c = simulate_odmr(prof, freqs, b_field_t=b_field, linewidth_mhz=5.0)
    dip_idx = int(np.argmin(c))
    # find the partner dip on the opposite side of D
    if f[dip_idx] < f0:
        partner = np.where(f > f0)[0]
    else:
        partner = np.where(f < f0)[0]
    if len(partner) > 3:
        p_idx = int(partner[np.argmin(c[partner])])
        d_fit = float((f[dip_idx] + f[p_idx]) / 2.0)
    else:
        d_fit = float(f[dip_idx])
    # estimate linewidth (FWHM) from the half-minimum width
    half = (max(c) + min(c)) / 2.0
    inside = np.where(c < half)[0]
    fwhm = float(f[inside[-1]] - f[inside[0]]) if len(inside) > 2 else 0.01

    t1, t2 = coherence_at_temperature(prof, temperature_k)
    return {
        "profile": prof.short, "temperature_k": temperature_k,
        "stages": [
            {"name": "1 · Detect defects (camera frame)",
             "frame_shape": list(frame.shape),
             "defects_detected": n_detected,
             "frame_mean": round(float(frame.mean()), 2),
             "frame_max": round(float(frame.max()), 2)},
            {"name": "2 · Auto-align qubit grid",
             "sites_aligned": len(aligned), "sites": aligned},
            {"name": "3 · ODMR sweep + fit D",
             "frequencies_ghz": [round(float(x), 5) for x in f],
             "contrast": [round(float(x), 5) for x in c],
             "d_fit_ghz": round(d_fit, 5), "d_expected_ghz": prof.d_ghz,
             "linewidth_fwhm_ghz": round(fwhm, 5),
             "fit_ok": abs(d_fit - prof.d_ghz) < 0.03},
        ],
        "t1_ms": round(t1 * 1e3, 3), "t2_us": round(t2 * 1e6, 3),
    }


@app.post("/api/circuit/run")
def run_circuit(req: RunRequest, sync: bool = False):
    """Enqueue a demo-algorithm circuit run as an async job (Redis + RQ).

    Returns ``{job_id, status}``; poll ``GET /api/jobs/{job_id}`` for the
    result. Pass ``?sync=true`` to run synchronously in-process (handy for
    tests / quick CLI use).
    """
    payload = _req_dict(req)
    payload.setdefault("noise_mode", "real")
    payload.setdefault("temperature_k", 300.0)
    if sync:
        from api.runner import run_circuit as _run
        return {"job_id": None, "status": "finished", "result": _run(payload)}
    job_id = jobs.enqueue("circuit", payload)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/circuit/custom")
def run_custom_circuit(req: CustomCircuitRequest, sync: bool = False):
    """Enqueue a user-supplied Qiskit circuit (OpenQASM 2 or Python snippet)
    as an async job. The snippet must build a ``QuantumCircuit`` named ``qc``
    (or ``circuit``) with the restricted primitives in
    :func:`build_user_circuit`. Local lab / demo tool only.
    """
    payload = _req_dict(req)
    payload.setdefault("noise_mode", "real")
    payload.setdefault("temperature_k", 300.0)
    payload["algorithm"] = "custom"
    if sync:
        from api.runner import run_custom as _run
        return {"job_id": None, "status": "finished", "result": _run(payload)}
    job_id = jobs.enqueue("custom", payload)
    return {"job_id": job_id, "status": "queued"}


# --- async job polling + history -------------------------------------------

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    rec = jobs.get_job(job_id)
    if rec is None:
        return {"job_id": job_id, "status": "unknown"}
    return rec


@app.get("/api/jobs")
def list_jobs(limit: int = 50):
    return jobs.list_jobs(limit=limit)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    return {"deleted": jobs.delete_job(job_id)}


# --- saved Qiskit scripts (Code tab) ----------------------------------------

class ScriptRequest(BaseModel):
    name: str
    code: str
    id: Optional[str] = None


@app.get("/api/scripts")
def list_scripts():
    return jobs.list_scripts()


@app.post("/api/scripts")
def save_script(req: ScriptRequest):
    rec = jobs.save_script(req.name, req.code, req.id)
    return rec


@app.delete("/api/scripts/{sid}")
def delete_script(sid: str):
    return {"deleted": jobs.delete_script(sid)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)