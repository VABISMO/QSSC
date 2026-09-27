# tests/test_reaudit_fixes.py
"""Regression tests for the re-audit-1 fixes (#128, #129, #143).

#128 -- the qrand one-time secret (private_key_pem / symmetric_key_hex) must be
deliverable on the RQ/Redis path, where the worker is a SEPARATE PROCESS from
the API. A process-local ``_secret_store`` is invisible across processes, so the
secret is stashed in Redis (TTL) and popped atomically by ``get_job``.

#129 -- the per-IP rate-limit bucket must actually evict: (a) the trim runs
before the append so an empty deque is detected, and (b) a bounded periodic
sweep drops IPs that hit once and never return (the trim only fires on an IP's
NEXT request, which never comes for a silent IP).
"""
from __future__ import annotations

import time
from collections import deque


# ---------------------------------------------------------------------------
# #128: Redis-path one-time secret delivery
# ---------------------------------------------------------------------------

class _FakeRedis:
    """Minimal Redis stand-in: set (with ex), register_script (GET+DEL)."""
    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, ex=None):
        self.store[key] = value  # ex ignored in the fake (TTL tested via pop)

    def register_script(self, src):
        def _run(keys, client=None, args=None):
            k = keys[0]
            v = self.store.get(k)
            if v is not None:
                del self.store[k]  # atomic GET+DEL
            return v
        return _run


def test_qrand_secret_stashed_in_redis_when_redis_up(monkeypatch):
    """RQ path: the worker stashes the secret in Redis, NOT _secret_store."""
    from api import jobs
    fake = _FakeRedis()
    monkeypatch.setattr(jobs, "_get_redis", lambda: fake)
    monkeypatch.setattr(jobs, "_secret_store", {})
    jobs._secret_pop_script = None  # force re-register on the fake

    result = {"private_key_pem": "-----BEGIN KEY-----\nXYZ\n-----END KEY-----",
              "symmetric_key_hex": "deadbeef", "entropy_bits": 256}
    jobs._stash_secrets("job-redis-1", result)

    # the secret fields are stripped from the result (never persisted to Mongo)
    assert "private_key_pem" not in result
    assert "symmetric_key_hex" not in result
    assert result["entropy_bits"] == 256  # non-secret fields kept
    # the secret is in Redis, NOT in the process-local _secret_store
    assert (jobs._SECRET_REDIS_PREFIX + "job-redis-1") in fake.store
    assert jobs._secret_store == {}


def test_qrand_secret_popped_once_from_redis(monkeypatch):
    """The API process pops the secret from Redis exactly once (atomic GETDEL)."""
    from api import jobs
    fake = _FakeRedis()
    monkeypatch.setattr(jobs, "_get_redis", lambda: fake)
    jobs._secret_pop_script = None

    result = {"private_key_pem": "PEM", "symmetric_key_hex": "abcd"}
    jobs._stash_secrets("job-redis-2", result)

    secrets = jobs._pop_secrets("job-redis-2")
    assert secrets == {"private_key_pem": "PEM", "symmetric_key_hex": "abcd"}
    # second pop gets nothing (one-time delivery)
    assert jobs._pop_secrets("job-redis-2") == {}
    # the Redis key is gone after the pop
    assert (jobs._SECRET_REDIS_PREFIX + "job-redis-2") not in fake.store


def test_qrand_secret_inprocess_fallback_still_works(monkeypatch):
    """Redis down (in-process fallback): _secret_store + timer path is intact."""
    from api import jobs
    monkeypatch.setattr(jobs, "_get_redis", lambda: None)
    monkeypatch.setattr(jobs, "_secret_store", {})

    result = {"private_key_pem": "PEM2", "symmetric_key_hex": "ef00"}
    jobs._stash_secrets("job-fb-1", result)
    assert "private_key_pem" not in result
    assert jobs._secret_store["job-fb-1"]["private_key_pem"] == "PEM2"
    assert jobs._pop_secrets("job-fb-1") == {"private_key_pem": "PEM2",
                                             "symmetric_key_hex": "ef00"}
    assert jobs._pop_secrets("job-fb-1") == {}


# ---------------------------------------------------------------------------
# #129: rate-limit bucket eviction
# ---------------------------------------------------------------------------

class _FakeRequest:
    def __init__(self, ip="1.2.3.4", xff=None):
        self.client = type("C", (), {"host": ip})()
        self.headers = {"x-forwarded-for": xff} if xff else {}


def _set_rate_limit(monkeypatch, per_min):
    from api import main as _m
    monkeypatch.setattr(_m, "RATE_LIMIT_PER_MIN", per_min)
    _m._rate_buckets.clear()


def test_rate_limit_trims_before_append_evicts_empty(monkeypatch):
    """An IP that went silent >60s has its old entries trimmed and the bucket
    recreated with just the new entry (the prior append-then-trim left the
    just-appended entry, making the eviction dead code)."""
    from api import main as _m
    from api._guards import _rate_limited
    _set_rate_limit(monkeypatch, 20)
    t = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: t[0])

    # 3 requests from the same IP at t=1000
    for _ in range(3):
        _rate_limited(_FakeRequest("9.9.9.9"))
    assert len(_m._rate_buckets["9.9.9.9"]) == 3

    # the IP goes silent for 61s, then returns
    t[0] = 1061.0
    _rate_limited(_FakeRequest("9.9.9.9"))
    # the 3 old entries are trimmed; only the new (1061.0) entry remains
    assert _m._rate_buckets["9.9.9.9"] == deque([1061.0])


def test_rate_limit_sweep_evicts_silent_ips(monkeypatch):
    """A silent IP (hit once, never returns) is dropped by the periodic sweep,
    not just by its own next-request trim (which never fires for a silent IP)."""
    from api import main as _m
    from api._guards import _rate_limited, _SWEEP_MASK
    _set_rate_limit(monkeypatch, 20)
    t = [2000.0]
    monkeypatch.setattr(time, "monotonic", lambda: t[0])

    # one hit from a rotating IP that never returns
    _rate_limited(_FakeRequest("10.0.0.1"))
    assert "10.0.0.1" in _m._rate_buckets

    # advance time past 60s and fire enough calls from OTHER IPs to trigger the
    # periodic sweep (every _SWEEP_MASK+1 calls). The silent 10.0.0.1 key must
    # be swept once the sweep runs.
    t[0] = 3000.0  # 1000s later -> 10.0.0.1's entry is long expired
    for i in range(_SWEEP_MASK + 2):
        _rate_limited(_FakeRequest("172.16.0.1"))
    assert "10.0.0.1" not in _m._rate_buckets  # swept: silent IP evicted


def test_rate_limit_returns_over_at_threshold(monkeypatch):
    """Sanity: the over-limit flag still trips at RATE_LIMIT_PER_MIN+1."""
    from api._guards import _rate_limited
    _set_rate_limit(monkeypatch, 3)
    monkeypatch.setattr(time, "monotonic", lambda: 5000.0)
    req = _FakeRequest("5.5.5.5")
    assert _rate_limited(req) is False  # 1
    assert _rate_limited(req) is False  # 2
    assert _rate_limited(req) is False  # 3
    assert _rate_limited(req) is True   # 4 > 3