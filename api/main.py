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
    num_qubits: int = Field(2, ge=1, le=20)         # bell / qft / qrand
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
        ],
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

# Static, stable descriptions of the 9 RTL modules + the 9 firmware commands.
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


if __name__ == "__main__":
    import uvicorn
    # Bind 127.0.0.1 for the local lab run so the exec sandbox is not exposed to
    # the LAN. The Dockerfile binds 0.0.0.0 (required by Render's health check).
    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=False)