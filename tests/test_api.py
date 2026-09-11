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
        "shots": 512, "seed": 1, "noise_mode": "real", "temperature_k": 4.0}
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
    # 4 stages: detector scan, auto-align, ODMR D-fit, per-pin resonance sweep.
    # The per-pin sweep produces the calibrated resonance map (the addressing
    # table) -- it only exists AFTER calibration, never pre-baked.
    assert len(j["stages"]) == 4
    s0 = j["stages"][0]
    # stage 0 scans the on-chip detector array (no camera frame)
    assert "detector" in s0["name"].lower() or "scan" in s0["name"].lower()
    assert s0["defects_detected"] == 64  # all sites active + bright
    assert j["stages"][2]["fit_ok"] in (True, False)
    s3 = j["stages"][3]
    assert "per-pin" in s3["name"].lower() or "resonance" in s3["name"].lower()
    assert s3["pins_measured"] == 64
    # stage 4 now also fits per-pin coherence: Ramsey T2* + Hahn-echo T2 means.
    assert s3["t2_mean_us"] > 0 and s3["t2_star_mean_us"] > 0
    assert s3["t2_mean_us"] > s3["t2_star_mean_us"]  # Hahn refocuses > Ramsey
    # the per-pin resonance map is the calibration OUTPUT: 64 measured pins,
    # each with a measured resonance + detuning around D=2.87 GHz AND a measured
    # coherence (t2_us = Hahn echo, t2_star_us = Ramsey -- not the model anchor).
    per_pin = j["per_pin"]
    assert len(per_pin) == 64
    assert all({"qubit_id", "row", "col", "resonance_ghz",
                "detuning_mhz", "t2_us", "t2_star_us", "usable"}
               <= set(p.keys()) for p in per_pin)
    resonances = [p["resonance_ghz"] for p in per_pin]
    assert 2.8 < min(resonances) < max(resonances) < 2.94  # NV D=2.87 GHz ± detuning
    # measured per-pin Hahn T2 sits near the 1.8 ms anchor (5% fit noise) and is
    # ~10x the Ramsey T2* (teaching ratio); per-pin values vary (real fit noise).
    t2s = [p["t2_us"] for p in per_pin if p["usable"]]
    t2stars = [p["t2_star_us"] for p in per_pin if p["usable"]]
    assert t2s and 1500.0 < np.mean(t2s) < 2100.0      # ~1.8 ms anchor
    assert t2stars and 100.0 < np.mean(t2stars) < 250.0  # ~180 us (T2/10)


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
                          "shots": 2048, "seed": 7, "noise_mode": "phenomenological",
                          "temperature_k": 1e-3})
    assert r.status_code == 200
    j = r.json()["result"]
    # Shor-15 factors 3 x 5 (phenomenological mode is ideal-ish for small n)
    assert {3, 5} <= set(j["factors"]), j["factors"]


# --- custom circuit endpoint + gating --------------------------------------

def test_custom_circuit_python(client):
    r = client.post("/api/circuit/custom", params={"sync": True},
                    json={"code": GHZ_PY, "profile": "NV", "shots": 256, "seed": 2,
                          "temperature_k": 4.0})
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
                    json={"code": GHZ_PY, "max_qubits": 28})
    assert r.status_code == 422


def test_run_rejects_bad_temperature(client):
    r = client.post("/api/circuit/run", json={**BELL, "temperature_k": 0})
    assert r.status_code == 422


# --- integration: every demo algorithm runs end-to-end through the API ----
# one parametrized case per counts-producing algorithm; the non-counts ones
# (qrand crypto, bb84 qkd) get dedicated tests below.

ALGO_CASES = [
    ("bell",     {"num_qubits": 2}),
    ("qft",      {"num_qubits": 3, "x": 1}),
    ("grover",   {"n_items": 3, "marked": 5}),
    ("math",     {"op": "add", "a": 3, "b": 2, "bits": 4}),
    ("period",   {"n": 15}),
    ("qftnos",   {"n": 15}),
]


@pytest.mark.parametrize("algo, extra", ALGO_CASES)
def test_circuit_run_all_algorithms(client, algo, extra):
    """Every demo algorithm returns 200/finished with well-formed counts and
    the on-chip optical readout block attached."""
    body = {"algorithm": algo, "profile": "NV", "shots": 512, "seed": 7,
            "noise_mode": "phenomenological"}
    body.update(extra)
    r = client.post("/api/circuit/run", params={"sync": True}, json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "finished"
    res = j["result"]
    assert "error" not in res, (algo, res.get("error"))
    assert res["counts"], f"{algo}: empty counts"
    assert sum(res["counts"].values()) == body["shots"]
    assert res["num_clbits"] >= 1
    # the on-chip detector readout fires for every counts-producing run
    opt = res["optical"]
    assert opt is not None
    assert "displayed_bitstring" in opt and "read_states" in opt
    assert "photocurrent_mean" in opt


def test_circuit_run_qrand_crypto(client):
    """qrand returns quantum entropy + an X.509 keypair (no counts shape)."""
    r = client.post("/api/circuit/run", params={"sync": True},
                    json={"algorithm": "qrand", "profile": "NV", "num_qubits": 8,
                          "num_bytes": 16, "shots": 8192, "seed": 3,
                          "noise_mode": "ideal"})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert "error" not in res, res.get("error")
    assert len(res["entropy_hex"]) == 32          # 16 bytes
    assert res["extraction"]["sufficient"] is True
    assert res["symmetric_key_hex"]
    assert "BEGIN CERTIFICATE" in res["certificate_pem"]
    assert int(res["serial"], 16) < 2 ** 159       # RFC 5280 serial bound


def test_circuit_run_bb84_qkd(client):
    """bb84 returns a QBER + sifted key (no circuit counts)."""
    r = client.post("/api/circuit/run", params={"sync": True},
                    json={"algorithm": "bb84", "profile": "NV", "num_bits": 64,
                          "eavesdrop": False, "shots": 256, "seed": 5,
                          "noise_mode": "ideal"})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert "error" not in res, res.get("error")
    assert res["qber"] >= 0.0
    assert res["sifted_bits"] >= 1
    # a clean channel (no Eve, low readout noise) stays under the 11% threshold
    assert res["qber"] < res["qber_threshold"]
    assert res["secure"] is True


def test_bell_grid_lights_measured_qubits(client):
    """Regression for the reported bug: running a Bell pair returned bitstring
    '00' and lit no qubits. The grid must light by per-qubit P(|1>) across all
    counts (so both qubits light for |00>+|11>), and the optical read_states
    must mark the electrodes that were never pumped as -1 (not threshold them
    to |1>)."""
    r = client.post("/api/circuit/run", params={"sync": True},
                    json={"algorithm": "bell", "profile": "NV", "num_qubits": 2,
                          "shots": 1024, "seed": 1, "noise_mode": "real"})
    res = r.json()["result"]
    counts = res["counts"]
    total = sum(counts.values())
    nc = res["num_clbits"]

    def p1(qubit):
        # circuit clbit i maps to bitstring position (num_clbits-1-i)
        s = nc - 1 - qubit
        return sum(c for bs, c in counts.items()
                   if s < len(bs) and bs[s] == "1") / total

    # both measured qubits carry ~50% |1>, so the grid lights even when the
    # single most-likely outcome is '00'
    assert 0.1 < p1(0) < 0.9 and 0.1 < p1(1) < 0.9

    opt = res["optical"]
    states = opt["read_states"]
    # the two pumped sites are read as a real 0/1; the other 62 are -1
    measured = [states[i] for i in (0, 1)]
    assert all(v in (0, 1) for v in measured)
    assert all(states[i] == -1 for i in range(2, len(states)))
    # photocurrent_mean reflects only the pumped electrodes (not the floor)
    assert opt["photocurrent_mean"] > 0.0


# --- qubit-count cap (chip has 64 sites; classical sim is bounded) --------

def test_circuit_run_bell_20_qubits(client):
    """20 qubits (was capped at 12) runs and lights 20 measured grid sites."""
    r = client.post("/api/circuit/run", params={"sync": True},
                    json={"algorithm": "bell", "profile": "NV", "num_qubits": 20,
                          "shots": 512, "seed": 1, "noise_mode": "real"})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert "error" not in res, res.get("error")
    assert sum(res["counts"].values()) == 512
    assert res["num_clbits"] == 20
    # 20 of the 64 sites were measured; the rest are -1
    st = res["optical"]["read_states"]
    assert sum(1 for v in st if v in (0, 1)) == 20
    assert sum(1 for v in st if v == -1) == 44


def test_circuit_run_qft_20_qubits(client):
    """QFT on 20 qubits (non-Clifford, real statevector) completes."""
    r = client.post("/api/circuit/run", params={"sync": True},
                    json={"algorithm": "qft", "profile": "NV", "num_qubits": 20,
                          "x": 1, "shots": 512, "seed": 2, "noise_mode": "real"})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert "error" not in res, res.get("error")
    assert sum(res["counts"].values()) == 512


def test_too_wide_circuit_errors_not_faked(client):
    """Regression: a circuit wider than the classical simulation ceiling
    (math mul bits=17 -> 68 qubits > SIM_MAX_QUBITS=64) used to silently fall
    back to fake all-zero counts. It must now return an error. (The cap was
    28 statevector qubits; raising to 64 via the Aer MPS path moved the ceiling,
    so the over-width probe must move past 64 too -- bits=8 -> 32q is now a
    legitimate MPS run, not an error.)"""
    r = client.post("/api/circuit/run", params={"sync": True},
                    json={"algorithm": "math", "profile": "NV", "op": "mul",
                          "a": 3, "b": 5, "bits": 17, "shots": 256, "seed": 3,
                          "noise_mode": "real"})
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "failed"
    assert "68 qubits" in j["result"]["error"] or "wider than" in j["result"]["error"]


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