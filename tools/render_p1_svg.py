#!/usr/bin/env python3
"""Render docs/p1_pin_matrix.svg from the real P1 pin-array gcode + a cross-section.

Parses ``hardware/gcode/p1_pin_array_support.gcode`` (emitted by
``tools/gen_qssc_gcode.py``) and draws the real 64-hole matrix the CNC drills,
plus a schematic cross-section of the two pin-scaffold devices (the detector
pin-molded paste junction and the emitter pin-as-cathode-contact stack).

Usage:
    python3 tools/render_p1_svg.py        # writes docs/p1_pin_matrix.svg

This model (glm-5.2:cloud) has no image input -- the script is the only source
of truth for the drawing; verify with ``xmllint --noout docs/p1_pin_matrix.svg``.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import tools.gen_qssc_gcode as gen
from tools.render_p0_svg import parse_toolpath

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "p1_pin_matrix.svg"

# gcode coords are support-local mm (0..20).
HOLE_R = 0.75         # 1.5 mm hole radius (circle-bored; matches gen P1_HOLE_R)

INK = "#1b1b1b"
SUPPORT_FILL = "#3a6146"     # FR4-ish green
SUPPORT_EDGE = "#92501a"
ITO_FILL = "#bfe3c9"         # ITO glass
PIN_FILL = "#c9c9c9"         # tin-plated pin
PIN_EDGE = "#7a7a7a"
GRAPHENE_FILL = "#2b2f33"
MOS_FILL = "#6b5a8c"
PEDOT_FILL = "#3a4a7a"
ZN_FILL = "#d8d8b0"
EMIT_FILL = "#7fd06a"
LABEL = "#111"
NOTE = "#333"
FAINT = "#888"


def _g(parent, tag, attrs: dict) -> ET.Element:
    """Add a child element with stringified attributes (allows hyphenated keys)."""
    return ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})


def _hole_centers(text: str) -> list[tuple[float, float]]:
    """The 64 pin-hole centres, parsed from the real gcode.

    ``peck_drill`` plunges once per hole (plus repeat pecks at the same XY), so we
    dedupe ALL plunge positions -- but the gcode also plunges for the engraved
    bounding box, the 4 corner fiducials and the outline edges. We therefore keep
    only the plunges that land ON the 2.54 mm pin grid (the generator's
    ``P1_PIN_CENTRES``), which is exactly the 64 pin holes. This still parses the
    real gcode (it asserts every pin centre is actually plunged in the file) -- it
    just rejects the non-pin plunges.
    """
    _cuts, plunges = parse_toolpath(text)
    pin_set = {(round(gen.P1_PIN_START + i * gen.P1_PIN_PITCH, 2),
                round(gen.P1_PIN_START + j * gen.P1_PIN_PITCH, 2))
               for i in range(gen.P1_PIN_N) for j in range(gen.P1_PIN_N)}
    seen: set[tuple[int, int]] = set()
    out: list[tuple[float, float]] = []
    for x, y, _z in plunges:
        key = (round(x, 2), round(y, 2))
        if key in pin_set and key not in seen:
            seen.add(key)
            out.append((x, y))
    return out


def build_svg() -> str:
    gcode = gen.FILES["p1_pin_array_support.gcode"]()
    holes = _hole_centers(gcode)
    assert len(holes) == 64, f"expected 64 holes, got {len(holes)}"

    svg = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "viewBox": "0 0 1300 860",
        "id": "p1PinMatrix",
    })
    ET.SubElement(svg, "title").text = "QSSC P1 pin-scaffold: 64-hole matrix + cross-section"
    ET.SubElement(svg, "desc").text = (
        "Top view of the 8x8 = 64-hole pin-array support circle-bored by "
        "p1_pin_array_support.gcode (2.54 mm pitch, 1.5 mm holes), plus a "
        "cross-section of the detector (pin-molded graphene/MoS2+PEDOT paste) "
        "and the emitter (pin-molded CQD paste, symmetric to the detector). "
        "Rendered directly from the real gcode."
    )

    # ---- title ----
    _g(svg, "text", {"x": 650, "y": 34, "font-family": "sans-serif",
                     "font-size": 20, "font-weight": "bold",
                     "text-anchor": "middle", "fill": LABEL}).text = (
        "QSSC-P1  PIN-HEADER SCAFFOLD  (8x8 = 64 pins, 2.54 mm pitch)")
    _g(svg, "text", {"x": 650, "y": 56, "font-family": "sans-serif",
                     "font-size": 12, "text-anchor": "middle",
                     "fill": NOTE}).text = (
        "top view of the CNC 64-hole matrix (rendered from the real gcode) "
        "+ cross-section of the two devices")

    # ---- panel A: top-view matrix ----
    ax, ay, aw, ah = 40, 90, 620, 620
    _g(svg, "rect", {"x": ax, "y": ay, "width": aw, "height": ah, "rx": 6,
                     "fill": "#f7f7f4", "stroke": "#ddd"})
    _g(svg, "text", {"x": ax + 10, "y": ay + 22, "font-family": "sans-serif",
                     "font-size": 14, "font-weight": "bold",
                     "fill": LABEL}).text = "A. PIN-ARRAY SUPPORT  (top view, 20x20x3 mm)"
    _g(svg, "text", {"x": ax + 10, "y": ay + 40, "font-family": "sans-serif",
                     "font-size": 11, "fill": NOTE}).text = (
        "8x8 = 64 holes, 2.54 mm pitch (standard pin header), 1.5 mm dia, "
        "centred (first pin 1.11 mm). 1 mm bit CIRCLE-BORES each (visible polygon circle, no G02).")

    ox = ax + 20
    oy = ay + 60
    plot_w = 560          # 20 mm * 28 px/mm (drawn scale inside the panel)
    ds = plot_w / gen.P1_SUPPORT

    def to_px(x: float, y: float) -> tuple[float, float]:
        return (ox + x * ds, oy + y * ds)

    # support plate
    px, py = to_px(0, 0)
    _g(svg, "rect", {"x": px, "y": py, "width": gen.P1_SUPPORT * ds,
                     "height": gen.P1_SUPPORT * ds, "rx": 4,
                     "fill": SUPPORT_FILL, "fill-opacity": 0.18,
                     "stroke": SUPPORT_EDGE, "stroke-width": 2})

    # the 64 holes
    for x, y in holes:
        cx, cy = to_px(x, y)
        _g(svg, "circle", {"cx": cx, "cy": cy, "r": HOLE_R * ds, "fill": "#fff",
                           "stroke": INK, "stroke-width": 1.1})

    # 4 corner fiducials (inset 1 mm) as crosshairs
    for fx, fy in [(1, 1), (19, 1), (19, 19), (1, 19)]:
        cx, cy = to_px(fx, fy)
        _g(svg, "line", {"x1": cx - 5, "y1": cy, "x2": cx + 5, "y2": cy,
                         "stroke": SUPPORT_EDGE, "stroke-width": 1.2})
        _g(svg, "line", {"x1": cx, "y1": cy - 5, "x2": cx, "y2": cy + 5,
                         "stroke": SUPPORT_EDGE, "stroke-width": 1.2})

    # first + last pin coordinate labels
    for i, c in enumerate(gen.P1_PIN_CENTRES):
        if i in (0, gen.P1_PIN_N - 1):
            cx, _ = to_px(c, 0)
            _g(svg, "text", {"x": cx, "y": py + gen.P1_SUPPORT * ds + 14,
                             "font-family": "sans-serif", "font-size": 9,
                             "text-anchor": "middle", "fill": FAINT}).text = f"{c:.2f}"
    _g(svg, "text", {"x": ox + gen.P1_SUPPORT * ds / 2, "y": py - 8,
                     "font-family": "sans-serif", "font-size": 10,
                     "text-anchor": "middle", "fill": NOTE}).text = (
        "64 pin holes @ 2.54 mm pitch  ->  17.78x17.78 mm array, centred on 20x20")

    # ---- panel B: cross-section ----
    bx, by, bw, bh = 700, 90, 560, 720
    _g(svg, "rect", {"x": bx, "y": by, "width": bw, "height": bh, "rx": 6,
                     "fill": "#f7f7f4", "stroke": "#ddd"})
    _g(svg, "text", {"x": bx + 10, "y": by + 22, "font-family": "sans-serif",
                     "font-size": 14, "font-weight": "bold",
                     "fill": LABEL}).text = "B. CROSS-SECTION  (one pixel, the two device modes)"
    _g(svg, "text", {"x": bx + 10, "y": by + 40, "font-family": "sans-serif",
                     "font-size": 11, "fill": NOTE}).text = (
        "same pin-mold process; detector layers vs emitter layers below")

    # --- detector (pin-molded paste) ---
    cy0 = by + 70
    _g(svg, "text", {"x": bx + 16, "y": cy0, "font-family": "sans-serif",
                     "font-size": 12, "font-weight": "bold",
                     "fill": LABEL}).text = "DETECTOR  (fluorescence sensor)  -- pin-molded paste"
    sx = bx + 60
    sw = 240
    glass_y = cy0 + 250
    _g(svg, "rect", {"x": sx, "y": glass_y, "width": sw, "height": 22,
                     "fill": ITO_FILL, "stroke": "#5a8", "stroke-width": 1})
    _g(svg, "text", {"x": sx + sw + 8, "y": glass_y + 14, "font-family": "sans-serif",
                     "font-size": 10, "fill": NOTE}).text = (
        "ITO glass = DET_COMMON (common back-electrode)")

    # support block with a hole
    sup_y = glass_y - 90
    _g(svg, "rect", {"x": sx, "y": sup_y, "width": sw, "height": 90,
                     "fill": SUPPORT_FILL, "fill-opacity": 0.25,
                     "stroke": SUPPORT_EDGE, "stroke-width": 1})
    _g(svg, "text", {"x": sx + sw + 8, "y": sup_y + 14, "font-family": "sans-serif",
                     "font-size": 10, "fill": NOTE}).text = "20x20x3 mm support (1.5 mm hole)"

    hole_x = sx + sw / 2 - 16
    hole_w = 32
    _g(svg, "rect", {"x": hole_x, "y": sup_y, "width": hole_w, "height": 90,
                     "fill": "#fff", "stroke": "#bbb", "stroke-width": 0.8})

    # graphene annulus (inner, against the pin) + MoS2+PEDOT annulus (outer)
    pin_x = hole_x + hole_w / 2 - 5
    pin_w = 10
    _g(svg, "rect", {"x": hole_x + 4, "y": sup_y, "width": hole_w - 8, "height": 90,
                     "fill": GRAPHENE_FILL})
    _g(svg, "text", {"x": sx - 4, "y": sup_y + 50, "font-family": "sans-serif",
                     "font-size": 9, "text-anchor": "end",
                     "fill": NOTE}).text = "graphene skin (inner)"
    _g(svg, "rect", {"x": hole_x + 1, "y": sup_y, "width": 3, "height": 90,
                     "fill": MOS_FILL})
    _g(svg, "rect", {"x": hole_x + hole_w - 4, "y": sup_y, "width": 3, "height": 90,
                     "fill": PEDOT_FILL})
    _g(svg, "text", {"x": sx - 4, "y": sup_y + 65, "font-family": "sans-serif",
                     "font-size": 9, "text-anchor": "end",
                     "fill": NOTE}).text = "MoS2 + PEDOT:PSS (outer)"
    # paste column down to the ITO (the active junction)
    _g(svg, "rect", {"x": hole_x + 2, "y": glass_y - 30, "width": hole_w - 4,
                     "height": 30, "fill": MOS_FILL, "fill-opacity": 0.7})
    _g(svg, "text", {"x": sx + sw + 8, "y": glass_y - 14, "font-family": "sans-serif",
                     "font-size": 10, "fill": NOTE}).text = (
        "paste column = active layer (pin -> ITO)")

    # pin shaft (tip ~0.5 mm above ITO, not touching)
    _g(svg, "rect", {"x": pin_x, "y": sup_y - 40, "width": pin_w, "height": 120,
                     "fill": PIN_FILL, "stroke": PIN_EDGE, "stroke-width": 0.8})
    _g(svg, "text", {"x": sx + sw + 8, "y": sup_y - 28, "font-family": "sans-serif",
                     "font-size": 10, "fill": NOTE}).text = "pin = per-pixel electrode + fan-out"
    _g(svg, "text", {"x": sx + sw / 2, "y": glass_y + 40, "font-family": "sans-serif",
                     "font-size": 10, "text-anchor": "middle",
                     "fill": "#b00"}).text = (
        "NV PL 637-800 nm  ->  MoS2 photogates  ->  photocurrent pin-to-ITO")

    # --- emitter (pin-molded CQD paste, symmetric to the detector) ---
    cy1 = by + 400
    _g(svg, "text", {"x": bx + 16, "y": cy1, "font-family": "sans-serif",
                     "font-size": 12, "font-weight": "bold",
                     "fill": LABEL}).text = "EMITTER  (CQD array)  -- pin-molded paste (symmetric)"
    eglass_y = cy1 + 250
    _g(svg, "rect", {"x": sx, "y": eglass_y, "width": sw, "height": 22,
                     "fill": ITO_FILL, "stroke": "#5a8", "stroke-width": 1})
    _g(svg, "text", {"x": sx + sw + 8, "y": eglass_y + 14, "font-family": "sans-serif",
                     "font-size": 10, "fill": NOTE}).text = (
        "ITO glass = EM_ANODE (common anode)")

    # support block with a hole (same as the detector)
    esup_y = eglass_y - 90
    _g(svg, "rect", {"x": sx, "y": esup_y, "width": sw, "height": 90,
                     "fill": SUPPORT_FILL, "fill-opacity": 0.25,
                     "stroke": SUPPORT_EDGE, "stroke-width": 1})
    _g(svg, "text", {"x": sx + sw + 8, "y": esup_y + 14, "font-family": "sans-serif",
                     "font-size": 10, "fill": NOTE}).text = "20x20x3 mm support (1.5 mm hole)"
    _g(svg, "rect", {"x": hole_x, "y": esup_y, "width": hole_w, "height": 90,
                     "fill": "#fff", "stroke": "#bbb", "stroke-width": 0.8})

    # emitter paste layers around the pin (inner -> outer): ZnO / CQD / PEDOT:PSS
    # ZnO skin (inner, ETL -- next to the cathode pin)
    _g(svg, "rect", {"x": hole_x + 4, "y": esup_y, "width": hole_w - 8, "height": 90,
                     "fill": ZN_FILL})
    _g(svg, "text", {"x": sx - 4, "y": esup_y + 50, "font-family": "sans-serif",
                     "font-size": 9, "text-anchor": "end",
                     "fill": NOTE}).text = "ZnO skin (inner, ETL)"
    # CQD + PEDOT:PSS annulus (outer, EML + HIL -- near the ITO anode)
    _g(svg, "rect", {"x": hole_x + 1, "y": esup_y, "width": 3, "height": 90,
                     "fill": EMIT_FILL})
    _g(svg, "rect", {"x": hole_x + hole_w - 4, "y": esup_y, "width": 3, "height": 90,
                     "fill": PEDOT_FILL})
    _g(svg, "text", {"x": sx - 4, "y": esup_y + 65, "font-family": "sans-serif",
                     "font-size": 9, "text-anchor": "end",
                     "fill": NOTE}).text = "CQD + PEDOT:PSS (outer)"
    # paste column down to the ITO (the diode)
    _g(svg, "rect", {"x": hole_x + 2, "y": eglass_y - 30, "width": hole_w - 4,
                     "height": 30, "fill": PEDOT_FILL, "fill-opacity": 0.7})
    _g(svg, "text", {"x": sx + sw + 8, "y": eglass_y - 14, "font-family": "sans-serif",
                     "font-size": 10, "fill": NOTE}).text = (
        "paste column = diode (pin -> ITO)")

    # pin shaft (cathode; tip held above ITO so the paste bridges pin-to-ITO)
    _g(svg, "rect", {"x": pin_x, "y": esup_y - 40, "width": pin_w, "height": 120,
                     "fill": PIN_FILL, "stroke": PIN_EDGE, "stroke-width": 0.8})
    _g(svg, "text", {"x": sx + sw + 8, "y": esup_y - 28, "font-family": "sans-serif",
                     "font-size": 10, "fill": NOTE}).text = "pin = cathode + fan-out"
    _g(svg, "text", {"x": sx + sw / 2, "y": eglass_y + 40, "font-family": "sans-serif",
                     "font-size": 10, "text-anchor": "middle",
                     "fill": "#080"}).text = (
        "green EL up  (520-540 nm)  -- layered paste diode (same process as detector)")

    # ---- footer note ----
    _g(svg, "text", {"x": 650, "y": 845, "font-family": "sans-serif",
                     "font-size": 11, "text-anchor": "middle",
                     "fill": NOTE}).text = (
        "Rendered from the real gcode (p1_pin_array_support.gcode). "
        "Pin = standard 2.54 mm header. 1 mm bit circle-bores 1.5 mm holes (0.43 mm clearance).")

    xml = ET.tostring(svg, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml + "\n"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_svg())
    print(f"  wrote {OUT}  ({build_svg().count(chr(10))} lines)")


if __name__ == "__main__":
    main()