# api/jobs.py
"""Asynchronous job queue + execution history for the QSSC API.

* **Queue** — `redis` + `rq`. Circuit runs are enqueued and executed by an
  `rq worker` process, so the dashboard stays responsive while a (potentially
  seconds-long) Qiskit simulation runs. The frontend polls
  ``GET /api/jobs/{id}`` and updates when the job is ready.
* **History** — every finished/failed run is persisted to **MongoDB**
  (db ``qssc``, collection ``jobs``) and listed by ``GET /api/jobs``. Clicking a
  history entry reloads its result into the Hardware tab.

If Redis or MongoDB is unreachable at enqueue time we fall back to an in-process
``ThreadPoolExecutor`` / in-memory store so the local lab tool still runs out of
the box.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError

logger = logging.getLogger("qssc.jobs")

REDIS_URL = "redis://localhost:6379/0"
MONGO_URL = "mongodb://127.0.0.1:27017/"
MONGO_DB = "qssc"
MONGO_COLL = "jobs"

# Hard cap on an in-process job (seconds). Mirrors the rq `job_timeout=300`.
# A watchdog marks the job failed if it exceeds this, so one runaway job can
# not hold a fallback worker indefinitely. (Python cannot hard-kill the worker
# thread, but the request-layer resource caps bound the normal case.)
JOB_TIMEOUT_S = 300

_redis = None
_queue = None
_mongo = None
# in-process fallback
_fallback_exec = ThreadPoolExecutor(max_workers=2)
_fallback_store: dict[str, dict] = {}
_fallback_lock = threading.Lock()

_queue_disabled = False


def _get_redis():
    global _redis, _queue, _queue_disabled
    if _queue_disabled:
        return None
    if _redis is None:
        try:
            import redis as _r
            from rq import Queue
            _redis = _r.Redis.from_url(REDIS_URL, socket_connect_timeout=1.5,
                                       socket_timeout=1.5)
            _redis.ping()
            _queue = Queue("qssc", connection=_redis)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis/RQ unavailable (%s) — using in-process jobs",
                           exc)
            _queue_disabled = True
            return None
    return _redis


def _get_mongo():
    """Return the jobs collection, or ``None`` if Mongo is unavailable.

    Uses ``_mongo = False`` as an internal "known-unavailable, do not retry"
    sentinel (so a down server does not trigger a 1.5 s reconnect on every
    call) but always returns ``None`` to callers when unavailable.  Callers
    gate on ``is not None``, so returning ``False`` (the old behaviour) made
    them attempt ``False.update_one(...)`` / ``.find_one(...)`` and raise
    AttributeError on every call.  Returning ``None`` makes the fallback path
    clean and silent.
    """
    global _mongo
    if _mongo is False:
        return None
    if _mongo is None:
        try:
            from pymongo import MongoClient
            c = MongoClient(MONGO_URL, serverSelectionTimeoutMS=1500)
            c.admin.command("ping")
            _mongo = c[MONGO_DB][MONGO_COLL]
        except Exception as exc:  # noqa: BLE001
            logger.warning("MongoDB unavailable (%s) — using in-memory history",
                           exc)
            _mongo = False
            return None
    return _mongo


def _now_ms() -> float:
    return round(time.time() * 1000, 1)


def _record(job_id: str, **fields):
    rec = {"job_id": job_id, **fields}
    coll = _get_mongo()
    if coll is not None:
        try:
            coll.update_one({"job_id": job_id}, {"$set": rec}, upsert=True)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("mongo write failed: %s", exc)
    with _fallback_lock:
        _fallback_store[job_id] = {**_fallback_store.get(job_id, {}), **rec}


# --- worker entrypoints (executed by `rq worker`, or in-process fallback) ----

def _worker_run(kind: str, job_id: str, payload: dict) -> dict:
    from api import runner
    _record(job_id, status="started", started_at=_now_ms())
    try:
        if kind == "custom":
            result = runner.run_custom(payload)
        else:
            result = runner.run_circuit(payload)
        ok = "error" not in result
        _record(job_id, status="finished" if ok else "failed",
                finished_at=_now_ms(), result=result, success=ok)
        return {"job_id": job_id, "status": "finished" if ok else "failed"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("job %s failed", job_id)
        _record(job_id, status="failed", finished_at=_now_ms(),
                result={"error": str(exc)}, success=False)
        return {"job_id": job_id, "status": "failed", "error": str(exc)}


def worker_run_circuit(job_id: str, payload: dict) -> dict:
    return _worker_run("circuit", job_id, payload)


def worker_run_custom(job_id: str, payload: dict) -> dict:
    return _worker_run("custom", job_id, payload)


def _run_in_fallback(kind: str, job_id: str, payload: dict):
    fn = worker_run_custom if kind == "custom" else worker_run_circuit
    future = _fallback_exec.submit(fn, job_id, payload)

    # Watchdog: enforce JOB_TIMEOUT_S so a runaway job cannot hold a fallback
    # worker forever. The worker thread itself cannot be hard-killed in Python,
    # but we mark the job failed and unblock the bookkeeping; the request-layer
    # resource caps (qubits/shots/points) bound the normal-case runtime.
    def _watch():
        try:
            future.result(timeout=JOB_TIMEOUT_S)
        except FuturesTimeoutError:
            logger.warning("job %s exceeded %ss in-process timeout", job_id, JOB_TIMEOUT_S)
            _record(job_id, status="failed", finished_at=_now_ms(),
                    result={"error": f"job exceeded the {JOB_TIMEOUT_S}s in-process timeout"},
                    success=False)
        except Exception:  # noqa: BLE001
            # the worker already recorded its own failure
            pass

    threading.Thread(target=_watch, daemon=True).start()


# --- public API used by the FastAPI endpoints --------------------------------

def enqueue(kind: str, payload: dict) -> str:
    """Enqueue a circuit job. ``kind`` is ``'circuit'`` or ``'custom'``.

    Returns a job_id. Status is polled via :func:`get_job`.
    """
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    summary = {
        "kind": kind,
        "algorithm": payload.get("algorithm", "custom"),
        "profile": payload.get("profile", "NV"),
        "temperature_k": payload.get("temperature_k", 300.0),
        "shots": payload.get("shots", 2048),
        "noise_mode": payload.get("noise_mode", "real"),
    }
    if kind == "custom":
        summary["code"] = (payload.get("code", "") or "")[:160]
    _record(job_id, status="queued", created_at=_now_ms(),
            queued_at=_now_ms(), **summary)

    conn = _get_redis()
    if conn is not None and _queue is not None:
        try:
            fn = (worker_run_custom if kind == "custom"
                  else worker_run_circuit)
            rq_job = _queue.enqueue(fn, job_id, payload, job_timeout=300,
                                    result_ttl=600, failure_ttl=600)
            _record(job_id, rq_id=rq_job.id)
            return job_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("rq enqueue failed (%s) — in-process fallback",
                           exc)
    # in-process fallback
    _run_in_fallback(kind, job_id, payload)
    return job_id


def _mongo_to_view(rec: dict) -> dict:
    out = {k: v for k, v in rec.items() if k != "_id"}
    return out


def get_job(job_id: str) -> dict | None:
    coll = _get_mongo()
    rec = None
    if coll is not None:
        try:
            rec = coll.find_one({"job_id": job_id}, {"_id": 0})
        except Exception as exc:  # noqa: BLE001
            logger.warning("mongo read failed: %s", exc)
    if rec is None:
        with _fallback_lock:
            rec = _fallback_store.get(job_id)
    if rec is None:
        return None
    # enrich status from rq if we have an rq_id and the local status is still
    # queued/started (the worker updates Mongo directly, so this is a safety net)
    rq_id = rec.get("rq_id")
    if rq_id and rec.get("status") in ("queued", "started"):
        try:
            from rq.job import Job
            conn = _get_redis()
            if conn is not None:
                job = Job.fetch(rq_id, connection=conn)
                mapping = {None: "queued", "queued": "queued",
                           "started": "started", "finished": "finished",
                           "failed": "failed"}
                rq_status = mapping.get(job.get_status(), rec["status"])
                if rq_status != rec["status"]:
                    rec["status"] = rq_status
        except Exception:  # noqa: BLE001
            pass
    return _mongo_to_view(rec)


def list_jobs(limit: int = 50) -> list[dict]:
    coll = _get_mongo()
    if coll is not None:
        try:
            cur = coll.find({}, {"_id": 0, "result": 0, "code": 0}) \
                       .sort("created_at", -1).limit(int(limit))
            return [_mongo_to_view(r) for r in cur]
        except Exception as exc:  # noqa: BLE001
            logger.warning("mongo list failed: %s", exc)
    with _fallback_lock:
        items = sorted(_fallback_store.values(),
                       key=lambda r: r.get("created_at", 0), reverse=True)
    out = []
    for r in items[:limit]:
        r2 = {k: v for k, v in r.items() if k not in ("result", "code")}
        out.append(r2)
    return out


def delete_job(job_id: str) -> bool:
    coll = _get_mongo()
    if coll is not None:
        try:
            res = coll.delete_one({"job_id": job_id})
            if res.deleted_count:
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("mongo delete failed: %s", exc)
    with _fallback_lock:
        return _fallback_store.pop(job_id, None) is not None


# --- saved Qiskit scripts (MongoDB "scripts" collection) --------------------

_scripts_fallback: dict[str, dict] = {}


def _scripts_coll():
    coll = _get_mongo()
    if coll is None or coll is False:
        return None
    try:
        return coll.database["scripts"]
    except Exception:  # noqa: BLE001
        return None


def list_scripts() -> list[dict]:
    coll = _scripts_coll()
    if coll is not None:
        try:
            cur = coll.find({}, {"_id": 0}).sort("updated_at", -1)
            return list(cur)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mongo scripts list failed: %s", exc)
    with _fallback_lock:
        return sorted(_scripts_fallback.values(),
                      key=lambda r: r.get("updated_at", 0), reverse=True)


def save_script(name: str, code: str, sid: str | None = None) -> dict:
    name = (name or "untitled").strip()[:120] or "untitled"
    code = code or ""
    now = _now_ms()
    if not sid:
        sid = f"script-{uuid.uuid4().hex[:10]}"
    rec = {"id": sid, "name": name, "code": code, "updated_at": now}
    coll = _scripts_coll()
    if coll is not None:
        try:
            coll.update_one({"id": sid}, {"$set": rec}, upsert=True)
            return rec
        except Exception as exc:  # noqa: BLE001
            logger.warning("mongo script save failed: %s", exc)
    with _fallback_lock:
        _scripts_fallback[sid] = rec
    return rec


def delete_script(sid: str) -> bool:
    coll = _scripts_coll()
    if coll is not None:
        try:
            if coll.delete_one({"id": sid}).deleted_count:
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("mongo script delete failed: %s", exc)
    with _fallback_lock:
        return _scripts_fallback.pop(sid, None) is not None