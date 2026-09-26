# api/main.py
"""FastAPI backend for the Quantum Solid State Chip (QSSC) dashboard.

Exposes the simulator, the Qiskit backend, the ODMR model and the fabrication
protocol as a small REST API consumed by the React frontend.  Run with:

    uvicorn api.main:app --reload --port 8000

This module is the application factory: it owns the deploy configuration (env-
driven), builds the ``FastAPI`` app + CORS middleware, and includes the
domain routers from :mod:`api.routes_{calibration,signal,jobs,fpga,sensor}`.
The shared request guards, helpers and Pydantic models live in
:mod:`api._guards` and :mod:`api.models`. ``calibration_run`` and
``signal_array`` are re-exported here so ``from api.main import
calibration_run, signal_array`` (used by tests) keeps resolving, and
``make_connection`` is re-exported so ``patch("api.main.make_connection", …)``
remains the test patch target every route honours (via
:func:`api._guards.connect_fpga`).
"""
from __future__ import annotations

import os
from collections import defaultdict, deque

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from qssc import make_connection  # noqa: F401 — re-exported as the test patch target

from ._guards import (  # noqa: F401 — re-exported for api.main.* callers
    GRAD_COIL_VDD, GRAD_SENSE_OHM, GRAD_ADDRESSING_A,
    GRAD_ADDRESSING_MHZ_PER_UM, GRAD_PIXEL_UM, ODMR_LINEWIDTH_MHZ,
    GRAD_COIL_MAX_MHZ_PER_UM, _real_odmr_sweep,
)
from .routes_calibration import router as calibration_router, calibration_run
from .routes_signal import router as signal_router, signal_array
from .routes_jobs import router as jobs_router
from .routes_fpga import router as fpga_router
from .routes_sensor import router as sensor_router

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

app.include_router(calibration_router)
app.include_router(signal_router)
app.include_router(jobs_router)
app.include_router(fpga_router)
app.include_router(sensor_router)

# Re-export the two test-contract endpoints + make_connection at the
# api.main namespace (tests do `from api.main import calibration_run,
# signal_array` and `patch("api.main.make_connection", ...)`).
__all__ = [
    "app", "make_connection", "calibration_run", "signal_array",
    "CUSTOM_CIRCUIT_ENABLED", "CUSTOM_CIRCUIT_API_KEY",
    "ALLOW_SYNC_REMOTE", "CORS_ORIGINS", "RATE_LIMIT_PER_MIN",
]


if __name__ == "__main__":
    import os
    import uvicorn
    # Bind 127.0.0.1 for the local lab run so the exec sandbox is not exposed to
    # the LAN. The Dockerfile binds 0.0.0.0 (required by Render's health check).
    # Default port 8011 matches the Vite dev proxy (frontend/vite.config.js) so
    # `python -m api.main` + `npm run dev` line up without an extra flag; override
    # with QSSC_API_PORT.
    uvicorn.run("api.main:app", host="127.0.0.1",
                port=int(os.environ.get("QSSC_API_PORT", "8011")), reload=False)