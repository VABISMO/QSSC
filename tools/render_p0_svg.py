#!/usr/bin/env python3
"""Render docs/p0_assembly.svg DIRECTLY from the real P0 gcode toolpaths.

Why this exists: the earlier assembly SVG used hand-drawn approximations of each
CNC job, and the drawings drifted from the actual gcode (wrong shapes, wrong
counts, schematic serpentine that did not match the boustrophedon the bit
actually mills). This script parses the gcode emitted by ``tools/gen_qssc_gcode.py``
and draws every cut segment / plunge the bit really makes, so the SVG is a
faithful top view of the generated toolpaths -- if the gcode changes, re-run this
and the SVG tracks it exactly.

Usage:
    python3 tools/render_p0_svg.py        # writes docs/p0_assembly.svg

This model (glm-5.2:cloud) has no image input -- the script is the only source
of truth for the drawing; verify with ``xmllint --noout docs/p0_assembly.svg``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import tools.gen_qssc_gcode as gen

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "p0_assembly.svg"

# ---------------------------------------------------------------------------
# gcode toolpath parser
# ---------------------------------------------------------------------------

_WORD = re.compile(r"([A-Z])(-?[\d.]+)")


def parse_toolpath(text: str):
    """Walk a gcode program and return (cuts, plunges).

    cuts    : list of (x0, y0, x1, y1, z) feed segments milled while Z < 0
    plunges : list of (x, y, z) points where the bit enters the material
              (a G1 Z move from safe Z to a negative Z, at constant XY)
    """
    x = y = z = 0.0
    cuts: list[tuple[float, float, float, float, float]] = []
    plunges: list[tuple[float, float, float]] = []
    for raw in text.splitlines():
        body = raw.split(";", 1)[0].strip()
        if not body:
            continue
        cmd = body.split()[0].upper()
        if not cmd.startswith("G") or cmd[1:] not in {"0", "1", "00", "01"}:
            continue
        nx = ny = nz = None
        for letter, val in _WORD.findall(body):
            if letter == "X":
                nx = float(val)
            elif letter == "Y":
                ny = float(val)
            elif letter == "Z":
                nz = float(val)
        ox, oy, oz = x, y, z
        if nx is not None:
            x = nx
        if ny is not None:
            y = ny
        if nz is not None:
            z = nz
        # plunge: a Z move down into material with no XY change
        if nz is not None and z < 0.0 and oz >= 0.0 and nx is None and ny is None:
            plunges.append((x, y, z))
        # a cut segment: an XY feed move while buried in the material
        if (nx is not None or ny is not None) and z < 0.0:
            cuts.append((ox, oy, x, y, z))
    return cuts, plunges


# ---------------------------------------------------------------------------
# coordinate helpers (gcode is mm, origin bottom-left; SVG y grows downward)
# ---------------------------------------------------------------------------

def gx2sx(gx: float, ox: float, s: float, xmin: float) -> float:
    return ox + (gx - xmin) * s


def gy2sy(gy: float, oy: float, s: float, ymax: float) -> float:
    return oy + (ymax - gy) * s


def cut_path_d(cuts, ox, oy, s, xmin, ymax) -> str:
    """One <path> d-string covering every cut segment (M..L per segment)."""
    parts = []
    for x0, y0, x1, y1, _z in cuts:
        sx0 = gx2sx(x0, ox, s, xmin)
        sy0 = gy2sy(y0, oy, s, ymax)
        sx1 = gx2sx(x1, ox, s, xmin)
        sy1 = gy2sy(y1, oy, s, ymax)
        parts.append(f"M{sx0:.2f},{sy0:.2f}L{sx1:.2f},{sy1:.2f}")
    return " ".join(parts)


def plunge_group(plunges, ox, oy, s, xmin, ymax, r, fill) -> str:
    circles = []
    for x, y, _z in plunges:
        sx = gx2sx(x, ox, s, xmin)
        sy = gy2sy(y, oy, s, ymax)
        circles.append(f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="{r:.2f}" fill="{fill}"/>')
    return "".join(circles)


def die_frame(ox, oy, s, xmin, ymax, die_x, die_y, die_w, stroke="#888") -> str:
    """Thin reference rectangle for the 20x20 die footprint."""
    x0 = gx2sx(die_x, ox, s, xmin)
    y0 = gy2sy(die_y + die_w, oy, s, ymax)
    w = die_w * s
    h = die_w * s
    return (f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'fill="none" stroke="{stroke}" stroke-width="0.7" stroke-dasharray="3 2"/>')


# ---------------------------------------------------------------------------
# panel text (describes the gcode; stable)
# ---------------------------------------------------------------------------

PANEL_TEXT = {
    "A": {
        "ttl": "Job A - L1 BOTTOM face: Ag cathode stencil (64)",
        "sub": "the face that points DOWN to the carrier . Kapton stencil . drawn from p0_cathode_stencil.gcode",
        "purpose": "PURPOSE: cut a 64-window Kapton mask so Ag paste doctor-bladed through it forms 64 isolated cathode pads on L1's bottom face.",
        "cnc": ["- cut 64 square window openings through 50 um Kapton (Z-0.06)",
                "- 8x8 grid, 2.5 mm pitch, window 1.0 mm (NOT 1.5)",
                "- 1 mm bit + 1.0 mm window at 2.5 mm pitch -> 0.5 mm Kapton web",
                "  -> 64 isolated openings (at 1.5 mm the kerfs touch -> Ag shorts)"],
        "then": ["doctor-blade Ag paste through the 64 windows onto the ZnO",
                 "-> 64 Ag cathode pads",
                 "L1 ITO is a CONTINUOUS common anode (no isolation grooves):",
                 "EM_ANODE = silver-epoxy on a bare-ITO corner (masked during",
                 "stack deposition); diode rectification stops pixel cross-talk"],
        "mat": ["20x20x1.1 mm ITO-coated glass (150 nm ITO, continuous anode)",
                "50 um Kapton foil (stencil) . Ag paste . silver epoxy (EM_ANODE)",
                "1.0 mm flat end-mill . backing board . bond BEFORE seating"],
    },
    "B": {
        "ttl": "Job B - L2 TOP face: SERPENTINE ENTANGLEMENT BUS (64 NV sites in series)",
        "sub": "the face that points UP to L3 . 20x20x1 glass wafer . drawn from p0_waveguide_channel.gcode",
        "purpose": "PURPOSE: mill ONE continuous serpentine channel threading all 64 NV sites in series on one shared optical core = the on-chip entanglement bus.",
        "cnc": ["- mill ONE continuous serpentine channel, 1 mm wide, two Z passes (-0.25, -0.5)",
                "- 8 horizontal runs (one per pixel row Y=36.25..53.75) + 7 vertical",
                "  connectors at alternating ends = a single boustrophedon core",
                "- 64 NV-site peck markers (Z-0.20) along the bus",
                "- perimeter MW-loop seating groove (Z-0.30) around the whole die",
                "- 532 pump-in + bus-out facet markers"],
        "then": ["- place one 150-200 um Adamas FMDNVLN microdiamond at each of the 64 NV sites",
                 "- fill the bus with Ti-doped TEOS sol-gel (no diamonds in the sol)",
                 "- soft-bake 80 C, densify 300 C hotplate -> SiO2 core n~1.55 > glass 1.52 (TIR)",
                 "- seat Cu wire in the perimeter groove"],
        "mat": ["20x20x1 mm borosilicate glass wafer", "Ti-doped TEOS sol (n~1.55) . NV- microdiamonds 150-200 um (Adamas FMDNVLN)",
                "T2~1.8 ms long coherence (simulator, 12C bulk) . T1~5.9-10.2 ms, ODMR 4.4-4.9% (Adamas datasheet) . Cu wire . 1.0 mm bit"],
        "why": ["All 64 NV sites share the ONE serial bus, so a photon from any site",
                "propagates along the same core to any other. Two indistinguishable",
                "photons interfere at the bus output -> spin-spin entanglement heralded.",
                "MW 2.87 GHz ODMR; lock-in reads the L3 detector photocurrent."],
    },
    "C": {
        "ttl": "Job C - L3 TOP face: detector stencil (64 Au/Cr pads) on ITO glass",
        "sub": "top of the stack . ITO glass + MoS2/graphene + Au/Cr pads . drawn from p0_detector_stencil.gcode",
        "purpose": "PURPOSE: cut a 64-window Kapton mask so Au/Cr doctor-bladed through it forms 64 graphene-detector readout pads, then spin PMMA passivation over the channels.",
        "cnc": ["- cut 64 Au/Cr pad openings through 50 um Kapton (Z-0.06), 8x8 grid,",
                "  2.5 mm pitch, window 1.0 mm (same 0.5 mm web limit as Job A)",
                "- align Kapton over the L3 ITO glass via shared corner fiducials"],
        "then": ["doctor-blade Au/Cr through the 64 windows -> 64 detector readout pads,",
                 "one per pixel, each reading the NV red PL (637-800 nm) from the",
                 "L2 site directly beneath it",
                 "spin PMMA passivation (F3.1b): keep the 64 Kapton cutout squares,",
                 "place one on each pad -> spin ~150 nm PMMA over the channels, peel",
                 "squares -> pads stay bare for bonding"],
        "mat": ["L3 substrate = ITO glass (ITO film = detector common masa, DET_COMMON,",
                "edge-contacted); PL enters through the glass face (transparent at 637-800 nm)",
                "graphene-in-alcohol flake ink + MoS2-nanosheet ink (LPE from powder, F3.0),",
                "spin-coat MoS2 first then graphene (vdW adhesion + PMMA edge-lock, no CVD)",
                "50 um Kapton . Au/Cr paste . 1 mm bit . PMMA 950K (passivation + edge-lock)",
                "bond 64 top contacts + DET_COMMON AFTER seating"],
    },
    "D": {
        "ttl": "Job D - FR4 carrier 135x90 (one die pocket + 130 holes)",
        "sub": "breakout for the stacked die . 2 mm pin pitch . drawn from p0_carrier_pcb.gcode",
        "purpose": "PURPOSE: machine the FR4 holder (one die pocket + 130 bond holes + outline) that seats the stacked die and brings all 130 contacts to a 2 mm socket.",
        "cnc": ["- pocket ONE 20x20 x 1.1 mm die cavity at (57.5,35) [raster fill shown]",
                "- peck-drill 130 Ø1 holes through FR4 at 2 mm pitch:",
                "  64 emitter-cathode (Y=5) + 64 detector (Y=85)",
                "  + 1 EM_ANODE (3.5,8.0) + 1 DET_COMMON (3.5,82.0) = the 2 ITO commons",
                "- through-cut the 135x90 outline with 4 bridges (Z-3.0)",
                "-> 2 mm male pin headers -> Samtec/Harwin 2 mm socket"],
        "then": [],
        "mat": ["135x90x1.6 mm FR4 . 2 mm male pin headers", "1.0 mm flat end-mill . Ø1 peck drill"],
    },
}

# cut colour per job + whether it is a die-crop (True) or full-carrier (False)
JOB_CFG = {
    "A": ("p0_cathode_stencil.gcode", "#5a5d65", True),
    "B": ("p0_waveguide_channel.gcode", "#2f7d92", True),
    "C": ("p0_detector_stencil.gcode", "#b8902a", True),
    "D": ("p0_carrier_pcb.gcode", "#444", False),
}

DIE_X, DIE_Y, DIE_W = 57.5, 35.0, 20.0
BIT = 1.0  # mm

# die-crop viewport (mm): shows the 20x20 die + a small margin
DIE_XMIN, DIE_YMIN, DIE_XMAX, DIE_YMAX = 56.0, 33.0, 79.0, 57.0
# full-carrier viewport (mm)
CAR_XMIN, CAR_YMIN, CAR_XMAX, CAR_YMAX = -2.0, -2.0, 137.0, 92.0


def text_lines(lines, x, y0, step=14, cls="sm", extra=""):
    out = []
    y = y0
    for ln in lines:
        out.append(f'<text x="{x}" y="{y}" class="{cls}" {extra}>{ln}</text>')
        y += step
    return "".join(out), y


def render_die_panel(key, panel_x, panel_y, panel_w, panel_h, draw_x, draw_y, s):
    gcode_name, color, _ = JOB_CFG[key]
    cuts, plunges = parse_toolpath(gen.FILES[gcode_name]())
    # drop features outside the die crop (the 4 carrier-corner fiducials live at
    # (1,1)/(134,1)/(134,89)/(1,89) -- far outside the 20x20 die window)
    m = 1.5
    cuts = [c for c in cuts
            if not (c[0] < DIE_XMIN - m or c[0] > DIE_XMAX + m or
                    c[2] < DIE_XMIN - m or c[2] > DIE_XMAX + m or
                    c[1] < DIE_YMIN - m or c[1] > DIE_YMAX + m or
                    c[3] < DIE_YMIN - m or c[3] > DIE_YMAX + m)]
    plunges = [p for p in plunges
               if DIE_XMIN - m <= p[0] <= DIE_XMAX + m and
               DIE_YMIN - m <= p[1] <= DIE_YMAX + m]
    ox, oy = draw_x, draw_y
    xmin, ymax = DIE_XMIN, DIE_YMAX
    sw = BIT * s

    # split cuts by Z so each feature gets its own colour:
    #   Z -0.10 = die-footprint alignment engrave -> NOT drawn as a cut (it would
    #             read as a thick outer track hugging the outer pads); the thin
    #             dashed die_frame rect below is the only die reference shown.
    #   Z -0.30 = MW perimeter loop groove (Job C only) -> copper, dashed.
    #   everything else (Z-0.05 scribes, Z-0.06 windows, Z-0.25/-0.5 serpentine)
    #             = the actual functional feature -> job colour, true bit width.
    DIE_FRAME_Z, MW_Z, NV_Z = -0.10, -0.30, -0.20
    main_cuts = [c for c in cuts if abs(c[4] - DIE_FRAME_Z) > 1e-3 and abs(c[4] - MW_Z) > 1e-3]
    mw_cuts = [c for c in cuts if abs(c[4] - MW_Z) < 1e-3]
    nv_plunges = [p for p in plunges if abs(p[2] - NV_Z) < 1e-3]

    main_d = cut_path_d(main_cuts, ox, oy, s, xmin, ymax)
    frame = die_frame(ox, oy, s, xmin, ymax, DIE_X, DIE_Y, DIE_W)

    parts = [f'<g id="panel{key}">',
             f'<rect class="panel" x="{panel_x}" y="{panel_y}" width="{panel_w}" height="{panel_h}" rx="4"/>']
    tx = panel_x + 13
    ty = panel_y + 22
    t = PANEL_TEXT[key]
    parts.append(f'<text x="{tx}" y="{ty}" class="ttl">{t["ttl"]}</text>')
    parts.append(f'<text x="{tx}" y="{ty+16}" class="sub">{t["sub"]}</text>')
    parts.append(f'<text x="{tx}" y="{ty+34}" class="sm b" fill="#1a5fb4">{t["purpose"]}</text>')

    # the real toolpath drawing: main feature
    parts.append(f'<path d="{main_d}" fill="none" stroke="{color}" stroke-width="{sw:.2f}" '
                 f'stroke-linecap="round" stroke-linejoin="round"/>')
    # MW perimeter loop (Job C) -- a Cu-wire seating groove around the die, NOT the
    # functional channel. Draw it as a THIN subtle ring (not the 1 mm kerf width) so
    # it reads as a perimeter annotation instead of a fat orange track that buries the
    # serpentine's outer runs (the groove sits 0.25 mm outside rows Y=36.25/53.75).
    if mw_cuts:
        mw_d = cut_path_d(mw_cuts, ox, oy, s, xmin, ymax)
        parts.append(f'<path d="{mw_d}" fill="none" stroke="#b87333" stroke-width="1.4" '
                     f'stroke-opacity="0.75" stroke-linecap="round" stroke-linejoin="round" '
                     f'stroke-dasharray="5 3"/>')
    # 64 NV sites (Job C) -- red dots, numbered 1..64 in boustrophedon visit order.
    # The 2 facet markers (532 pump-in / bus-out) are also at Z-0.20 but sit on the
    # left facet (X=58.0), off the pixel-centre grid -> split them out as green dots.
    if nv_plunges:
        PX = [58.75, 61.25, 63.75, 66.25, 68.75, 71.25, 73.75, 76.25]
        PY = [36.25, 38.75, 41.25, 43.75, 46.25, 48.75, 51.25, 53.75]

        def on_grid(p):
            return any(abs(p[0] - x) < 0.1 for x in PX) and any(abs(p[1] - y) < 0.1 for y in PY)

        sites = [p for p in nv_plunges if on_grid(p)]
        facets = [p for p in nv_plunges if not on_grid(p)]
        # number the 64 sites in boustrophedon visit order (row ascending, alternate X dir)
        rows: dict[float, list] = {}
        for p in sites:
            rows.setdefault(round(p[1], 3), []).append(p)
        ordered = []
        for i, y in enumerate(sorted(rows)):
            rs = sorted(rows[y], key=lambda p: p[0])
            ordered.extend(rs if i % 2 == 0 else list(reversed(rs)))
        for n, (px, py, _z) in enumerate(ordered, 1):
            sx = gx2sx(px, ox, s, xmin)
            sy = gy2sy(py, oy, s, ymax)
            parts.append(f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="3.4" fill="#c0392b" '
                         f'stroke="#fff" stroke-width="0.4"/>')
            parts.append(f'<text x="{sx:.2f}" y="{sy+1.5:.2f}" class="xs" text-anchor="middle" '
                         f'fill="#fff">{n}</text>')
        # the 2 facet markers -> green dots (pump in / bus out)
        for px, py, _z in facets:
            sx = gx2sx(px, ox, s, xmin)
            sy = gy2sy(py, oy, s, ymax)
            parts.append(f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="3.2" fill="#2ea043" '
                         f'stroke="#fff" stroke-width="0.4"/>')
        # boustrophedon direction arrows on each horizontal run
        seen = set()
        for c in main_cuts:
            x0, y0, x1, y1, z = c
            if abs(y0 - y1) > 1e-6 or abs(x1 - x0) < 5.0:
                continue
            key_y = round(y0, 3)
            if key_y in seen:
                continue
            seen.add(key_y)
            midx = (x0 + x1) / 2.0
            d = 1 if x1 > x0 else -1
            ax = gx2sx(midx + d * 1.0, ox, s, xmin)
            ay = gy2sy(y0, oy, s, ymax)
            bx = gx2sx(midx - d * 1.0, ox, s, xmin)
            parts.append(f'<path d="M{ax:.2f},{ay:.2f} L{bx:.2f},{ay:.2f}" stroke="#1b4f5c" '
                         f'stroke-width="1.4" marker-end="url(#arrS)"/>')
    parts.append(frame)

    # right-side text columns
    rx = draw_x + (DIE_XMAX - DIE_XMIN) * s + 18
    ry = draw_y - 8
    parts.append(f'<text x="{rx}" y="{ry}" class="lab b">What the CNC does</text>')
    ry += 16
    block, ry = text_lines(t["cnc"], rx, ry, 14, "sm")
    parts.append(block)
    if t["then"]:
        parts.append(f'<text x="{rx}" y="{ry+8}" class="lab b">Then (not CNC)</text>')
        ry += 24
        block, ry = text_lines(t["then"], rx, ry, 14, "sm")
        parts.append(block)
    parts.append(f'<text x="{rx}" y="{ry+8}" class="lab b">Materials</text>')
    ry += 24
    block, ry = text_lines(t["mat"], rx, ry, 14, "sm")
    parts.append(block)
    if key == "B":
        parts.append(f'<text x="{rx}" y="{ry+10}" class="lab b" fill="#c0392b">Why this is the entanglement layer</text>')
        ry += 26
        block, ry = text_lines(t["why"], rx, ry, 14, "sm")
        parts.append(block)
    parts.append("</g>")
    return "".join(parts)


def render_carrier_panel(key, panel_x, panel_y, panel_w, panel_h, draw_x, draw_y, s):
    gcode_name, color, _ = JOB_CFG[key]
    cuts, plunges = parse_toolpath(gen.FILES[gcode_name]())
    ox, oy = draw_x, draw_y
    xmin, ymax = CAR_XMIN, CAR_YMAX
    sw = BIT * s
    d = cut_path_d(cuts, ox, oy, s, xmin, ymax)
    plunges_svg = plunge_group(plunges, ox, oy, s, xmin, ymax, sw * 0.5, color)
    parts = [f'<g id="panel{key}">',
             f'<rect class="panel" x="{panel_x}" y="{panel_y}" width="{panel_w}" height="{panel_h}" rx="4"/>']
    tx = panel_x + 13
    ty = panel_y + 22
    t = PANEL_TEXT[key]
    parts.append(f'<text x="{tx}" y="{ty}" class="ttl">{t["ttl"]}</text>')
    parts.append(f'<text x="{tx}" y="{ty+16}" class="sub">{t["sub"]}</text>')
    parts.append(f'<text x="{tx}" y="{ty+34}" class="sm b" fill="#1a5fb4">{t["purpose"]}</text>')
    parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw:.2f}" '
                 f'stroke-linecap="round" stroke-linejoin="round"/>')
    parts.append(plunges_svg)
    # carrier outline frame
    cx0 = gx2sx(0, ox, s, xmin); cy0 = gy2sy(90, oy, s, ymax)
    parts.append(f'<rect x="{cx0:.2f}" y="{cy0:.2f}" width="{135*s:.2f}" height="{90*s:.2f}" '
                 f'fill="none" stroke="#333" stroke-width="1"/>')
    parts.append(f'<text x="{gx2sx(67.5, ox, s, xmin):.1f}" y="{gy2sy(57, oy, s, ymax):.1f}" '
                 f'class="sm" text-anchor="middle">[DIE POCKET] 20x20 @ (57.5,35)</text>')
    parts.append(f'<text x="{gx2sx(67.5, ox, s, xmin):.1f}" y="{gy2sy(33, oy, s, ymax):.1f}" '
                 f'class="sm" text-anchor="middle">stacked die seats here</text>')
    # bottom-edge / top-edge hole-row labels
    parts.append(f'<text x="{gx2sx(67.5, ox, s, xmin):.1f}" y="{gy2sy(8, oy, s, ymax):.1f}" '
                 f'class="sm" text-anchor="middle">64 emitter-cathode holes (Y=5)</text>')
    parts.append(f'<text x="{gx2sx(67.5, ox, s, xmin):.1f}" y="{gy2sy(87, oy, s, ymax):.1f}" '
                 f'class="sm" text-anchor="middle">64 detector holes (Y=85)</text>')
    # the two ITO common holes (EM_ANODE bottom-left, DET_COMMON top-left)
    parts.append(f'<text x="{gx2sx(2.5, ox, s, xmin):.1f}" y="{gy2sy(13, oy, s, ymax):.1f}" '
                 f'font-size="8" fill="#92501a" text-anchor="start">EM_ANODE (3.5,8.0)</text>')
    parts.append(f'<text x="{gx2sx(2.5, ox, s, xmin):.1f}" y="{gy2sy(81, oy, s, ymax):.1f}" '
                 f'font-size="8" fill="#92501a" text-anchor="start">DET_COMMON (3.5,82.0)</text>')
    # text block under the drawing
    ry = draw_y + 92 * s + 16
    parts.append(f'<text x="{tx}" y="{ry}" class="lab b">What the CNC does</text>')
    ry += 16
    block, ry = text_lines(t["cnc"], tx, ry, 14, "sm")
    parts.append(block)
    parts.append("</g>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# static conceptual panels (cross-section + materials/entanglement)
# ---------------------------------------------------------------------------

CROSS_SECTION = """<g id="panelF">
  <rect class="panel" x="20" y="1545" width="760" height="570" rx="4"/>
  <text x="33" y="1567" class="ttl">ASSEMBLY cross-section (vertical stack, top -> bottom)</text>
  <text x="33" y="1583" class="sub">thickness exaggerated . die clamped between two contact planes . 532 pump from LEFT . PL up . no TSV</text>
  <rect x="180" y="1600" width="240" height="8" fill="#7a7d85"/>
  <text x="300" y="1596" class="sm" text-anchor="middle">2 mm pin row . top edge Y=85 . 64 detector bonds + DET_COMMON</text>
  <rect x="180" y="1612" width="240" height="12" fill="#d4af37" fill-opacity="0.55"/>
  <text x="428" y="1621" class="sm">64 detector contacts (Au/Cr, TOP)</text>
  <rect x="180" y="1626" width="240" height="34" fill="#2b2b2b" stroke="#111" stroke-width="0.6"/>
  <rect x="180" y="1626" width="240" height="4" fill="#e8c87a" fill-opacity="0.6"/>
  <text x="300" y="1647" class="lab" text-anchor="middle" fill="#eee">L3 . ITO glass + graphene/MoS2 photodetector + PMMA</text>
  <text x="428" y="1641" class="sm" fill="#92501a">ITO film = DET_COMMON masa (edge-contacted)</text>
  <rect x="180" y="1664" width="240" height="74" fill="#9fd0e0" stroke="#5b94a4" stroke-width="0.8"/>
  <path d="M188,1686 L412,1686 L412,1700 L188,1700 L188,1714 L412,1714 L412,1728 L188,1728" fill="none" stroke="#4a8294" stroke-width="2.4"/>
  <g fill="#c0392b"><circle cx="196" cy="1686" r="1.6"/><circle cx="300" cy="1700" r="1.6"/><circle cx="404" cy="1728" r="1.6"/></g>
  <text x="300" y="1660" class="lab" text-anchor="middle">L2 . TEOS sol-gel ENTANGLEMENT BUS + 64 NV sites</text>
  <text x="428" y="1686" class="sm">serpentine core, 64 NV</text>
  <text x="428" y="1700" class="sm">sites in series</text>
  <text x="428" y="1714" class="sm">n~1.55 TIR</text>
  <rect x="180" y="1742" width="240" height="40" fill="#36c46a" stroke="#238a4a" stroke-width="0.8"/>
  <text x="300" y="1766" class="lab" text-anchor="middle">L1 . CQD emitter 8x8 (green LED)</text>
  <text x="428" y="1758" class="sm">ITO/PEDOT/TFB/CQD/</text>
  <text x="428" y="1772" class="sm">ZnO/Ag (see Job A)</text>
  <text x="428" y="1786" class="sm" fill="#92501a">ITO = continuous EM_ANODE (edge)</text>
  <rect x="180" y="1786" width="240" height="12" fill="#c9ccd2"/>
  <text x="428" y="1795" class="sm">64 Ag cathodes + EM_ANODE (BOTTOM)</text>
  <rect x="170" y="1804" width="260" height="14" fill="#3a7a44" stroke="#26502e" stroke-width="0.8"/>
  <text x="300" y="1834" class="sm" text-anchor="middle">FR4 carrier 1.6 mm (one 20x20 die pocket)</text>
  <rect x="180" y="1850" width="240" height="8" fill="#7a7d85"/>
  <text x="300" y="1872" class="sm" text-anchor="middle">2 mm pin row . bottom edge Y=5 . 64 cathode bonds + EM_ANODE</text>
  <text x="300" y="1886" class="sm" text-anchor="middle" fill="#92501a">bond bottom cathodes + EM_ANODE BEFORE seating the die</text>
  <path d="M110,1700 L176,1700" stroke="#2ea043" stroke-width="2.8" marker-end="url(#arrG)"/>
  <text x="105" y="1696" class="sm" text-anchor="end" fill="#2ea043">532 nm laser</text>
  <text x="105" y="1708" class="sm" text-anchor="end" fill="#2ea043">pump -></text>
  <path d="M300,1680 L300,1662" stroke="#c0392b" stroke-width="2.2" marker-end="url(#arrR)"/>
  <text x="308" y="1674" class="sm" fill="#c0392b">NV PL up</text>
  <path d="M240,1740 L240,1722" stroke="#2ea043" stroke-width="1.6" marker-end="url(#arrG)" stroke-dasharray="3 2"/>
  <text x="246" y="1734" class="sm" fill="#2ea043">CQD EL up (2nd pump)</text>
  <path d="M130,1778 Q300,1650 470,1778" fill="none" stroke="#b87333" stroke-width="2.4"/>
  <text x="300" y="1654" class="sm" text-anchor="middle" fill="#92501a">MW loop 2.87 GHz</text>
  <g stroke="#888" stroke-width="0.8" fill="none"><path d="M170,1612 L160,1612 L160,1798 L170,1798"/></g>
  <text x="156" y="1705" class="sm" text-anchor="end" fill="#555" transform="rotate(-90 156 1705)">two-faced die . 20x20 . no TSV</text>
  <rect x="33" y="1910" width="734" height="190" fill="#fbf6ec" stroke="#d8b96a" stroke-width="0.8" rx="3"/>
  <text x="43" y="1930" class="lab b">Stack order + physics</text>
  <g class="sm">
    <text x="43" y="1950">1. Job A: 64 Ag cathodes on L1 bottom (Kapton stencil). L1 ITO = CONTINUOUS common anode (EM_ANODE =</text>
    <text x="43" y="1964">   silver-epoxy on a bare-ITO corner, masked during stack deposition; no isolation grooves). Spin PEDOT/TFB/CQD-PVA/ZnO.</text>
    <text x="43" y="1980">2. Job B: mill the serpentine entanglement bus on L2; fill with TEOS + NV microdiamonds; seat Cu MW loop.</text>
    <text x="43" y="1996">3. L3: LPE MoS2 powder -> ink (F3.0); clean ITO glass, spin-coat MoS2 + graphene-in-alcohol inks (ITO film = DET_COMMON masa);</text>
    <text x="43" y="2010">   Job C (64 Au/Cr pads); spin PMMA passivation (F3.1b). PL enters through the ITO glass face.</text>
    <text x="43" y="2026">4. Job D: FR4 carrier (one die pocket + 130 holes). Stack L1+L2+L3 (index-matching epoxy; LEFT facet of L2 exposed for 532 pump); seat.</text>
    <text x="43" y="2042">5. Bond 64 top detector contacts + DET_COMMON; 130 pins -> 2 mm socket on a breakout motherboard.</text>
    <text x="43" y="2072" fill="#92501a">Readout: NV PL up the bus -> L3 graphene (lock-in, MW 2.87 GHz ODMR). Sole detector = graphene/MoS2 on ITO glass.</text>
  </g>
</g>"""

MATERIALS = """<g id="panelG">
  <rect class="panel" x="813" y="1545" width="487" height="570" rx="4"/>
  <text x="823" y="1567" class="ttl">Materials + the serial entanglement bus</text>
  <text x="823" y="1590" class="lab b">L1 emitter (bottom layer)</text>
  <g class="sm">
    <text x="823" y="1608">20x20x1.1 mm ITO glass (150 nm ITO, CONTINUOUS common anode) . PEDOT:PSS . TFB (chlorobenzene)</text>
    <text x="823" y="1622">green Cd-free CQD-PVA . ZnO nanopowder . Ag paste (64 cathodes) . silver epoxy (EM_ANODE) . Kapton 50 um</text>
  </g>
  <text x="823" y="1646" class="lab b">L2 waveguide + NV (middle layer)</text>
  <g class="sm">
    <text x="823" y="1664">20x20x1 mm borosilicate glass . Ti-doped TEOS sol (n~1.55)</text>
    <text x="823" y="1678">NV- microdiamonds 150-200 um (Adamas FMDNVLN, T1~5.9-10.2 ms) . Cu wire (MW loop)</text>
  </g>
  <text x="823" y="1702" class="lab b">L3 detector (top layer)</text>
  <g class="sm">
    <text x="823" y="1720">graphene-in-alcohol + MoS2-ink (LPE from powder) spin-coated on ITO glass (ITO film = DET_COMMON masa,</text>
    <text x="823" y="1734">edge-contacted; PL enters through the glass face) . vdW adhesion + PMMA edge-lock . Kapton 50 um</text>
    <text x="823" y="1748">PMMA 950K spin-coated passivation over the channels (F3.1b, pad-masked) . 1.0 mm flat end-mill (all 4 jobs, no tool change)</text>
  </g>
  <text x="823" y="1758" class="lab b">Carrier + pump</text>
  <g class="sm">
    <text x="823" y="1776">135x90x1.6 mm FR4 . 2 mm male pin headers . Samtec TMM-1xx / Harwin M20-782</text>
    <text x="823" y="1790">2 mm female socket . 532 nm DPSS laser (left-facet pump) . magnet wire fan-out</text>
  </g>
  <text x="823" y="1816" class="lab b" fill="#c0392b">The serial entanglement bus</text>
  <g class="sm">
    <text x="823" y="1834">One continuous boustrophedon core (8 runs + 7 connectors) threads all</text>
    <text x="823" y="1848">64 NV microdiamonds IN SERIES on the same TEOS-sol-gel optical core.</text>
    <text x="823" y="1862">532 nm pump enters the LEFT facet, walks the whole bus and pumps every</text>
    <text x="823" y="1876">site in sequence. Each site's red PL (637-800 nm) leaves UP through the</text>
    <text x="823" y="1890">wafer to the L3 graphene pad directly above it. Because all 64 sites share</text>
    <text x="823" y="1904">one core, a photon from any site reaches any other -> two indistinguishable</text>
    <text x="823" y="1918">photons interfere at the bus output -> spin-spin entanglement heralded. A Cu</text>
    <text x="823" y="1932">loop in the perimeter groove baths all 64 sites at 2.87 GHz (ODMR); a</text>
    <text x="823" y="1946">lock-in reads per-pixel detector photocurrent.</text>
    <text x="823" y="1972" fill="#92501a">The 4 job panels above are rendered straight from the .gcode toolpaths</text>
    <text x="823" y="1986" fill="#92501a">(tools/render_p0_svg.py) -> the drawings are the real cut geometry.</text>
  </g>
</g>"""


NOTES_PANEL = """<g id="panelNotes">
  <rect class="panel" x="20" y="1095" width="630" height="430" rx="4"/>
  <text x="33" y="1117" class="ttl">ASSEMBLY ORDER (4 CNC jobs, 1 mm bit, no tool change)</text>
  <text x="33" y="1133" class="sub">vertical 3-layer stack on one 20x20 two-faced die . no TSV . 130 bond holes</text>
  <g class="sm">
    <text x="33" y="1158" class="lab b">Job A - L1 BOTTOM: Ag cathode stencil (64 windows)</text>
    <text x="45" y="1172">Kapton mask, doctor-blade Ag -> 64 cathode pads on the L1 bottom face.</text>
    <text x="45" y="1186">L1 ITO is a CONTINUOUS common anode (no isolation grooves): EM_ANODE =</text>
    <text x="45" y="1200">silver-epoxy on a bare-ITO corner (masked during stack deposition);</text>
    <text x="45" y="1214">diode rectification stops pixel cross-talk.</text>
    <text x="33" y="1238" class="lab b">Job B - L2 TOP: serpentine entanglement bus (64 NV sites in series)</text>
    <text x="45" y="1252">One continuous boustrophedon core threads all 64 NV microdiamonds on</text>
    <text x="45" y="1266">one TEOS-sol-gel optical core -> the on-chip entanglement bus.</text>
    <text x="33" y="1290" class="lab b">Job C - L3 TOP: Au/Cr detector stencil (64 pads) on ITO glass</text>
    <text x="45" y="1304">L3 substrate = ITO glass; the ITO film is the detector common masa</text>
    <text x="45" y="1318">(DET_COMMON, edge-contacted). Spin-coat MoS2-ink + graphene-in-alcohol,</text>
    <text x="45" y="1332">64 Au/Cr readout pads, PMMA passivation over the channels.</text>
    <text x="33" y="1356" class="lab b">Job D - FR4 carrier 135x90 (one die pocket + 130 holes)</text>
    <text x="45" y="1370">64 detector (Y=85) + 64 emitter-cathode (Y=5) + EM_ANODE (3.5,8.0) +</text>
    <text x="45" y="1384">DET_COMMON (3.5,82.0) = 130 bond holes -> 2 mm socket on a breakout.</text>
    <text x="33" y="1414" fill="#92501a">Bond the 64 bottom cathodes + EM_ANODE BEFORE seating the die;</text>
    <text x="33" y="1428" fill="#92501a">seat, then bond the 64 top detector contacts + DET_COMMON.</text>
    <text x="33" y="1458">532 nm pump in at the L2 LEFT facet -> walks the serial bus -> every NV</text>
    <text x="33" y="1472">site pumped in sequence -> red PL (637-800 nm) UP to the L3 graphene pad</text>
    <text x="33" y="1486">above each site -> lock-in readout at the 2.87 GHz ODMR dip.</text>
  </g>
</g>"""


def build_svg() -> str:
    """Return the complete assembly SVG as a string (drawn from the gcode)."""
    # viewBox 1320 x 2140
    svg = []
    svg.append('<?xml version="1.0" encoding="UTF-8"?>')
    svg.append('<!-- QSSC-P0.2.0 assembly. The 4 job top-views are rendered DIRECTLY from the')
    svg.append('     real gcode toolpaths by tools/render_p0_svg.py (parse G0/G1 cuts + plunges,')
    svg.append('     draw each segment at the true 1 mm bit width). English only. -->')
    svg.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1320 2140" '
               'font-family="ui-sans-serif, system-ui, -apple-system, \'Segoe UI\', Roboto, sans-serif" '
               'text-rendering="geometricPrecision">')
    svg.append('<title>QSSC-P0.2.0 assembly: per-CNC-layer top views rendered from gcode + cross-section</title>')
    svg.append('<desc>Top view of every CNC job rendered from the actual gcode toolpaths (Job A Ag cathode stencil, '
               'Job B serpentine TEOS+NV entanglement bus, Job C Au/Cr detector stencil on ITO glass, '
               'Job D FR4 carrier with 130 holes) plus the vertical assembly cross-section and the serial-bus entanglement explanation.</desc>')
    svg.append('<defs>'
               '<marker id="arrG" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
               'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#2ea043"/></marker>'
               '<marker id="arrR" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
               'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#c0392b"/></marker>'
               '<marker id="arrS" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
               'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#1b4f5c"/></marker>'
               '</defs>')
    svg.append('<style>')
    svg.append('.bg{fill:#ffffff}.panel{fill:#fcfcfa;stroke:#bbb;stroke-width:1}')
    svg.append('.ttl{font-size:15px;font-weight:700;fill:#222}.sec{font-size:12.5px;font-weight:700;fill:#333;letter-spacing:.5px}')
    svg.append('.sub{font-size:11px;fill:#555}.lab{font-size:11px;fill:#222}.sm{font-size:9.5px;fill:#444}')
    svg.append('.xs{font-size:7px;fill:#fff}.b{font-weight:700}')
    svg.append('</style>')
    svg.append('<rect class="bg" width="1320" height="2140"/>')
    svg.append('<text x="25" y="28" font-size="18" font-weight="700" fill="#222">QSSC-P0 . 2.0 assembly: every CNC layer rendered from the real gcode + cross-section</text>')
    svg.append('<text x="25" y="48" class="sub">vertical 3-layer stack on one 20x20 die . L3 graphene/MoS2 detector on ITO glass TOP / L2 TEOS+NV entanglement bus MIDDLE / L1 CQD emitter on ITO glass BOTTOM . 4 CNC jobs, 1 mm bit . 64 NV sites wired in series on one bus</text>')
    svg.append('<line x1="25" y1="56" x2="1295" y2="56" stroke="#ccc"/>')
    svg.append('<text x="25" y="78" class="sec">THE 4 CNC LAYERS (top view of each, drawn from the .gcode toolpaths at the centred die pocket (57.5,35))</text>')

    # Panel A: L1 Ag cathode stencil, die crop, scale 12 px/mm (top-left)
    svg.append(render_die_panel("A", 20, 90, 630, 430, 40, 150, 12.0))
    # Panel C: L3 Au/Cr detector stencil on ITO glass, die crop, scale 12 px/mm (top-right)
    svg.append(render_die_panel("C", 670, 90, 630, 430, 690, 150, 12.0))
    # Panel B: L2 serpentine entanglement bus, wide die crop, scale 15 px/mm (middle)
    svg.append(render_die_panel("B", 20, 535, 1280, 545, 40, 595, 15.0))
    # Panel D: FR4 carrier, full carrier, scale 3.4 px/mm (bottom-right)
    svg.append(render_carrier_panel("D", 670, 1095, 630, 430, 685, 1155, 3.4))
    # Assembly-order notes panel (bottom-left)
    svg.append(NOTES_PANEL)

    svg.append(CROSS_SECTION)
    svg.append(MATERIALS)
    svg.append('</svg>')
    return "\n".join(svg) + "\n"


def main() -> int:
    text = build_svg()
    OUT.write_text(text)
    print(f"wrote {OUT}  ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())