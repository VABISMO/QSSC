"""Shared request guards, helpers and deploy config readers for the QSSC API.

Pure helpers (``_client_ip``, ``_is_local``, ``_clamp_points``,
``_gradient_from_code``, ``_req_dict``, ``_backend``, ``_real_odmr_sweep``) and
the field-gradient coil constants live here. The three deploy-config-gated
guards (``_rate_limited``, ``_check_sync``, ``_check_custom_circuit``) read
their config knobs (``RATE_LIMIT_PER_MIN``, ``ALLOW_SYNC_REMOTE``,
``CUSTOM_CIRCUIT_ENABLED``, ``CUSTOM_CIRCUIT_API_KEY``) through the
:mod:`api.main` namespace at call time, so ``monkeypatch.setattr(main_mod,
"RATE_LIMIT_PER_MIN", ...)`` and the ``api.main.make_connection`` test patch
keep working after the APIRouter split. ``connect_fpga`` is the single
chokepoint every route uses for the FPGA connection, so a test patch of
``api.main.make_connection`` propagates to every endpoint.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import numpy as np
from fastapi import HTTPException, Request

from qssc import (
    QSSCBackend, DetectorInterface, MicrowaveController,
)

# ---------------------------------------------------------------------------
# Field-gradient coil driver (MCP4921 DAC -> coil current)
# ---------------------------------------------------------------------------
# The gradient-coil current is set by a 12-bit MCP4921 SPI DAC. Vout = Vdd *
# code/4096 with Vdd = +5V; the LM358 + IRLML2502 servo drives I_coil = Vdac /
# Rsense with Rsense = 1 ohm, so I_coil[A] = 5.0 * code/4096. The field gradient
# across the pixel is anchored at the verified addressing point: I = 2.2 A ->
# 7 mT across 200 um. NV gyromagnetic ratio gamma = 28 MHz/mT, so 7 mT/200 um
# -> 0.98 MHz/um of line spread. The gradient scales linearly with the current:
#   gradient_mhz_per_um = (I_coil / 2.2) * 0.98
# At code 0 the gradient is off (ensemble collapses to one line); at the
# addressing code (~1803, I=2.2A) the spread is ~196 MHz; at full code 4095 the
# spread is ~445 MHz (the DAC range exceeds the addressing gradient by design).
GRAD_COIL_VDD = 5.0          # MCP4921 Vdd / Vref [V] (board rail)
GRAD_SENSE_OHM = 1.0         # Rsense [ohm] (board)
GRAD_ADDRESSING_A = 2.2      # I_coil for the 7 mT addressing gradient [A]
GRAD_ADDRESSING_MHZ_PER_UM = 0.98  # 7 mT/200um * 28 MHz/mT [MHz/um]
GRAD_PIXEL_UM = 200.0        # pixel size [um]
ODMR_LINEWIDTH_MHZ = 5.0     # intrinsic single-line ODMR linewidth (simulate_odmr)
# Full-scale coil gradient (DAC code 4095): 5 V * 4095/4096 / 2.2 A * 0.98 MHz/µm.
# The on-board MCP4921 + gradient coil cannot exceed this, so the API caps the
# requested gradient here — the per-pixel register width tops out at ~89 lines
# (gradient × pixel ÷ linewidth) at full coil current.
GRAD_COIL_MAX_MHZ_PER_UM = round(
    GRAD_COIL_VDD * (4095 / 4096.0) / GRAD_ADDRESSING_A * GRAD_ADDRESSING_MHZ_PER_UM, 2)


# ---------------------------------------------------------------------------
# Backend factory + request dict
# ---------------------------------------------------------------------------

def _backend(profile: str, temperature_k: float, seed: int, noise_mode: str,
             n_nv_per_pixel: Optional[int] = None,
             pixel_mode: str = "register",
             register_nv_spacing_nm: float = 2.75,
             gradient_mhz_per_um: float = 0.0,
             pixel_size_um: float = 200.0,
             register_line_freqs_hz: Optional[list] = None,
             prime_readout: bool = False,
             n_noise_bins: int = 64) -> QSSCBackend:
    # Delegate to the canonical ``api.runner._backend`` (the superset factory
    # that also wires hardware / register_widths_per_pixel / anti_noise / etc.).
    # There used to be a second, divergent copy of this factory here that drifted
    # from the runner one (no ``hardware=`` -> no SimulatedFPGA, no calibrated
    # per-pixel widths); keeping two copies was a maintenance hazard and meant
    # the calibration ``backend_info`` route built a subtly different backend
    # than the run routes. The single source of truth now lives in runner.py;
    # this thin wrapper keeps the existing ``from ._guards import _backend``
    # import sites (routes_calibration) working with an identical backend.
    #
    # ``hardware`` defaults to False inside runner._backend, so the calibration
    # path still gets a SimulatedFPGA (describe() does not reflect the FPGA
    # object, so the /api/backend/info response is unchanged).
    from .runner import _backend as _runner_backend
    return _runner_backend(
        profile=profile, temperature_k=temperature_k, seed=seed,
        noise_mode=noise_mode,
        n_nv_per_pixel=n_nv_per_pixel,
        pixel_mode=pixel_mode,
        register_nv_spacing_nm=register_nv_spacing_nm,
        gradient_mhz_per_um=gradient_mhz_per_um,
        pixel_size_um=pixel_size_um,
        register_line_freqs_hz=register_line_freqs_hz,
        prime_readout=prime_readout,
        n_noise_bins=n_noise_bins)


def _req_dict(model) -> dict:
    """Pydantic model -> plain dict (dropping unset None noise fields)."""
    d = model.model_dump()
    return {k: v for k, v in d.items() if v is not None}


# ---------------------------------------------------------------------------
# Request guards (rate limit, sync, custom-circuit gate)
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str:
    # trust the first hop only when a proxy (Render) sets the header
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_local(request: Request) -> bool:
    # Loopback peers are always local. "testclient" is the Starlette TestClient
    # connection host (tests only); it is the connection peer, not a spoofable
    # header, but we still gate it behind QSSC_TESTING so a deploy behind a
    # proxy can never treat a spoofed peer as local. Behind Render's proxy
    # request.client.host is the proxy, so real public callers are never local
    # here — sync stays blocked for them.
    peer = request.client.host if request.client else ""
    if peer in ("127.0.0.1", "::1", "localhost"):
        return True
    if peer == "testclient" and os.environ.get("QSSC_TESTING"):
        return True
    return False


def _rate_limited(request: Request) -> bool:
    """True if the caller exceeded RATE_LIMIT_PER_MIN requests in the last 60s.

    Reads ``RATE_LIMIT_PER_MIN`` and the per-IP bucket dict through the
    :mod:`api.main` namespace so a test ``monkeypatch.setattr(main_mod,
    "RATE_LIMIT_PER_MIN", ...)`` is honoured.
    """
    from api import main as _m
    now = time.monotonic()
    ip = _client_ip(request)
    bucket = _m._rate_buckets[ip]
    bucket.append(now)
    while bucket and now - bucket[0] > 60.0:
        bucket.popleft()
    over = len(bucket) > _m.RATE_LIMIT_PER_MIN
    # Evict the per-IP KEY once its deque is empty so a public deploy with
    # many distinct client IPs does not grow _rate_buckets without bound
    # (the deques are trimmed above, but the dict keys were never removed,
    # audit #123). uvicorn is single-threaded so no lock is needed.
    if not bucket:
        del _m._rate_buckets[ip]
    return over


def _check_sync(request: Request, sync: bool) -> None:
    """Block ?sync=true from non-localhost: it runs in the request thread and
    blocks the single uvicorn worker on a free-tier host (DoS vector)."""
    from api import main as _m
    if request is None:
        return  # programmatic call (no HTTP context) — caller takes responsibility
    if sync and not _is_local(request) and not _m.ALLOW_SYNC_REMOTE:
        raise HTTPException(
            403, "?sync=true is only allowed from localhost on a public deploy "
                 "(it blocks the single API worker).",
        )


def _check_custom_circuit(request: Request) -> None:
    """Gate the custom-circuit endpoint: disabled flag + optional API key."""
    from api import main as _m
    if not _m.CUSTOM_CIRCUIT_ENABLED:
        raise HTTPException(
            503, "The custom-circuit endpoint is disabled on this deployment "
                 "(QSSC_ENABLE_CUSTOM_CIRCUIT=0).",
        )
    if _m.CUSTOM_CIRCUIT_API_KEY and request.headers.get("x-api-key", "") != _m.CUSTOM_CIRCUIT_API_KEY:
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


# --- Field-gradient coil driver: see GRAD_COIL_* constants above. ---------

def _gradient_from_code(code: Optional[int]) -> tuple[float, float, int]:
    """Map a 12-bit DAC code to (gradient_mhz_per_um, coil_current_a, code_int).

    Returns (0.0, 0.0, 0) for None / out-of-range so the no-gradient path is the
    safe default. The spread across the pixel = gradient_mhz_per_um * pixel_um.
    """
    if code is None:
        return 0.0, 0.0, 0
    try:
        c = int(code)
    except (TypeError, ValueError):
        return 0.0, 0.0, 0
    if c <= 0:
        return 0.0, 0.0, 0
    c = min(c, 4095)
    i_coil = GRAD_COIL_VDD * c / 4096.0 / GRAD_SENSE_OHM
    grad = (i_coil / GRAD_ADDRESSING_A) * GRAD_ADDRESSING_MHZ_PER_UM
    return grad, i_coil, c


def _real_odmr_sweep(mw: MicrowaveController, det: DetectorInterface,
                     f0: float, span: float, points: int,
                     site: int = 0, amp: float = 0.5,
                     duration_ns: int = 500):
    """Run a real ODMR sweep on the board and return (f_ghz, contrast).

    For each frequency the ADF4351 is reprogrammed (``APPLY_PULSE``) and the
    site's photodetector is read (``READ_DETECTOR``). NV fluorescence *drops*
    on resonance, so the raw 16-bit ADC code is high off-resonance and dips at
    D +/- gamma*B; normalising to the peak turns that into the same
    contrast-has-a-minimum-at-the-dip shape ``simulate_odmr`` returns, so the
    host-side D/linewidth fit is identical. Each point is one serial
    round-trip, so ``points`` is capped small (<=64) by the caller.
    """
    freqs = np.linspace(f0 - span, f0 + span, int(points))
    codes = np.empty(len(freqs), dtype=np.float64)
    for i, fg in enumerate(freqs):
        mw.apply_pulse(site, float(fg), amp, duration_ns)
        codes[i] = det.read(site)
    peak = float(codes.max()) or 1.0
    contrast = codes / peak
    return freqs, contrast


def connect_fpga(*args, **kwargs):
    """Open the FPGA connection through the :mod:`api.main` namespace.

    Every route calls this instead of ``make_connection`` directly so a test
    patch of ``api.main.make_connection`` propagates to every endpoint (the
    name is resolved at call time, not at import time).
    """
    from api import main as _m
    return _m.make_connection(*args, **kwargs)