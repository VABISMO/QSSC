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