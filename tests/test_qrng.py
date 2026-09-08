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
    assert SimulatedFPGA("NV", seed=1).send_command("READ_RNG 0") == "ERR"
    assert SimulatedFPGA("NV", seed=1).send_command("READ_RNG 4097") == "ERR"


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