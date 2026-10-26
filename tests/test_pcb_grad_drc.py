# tests/test_pcb_grad_drc.py
"""DRC gate test for the gradient-variant board (``hardware/pcb/qssc-grad.kicad_pcb``).

The qssc-grad variant adds the Invention-5 gradient coil + MCP4921 DAC to the
main board so a single global MW antenna can address pixels by frequency (the
redundant per-pixel antenna of the closed issue #77 is not needed). It was
routed to DRC 0/0/0 and merged via PR #79; this test pins that clean state on
every push, mirroring ``test_pcb_drc.py`` (main) and ``test_pcb_head.py``'s
DRC case (head). Without it, the grad variant -- the only one of the three
routed boards not exercised by a DRC test -- could regress silently.

Runs the real ``kicad-cli pcb drc`` IN-PLACE on the committed board so the
sibling ``.kicad_pro`` netclasses (Default/Analog/Chip/Power/RF/USB) load with
their real clearance/track/via values. Skips automatically where ``kicad-cli``
is not on PATH (CI has no KiCad).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PCB = REPO / "hardware" / "pcb" / "qssc-grad.kicad_pcb"
if not PCB.exists():
    pytest.skip("qssc-grad board is local-only (not published here)",
                allow_module_level=True)


def _have_kicad_cli() -> bool:
    return shutil.which("kicad-cli") is not None


pytestmark = pytest.mark.skipif(not _have_kicad_cli(),
                                reason="kicad-cli not on PATH")


def test_grad_drc_zero_violations(tmp_path):
    # Run DRC IN-PLACE on the committed board so the sibling .kicad_pro loads
    # the real netclasses (a temp-dir copy falls back to default netclasses).
    out = tmp_path / "drc.json"
    res = subprocess.run(
        ["kicad-cli", "pcb", "drc", "--severity-all", "--format", "json",
         "--output", str(out), str(PCB)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, f"kicad-cli pcb drc failed:\n{res.stderr}"

    data = json.loads(out.read_text())
    violations = data.get("violations", [])
    footprints = [v for v in violations
                  if str(v.get("type", "")).startswith("footprint")]
    unconnected = data.get("unconnected_items", [])

    assert violations == [], (
        f"{len(violations)} grad DRC violation(s):\n"
        + json.dumps(violations[:10], indent=2)
    )
    assert footprints == [], (
        f"{len(footprints)} footprint error(s) inside violations"
    )
    assert unconnected == [], (
        f"{len(unconnected)} unconnected item(s) in qssc-grad under "
        f"--severity-all (the variant must stay fully routed): "
        + json.dumps(unconnected[:10], indent=2)
    )