# tests/test_mongo_persistence.py
"""Regression test for audit #108: the Mongo persistence path was entirely
untested -- every suite monkeypatched ``jobs._get_mongo`` to ``None``, forcing
the in-memory fallback. The real write/read path (``_record`` ->
``coll.update_one``, ``get_job`` -> ``coll.find_one``, ``list_jobs`` ->
``coll.find``) was never exercised, so a non-BSON-serializable field in the
result dict (a numpy scalar, a qiskit Counts that is not plain-dict enough,
etc.) would be SILENTLY swallowed by the ``except Exception`` in ``_record``
and history would not actually persist -- the user only finding out after a
restart.

This test installs a STRICT fake collection that mimics pymongo's BSON
serializability rules (rejects numpy scalars and non-dict/list/primitive
objects) and asserts a finished circuit job round-trips through ``get_job``
and ``list_jobs`` via the fake -- i.e. that ``update_one`` did NOT raise and
the record was actually stored, not silently dropped to the fallback.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app


# BSON-serializable primitive types (what pymongo accepts without encoding).
_BSON_OK = (dict, list, tuple, str, int, float, bool, bytes, type(None))


def _is_bson_serializable(value, _depth=0):
    if _depth > 64:
        return False
    # numpy scalars are NOT BSON-serializable by default (pymongo raises
    # TypeError: cannot encode object np.int64) -- this is the exact silent
    # drop the audit flagged. Reject them explicitly.
    t = type(value)
    mod = getattr(t, "__module__", "")
    if mod.startswith("numpy"):
        return False
    # datetimes are BSON-serializable; accept by module name to avoid a
    # hard datetime import here.
    if mod == "datetime" and t.__name__ in ("datetime", "date"):
        return True
    if isinstance(value, bool) or value is None:  # bool before int (bool is int subclass)
        return True
    if isinstance(value, (str, int, float, bytes)):
        return True
    if isinstance(value, dict):
        return all(_is_bson_serializable(v, _depth + 1) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_is_bson_serializable(v, _depth + 1) for v in value)
    return False


class _StrictFakeCursor:
    def __init__(self, docs, proj=None):
        self._docs = list(docs)
        self._proj = proj or {}

    def sort(self, _field, _direction):
        return self

    def limit(self, _n):
        return self

    def __iter__(self):
        # Apply exclusion projection ({"field": 0, ...}) like pymongo does.
        excl = {k for k, v in self._proj.items() if v == 0}
        out = []
        for d in self._docs:
            out.append({k: v for k, v in d.items() if k not in excl})
        return iter(out)


class _StrictFakeCollection:
    """A minimal in-memory Mongo collection that enforces BSON strictness.

    Mirrors the subset of the pymongo Collection API that ``api.jobs`` uses
    (``update_one`` / ``find_one`` / ``find`` / ``delete_one``). Crucially,
    ``update_one`` VALIDATES every value is BSON-serializable and RAISES if
    not -- so a result field that pymongo would reject surfaces here instead
    of being silently swallowed by ``_record``'s ``except``.
    """

    def __init__(self):
        self.store: dict[str, dict] = {}
        self.write_errors = 0

    def update_one(self, filt, spec, upsert=False):
        rec = spec.get("$set", {})
        merged = {**self.store.get(filt["job_id"], {}), **rec}
        for v in rec.values():
            if not _is_bson_serializable(v):
                self.write_errors += 1
                raise TypeError(
                    f"non-BSON-serializable value in record {filt['job_id']}: "
                    f"{type(v).__module__}.{type(v).__name__}")
        self.store[filt["job_id"]] = merged
        return type("R", (), {"matched_count": 1, "upserted_id": None})()

    def find_one(self, filt, _proj=None):
        return self.store.get(filt.get("job_id"))

    def find(self, filt, proj=None):
        return _StrictFakeCursor(self.store.values(), proj)

    def delete_one(self, filt):
        return type("R", (), {"deleted_count": self.store.pop(filt["job_id"], None) is not None})()


@pytest.fixture
def strict_mongo(monkeypatch):
    import api.jobs as jobs
    fake = _StrictFakeCollection()
    # Reset the "known-unavailable" sentinel so _get_mongo returns our fake.
    monkeypatch.setattr(jobs, "_mongo", fake, raising=False)
    monkeypatch.setattr(jobs, "_get_redis", lambda: None)
    # Clear the fallback store so a silent drop cannot be masked by a stale
    # entry from a previous test.
    monkeypatch.setattr(jobs, "_fallback_store", {}, raising=False)
    return fake


@pytest.fixture
def client(strict_mongo):
    with TestClient(app) as c:
        yield c


def test_finished_job_round_trips_through_mongo(client, strict_mongo):
    """A finished circuit job must persist to Mongo and round-trip via
    get_job / list_jobs -- proving the result dict is BSON-serializable and
    ``_record`` did not silently drop it to the fallback (audit #108)."""
    import time as _t
    from api.jobs import get_job, list_jobs
    # Async enqueue (no ?sync=true) so the worker finisher calls _record.
    # With _get_redis None the in-process fallback executor runs
    # worker_run_circuit, which persists the result via _record.
    r = client.post("/api/circuit/run",
                    json={"algorithm": "shor", "profile": "NV", "n": 15,
                          "shots": 1024, "seed": 3, "noise_mode": "real",
                          "temperature_k": 300.0})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert job_id, "enqueue did not return a job_id"
    # Poll until the in-process fallback worker finishes (short Shor-15).
    rec = None
    for _ in range(120):
        rec = get_job(job_id)
        if rec and rec.get("status") in ("finished", "failed"):
            break
        _t.sleep(0.25)
    assert rec is not None, "get_job returned None -- record never persisted"
    assert rec["status"] == "finished", f"job did not finish: {rec.get('status')}"
    # The strict fake's update_one RAISES on a non-BSON value; _record's
    # except would then swallow it and fall back, leaving the fake store
    # without the finished record. Assert the finished record IS in the fake
    # store (update_one did not raise) and no write errors were seen.
    assert job_id in strict_mongo.store, (
        "finished record is NOT in Mongo -- _record silently swallowed a "
        "non-BSON-serializable value and dropped to the fallback (audit #108)")
    assert strict_mongo.write_errors == 0, (
        f"_record raised {strict_mongo.write_errors} non-BSON value(s) -- "
        "history is not actually persisting to Mongo")
    assert "result" in rec and rec["result"] is not None
    # list_jobs must surface it with result/code stripped, summary present.
    jobs_list = list_jobs(50)
    assert any(j["job_id"] == job_id for j in jobs_list)
    assert all("result" not in j for j in jobs_list)
    assert all("algorithm" in j for j in jobs_list)