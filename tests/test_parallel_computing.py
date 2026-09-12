"""Invention 4 — 64-parallel distributed computing.

The 64 die pixels are INDEPENDENT ~10-NV processors at 300 K (1/r**3 -> zero at
the 2.5 mm pitch, so no cross-pixel coupling): the chip's compute throughput is
64x a single pixel, not one 640-qubit machine (64x10 > 63-qubit MPS cap).
``run_array`` runs one circuit per pixel (independent noise realizations) and
aggregates.
"""
from __future__ import annotations

from qiskit import QuantumCircuit

from qssc.backend import QSSCBackend, N_SITES


def _bell(n=2):
    qc = QuantumCircuit(n, n)
    qc.h(0)
    for i in range(n - 1):
        qc.cx(i, i + 1)
    qc.measure(range(n), range(n))
    return qc


def test_run_array_runs_one_circuit_per_pixel():
    """run_array runs N circuits (one per pixel) and returns per-pixel results
    + aggregated counts."""
    b = QSSCBackend("NV", seed=42, noise_mode="real")
    circs = [_bell(2) for _ in range(8)]
    out = b.run_array(circs, shots=256)
    assert out["n_pixels"] == 8
    assert out["throughput_x"] == 8
    assert len(out["per_pixel"]) == 8
    agg = out["aggregate_counts"]
    # 8 pixels x 256 shots = 2048 total counts aggregated
    assert sum(agg.values()) == 8 * 256


def test_run_array_caps_at_64_pixels():
    """The pixel count is capped at the die size N_SITES=64."""
    b = QSSCBackend("NV", seed=42, noise_mode="real")
    circs = [_bell(2) for _ in range(80)]
    out = b.run_array(circs, shots=64)
    assert out["n_pixels"] == N_SITES == 64
    assert sum(out["aggregate_counts"].values()) == 64 * 64


def test_run_array_pixels_are_independent_realizations():
    """Each pixel gets an independent noise realization (per-pixel seed
    offset), so the per-pixel counts differ (not identical copies)."""
    b = QSSCBackend("NV", seed=42, noise_mode="real")
    circs = [_bell(3) for _ in range(16)]
    out = b.run_array(circs, shots=512)
    counts = [tuple(sorted(r.get_counts().items())) for r in out["per_pixel"]]
    # not all identical (independent seeds -> noise differs)
    assert len(set(counts)) > 1


def test_run_array_aggregate_equals_64x_shots():
    """Parallel sampling: the same circuit on all 64 pixels with independent
    seeds = 64x the shots of one pixel, aggregated."""
    b = QSSCBackend("NV", seed=42, noise_mode="ideal")
    circs = [_bell(2) for _ in range(64)]
    out = b.run_array(circs, shots=128)
    assert sum(out["aggregate_counts"].values()) == 64 * 128
    # ideal Bell -> aggregate top states 00/11 ~50/50
    agg = out["aggregate_counts"]
    total = sum(agg.values())
    p00 = agg.get("00", 0) / total
    p11 = agg.get("11", 0) / total
    assert 0.4 < p00 < 0.6 and 0.4 < p11 < 0.6


def test_run_array_empty():
    b = QSSCBackend("NV", seed=42, noise_mode="real")
    out = b.run_array([], shots=64)
    assert out["n_pixels"] == 0
    assert out["aggregate_counts"] == {}


def test_parallel_shor_runner():
    """The API runner's parallel-shor mode factors N by running coprime bases
    across pixels; the first pixel to find the period wins."""
    from api.runner import run_parallel
    res = run_parallel({
        "mode": "shor", "n": 15, "n_pixels": 8, "shots": 2048,
        "noise_mode": "none", "seed": 42,  # ideal so at least one pixel factors
    })
    assert res.get("mode") == "shor"
    assert res.get("n_pixels") == 8
    assert len(res["per_pixel"]) == 8
    assert res["success"] is True
    assert res["factors"] is not None
    assert res["factors"][0] * res["factors"][1] == 15
    # the winner is one of the pixels
    assert res["winner_pixel"] is not None


def test_parallel_sample_runner():
    """The API runner's parallel-sample mode runs the same circuit on every
    pixel with independent seeds = n_pixels x shots aggregated."""
    from api.runner import run_parallel
    res = run_parallel({
        "mode": "sample", "n_pixels": 16, "num_qubits": 3,
        "shots": 256, "noise_mode": "ideal", "seed": 42,
    })
    assert res["mode"] == "sample"
    assert res["n_pixels"] == 16
    assert res["total_shots"] == 16 * 256
    assert sum(res["aggregate_counts"].values()) == 16 * 256


def test_parallel_endpoint():
    """The /api/circuit/run-parallel endpoint serves a parallel-sample run."""
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    r = c.post("/api/circuit/run-parallel", json={
        "mode": "sample", "n_pixels": 8, "num_qubits": 2,
        "shots": 256, "noise_mode": "ideal", "seed": 1,
    })
    assert r.status_code == 200
    j = r.json()
    assert j["mode"] == "sample"
    assert j["n_pixels"] == 8
    assert sum(j["aggregate_counts"].values()) == 8 * 256


# -- Invention 4+5: calibrated per-pixel width -------------------------------
def _calibrated_backend(widths, seed=42):
    """Build a backend with a measured per-pixel width list."""
    return QSSCBackend("NV", seed=seed, noise_mode="real",
                       register_widths_per_pixel=widths)


def test_run_array_skips_narrow_pixels():
    """A circuit WIDER than a pixel's calibrated width is skipped for that
    pixel (never truncated); the rest run and produce correct results."""
    # 8 pixels: widths 2,2,2,2,5,5,5,5. A 4-qubit circuit fits pixels 4..7
    # (width 5) and is skipped on pixels 0..3 (width 2 < 4).
    widths = [2, 2, 2, 2, 5, 5, 5, 5]
    b = _calibrated_backend(widths)
    circs = [_bell(4) for _ in range(8)]
    out = b.run_array(circs, shots=128)
    assert out["n_pixels"] == 8
    assert out["skipped_pixels"] == [0, 1, 2, 3]
    assert out["n_run"] == 4
    # the 4 run pixels each contributed 128 shots -> 512 aggregate
    assert sum(out["aggregate_counts"].values()) == 4 * 128
    # skipped entries are dicts flagging the reason + widths
    for i in range(4):
        e = out["per_pixel"][i]
        assert isinstance(e, dict) and e.get("skipped") is True
        assert e["pixel_width"] == 2 and e["circuit_width"] == 4


def test_run_array_per_pixel_width():
    """Different pixels run different-width circuits: a 2-qubit circuit fits
    ALL pixels (even the width-2 ones), nothing is skipped, and the per-pixel
    widths are reported back."""
    widths = [2, 3, 4, 5, 6, 7, 8, 9]
    b = _calibrated_backend(widths)
    circs = [_bell(2) for _ in range(8)]
    out = b.run_array(circs, shots=64)
    assert out["skipped_pixels"] == []
    assert out["n_run"] == 8
    assert out["per_pixel_widths"] == widths
    assert out["min_width"] == 2 and out["max_width"] == 9
    assert sum(out["aggregate_counts"].values()) == 8 * 64


def test_run_array_uniform_when_no_calibration():
    """Without register_widths_per_pixel the uniform path is unchanged: no
    skips, no per_pixel_widths key, full aggregate (back-compat)."""
    b = QSSCBackend("NV", seed=42, noise_mode="real")
    circs = [_bell(2) for _ in range(8)]
    out = b.run_array(circs, shots=64)
    assert out["n_pixels"] == 8
    assert "skipped_pixels" not in out
    assert "per_pixel_widths" not in out
    assert sum(out["aggregate_counts"].values()) == 8 * 64


def test_best_uniform_width_helper():
    """best_uniform_width picks the N maximizing N * count(width >= N)."""
    from qssc.array_calibration import best_uniform_width
    # N=4: 8 pixels -> 32 (winner); N=6: 5 -> 30; N=10: 2 -> 20
    n, k = best_uniform_width([4, 6, 6, 10, 10, 6, 4, 4])
    assert (n, k) == (4, 8)


# -- Invention 6+7: calibrate-array sub_pixel + integration -------------------
def test_calibrate_array_endpoint_integration_raises_width():
    """The /api/circuit/calibrate-array endpoint with integration_shots>=16
    returns >= total working qubits than the 1-shot baseline (Invention 7)."""
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    base = {
        "gradient_mhz_per_um": 2.0, "seed": 42, "source": "sim",
    }
    r1 = c.post("/api/circuit/calibrate-array", json={**base, "integration_shots": 1})
    r16 = c.post("/api/circuit/calibrate-array", json={**base, "integration_shots": 16})
    assert r1.status_code == 200 and r16.status_code == 200
    j1, j16 = r1.json(), r16.json()
    assert j16["integration_shots"] == 16
    # integration can only add detected lines (lower noise), never remove
    assert sum(j16["per_pixel_widths"]) >= sum(j1["per_pixel_widths"])
    assert j16["max_width"] >= j1["max_width"]


def test_calibrate_array_endpoint_sub_pixel_reports_channels():
    """The endpoint with sub_pixel=true reports spatial_channels = 64*S and
    per-pixel sub_pixel_position, but does NOT change register_width (Invention 6
    is imaging metadata, not a qubit gain)."""
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    base = {"gradient_mhz_per_um": 1.0, "seed": 42, "source": "sim"}
    r_plain = c.post("/api/circuit/calibrate-array", json=base)
    r_sp = c.post("/api/circuit/calibrate-array",
                  json={**base, "sub_pixel": True, "sigma_sheet_mm": 1.0})
    assert r_plain.status_code == 200 and r_sp.status_code == 200
    jp, js = r_plain.json(), r_sp.json()
    assert js["sub_pixel_factor"] == 8
    assert js["spatial_channels"] == 64 * 8
    assert jp["sub_pixel_factor"] == 0 and jp["spatial_channels"] == 0
    # centroiding does NOT change the per-pixel qubit register width
    assert jp["per_pixel_widths"] == js["per_pixel_widths"]
    # per-pixel sub-pixel positions are populated in the sim path
    assert sum(1 for p in js["pixels"] if p["sub_pixel_position"] is not None) == 64


def test_calibrate_array_endpoint_real_path_averages_n_sweeps():
    """Invention 7 real-hardware path: with source=real and integration_shots=N,
    the endpoint must call run_all_odmr N times and average (noise/sqrt(N)), not
    call it once while recording integration_shots=N. A spy FPGA counts calls and
    returns single-shot SimulatedFPGA-style matrices so the averaging is observable."""
    from fastapi.testclient import TestClient
    import api.main as api_main
    from api.main import app
    from qssc.simulator import SimulatedFPGA

    # A spy that wraps a real SimulatedFPGA (single-shot, integration_shots=1 so
    # each call is an independent noisy sweep) and counts run_all_odmr calls.
    base_fpga = SimulatedFPGA("NV", seed=7)
    for q in range(64):
        base_fpga.emitter_gs[q] = 4095
    calls = {"n": 0}

    class _SpyFPGA:
        def __getattr__(self, name):
            return getattr(base_fpga, name)

        def run_all_odmr(self, f_start, f_stop, n):
            calls["n"] += 1
            return base_fpga.run_all_odmr(f_start, f_stop, n)

    # Bypass _fpga()'s cache so the endpoint uses our spy on the real path.
    saved = api_main._FPGA
    api_main._FPGA = _SpyFPGA()
    try:
        c = TestClient(app)
        r = c.post("/api/circuit/calibrate-array", json={
            "source": "real", "integration_shots": 4, "seed": 42,
            "gradient_mhz_per_um": 1.0, "n_points": 32,
        })
    finally:
        api_main._FPGA = saved
    assert r.status_code == 200, r.text
    j = r.json()
    # the endpoint actually ran 4 sweeps and averaged them (Invention 7 real path)
    assert calls["n"] == 4
    assert j["integration_shots"] == 4
    assert j["source"] == "real"

    # Averaging lowers noise: the 4-shot real path should detect >= as many lines
    # as a single-shot real path (same seed/gradient), never fewer.
    api_main._FPGA = _SpyFPGA()
    calls["n"] = 0
    try:
        r1 = c.post("/api/circuit/calibrate-array", json={
            "source": "real", "integration_shots": 1, "seed": 42,
            "gradient_mhz_per_um": 1.0, "n_points": 32,
        })
    finally:
        api_main._FPGA = saved
    assert r1.status_code == 200 and calls["n"] == 1
    assert sum(j["per_pixel_widths"]) >= sum(r1.json()["per_pixel_widths"])