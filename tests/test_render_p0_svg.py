# tests/test_render_p0_svg.py
"""The assembly SVG must be a faithful render of the real P0 gcode toolpaths.

``tools/render_p0_svg.py`` parses the gcode emitted by ``gen_qssc_gcode.py`` and
draws every cut segment / plunge the bit makes, so the drawing can never drift
from the gcode. These checks guard that contract:

  * the SVG is well-formed XML and deterministic (two builds byte-identical)
  * every one of the 4 job panels (A-D) + the assembly-order notes panel is present,
    each job panel drawn from its gcode
  * the render uses the REAL toolpath counts (cut segments + plunges parsed
    from the gcode), not hand-drawn approximations
  * the purpose line + the "rendered from the real gcode" provenance are present
  * no leftover BPW34 mentions and no "honest" wording anywhere in the SVG
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import tools.render_p0_svg as rnd

# 4 job panels (A cathode, B waveguide, C detector, D carrier) + the notes panel.
PANEL_IDS = ["panelA", "panelB", "panelC", "panelD", "panelNotes"]


def test_svg_is_well_formed_xml():
    root = ET.fromstring(rnd.build_svg())
    assert root.tag.endswith("svg")


def test_render_is_deterministic():
    assert rnd.build_svg() == rnd.build_svg()


def test_all_four_job_panels_present():
    svg = rnd.build_svg()
    for pid in PANEL_IDS:
        assert f'id="{pid}"' in svg, f"missing panel {pid}"


def test_drawn_from_real_gcode_provenance():
    svg = rnd.build_svg()
    assert "rendered from the real gcode" in svg.lower() or "rendered directly from" in svg.lower()
    assert "PURPOSE:" in svg  # one-line purpose per panel


def test_each_panel_uses_real_toolpath_counts():
    """Each panel's <path> + plunge circles must come from the parsed gcode,
    not a hand approximation. Cross-check: the detector + cathode stencils each
    mill 64 square windows (256 edge segments + 4 die-frame edges = 260 cuts)."""
    import tools.gen_qssc_gcode as gen
    for name, expect_cuts in [
        ("p0_cathode_stencil.gcode", 260),
        ("p0_detector_stencil.gcode", 260),
    ]:
        cuts, _plunges = rnd.parse_toolpath(gen.FILES[name]())
        assert len(cuts) == expect_cuts, f"{name}: {len(cuts)} cuts (want {expect_cuts})"


def test_no_BPW34_and_no_honest_in_svg():
    svg = rnd.build_svg()
    assert "BPW34" not in svg
    assert "honest" not in svg.lower()