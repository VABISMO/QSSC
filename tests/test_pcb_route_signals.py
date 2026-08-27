# tests/test_pcb_route_signals.py
"""Structural checks for the headless signal router + FPGA BGA fanout
(``tools/route_signals.py``).

Skips automatically where ``pcbnew`` (KiCad) is not installed -- CI has no
KiCad, so this only runs locally (like ``test_pcb_route.py``). The check runs a
SUBPROCESS that loads a TEMPORARY copy of the board, applies the HDI-lite design
rules and the BGA via-in-pad fanout (``_apply_hdi_rules`` + ``_bga_fanout``), and
saves -- then the test parses the saved ``.kicad_pcb`` sexp (no pcbnew) for the
0.20/0.10 mm via-in-pad vias.

Only the FAST path is exercised here (rules + fanout). The full A* signal
routing (``build``) is resource-capped and too slow for a unit test; it is run
via the documented ``tools/`` pipeline (route_signals -> route_qssc_pcb) and
verified DRC-clean manually + committed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pcbnew")

REPO = Path(__file__).resolve().parent.parent
SRC_PCB = REPO / "hardware" / "pcb" / "qssc.kicad_pcb"
TOOLS = REPO / "tools"

_PROBE = """
import json, sys
sys.path.insert(0, {tools!r})
import pcbnew
import route_signals as R
b = pcbnew.LoadBoard({pcb!r})
# Capture everything BEFORE any mutation: KiCad's swig binding returns bare
# SwigPyObjects for GetBoardEdgesBoundingBox/GetFootprints/GetDesignSettings
# after board.Remove() runs in _clear_tracks. Read bbox + pads + (after
# _apply_hdi_rules, which mutates but does not Remove) the design settings.
bb = b.GetBoardEdgesBoundingBox()
fps = R._capture_pads(b)
R._apply_hdi_rules(b)
br = b.GetDesignSettings()              # works (no Remove yet)
x0, y0 = bb.GetX(), bb.GetY()
R._clear_tracks(b)                      # Remove -> breaks getters, we have all
routed = set()
vip_net, pad_vip, vip_vias = R._bga_fanout(b, x0, y0, routed, fps=fps)
n = len(vip_vias)
nets = set(vip_vias.keys())
b.SetModified()
pcbnew.SaveBoard({pcb!r}, b)
# Write the result to a FILE, not stdout: pcbnew's swig binding prints
# "swig/python detected a memory leak ..." warnings to stdout as the proxy
# objects are destroyed at interpreter shutdown, which interleave with and
# corrupt a printed JSON line. A file write is atomic and clean.
with open({out!r}, "w") as fh:
    json.dump({{
        "hdi_via": br.m_ViasMinSize,
        "hdi_drill": br.m_MinThroughDrill,
        "hdi_annular": br.m_ViasMinAnnularWidth,
        "bga_vias": len(pad_vip),
        "via_nets": sorted(nets),
    }}, fh)
"""


def _run_probe(target: Path, out_path: Path) -> dict:
    script = _PROBE.format(tools=str(TOOLS), pcb=str(target), out=str(out_path))
    subprocess.run([sys.executable, "-c", script],
                   check=True, capture_output=True, text=True)
    return json.loads(out_path.read_text())


def _parse_vias(path: Path):
    t = path.read_text()
    return re.split(r"(?m)^\t\(via\b", t)[1:]


def test_hdi_rules_and_bga_fanout(tmp_path):
    dst = tmp_path / "qssc.kicad_pcb"
    out = tmp_path / "probe.json"
    shutil.copy(SRC_PCB, dst)
    r = _run_probe(dst, out)

    # HDI-lite design rules relaxed for the 0.5 mm BGA via-in-pad
    assert r["hdi_via"] == 200_000          # 0.20 mm min via
    assert r["hdi_drill"] == 100_000        # 0.10 mm min drill
    assert r["hdi_annular"] == 50_000       # 0.05 mm via-in-pad annular

    # BGA fanout placed via-in-pads on the shadowed netted balls. GND balls are
    # skipped (the F.Cu GND flood from route_qssc_pcb surrounds them, so they
    # connect on F.Cu without a via). +3V3 balls are NOT skipped: the +3V3 plane
    # lives on In2.Cu only (no F.Cu +3V3 pour), so an F.Cu +3V3 ball needs the
    # through-hole via-in-pad to reach its plane -- it is correctly present.
    assert r["bga_vias"] > 0
    assert r["via_nets"]
    assert "GND" not in r["via_nets"]

    # the saved board carries the 0.20/0.10 mm vias in its sexp
    vias = _parse_vias(dst)
    small = [b for b in vias if re.search(r"\(size 0\.2\)", b)
             and re.search(r"\(drill 0\.1\)", b)]
    assert len(small) >= r["bga_vias"]