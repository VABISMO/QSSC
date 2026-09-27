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

# Hard cap on an in-process job (seconds). The frontend polls GET /api/jobs/{id}
# for MAX_POLLS*600ms = 600 s, so a job still running past that is already a
# timeout in the UI. Marking it failed here at 600 s frees the bookkeeping and
# surfaces a real error instead of an indefinite "queued". (Python cannot
# hard-kill the worker thread, so the thread may keep running in the background
# -- the larger worker pool below + the runner N/qubit cap keep that rare.)
# 600 s (not 180) gives the 16-qubit parallel Shor band (N=16..31, 4 coprime
# bases raced across pixels) headroom to finish under the cap. The 19-qubit
# band (N=32..63) is capped to 4 race pixels so a single ~115 s attempt also
# fits. The qubit cap (shor <=19q, math <=36q) prevents the truly-unbounded
# mul/Shor sizes that would wedge a slot past this watchdog.
JOB_TIMEOUT_S = 600

_redis = None
_queue = None
_mongo = None
# in-process fallback. 4 workers so one slow/stuck job (e.g. a large Shor run
# that escaped the runner cap, or an Aer sim that hung) does not starve the
# queue for everyone else -- the "panel hangs because one Shor job is stuck"
# symptom. The runner's MAX_FACTOR_N / qubit caps prevent the common case.
_fallback_exec = ThreadPoolExecutor(max_workers=4)
_fallback_store: dict[str, dict] = {}
_fallback_lock = threading.Lock()

# One-time secret delivery: qrand derives an Ed25519 private key + symmetric
# key. Those must NEVER persist to MongoDB / disk (anyone who later reads the
# job record could recover the private key). Instead the worker strips them
# from the result before persisting and stashes them here, keyed by job_id;
# the first finished-status poll merges them into the view and pops them, so
# the caller gets the private key exactly once (like a sealed envelope) while
# the long-lived job record carries only the public cert + entropy. In-memory
# only -> a restart clears them (re-run qrand to regenerate). See SECURITY.
_secret_store: dict[str, dict] = {}
_secret_lock = threading.Lock()

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


def _current_status(job_id: str):
    """Read the currently-stored status of a job (mongo or fallback), or None.

    Used by the worker to detect the watchdog race: the in-process fallback's
    ``_watch`` thread marks a job ``failed`` at ``JOB_TIMEOUT_S`` but cannot
    hard-kill the worker thread, so a worker that finishes *after* the timeout
    must NOT overwrite that ``failed`` record with a stale ``finished``."""
    coll = _get_mongo()
    if coll is not None:
        try:
            rec = coll.find_one({"job_id": job_id}, {"status": 1})
            if rec is not None:
                return rec.get("status")
        except Exception:  # noqa: BLE001
            pass
    with _fallback_lock:
        rec = _fallback_store.get(job_id)
        return rec.get("status") if rec else None


# --- worker entrypoints (executed by `rq worker`, or in-process fallback) ----

# Secret fields a qrand run produces. These are stripped from the result before
# it is persisted to MongoDB / the fallback store and held for one-time
# delivery on the first finished-status poll.
_SECRET_FIELDS = ("private_key_pem", "symmetric_key_hex")

# When the RQ worker (Redis up) runs the job it is a SEPARATE PROCESS from the
# API, so a process-local ``_secret_store`` is invisible to the API's
# ``get_job`` -> the private key is never delivered (audit #128). Store the
# one-time secret in Redis instead (localhost, TTL=JOB_TIMEOUT_S) so the API
# process can atomically pop it on the first finished poll. The in-process
# fallback (Redis down) keeps using ``_secret_store`` (same process). The Mongo
# record never carries the secret (it is stripped here, before ``_record``).
_SECRET_REDIS_PREFIX = "qssc:secret:"
# Atomic one-time pop: GET the value then DEL it in one Redis round-trip so two
# concurrent finished-polls cannot both receive the secret. Works on any Redis
# version (no GETDEL requirement).
_SECRET_POP_LUA = """
local v = redis.call('GET', KEYS[1])
if v then redis.call('DEL', KEYS[1]) end
return v
"""
_secret_pop_script = None  # registered lazily on first use (needs a connection)


def _stash_secrets(job_id: str, result: dict) -> None:
    """Move qrand secret fields out of ``result`` into the one-time secret store
    (in-place on ``result``). Non-qrand results are a no-op.

    Undelivered secrets (a qrand job whose result is never polled) are evicted
    after ``JOB_TIMEOUT_S`` -- via Redis TTL (RQ path) or a one-shot timer
    (in-process fallback) -- so private keys do not live in memory longer than
    necessary (audit #120, #128)."""
    secrets = {f: result.pop(f) for f in _SECRET_FIELDS if f in result}
    if not secrets:
        return
    conn = _get_redis()
    if conn is not None:
        import json as _json
        conn.set(_SECRET_REDIS_PREFIX + job_id, _json.dumps(secrets),
                 ex=JOB_TIMEOUT_S)
    else:
        with _secret_lock:
            _secret_store[job_id] = secrets
        # Schedule eviction of the one-time secret if the user never polls it.
        t = threading.Timer(JOB_TIMEOUT_S, _pop_secrets, args=(job_id,))
        t.daemon = True
        t.start()


def _pop_secrets(job_id: str) -> dict:
    """Atomically pop + return the one-time secret for ``job_id`` (or ``{}``).

    Redis path: GET+DEL via a Lua script (atomic, so two concurrent polls
    cannot both receive the key). In-process fallback: ``_secret_store.pop``."""
    conn = _get_redis()
    if conn is not None:
        global _secret_pop_script
        if _secret_pop_script is None:
            _secret_pop_script = conn.register_script(_SECRET_POP_LUA)
        try:
            raw = _secret_pop_script(
                keys=[_SECRET_REDIS_PREFIX + job_id], client=conn)
        except Exception:  # noqa: BLE001
            return {}
        if not raw:
            return {}
        try:
            import json as _json
            return _json.loads(raw)
        except Exception:  # noqa: BLE001
            return {}
    with _secret_lock:
        return _secret_store.pop(job_id, {})


def _worker_run(kind: str, job_id: str, payload: dict) -> dict:
    from api import runner
    _record(job_id, status="started", started_at=_now_ms())
    try:
        if kind == "custom":
            result = runner.run_custom(payload)
        elif kind == "parallel":
            result = runner.run_parallel(payload)
        elif kind == "calibrate":
            result = runner.run_calibrate_array(payload)
        else:
            result = runner.run_circuit(payload)
        ok = "error" not in result
        if ok:
            _stash_secrets(job_id, result)
        # Watchdog race guard: the _watch thread may have already marked this
        # job failed at JOB_TIMEOUT_S while the worker was still running (Python
        # cannot hard-kill the thread). Do not let a late finish overwrite that
        # with a stale "finished" -- the user already saw a timeout, and a later
        # refresh must not flip the record back.
        if _current_status(job_id) == "failed":
            return {"job_id": job_id, "status": "failed"}
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


def worker_run_parallel(job_id: str, payload: dict) -> dict:
    return _worker_run("parallel", job_id, payload)


def worker_run_calibrate(job_id: str, payload: dict) -> dict:
    return _worker_run("calibrate", job_id, payload)


def _run_in_fallback(kind: str, job_id: str, payload: dict):
    fn = {"custom": worker_run_custom, "parallel": worker_run_parallel,
          "calibrate": worker_run_calibrate}.get(kind, worker_run_circuit)
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
    """Enqueue a circuit job. ``kind`` is ``'circuit'``, ``'custom'``,
    ``'parallel'`` (64-parallel run) or ``'calibrate'`` (array calibration).

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
    if kind == "parallel":
        summary["algorithm"] = f"parallel-{payload.get('mode', 'sample')}"
        summary["shots"] = payload.get("shots", 2048)
    if kind == "calibrate":
        summary["algorithm"] = "calibrate-array"
        summary["shots"] = 0
    _record(job_id, status="queued", created_at=_now_ms(),
            queued_at=_now_ms(), **summary)

    conn = _get_redis()
    if conn is not None and _queue is not None:
        try:
            fn = {"custom": worker_run_custom,
                  "parallel": worker_run_parallel,
                  "calibrate": worker_run_calibrate}.get(kind, worker_run_circuit)
            # 64-parallel Shor and the array calibration are heavier than a
            # single-pixel run (up to 64 circuits / 64 decompose_odmr fits), so
            # give them a longer RQ job timeout.
            job_timeout = 900 if kind in ("parallel", "calibrate") else 300
            rq_job = _queue.enqueue(fn, job_id, payload, job_timeout=job_timeout,
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
    view = _mongo_to_view(rec)
    # One-time secret delivery: on the first finished read, merge the qrand
    # private/symmetric key back into the result view and drop it from memory.
    # Merge into a NEW result dict (not the persisted record's dict, which the
    # shallow _mongo_to_view copy shares by reference in the fallback path) so
    # the secret is never written back into the stored job record.
    if view.get("status") == "finished":
        secrets = _pop_secrets(job_id)
        if secrets and isinstance(view.get("result"), dict):
            view["result"] = {**view["result"], **secrets}
    return view


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
    _pop_secrets(job_id)  # drop any undelivered one-time secrets too
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