"""Pytest wrapper around the self-checking iverilog testbench for the QSSC FPGA.

The RTL itself is Verilog (fpga/rtl/*.v); this test compiles and runs the
behavioural testbench fpga/sim/tb_top_level.v under Icarus Verilog (FAST_SIM)
and parses its self-checking output, so the firmware command protocol + the
microwave pulse-width timing + the parallel-emitter synchrony are gated in the
same `pytest` run as the Python layer. Skip cleanly if iverilog is absent.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TB = ROOT / "fpga" / "sim" / "tb_top_level.v"
RTL = sorted((ROOT / "fpga" / "rtl").glob("*.v"))


def _have_iverilog() -> bool:
    return shutil.which("iverilog") is not None and shutil.which("vvp") is not None


pytestmark = pytest.mark.skipif(not _have_iverilog(), reason="iverilog not installed")


def _run_tb() -> tuple[int, str]:
    """Compile (FAST_SIM) + run the testbench; return (rc, stdout)."""
    vvp = Path("/tmp/qssc_tb_top.vvp")
    compile_cmd = [
        "iverilog", "-g2012", "-D", "FAST_SIM",
        "-o", str(vvp), "-s", "tb_top_level", str(TB), *map(str, RTL),
    ]
    cp = subprocess.run(compile_cmd, capture_output=True, text=True)
    if cp.returncode != 0:
        return cp.returncode, cp.stdout + cp.stderr
    run = subprocess.run(["vvp", str(vvp)], capture_output=True, text=True)
    return run.returncode, run.stdout + run.stderr


def test_fpga_testbench_passes():
    """All self-checks in tb_top_level.v must pass (10 command scenarios)."""
    rc, out = _run_tb()
    assert rc == 0, f"testbench exited {rc}\n{out}"
    assert "=== ALL CHECKS PASSED ===" in out, out
    assert "FAIL" not in out, out


def test_microwave_pulse_width_is_exact():
    """A 500 ns APPLY_PULSE must gate the RF switch for exactly 50 clk cycles
    (100 MHz -> 10 ns/tick). This locks the pulse-duration timing the Qiskit
    backend relies on for the spin-control pulses."""
    _rc, out = _run_tb()
    assert "mw_gate pulse width = 50 cycles" in out, out


def test_emitters_fire_in_parallel():
    """DRIVE_PATTERN must arm multiple channels (gs>0) and assert the shared
    em_blank (PWM-on) line, which gates all 64 channels on the same cycle."""
    _rc, out = _run_tb()
    assert "3 emitters fire in parallel" in out, out


def test_detector_parallel_frame():
    """READ_DETECTORS streams all 64 AD7616 16-bit samples (320 ASCII bytes)."""
    _rc, out = _run_tb()
    assert "detector stream length = 320 bytes" in out, out


def test_run_all_odmr_verb_passes():
    """The RUN_ALL_ODMR RTL verb (Invention 4+5 calibration primitive) emits
    n rows of 64 codes + OK. The self-check in tb_top_level.v covers the RTL."""
    _rc, out = _run_tb()
    assert 'RUN_ALL_ODMR = 2 rows x 64 "1234" + OK  (PASS)' in out, out


def test_simulated_fpga_run_all_odmr_shape():
    """SimulatedFPGA.run_all_odmr returns an (n_points, 64) int code matrix --
    the host-side mirror of the RTL verb, used for the sim calibration path."""
    import numpy as np
    from qssc.simulator import SimulatedFPGA
    fpga = SimulatedFPGA("NV", seed=5)
    codes = fpga.run_all_odmr(2_870_000, 2_880_000, 16)
    assert isinstance(codes, np.ndarray)
    assert codes.shape == (16, 64)
    assert codes.dtype.kind in "iu"
    # codes are 16-bit ADC samples -> bounded [0, 65535]
    assert int(codes.min()) >= 0 and int(codes.max()) <= 0xFFFF


def test_simulated_fpga_sub_pixel_and_integration():
    """Invention 6+7: SimulatedFPGA accepts a SubPixelModel (sheet spreading) and
    integration_shots (sqrt(N) noise reduction); both keep the (n,64) shape and
    ADC bounds, and integration lowers the per-point noise."""
    from qssc.simulator import SimulatedFPGA
    from qssc.detector_centroid import SubPixelModel

    def _pump(f):
        for q in range(64):
            f.emitter_gs[q] = 4095

    # sub-pixel sheet spreading: shape + bounds preserved
    f_sp = SimulatedFPGA("NV", seed=5, sub_pixel=SubPixelModel())
    _pump(f_sp)
    c_sp = f_sp.run_all_odmr(2_870_000, 2_880_000, 16)
    assert c_sp.shape == (16, 64)
    assert int(c_sp.min()) >= 0 and int(c_sp.max()) <= 0xFFFF

    # integration: 16 shots -> noise drops vs 1 shot (same seed + pump)
    f1 = SimulatedFPGA("NV", seed=5, integration_shots=1)
    _pump(f1)
    r1 = f1.run_all_odmr(2_870_000, 2_880_000, 32)
    f16 = SimulatedFPGA("NV", seed=5, integration_shots=16)
    _pump(f16)
    r16 = f16.run_all_odmr(2_870_000, 2_880_000, 32)
    assert r16.shape == (32, 64)
    # the off-resonance pads' spread is dominated by noise; averaged std is lower
    assert float(r16.std()) < float(r1.std())

# ---------------------------------------------------------------------------
# READ_LOCKINP IQ demod -- SimulatedFPGA + FPGAConnection parse/bounds tests
# ---------------------------------------------------------------------------

def test_simulated_fpga_read_lockinp_amplitude():
    """SimulatedFPGA.read_lockinp generates a prime-modulated detector stream
    and demods it against the per-prime ROM, recovering the modulation depth
    (~1280 codes = 0.5*ODMR_CONTRAST*BRIGHT_CODE) with SNR > 1 over enough
    whole periods. Pins the host<->sim mirror of the firmware IQ demod."""
    from qssc.simulator import SimulatedFPGA
    from qssc.prime_readout import PRIME_SYMBOLS

    fpga = SimulatedFPGA("NV", seed=5)
    idx, p = 3, PRIME_SYMBOLS[3]      # p=7
    n = p * 16                        # 16 whole periods -> SNR gain
    res = fpga.read_lockinp(site=0, n_samples=n, prime_idx=idx)
    assert res["prime"] == p
    assert res["n_samples"] == n
    assert res["prime_idx"] == idx
    # recovered modulation depth ~1280 codes; noise/16-period average ~160
    assert abs(res["amplitude_code"] - 1280) < 400, res
    assert res["snr"] > 1.0, res


def test_fpga_connection_read_lockinp_parse():
    """FPGAConnection.read_lockinp parses the three-int IQ reply
    (``<I> <Q> <n>``) and builds the IQ result dict, and validates the
    site / n_samples / prime_idx bounds. The real constructor opens a serial
    port + does an ID handshake, so the instance is built via __new__ with a
    stubbed send_command -- this tests the wire-format parse + bounds, not the
    UART transport."""
    from qssc.backend import FPGAConnection
    from qssc.prime_readout import PRIME_SYMBOLS

    sent = []

    def fake_send(cmd):
        sent.append(cmd)
        # p=7 constant-code reply: I=-1234 (cos-rounding residual), Q=0, n=7
        return "-000000000001234 000000000000000 0000000007"

    conn = FPGAConnection.__new__(FPGAConnection)
    conn.send_command = fake_send

    res = conn.read_lockinp(site=0, n_samples=7, prime_idx=3)
    assert sent == ["READ_LOCKINP 0 7 3"]
    assert res["I"] == -1234
    assert res["Q"] == 0
    assert res["n_samples"] == 7
    assert res["prime"] == PRIME_SYMBOLS[3] == 7
    assert res["prime_idx"] == 3
    # amplitude from a DC residual is a small rounding-scale value, not a signal
    assert res["amplitude_code"] >= 0

    # bounds validation (never reaches the wire on a bad arg)
    for bad in [(64, 7, 3), (-1, 7, 3), (0, 0, 3), (0, 100001, 3),
                (0, 7, 16), (0, 7, -1)]:
        try:
            conn.read_lockinp(*bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad}")
    assert sent == ["READ_LOCKINP 0 7 3"], "bad args must not reach the wire"
