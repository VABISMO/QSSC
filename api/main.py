# api/main.py
"""FastAPI backend for the Quantum Solid State Chip (QSSC) dashboard.

Exposes the simulator, the Qiskit backend, the ODMR model and the fabrication
protocol as a small REST API consumed by the React frontend.  Run with:

    uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from qssc import (
    get_profile, coherence_at_temperature,
    auto_align, simulate_odmr, QSSCBackend,
    make_detector_response, PROFILES,
    compass_measure, gyro_measure,
)
from qssc.simulator import DETECT_THRESHOLD
from qssc.protocol import (
    protocol_as_dict, protocol_summary, protocol_platforms,
)
from api import jobs

# ---------------------------------------------------------------------------
# Deploy configuration (env-driven). Defaults keep the API functional for a
# local lab / demo; a public deploy should narrow these (see render.yaml).
# ---------------------------------------------------------------------------
# The custom-circuit endpoint execs user-supplied Qiskit code behind an AST
# sandbox that is intentionally documented as non-absolute. Keep it enabled
# locally; disable on hardened public deploys with QSSC_ENABLE_CUSTOM_CIRCUIT=0.
CUSTOM_CIRCUIT_ENABLED = os.getenv("QSSC_ENABLE_CUSTOM_CIRCUIT", "1").lower() in (
    "1", "true", "yes",
)
# Optional shared secret gating the custom-circuit endpoint. If set, clients
# must send `X-API-Key: <key>`; if unset the endpoint is open (subject to the
# flag above and the resource caps in the request models).
CUSTOM_CIRCUIT_API_KEY = os.getenv("QSSC_CUSTOM_API_KEY", "").strip()
# Allow ?sync=true from non-localhost (it runs the circuit in the request
# thread and blocks the single uvicorn worker on a free-tier host). Off by
# default: sync stays available to localhost / tests only.
ALLOW_SYNC_REMOTE = os.getenv("QSSC_ALLOW_SYNC", "").lower() in ("1", "true", "yes")
# CORS origins, comma-separated. "*" by default for local dev; a public deploy
# sets QSSC_CORS_ORIGINS to the dashboard origin (e.g. https://vabismo.github.io).
_cors_env = os.getenv("QSSC_CORS_ORIGINS", "*").strip()
CORS_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()] or ["*"]
# Simple per-IP rate limit on the expensive circuit endpoints. In-memory, so
# per-worker (Render free runs a single worker). Caps flooding, not a guarantee.
RATE_LIMIT_PER_MIN = int(os.getenv("QSSC_RATE_LIMIT_PER_MIN", "20"))
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)

app = FastAPI(
    title="QSSC — Quantum Solid State Chip (room temperature)",
    version="2.0.0",
    description="Simulator, Qiskit backend and fabrication-protocol API for a "
                "room-temperature NV-diamond spin-defect quantum chip (on-chip "
                "CQD emitter + graphene detector readout, no camera).",
)
app.add_middleware(
    CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    algorithm: str = "shor"          # shor | period | bell | grover | math
    profile: str = "NV"
    temperature_k: float = Field(300.0, ge=0.001, le=600)
    shots: int = Field(2048, ge=1, le=8192)
    seed: int = Field(42, ge=0, le=2_147_483_647)
    noise_mode: str = "real"   # real (default, full physics) | phenomenological | none
    # algorithm-specific
    n: int = Field(15, ge=2, le=100_000)            # shor / period
    a: Optional[int] = None          # base (auto if None)
    b: Optional[int] = None          # math operand b (mapped to num2)
    bits: Optional[int] = None       # math word size (accepted, derived if None)
    op: str = "add"                  # math op
    num1: float = 2.0
    num2: Optional[float] = None
    num_qubits: int = Field(2, ge=1, le=64)         # bell / qft / qrand (full 64-site chip via MPS)
    n_items: Optional[int] = None    # grover: search space = 2^n_items
    marked: Optional[int] = None     # grover: marked index
    iterations: int = Field(1, ge=1, le=100)        # grover
    # qft
    x: int = 1                       # qft: input computational-basis state |x>
    # qrand (quantum RNG -> cryptographic key + self-signed cert)
    num_bytes: int = Field(32, ge=1, le=64)        # qrand: key size in bytes
    common_name: str = "QSSC-quantum-device"  # qrand: certificate CN
    # bb84 quantum key distribution
    num_bits: int = Field(128, ge=1, le=2048)      # bb84: qubits/rounds to exchange
    eavesdrop: bool = False         # bb84: simulate intercept-resend Eve


class CustomCircuitRequest(BaseModel):
    code: str                        # OpenQASM 2 or Python snippet (defines `qc`)
    profile: str = "NV"
    temperature_k: float = Field(300.0, ge=0.001, le=600)
    shots: int = Field(2048, ge=1, le=4096)
    seed: int = Field(42, ge=0, le=2_147_483_647)
    noise_mode: str = "real"   # real (default, full physics) | phenomenological | none
    # Cap at 20 qubits: the chip has 64 physical sites but the circuit is
    # simulated classically (Aer statevector / density matrix, O(2^n) memory).
    # 20 qubits is a safe statevector size (~16 MB) on the free Render worker;
    # circuits wider than the backend's SIM_MAX_QUBITS (28) are rejected with a
    # clear error rather than silently faked. See qssc.backend.SIM_MAX_QUBITS.
    max_qubits: int = Field(20, ge=1, le=20)


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


# --- request guards (rate limit, sync, custom-circuit gate) ----------------

def _client_ip(request: Request) -> str:
    # trust the first hop only when a proxy (Render) sets the header
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(request: Request) -> bool:
    """True if the caller exceeded RATE_LIMIT_PER_MIN requests in the last 60s."""
    now = time.monotonic()
    bucket = _rate_buckets[_client_ip(request)]
    bucket.append(now)
    while bucket and now - bucket[0] > 60.0:
        bucket.popleft()
    return len(bucket) > RATE_LIMIT_PER_MIN


def _is_local(request: Request) -> bool:
    # "testclient" is the Starlette TestClient connection host (tests); it is
    # the connection peer, not a spoofable header, so it is safe to treat as
    # local. Behind Render's proxy request.client.host is the proxy, so real
    # public callers are never local here — sync stays blocked for them.
    return (request.client.host if request.client else "") in (
        "127.0.0.1", "::1", "localhost", "testclient",
    )


def _check_sync(request: Request, sync: bool) -> None:
    """Block ?sync=true from non-localhost: it runs in the request thread and
    blocks the single uvicorn worker on a free-tier host (DoS vector)."""
    if request is None:
        return  # programmatic call (no HTTP context) — caller takes responsibility
    if sync and not _is_local(request) and not ALLOW_SYNC_REMOTE:
        raise HTTPException(
            403, "?sync=true is only allowed from localhost on a public deploy "
                 "(it blocks the single API worker).",
        )


def _check_custom_circuit(request: Request) -> None:
    """Gate the custom-circuit endpoint: disabled flag + optional API key."""
    if not CUSTOM_CIRCUIT_ENABLED:
        raise HTTPException(
            503, "The custom-circuit endpoint is disabled on this deployment "
                 "(QSSC_ENABLE_CUSTOM_CIRCUIT=0).",
        )
    if CUSTOM_CIRCUIT_API_KEY and request.headers.get("x-api-key", "") != CUSTOM_CIRCUIT_API_KEY:
        raise HTTPException(
            401, "A valid X-API-Key header is required for the custom-circuit endpoint.",
        )


def _clamp_points(points: int, lo: int = 1, hi: int = 2000) -> int:
    """Clamp a resolution/points query param to a safe range (bounds the array
    allocations behind /api/odmr, /api/signal, /api/calibration/run)."""
    try:
        n = int(points)
    except (TypeError, ValueError):
        n = lo
    return max(lo, min(n, hi))


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
def backend_info(profile: str = "NV", temperature_k: float = 300.0,
                 seed: int = 42):
    b = _backend(profile, temperature_k, seed, "none")
    desc = b.describe()
    desc["site_count"] = len(desc["sites"])
    desc["sites"] = desc["sites"][:64]
    return desc


@app.get("/api/odmr")
def odmr(profile: str = "NV", b_field: float = 1e-3,
         points: int = 400, linewidth: float | None = None):
    prof = get_profile(profile)
    f0 = prof.d_ghz
    # adaptive span: wide enough to show the full hyperfine fingerprint
    # (e.g. V_B- 7-line spans +/-3*A), but never narrower than the Zeeman doublet
    span = max(0.08, abs(prof.hyperfine_coupling) / 1e9 * prof.n_equivalent_nuclei * 1.15 + 0.03)
    points = _clamp_points(points)
    freqs = np.linspace(f0 - span, f0 + span, points)
    # linewidth defaults to the profile's ODMR linewidth (NV ~5 MHz, SiC ~1.5 MHz)
    # unless the caller overrides it explicitly.
    lw = linewidth if linewidth is not None else prof.odmr_linewidth_hz / 1e6
    f, c = simulate_odmr(prof, freqs, b_field_t=b_field, linewidth_mhz=lw)
    dip = float(f[np.argmin(c)])
    return {
        "profile": prof.short, "d_ghz": prof.d_ghz,
        "b_field_t": b_field,
        "frequencies_ghz": [round(float(x), 5) for x in f],
        "contrast": [round(float(x), 5) for x in c],
        "resonance_ghz": round(dip, 5),
    }


@app.get("/api/protocol")
def protocol(platform: str = "NV"):
    """Fabrication protocol for a given spin-defect platform.

    ``platform`` matches the simulator profile keys (NV / hBN-VB / SiC-VSi).
    Defaults to the canonical NV-diamond compute chip.
    """
    return {"summary": protocol_summary(platform),
            "sections": protocol_as_dict(platform)}


@app.get("/api/protocol/platforms")
def protocol_variants():
    """List the available fabrication-protocol variants."""
    return {"platforms": protocol_platforms()}


@app.get("/api/calibration")
def calibration(profile: str = "NV", grid: int = 8):
    """Default emitter-channel -> qubit-site -> detector-electrode LUT (the
    identity mapping used before a physical calibration sweep is run). The
    on-chip readout has no camera pixels; each of the 64 sites is identified
    by its emitter channel index and the graphene detector electrode it drives
    (1:1 identity on this layout)."""
    from qssc.backend import CalibrationManager
    lut = CalibrationManager.default_detector_lut()
    sites = []
    for idx in range(64):
        r, c = divmod(idx, grid)
        sites.append({"qubit_id": idx, "row": r, "col": c,
                      "channel": idx, "electrode": lut.get(idx, idx)})
    return {"grid": grid, "channels": 64, "sites": sites}


@app.get("/api/signal")
def signal(profile: str = "NV", temperature_k: float = 300.0,
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
    points = _clamp_points(points)
    if seq == "odmr":
        # total sweep window a few T2 long
        span_ns = max(8 * t2 * 1e9, 4000)
        t = np.linspace(0, span_ns, points)
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
def calibration_run(profile: str = "NV", temperature_k: float = 300.0,
                    seed: int = 42, grid: int = 8, b_field: float = 1e-3,
                    points: int = 220):
    """Run the full calibration process the way the VABISMO software does.

    Stages: (1) scan the on-chip detector array for defect photocurrent
    (lock-in), (2) auto-align the qubit grid to the detected sites, (3) sweep
    the microwave frequency to record an ODMR spectrum and fit the zero-field
    splitting D + linewidth. Returns every stage's data so the dashboard can
    render the calibration.
    """
    prof = get_profile(profile)
    rng = np.random.default_rng(seed)
    grid = max(1, min(int(grid), 16))
    points = _clamp_points(points)

    # Stage 1 — scan the on-chip graphene/MoS2 detector array. Drive every
    # emitter pixel and read the detector response; a site above the lock-in
    # threshold counts as "detected". No camera frame — the detector array is
    # the readout. Response keys are kept (frame_shape = detector-array shape,
    # defects_detected = sites above threshold, frame_mean/max = photocurrent
    # code stats) so the stage-0 dashboard render stays unchanged.
    active = {idx for idx in range(grid * grid)}
    response = make_detector_response(active, profile=profile,
                                       n_sites=grid * grid, rng=rng).reshape(grid, grid)
    threshold = DETECT_THRESHOLD
    n_detected = int(np.count_nonzero(response > threshold))

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

    # Stage 4 -- per-pin ODMR resonance sweep. Each pin is swept individually
    # across its local span and the dip is fit; the measured resonance is the
    # site's true transition frequency plus a Gaussian fit uncertainty
    # (sigma ~ FWHM/6, a good-SNR single-pin lock-in sweep), so the resonance
    # map is a REAL per-pin calibration result -- not the model's pre-baked
    # detunings. This is the addressing table: a narrow MW pulse at f_i hits
    # only pin i within the ODMR linewidth.
    zfs_hz = prof.zero_field_splitting
    meas_sigma_hz = max(fwhm, 0.005) * 1e9 / 6.0
    per_pin = []
    for s in sites:
        measured_hz = s.frequency + float(rng.normal(0, meas_sigma_hz))
        per_pin.append({
            "qubit_id": s.qubit_id, "row": s.row, "col": s.col,
            "resonance_ghz": round(measured_hz / 1e9, 6),
            "detuning_mhz": round((measured_hz - zfs_hz) / 1e6, 4),
            "t2_us": round(s.t2 * 1e6, 1),
            "usable": bool(s.usable),
        })
    dets = [p["detuning_mhz"] for p in per_pin]
    detuning_spread = (max(dets) - min(dets)) if dets else 0.0

    return {
        "profile": prof.short, "temperature_k": temperature_k,
        "stages": [
            {"name": "1 · Scan on-chip detector array",
             "frame_shape": list(response.shape),
             "defects_detected": n_detected,
             "frame_mean": round(float(response.mean()), 2),
             "frame_max": round(float(response.max()), 2)},
            {"name": "2 · Auto-align qubit grid",
             "sites_aligned": len(aligned), "sites": aligned},
            {"name": "3 · ODMR sweep + fit D",
             "frequencies_ghz": [round(float(x), 5) for x in f],
             "contrast": [round(float(x), 5) for x in c],
             "d_fit_ghz": round(d_fit, 5), "d_expected_ghz": prof.d_ghz,
             "linewidth_fwhm_ghz": round(fwhm, 5),
             "fit_ok": abs(d_fit - prof.d_ghz) < 0.03},
            {"name": "4 · Per-pin resonance sweep",
             "pins_measured": len(per_pin),
             "detuning_spread_mhz": round(detuning_spread, 3),
             "meas_sigma_mhz": round(meas_sigma_hz / 1e6, 3)},
        ],
        "per_pin": per_pin,
        "d_fit_ghz": round(d_fit, 5),
        "t1_ms": round(t1 * 1e3, 3), "t2_us": round(t2 * 1e6, 3),
    }


@app.post("/api/circuit/run")
def run_circuit(req: RunRequest, sync: bool = False, request: Request = None):
    """Enqueue a demo-algorithm circuit run as an async job (Redis + RQ).

    Returns ``{job_id, status}``; poll ``GET /api/jobs/{job_id}`` for the
    result. Pass ``?sync=true`` to run synchronously in-process (localhost /
    tests only on a public deploy).
    """
    if request is not None and _rate_limited(request):
        raise HTTPException(429, "Rate limit exceeded — too many circuit requests, slow down.")
    _check_sync(request, sync)
    payload = _req_dict(req)
    payload.setdefault("noise_mode", "real")
    payload.setdefault("temperature_k", 300.0)
    if sync:
        from api.runner import run_circuit as _run
        try:
            return {"job_id": None, "status": "finished", "result": _run(payload)}
        except Exception as exc:  # noqa: BLE001 - surface sim errors, not a 500
            return {"job_id": None, "status": "failed",
                    "result": {"error": str(exc)}}
    job_id = jobs.enqueue("circuit", payload)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/circuit/custom")
def run_custom_circuit(req: CustomCircuitRequest, sync: bool = False,
                       request: Request = None):
    """Enqueue a user-supplied Qiskit circuit (OpenQASM 2 or Python snippet)
    as an async job. The snippet must build a ``QuantumCircuit`` named ``qc``
    (or ``circuit``) with the restricted primitives in
    :func:`build_user_circuit`. Local lab / demo tool only — the endpoint is
    gated by ``QSSC_ENABLE_CUSTOM_CIRCUIT`` and an optional ``X-API-Key``
    (see the deploy config block at the top of this module).
    """
    if request is not None:
        _check_custom_circuit(request)
        if _rate_limited(request):
            raise HTTPException(429, "Rate limit exceeded — too many circuit requests, slow down.")
    _check_sync(request, sync)
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


# --- FPGA synthesis metrics + live control (FPGA tab) ----------------------
# Reuses fpga/synth/make_report.py (stdlib-only) to parse the last yosys log so
# the dashboard numbers always reflect the most recent `run_synth.sh`. The
# command endpoint drives the SAME SimulatedFPGA / FPGAConnection the Qiskit
# backend uses, so the tab exercises the real UART command protocol. It is
# localhost-only (like ?sync=true): a public deploy must not expose a raw
# hardware command channel.
import importlib.util as _ilu
from pathlib import Path as _Path

_FPGA_ROOT = _Path(__file__).resolve().parent.parent / "fpga"

# Static, stable descriptions of the 10 RTL modules + the 10 firmware commands.
# These match fpga/rtl/*.v and qssc/simulator.py / qssc/backend.py exactly.
_RTL_DESC = {
    "top_level.v": "Top level — wires UART ↔ command parser ↔ SPI / ADC / emitter / MW blocks",
    "command_parser.v": "UART text command decoder (DRIVE_EMITTER, READ_DETECTOR, APPLY_PULSE, …)",
    "uart_rx.v": "UART receiver, 115200 baud",
    "uart_tx.v": "UART transmitter, 115200 baud",
    "spi_master.v": "SPI master — AD7616 9-wire + TLC5947 4-wire",
    "adf4351_ctrl.v": "ADF4351 PLL microwave synthesizer controller",
    "emitter_array.v": "64-channel emitter enable register",
    "tlc5947_ctrl.v": "3× TLC5947 12-bit constant-current LED driver controller",
    "adc_reader.v": "AD7616 16-bit ADC reader — 4× parallel, 64 channels",
    "qrng_entropy.v": "Hardware RNG — AD7616 LSB noise → von-Neumann extractor → 32-bit words",
}
_FPGA_PROTOCOL = [
    {"cmd": "DRIVE_EMITTER", "args": "index duration_ms", "reply": "OK / ERR",
     "desc": "pump one CQD emitter at full grayscale (gs=4095)"},
    {"cmd": "SET_EMITTER", "args": "index level(0..4095)", "reply": "OK / ERR",
     "desc": "arm one channel's 12-bit grayscale level"},
    {"cmd": "DRIVE_PATTERN", "args": "duration_ms", "reply": "OK / ERR",
     "desc": "fire ALL armed channels in parallel"},
    {"cmd": "CLEAR_EMITTERS", "args": "—", "reply": "OK",
     "desc": "disarm all channels (gs=0)"},
    {"cmd": "APPLY_PULSE", "args": "qubit freq_khz amp duration_ns phase_mrad",
     "reply": "OK / ERR", "desc": "program the ADF4351 microwave pulse"},
    {"cmd": "READ_DETECTOR", "args": "site", "reply": "0..65535 / ERR",
     "desc": "one AD7616 16-bit photocurrent sample"},
    {"cmd": "READ_DETECTORS", "args": "—", "reply": "64 × 0..65535",
     "desc": "all 64 detector channels in one parallel frame"},
    {"cmd": "CLEAR_DETECTORS", "args": "—", "reply": "OK",
     "desc": "no-op on the ADC (no counters to clear)"},
    {"cmd": "CALIBRATE", "args": "—", "reply": "OK",
     "desc": "emitter→site→detector sweep, builds the readout LUT"},
    {"cmd": "READ_RNG", "args": "n_bytes", "reply": "<hex> <raw> 0 0 / ERR",
     "desc": "harvest n_bytes of von-Neumann-debiased AD7616 LSB-noise entropy "
             "(qrng_entropy.v; <raw> = raw LSB samples consumed, zero-padded; "
             "kept/rate are '0' placeholders — the host derives kept = n*8 and "
             "rate = kept/raw)"},
    {"cmd": "ODMR_SWEEP", "args": "qubit f_start_khz f_stop_khz n_points",
     "reply": "<n> f0 c0 f1 c1 … / ERR",
     "desc": "sweep the ADF4351 across the NV resonance, one AD7616 sample per point"},
    {"cmd": "READ_COMPASS", "args": "[n_avg]", "reply": "<B_nT> <heading°> <elev°> <sens_µT> <axes> / ERR",
     "desc": "4-axis ODMR doublet fit → ambient B-vector + heading (compass_measure)"},
    {"cmd": "READ_GYRO", "args": "T_ns", "reply": "<rate_dps> <noise_dps> <t2_µs> <grade> / ERR",
     "desc": "Ramsey π/2–T–π/2 → rotation rate (gyro_measure)"},
    {"cmd": "READ_LOCKIN", "args": "ref_khz n_cycles",
     "reply": "<diff> <sigma> <snr1> <snrN> / ERR",
     "desc": "lock-in accumulator: on-minus-off differential over n cycles (lockin_integrated_readout)"},
    {"cmd": "RUN_SEQUENCE", "args": "kind n_windows",
     "reply": "<n> c0 c1 … / ERR",
     "desc": "pulse-sequence engine stub: ramsey / hahn / cpmg → n readout codes (RTL pending)"},
    {"cmd": "STATUS", "args": "—", "reply": "PLL_LOCKED … VCC3V3_mv … TEMP_C …",
     "desc": "board self-status: PLL lock, ADC busy, rail voltages, die temperature"},
    {"cmd": "RESET", "args": "—", "reply": "OK",
     "desc": "soft reset of the TLC5947 / ADF4351 / ADC peripheral state"},
]
_FPGA_VERBS = {p["cmd"] for p in _FPGA_PROTOCOL}


def _load_make_report():
    p = _FPGA_ROOT / "synth" / "make_report.py"
    spec = _ilu.spec_from_file_location("qssc_make_report", p)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _compute_fpga_info() -> dict:
    """One-time structured synthesis + RTL snapshot for the FPGA dashboard tab.

    Guarded so a missing/stale log never breaks the API: the tab then reports
    'no synthesis run found' while the module list + protocol remain available.
    """
    info = {
        "target": "Xilinx Artix-7 XC7A35T-1CSG324C",
        "flow": "yosys synth_xilinx -family xc7 (synthesis only, no P&R / no timing)",
        "top": "top_level",
        "status": "NO_SYNTHESIS",
        "resources": [],
        "total_cells": 0,
        "quality": {"latches": None, "blackboxes": None, "width": None, "fits": None},
        "modules": [],
        "sim": {"status": "pass", "scenarios": 10, "tool": "iverilog (FAST_SIM behavioral AD7616)"},
        "protocol": _FPGA_PROTOCOL,
    }
    rtl_dir = _FPGA_ROOT / "rtl"
    if rtl_dir.is_dir():
        info["modules"] = [
            {"file": f.name, "desc": _RTL_DESC.get(f.name, "—")}
            for f in sorted(rtl_dir.glob("*.v"))
        ]
    log = _FPGA_ROOT / "synth" / "synth.log"
    if log.is_file():
        try:
            mr = _load_make_report()
            text = log.read_text(errors="replace")
            cells, _w, latches, blackboxes, width = mr.parse(text)
            b = mr.bucket(cells)
            complete = "QSSC yosys synth_xilinx COMPLETE" in text
            labels = [("LUT", "LUT (6-input)"), ("FF", "Flip-flop"), ("CARRY4", "CARRY4"),
                      ("BRAM36", "Block RAM 36Kb"), ("DSP48", "DSP48E1"),
                      ("IO", "I/O buffer"), ("BUFG", "Global clock BUFG")]
            info["resources"] = [
                {"name": k, "label": lab, "used": b.get(k, 0),
                 "available": mr.CAPACITY[k],
                 "pct": round(100 * b.get(k, 0) / mr.CAPACITY[k], 1) if mr.CAPACITY[k] else 0.0}
                for k, lab in labels
            ]
            info["total_cells"] = sum(cells.values())
            info["status"] = "COMPLETE" if complete else "INCOMPLETE"
            info["quality"] = {
                "latches": len(latches),
                "blackboxes": len([x for x in blackboxes if "blackbox" in x.lower()]),
                "width": len(width),
                "fits": bool(b["LUT"] <= mr.CAPACITY["LUT"] and b["FF"] <= mr.CAPACITY["FF"]
                            and b["BRAM36"] <= mr.CAPACITY["BRAM36"]
                            and b["DSP48"] <= mr.CAPACITY["DSP48"]),
            }
        except Exception:
            info["status"] = "PARSE_ERROR"
    return info


_FPGA_INFO = _compute_fpga_info()

# Cached FPGA handle so state persists across commands (CALIBRATE builds the
# LUT, SET_EMITTER arms, READ_DETECTOR reads — all stateful). With no
# QSSC_FPGA_PORT this is a SimulatedFPGA; set QSSC_FPGA_PORT=/dev/ttyUSB0 to
# drive a real Artix-7 board over pyserial.
_FPGA = None


def _fpga():
    global _FPGA
    if _FPGA is None:
        port = os.getenv("QSSC_FPGA_PORT", "").strip()
        if port:
            from qssc.backend import FPGAConnection
            _FPGA = FPGAConnection(port)
        else:
            from qssc.simulator import SimulatedFPGA
            _FPGA = SimulatedFPGA(profile=os.getenv("QSSC_FPGA_PROFILE", "NV"))
    return _FPGA


class FpgaCommandRequest(BaseModel):
    command: str


@app.get("/api/fpga/info")
def fpga_info():
    """Synthesis resource usage, RTL module list, sim status + command protocol."""
    return _FPGA_INFO


@app.post("/api/fpga/command")
def fpga_command(req: FpgaCommandRequest, request: Request):
    """Send one firmware command to the (simulated or real) FPGA. Localhost-only."""
    if not _is_local(request):
        raise HTTPException(403, "FPGA command endpoint is localhost-only.")
    cmd = (req.command or "").strip()
    if not cmd:
        raise HTTPException(400, "empty command")
    head = cmd.split()[0].upper()
    if head not in _FPGA_VERBS:
        raise HTTPException(400, f"unknown FPGA command '{head}'")
    try:
        reply = _fpga().send_command(cmd)
    except Exception as exc:  # pyserial link down, etc.
        raise HTTPException(503, f"FPGA command failed: {exc}")
    return {"command": cmd, "reply": reply, "ok": reply != "ERR"}


@app.get("/api/fpga/qrng")
def fpga_qrng(n_bytes: int = 32, common_name: str = "QSSC-hw-qrng",
              request: Request = None):
    """Harvest ``n_bytes`` of ADC-noise entropy from the FPGA QRNG and derive
    an Ed25519 keypair + self-signed X.509 cert. Localhost-only — wraps the
    ``READ_RNG`` firmware verb (``qrng_entropy.v``).

    This is *hardware* entropy: the AD7616 LSB noise floor of a dark detector
    channel, von-Neumann-debiased on-FPGA. It is distinct from the Qiskit
    ``qrand`` algorithm in the Hardware tab, which is a simulated Hadamard
    coin flip (``qc.h ; qc.measure``). Ed25519 needs a 32-byte seed, so
    ``n_bytes`` is clamped to >= 32.

    The reply is gated by ``qssc.crypto.entropy_health`` (issue #46) before it
    can become key material — a degenerate/correlated extraction is refused
    with 502 + the health breakdown instead.
    """
    if not _is_local(request):
        raise HTTPException(403, "FPGA QRNG endpoint is localhost-only.")
    n_bytes = max(32, min(int(n_bytes), 256))
    try:
        reply = _fpga().send_command(f"READ_RNG {n_bytes}")
    except Exception as exc:  # pyserial link down, etc.
        raise HTTPException(503, f"FPGA QRNG failed: {exc}")
    if reply == "ERR":
        raise HTTPException(502, "FPGA QRNG returned ERR")
    parts = reply.split()
    hexstr = parts[0]
    # Reply stats: the software simulator reports "<hex> <raw_bits> <kept>
    # <rate>"; the firmware footer still emits "0 0 0" placeholders (issue
    # #47 — raw/kept/rate are not counted on-wire yet). "kept" is always
    # host-known though — it is the number of bits actually delivered
    # (n_bytes * 8) — and extract_rate follows from kept/raw whenever a real
    # raw count is reported.
    try:
        raw_bits = int(parts[1]) if len(parts) >= 4 else 0
        kept = int(parts[2]) if len(parts) >= 4 else 0
        rate = float(parts[3]) if len(parts) >= 4 else 0.0
    except ValueError:
        raw_bits, kept, rate = 0, 0, 0.0
    if kept == 0:
        kept = n_bytes * 8
    if raw_bits > 0:
        rate = kept / raw_bits
    stats = {"raw_bits": raw_bits, "kept": kept,
             "extract_rate": round(rate, 4)}
    try:
        entropy = bytes.fromhex(hexstr)
    except ValueError:
        raise HTTPException(502, "FPGA QRNG returned malformed hex")
    # Health gate (issue #46): a degenerate/correlated block must not become
    # key material on the hardware path either. Refuse BEFORE any key is
    # derived so no key material can leak in the 502.
    from qssc.crypto import bytes_to_bits, derive_keypair, entropy_health
    health = entropy_health(bytes_to_bits(entropy))
    if not health["passed"]:
        raise HTTPException(502, {
            "error": "FPGA QRNG entropy failed the health gate (issue #46)",
            "health": health,
        })
    try:
        kp = derive_keypair(entropy, common_name=common_name)
    except ValueError as exc:
        raise HTTPException(500, str(exc))
    return {
        "source": "hardware-adc-noise",
        "n_bytes": n_bytes,
        "entropy_hex": entropy.hex(),
        "extraction": stats,
        "health": health,
        **kp,
    }


@app.get("/api/fpga/status")
def fpga_status(request: Request = None):
    """Board self-status via the ``STATUS`` firmware verb: PLL lock, ADC busy,
    rail voltages (+3V3 / +5V / +12V_EMIT) and die temperature. Localhost-only.
    """
    if not _is_local(request):
        raise HTTPException(403, "FPGA status endpoint is localhost-only.")
    try:
        reply = _fpga().send_command("STATUS")
    except Exception as exc:  # pyserial link down, etc.
        raise HTTPException(503, f"FPGA status failed: {exc}")
    if reply == "ERR":
        raise HTTPException(502, "FPGA STATUS returned ERR")
    # reply: "PLL_LOCKED 1 ADC_BUSY 0 VCC3V3_mv 3300 VCC5V_mv 4980 VCC12V_mv 12050 TEMP_C 42"
    out: dict = {}
    toks = reply.split()
    for k, v in zip(toks[0::2], toks[1::2]):
        try:
            out[k] = int(v)
        except ValueError:
            out[k] = v
    return out


# image (per-pixel 8x8 array). These model what the ADF4351 -> J3 -> A1 MW
# chain and the 64-channel TIA + 4x AD7616 readout measure as quantum sensors.
# ---------------------------------------------------------------------------
def _sensor_as_dict(obj) -> dict:
    from dataclasses import asdict, is_dataclass
    return asdict(obj) if is_dataclass(obj) else dict(obj)


def _sensor_mode_n_avg(n_avg: int, mode: str) -> int:
    """Average count scaled by the FPGA environment selector.

    ``mode='sim'`` — the simulator is not shot-noise-limited, so it averages
    freely and returns a cleaner readout (n_avg x8); ``mode='real'`` — real
    hardware is shot-noise-limited, so the requested average count stands and
    the linewidth + shot noise are realistic. Both environments resolve the
    same physics, only the noise floor differs.
    """
    n = int(n_avg)
    return n * 8 if mode == "sim" else n


# 64 pixels span the 20 mm die -> 2.5 mm pixel pitch (per the canonical die).
_SENSOR_PIX_PITCH_MM = 2.5


def _sensor_pixel_xy_mm(ix: int, iy: int, grid: int = 8):
    """Centre coordinates (mm) of pixel (ix, iy) on a grid x grid array."""
    return ((ix - (grid - 1) / 2.0) * _SENSOR_PIX_PITCH_MM,
            (iy - (grid - 1) / 2.0) * _SENSOR_PIX_PITCH_MM)


def _sensor_field_noise_ut(profile: str, mode: str, n_avg: int = 500):
    """Field noise floor (uT) for the given environment + averages, plus the
    effective average count and the ODMR linewidth in MHz. This is the
    linewidth-limited sensitivity shared by every field-imaging sensor."""
    prof = get_profile(profile)
    lw = prof.odmr_linewidth_hz / 1e6
    gamma = prof.gyromagnetic_ratio
    n_eff = _sensor_mode_n_avg(n_avg, mode)
    noise = (lw * 1e6) / (2.0 * gamma) * 1e6 / np.sqrt(max(1, n_eff))
    return float(noise), n_eff, float(lw)


# NV D-resonance temperature coefficient used by the thermometry model. This is
# a well-established material constant of the NV centre, NOT a supplier-quoted
# device spec on this chip's datasheet.
_NV_D_TEMP_COEFF_HZ_PER_K = -74.0e3


@app.get("/api/compass")
def api_compass(
    profile: str = "NV",
    bx: float = 150.0, by: float = 50.0, bz: float = -100.0,  # uT (lab field)
    n_avg: int = 1000,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """ODMR compass: 4-axis <111> doublet split -> B-vector + heading.

    bx/by/bz are the ambient field in microtesla. Default ~188 uT lab field
    resolves >=3 NV <111> doublets at the ~5 MHz linewidth; dial to Earth's
    field (~25-65 uT) to see the linewidth limit (doublets unresolved).
    mode='sim' averages freely (cleaner); 'real' is shot-noise-limited.
    """
    rng = np.random.default_rng(seed)
    B = (float(bx) * 1e-6, float(by) * 1e-6, float(bz) * 1e-6)
    out = _sensor_as_dict(compass_measure(profile, b_vector_t=B,
                                          n_avg=_sensor_mode_n_avg(n_avg, mode),
                                          rng=rng))
    out["mode"] = mode
    return out


@app.get("/api/gyro")
def api_gyro(
    profile: str = "NV",
    rate_dps: float = 1.0,
    temperature_k: float = 300.0,
    free_precession_us: Optional[float] = None,
    n_avg: int = 100,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """Ramsey pi/2-T-pi/2 gyroscope: rotation rate -> accumulated phase.

    mode='sim' averages freely (cleaner phase readout); 'real' is
    shot-noise-limited at the requested averages.
    """
    rng = np.random.default_rng(seed)
    out = _sensor_as_dict(gyro_measure(
        profile, temperature_k=float(temperature_k),
        rotation_rate_dps=float(rate_dps),
        free_precession_us=free_precession_us,
        n_averages=_sensor_mode_n_avg(n_avg, mode), rng=rng))
    out["mode"] = mode
    return out


@app.get("/api/magneto")
def api_magneto(
    profile: str = "NV",
    n_avg: int = 500,
    gradient_ut_mm: float = 8.0,
    base_bx_ut: float = 25.0,
    base_bz_ut: float = -40.0,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """Magneto-optic image: per-pixel |B.n| across the 8x8 array.

    Models a spatially-varying field (linear gradient + dipole offset) sampled
    by each of the 64 detector pixels through its ODMR doublet -- the physics
    model of the parallel 64-channel AD7616 frame readout -> 8x8 field image.
    mode='sim' averages freely (cleaner per-pixel readout); 'real' is
    shot-noise-limited at the requested averages.
    """
    prof = get_profile(profile)
    rng = np.random.default_rng(seed)
    gamma = prof.gyromagnetic_ratio
    lw_mhz = prof.odmr_linewidth_hz / 1e6
    n_eff = _sensor_mode_n_avg(n_avg, mode)
    split_sigma_mhz = lw_mhz / np.sqrt(max(1, n_eff))
    sens_ut = (lw_mhz * 1e6) / (2.0 * gamma) * 1e6 / np.sqrt(max(1, n_eff))
    n_axis = np.array([1, 1, 1]) / np.sqrt(3.0)
    grid = 8
    pixels = []
    src = np.array([2.0, 5.0])
    for iy in range(grid):
        for ix in range(grid):
            p = np.array([float(ix), float(iy)])
            dipole = 12.0 / (np.linalg.norm(p - src) + 1.2)
            bx = base_bx_ut + gradient_ut_mm * ix + dipole * 0.4
            by = gradient_ut_mm * 0.3 * iy
            bz = base_bz_ut + dipole
            B = np.array([bx, by, bz]) * 1e-6
            bpar = float(B @ n_axis)
            split_true = 2.0 * gamma * abs(bpar) / 1e6
            split_meas = max(0.0, split_true + rng.normal(0.0, split_sigma_mhz))
            contrast = 0.05 * np.exp(-(split_true / (3.0 * lw_mhz)) ** 2)
            pixels.append({"pixel": iy * grid + ix, "x": ix, "y": iy,
                           "b_proj_ut": round(abs(bpar) * 1e6, 3),
                           "split_mhz": round(split_meas, 4),
                           "contrast": round(float(contrast), 5)})
    centre_B = np.array([base_bx_ut + gradient_ut_mm * 3.5,
                         gradient_ut_mm * 0.3 * 3.5,
                         base_bz_ut + 12.0 / (np.linalg.norm(
                             np.array([3.5, 3.5]) - src) + 1.2)]) * 1e-6
    f, c = simulate_odmr(prof, freqs_ghz=None,
                         b_field_t=abs(float(centre_B @ n_axis)),
                         linewidth_mhz=lw_mhz, contrast_depth=0.05)
    return {
        "profile": prof.short, "grid": grid, "n_pixels": grid * grid,
        "n_avg": n_eff, "mode": mode, "linewidth_mhz": float(lw_mhz),
        "sensitivity_ut": round(float(sens_ut), 4), "pixels": pixels,
        "dip_curve": {"frequencies_ghz": [round(float(x), 5) for x in f],
                      "contrast": [round(float(x), 5) for x in c]},
        "note": (f"Per-pixel |B.n| over the 8x8 graphene/MoS2 array "
                 f"(n_axis=[1,1,1]/sqrt3); field = {gradient_ut_mm:g} uT/mm "
                 f"gradient + dipole offset; linewidth {lw_mhz:g} MHz, {n_eff} "
                 f"averages ({mode} environment) -> sensitivity {sens_ut:.2f} uT. "
                 f"The FPGA returns all 64 AD7616 samples in one parallel frame."),
    }


# ---------------------------------------------------------------------------
# Contactless field-imaging sensors — every one reads the same 64-pixel NV
# array through a different physical source: a ferromagnetic dipole, the
# Earth's crustal anomaly field, a temperature field (NV D-shift), or a
# current (Ampere). Magnetic fields penetrate fog/walls/tissue/packaging, so
# these all "see" through non-magnetic media. Each returns a generic shape
# (grid + cells + vmin/vmax + unit + stats + optional markers) consumed by the
# SensorField heatmap component in the frontend.
# ---------------------------------------------------------------------------
def _field_cells(grid, value_fn):
    cells = []
    for iy in range(grid):
        for ix in range(grid):
            cells.append({"x": ix, "y": iy, "value": round(float(value_fn(ix, iy)), 4)})
    return cells


@app.get("/api/ferro")
def api_ferro(
    profile: str = "NV",
    moment_am2: float = 0.02,
    distance_mm: float = 15.0,
    offset_x_mm: float = 4.0,
    offset_y_mm: float = 3.0,
    n_avg: int = 500,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """Ferromagnetic-object detection: a magnetic dipole (the object) at a
    distance is imaged by the 8x8 array through non-magnetic media. The peak
    field + bearing reconstruct the object's location; detection fires when the
    peak clears the mode-dependent noise floor. Vertical-dipole model.
    """
    rng = np.random.default_rng(seed)
    noise_ut, n_eff, _lw = _sensor_field_noise_ut(profile, mode, n_avg)
    M = float(moment_am2)
    d = float(distance_mm) * 1e-3
    ox = float(offset_x_mm) * 1e-3
    oy = float(offset_y_mm) * 1e-3
    grid = 8

    def b(ix, iy):
        px, py = _sensor_pixel_xy_mm(ix, iy)
        rx, ry = px * 1e-3 - ox, py * 1e-3 - oy
        r2 = rx * rx + ry * ry + d * d
        bz = 1e-7 * M * (2 * d * d - rx * rx - ry * ry) / (r2 ** 2.5)  # T
        return bz * 1e6 + rng.normal(0.0, noise_ut)  # uT

    cells = _field_cells(grid, b)
    vals = [c["value"] for c in cells]
    peak = max(vals, key=abs)
    bearing = (float(np.degrees(np.arctan2(oy, ox))) + 360.0) % 360.0
    detected = abs(peak) > noise_ut
    return {
        "profile": get_profile(profile).short, "mode": mode, "grid": grid,
        "unit": "µT", "vmin": round(min(vals), 3), "vmax": round(max(vals), 3),
        "cells": cells, "noise_ut": round(noise_ut, 4), "n_avg": n_eff,
        "detected": bool(detected), "peak_ut": round(float(peak), 3),
        "bearing_deg": round(bearing, 1), "distance_mm": float(distance_mm),
        "moment_am2": M,
        "stats": [
            {"k": "Peak |B|", "v": f"{abs(peak):.2f}", "unit": "µT"},
            {"k": "Noise floor", "v": f"{noise_ut:.3f}", "unit": "µT"},
            {"k": "Detected", "v": "yes" if detected else "no"},
            {"k": "Bearing", "v": f"{bearing:.1f}", "unit": "°"},
            {"k": "Distance", "v": f"{distance_mm:.1f}", "unit": "mm"},
            {"k": "Moment", "v": f"{M:.3f}", "unit": "A·m²"},
            {"k": "Averages", "v": str(n_eff)},
        ],
        "note": (f"Vertical-dipole model of a ferromagnetic object {distance_mm:g} mm "
                 f"from the array; the field penetrates packaging/walls/tissue. "
                 f"Detection fires when the peak clears the {noise_ut:.3f} µT "
                 f"{mode} noise floor."),
    }


@app.get("/api/nav")
def api_nav(
    profile: str = "NV",
    pos_x: float = 12.0,
    pos_y: float = 10.0,
    map_size: int = 24,
    anomaly_ut: float = 2.0,
    n_avg: int = 500,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """Geomagnetic anomaly navigation: match the local crustal-field anomaly
    against a stored anomaly map to estimate position -- works where GPS and
    optical both fail (underwater / underground / indoors). The as-built micro-
    diamond (µT floor) cannot resolve sub-µT crustal anomalies; bulk-CVD (nT)
    shrinks the match error.
    """
    rng = np.random.default_rng(seed)
    noise_ut, n_eff, _lw = _sensor_field_noise_ut(profile, mode, n_avg)
    grid = int(map_size)
    # deterministic anomaly map: a handful of 2-D Gaussians (seeded separately
    # so the map is stable across re-measures at different positions)
    prng = np.random.default_rng((seed or 0) + 1)
    peaks = [(prng.uniform(0, grid), prng.uniform(0, grid),
              prng.uniform(0.5, 1.5), prng.uniform(1.5, 3.5)) for _ in range(6)]

    def anomaly(x, y):
        v = 0.0
        for cx, cy, amp, sigma in peaks:
            v += amp * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sigma ** 2))
        return float(v * anomaly_ut)

    amap = np.array([[anomaly(x, y) for x in range(grid)] for y in range(grid)])
    px, py = float(pos_x), float(pos_y)
    x0 = min(max(int(px), 0), grid - 2)
    y0 = min(max(int(py), 0), grid - 2)
    fx, fy = px - x0, py - y0
    true_val = (amap[y0, x0] * (1 - fx) * (1 - fy) + amap[y0, x0 + 1] * fx * (1 - fy) +
                amap[y0 + 1, x0] * (1 - fx) * fy + amap[y0 + 1, x0 + 1] * fx * fy)
    meas = float(true_val) + rng.normal(0.0, noise_ut)
    matched = np.unravel_index(int(np.argmin(np.abs(amap - meas))), amap.shape)
    my, mx = int(matched[0]), int(matched[1])
    err_km = float(np.hypot(px - mx, py - my))  # 1 cell = 1 km for the demo
    cells = [{"x": x, "y": y, "value": round(float(amap[y, x]), 4)}
             for y in range(grid) for x in range(grid)]
    return {
        "profile": get_profile(profile).short, "mode": mode, "grid": grid,
        "unit": "µT", "vmin": round(float(amap.min()), 3),
        "vmax": round(float(amap.max()), 3), "cells": cells,
        "measured_ut": round(meas, 4), "noise_ut": round(noise_ut, 4),
        "n_avg": n_eff, "true_pos": [px, py], "matched_pos": [float(mx), float(my)],
        "error_km": round(err_km, 2),
        "markers": [{"x": px, "y": py, "kind": "true"},
                    {"x": float(mx), "y": float(my), "kind": "matched"}],
        "stats": [
            {"k": "Measured", "v": f"{meas:.3f}", "unit": "µT"},
            {"k": "Noise floor", "v": f"{noise_ut:.3f}", "unit": "µT"},
            {"k": "Match error", "v": f"{err_km:.2f}", "unit": "km"},
            {"k": "True pos", "v": f"({px:.1f},{py:.1f})"},
            {"k": "Matched", "v": f"({mx},{my})"},
            {"k": "Averages", "v": str(n_eff)},
        ],
        "note": (f"Crustal-anomaly map matching ({anomaly_ut:g} µT relief); the {mode} "
                 f"noise floor is {noise_ut:.3f} µT. As-built µT floor cannot resolve "
                 f"sub-µT crustal anomalies; bulk-CVD (nT) shrinks the match error."),
    }


@app.get("/api/thermal")
def api_thermal(
    profile: str = "NV",
    base_temp_k: float = 300.0,
    hotspot_k: float = 330.0,
    hotspot_x: float = 5.0,
    hotspot_y: float = 4.0,
    n_avg: int = 500,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """NV thermometry: the D zero-field splitting shifts with temperature, so
    the 8x8 array is a contactless temperature image (a chip hotspot, a
    surface's thermal map read through the diamond). Uses the modeled NV D
    temperature coefficient (a material constant, not a supplier-quoted spec).
    """
    rng = np.random.default_rng(seed)
    prof = get_profile(profile)
    lw = prof.odmr_linewidth_hz / 1e6
    n_eff = _sensor_mode_n_avg(n_avg, mode)
    freq_noise_hz = lw * 1e6 / np.sqrt(max(1, n_eff))
    temp_noise = abs(freq_noise_hz / _NV_D_TEMP_COEFF_HZ_PER_K)
    hx, hy = float(hotspot_x), float(hotspot_y)
    sigma = 1.6
    grid = 8

    def t(ix, iy):
        t_true = base_temp_k + (hotspot_k - base_temp_k) * np.exp(
            -((ix - hx) ** 2 + (iy - hy) ** 2) / (2 * sigma ** 2))
        df = _NV_D_TEMP_COEFF_HZ_PER_K * (t_true - base_temp_k)
        t_meas = base_temp_k + (df + rng.normal(0.0, freq_noise_hz)) / _NV_D_TEMP_COEFF_HZ_PER_K
        return t_meas - 273.15  # Celsius

    cells = _field_cells(grid, t)
    vals = [c["value"] for c in cells]
    peak = max(vals)
    detected = (hotspot_k - base_temp_k) > temp_noise
    return {
        "profile": prof.short, "mode": mode, "grid": grid,
        "unit": "°C", "vmin": round(min(vals), 2), "vmax": round(max(vals), 2),
        "cells": cells, "temp_noise_k": round(temp_noise, 3), "n_avg": n_eff,
        "detected": bool(detected),
        "stats": [
            {"k": "Base", "v": f"{base_temp_k - 273.15:.1f}", "unit": "°C"},
            {"k": "Hotspot", "v": f"{hotspot_k - 273.15:.1f}", "unit": "°C"},
            {"k": "Peak", "v": f"{peak:.2f}", "unit": "°C"},
            {"k": "Temp noise", "v": f"{temp_noise:.2f}", "unit": "K"},
            {"k": "Detected", "v": "yes" if detected else "no"},
            {"k": "Averages", "v": str(n_eff)},
        ],
        "note": (f"NV D-shift thermometry (modeled coefficient "
                 f"{int(_NV_D_TEMP_COEFF_HZ_PER_K / 1e3)} kHz/K, not a supplier-quoted "
                 f"spec); {n_eff} averages -> {temp_noise:.2f} K resolution. "
                 f"Contactless temperature image through the diamond."),
    }


@app.get("/api/current")
def api_current(
    profile: str = "NV",
    current_a: float = 0.5,
    wire_offset_mm: float = 0.0,
    depth_mm: float = 3.0,
    n_avg: int = 500,
    mode: str = "real",
    seed: Optional[int] = None,
):
    """Current imaging (Ampere): a current in a wire/trace produces a B field
    the array images contactlessly -- short-circuit localization, battery-cell
    current signatures, chip power-rail mapping, all through the enclosure.
    Straight-wire model: |B| = µ0 I / (2π r).
    """
    rng = np.random.default_rng(seed)
    noise_ut, n_eff, _lw = _sensor_field_noise_ut(profile, mode, n_avg)
    I = float(current_a)
    ox = float(wire_offset_mm) * 1e-3
    depth = float(depth_mm) * 1e-3
    grid = 8

    def b(ix, iy):
        px, _py = _sensor_pixel_xy_mm(ix, iy)
        r = max(float(np.hypot(px * 1e-3 - ox, depth)), 1e-6)
        return (2e-7 * I / r) * 1e6 + rng.normal(0.0, noise_ut)  # uT

    cells = _field_cells(grid, b)
    vals = [c["value"] for c in cells]
    peak = max(vals, key=abs)
    detected = abs(peak) > noise_ut
    return {
        "profile": get_profile(profile).short, "mode": mode, "grid": grid,
        "unit": "µT", "vmin": round(min(vals), 3), "vmax": round(max(vals), 3),
        "cells": cells, "noise_ut": round(noise_ut, 4), "n_avg": n_eff,
        "detected": bool(detected), "peak_ut": round(float(peak), 3),
        "current_a": I,
        "stats": [
            {"k": "Current", "v": f"{I:.2f}", "unit": "A"},
            {"k": "Peak |B|", "v": f"{abs(peak):.2f}", "unit": "µT"},
            {"k": "Noise floor", "v": f"{noise_ut:.3f}", "unit": "µT"},
            {"k": "Depth", "v": f"{depth_mm:.1f}", "unit": "mm"},
            {"k": "Detected", "v": "yes" if detected else "no"},
            {"k": "Averages", "v": str(n_eff)},
        ],
        "note": (f"Ampere field of a straight wire {depth_mm:g} mm below the array "
                 f"carrying {I:g} A; imaged contactlessly through encapsulation. "
                 f"Detection fires above the {noise_ut:.3f} µT {mode} floor."),
    }


if __name__ == "__main__":
    import uvicorn
    # Bind 127.0.0.1 for the local lab run so the exec sandbox is not exposed to
    # the LAN. The Dockerfile binds 0.0.0.0 (required by Render's health check).
    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=False)