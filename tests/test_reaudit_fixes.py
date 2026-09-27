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

import numpy as np


# ---------------------------------------------------------------------------
# #128: Redis-path one-time secret delivery
# ---------------------------------------------------------------------------

class _FakeRedis:
    """Minimal Redis stand-in: set (with ex), get, delete, scan_iter,
    register_script (GET+DEL). Used by the #128 secret tests and the #130
    job-record-mirror test."""
    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, ex=None):
        self.store[key] = value  # ex ignored in the fake (TTL tested via pop)

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0

    def scan_iter(self, match=None, count=None):
        pat = (match or "*").replace("*", "")
        for k in list(self.store):
            if k.startswith(pat):
                yield k

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

# ---------------------------------------------------------------------------
# #130: Redis job-record mirror when Mongo is down (RQ + Mongo-down path)
# ---------------------------------------------------------------------------

def test_record_mirrors_to_redis_when_mongo_down(monkeypatch):
    """#130: with Mongo down + Redis up, _record mirrors the job record to Redis
    so the API process (separate from the RQ worker) can read it via get_job
    instead of the result being stranded in the worker's process-local store."""
    from api import jobs
    fake = _FakeRedis()
    monkeypatch.setattr(jobs, "_get_mongo", lambda: None)
    monkeypatch.setattr(jobs, "_get_redis", lambda: fake)
    monkeypatch.setattr(jobs, "_fallback_store", {})
    jobs._record("job-r1", status="finished",
                 result={"algorithm": "bell"}, created_at=1)
    # the record is in Redis under the job-record prefix, NOT in-process
    assert (jobs._REDIS_JOB_PREFIX + "job-r1") in fake.store
    assert jobs._fallback_store == {}
    # get_job (Mongo down -> Redis fallback) reads it back cross-process
    view = jobs.get_job("job-r1")
    assert view is not None
    assert view["status"] == "finished"
    assert view["result"]["algorithm"] == "bell"


def test_get_job_drops_code_preview(monkeypatch):
    """#142: the per-job view drops the custom-circuit code preview (GET
    /api/jobs/{id} is unauthenticated; list_jobs already strips it)."""
    from api import jobs
    monkeypatch.setattr(jobs, "_get_mongo", lambda: None)
    monkeypatch.setattr(jobs, "_get_redis", lambda: None)
    monkeypatch.setattr(jobs, "_fallback_store", {})
    jobs._fallback_store["job-c1"] = {
        "job_id": "job-c1", "status": "finished", "code": "secret snippet",
        "result": {"algorithm": "custom"}, "created_at": 1,
    }
    view = jobs.get_job("job-c1")
    assert view is not None
    assert "code" not in view
    assert view["result"]["algorithm"] == "custom"


# ---------------------------------------------------------------------------
# #131: _HW_FPGA cache guarded by a module-level lock
# ---------------------------------------------------------------------------

def test_hw_fpga_cache_lock_exists():
    """#131: the cached real-FPGA handle is guarded by a module-level lock so
    two concurrent in-process hardware jobs don't double-open the port."""
    from api import runner
    assert hasattr(runner, "_HW_FPGA_LOCK")
    assert hasattr(runner._HW_FPGA_LOCK, "acquire")
    assert hasattr(runner._HW_FPGA_LOCK, "release")


# ---------------------------------------------------------------------------
# #133: on-D pulse accepts with the gradient ON (within-pixel, broadcast MW)
# ---------------------------------------------------------------------------

def test_apply_pulse_on_d_accepts_with_gradient_on():
    """#133: an on-D pulse must flip the central NV in every pixel even with the
    gradient ON. MW is broadcast and the gradient is within-pixel (all pixels
    share a D-centred register); the #117/#118 cross-pixel shift wrongly
    rejected on-D pulses when the gradient was on."""
    from qssc.simulator import SimulatedFPGA
    fpga = SimulatedFPGA("NV", seed=0, gradient_mhz_per_um=1.0)  # gradient ON
    d_ghz = fpga.sim_freq / 1e9
    assert fpga._apply_pulse(0, d_ghz, 1, 200, 0.0) == "OK"
    # a drive far outside the within-pixel spread + linewidth still rejects
    assert fpga._apply_pulse(0, d_ghz + 2.0, 1, 200, 0.0) == "ERR"


# ---------------------------------------------------------------------------
# #134: gyro_measure sensor_t2_us override
# ---------------------------------------------------------------------------

def test_gyro_sensor_t2_override_widens_rate_noise():
    """#134: with sensor_t2_us=10 (sensor grade) the Ramsey fringe decays much
    faster than at the profile 1.8 ms, so rate_noise is higher (the prior code
    used the profile T2 for the envelope, making rate_noise ~2.6x optimistic at
    T=10 us). The T2 actually used is reported in GyroResult.t2_us."""
    from qssc.sensors import gyro_measure
    r_def = gyro_measure("NV", rotation_rate_dps=1.0,
                         free_precession_us=10.0, rng=np.random.default_rng(1))
    r_sens = gyro_measure("NV", rotation_rate_dps=1.0,
                          free_precession_us=10.0, sensor_t2_us=10.0,
                          rng=np.random.default_rng(1))
    assert r_sens.t2_us == 10.0
    assert r_sens.rate_noise_dps > r_def.rate_noise_dps  # less optimistic


# ---------------------------------------------------------------------------
# #137: auto_align site_pitch_um default = 2500 um (2.5 mm die pitch)
# ---------------------------------------------------------------------------

def test_auto_align_site_pitch_default_2500um():
    """#137: the die pitch is 2.5 mm = 2500 um, not 2.5 um (a 1000x unit error)."""
    import inspect
    from qssc.simulator_site import auto_align
    sig = inspect.signature(auto_align)
    assert sig.parameters["site_pitch_um"].default == 2500.0


# ---------------------------------------------------------------------------
# #138: vibration is correlated (no 1/sqrt(N)) for short integrations
# ---------------------------------------------------------------------------

def test_vibration_correlated_for_short_integration():
    """#138: for an integration shorter than one vibration period (~33 ms at
    30 Hz / 1 kHz cadence -> ~33 shots), vibration is correlated and NOT divided
    by sqrt(N); past one period it averages down by 1/sqrt(N)."""
    import math
    from qssc.noise_filters import (effective_noise_sigma, NoiseEnvironment,
                                    AntiNoiseConfig)
    filters = AntiNoiseConfig()  # pass-through (keep_ff=1, cm=1, iso=1)
    env_vib = NoiseEnvironment.lab()            # vibration_counts=120, vib_hz=30
    env_novib = NoiseEnvironment.lab()
    env_novib.vibration_counts = 0.0

    def vib_contrib(n):
        a = effective_noise_sigma(100.0, env_vib, filters, integration_shots=n)
        b = effective_noise_sigma(100.0, env_novib, filters, integration_shots=n)
        return math.sqrt(max(a * a - b * b, 0.0))

    vib_full = env_vib.vibration_counts  # 30.0 (iso=1, keep_ff=1, cm=1)
    # short: 10 shots = 10 ms < 33 ms vibration period -> correlated (full)
    assert abs(vib_contrib(10) - vib_full) < 1.0
    # long: 100 shots = 100 ms > 33 ms -> uncorrelated (/sqrt(100) = /10)
    assert abs(vib_contrib(100) - vib_full / math.sqrt(100)) < 1.0


# ---------------------------------------------------------------------------
# #139: b_field_from_odmr mean linewidth is contrast-weighted
# ---------------------------------------------------------------------------

def test_bfield_mean_linewidth_contrast_weighted():
    """#139: a dim wide noise line must not inflate the resolvable threshold.
    Contrast-weighted mean_lw ~5.74 MHz -> splitting 19.9 > 5.74 -> resolvable;
    the prior simple mean (5+5+50)/3 = 20 MHz -> 19.9 < 20 -> NOT resolvable."""
    from qssc.odmr_decompose import b_field_from_odmr, OdmrComponent
    comps = [
        OdmrComponent(freq_ghz=2.86, linewidth_mhz=5.0, contrast=0.30, snr=10.0),
        OdmrComponent(freq_ghz=2.88, linewidth_mhz=5.0, contrast=0.30, snr=10.0),
        OdmrComponent(freq_ghz=2.87, linewidth_mhz=50.0, contrast=0.01, snr=0.1),
    ]
    est = b_field_from_odmr(comps)
    assert est is not None
    assert est.resolvable is True


# ---------------------------------------------------------------------------
# #141: FpgaCommandRequest.command bounded at 4096 chars
# ---------------------------------------------------------------------------

def test_fpga_command_request_max_length():
    """#141: the FPGA command string is bounded (consistent with ScriptRequest)."""
    import pytest
    from api.models import FpgaCommandRequest
    FpgaCommandRequest(command="ID")  # short -> OK
    with pytest.raises(Exception):
        FpgaCommandRequest(command="X" * 4097)


# ---------------------------------------------------------------------------
# #140 remainder: _check_write (non-localhost) + _client_ip XFF spoof
# ---------------------------------------------------------------------------

def test_check_write_blocks_non_localhost(monkeypatch):
    """#140: _check_write blocks destructive endpoints from a non-localhost peer
    when no API key is configured."""
    import pytest
    from fastapi import HTTPException
    from api._guards import _check_write
    from api import main as _m
    monkeypatch.setenv("QSSC_TESTING", "0")  # testclient peer NOT gated local
    monkeypatch.setattr(_m, "CUSTOM_CIRCUIT_API_KEY", "")
    req = _FakeRequest("8.8.8.8")  # non-local peer
    with pytest.raises(HTTPException) as exc:
        _check_write(req)
    assert exc.value.status_code == 403


def test_client_ip_ignores_spoofed_xff_leftmost(monkeypatch):
    """#140: a direct public client's XFF is fully forged -> _client_ip uses the
    connection peer, not the spoofed leftmost (or rightmost) XFF entry."""
    from api._guards import _client_ip
    monkeypatch.delenv("QSSC_TRUSTED_PROXY_CIDR", raising=False)
    req = _FakeRequest("203.0.113.5", xff="1.1.1.1, 2.2.2.2")
    assert _client_ip(req) == "203.0.113.5"


def test_client_ip_uses_rightmost_xff_for_trusted_proxy(monkeypatch):
    """#140: when the immediate peer is a trusted proxy, the RIGHTMOST XFF entry
    (the one the proxy appended) is used, not the spoofable leftmost."""
    from api._guards import _client_ip
    monkeypatch.setenv("QSSC_TRUSTED_PROXY_CIDR", "10.0.0.0/8")
    req = _FakeRequest("10.0.0.1", xff="9.9.9.9, 203.0.113.5")
    assert _client_ip(req) == "203.0.113.5"
