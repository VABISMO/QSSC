"""Per-pixel analog signal lab (/api/signal/array) — fired vs received.

The endpoint returns, for every pixel in the 8x8 array, the FIRED emitter
drive envelope and the RECEIVED AD7616 photocurrent trace on a shared time
axis, plus per-pixel KPIs and an array summary. These tests pin the shape,
the alive/dead distinction, the KPI consistency, and the grounded link to the
calibration grid's per-pixel NV model.
"""
from __future__ import annotations

import numpy as np
import pytest


def _run(**kw):
    from api.main import signal_array
    defaults = dict(profile="NV", sequence="ramsey", detuning_mhz=2.0,
                    points=120, seed=3)
    defaults.update(kw)
    return signal_array(**defaults)


def test_signal_array_shape_and_axes():
    r = _run()
    assert r["n_pixels"] == 64
    assert r["n_points"] == 120
    assert len(r["time_ns"]) == 120
    assert len(r["fired"]) == 64 and len(r["fired"][0]) == 120
    assert len(r["received"]) == 64 and len(r["received"][0]) == 120
    # received are integer AD7616 codes in range; fired is a 0..1 envelope
    assert all(isinstance(c, int) for c in r["received"][0])
    assert all(0 <= c <= 65535 for c in r["received"][0])
    assert all(0.0 <= v <= 1.0 for v in r["fired"][0])


def test_dead_pixels_are_not_armed_and_floor_only():
    from qssc.simulator import BRIGHT_CODE
    r = _run()
    px = r["pixels"]
    dead = [p for p in px if not p["alive"]]
    assert dead, "expected at least one dead pixel"
    for i, p in enumerate(px):
        if not p["alive"]:
            # a dead pixel (no diamond) is never armed -> fired all zero, n_nv 0
            assert sum(r["fired"][i]) == 0.0
            assert p["n_nv"] == 0
            # its received stays at the detector background floor + noise and
            # never reaches a bright-ensemble level
            assert max(r["received"][i]) < BRIGHT_CODE * 0.5
        else:
            assert sum(r["fired"][i]) > 0.0
            assert p["n_nv"] >= 1
    # at least one alive pixel is bright (large aggregate -> near BRIGHT_CODE)
    assert max(max(r["received"][i]) for i, p in enumerate(px) if p["alive"]) > BRIGHT_CODE * 0.5


def test_kpi_consistency():
    r = _run()
    px = r["pixels"]
    alive = [p for p in px if p["alive"]]
    assert alive, "expected at least one alive pixel"
    for p in alive:
        assert p["amplitude"] >= 0
        assert p["peak"] >= p["mean"]
        assert p["noise_floor"] >= 0
        assert p["snr"] >= 0
        assert p["dip_depth"] >= 0
        assert p["n_nv"] >= 1
    s = r["summary"]
    assert s["alive"] + s["dead"] == 64
    assert s["alive"] == len(alive)
    # mean SNR is the mean of the alive pixels' SNRs
    assert s["mean_snr"] == round(float(np.mean([p["snr"] for p in alive])), 2)


def test_summary_total_photocurrent_matches_alive_mean():
    r = _run()
    px = r["pixels"]
    alive = [p for p in px if p["alive"]]
    # total = sum of alive pixels' mean photocurrent (rounded to 0.1 in the
    # per-pixel KPI, so allow a small accumulation tolerance)
    total = r["summary"]["total_photocurrent"]
    assert total == pytest.approx(
        float(np.sum([p["mean"] for p in alive])), abs=5.0)


def test_sequence_varies_received_trace():
    """Different sequences produce different received shapes (ramsey fringes vs
    ODMR dip vs readout decay) -- the base PL model drives the trace."""
    from api.main import signal_array
    shapes = {}
    for seq in ("ramsey", "odmr", "pulse"):
        r = signal_array(profile="NV", sequence=seq, detuning_mhz=2.0,
                         points=120, seed=3)
        # use the first alive pixel's received trace shape (min/max/mean)
        ai = next(i for i, p in enumerate(r["pixels"]) if p["alive"])
        v = np.array(r["received"][ai])
        shapes[seq] = (round(float(v.mean()), 0), round(float(v.std()), 0))
    # the three sequences have distinct mean/std (different PL dynamics)
    assert len(set(shapes.values())) == 3, shapes


def test_seed_reproducibility():
    a = _run(seed=11)
    b = _run(seed=11)
    assert a["received"] == b["received"]
    assert a["fired"] == b["fired"]


def test_hardware_without_board_reports_no_fpga(monkeypatch):
    """Hardware mode with no board answers 503 + the no-fpga body (HTTP)."""
    monkeypatch.delenv("QSSC_FPGA_PORT", raising=False)
    from fastapi.testclient import TestClient
    from api.main import app
    r = TestClient(app).get(
        "/api/signal/array", params={"profile": "NV", "sequence": "ramsey",
                                     "points": 16, "hardware": True})
    assert r.status_code == 503, r.text
    assert r.json().get("error") == "no-fpga-detected"


def test_per_pixel_nv_uses_calibrated_ceiling_model():
    """A bright alive pixel's NV count reflects the grounded
    nv_ceiling_for_size model (<= the 300 NV ceiling at 200 um), not a flat
    scalar. With the uniform(50,200) um draw the count spans a real range."""
    from qssc.array_calibration import nv_ceiling_for_size
    r = _run()
    nvs = [p["n_nv"] for p in r["pixels"] if p["alive"]]
    assert max(nvs) <= nv_ceiling_for_size(200.0)
    assert min(nvs) >= 1
    # not a flat scalar repeated -- there is real per-pixel spread
    assert len(set(nvs)) > 1


if __name__ == "__main__":
    pytest.main([__file__, "-q"])