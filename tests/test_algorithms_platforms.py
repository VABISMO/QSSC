# tests/test_algorithms_platforms.py
"""Cross-platform regression suite: every algorithm on all 3 defect platforms.

Run this whenever the backend physics, the noise model, the circuits or the
runner change -- it validates that each algorithm still produces a correct
result on hBN V_B-, NV-diamond and SiC V_Si, and that the per-site readout
error produced by ``auto_align`` stays anchored to the profile's nominal value
(regression for the sigmoid-collapse bug that shrank readout ~10x).

Slow-ish (full-physics Aer runs); use ``pytest tests/test_algorithms_platforms.py -q``.
"""
import numpy as np
import pytest

from qssc import auto_align, get_profile
from api.runner import run_circuit

PLATFORMS = ["hBN-VB", "NV", "SiC-VSi"]

# cold = coherence window open (deep algorithms recover); room = 300 K
COLD = 1e-3
ROOM = 300.0


def _run(algo, profile, temperature_k, **kw):
    payload = {"algorithm": algo, "profile": profile,
               "temperature_k": temperature_k, "seed": 42,
               "noise_mode": "real", "shots": kw.pop("shots", 2048)}
    payload.update(kw)
    return run_circuit(payload)


# ---------------------------------------------------------------------------
# readout-error anchoring (the auto_align sigmoid-collapse regression)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("profile", PLATFORMS)
def test_auto_align_readout_anchored(profile):
    """auto_align sites must keep readout_err near the profile nominal, not
    collapse it ~10x (the old unbounded-sigmoid bug)."""
    prof = get_profile(profile)
    sites = auto_align(profile, grid=8, temperature_k=ROOM,
                       rng=np.random.default_rng(0))
    usable = [s.readout_err for s in sites if s.usable]
    assert usable, f"{profile}: no usable sites detected"
    mean_ro = float(np.mean(usable))
    assert 0.5 * prof.readout_error <= mean_ro <= 1.5 * prof.readout_error, (
        f"{profile}: mean readout_err {mean_ro:.3f} not anchored to nominal "
        f"{prof.readout_error:.3f}")


# ---------------------------------------------------------------------------
# shallow algorithms — validated at room temperature (real chip operation)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("profile", PLATFORMS)
def test_bell_on_platform(profile):
    r = _run("bell", profile, ROOM, num_qubits=2, shots=1024)
    assert "error" not in r
    assert sum(r["counts"].values()) == 1024
    top = max(r["counts"], key=r["counts"].get)
    assert top in ("00", "11")  # Bell -> only 00 / 11 dominate


@pytest.mark.parametrize("profile", PLATFORMS)
def test_qrand_keygen_on_platform(profile):
    r = _run("qrand", profile, ROOM, num_qubits=8, num_bytes=16,
             common_name=f"QSSC-{profile}-node", shots=8192)
    assert "error" not in r
    assert len(r["entropy_hex"]) == 32  # 16 bytes
    assert "BEGIN CERTIFICATE" in r["certificate_pem"]
    assert "BEGIN PRIVATE KEY" in r["private_key_pem"]
    assert r["key_algorithm"] == "Ed25519"
    assert r["common_name"] == f"QSSC-{profile}-node"


@pytest.mark.parametrize("profile", PLATFORMS)
def test_bb84_no_eve_on_platform(profile):
    r = _run("bb84", profile, ROOM, num_bits=128, eavesdrop=False, shots=512)
    assert "error" not in r
    assert r["protocol"] == "BB84"
    assert r["sifted_bits"] > 0
    # QBER is readout-dominated; must reflect the (now anchored) readout error,
    # not the old ~1% collapsed value.
    assert r["qber"] > 0.02
    assert r["qber"] < 0.20


@pytest.mark.parametrize("profile", PLATFORMS)
def test_bb84_eve_detected_on_platform(profile):
    r = _run("bb84", profile, ROOM, num_bits=128, eavesdrop=True, shots=512)
    assert "error" not in r
    # intercept-resend Eve adds ~25% QBER on top of the channel -> detected
    assert r["qber"] > 0.20
    assert r["eve_detected"] is True
    assert r["secure"] is False


# ---------------------------------------------------------------------------
# deeper algorithms — validated cold (coherence window open) on every platform.
# At 300 K hBN (T2 ~ 2 us) any multi-2q-gate circuit (Grover, QFT, arithmetic,
# Shor) decoheres, so the correct-result validation runs cold where coherence
# is not the limiting factor. The 300 K decoherence behaviour is checked
# separately below.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("profile", PLATFORMS)
def test_grover_on_platform(profile):
    r = _run("grover", profile, COLD, n_items=3, marked=5, shots=1024)
    assert "error" not in r
    assert r["found"] == 5
    assert r["success"] is True


@pytest.mark.parametrize("profile", PLATFORMS)
def test_math_mul_on_platform(profile):
    r = _run("math", profile, COLD, op="mul", a=3, b=4, bits=4, shots=4096)
    assert "error" not in r
    assert r["result"] == 12  # 3 * 4


@pytest.mark.parametrize("profile", PLATFORMS)
def test_qft_uniform_on_platform(profile):
    r = _run("qft", profile, COLD, num_qubits=3, x=1, shots=4096)
    assert "error" not in r
    assert r["basis_states"] == 8
    assert r["uniform"] is True


@pytest.mark.parametrize("profile", PLATFORMS)
def test_shor_15_cold_on_platform(profile):
    r = _run("shor", profile, COLD, n=15, a=7, shots=4096)
    assert "error" not in r
    assert r["factors"] == [3, 5]
    assert r["success"] is True


@pytest.mark.parametrize("profile", PLATFORMS)
def test_qftnos_15_cold_on_platform(profile):
    r = _run("qftnos", profile, COLD, n=15, a=7, shots=4096)
    assert "error" not in r
    assert r["factors"] == [3, 5]
    assert r["success"] is True
    # QFT-NOS must use fewer qubits than full Shor
    assert r["shor_comparison"] is not None
    assert r["num_qubits"] < r["shor_comparison"]["num_qubits"]


@pytest.mark.parametrize("profile", PLATFORMS)
def test_period_15_cold_on_platform(profile):
    r = _run("period", profile, COLD, n=15, a=7, shots=4096)
    assert "error" not in r
    assert r["period"] == 4  # 7 mod 15 has period 4


# ---------------------------------------------------------------------------
# decoherence: deep algorithms must NOT recover at room temperature
# ---------------------------------------------------------------------------
def test_shor_decoheres_at_room_temp_hbn():
    r = _run("shor", "hBN-VB", ROOM, n=15, a=7, shots=2048)
    assert "error" not in r
    assert r["factors"] is None  # decohered at 300 K (T2 ~ 2 us)
    assert r["assessment"]["within_coherence"] is False


def test_qftnos_decoheres_at_room_temp_hbn():
    """Regression for the QFT-NOS DM-optimism bug: at 300 K hBN (T2 ~ 2 us)
    the controlled mod-mult block is an opaque ``UnitaryGate`` that the DM
    noise model does NOT attach thermal error to, so a density-matrix run
    used to recover the factors [3,5] *despite* the assessment
    reporting the circuit exceeds the coherence budget. The runner result
    and the assessment must agree: at room temp QFT-NOS decoheres (no
    factors, outside coherence), matching full Shor's behaviour."""
    r = _run("qftnos", "hBN-VB", ROOM, n=15, a=7, shots=4096)
    assert "error" not in r
    assert r["assessment"]["within_coherence"] is False
    # the run must be consistent with the assessment: decohered, no factors
    assert r["factors"] is None or not r["success"]