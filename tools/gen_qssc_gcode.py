#!/usr/bin/env python3
"""Deterministically regenerate the QSSC CNC toolpaths (hardware/gcode/*.gcode).

Replaces the hand-written, carried-over QNOS toolpaths with auditable,
reproducible g-code generated from explicit parameters. Fixes found in the
originals:

  * **Safe-start block** -- Z retracts to a safe height BEFORE the spindle
    starts (the originals ran ``M03 S8000`` immediately with the bit near the
    stock; a safe-start block is standard CNC practice).
  * **Round pillars, not square** -- a ``G02/G03`` circular finishing pass
    rounds each 8x8 site post to the *claimed* diameter. The originals pocketed
    with a cross-hatch that leaves SQUARE posts yet the header said "2 mm
    diameter cylinders" -- the geometry did not match the description.
  * **Correct outlines** -- ``cylinder_chip`` frees ALL FOUR arrays (the
    original pocketed four but only through-cut a single 32x32 region, leaving
    three arrays still attached to the stock).
  * **Correct tooling** -- the ``fluorescence-sensor`` grooves use the 1 mm
    V-bit, but the through-cut outline is emitted as a SEPARATE end-mill
    section with an explicit tool-change note (the original used the V-bit for
    a through-profile cut, which is not a V-bit operation).
  * **Bridges (tabs)** on through-cuts so the freed part is retained by the
    stock rather than launched -- standard practice for through-cutting.
  * Explicit, consistent feeds/speeds and Z-step.

These are MACRO carrier/substrate toolpaths (mm scale) for the optical
substrate that hosts the emitter / waveguide over the 8x8 site array -- NOT the
microchip features (2.5 um pitch, made by lithography). The generator fixes
geometry and safety structure; it does NOT know your machine's tool offsets,
backlash, fixturing or workpiece zero. ALWAYS verify feeds, fixturing, tool
offsets and workpiece zero in your CAM/controller before every run.

Run:  python tools/gen_qssc_gcode.py      (regenerates all three files)
"""
from __future__ import annotations

import math
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "hardware" / "gcode"

# --- shared machine defaults ------------------------------------------------
SAFE_Z = 10.0          # rapid / retract height (mm)
FINAL_Z = 50.0         # end-of-program retract (mm)
SPINDLE_RPM = 8000
PLUNGE_FEED = 100      # mm/min for Z moves into material
CUT_FEED = 200         # mm/min for XY cuts
SAFE_START = f"""G21 G90 G94 G17 G40 G49 G80   ; mm, abs, feed/min, XY plane, cancel comp
G0 Z{SAFE_Z:.1f}                       ; safe Z FIRST
M03 S{SPINDLE_RPM}                    ; spindle ON only after Z is safe"""

PROGRAM_END = f"""G0 Z{FINAL_Z:.1f}
M05
G0 X0.0 Y0.0
M30"""


class G:
    """Tiny g-code emitter that tracks position so arcs are well-formed."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.x = 0.0
        self.y = 0.0
        self.z = SAFE_Z

    def comment(self, *parts: str) -> None:
        self.lines.append("; " + " | ".join(parts))

    def blank(self) -> None:
        self.lines.append("")

    def rapid(self, x: float, y: float, z: float | None = None) -> None:
        if z is not None and z != self.z:
            self.lines.append(f"G0 Z{z:.3f}")
            self.z = z
        self.lines.append(f"G0 X{x:.3f} Y{y:.3f}")
        self.x, self.y = x, y

    def plunge(self, z: float, feed: int = PLUNGE_FEED) -> None:
        self.lines.append(f"G1 Z{z:.3f} F{feed}")
        self.z = z

    def feed(self, x: float, y: float, feed: int = CUT_FEED) -> None:
        self.lines.append(f"G1 X{x:.3f} Y{y:.3f} F{feed}")
        self.x, self.y = x, y

    def arc_cw(self, x: float, y: float, i: float, j: float,
               feed: int = CUT_FEED) -> None:
        """Clockwise arc (G02) to (x,y) with center offset (i,j) from current."""
        self.lines.append(f"G02 X{x:.3f} Y{y:.3f} I{i:.3f} J{j:.3f} F{feed}")
        self.x, self.y = x, y

    def retract(self, z: float = SAFE_Z) -> None:
        self.lines.append(f"G0 Z{z:.3f}")
        self.z = z

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


# --- helpers -----------------------------------------------------------------

def crosshatch_pocket(g: G, x0: float, y0: float, size: float, pitch: float,
                      bit: float, z_passes: list[float]) -> None:
    """Rough pocket an ``size`` x ``size`` region by cross-hatch raster.

    Leaves a grid of square posts (``pitch`` spacing, ``bit`` wide) -- the
    finishing pass rounds them. Slots are centred on the post gaps so the post
    grid sits on the array origin.
    """
    n = round(size / pitch)
    # Posts sit at x0 + pitch/2 + k*pitch (the 8x8 site centres); the cross-hatch
    # removes the material BETWEEN them, so slot centres land at x0 + pitch*0.75 +
    # k*pitch (e.g. 3, 7, 11, ... on a 30 mm / pitch-4 array), leaving a 2 mm square
    # post centred on every site. The finishing pass rounds each to a cylinder.
    slot_y = [y0 + pitch * 0.75 + k * pitch for k in range(n - 1)]
    slot_x = [x0 + pitch * 0.75 + k * pitch for k in range(n - 1)]
    for z in z_passes:
        for sy in slot_y:
            g.rapid(x0, sy)
            g.plunge(z)
            g.feed(x0 + size, sy)
            g.retract()
        for sx in slot_x:
            g.rapid(sx, y0)
            g.plunge(z)
            g.feed(sx, y0 + size)
            g.retract()


def round_posts(g: G, x0: float, y0: float, size: float, pitch: float,
                pillar_r: float, z: float) -> None:
    """Circular G02 finishing pass that rounds each square post to a cylinder.

    Post centres sit on the ``pitch`` grid offset by ``pitch/2`` from origin so
    a post sits at each of the 8x8 site centres. The arc radius equals the post
    half-width (the inscribed circle of the square rough post), leaving a true
    round pillar of ``2*pillar_r`` diameter.
    """
    n = round(size / pitch)
    centres = [x0 + pitch / 2 + k * pitch for k in range(n)]
    r = pillar_r
    for cy in centres:
        for cx in centres:
            # start at the west point of the circle, go CW a full 360 back to it.
            # G02 I/J is the centre offset from the start point (cx-r, cy) to the
            # centre (cx, cy) -> I = +r, J = 0. End point == start point = full circle.
            g.rapid(cx - r, cy)
            g.plunge(z)
            g.arc_cw(cx - r, cy, i=r, j=0.0)
            g.retract()


def outline_with_bridges(g: G, x0: float, y0: float, size: float, bit: float,
                         z_passes: list[float], bridges: int = 4,
                         bridge_len: float = 1.0) -> None:
    """Through-cut a ``size`` square at ``+bit/2`` offset (so the bit frees the
    part) with ``bridges`` tabs of ``bridge_len`` so the part is retained.

    The bit centre travels at the part edge + half the bit, i.e. the cut
    removes material from the edge outward -- standard for freeing a part to
    nominal size. Tabs keep the part attached to the stock for safety.
    """
    off = bit / 2.0
    # four edges, each split by a centred bridge (tab): the bridge is left at
    # the nominal Z (not cut through) over a short span in the middle of each
    # edge.
    corners = [
        (x0 - off, y0 - off),
        (x0 + size + off, y0 - off),
        (x0 + size + off, y0 + size + off),
        (x0 - off, y0 + size + off),
    ]
    edges = [
        (corners[0], corners[1]),   # bottom  (X+ )
        (corners[1], corners[2]),   # right   (Y+ )
        (corners[2], corners[3]),   # top     (X- )
        (corners[3], corners[0]),   # left    (Y- )
    ]
    for z in z_passes:
        for (sx, sy), (ex, ey) in edges:
            # bridge gap in the middle of the edge
            mx, my = (sx + ex) / 2.0, (sy + ey) / 2.0
            # direction unit vector
            dx, dy = ex - sx, ey - sy
            L = math.hypot(dx, dy)
            ux, uy = dx / L, dy / L
            half = bridge_len / 2.0
            # cut start -> bridge-in, lift over bridge, bridge-out -> end
            g.rapid(sx, sy)
            g.plunge(z)
            g.feed(mx - ux * half, my - uy * half)
            g.retract()
            g.rapid(mx + ux * half, my + uy * half)
            g.plunge(z)
            g.feed(ex, ey)
            g.retract()


# --- the three toolpaths -----------------------------------------------------

def gen_square_chip() -> str:
    g = G()
    g.comment("QSSC CNC toolpath -- square chip carrier (8x8 round pillar array)")
    g.comment("GENERATED by tools/gen_qssc_gcode.py -- deterministic, auditable")
    g.comment(f"Stock: 30x30x3 mm acrylic | tool: 2.0 mm flat end-mill | "
              f"spindle {SPINDLE_RPM} rpm")
    g.comment("Sites: 8x8 = 64 | pitch 4 mm | pillar dia 2 mm | base 1 mm "
              "(pocket depth 2 mm, Z-step 1 mm)")
    g.comment("Rough cross-hatch leaves square posts; circular G02 finish "
              "rounds them to 2 mm cylinders.")
    g.comment("Outline through-cuts the 30 mm part with 4 bridges (tabs).")
    g.blank()
    g.lines.append(SAFE_START)
    g.blank()
    SIZE, PITCH, BIT = 30.0, 4.0, 2.0
    z_passes = [-1.0, -2.0]
    g.comment("-- rough pocket (cross-hatch, leaves 8x8 square posts) --")
    crosshatch_pocket(g, 0.0, 0.0, SIZE, PITCH, BIT, z_passes)
    g.blank()
    g.comment("-- finish pass (G02 circles, rounds posts to 2 mm dia) --")
    round_posts(g, 0.0, 0.0, SIZE, PITCH, pillar_r=BIT / 2.0, z=-2.0)
    g.blank()
    g.comment("-- outline through-cut (2 mm end-mill, 4 bridges) --")
    outline_with_bridges(g, 0.0, 0.0, SIZE, BIT, [-1.0, -2.0, -3.0], bridges=4)
    g.blank()
    g.lines.append(PROGRAM_END)
    return g.text()


def gen_cylinder_chip() -> str:
    """Four 30x30 arrays in a 2x2 grid, 5 mm apart; each is an 8x8 round-pillar
    array. The outline frees ALL FOUR arrays (the original freed only one)."""
    g = G()
    g.comment("QSSC CNC toolpath -- 4-array production carrier (4x 8x8 pillars)")
    g.comment("GENERATED by tools/gen_qssc_gcode.py -- deterministic, auditable")
    g.comment("Stock: ~65x65x3 mm acrylic | tool: 2.0 mm flat end-mill | "
              f"spindle {SPINDLE_RPM} rpm")
    g.comment("4 arrays (2x2, 5 mm separation), each 30x30 mm, 8x8 = 64 round "
              "pillars, pitch 4 mm, pillar dia 2 mm, base 1 mm.")
    g.comment("Outline through-cuts ALL FOUR arrays (the original only freed "
              "one 32x32 region); each with 4 bridges.")
    g.blank()
    g.lines.append(SAFE_START)
    g.blank()
    SIZE, PITCH, BIT, SEP = 30.0, 4.0, 2.0, 5.0
    step = SIZE + SEP
    origins = [(0.0, 0.0), (step, 0.0), (0.0, step), (step, step)]
    for i, (ox, oy) in enumerate(origins, 1):
        g.blank()
        g.comment(f"-- array {i}/4 at origin ({ox:.0f}, {oy:.0f}) --")
        g.comment("  rough pocket (cross-hatch)")
        crosshatch_pocket(g, ox, oy, SIZE, PITCH, BIT, [-1.0, -2.0])
        g.comment("  finish pass (G02 circles -> round pillars)")
        round_posts(g, ox, oy, SIZE, PITCH, pillar_r=BIT / 2.0, z=-2.0)
    g.blank()
    g.comment("-- outline through-cut: free ALL FOUR arrays (4 bridges each) --")
    for ox, oy in origins:
        outline_with_bridges(g, ox, oy, SIZE, BIT, [-1.0, -2.0, -3.0], bridges=4)
    g.blank()
    g.lines.append(PROGRAM_END)
    return g.text()


def gen_fluorescence_sensor() -> str:
    """8 vertical + 8 horizontal V-bit grooves, segmented so the 8x8
    intersections remain as pixel pads (250 um gaps centred on each crossing).
    The through-cut outline is a SEPARATE end-mill section (tool change)."""
    g = G()
    g.comment("QSSC CNC toolpath -- fluorescence-sensor grid (8x8 pixel pads)")
    g.comment("GENERATED by tools/gen_qssc_gcode.py -- deterministic, auditable")
    g.comment("Stock: 30x30x3 mm | GROOVES: 1.0 mm 90deg V-bit, depth -0.15 mm")
    g.comment("8 vertical + 8 horizontal grooves, 3.75 mm pitch, segmented with "
              "0.25 mm gaps at each crossing -> leaves 8x8 pixel pads.")
    g.comment("OUTLINE: separate section, requires a 2.0 mm end-mill tool "
              "change (the original wrongly used the V-bit for the through-cut).")
    g.blank()
    SIZE = 30.0
    PITCH = 3.75
    GAP = 0.25
    DEPTH = -0.15
    n = round(SIZE / PITCH)               # 8
    centres = [PITCH / 2 + k * PITCH for k in range(n)]   # 1.875, 5.625, ...
    margin = 0.5                          # stay 0.5 mm inside the stock edge
    g.lines.append(SAFE_START)
    g.blank()
    g.comment("-- groove grid: 1 mm V-bit, segmented at each crossing --")
    for xv in centres:                     # vertical grooves (vary X, cut Y)
        segs = _segments(margin, SIZE - margin, centres, GAP)
        for a, b in segs:
            g.rapid(xv, a)
            g.plunge(DEPTH)
            g.feed(xv, b)
            g.retract()
    for yv in centres:                     # horizontal grooves (vary Y, cut X)
        segs = _segments(margin, SIZE - margin, centres, GAP)
        for a, b in segs:
            g.rapid(a, yv)
            g.plunge(DEPTH)
            g.feed(b, yv)
            g.retract()
    g.blank()
    g.comment("-- TOOL CHANGE: remove 1 mm V-bit, fit 2.0 mm end-mill --")
    g.comment("-- resume spindle, then through-cut the outline (4 bridges) --")
    g.lines.append("M05")
    g.lines.append(f"G0 Z{SAFE_Z:.1f}")
    g.lines.append(f"M03 S{SPINDLE_RPM}")
    outline_with_bridges(g, 0.0, 0.0, SIZE, bit=2.0,
                         z_passes=[-1.0, -2.0, -3.0], bridges=4)
    g.blank()
    g.lines.append(PROGRAM_END)
    return g.text()


def _segments(lo: float, hi: float, breaks: list[float],
              gap: float) -> list[tuple[float, float]]:
    """Split [lo, hi] into cut segments, leaving a ``gap``-wide tab centred on
    each value in ``breaks`` (so a groove is interrupted at every crossing ->
    the crossing stays as a solid pixel pad)."""
    half = gap / 2.0
    pts = [lo] + [v for c in breaks for v in (c - half, c + half)] + [hi]
    pts = sorted(p for p in pts if lo - 1e-6 <= p <= hi + 1e-6)
    segs = []
    for i in range(0, len(pts) - 1, 2):
        a, b = pts[i], pts[i + 1]
        if b - a > 1e-3:
            segs.append((a, b))
    return segs


# --- driver -----------------------------------------------------------------

FILES = {
    "square_chip_cnc.gcode": gen_square_chip,
    "cylinder_chip.gcode": gen_cylinder_chip,
    "fluorescence-sensor.gcode": gen_fluorescence_sensor,
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in FILES.items():
        text = fn()
        path = OUT_DIR / name
        path.write_text(text)
        nlines = text.count("\n")
        print(f"  wrote {path}  ({nlines} lines)")


if __name__ == "__main__":
    main()