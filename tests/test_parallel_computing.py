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