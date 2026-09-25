# tests/test_audit_regressions.py
"""Regression tests for the 2026-09 full-project audit (branch
``fix/audit-physics-hardware-doc``).

Covers two HTTP endpoints that had no direct test before:

* ``GET /api/fpga/status`` -- the ``STATUS`` firmware-verb board self-status
  (PLL lock, ADC busy, rails, die temp). The handler parses the space-separated
  ``"PLL_LOCKED 1 ADC_BUSY 0 VCC3V3_mv 3300 ..."`` reply into a dict; this test
  pins that parsing so a future refactor of ``_fpga`` / ``send_command`` cannot
  silently break the frontend's hardware-status card.
* ``GET /api/sensor/magneto`` -- the 64-pixel VECTOR magnetometer. Pins the
  response shape (8x8 image, per-pin B-vectors, mean/std stats) so the
  calibration -> magnetometry loop stays intact.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    # QSSC_TESTING=1 is set session-wide by tests/conftest.py so _is_local
    # treats the TestClient peer ("testclient") as local -> the localhost-only
    # FPGA endpoints are reachable from the test client.
    import api.jobs as jobs
    monkeypatch.setattr(jobs, "_get_redis", lambda: None)
    monkeypatch.setattr(jobs, "_get_mongo", lambda: None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- S2: /api/fpga/status ---------------------------------------------------

class _FakeFPGA:
    """Stand-in for the cached ``_fpga()`` connection with a canned reply."""

    def __init__(self, reply: str):
        self._reply = reply
        self.last_cmd: str | None = None

    def send_command(self, cmd: str) -> str:
        self.last_cmd = cmd
        return self._reply


def test_fpga_status_parses_status_verb(client, monkeypatch):
    """``STATUS`` reply tokens are parsed into a typed dict (int when possible)."""
    import api.routes_fpga as routes_fpga

    reply = ("PLL_LOCKED 1 ADC_BUSY 0 VCC3V3_mv 3300 "
             "VCC5V_mv 4980 VCC12V_mv 12050 TEMP_C 25")
    fake = _FakeFPGA(reply)
    # Bypass the module-level cache + real/simulated FPGA open by replacing the
    # accessor the handler calls (`_fpga()`).
    monkeypatch.setattr(routes_fpga, "_fpga", lambda: fake)

    r = client.get("/api/fpga/status")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["PLL_LOCKED"] == 1
    assert j["ADC_BUSY"] == 0
    assert j["VCC3V3_mv"] == 3300
    assert j["VCC5V_mv"] == 4980
    assert j["VCC12V_mv"] == 12050
    assert j["TEMP_C"] == 25
    # the handler must have sent the STATUS verb, not something else
    assert fake.last_cmd == "STATUS"


def test_fpga_status_err_returns_502(client, monkeypatch):
    """An ``ERR`` reply surfaces as a 502 (bad gateway), not a 200 with an empty dict."""
    import api.routes_fpga as routes_fpga

    monkeypatch.setattr(routes_fpga, "_fpga",
                        lambda: _FakeFPGA("ERR"))
    r = client.get("/api/fpga/status")
    assert r.status_code == 502


def test_fpga_status_link_down_returns_503(client, monkeypatch):
    """A pyserial-link failure (send_command raises) surfaces as a 503."""
    import api.routes_fpga as routes_fpga

    class _Boom:
        def send_command(self, _cmd):
            raise OSError("serial link down")

    monkeypatch.setattr(routes_fpga, "_fpga", lambda: _Boom())
    r = client.get("/api/fpga/status")
    assert r.status_code == 503


# --- S3: /api/sensor/magneto ------------------------------------------------

def test_sensor_magneto_shape(client):
    """64-pixel vector magnetometer returns an 8x8 image + per-pin B-vectors."""
    r = client.get("/api/sensor/magneto", params={"seed": 42, "n_avg": 200})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["profile"] == "NV"
    assert j["grid"] == 8
    assert j["n_pixels"] == 64
    # image is the 8x8 |B| map (uT) as a flat list of 64 {x,y,value} pixels
    img = j["image"]
    assert len(img) == 64
    assert {p["x"] for p in img} == set(range(8))
    assert {p["y"] for p in img} == set(range(8))
    assert all("value" in p for p in img)
    assert j["image_min_ut"] <= j["image_max_ut"]
    # per-pin B-vectors: 64 pixels, each a 3-vector
    pixels = j["pixels"]
    assert len(pixels) == 64
    for px in pixels:
        bv = px["b_vector_ut"]
        assert len(bv) == 3
    # array-averaged single-number reading
    assert j["mean_b_mag_ut"] > 0
    assert len(j["mean_b_vector_ut"]) == 3
    assert j["std_b_mag_ut"] >= 0.0


def test_sensor_magneto_differential_removes_gradient(client):
    """Differential mode subtracts the smooth gradient baseline -> the array
    mean |B| drops versus non-differential (the DC background is removed)."""
    base = {"seed": 7, "n_avg": 200, "gradient_ut_mm": 8.0}
    r_plain = client.get("/api/sensor/magneto", params=base)
    r_diff = client.get("/api/sensor/magneto",
                        params={**base, "differential": "true"})
    assert r_plain.status_code == 200 and r_diff.status_code == 200
    assert r_diff.json()["mean_b_mag_ut"] < r_plain.json()["mean_b_mag_ut"]