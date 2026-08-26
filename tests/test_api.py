# tests/test_api.py
"""Integration tests for the QSSC FastAPI backend (``api.main``).

Every endpoint is exercised through Starlette's ``TestClient`` (no network).
Circuit runs use ``?sync=true`` so the result comes back inline -- TestClient's
connection host ``"testclient"`` is treated as local by ``_is_local``, so sync
is allowed (on a public deploy sync is blocked for non-localhost callers). The
async job path is exercised end-to-end too: enqueue, poll ``GET /api/jobs/{id}``
until terminal, then ``list`` + ``delete``.
"""
from __future__ import annotations

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import main as main_mod
from api.main import app

BELL = {"algorithm": "bell", "profile": "NV", "num_qubits": 2,
        "shots": 512, "seed": 1, "noise_mode": "real"}
GHZ_PY = ("from qiskit import QuantumCircuit\n"
          "qc = QuantumCircuit(3, 3)\n"
          "qc.h(0); qc.cx(0, 1); qc.cx(1, 2)\n"
          "qc.measure(range(3), range(3))\n")
GHZ_QASM = ("OPENQASM 2.0;\n"
            "include \"qelib1.inc\";\n"
            "qreg q[3]; creg c[3];\n"
            "h q[0]; cx q[0],q[1]; cx q[1],q[2];\n"
            "measure q -> c;\n")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    # lift the per-IP circuit rate limit so a full test run never trips a 429.
    monkeypatch.setattr(main_mod, "RATE_LIMIT_PER_MIN", 10_000)
    # force the in-process job fallback (no rq worker / no Mongo in the test
    # env): async jobs then deterministically complete in a background thread
    # here, instead of sitting "queued" forever waiting for a worker.
    import api.jobs as jobs
    monkeypatch.setattr(jobs, "_get_redis", lambda: None)
    monkeypatch.setattr(jobs, "_get_mongo", lambda: None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- read / simulate endpoints ----------------------------------------------

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok" and j["name"] == "QSSC"


def test_profiles_lists_all_platforms(client):
    r = client.get("/api/profiles")
    assert r.status_code == 200
    keys = {p["key"] for p in r.json()}
    assert {"NV", "hBN-VB", "SiC-VSi"} <= keys
    nv = next(p for p in r.json() if p["key"] == "NV")
    assert nv["d_ghz"] == pytest.approx(2.87)


def test_backend_info(client):
    r = client.get("/api/backend/info", params={"profile": "NV"})
    assert r.status_code == 200
    j = r.json()
    assert "NV" in j["defect"]
    assert j["site_count"] == 64
    assert "fpga" in j and "limitations" in j and "references" in j


def test_odmr_dip_near_d_and_ensemble_scale(client):
    r = client.get("/api/odmr", params={"profile": "NV", "points": 200})
    assert r.status_code == 200
    j = r.json()
    f = np.array(j["frequencies_ghz"])
    c = np.array(j["contrast"])
    assert len(f) == len(c) == 200
    assert j["d_ghz"] == pytest.approx(2.87)
    # deepest dip within ~60 MHz of D
    assert abs(float(f[np.argmin(c)]) - 2.87) < 0.06
    # "contrast" is normalised PL (1.0 baseline); the ODMR dip is a small
    # ensemble-contrast drop (a few %), not a 75% cliff.
    dip = 1.0 - float(c.min())
    assert 0.01 < dip < 0.15, f"ODMR dip {dip:.3f} not ensemble-scale"


@pytest.mark.parametrize("seq", ["ramsey", "odmr", "pulse"])
def test_signal_sequences(client, seq):
    r = client.get("/api/signal", params={"profile": "NV", "sequence": seq})
    assert r.status_code == 200
    j = r.json()
    assert j["sequence"] == seq
    assert len(j["time_ns"]) == len(j["signal"]) > 0
    assert all(0.0 <= float(s) <= 1.05 for s in j["signal"])


def test_protocol_endpoints(client):
    r = client.get("/api/protocol", params={"platform": "NV"})
    assert r.status_code == 200
    j = r.json()
    assert "sections" in j and "summary" in j
    r2 = client.get("/api/protocol/platforms")
    assert r2.status_code == 200
    assert "platforms" in r2.json()
    r3 = client.get("/api/calibration", params={"profile": "NV", "grid": 8})
    assert r3.status_code == 200
    j3 = r3.json()
    assert j3["grid"] == 8 and len(j3["sites"]) == 64


def test_calibration_run_three_stages(client):
    r = client.post("/api/calibration/run",
                    params={"profile": "NV", "grid": 8}, json={})
    assert r.status_code == 200
    j = r.json()
    assert len(j["stages"]) == 3
    s0 = j["stages"][0]
    # stage 0 scans the on-chip detector array (no camera frame)
    assert "detector" in s0["name"].lower() or "scan" in s0["name"].lower()
    assert s0["defects_detected"] == 64  # all sites active + bright
    assert j["stages"][2]["fit_ok"] in (True, False)


# --- circuit runner (sync) --------------------------------------------------

def test_circuit_run_sync_bell(client):
    r = client.post("/api/circuit/run", params={"sync": True}, json=BELL)
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "finished" and j["job_id"] is None
    counts = j["result"]["counts"]
    assert sum(counts.values()) == 512
    # Bell -> 00 / 11 dominate
    top = max(counts, key=counts.get)
    assert top in ("00", "11")


def test_circuit_run_sync_shor_factors(client):
    r = client.post("/api/circuit/run", params={"sync": True},
                    json={"algorithm": "shor", "profile": "NV", "n": 15,
                          "shots": 2048, "seed": 7, "noise_mode": "phenomenological"})
    assert r.status_code == 200
    j = r.json()["result"]
    # Shor-15 factors 3 x 5 (phenomenological mode is ideal-ish for small n)
    assert {3, 5} <= set(j["factors"]), j["factors"]


# --- custom circuit endpoint + gating --------------------------------------

def test_custom_circuit_python(client):
    r = client.post("/api/circuit/custom", params={"sync": True},
                    json={"code": GHZ_PY, "profile": "NV", "shots": 256, "seed": 2})
    assert r.status_code == 200
    counts = r.json()["result"]["counts"]
    assert sum(counts.values()) == 256
    # GHZ -> 000 / 111 dominate
    top = max(counts, key=counts.get)
    assert top in ("000", "111")


def test_custom_circuit_qasm(client):
    r = client.post("/api/circuit/custom", params={"sync": True},
                    json={"code": GHZ_QASM, "profile": "NV", "shots": 256, "seed": 3})
    assert r.status_code == 200
    assert sum(r.json()["result"]["counts"].values()) == 256


def test_custom_circuit_disabled(monkeypatch, client):
    monkeypatch.setattr(main_mod, "CUSTOM_CIRCUIT_ENABLED", False)
    r = client.post("/api/circuit/custom", params={"sync": True},
                    json={"code": GHZ_PY, "shots": 64})
    assert r.status_code == 503


def test_custom_circuit_api_key_required(monkeypatch, client):
    monkeypatch.setattr(main_mod, "CUSTOM_CIRCUIT_API_KEY", "s3cr3t")
    # without the header -> 401
    r = client.post("/api/circuit/custom", params={"sync": True},
                    json={"code": GHZ_PY, "shots": 64})
    assert r.status_code == 401
    # with the right header -> 200
    r2 = client.post("/api/circuit/custom", params={"sync": True},
                     json={"code": GHZ_PY, "shots": 64},
                     headers={"X-API-Key": "s3cr3t"})
    assert r2.status_code == 200


# --- resource-cap validation (Pydantic Field bounds) -----------------------

def test_run_rejects_oversize_shots(client):
    r = client.post("/api/circuit/run", json={**BELL, "shots": 99_999})
    assert r.status_code == 422


def test_custom_rejects_oversize_qubits(client):
    r = client.post("/api/circuit/custom",
                    json={"code": GHZ_PY, "max_qubits": 20})
    assert r.status_code == 422


def test_run_rejects_bad_temperature(client):
    r = client.post("/api/circuit/run", json={**BELL, "temperature_k": 0})
    assert r.status_code == 422


# --- async job lifecycle ----------------------------------------------------

def _wait_for_job(client, job_id, timeout=40):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        rec = client.get(f"/api/jobs/{job_id}").json()
        last = rec
        if rec.get("status") in ("finished", "failed"):
            return rec
        time.sleep(0.2)
    return last


def test_async_job_lifecycle(client):
    r = client.post("/api/circuit/run", json=BELL)  # no sync -> enqueued
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "queued" and j["job_id"]
    job_id = j["job_id"]

    rec = _wait_for_job(client, job_id)
    assert rec is not None
    assert rec["status"] == "finished", rec
    assert sum(rec["result"]["counts"].values()) == 512

    # history list contains the job
    jobs = client.get("/api/jobs").json()
    assert any(x.get("job_id") == job_id for x in jobs)

    # delete it
    d = client.delete(f"/api/jobs/{job_id}").json()
    assert d["deleted"] is True
    # subsequent get -> unknown
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "unknown"


def test_get_unknown_job(client):
    r = client.get("/api/jobs/job-does-not-exist")
    assert r.status_code == 200
    assert r.json()["status"] == "unknown"


# --- saved scripts CRUD -----------------------------------------------------

def test_scripts_crud(client):
    # start clean: list may contain leftovers; capture an id we create
    r = client.post("/api/scripts", json={"name": "bell-test", "code": GHZ_PY})
    assert r.status_code == 200
    sid = r.json()["id"]
    # it appears in the list
    lst = client.get("/api/scripts").json()
    assert any(s["id"] == sid for s in lst)
    # update (same id) -> still one entry with new code
    r2 = client.post("/api/scripts",
                     json={"name": "bell-test", "code": GHZ_QASM, "id": sid})
    assert r2.status_code == 200 and r2.json()["id"] == sid
    # delete
    d = client.delete(f"/api/scripts/{sid}")
    assert d.status_code == 200 and d.json()["deleted"] is True