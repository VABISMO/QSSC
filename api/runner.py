# api/runner.py
"""Pure circuit-execution functions for the QSSC API.

These run a demo algorithm or a user-supplied circuit on a :class:`QSSCBackend`
and return a JSON-serialisable result dict. They hold no FastAPI / request
state, so they can be called both directly from the endpoints (synchronous
fallback) **and** from an RQ worker process (asynchronous path).
"""
from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

from qssc import (
    QSSCBackend, SimulatedFPGA, FPGAConnection,
    make_connection, NoFPGADetected,
    create_shor_circuit,
    create_math_circuit, grover_search, postprocess_period,
    select_coprime, is_prime, perfect_power,
    build_user_circuit, UserCircuitError,
)
from qssc.array_calibration import (
    ArrayCalibration, best_uniform_width,
    nv_ceiling_for_size, GRAD_COIL_MAX_MHZ_PER_UM,
    PIXEL_SIZE_MIN_UM, PIXEL_SIZE_MAX_UM, _linewidth_mhz_for,
    gradient_dac_code_from_mhz,
)
from qssc.noise_filters import AntiNoiseConfig, noise_environment_preset
# Re-exported so tests can monkeypatch api.runner.von_neumann_extract (the
# qrand runner resolves it through this module at call time -- see
# api.runner_algorithms._run_qrand).
from qssc.crypto import von_neumann_extract  # noqa: F401

# Path to the cached per-pixel array calibration. The API's
# /api/circuit/calibrate-array endpoint writes it; run_parallel reads it so
# each pixel runs at its MEASURED register width without the caller re-running
# the calibration. Repo root = parent of this api/ directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_CALIB_FILE = _REPO_ROOT / "hardware" / "calibration" / "array_calibration.json"


def _load_cached_calibration() -> ArrayCalibration | None:
    """Return the cached array calibration, or None if not yet calibrated."""
    try:
        if _CALIB_FILE.exists():
            return ArrayCalibration.from_json(_CALIB_FILE)
    except Exception as exc:  # noqa: BLE001 - a corrupt cache must never break a run
        logger.warning("calibration cache unreadable (%s); ignoring cached state", exc)
        return None
    return None


def _anti_noise_from_req(req: dict) -> AntiNoiseConfig | None:
    """Build an :class:`AntiNoiseConfig` from the request's ``anti_noise`` dict.

    Returns ``None`` (-> back-compat, no DD / no filters) when the field is
    absent, empty, or resolves to the all-off default config.
    """
    cfg = AntiNoiseConfig.from_dict(req.get("anti_noise"))
    return None if cfg.is_default() else cfg


def _backend(profile: str, temperature_k: float, seed: int, noise_mode: str,
             hardware: bool = False,
             n_nv_per_pixel: Optional[int] = None,
             pixel_mode: str = "register",
             register_nv_spacing_nm: float = 2.75,
             gradient_mhz_per_um: float = 0.0,
             pixel_size_um: float = 200.0,
             register_line_freqs_hz: Optional[list] = None,
             prime_readout: bool = False,
             n_noise_bins: int = 64,
             register_widths_per_pixel=None,
             register_line_freqs_per_pixel=None,
             anti_noise=None,
             noise_env=None,
             integration_shots: int = 1) -> QSSCBackend:
    # pixel_mode selects the circuit-qubit model: register (one pixel's N-NV
    # register, within-pixel all-to-all dipole 2q) or ensemble (64 chip pixels,
    # no within-pixel 2q). N is the MEASURED resolvable ODMR line count when
    # register_line_freqs_hz is given (the calibration's decompose_odmr at the
    # applied gradient), else the nominal default. The register auto-grows to
    # fit a wider circuit (QSSCBackend._ensure_register). Passing explicit
    # chip-view sites here would make the 64 pixels a (cross-pixel, unphysical)
    # K_64. ``gradient_mhz_per_um`` + ``register_line_freqs_hz`` close the
    # calibration -> circuit loop: the register sits on the real addressed
    # frequencies at the applied gradient, not the synthetic 6 MHz grid.
    #
    # Calibrated path: when register_widths_per_pixel is given (a
    # per-pixel MEASURED width list from calibrate_array), the backend builds
    # one independent register per pixel and run_array runs each pixel at its
    # own width (skipping narrow pixels). gradient_mhz_per_um / pixel_size_um
    # must match the calibration so each pixel's register is capped consistently.
    from qssc.backend import N_NV_PER_PIXEL
    fpga = _make_fpga(profile, temperature_k, seed, hardware)
    return QSSCBackend(profile=profile, seed=seed,
                       temperature_k=temperature_k, noise_mode=noise_mode,
                       hardware=fpga, pixel_mode=pixel_mode,
                       n_nv_per_pixel=(n_nv_per_pixel if n_nv_per_pixel is not None
                                       else N_NV_PER_PIXEL),
                       register_nv_spacing_nm=register_nv_spacing_nm,
                       gradient_mhz_per_um=gradient_mhz_per_um,
                       pixel_size_um=pixel_size_um,
                       register_line_freqs_hz=register_line_freqs_hz,
                       prime_readout=prime_readout,
                       n_noise_bins=n_noise_bins,
                       register_widths_per_pixel=register_widths_per_pixel,
                       register_line_freqs_per_pixel=register_line_freqs_per_pixel,
                       anti_noise=anti_noise,
                       noise_env=noise_env,
                       integration_shots=integration_shots)


def _pixel_kwargs(req: dict) -> dict:
    """Extract the pixel-mode + register-width params from a request payload
    (RunRequest / CustomCircuitRequest / ParallelRunRequest) for ``_backend``.
    The measured register_line_freqs_hz from the calibration's decompose_odmr
    and the applied gradient_mhz_per_um flow in here, so the backend's register
    width N is the measured truth on the real addressed frequency grid."""
    return {
        "pixel_mode": req.get("pixel_mode", "register"),
        "n_nv_per_pixel": req.get("n_nv_per_pixel"),
        "register_nv_spacing_nm": float(req.get("register_nv_spacing_nm", 2.75)),
        "gradient_mhz_per_um": float(req.get("gradient_mhz_per_um", 0.0)),
        "pixel_size_um": float(req.get("pixel_size_um", 200.0)),
        "register_line_freqs_hz": req.get("register_line_freqs_hz"),
        "prime_readout": bool(req.get("prime_readout", False)),
        "n_noise_bins": int(req.get("n_noise_bins", 64)),
        "noise_env": noise_environment_preset(str(req.get("noise_env", "none"))),
        "integration_shots": int(req.get("integration_shots", 1)),
    }


def _make_fpga(profile: str, temperature_k: float, seed, hardware: bool):
    """Return the FPGA connection for this run.

    ``hardware=False`` (the Simulator mode) -> :class:`SimulatedFPGA` (today's
    Aer + on-chip readout model). ``hardware=True`` (the Hardware real mode)
    -> a real :class:`FPGAConnection` opened on ``QSSC_FPGA_PORT``; its
    constructor runs the ``ID`` board-present probe and raises
    :class:`NoFPGADetected` if no flashed board answers, so the API surfaces a
    clear "no FPGA detected" result instead of silently simulating.

    The real connection is **cached per (port, profile, temperature, seed) and
    reused across runs** within this process (the RQ worker). Re-opening
    /dev/ttyUSB0 on every run without closing the previous handle leaks the
    port -- the OS refuses a second open while the first is unclosed, which
    surfaced as a spurious NoFPGADetected on the 2nd+ hardware run even with
    the board plugged in. A config change (port/profile/temperature/seed)
    re-opens; a failed open is not cached so the next run retries.
    """
    if not hardware:
        # Attach a SimulatedFPGA so the on-chip readout path fires per run --
        # the CQD emitter + graphene detector + lock-in model produces a
        # displayed bitstring, per-site read states and a photocurrent mean
        # that the frontend's Execution component physics card reflects the
        # circuit that actually ran (not a fixed snapshot).
        return SimulatedFPGA(profile=profile, temperature_k=temperature_k,
                             seed=seed)
    port = os.getenv("QSSC_FPGA_PORT", "").strip()
    if not port:
        raise NoFPGADetected(
            "", "QSSC_FPGA_PORT is not set — connect the board and set "
                "QSSC_FPGA_PORT (e.g. /dev/ttyUSB0), or switch to Simulator.")
    # Real board: FPGAConnection.__init__ opens the port AND probes ``ID``.
    # NoFPGADetected propagates up to the API, which returns the
    # no-fpga-detected body for the frontend banner.
    key = (port, profile, float(temperature_k), int(seed))
    global _HW_FPGA, _HW_FPGA_KEY
    if _HW_FPGA is not None and _HW_FPGA_KEY == key:
        return _HW_FPGA
    _HW_FPGA = make_connection(port=port, profile=profile,
                               temperature_k=temperature_k, seed=seed)
    _HW_FPGA_KEY = key
    return _HW_FPGA


# Cached real FPGAConnection for the worker process (see _make_fpga).
_HW_FPGA = None
_HW_FPGA_KEY = None


def _source_label(fpga) -> str:
    """'hardware' for a real FPGAConnection, 'simulator' otherwise."""
    return "hardware" if isinstance(fpga, FPGAConnection) else "simulator"


def _no_fpga_result(exc: NoFPGADetected) -> dict:
    """Structured result body for the frontend's 'no FPGA detected' banner."""
    return {"error": "no-fpga-detected",
            "message": ("No FPGA detected — connect the board and set "
                        "QSSC_FPGA_PORT (e.g. /dev/ttyUSB0), or switch to "
                        "Simulator. (" + str(exc) + ")")}


def _circuit_meta(qc) -> dict:
    return {"num_qubits": qc.num_qubits, "num_clbits": qc.num_clbits,
            "depth": qc.depth()}


@dataclass
class RunContext:
    """Per-run shared state for :func:`run_circuit` and the per-algorithm
    runners in :mod:`api.runner_algorithms`.

    Holds the backend (``backend``, mutable -- the ``qrand`` runner rebuilds it
    to reseed the readout noise) plus the run-level scalars every algorithm
    branch reads (``shots`` / ``noise_mode`` / ``seed`` / ``profile`` /
    ``temperature_k`` / ``hardware`` / ``anti_noise``). ``build`` is the factory
    that replaces the repeated ``_backend(...)`` / ``_anti_noise_from_req(...)``
    / ``_pixel_kwargs(...)`` preamble at the top of the former god-function; it
    raises :class:`NoFPGADetected` (caught by the dispatcher -> the no-fpga
    result body) exactly as the inlined ``_backend`` call did.
    """
    profile: str
    temperature_k: float
    seed: int
    shots: int
    noise_mode: str
    hardware: bool
    anti_noise: object
    backend: QSSCBackend

    @classmethod
    def build(cls, req: dict) -> "RunContext":
        """Build the run context from a request payload.

        Raises :class:`NoFPGADetected` when ``hardware=True`` and no board
        answers (the dispatcher catches it and returns the no-fpga body)."""
        profile = req.get("profile", "NV")
        temperature_k = float(req.get("temperature_k", 300.0))
        seed = int(req.get("seed", 42))
        shots = int(req.get("shots", 2048))
        noise_mode = req.get("noise_mode", "real")
        hardware = bool(req.get("hardware", False))
        anti_noise = _anti_noise_from_req(req)
        b = _backend(profile, temperature_k, seed, noise_mode,
                     hardware=hardware, anti_noise=anti_noise,
                     **_pixel_kwargs(req))
        return cls(profile=profile, temperature_k=temperature_k, seed=seed,
                   shots=shots, noise_mode=noise_mode, hardware=hardware,
                   anti_noise=anti_noise, backend=b)


# Per-algorithm runners -- mechanical extraction of the former run_circuit
# branches. Imported after the helpers above so the runners can in turn
# import ``_backend`` / ``_pixel_kwargs`` / ``_circuit_meta`` from this module
# without a circular-import gap.
from api.runner_algorithms import _DISPATCH


def run_circuit(req: dict) -> dict:
    """Execute a demo algorithm. ``req`` mirrors :class:`RunRequest`.

    Builds the :class:`RunContext` + the partial result dict, then dispatches
    to the per-algorithm runner in :mod:`api.runner_algorithms`. The runner
    mutates ``result`` in place and returns the qiskit result object (for the
    assessment / noise / timing finalizer) or an ``{"error": ...}`` dict on an
    early-return. ``NoFPGADetected`` from ``RunContext.build`` -> the no-fpga
    result body, matching the former inlined ``_backend`` call.
    """
    algo = str(req.get("algorithm", "shor")).lower()
    try:
        ctx = RunContext.build(req)
    except NoFPGADetected as exc:
        return _no_fpga_result(exc)
    src = _source_label(ctx.backend.hardware)
    result = {"algorithm": algo, "profile": ctx.profile,
              "temperature_k": ctx.temperature_k, "shots": ctx.shots,
              "noise_mode": ctx.noise_mode,
              "source": src, "io": "real" if src == "hardware" else "simulated"}
    t0 = time.perf_counter()

    # Hard cap on the integer N for the period-finding / factoring algorithms.
    # This is a TIME cap on the QUBIT COUNT (not memory, not an N value): Shor's
    # controlled mod-exp is a compiled block, so _run_real takes the statevector
    # phenomenological path (19q statevector = 4 MB, never a density matrix).
    # Each Aer run grows ~exponentially with qubit count -- 16q is ~22-50 s,
    # 19q (N>=32 for shor) is ~60-90 s for a single attempt -- so we cap at 19
    # qubits and run only ONE attempt in the 17-19q band (max_attempts below).
    # shor/period = 3*bitlen(N)+1 -> N <= 63; qftnos = 2*bitlen(N)+1 -> N <= 511.
    # There is NO per-mode limit: in real mode at 300 K the deeper circuit
    # decoheres to factors=None (the real T2 physics), and at low temperature (or
    # in ideal/no-noise mode) it factors. Let the physics show, not an arbitrary
    # rejection.

    fn = _DISPATCH.get(algo)
    if fn is None:
        return {"error": f"unknown algorithm '{algo}'"}
    res = fn(ctx, req, result)
    # An early-return runner yields an {"error": ...} dict -- return it
    # immediately, skipping the assessment / noise / timing finalizer
    # (matching the original branches' early ``return {"error": ...}``).
    if isinstance(res, dict) and "error" in res:
        return res

    result["assessment"] = getattr(res, "assessment", {})
    result["approximate"] = getattr(res, "approximate", False)
    if getattr(res, "optical", None) is not None:
        result["optical"] = res.optical
    # Per-run noise budget so the frontend can compare prime readout on vs
    # off: the base optical readout error, the prime SNR improvement factor,
    # and the EFFECTIVE readout error after the coprime matched-filter denoiser
    # (equal to the base when prime_readout is off).
    result["noise"] = ctx.backend.noise_summary()
    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return result


# Algorithms routed to the parallel `sample` mode (one circuit per pixel, same
# circuit on every pixel -> n_pixels x shots aggregated). Mirrors run_circuit's
# per-algorithm construction but builds ONLY the circuit (no execution / no
# per-algorithm interpretation -- the parallel path aggregates counts across
# pixels; the shor-family race is handled by mode='shor', not here).
_SAMPLE_ALGOS = ("bell", "grover", "qft", "math", "qrand")


def _build_pixel_circuit(algo: str, req: dict):
    """Build a single QuantumCircuit for one pixel in parallel ``sample`` mode.

    Returns ``(qc, label)``. Falls back to H+measure on ``num_qubits`` for any
    algorithm not in :data:`_SAMPLE_ALGOS` (bb84 / custom / unknown).
    """
    from qiskit import QuantumCircuit
    algo = (algo or "").lower()
    if algo == "bell":
        n = max(2, int(req.get("num_qubits", 2)))
        qc = QuantumCircuit(n, n)
        qc.h(0)
        for i in range(n - 1):
            qc.cx(i, i + 1)
        qc.measure(range(n), range(n))
        return qc, "bell"
    if algo == "grover":
        from qiskit import QuantumRegister
        nq = max(2, min(8, int(req.get("n_items") or req.get("num_qubits") or 3)))
        marked = int(req.get("marked") or 0) % (2 ** nq)
        qr = QuantumRegister(nq, "o")
        oracle = QuantumCircuit(qr)
        for i in range(nq):
            if not ((marked >> i) & 1):
                oracle.x(qr[i])
        oracle.h(qr[-1])
        oracle.mcx(list(range(nq - 1)), qr[-1])
        oracle.h(qr[-1])
        for i in range(nq):
            if not ((marked >> i) & 1):
                oracle.x(qr[i])
        iters = max(1, int(round(math.pi / 4 * math.sqrt(2 ** nq))))
        qc = grover_search(nq, oracle, iterations=iters)
        return qc, "grover"
    if algo == "qft":
        n = max(2, min(64, int(req.get("num_qubits", 4))))
        x = int(req.get("x", 1)) % (2 ** n)
        qc = QuantumCircuit(n, n)
        for i in range(n):
            if (x >> i) & 1:
                qc.x(i)
        try:
            from qiskit.circuit.library import QFTGate
            gate = QFTGate(n)
        except Exception:  # noqa: BLE001 - older qiskit fallback
            from qiskit.circuit.library import QFT
            gate = QFT(n).to_gate()
        qc.append(gate, range(n))
        qc.measure(range(n), range(n))
        return qc, "qft"
    if algo == "math":
        op = req.get("op", "add")
        num1 = float(req.get("a") if req.get("a") is not None
                     else req.get("num1", 2.0))
        raw_b = req.get("b") if req.get("b") is not None else req.get("num2")
        num2 = float(raw_b) if raw_b is not None else None
        qc = create_math_circuit(op, num1, num2, bits=req.get("bits"))
        return qc, "math"
    if algo == "qrand":
        n = max(2, min(64, int(req.get("num_qubits", 8))))
        qc = QuantumCircuit(n, n)
        qc.h(range(n))
        qc.measure(range(n), range(n))
        return qc, "qrand"
    # bb84 / custom / unknown -> H+measure on num_qubits (the parallel aggregate
    # of a single-qubit protocol is not meaningful; show the uniform sample).
    nq = max(1, min(64, int(req.get("num_qubits", 4))))
    qc = QuantumCircuit(nq, nq)
    qc.h(range(nq))
    qc.measure(range(nq), range(nq))
    return qc, algo or "sample"


def _postprocess_sample(algo, req, qc, counts, shots):
    """Derive the per-algorithm result fields from the parallel ``sample`` mode
    AGGREGATE counts so the 64-pixel measurement gets the SAME green result
    marker + derivation modal as the single-pixel run.

    Mirrors the postprocess in :func:`run_circuit`'s bell/grover/qft/math
    branches, but operates on the n_pixels x shots aggregate (the histogram the
    dashboard shows) instead of a single pixel's counts -- so the marked bar IS
    the top aggregate bar. ``counts`` is the aggregate dict; ``shots`` is the
    TOTAL aggregate shots (n_pixels x per-pixel shots). Returns a dict to merge
    into the parallel result. bell needs no backend fields (the frontend derives
    it from counts + num_clbits); qrand/bb84 have no single "result bar" in the
    aggregate (every bar is a random / protocol bit), so they return {} and the
    frontend shows a 64x-ensemble derivation.
    """
    algo = (algo or "").lower()
    if algo == "grover":
        nq = max(2, min(8, int(req.get("n_items") or req.get("num_qubits") or 3)))
        marked = int(req.get("marked") or 0) % (2 ** nq)
        iters = max(1, int(round(math.pi / 4 * math.sqrt(2 ** nq))))
        bs = max(counts, key=counts.get) if counts else None
        found = int(bs.replace(" ", ""), 2) if (bs and qc.num_clbits) else 0
        return {"n_items": nq, "marked": marked, "counts": counts,
                "found": found, "success": found == marked, "iterations": iters}
    if algo == "qft":
        n = max(2, min(64, int(req.get("num_qubits", 4))))
        x = int(req.get("x", 1)) % (2 ** n)
        states = 2 ** n
        expected = shots / states if states else 0
        observed = list(counts.values()) if counts else []
        chi2_norm = ((sum((c - expected) ** 2 / expected for c in observed)
                      / max(1, states)) if expected else 0.0)
        return {"n": n, "x": x, "counts": counts, "basis_states": states,
                "expected_per_state": round(expected, 2),
                "uniformity_chi2": round(chi2_norm, 3),
                "uniform": bool(chi2_norm < 2.0), "qft": "forward"}
    if algo == "math":
        op = req.get("op", "add")
        num1 = float(req.get("a") if req.get("a") is not None
                     else req.get("num1", 2.0))
        raw_b = req.get("b") if req.get("b") is not None else req.get("num2")
        num2 = float(raw_b) if raw_b is not None else None
        bs = max(counts, key=counts.get) if counts else None
        _rw = qc.metadata.get("result_width", qc.num_clbits) if qc.metadata else qc.num_clbits
        out = int(bs.replace(" ", ""), 2) & ((1 << _rw) - 1) if bs else 0
        if op in ("sqrt", "log"):
            out = (out / 2 ** _rw) * num1
        return {"op": op, "a": num1, "b": num2, "counts": counts,
                "result": out, "result_width": _rw}
    # bell / qrand / bb84 / custom / unknown: no extra derive fields here.
    return {}


def run_parallel(req: dict) -> dict:
    """Run one circuit per pixel across the 64-pixel array IN PARALLEL and
    aggregate (distributed computing).

    The 64 die pixels are INDEPENDENT ~10-NV processors at 300 K (1/r**3 -> 0
    at the 2.5 mm pitch): the chip's compute throughput is 64x a single pixel,
    not one 640-qubit circuit. Three modes:

      * ``shor``  -- up to ``n_pixels`` coprime bases ``a`` (one per pixel)
        factor ``n``; whichever pixel finds the period wins, the rest fail.
      * ``sample`` -- the same circuit (QASM) on all ``n_pixels`` pixels with
        independent seeds = n_pixels x shots aggregated in one wall-clock step.
      * ``custom`` -- a list of QASM circuits, one per pixel.

    Returns ``{mode, n_pixels, throughput_x, per_pixel, aggregate_counts, ...}``.
    """
    t0 = time.perf_counter()
    profile = req.get("profile", "NV")
    temperature_k = float(req.get("temperature_k", 300.0))
    seed = int(req.get("seed", 42))
    shots = int(req.get("shots", 2048))
    noise_mode = req.get("noise_mode", "real")
    mode = str(req.get("mode", "sample")).lower()
    n_pixels = max(1, min(64, int(req.get("n_pixels", 64))))
    anti_noise = _anti_noise_from_req(req)
    # When the caller opts in (use_calibration=true), load the
    # cached per-pixel calibration so each pixel runs at its MEASURED register
    # width (skip-narrow). Default False = the uniform-register back-compat
    # path (the existing behaviour; calibrate first via /api/circuit/calibrate-
    # array, then set use_calibration=true).
    use_cal = bool(req.get("use_calibration", False))
    calib = _load_cached_calibration() if use_cal else None
    reg_widths = None
    reg_freqs_per_pixel = None
    grad = 0.0
    psz = 200.0
    if calib is not None and calib.profile.lower() == str(profile).lower():
        reg_widths = calib.widths
        grad = calib.gradient_mhz_per_um
        psz = calib.pixel_size_um
        # Close the calibration -> circuit loop: feed each pixel's
        # MEASURED ODMR line centres (GHz -> Hz) back so the register sits on the
        # real addressed frequency grid, not auto_align's synthetic 6 MHz grid.
        # The frontend's per-pixel qubits+freqs modal then shows the measured
        # frequencies. Pixels with empty odmr_lines fall back to the synthetic
        # grid (auto_align with register_line_freqs_hz=None).
        reg_freqs_per_pixel = [
            ([round(g * 1e9) for g in p.odmr_lines] if p.odmr_lines else None)
            for p in calib.pixels
        ]
    try:
        kw = _pixel_kwargs(req)
        kw["anti_noise"] = anti_noise
        if reg_widths is not None:
            # The calibration's gradient + pixel_size are the truth for the
            # per-pixel registers; override the request's defaults so each
            # pixel's register is capped consistently with the measurement.
            kw["gradient_mhz_per_um"] = grad
            kw["pixel_size_um"] = psz
            # The per-pixel measured width only gates the SAMPLE/CUSTOM modes
            # (each pixel runs at its own width -> narrow pixels skipped). The
            # SHOR race uses a FIXED circuit width (3*bitlen(N)+1) set by N, not
            # by the pixel -- applying register_widths_per_pixel there would skip
            # every pixel whose measured width < the Shor width (e.g. Shor(15)=13q
            # vs measured widths 5-12 -> 0 pixels run -> no factors, empty
            # histogram). So do NOT gate the shor race by the per-pixel width.
            if mode != "shor":
                kw["register_widths_per_pixel"] = reg_widths
                if reg_freqs_per_pixel is not None:
                    kw["register_line_freqs_per_pixel"] = reg_freqs_per_pixel
        b = _backend(profile, temperature_k, seed, noise_mode,
                     hardware=False, **kw)
    except NoFPGADetected as exc:
        return _no_fpga_result(exc)

    out = {"mode": mode, "profile": profile, "n_pixels": n_pixels,
           "throughput_x": n_pixels, "noise_mode": noise_mode,
           "temperature_k": temperature_k, "shots": shots,
           "calibrated": reg_widths is not None}
    if reg_widths is not None:
        bw = best_uniform_width(reg_widths[:n_pixels])
        out["per_pixel_widths"] = reg_widths[:n_pixels]
        out["best_uniform_width"] = bw[0]
        out["best_uniform_pixels"] = bw[1]
        # Always report the calibrated width range so the frontend can show it
        # even when no pixel was skipped (the per-mode branches below only set
        # min/max_width when skipped_pixels is present).
        out["min_width"] = int(np.min(reg_widths[:n_pixels]))
        out["max_width"] = int(np.max(reg_widths[:n_pixels]))

    if mode == "shor":
        n = int(req.get("n", 15))
        if n < 2 or is_prime(n):
            return {"error": "n must be a composite integer > 1"}
        nq = 3 * n.bit_length() + 1
        if nq > 19:
            return {"error": f"N={n} -> {nq} qubits too large (>19). Use N <= 63."}
        rng = np.random.default_rng(seed)
        circuits = []
        bases = []
        for _ in range(n_pixels):
            a = select_coprime(n, rng)
            qc, _ = create_shor_circuit(n, a=a, rng=rng)
            circuits.append(qc)
            bases.append(a)
        res = b.run_array(circuits, shots=shots, max_pixels=n_pixels)
        # Prime power N = p^k: the quantum gcd step is trivial (a^(r/2) = +/-1
        # mod p^k -> gcd is 1 or N), so the factor is recovered classically.
        # Mirrors run_circuit's shor branch so the 64-shor derivation can show
        # the SAME full phase->period->gcd chain (plus the race context).
        pp = perfect_power(n)
        per_pixel = []
        winner = None
        for i, (r, a) in enumerate(zip(res["per_pixel"], bases)):
            if isinstance(r, dict) and r.get("skipped"):
                per_pixel.append({"pixel": i, "a": a, "skipped": True,
                                  "reason": r.get("reason"),
                                  "pixel_width": r.get("pixel_width"),
                                  "circuit_width": r.get("circuit_width")})
                continue
            counts = r.get_counts()
            _ret = (None if noise_mode == "none"
                    else float(r.assessment.get("signal_retention", 0.0) or 0.0))
            cb = circuits[i].metadata["counting_bits"]
            _reveal = {}
            r_period, factors = postprocess_period(
                counts, cb, a, n, signal_retention=_ret, reveal=_reveal)
            # prime power: classical factors, keep the measured phase peak
            if pp is not None:
                factors = (pp[0], n // pp[0])
            ok = bool(factors[0]) and factors[0] * factors[1] == n
            entry = {"pixel": i, "a": a, "period": r_period,
                     "factors": list(factors) if factors[0] else None,
                     "success": ok, "counts": counts,
                     "counting_bits": cb,
                     "recovered": (_reveal if _reveal.get("bitstring") else None)}
            per_pixel.append(entry)
            if ok and winner is None:
                winner = entry
        out.update({"n": n, "per_pixel": per_pixel,
                    "aggregate_counts": res["aggregate_counts"],
                    "factors": (list(winner["factors"]) if winner else None),
                    "success": winner is not None,
                    "winner_pixel": (winner["pixel"] if winner else None),
                    "winner_base": (winner["a"] if winner else None),
                    "period": (winner["period"] if winner else None),
                    "a": (winner["a"] if winner else None),
                    "counting_qubits": (winner["counting_bits"] if winner else None),
                    "num_qubits": circuits[0].num_qubits,
                    "num_clbits": circuits[0].num_clbits})
        if pp is not None:
            out["prime_power"] = {"base": pp[0], "exp": pp[1]}
        if winner and winner.get("recovered"):
            out["recovered"] = {
                "bitstring": winner["recovered"]["bitstring"],
                "phase": round(float(winner["recovered"]["phase"]), 6),
                "count": winner["recovered"]["count"],
                "period": winner["recovered"]["period"],
                "factors": list(winner["factors"]) if winner["factors"][0] else None,
            }
            if pp is not None:
                out["recovered"]["prime_power"] = {"base": pp[0], "exp": pp[1]}
        if "skipped_pixels" in res:
            out["skipped_pixels"] = res["skipped_pixels"]
            out["n_run"] = res["n_run"]
            out["min_width"] = res["min_width"]
            out["max_width"] = res["max_width"]

    elif mode == "sample":
        from qiskit import QuantumCircuit
        # build the same circuit on every pixel (independent seeds -> 64x shots).
        # When the request carries an `algorithm` (bell/grover/qft/math/qrand),
        # build THAT circuit per pixel via _build_pixel_circuit so the 64-parallel
        # run card reuses the single-pixel algorithm menu; otherwise fall back to
        # QASM (if provided) or a uniform H+measure sample.
        qasm = req.get("qasm")
        algo = str(req.get("algorithm", "")).lower()
        if qasm:
            from qiskit import qasm2
            qc0 = qasm2.loads(str(qasm)) if isinstance(qasm, str) else list(qasm)[0]
            label = "qasm"
        elif algo and algo != "sample":
            qc0, label = _build_pixel_circuit(algo, req)
        else:
            nq = max(1, min(64, int(req.get("num_qubits", 4))))
            qc0 = QuantumCircuit(nq, nq)
            qc0.h(range(nq))
            qc0.measure(range(nq), range(nq))
            label = "sample"
        circuits = [qc0] * n_pixels
        res = b.run_array(circuits, shots=shots, max_pixels=n_pixels)
        per_pixel = []
        for i, r in enumerate(res["per_pixel"]):
            if isinstance(r, dict) and r.get("skipped"):
                per_pixel.append({"pixel": i, "skipped": True,
                                  "reason": r.get("reason"),
                                  "pixel_width": r.get("pixel_width"),
                                  "circuit_width": r.get("circuit_width")})
                continue
            per_pixel.append({"pixel": i, "counts": r.get_counts(),
                              "approximate": r.approximate})
        n_run = sum(1 for p in per_pixel if not p.get("skipped"))
        out.update({"algorithm": label, "num_qubits": qc0.num_qubits,
                    "num_clbits": qc0.num_clbits,
                    "per_pixel": per_pixel,
                    "aggregate_counts": res["aggregate_counts"],
                    "total_shots": shots * n_run,
                    "n_run": n_run})
        # Per-algorithm result fields from the AGGREGATE counts so the 64-pixel
        # measurement gets the same green result marker + derivation modal as
        # the single-pixel run (found/marked/uniform/result/...). bell derives
        # client-side; qrand/bb84 return {} (no single result bar).
        if algo and algo != "sample" and not qasm:
            out.update(_postprocess_sample(algo, req, qc0,
                                            res["aggregate_counts"],
                                            shots * n_run))
        if "skipped_pixels" in res:
            out["skipped_pixels"] = res["skipped_pixels"]
            out["min_width"] = res["min_width"]
            out["max_width"] = res["max_width"]
        if "per_pixel_odmr_lines" in res:
            out["per_pixel_odmr_lines"] = res["per_pixel_odmr_lines"]

    elif mode == "custom":
        qasms = req.get("circuits", [])
        if not qasms:
            return {"error": "mode=custom requires a non-empty 'circuits' list"}
        from qiskit import qasm2
        circuits = []
        for q in qasms[:n_pixels]:
            circuits.append(qasm2.loads(str(q)))
        res = b.run_array(circuits, shots=shots, max_pixels=len(circuits))
        per_pixel = []
        for i, r in enumerate(res["per_pixel"]):
            if isinstance(r, dict) and r.get("skipped"):
                per_pixel.append({"pixel": i, "skipped": True,
                                  "reason": r.get("reason"),
                                  "pixel_width": r.get("pixel_width"),
                                  "circuit_width": r.get("circuit_width"),
                                  "num_qubits": circuits[i].num_qubits})
                continue
            per_pixel.append({"pixel": i, "counts": r.get_counts(),
                              "num_qubits": circuits[i].num_qubits,
                              "approximate": r.approximate})
        n_run = sum(1 for p in per_pixel if not p.get("skipped"))
        out.update({"n_pixels": len(circuits), "throughput_x": n_run,
                    "n_run": n_run,
                    "per_pixel": per_pixel, "aggregate_counts": res["aggregate_counts"]})
        if "skipped_pixels" in res:
            out["skipped_pixels"] = res["skipped_pixels"]
            out["min_width"] = res["min_width"]
            out["max_width"] = res["max_width"]
        if "per_pixel_odmr_lines" in res:
            out["per_pixel_odmr_lines"] = res["per_pixel_odmr_lines"]
    else:
        return {"error": f"unknown parallel mode '{mode}' "
                        f"(use shor | sample | custom)"}

    out["noise"] = b.noise_summary()
    out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return out


def run_calibrate_array(req: dict) -> dict:
    """Calibrate the 64-pixel array's per-pixel register width.

    Mirror of the ``/api/circuit/calibrate-array`` endpoint logic, factored out
    so it can run as an async RQ job (the sim path synthesizes 64 ODMR spectra
    and runs ``decompose_odmr`` on each — ~30 s — so it must NOT block the
    uvicorn event loop). ``source="sim"`` synthesizes each pixel's spectrum;
    ``source="real"`` drives the ``RUN_ALL_ODMR`` firmware verb. The result is
    persisted to ``hardware/calibration/array_calibration.json`` and returned.
    """
    from qssc.array_calibration import (
        calibrate_array, calibrate_array_from_codes,
    )
    from qssc.defects import get_profile
    from qssc.detector_centroid import SubPixelModel
    from qssc.noise_filters import (
        noise_environment_preset, effective_noise_sigma, filter_gain_breakdown,
    )
    from qssc.simulator import READOUT_NOISE, BRIGHT_CODE
    import numpy as _np
    profile = req.get("profile", "NV")
    prof = get_profile(profile)
    source = str(req.get("source", "sim")).lower()
    # Build the anti-noise stack from the request. noise_env="none"
    # -> None (no environmental channels, back-compat); anti_noise empty -> the
    # all-off default config (back-compat).
    noise_env = noise_environment_preset(str(req.get("noise_env", "none")))
    anti_noise = AntiNoiseConfig.from_dict(req.get("anti_noise"))
    # Build the sub-pixel centroiding model if requested.
    sub_pixel = (SubPixelModel(sigma_sheet_mm=float(req.get("sigma_sheet_mm", 1.0)))
                 if bool(req.get("sub_pixel", False)) else None)
    # Line-recovery mode (superres takes precedence over bayesian).
    superres = req.get("superres")
    bayesian = req.get("bayesian")
    if superres is not None:
        superres = float(superres)
    if bayesian is not None:
        bayesian = float(bayesian)
    n_shots = max(1, int(req.get("integration_shots", 1)))
    try:
        if source == "real":
            fpga = _make_fpga(profile, float(req.get("temperature_k", 300.0)),
                              int(req.get("seed", 42)), hardware=True)
            if not hasattr(fpga, "run_all_odmr"):
                return {"error": "real calibration needs a board with run_all_odmr "
                                 "(set QSSC_FPGA_PORT or use source=sim)"}
            d_khz = int(prof.zero_field_splitting / 1e3)
            spread_khz = int(float(req.get("gradient_mhz_per_um", 0.5))
                             * float(req.get("pixel_size_um", 200.0)) * 1e3)
            span_khz = max(spread_khz + int(prof.odmr_linewidth_hz / 1e3) * 4,
                           int(prof.odmr_linewidth_hz / 1e3) * 6)
            f_start = d_khz - span_khz
            f_stop = d_khz + span_khz
            n = int(req.get("n_points", 400))
            # Program the gradient coil BEFORE the sweep so each
            # pixel's ODMR lines spread across the pixel by grad*pixel_size,
            # which is what makes the lines resolvable (-> per-pixel register
            # width). Without this the 64 pixels all see one collapsed line.
            # Mirror of the single-pixel calibration path: a failed coil write
            # must not abort the sweep, but the reply must NOT claim the
            # gradient was applied when it was not (the width measurement is
            # only valid at the applied gradient).
            grad_mhz = float(req.get("gradient_mhz_per_um", 0.5))
            explicit_code = req.get("gradient_code")
            grad_code = (int(explicit_code) if explicit_code is not None
                         else gradient_dac_code_from_mhz(grad_mhz))
            gradient_applied_ok = False
            if grad_code > 0:
                try:
                    fpga.send_command(f"SET_GRADIENT {grad_code}")
                    gradient_applied_ok = True
                except Exception as exc:  # noqa: BLE001 - do not abort the sweep
                    gradient_applied_ok = False
                    logger.debug("gradient coil write failed in run_parallel: %s", exc)
            # N-shot lock-in integration. Average N independent
            # RUN_ALL_ODMR sweeps -> per-point readout noise drops by sqrt(N)
            # (uncorrelated thermal/Johnson noise), so dimmer lone-NV ODMR bins
            # clear the min_snr bar and the MEASURED width rises. Cost is
            # measurement time xN, not silicon. integration_shots=1 -> one sweep
            # (unchanged). Each sweep is a single-shot (n,64) matrix.
            if n_shots > 1:
                acc = None
                for _ in range(n_shots):
                    m = _np.asarray(fpga.run_all_odmr(f_start, f_stop, n),
                                    dtype=float)
                    acc = m if acc is None else acc + m
                matrix = (acc / n_shots).astype(_np.int32)
            else:
                matrix = fpga.run_all_odmr(f_start, f_stop, n)
            freqs_ghz = _np.linspace(f_start * 1e3, f_stop * 1e3, n) / 1e9
            calib = calibrate_array_from_codes(
                matrix, freqs_ghz, prof,
                gradient_mhz_per_um=float(req.get("gradient_mhz_per_um", 0.5)),
                pixel_size_um=float(req.get("pixel_size_um", 200.0)),
                seed=int(req.get("seed", 42)),
                temperature_k=float(req.get("temperature_k", 300.0)),
                integration_shots=n_shots, sub_pixel=sub_pixel,
                noise_env=noise_env, anti_noise=anti_noise,
                superres=superres, bayesian=bayesian)
            io_label = "real"
        else:
            calib = calibrate_array(
                profile,
                gradient_mhz_per_um=float(req.get("gradient_mhz_per_um", 0.5)),
                pixel_size_um=float(req.get("pixel_size_um", 200.0)),
                seed=int(req.get("seed", 42)),
                temperature_k=float(req.get("temperature_k", 300.0)),
                max_nv_per_microdiamond=int(req.get("max_nv_per_microdiamond", 300)),
                integration_shots=n_shots, sub_pixel=sub_pixel,
                noise_env=noise_env, anti_noise=anti_noise,
                superres=superres, bayesian=bayesian)
            io_label = "simulated"
    except NoFPGADetected as exc:
        return {"error": "no-fpga-detected",
                "message": ("No FPGA detected — connect the board and set "
                            "QSSC_FPGA_PORT, or use source=sim. (" + str(exc) + ")")}
    except Exception as exc:  # noqa: BLE001 - surface sim errors, not a 500
        return {"error": str(exc)}
    # persist the calibration cache
    try:
        _CALIB_FILE.parent.mkdir(parents=True, exist_ok=True)
        calib.to_json(_CALIB_FILE)
    except Exception as exc:  # noqa: BLE001 - a cache write failure must not break the reply
        logger.debug("calibration cache write failed: %s", exc)
    bw = best_uniform_width(calib.widths)
    # Which line-recovery mode produced the widths.
    if superres is not None and superres > 0.0:
        readout_mode = f"superres({superres})"
    elif bayesian is not None and 0.0 < bayesian < 1.0:
        readout_mode = f"bayesian({bayesian})"
    else:
        readout_mode = "hard-threshold"
    # Report the effective noise + per-filter gain breakdown so the
    # frontend can show what each filter bought. Collapses to the prior values
    # when the stack is unset (noise_env="none", anti_noise empty).
    eff_noise = effective_noise_sigma(
        READOUT_NOISE, noise_env, anti_noise, calib.integration_shots)
    filter_gain = filter_gain_breakdown(noise_env, anti_noise)
    return {
        "profile": calib.profile,
        "source": io_label,
        "d_fit_ghz": round(prof.zero_field_splitting / 1e9, 6),
        "gradient_mhz_per_um": calib.gradient_mhz_per_um,
        # The MCP4921 DAC code programmed into the gradient coil
        # before the sweep (None for the sim path, which models the gradient
        # analytically and never touches the coil). gradient_applied_ok is only
        # meaningful for source=real; it is True when the board acknowledged
        # SET_GRADIENT (the per-pixel widths are only valid when it is).
        "gradient_code": (grad_code if source == "real" else None),
        "gradient_applied_ok": (gradient_applied_ok if source == "real" else None),
        "pixel_size_um": calib.pixel_size_um,
        "seed": calib.seed,
        "temperature_k": calib.temperature_k,
        "n_pixels": calib.n_pixels,
        "per_pixel_widths": calib.widths,
        "min_width": calib.min_width,
        "max_width": calib.max_width,
        "best_uniform_width": bw[0],
        "best_uniform_pixels": bw[1],
        "total_working_qubits": bw[0] * bw[1],
        # N-shot lock-in integration (raises measured width).
        "integration_shots": calib.integration_shots,
        # The per-pixel line-recovery mode that produced the
        # widths (hard-threshold / superres(factor) / bayesian(confidence)).
        "readout_mode": readout_mode,
        # Sub-pixel detector imaging resolution (does NOT change
        # register_width -- within-pad spots merge; centroiding localizes one).
        "sub_pixel_factor": calib.sub_pixel_factor,
        "spatial_channels": calib.spatial_channels,
        # Classical anti-noise filter stack. effective_noise_rel is
        # the post-filter readout noise as a fraction of BRIGHT_CODE; filter_gain
        # is the per-channel residual breakdown; noise_env/anti_noise echo the
        # config. All collapse to back-compat values when the stack is unset.
        "noise_env": str(req.get("noise_env", "none")),
        "anti_noise": (anti_noise.to_dict()
                       if not anti_noise.is_default() else None),
        "effective_noise_counts": round(eff_noise, 2),
        "effective_noise_rel": round(eff_noise / BRIGHT_CODE, 6),
        "filter_gain": filter_gain,
        "pixels": [
            {"pixel_id": p.pixel_id, "nv_present": p.nv_present,
             "microdiamonds": p.microdiamonds, "register_width": p.register_width,
             "odmr_lines": p.odmr_lines,
             "sub_pixel_position": p.sub_pixel_position}
            for p in calib.pixels
        ],
        # Physically-possible caps the inputs were clamped to (mirror the
        # frontend limits). Lets the UI show the achievable ceiling, not an
        # impossible request.
        "physics_limits": {
            "gradient_max_mhz_per_um": GRAD_COIL_MAX_MHZ_PER_UM,
            "pixel_size_min_um": PIXEL_SIZE_MIN_UM,
            "pixel_size_max_um": PIXEL_SIZE_MAX_UM,
            "nv_ceiling": nv_ceiling_for_size(calib.pixel_size_um),
            "linewidth_mhz": _linewidth_mhz_for(calib.profile),
        },
    }


def run_custom(req: dict) -> dict:
    """Execute a user-supplied Qiskit circuit (QASM2 or Python snippet)."""
    t0 = time.perf_counter()
    try:
        qc = build_user_circuit(req.get("code", ""),
                                max_qubits=int(req.get("max_qubits", 12)))
    except UserCircuitError as exc:
        return {"error": str(exc)}
    profile = req.get("profile", "NV")
    hardware = bool(req.get("hardware", False))
    try:
        b = _backend(profile, float(req.get("temperature_k", 300.0)),
                     int(req.get("seed", 42)), req.get("noise_mode", "real"),
                     hardware=hardware, anti_noise=_anti_noise_from_req(req),
                     **_pixel_kwargs(req))
    except NoFPGADetected as exc:
        return _no_fpga_result(exc)
    try:
        res = b.run(qc, shots=int(req.get("shots", 2048))).result()
        counts = res.get_counts()
    except Exception as exc:  # noqa: BLE001 - surface runtime errors to the user
        return {"error": f"run failed: {exc}"}
    src = _source_label(b.hardware)
    return {
        "algorithm": "custom", "profile": profile,
        "temperature_k": float(req.get("temperature_k", 300.0)),
        "shots": int(req.get("shots", 2048)),
        "noise_mode": req.get("noise_mode", "real"),
        "source": src, "io": "real" if src == "hardware" else "simulated",
        "counts": counts,
        "num_qubits": qc.num_qubits, "num_clbits": qc.num_clbits,
        "depth": qc.depth(), "ops": qc.count_ops(),
        "assessment": getattr(res, "assessment", {}),
        "approximate": getattr(res, "approximate", False),
        "noise": b.noise_summary(),
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        **({"optical": res.optical} if getattr(res, "optical", None) is not None else {}),
    }