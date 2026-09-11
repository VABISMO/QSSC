# tests/test_pcb_drc.py
"""DRC gate test for the routed main board (``hardware/pcb/qssc.kicad_pcb``).

This is the PRIMARY regression gate for the 6-layer HDI rewrite: a board that
routes more nets but fails DRC is *worse* than a partial board. It
runs the actual ``kicad-cli pcb drc`` headlessly and asserts the committed board
is clean: 0 clearance/shorting violations, 0 footprint errors, 0 unconnected.

The committed ``qssc.kicad_pcb`` is already routed by the tools pipeline
(``gen_qssc_kicad.py`` -> ``route_signals.py`` -> ``route_qssc_pcb.py``); this
test does NOT regenerate or re-route -- it verifies the committed artefact.

DRC is run IN-PLACE on the committed board (board + sibling ``.kicad_pro``) so
the project's netclasses (Default/Analog/Chip/Power/RF/USB) load with their real
clearance/track/via values. A copy to a temp dir falls back to KiCad's default
netclasses and silently passes/wrong-fails. ``--severity-all`` forces every
rule to be reported even where the project masks it: ``unconnected_items`` is
set to "ignore" in the project (so the GUI does not flag same-net-through-plane
members the headless router cannot dog-bone out of the 0.8 mm-pitch CSG324
field), but with ``--severity-all`` the unconnected count is authoritative, so
``unconnected_items == []`` is a REAL assertion here, not a vacuous one.

The MEANINGFUL end-to-end connectivity bar is ``test_pcb_connectivity.py``
(a layer-aware copper-graph union-find that handles poured planes); the two
tests are complementary -- this one gates on DRC geometry (clearance, shorts,
hole-to-hole, track width), the connectivity one gates on electrical
completeness (no split nets).

Skips automatically where ``kicad-cli`` is not on PATH (CI has no KiCad).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PCB = REPO / "hardware" / "pcb" / "qssc.kicad_pcb"


def _have_kicad_cli() -> bool:
    return shutil.which("kicad-cli") is not None


pytestmark = pytest.mark.skipif(not _have_kicad_cli(),
                                reason="kicad-cli not on PATH")


def test_drc_zero_violations(tmp_path):
    # Run DRC IN-PLACE on the committed board so the sibling .kicad_pro loads
    # the real netclasses (a temp-dir copy falls back to default netclasses).
    # --severity-all forces every rule to report even where the project masks it
    # to "ignore", so the unconnected_items count below is authoritative.
    out = tmp_path / "drc.json"
    res = subprocess.run(
        ["kicad-cli", "pcb", "drc", "--severity-all", "--format", "json",
         "--output", str(out), str(PCB)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, f"kicad-cli pcb drc failed:\n{res.stderr}"

    data = json.loads(out.read_text())
    violations = data.get("violations", [])
    # kicad-cli json has no separate footprint_errors key -- footprint errors
    # appear as entries in `violations` with type "footprint_*", so violations==[]
    # already covers them. Split them out only for a clearer failure message.
    footprints = [v for v in violations
                  if str(v.get("type", "")).startswith("footprint")]
    # Authoritative under --severity-all even though the project masks the rule.
    unconnected = data.get("unconnected_items", [])

    assert violations == [], (
        f"{len(violations)} DRC violation(s):\n"
        + json.dumps(violations[:10], indent=2)
    )
    assert footprints == [], (
        f"{len(footprints)} footprint error(s) inside violations"
    )
    assert unconnected == [], (
        f"{len(unconnected)} unconnected item(s) reported by DRC under "
        f"--severity-all (the board must be fully routed; see "
        f"test_pcb_connectivity for the plane-aware split-net bar): "
        + json.dumps(unconnected[:10], indent=2)
    )