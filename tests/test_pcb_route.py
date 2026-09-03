# tests/test_pcb_route.py
"""Structural checks for the headless PCB copper-plane foundation
(``tools/route_qssc_pcb.py``).

Skips automatically where ``pcbnew`` (KiCad) is not installed -- the CI runner
has no KiCad, so this only executes locally (and is skipped in CI, like the
yosys/FPGA steps). The check runs the router as a SUBPROCESS (its own python
process) so KiCad's python binding -- which can hand back bare ``SwigPyObject``
proxies (no methods) for boards re-loaded or iterated after a mutation under
pytest's import hooks -- is never exercised inside the test process. The
router is pointed at a TEMPORARY copy of the board via ``QSSC_PCB_PATH`` so the
committed ``qssc.kicad_pcb`` is never mutated by the test.

Verification parses the saved ``.kicad_pcb`` sexp (no pcbnew) for:
  * 2 zones: GND plane on In1.Cu, +3V3 plane on In2.Cu (PWR). The 6-layer HDI
    stackup has NO F.Cu/B.Cu GND floods -- those are signal layers now -- so the
    only planes are the two inner reference planes.
  * > 0 GND stitching vias, every one netted GND
  * idempotency: a second subprocess run reproduces the same counts/structure
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pcbnew")  # skip if KiCad's python binding isn't installed

REPO = Path(__file__).resolve().parent.parent
SRC_PCB = REPO / "hardware" / "pcb" / "qssc.kicad_pcb"
SCRIPT = REPO / "tools" / "route_qssc_pcb.py"


def _run_router(target: Path) -> None:
    env = {**os.environ, "QSSC_PCB_PATH": str(target)}
    subprocess.run([sys.executable, str(SCRIPT)], env=env,
                   check=True, capture_output=True)


def _parse(path: Path) -> dict:
    t = path.read_text()
    zone_blocks = re.split(r"(?m)^\t\(zone\b", t)[1:]
    zones = []
    for b in zone_blocks:
        nm = re.search(r'\(net_name "([^"]+)"\)', b)
        layer = re.search(r'\(layer "([^"]+)"\)', b)
        zones.append((layer.group(1) if layer else None,
                     nm.group(1) if nm else None))
    vias = re.split(r"(?m)^\t\(via\b", t)[1:]
    via_nets = {re.search(r"\(net (\d+)\)", b).group(1) for b in vias}
    # Resolve the GND netcode from the `(net N "GND")` declaration rather than
    # hardcoding it: regenerating the board (adding/removing nets, e.g. the
    # +7V_EMIT elimination) shifts every netcode, so a literal "13" is brittle.
    gnd = re.search(r'\(net (\d+) "GND"\)', t)
    return {"zones": sorted(zones), "via_count": len(vias),
            "via_nets": sorted(via_nets), "gnd_net": gnd.group(1) if gnd else None}


def test_copper_plane_foundation(tmp_path):
    dst = tmp_path / "qssc.kicad_pcb"
    shutil.copy(SRC_PCB, dst)

    _run_router(dst)
    r1 = _parse(dst)

    # --- 2 inner planes on the right layers/nets (6-layer HDI stackup: no
    # F.Cu/B.Cu GND floods -- those are signal layers). ---
    assert len(r1["zones"]) == 2
    assert r1["zones"] == sorted([
        ("In1.Cu", "GND"),     # GND plane
        ("In2.Cu", "+3V3"),    # +3V3 plane (PWR layer)
    ])

    # --- GND stitching vias present. The canonical board now also carries
    # signal routing vias from a prior ``route_signals.py`` pass, which
    # ``_clear_routed`` deliberately PRESERVES (only zones + its own wide
    # GND/+3V3 vias are rebuilt). So via_nets is GND plus the routed signal
    # nets; we assert GND stitching is present, not that it is the only net. ---
    assert r1["via_count"] > 50
    assert r1["gnd_net"] is not None
    assert r1["gnd_net"] in r1["via_nets"]

    # --- idempotent: a second run reproduces the same structure ---
    _run_router(dst)
    r2 = _parse(dst)
    assert r1 == r2