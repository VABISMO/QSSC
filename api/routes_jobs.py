"""Circuit enqueue + async job polling + saved Qiskit script endpoints.

The POST circuit endpoints enqueue RQ jobs (or run sync in-process); the job
polling + saved-script endpoints are the Code tab's persistence layer.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api import jobs

from ._guards import (
    _rate_limited, _check_sync, _check_custom_circuit, _check_write, _req_dict,
)
from .models import (
    RunRequest, CustomCircuitRequest, ParallelRunRequest, CalibrateArrayRequest,
    ScriptRequest,
)

router = APIRouter()


@router.post("/api/circuit/run")
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


@router.post("/api/circuit/custom")
def run_custom_circuit(req: CustomCircuitRequest, sync: bool = False,
                       request: Request = None):
    """Enqueue a user-supplied Qiskit circuit (OpenQASM 2 or Python snippet)
    as an async job. The snippet must build a ``QuantumCircuit`` named ``qc``
    (or ``circuit``) with the restricted primitives in
    :func:`build_user_circuit`. Local lab / demo tool only — the endpoint is
    gated by ``QSSC_ENABLE_CUSTOM_CIRCUIT`` and an optional ``X-API-Key``
    (see the deploy config block at the top of :mod:`api.main`).
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


@router.post("/api/circuit/run-parallel")
def run_parallel_circuits(req: ParallelRunRequest, sync: bool = False,
                          request: Request = None):
    """64-parallel distributed computing.

    Run one circuit per pixel across the 64-pixel array -- 64 INDEPENDENT
    ~10-NV processors at 300 K (1/r**3 -> zero at the 2.5 mm pitch, so the
    pixels do not couple) -- and aggregate. The chip's compute throughput is
    64x a single pixel, not one 640-qubit machine. Modes:

      * ``shor``   -- up to n_pixels coprime bases a factor N; first pixel to
        find the period wins (the rest fail silently).
      * ``sample`` -- the same circuit on all n_pixels pixels with independent
        seeds = n_pixels x shots aggregated in one wall-clock step.
      * ``custom`` -- a list of QASM circuits, one per pixel.

    Enqueued as an async RQ job (a 64-pixel Shor run is ~50 s of serial
    Qiskit, which would block the single-worker event loop if run inline).
    Returns ``{job_id, status}``; poll ``GET /api/jobs/{job_id}``. Pass
    ``?sync=true`` to run synchronously in-process (localhost / tests only).
    """
    if request is not None and _rate_limited(request):
        raise HTTPException(429, "Rate limit exceeded — too many circuit requests, slow down.")
    _check_sync(request, sync)
    payload = _req_dict(req)
    payload.setdefault("noise_mode", "real")
    payload.setdefault("temperature_k", 300.0)
    if sync:
        from api.runner import run_parallel as _run
        try:
            return {"job_id": None, "status": "finished", "result": _run(payload)}
        except Exception as exc:  # noqa: BLE101 - surface sim errors, not a 500
            return {"job_id": None, "status": "failed",
                    "result": {"error": str(exc)}}
    job_id = jobs.enqueue("parallel", payload)
    return {"job_id": job_id, "status": "queued"}


@router.post("/api/circuit/calibrate-array")
def calibrate_array_endpoint(req: CalibrateArrayRequest, sync: bool = False,
                             request: Request = None):
    """Calibrate the 64-pixel array's per-pixel register width.

    The width is the number of ODMR lines the field gradient makes
    resolvable in each pixel, capped by the NVs physically present — a MEASURED
    quantity, not a fixed knob. ``source="sim"`` synthesizes each pixel's
    spectrum and counts the lines; ``source="real"`` runs the ``RUN_ALL_ODMR``
    firmware verb and counts the lines from the measured 64-channel codes. The
    result is persisted to ``hardware/calibration/array_calibration.json`` and
    later ``run-parallel`` calls load it so each pixel runs at its own width.

    Enqueued as an async RQ job (the sim path synthesizes 64 ODMR spectra and
    runs ``decompose_odmr`` on each — ~30 s — which would block the
    single-worker event loop if run inline). Returns ``{job_id, status}``;
    poll ``GET /api/jobs/{job_id}``. Pass ``?sync=true`` to run synchronously
    in-process (localhost / tests only).
    """
    if request is not None and _rate_limited(request):
        raise HTTPException(429, "Rate limit exceeded — too many circuit requests, slow down.")
    _check_sync(request, sync)
    payload = _req_dict(req)
    payload.setdefault("temperature_k", 300.0)
    if sync:
        from api.runner import run_calibrate_array as _run
        try:
            return {"job_id": None, "status": "finished", "result": _run(payload)}
        except Exception as exc:  # noqa: BLE101 - surface errors, not a 500
            return {"job_id": None, "status": "failed",
                    "result": {"error": str(exc)}}
    job_id = jobs.enqueue("calibrate", payload)
    return {"job_id": job_id, "status": "queued"}


# --- async job polling + history -------------------------------------------

@router.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    rec = jobs.get_job(job_id)
    if rec is None:
        return {"job_id": job_id, "status": "unknown"}
    return rec


@router.get("/api/jobs")
def list_jobs(limit: int = 50):
    return jobs.list_jobs(limit=limit)


@router.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, request: Request = None):
    if request is not None:
        _check_write(request)
    return {"deleted": jobs.delete_job(job_id)}


# --- saved Qiskit scripts (Code tab) ----------------------------------------

@router.get("/api/scripts")
def list_scripts():
    return jobs.list_scripts()


@router.post("/api/scripts")
def save_script(req: ScriptRequest, request: Request = None):
    if request is not None:
        _check_write(request)
    rec = jobs.save_script(req.name, req.code, req.id)
    return rec


@router.delete("/api/scripts/{sid}")
def delete_script(sid: str, request: Request = None):
    if request is not None:
        _check_write(request)
    return {"deleted": jobs.delete_script(sid)}