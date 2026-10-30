# tests/test_qrng.py
"""QRNG pipeline tests: OS-entropy default (issue #45) + health gate (issue #46).

Covers the three layers the entropy flows through before it becomes key
material:

* ``qssc.crypto`` — the von-Neumann extractor and the ``entropy_health``
  sanity gate,
* ``SimulatedFPGA`` ``READ_RNG`` — the device-side harvest (must vary across
  instances unless a seed is pinned),
* ``api.runner.run_circuit`` ``qrand`` — the endpoint logic that turns a
  measured bitstream into an Ed25519 keypair.
"""
import numpy as np

from qssc import SimulatedFPGA
from qssc.crypto import (
    bits_to_bytes, bytes_to_bits, entropy_health, von_neumann_extract,
)
import api.routes_fpga as fpga_mod
from api.runner import run_circuit


# -- entropy_health ----------------------------------------------------------

def test_entropy_health_accepts_iid_uniform():
    rng = np.random.default_rng(7)
    bits = [int(b) for b in rng.integers(0, 2, size=4096)]
    h = entropy_health(bits)
    assert h["passed"] is True
    assert h["n"] == 4096
    assert 0.45 < h["p1"] < 0.55
    assert h["min_entropy_per_bit"] >= 0.9


def test_entropy_health_rejects_constant_stream():
    h = entropy_health([1] * 256)
    assert h["passed"] is False
    assert h["min_entropy_per_bit"] == 0.0


def test_entropy_health_rejects_extractor_degenerate_output():
    # Issue #46's exhibit: alternating input 0101... has p1 = 0.5, so the
    # extractor reports healthy per-pair stats — yet its output is all zeros.
    bits, stats = von_neumann_extract([0, 1] * 512, 128)
    assert stats["sufficient"] is True
    h = entropy_health(bits)
    assert h["passed"] is False


def test_entropy_health_rejects_correlated_stream():
    # blocks of eight equal bits: perfectly balanced 1s, but transitions
    # collapse to 1/8 — the lag-1 test must catch this.
    bits = ([1] * 8 + [0] * 8) * 128
    h = entropy_health(bits)
    assert h["passed"] is False
    assert h["freq_p"] >= 0.001          # frequency alone looks fine
    assert h["trans_p"] < 0.001          # lag-1 correlation exposes it


def test_entropy_health_needs_min_bits():
    assert entropy_health([0, 1] * 30)["passed"] is False
    assert entropy_health([0, 1] * 30)["reason"] == "too few bits (<64)"


def test_bytes_to_bits_roundtrip():
    data = bytes(range(64))
    assert bits_to_bytes(bytes_to_bits(data)) == data
    assert bytes_to_bits(b"\xa5") == [1, 0, 1, 0, 0, 1, 0, 1]   # MSB first


# -- SimulatedFPGA READ_RNG --------------------------------------------------

def test_fpga_read_rng_varies_across_instances():
    """Default seed=None -> OS entropy: two fresh devices must not agree."""
    a = SimulatedFPGA("NV").send_command("READ_RNG 32")
    b = SimulatedFPGA("NV").send_command("READ_RNG 32")
    ha, hb = a.split(), b.split()
    assert len(ha) == 4 and len(hb) == 4
    assert ha[0] != hb[0], "default-seeded devices returned identical entropy"
    # stats fields are decimal + rate, and the extractor kept something
    assert int(ha[1]) > 0 and int(ha[2]) > 0
    assert int(ha[1]) > int(ha[2]) >= 32 * 8   # VN discards pairs
    assert 0.0 < float(ha[3]) < 1.0


def test_fpga_read_rng_seed_reproducible():
    a = SimulatedFPGA("NV", seed=11).send_command("READ_RNG 32")
    b = SimulatedFPGA("NV", seed=11).send_command("READ_RNG 32")
    assert a == b
    c = SimulatedFPGA("NV", seed=12).send_command("READ_RNG 32")
    assert c != a


def test_fpga_read_rng_bounds():
    """READ_RNG bounds mirror the wire spec: command_parser.v validates
    n_bytes as 1..256, so the sim must ERR above 256 (it used to accept
    4096 -- accepted-here/rejected-on-hardware, round-4 audit)."""
    assert SimulatedFPGA("NV", seed=1).send_command("READ_RNG 0") == "ERR"
    assert SimulatedFPGA("NV", seed=1).send_command("READ_RNG 257") == "ERR"
    assert SimulatedFPGA("NV", seed=1).send_command("READ_RNG 4097") == "ERR"
    assert SimulatedFPGA("NV", seed=1).send_command("READ_RNG 256") != "ERR"


# -- qrand endpoint logic ----------------------------------------------------

def test_qrand_default_is_os_entropy():
    """Without an explicit seed, two default calls must differ (issue #45)."""
    a = run_circuit({"algorithm": "qrand", "num_bytes": 16, "shots": 8192,
                     "noise_mode": "ideal"})
    b = run_circuit({"algorithm": "qrand", "num_bytes": 16, "shots": 8192,
                     "noise_mode": "ideal"})
    assert "error" not in a and "error" not in b
    assert a["entropy_hex"] != b["entropy_hex"]
    assert a["private_key_pem"] != b["private_key_pem"]
    # the health gate ran and passed
    assert a["health"]["passed"] is True


def test_qrand_explicit_seed_reproducible():
    """An explicit seed keeps the deterministic path for tests."""
    req = {"algorithm": "qrand", "num_bytes": 16, "shots": 8192, "seed": 5,
           "noise_mode": "ideal"}
    a = run_circuit(req)
    b = run_circuit(req)
    assert "error" not in a
    assert a["entropy_hex"] == b["entropy_hex"]
    assert a["symmetric_key_hex"] == b["symmetric_key_hex"]


def test_qrand_health_gate_rejects_degenerate_stream(monkeypatch):
    """A stream that passes the extractor but fails the health gate must be
    rejected instead of becoming key material (issue #46)."""
    import api.runner as runner_mod

    def fake_extract(raw, need):
        bits = [0, 1] * (need * 2)          # deterministic, p1 = 0.5
        return bits[:need], {"raw_bits": len(raw), "pairs": need,
                             "kept": need, "extract_rate": 0.5,
                             "sufficient": True}

    monkeypatch.setattr(runner_mod, "von_neumann_extract", fake_extract)
    res = run_circuit({"algorithm": "qrand", "num_bytes": 16, "shots": 8192,
                       "seed": 3, "noise_mode": "ideal"})
    assert res.get("error", "").startswith("extracted entropy failed")
    assert res["health"]["passed"] is False
    assert "symmetric_key_hex" not in res


# -- /api/fpga/qrng endpoint (hardware path, issue #46) -----------------------

class _StubDevice:
    """Stand-in for SimulatedFPGA / FPGAConnection with a canned reply."""

    def __init__(self, reply: str):
        self.reply = reply

    def send_command(self, cmd: str) -> str:
        return self.reply


def _client():
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


def test_qrng_endpoint_accepts_rtl_zero_stats_reply(monkeypatch):
    """The firmware footer currently emits "0 0 0" placeholders (issue #47).
    The endpoint must accept that reply, gate it, derive kept = n_bytes * 8
    and leave rate at 0.0 while no raw count is reported on-wire."""
    rng = np.random.default_rng(7)
    entropy = rng.integers(0, 256, size=32, dtype=np.uint8).tobytes()
    monkeypatch.setattr(
        fpga_mod, "_fpga",
        lambda: _StubDevice(f"{entropy.hex()} 0 0 0"))
    with _client() as c:
        r = c.get("/api/fpga/qrng")
    assert r.status_code == 200
    j = r.json()
    # The stub device is not a real FPGAConnection, so the truthful source
    # label is "simulated" (the label flips to "hardware-adc-noise" only when
    # a real board answered — see test_hardware_real.test_qrng_source_label_sim
    # and the FPGAConnection path).
    assert j["source"] == "simulated"
    assert j["entropy_hex"] == entropy.hex()
    assert j["extraction"] == {"raw_bits": 0, "kept": 256,
                               "extract_rate": 0.0}
    assert j["health"]["passed"] is True
    assert "public_key_pem" in j


def test_qrng_endpoint_accepts_simulator_stats_reply(monkeypatch):
    """The software simulator reports "<hex> <raw_bits> <kept> <rate>" with
    real extraction stats; that format must keep working."""
    rng = np.random.default_rng(8)
    entropy = rng.integers(0, 256, size=32, dtype=np.uint8).tobytes()
    monkeypatch.setattr(
        fpga_mod, "_fpga",
        lambda: _StubDevice(f"{entropy.hex()} 128 32 0.25"))
    with _client() as c:
        r = c.get("/api/fpga/qrng")
    assert r.status_code == 200
    j = r.json()
    # rate is recomputed host-side from kept/raw (round-trips to 0.25)
    assert j["extraction"] == {"raw_bits": 128, "kept": 32,
                               "extract_rate": 0.25}
    assert j["health"]["passed"] is True


def test_qrng_endpoint_gates_degenerate_entropy(monkeypatch):
    """A degenerate extracted block (issue #46) must not become key material
    on the hardware path either -- the endpoint refuses with 502 + health
    breakdown and leaks no key material."""
    monkeypatch.setattr(
        fpga_mod, "_fpga",
        lambda: _StubDevice("55" * 32 + " 128 32"))   # alternating bits
    with _client() as c:
        r = c.get("/api/fpga/qrng")
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["error"].startswith("FPGA QRNG entropy failed the health")
    assert detail["health"]["passed"] is False
    assert "private_key_pem" not in r.json()
    assert "entropy_hex" not in r.json()


# -- the no-board FPGA-tab backend is seeded (audit #7b) --------------------
def test_tab_sim_backend_is_seeded(monkeypatch):
    """The FPGA tab's SimulatedFPGA is created with QSSC_SEED (default 42).

    The unseeded instance used here put every tab readout (QRNG included)
    outside the reproducibility gate: with the seed fixed, two fresh
    connections draw the same entropy; change the seed and it differs.
    """
    monkeypatch.delenv("QSSC_FPGA_PORT", raising=False)
    from api import routes_fpga
    from api.main import app
    from fastapi.testclient import TestClient

    def draw(seed):
        if seed is None:
            monkeypatch.delenv("QSSC_SEED", raising=False)
        else:
            monkeypatch.setenv("QSSC_SEED", seed)
        routes_fpga._FPGA = None
        r = TestClient(app).get("/api/fpga/qrng?num_bytes=16&common_name=t")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "entropy_hex" in body, body
        return body["entropy_hex"]

    try:
        default_a = draw(None)
        default_b = draw(None)
        assert default_a == default_b, "default seed (42) must reproduce"
        assert draw("1234") == draw("1234"), "QSSC_SEED must reproduce"
        assert draw("1234") != default_a, "a different seed must differ"
    finally:
        routes_fpga._FPGA = None
        monkeypatch.delenv("QSSC_SEED", raising=False)
