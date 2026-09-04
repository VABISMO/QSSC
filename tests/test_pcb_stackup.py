# tests/test_pcb_stackup.py
"""Stackup checks for the regenerated 6-layer HDI main board
(``hardware/pcb/qssc.kicad_pcb``).

Verifies (by parsing the ``.kicad_pcb`` sexp -- no pcbnew needed, so this runs
in CI too) that ``tools/gen_qssc_kicad.py`` emits the 6-layer stackup adopted in
HARDWARE_DESIGN.md §12:

    F.Cu  signal           (no user label)
    In1.Cu GND plane       (label "GND")
    In2.Cu +3V3 plane      (label "PWR")   -- HDI pivot: adjacent to F.Cu so
                                              the +3V3 balls tie with a short
                                              blind via, not a through VIP
    In3.Cu signal          (no user label)
    In4.Cu signal          (no user label)
    B.Cu  signal

The board must already be regenerated (``tools/gen_qssc_kicad.py``) and
committed. We do NOT regenerate here: generation is a slow, KiCad-dependent step
owned by the tools pipeline; the committed board is the source of truth.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PCB = REPO / "hardware" / "pcb" / "qssc.kicad_pcb"


def _layers(path: Path) -> list[tuple[str, str, str]]:
    """Return [(canonical_name, user_label, type)] for every declared layer in
    board order. Parses the ``(layers ... )`` block of the sexp. KiCad 8 writes
    each line as ``(N "canonical" type ["user"])`` -- type BEFORE the optional
    quoted user label -- so we capture type then the optional label."""
    lines = path.read_text().splitlines()
    out = []
    in_block = False
    for ln in lines:
        s = ln.strip()
        if s == "(layers":
            in_block = True
            continue
        if not in_block:
            continue
        if s == ")":            # closing paren of the (layers ...) block
            break
        m = re.match(r'\(\d+\s+"([^"]+)"\s+(\w+)(?:\s+"([^"]*)")?\)', s)
        if m:
            canon, typ, user = m.group(1), m.group(2), m.group(3) or ""
            out.append((canon, user, typ))
    assert out, "no layer declarations parsed from (layers ...) block"
    return out


def _copper_layers(path: Path) -> list[tuple[str, str, str]]:
    return [l for l in _layers(path) if l[2] == "signal" or l[2] == "power"]


def test_six_layer_stackup():
    layers = _copper_layers(PCB)
    names = [l[0] for l in layers]
    assert names == ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"], (
        f"expected 6-layer HDI stackup, got {names}"
    )
    labels = {l[0]: l[1] for l in layers}
    # In1.Cu is the GND plane, In2.Cu is the +3V3 plane (HDI pivot -- adjacent
    # to F.Cu so the +3V3 balls tie with a short blind via). In3/In4 are signal.
    assert labels["In1.Cu"] == "GND", labels
    assert labels["In2.Cu"] == "PWR", labels
    assert labels["In3.Cu"] == "", labels
    assert labels["In4.Cu"] == "", labels
    assert labels["F.Cu"] == "", labels
    assert labels["B.Cu"] == "", labels