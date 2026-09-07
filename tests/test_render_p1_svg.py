# tests/test_render_p1_svg.py
"""The P1 pin-matrix SVG must be a faithful render of the real P1 gcode.

``tools/render_p1_svg.py`` parses ``p1_pin_array_support.gcode`` and draws the
real 64-hole matrix the CNC drills, plus a schematic cross-section. Checks:
  * well-formed XML, deterministic, the 64 holes parsed from the real gcode
  * provenance ("rendered from the real gcode") + the standard 2.54 mm pitch
  * no leftover BPW34 mentions and no "honest" wording anywhere in the SVG
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import tools.render_p1_svg as rnd


def test_svg_is_well_formed_xml():
    root = ET.fromstring(rnd.build_svg())
    assert root.tag.endswith("svg")


def test_render_is_deterministic():
    assert rnd.build_svg() == rnd.build_svg()


def test_64_holes_drawn_from_real_gcode():
    svg = rnd.build_svg()
    # the render asserts it parsed exactly 64 unique hole centres from the gcode
    import tools.gen_qssc_gcode as gen
    holes = rnd._hole_centers(gen.FILES["p1_pin_array_support.gcode"]())
    assert len(holes) == 64
    # the SVG contains 64 hole circles (fill="#fff" stroke=INK) -- count the white
    # hole fill occurrences used only for the pin holes
    assert svg.count('fill="#fff"') >= 64


def test_provenance_and_pitch():
    svg = rnd.build_svg()
    assert "rendered from the real gcode" in svg.lower()
    assert "2.54 mm pitch" in svg
    assert "8x8" in svg or "8×8" in svg


def test_no_BPW34_and_no_honest_in_svg():
    svg = rnd.build_svg()
    assert "BPW34" not in svg
    assert "honest" not in svg.lower()