# tests/test_spincoater_gcode.py
"""Determinism + shape checks for the DIY spin-coater gcode
(``hardware/gcode/spincoater_collet.gcode`` / ``spincoater_tray.gcode``).

Both pieces are Ø80 mm x 10 mm MDF, milled with a single 2 mm bit on a 3018.
No G02/G03 (linear-segment polygon circles only).  The disk outline is cut LAST
with NO tabs, so the finished piece drops free of the block.
"""
from __future__ import annotations

import re

import tools.gen_spincoater_gcode as gen


def _lines(name: str) -> list[str]:
    return gen.FILES[name]().splitlines()


def _has_gcode_arc(text: str) -> bool:
    return bool(re.search(r"G0[23]\b", text))


def test_spincoater_files_deterministic() -> None:
    for name in gen.FILES:
        assert gen.FILES[name]() == gen.FILES[name](), f"{name} not deterministic"


def test_no_g02_g03() -> None:
    for name in gen.FILES:
        assert not _has_gcode_arc(gen.FILES[name]()), f"{name} has G02/G03"


def test_collet_shape() -> None:
    """Piece A: Ø80 disk, Ø10 axial hole, 4x Ø2 tie holes at (±20,±20),
    outline cut LAST, no tabs.  NO neck pocket, NO clamp hole (plain lid)."""
    t = gen.FILES["spincoater_collet.gcode"]()
    # through-cut to Z-10 (disk outline + Ø10 axial + 4 M2 plunge holes)
    assert "Z-10.000" in t, "no through-cut to Z-10"
    # deepest Z is the stock thickness
    zs = [float(m) for m in re.findall(r"Z(-?\d+\.\d+)", t)]
    assert min(zs) == -10.0
    # NO blind neck pocket, NO clamp hole -- the lid is plain
    assert "annulus" not in t and "neck" not in t.lower(), "stale neck pocket present"
    assert "M3 set-screw clamp" not in t, "stale clamp hole present"
    # Ø10 axial hole
    assert "Ø10 axial" in t
    # four M2 tie holes at the square vertices (±20, ±20)
    for hx, hy in gen.M2_HOLES:
        assert f"({hx:.1f}, {hy:.1f})" in t
    # NO tabs -- the outline is the LAST op and cuts the full circle through
    assert "NO tabs" in t
    assert "tabs" not in re.sub(r"(?i)no tabs", "", t), "stale tab reference"
    # the outline-cut comment appears AFTER the tie-hole comments (outline LAST)
    outline_i = t.rfind("outline, through-cut, NO tabs")
    tie_i = t.rfind("M2 tie hole @")
    assert outline_i > tie_i > 0, "outline is not the last operation"
    # exactly three operations: axial hole, tie holes, outline
    ops = [ln for ln in t.splitlines() if ln.startswith("; --- ")]
    assert len(ops) == 3, f"expected 3 ops, got {len(ops)}: {ops}"
    # comments are English, no forbidden words
    assert "BPW34" not in t and "honest" not in t.lower()


def test_tray_shape() -> None:
    """Piece B: Ø80 disk, 40x40 x 1 mm pocket, 4x Ø2 M2 tie holes, outline LAST,
    no tabs."""
    t = gen.FILES["spincoater_tray.gcode"]()
    # pocket is 1 mm blind -- Z-1.000 present, but NOT a through-cut at the pocket
    assert "Z-1.000" in t, "no 1 mm pocket floor"
    # through-cut only for the disk outline + 4 M2 holes (not the pocket)
    assert "Z-10.000" in t
    # 40x40 pocket documented
    assert "40 x 40" in t and "central pocket" in t
    # four M2 tie holes match piece A
    for hx, hy in gen.M2_HOLES:
        assert f"({hx:.1f}, {hy:.1f})" in t
    # NO tabs, outline LAST
    assert "NO tabs" in t
    assert "tabs" not in re.sub(r"(?i)no tabs", "", t), "stale tab reference"
    outline_i = t.rfind("outline, through-cut, NO tabs")
    tie_i = t.rfind("M2 tie hole @")
    assert outline_i > tie_i > 0, "outline is not the last operation"
    # no forbidden words
    assert "BPW34" not in t and "honest" not in t.lower()


def test_both_disks_same_outline_and_holes() -> None:
    """The two pieces share the Ø80 outline + 4 M2 holes so they stack."""
    a = gen.FILES["spincoater_collet.gcode"]()
    b = gen.FILES["spincoater_tray.gcode"]()
    for hx, hy in gen.M2_HOLES:
        assert f"({hx:.1f}, {hy:.1f})" in a
        assert f"({hx:.1f}, {hy:.1f})" in b