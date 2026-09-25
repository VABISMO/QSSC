# tests/test_loop_filter_qucs.py
"""CI gate for the ADF4351 loop-filter bandwidth (issue #85).

Runs ``hardware/sim/qucs/sim_qucs_main.py --ngspice-only`` and asserts the
derived open-loop unity-gain crossing stays inside the documented
~17.2 kHz band (12-25 kHz). This is the regression gate that earns the
qucs harness its place: if someone changes the loop-filter component
values (C31/C32/C33) or the charge-pump current and the bandwidth drifts
out of band, this test fails.

Skips when ngspice is not installed (``apt install ngspice``); the
dedicated ``analog-loop-filter`` CI job installs it and runs the same
command so the gate is authoritative on GitHub.
"""

import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "hardware", "sim", "qucs", "sim_qucs_main.py")

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("NGSPICE", "ngspice")) is None,
    reason="ngspice not installed (apt install ngspice) — run the "
    "analog-loop-filter CI job for the authoritative gate",
)


def test_loop_bandwidth_in_band():
    proc = subprocess.run(
        [os.environ.get("PYTHON", "python3"), SCRIPT, "--ngspice-only"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        f"loop-BW gate failed (rc={proc.returncode}):\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    # Sanity: the verdict line names the measured bandwidth and PASS.
    assert "PASS" in proc.stdout
    assert "kHz" in proc.stdout