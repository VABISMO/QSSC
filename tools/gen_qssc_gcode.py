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

Run:  python tools/gen_qssc_gcode.py      (regenerates all files)
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
    """Through-cut a ``size`` SQUARE at ``+bit/2`` offset (so the bit frees the
    part) with ``bridges`` tabs of ``bridge_len`` so the part is retained.

    The bit centre travels at the part edge + half the bit, i.e. the cut
    removes material from the edge outward -- standard for freeing a part to
    nominal size. Tabs keep the part attached to the stock for safety.
    """
    outline_rect_with_bridges(g, x0, y0, size, size, bit, z_passes,
                              bridges, bridge_len)


def outline_rect_with_bridges(g: G, x0: float, y0: float, w: float, h: float,
                              bit: float, z_passes: list[float],
                              bridges: int = 4, bridge_len: float = 1.0) -> None:
    """Through-cut a ``w`` x ``h`` RECTANGLE at ``+bit/2`` offset with ``bridges``
    tabs per edge so the part is retained. Generalisation of
    ``outline_with_bridges`` to non-square parts (e.g. the 135x90 P0 carrier)."""
    off = bit / 2.0
    corners = [
        (x0 - off, y0 - off),
        (x0 + w + off, y0 - off),
        (x0 + w + off, y0 + h + off),
        (x0 - off, y0 + h + off),
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


# --- P0.2.0 helpers (DIRECT-matrix 8x8: 64 anode + 64 cathode electrodes) -------
# Optical die 20x20 mm, 8x8 pixels at 2.5 mm pitch. DIRECT matrix = every pixel
# its OWN isolated anode pad (top, ITO) + cathode pad (bottom, Ag) -> 128
# electrodes on the chip faces, hand-bonded to 128 holes on the carrier. Pad
# 1.5 mm, isolation channel 1.0 mm (= the 1 mm bit diameter).
P0_DIE = 20.0
P0_PITCH = 2.5
P0_PAD = 1.5            # anode pad size (set by 1mm isolation channels in the ITO)
P0_CATH_WIN = 1.0       # cathode stencil window -- MUST be < PITCH - BIT so adjacent
                        # windows keep a Kapton web (2.5 - 1.0 - 1.0 = 0.5 mm web). At
                        # 1.5 mm the kerf touches (zero web) and all 64 windows merge
                        # into one opening -> all cathodes short. 1.0 mm is the limit
                        # for a 1 mm bit at 2.5 mm pitch.
P0_BIT = 1.0
P0_N = 8
# 8 pad centres on each axis (1.25, 3.75, ... 18.75).
P0_PAD_CENTRES = [P0_PITCH / 2 + k * P0_PITCH for k in range(P0_N)]

# Carrier 135x90 FR4 breakout for the VERTICAL two-faced stack (master-mirror):
# ONE 20x20 die pocket centred on the carrier holds the 3-layer die
# (L3 graphene detector TOP / L2 TEOS+NV waveguide MIDDLE / L1 CQD emitter BOTTOM),
# + 128 bond holes (64 detector top edge Y=85 + 64 emitter-cathode bottom edge
# Y=5) + 1 common EM_ANODE hole, all at 2 mm pitch. Fits the 3018 bed (180x100).
P0_CARR_W = 135.0
P0_CARR_H = 90.0
P0_BOND_PITCH = 2.0

# --- SHARED coordinate system: every P0 layer is drawn in CARRIER coords so the
# files overlay in a gcode viewer. Work zero = carrier bottom-left (0,0). Each
# small piece (die wafer, Kapton) is fixtured on the bed at its carrier-relative
# position, OR a G55/G56 work offset is set to the piece's local origin. The 4
# corner fiducials below are emitted IDENTICALLY in every file -> registration.
# The die pocket is CENTRED on the carrier: (57.5,35)->(77.5,55) = centre (67.5,45).
# All three layer gcodes (ito/cathode/waveguide/detector) share this ONE footprint
# because the three layers are STACKED VERTICALLY on the same 20x20 die, not laid
# out side-by-side. Each layer is machined on its own substrate before stacking.
P0_DIE_X, P0_DIE_Y = 57.5, 35.0    # the single 20x20 die pocket, centred on carrier
P0_FIDUCIALS = [(1.0, 1.0),
                (P0_CARR_W - 1.0, 1.0),
                (P0_CARR_W - 1.0, P0_CARR_H - 1.0),
                (1.0, P0_CARR_H - 1.0)]


def raster_pocket(g: G, x0: float, y0: float, w: float, h: float, bit: float,
                  z_passes: list[float], stepover: float | None = None,
                  feed: int = CUT_FEED) -> None:
    """Clear a ``w`` x ``h`` pocket by zig-zag raster with ``bit`` diameter and
    ``stepover`` (default 0.6*bit -> 40% overlap, clean floor). Unlike
    ``crosshatch_pocket`` this leaves a FLAT floor (no posts) -- use it for
    seating a glass die flush in a carrier.
    """
    if stepover is None:
        stepover = bit * 0.6
    ys: list[float] = []
    n = 0
    while y0 + n * stepover < y0 + h - 1e-6:
        ys.append(y0 + n * stepover)
        n += 1
    ys.append(y0 + h)  # closing pass at the far edge
    for z in z_passes:
        for i, y in enumerate(ys):
            if i % 2 == 0:
                g.rapid(x0, y)
                g.plunge(z)
                g.feed(x0 + w, y, feed=feed)
            else:
                g.rapid(x0 + w, y)
                g.plunge(z)
                g.feed(x0, y, feed=feed)
            g.retract()


def peck_drill(g: G, x: float, y: float, z_target: float, peck: float = 0.5,
               feed: int = PLUNGE_FEED) -> None:
    """Peck-drill a single hole at (x, y) down to ``z_target`` (negative).
    Emits explicit plunge/retract cycles (no G83 canned cycle) so it runs on a
    plain GRBL 3018 controller. Retracts to SAFE_Z between pecks to clear chips.
    """
    g.rapid(x, y, z=SAFE_Z)
    z = 0.0
    while z > z_target + 1e-6:
        z = max(z - peck, z_target)
        g.plunge(z, feed=feed)
        g.retract()


def rect_groove(g: G, x0: float, y0: float, w: float, h: float, bit: float,
                z: float, feed: int = CUT_FEED) -> None:
    """Mill a closed rectangular channel ``bit`` wide around the perimeter of the
    (x0,y0,w,h) rectangle (bit centred on the edge). Corners carry the bit
    radius (0.5 mm for a 1 mm bit). Single Z pass. Used to seat a copper-wire
    microwave loop around the microdiamond pocket.
    """
    corners = [
        (x0, y0, x0 + w, y0),
        (x0 + w, y0, x0 + w, y0 + h),
        (x0 + w, y0 + h, x0, y0 + h),
        (x0, y0 + h, x0, y0),
    ]
    for sx, sy, ex, ey in corners:
        g.rapid(sx, sy)
        g.plunge(z)
        g.feed(ex, ey, feed=feed)
        g.retract()


# --- readability helpers (bounding box, fiducials, labels) -------------------
# Every P0 toolpath gets a light bounding-box engrave + corner fiducials so the
# layer is legible in a gcode viewer and registers to the other layers / carrier.

ENGRAVE_Z = -0.10      # shallow engrave for bounding boxes / labels (visual only)
FIDUCIAL_Z = -0.15     # registration dimple


def engrave_rect(g: G, x0: float, y0: float, w: float, h: float,
                 z: float = ENGRAVE_Z, feed: int = 100) -> None:
    """Lightly engrave a rectangle outline at shallow z (a visible frame, not a
    through cut). Used for stock bounding boxes and pocket call-outs."""
    g.rapid(x0, y0)
    g.plunge(z, feed=feed)
    g.feed(x0 + w, y0, feed=feed)
    g.feed(x0 + w, y0 + h, feed=feed)
    g.feed(x0, y0 + h, feed=feed)
    g.feed(x0, y0, feed=feed)
    g.retract()


def fiducial(g: G, x: float, y: float, z: float = FIDUCIAL_Z) -> None:
    """Peck a registration dimple at (x, y) so layers align visually."""
    g.rapid(x, y, z=SAFE_Z)
    g.plunge(z, feed=50)
    g.retract()


def corner_fiducials(g: G, x0: float, y0: float, w: float, h: float,
                     inset: float = 1.0) -> None:
    """Four registration dimples inset from the corners of a (x0,y0,w,h) box."""
    for cx, cy in [(x0 + inset, y0 + inset),
                   (x0 + w - inset, y0 + inset),
                   (x0 + w - inset, y0 + h - inset),
                   (x0 + inset, y0 + h - inset)]:
        fiducial(g, cx, cy)


def gen_p0_cathode_stencil() -> str:
    """P0 L1 emitter BOTTOM face -- Ag cathode stencil: 64 cathode pads.

    The L1 (emitter, BOTTOM) face of the vertical-stack die. 64 window openings
    (1.0x1.0 mm square outlines through 50 um Kapton, Z-0.06) in an 8x8 grid at
    the same 2.5 mm pitch as the ITO anodes. The window MUST be 1.0 mm (not 1.5)
    with a 1 mm bit: opened area = window + bit = 2.0 mm < 2.5 mm pitch ->
    0.5 mm Kapton web between windows, so the 64 openings stay ISOLATED. (At 1.5
    mm the kerf touches the neighbour -> all windows merge -> Ag shorts.)
    Doctor-blade Ag paste through the 64 windows onto the ZnO -> 64 isolated
    cathode pads, one per pixel, mirror of the anode grid. The 1.0 mm cathode
    pad is smaller than the 1.5 mm anode but centred on the same pixel, so it
    contacts the ZnO core fine. Drawn in CARRIER coords at the single die pocket
    (57.5,35)->(77.5,55) -- same footprint as the ITO isolation, waveguide and
    detector. Shared carrier-corner fiducials.
    """
    g = G()
    g.comment("QSSC P0 -- L1 EMITTER BOTTOM face: Ag CATHODE stencil (64 windows)")
    g.comment("GENERATED by tools/gen_qssc_gcode.py -- deterministic, auditable")
    g.comment("Stock: ~22x22 mm Kapton 50 um on backing | tool: 1.0 mm flat end-mill")
    g.comment(f"spindle {SPINDLE_RPM} rpm | cut feed 150 mm/min | depth -0.06 mm")
    g.comment("VERTICAL STACK: this is the L1 emitter BOTTOM face (same footprint as ITO).")
    g.comment("COORDS: carrier system. Die pocket (57.5,35)->(77.5,55). WORK ZERO = carrier (0,0).")
    g.comment("Align Kapton over the L1 wafer (shared fiducials). 8x8 windows, pitch 2.5 mm,")
    g.comment("window 1.0x1.0 mm (1mm-bit web limit) -> 0.5 mm web -> 64 ISOLATED Ag pads")
    g.blank()
    g.lines.append(SAFE_START)
    g.blank()
    g.comment("-- shared carrier-corner fiducials (identical in every P0 file) --")
    corner_fiducials(g, 0.0, 0.0, P0_CARR_W, P0_CARR_H)
    g.comment("-- die footprint (engrave, aligns to the ITO anode grid) --")
    engrave_rect(g, P0_DIE_X, P0_DIE_Y, P0_DIE, P0_DIE)
    g.blank()
    g.comment("-- 64 cathode window openings (8x8 square outlines through Kapton) --")
    for cx in P0_PAD_CENTRES:
        for cy in P0_PAD_CENTRES:
            gx, gy = P0_DIE_X + cx, P0_DIE_Y + cy
            g.comment(f"   cathode pad carrier ({gx:.2f},{gy:.2f})")
            engrave_rect(g, gx - P0_CATH_WIN / 2, gy - P0_CATH_WIN / 2,
                         P0_CATH_WIN, P0_CATH_WIN, z=-0.06, feed=150)
    g.blank()
    g.lines.append(PROGRAM_END)
    return g.text()


def gen_p0_waveguide_channel() -> str:
    """P0 L2 MIDDLE layer -- TEOS sol-gel ENTANGLEMENT BUS, 64 NV sites in series.

    The MIDDLE layer of the vertical-stack die: a 20x20x1 mm glass wafer (L2,
    seated between L1 emitter below and L3 detector above). A 1 mm SERPENTINE
    channel is milled on its TOP face as ONE continuous bus that threads ALL 64
    NV microdiamond sites (8x8 at 2.5 mm pitch -- one NV site per pixel, directly
    under each L3 detector pad and directly over each L1 cathode). The bus snakes
    boustrophedon: 8 horizontal runs (one per pixel row) joined by 7 vertical
    connectors at alternating ends -> a single continuous core. Filled with
    Ti-doped TEOS sol-gel (n~1.55 > glass 1.52 -> TIR guided) embedding the NV
    microdiamonds: the 532 nm pump enters the LEFT facet at the bus start,
    propagates the whole serpentine and pumps every NV site in sequence, and the
    NV red PL (637-800 nm) leaves each site VERTICALLY up to the L3 detector pad
    above. Because all 64 NV sites share the ONE serial bus, a photon emitted by
    any site propagates along the same core to any other -> the on-chip
    entanglement path (indistinguishable photons from two NV sites interfere at
    the bus output, heralding spin-spin entanglement). A single Cu MW loop
    (2.87 GHz ODMR) seats in a perimeter groove around the whole die, bathing all
    64 sites in one microwave field. Drawn in CARRIER coords at the single die
    pocket (57.5,35)->(77.5,55) -- SAME footprint as ITO/cathode/detector. Shared
    carrier-corner fiducials. No outline (wafer is snapped/clamped, not milled).
    """
    g = G()
    g.comment("QSSC P0 -- L2 MIDDLE: TEOS ENTANGLEMENT BUS (64 NV sites, serial serpentine)")
    g.comment("GENERATED by tools/gen_qssc_gcode.py -- deterministic, auditable")
    g.comment("Stock: 20x20x1 mm glass wafer (the L2 layer) | tool: 1.0 mm flat end-mill")
    g.comment(f"spindle {SPINDLE_RPM} rpm | channel feed 80 mm/min | channel depth -0.5 mm")
    g.comment("VERTICAL STACK: L1 emitter BELOW this wafer / L3 detector ABOVE it.")
    g.comment("ENTANGLEMENT BUS: 8 horizontal runs + 7 vertical connectors = ONE continuous")
    g.comment("serpentine core threading all 64 NV sites (8x8, 2.5 mm pitch). 532 nm pump in")
    g.comment("at the LEFT facet (bus start); NV PL exits each site UP to L3. Serial bus ->")
    g.comment("all sites share one waveguide -> photons from different sites interfere.")
    g.comment("COORDS: carrier system. Die pocket (57.5,35)->(77.5,55), centred on carrier.")
    g.comment("WORK ZERO = carrier (0,0). Fixture the 20x20 L2 wafer with BL at (57.5,35),")
    g.comment("or set G56 X57.5 Y35 and run in wafer-local coords. Shared fiducials.")
    g.comment("Fill the whole bus with Ti-doped TEOS sol-gel + NV microdiamonds; densify 300 C.")
    g.blank()
    g.lines.append(SAFE_START)
    g.blank()
    g.comment("-- shared carrier-corner fiducials (identical in every P0 file) --")
    corner_fiducials(g, 0.0, 0.0, P0_CARR_W, P0_CARR_H)
    g.comment("-- L2 wafer footprint (engrave, the 20x20 die pocket) --")
    engrave_rect(g, P0_DIE_X, P0_DIE_Y, P0_DIE, P0_DIE)
    g.blank()

    x_lo = P0_DIE_X + 0.5                 # 58.0 (left facet, 532 pump input)
    x_hi = P0_DIE_X + P0_DIE - 0.5        # 77.0 (right facet)
    row_y = [P0_DIE_Y + yc for yc in P0_PAD_CENTRES]   # 36.25 .. 53.75 (8 pixel rows)
    g.comment("-- 1. SERPENTINE ENTANGLEMENT BUS: one continuous core, 8 rows + 7 connectors --")
    g.comment(f"   532 pump in at LEFT facet ({x_lo:.1f},{row_y[0]:.2f}); bus ends ({x_lo:.1f},{row_y[-1]:.2f})")
    for z in (-0.25, -0.5):
        g.rapid(x_lo, row_y[0])
        g.plunge(z, feed=50)
        for r in range(P0_N):
            yr = row_y[r]
            if r % 2 == 0:                      # even row: left -> right
                g.feed(x_hi, yr, feed=80)
                cur_x = x_hi
            else:                               # odd row: right -> left
                g.feed(x_lo, yr, feed=80)
                cur_x = x_lo
            if r < P0_N - 1:                    # vertical connector to next row
                g.feed(cur_x, row_y[r + 1], feed=80)
        g.retract()
    g.blank()

    g.comment("-- 2. 64 NV microdiamond sites (8x8, one per pixel, marked in the bus) --")
    for cx in P0_PAD_CENTRES:
        for cy in P0_PAD_CENTRES:
            gx, gy = P0_DIE_X + cx, P0_DIE_Y + cy
            g.comment(f"   NV site carrier ({gx:.2f},{gy:.2f})")
            fiducial(g, gx, gy, z=-0.20)
    g.blank()

    g.comment("-- 3. MW loop seating groove: perimeter around the whole die (Cu wire, 2.87 GHz) --")
    g.comment("   one loop baths ALL 64 NV sites in the same microwave field (ODMR)")
    rect_groove(g, P0_DIE_X + 1.0, P0_DIE_Y + 1.0,
                P0_DIE - 2.0, P0_DIE - 2.0, P0_BIT, z=-0.30)
    g.blank()

    g.comment("-- 4. facet markers: 532 pump in (top-left) + bus dump (bottom-left) --")
    fiducial(g, x_lo, row_y[0], z=-0.20)
    fiducial(g, x_lo, row_y[-1], z=-0.20)
    g.blank()
    g.lines.append(PROGRAM_END)
    return g.text()


def gen_p0_detector_stencil() -> str:
    """P0 L3 detector TOP face -- graphene/MoS2 8x8 detector contact stencil.

    The TOP face of the vertical-stack die = the graphene/MoS2 photodetector
    (SOLE detector). 64 Au/Cr detector-pad openings (1.0x1.0 mm
    square outlines through 50 um Kapton, Z-0.06) in an 8x8 grid at the same 2.5
    mm pitch as the emitter grids below. The CVD graphene + MoS2 photogating film
    is deposited over the whole 20x20 area first; these 64 windows then stencil
    the Au/Cr readout pads -- one per detector pixel, each reading the NV red PL
    (637-800 nm) that arrives VERTICALLY from the L2 diamonds beneath. A shared
    graphene back-electrode (common) means 64 contacts + 1 common -> matches the
    master "64 DET top". The 1.0 mm window + 1 mm bit leaves a 0.5 mm Kapton web
    (same limit as the cathode stencil). Drawn in CARRIER coords at the single
    die pocket (57.5,35)->(77.5,55) -- SAME footprint as ITO/cathode/waveguide.
    Shared carrier-corner fiducials.
    """
    g = G()
    g.comment("QSSC P0 -- L3 DETECTOR TOP face: graphene/MoS2 8x8 contact stencil (64 pads)")
    g.comment("GENERATED by tools/gen_qssc_gcode.py -- deterministic, auditable")
    g.comment("Stock: ~22x22 mm Kapton 50 um on backing | tool: 1.0 mm flat end-mill")
    g.comment(f"spindle {SPINDLE_RPM} rpm | cut feed 150 mm/min | depth -0.06 mm")
    g.comment("VERTICAL STACK: this is the L3 detector TOP face (graphene/MoS2, sole detector).")
    g.comment("COORDS: carrier system. Die pocket (57.5,35)->(77.5,55). WORK ZERO = carrier (0,0).")
    g.comment("Align Kapton over the L3 wafer (shared fiducials). 8x8 windows, pitch 2.5 mm,")
    g.comment("window 1.0x1.0 mm -> 0.5 mm web -> 64 ISOLATED Au/Cr detector readout pads")
    g.comment("Graphene + MoS2 deposited over the area first; reads NV PL coming UP from L2.")
    g.blank()
    g.lines.append(SAFE_START)
    g.blank()
    g.comment("-- shared carrier-corner fiducials (identical in every P0 file) --")
    corner_fiducials(g, 0.0, 0.0, P0_CARR_W, P0_CARR_H)
    g.comment("-- die footprint (engrave, aligns to the L1/L2 grids below) --")
    engrave_rect(g, P0_DIE_X, P0_DIE_Y, P0_DIE, P0_DIE)
    g.blank()
    g.comment("-- 64 detector contact-pad openings (8x8 square outlines through Kapton) --")
    for cx in P0_PAD_CENTRES:
        for cy in P0_PAD_CENTRES:
            gx, gy = P0_DIE_X + cx, P0_DIE_Y + cy
            g.comment(f"   detector pad carrier ({gx:.2f},{gy:.2f})")
            engrave_rect(g, gx - P0_CATH_WIN / 2, gy - P0_CATH_WIN / 2,
                         P0_CATH_WIN, P0_CATH_WIN, z=-0.06, feed=150)
    g.blank()
    g.lines.append(PROGRAM_END)
    return g.text()


def gen_p0_carrier() -> str:
    """P0 carrier -- 135x90 FR4 breakout for the VERTICAL two-faced stack die.

    Stock 135x90x1.6 mm FR4 (fits the 3018 180x100 bed). The die is a single
    20x20 mm VERTICAL stack (L3 graphene detector TOP / L2 TEOS+NV waveguide
    MIDDLE / L1 CQD emitter BOTTOM), centred on the carrier:
      1. ONE die pocket 20x20 x 1.1 mm at (57.5,35)->(77.5,55): seats the
         two-faced stack die. Its LEFT facet (X=57.5) is the 532 nm pump input
         (left-edge of the carrier is notched/clear so the pump beam reaches the
         die facet). The MW loop (2.87 GHz ODMR) is hand-bent Cu wire taped over
         the die -- no CNC groove on the carrier.
      2. 130 bond holes Ø1 mm (peck-drilled through FR4) at 2 mm pitch
         (X = 3.5, 5.5, ..., 131.5):
           - 64 DETECTOR holes, TOP edge Y=85 -> L3 Au/Cr top-face signal pads
             (bond AFTER seating the die).
           - 64 EMITTER-CATHODE holes, BOTTOM edge Y=5 -> L1 Ag cathode contacts
             (bottom face, inaccessible after seating -> bond BEFORE seating).
           - 1 COMMON EM_ANODE hole at (3.5, 8.0) -> the shared L1 ITO anode.
           - 1 COMMON DET_COMMON hole at (3.5, 82.0) -> the shared L3 ITO
             detector back-electrode (the detector masa; mirror of EM_ANODE).
         Solder 2 mm male pin headers here and plug the carrier into 2 mm female
         socket receptacles (Samtec TMM-1xx / Harwin M20-782, 64 pos/row) on a
         breakout motherboard, OR hand-bond wires. Fan-out from the 20 mm die
         edges to the 128 mm carrier edges is hand-wired (magnet wire) on the
         carrier -- the CNC only drills the edge pin holes + pockets the die.
      3. Outline through-cut 135x90 with 4 bridges.
    Bounding box + corner fiducials + per-feature label comments so the carrier
    is legible in a gcode viewer.
    """
    g = G()
    g.comment("QSSC P0 -- FR4 CARRIER 135x90 (VERTICAL two-faced stack breakout)")
    g.comment("GENERATED by tools/gen_qssc_gcode.py -- deterministic, auditable")
    g.comment(f"Stock: {P0_CARR_W:.0f}x{P0_CARR_H:.0f}x1.6 mm FR4 | tool: 1.0 mm flat end-mill")
    g.comment(f"spindle {SPINDLE_RPM} rpm | pocket Z-step 0.2 | outline through Z-3.0")
    g.comment("VERTICAL STACK DIE 20x20 @ (57.5,35): L3 detector TOP / L2 TEOS+NV MID / L1 emitter BOTTOM")
    g.comment("532 nm pump -> LEFT facet X=57.5 | NV PL -> UP to L3 detector (guided)")
    g.comment("130 bond holes: 64 DETECTOR top Y=85 + 64 EMITTER-CATHODE bottom Y=5 + 1 EM_ANODE (3.5,8.0) + 1 DET_COMMON (3.5,82.0)")
    g.blank()
    g.lines.append(SAFE_START)
    g.blank()
    g.comment("-- stock bounding box (engrave, visual frame) --")
    engrave_rect(g, 0.0, 0.0, P0_CARR_W, P0_CARR_H)
    g.comment("-- shared carrier-corner fiducials (identical in every P0 file) --")
    corner_fiducials(g, 0.0, 0.0, P0_CARR_W, P0_CARR_H)

    g.blank()
    g.comment(f"-- 1. [DIE POCKET] 20x20 x 1.1 mm @ ({P0_DIE_X},{P0_DIE_Y}) -- the vertical stack die --")
    g.comment("   L3 graphene detector TOP / L2 TEOS+NV waveguide MID / L1 CQD emitter BOTTOM")
    g.comment("   LEFT facet X=57.5 = 532 nm PUMP input | NV PL exits UP to the L3 detector")
    engrave_rect(g, P0_DIE_X, P0_DIE_Y, P0_DIE, P0_DIE)  # call-out frame
    raster_pocket(g, P0_DIE_X, P0_DIE_Y, P0_DIE, P0_DIE, P0_BIT,
                  z_passes=[-0.2, -0.4, -0.6, -0.8, -1.0, -1.1])
    g.comment("   532 pump facet marker (left edge of the die pocket)")
    fiducial(g, P0_DIE_X, P0_DIE_Y + P0_DIE / 2.0, z=-0.20)

    g.blank()
    bond_x = [3.5 + k * P0_BOND_PITCH for k in range(64)]
    g.comment("-- 2a. [EMITTER-CATHODE BONDS] 64 Ø1 holes, BOTTOM edge Y=5 (L1 Ag cathodes) --")
    g.comment("   bottom face -> bond these BEFORE seating the die (face is inaccessible after)")
    for x in bond_x:
        peck_drill(g, x=x, y=5.0, z_target=-2.0, peck=0.5)
    g.blank()
    g.comment("-- 2b. [COMMON EM_ANODE] 1 Ø1 hole @ (3.5, 8.0) -- shared L1 ITO anode (bond BEFORE seating) --")
    peck_drill(g, x=3.5, y=8.0, z_target=-2.0, peck=0.5)
    g.blank()
    g.comment("-- 2c. [COMMON DET_COMMON] 1 Ø1 hole @ (3.5, 82.0) -- shared L3 ITO detector back-electrode (the detector masa; bond AFTER seating) --")
    peck_drill(g, x=3.5, y=82.0, z_target=-2.0, peck=0.5)
    g.blank()
    g.comment("-- 2d. [DETECTOR BONDS] 64 Ø1 holes, TOP edge Y=85 (L3 Au/Cr signal pads) --")
    g.comment("   top face -> bond these AFTER seating the die")
    for x in bond_x:
        peck_drill(g, x=x, y=P0_CARR_H - 5.0, z_target=-2.0, peck=0.5)

    g.blank()
    g.comment("-- 3. [OUTLINE] through-cut 135x90 (4 bridges) --")
    outline_rect_with_bridges(g, 0.0, 0.0, P0_CARR_W, P0_CARR_H, bit=P0_BIT,
                              z_passes=[-1.0, -2.0, -3.0], bridges=4)

    g.blank()
    g.lines.append(PROGRAM_END)
    return g.text()


# --- P1.0 pin-scaffold (commercial pin-header electrode array) ----------------
# P1 replaces the P0 hand-bonded magnet-wire fan-out with a COMMERCIAL pin-header
# scaffold: standard 2.54 mm pitch male pin headers (0.64 mm square, tin-plated,
# cut to length) seat in a CNC'd support so each pin is one pixel's electrode AND
# its own plug-in fan-out (solders straight into a 2.54 mm female socket -> no
# wire-bonding, no pogo clamshell, no magnet-wire fan-out). The 1 mm bit drills
# 1.0 mm holes -> ~0.18 mm radial clearance for the paste annulus (detector) or a
# free sliding fit (emitter cathode contacts). 8x8 = 64 pins (user-confirmed 8x8,
# supersedes the 48-pin draft) matches the 64-pixel grid. Support 20x20 overlays
# the P0 die / ITO glass footprint. Pitch is 2.54 mm (not the P0 2.5 mm) because
# the pin header's molded strip fixes the pitch -- a standard cut header only
# drops in at 2.54.
P1_SUPPORT = 20.0
P1_PIN_PITCH = 2.54          # standard PCB pin-header pitch (molded strip, fixed)
P1_PIN_N = 8                 # 8x8 = 64 pins (matches the 64-pixel grid)
P1_PIN_BIT = 1.0             # 1 mm flat end-mill
P1_HOLE_R = 0.75            # 1.5 mm hole dia -- > 1 mm bit so circle_bore mills a
                            #  VISIBLE polygon circle (a Z-only peck drill at 1.0 mm =
                            #  bit dia shows as a point in a top view, not a hole).
                            #  0.64 mm pin in 1.5 mm hole -> 0.43 mm radial clearance.
P1_STOCK_Z = 3.0             # 3 mm support: pin length + clearance, through-bored
# 8x8 grid centred on the 20x20 support: span (8-1)*2.54 = 17.78, start 1.11 mm.
P1_PIN_START = (P1_SUPPORT - (P1_PIN_N - 1) * P1_PIN_PITCH) / 2.0   # 1.11 mm
P1_PIN_CENTRES = [P1_PIN_START + k * P1_PIN_PITCH for k in range(P1_PIN_N)]

# --- P1 holes + mold: a 2x4 grid of the pin-array footprint, NO through-cut ---
# The user asked for 2 rows of 4 pieces of EACH type (holes + mold), no through-
# cut ("los rebordes no los quiero"): a single 2-row x 4-col grid -- row 1 =
# 4 HOLES pieces (64 holes each, blind 2 mm), row 2 = 4 MOLD pieces (17.78x17.78
# 1 mm pocket). 3 mm gap between copies; each holes array is FLUSH at the copy
# origin (first pin at the corner) so it aligns with the mold cell below/above.
P1_HOLES_Z = 2.0            # hole depth -- BLIND (NOT through): 2 mm of 3 mm stock
P1_MOLD_Z = 1.0             # mold pocket depth -- 1 mm
P1_MOLD_STEP = 0.5          # mold pocket stepover (~50% of the 1 mm bit)
P1_ARRAY_SPAN = (P1_PIN_N - 1) * P1_PIN_PITCH    # 17.78 mm -- the pin-array footprint
P1_COPY_GAP = 3.0           # gap between copies in the grid (mm)
P1_COPY_PITCH = P1_ARRAY_SPAN + P1_COPY_GAP      # 20.78 mm -- copy-to-copy pitch
P1_GRID_COLS = 4           # 4 pieces per row
P1_GRID_ROWS = 3          # 3 rows: row 0 = holes, row 1 = molds, row 2 = waveguides
P1_WG_Z = 1.0             # waveguide channel depth (BLIND, 1 mm) -- L2 bus, TEOS+microdiamond fill


def circle_bore(g: G, cx: float, cy: float, hole_r: float, bit_r: float,
                z_target: float, z_step: float = 0.5, n_seg: int = 16,
                feed: int = PLUNGE_FEED) -> None:
    """Bore a hole of radius ``hole_r`` with a ``bit_r`` end-mill by LINEAR-SEGMENT
    polygon circles (G1 XY moves), NOT G02 arcs.

    Why linear segments, not G02: some gcode viewers misrender the G02 I/J
    arc-centre offset as a diagonal chord toward the origin, which made the 8x8
    grid look "diagonal" even though the hole centres are a perfect axis-aligned
    grid. A polygon circle (16 short G1 XY segments) renders as a clean closed
    circle in EVERY viewer, so the grid reads as straight aligned rows. A single
    on-grid centre pierce marks the hole for the SVG parser (which filters
    plunges to the 2.54 mm pin grid); then the polygon steps down in Z. Emits
    explicit plunge cycles (no canned G83) for a plain GRBL 3018.
    """
    path_r = hole_r - bit_r               # 0.25 mm (1.5 mm hole - 1 mm bit)
    # 1. centre pierce -- the plunge here is at (cx, cy), ON the pin grid, so the
    #    SVG parser (_hole_centers) locates the hole. Steps down to full depth.
    g.rapid(cx, cy, z=SAFE_Z)
    z = 0.0
    while z > z_target + 1e-6:
        z = max(z - z_step, z_target)
        g.plunge(z, feed=feed)
    g.retract()
    # 2. linear-segment polygon circle stepping down Z -- visible in any viewer,
    #    no G02 I/J to misrender as a diagonal. Start at (cx + path_r, cy).
    g.rapid(cx + path_r, cy, z=SAFE_Z)
    z = 0.0
    while z > z_target + 1e-6:
        z = max(z - z_step, z_target)
        g.plunge(z, feed=feed)
        for k in range(n_seg + 1):
            a = 2.0 * math.pi * k / n_seg
            g.feed(cx + path_r * math.cos(a), cy + path_r * math.sin(a), feed=CUT_FEED)
        g.retract()


def rect_pocket(g: G, x0: float, y0: float, w: float, h: float, bit: float,
                z_target: float, stepover: float, z_step: float = 0.5,
                feed: int = CUT_FEED) -> None:
    """Pocket a ``w`` x ``h`` square (corner ``x0,y0``) down to ``z_target`` (BLIND).

    Two passes, both stepped down in Z:

      1. PERIMETER outline ("cuadrado de fuera") -- trace the square at each
         Z level so the mold has a crisp square wall. The bit center stays
         ``bit/2`` inside the boundary so the cut edge reaches the square.
      2. INTERIOR zigzag raster ("vaciado de por dentro") -- clear the inside at
         each Z level with a boustrophedon (snake) zigzag, step-over ``stepover``.

    No through-cut: the bit stops at ``z_target`` (leaves a floor). Used for the
    P1 mold (a 1 mm-deep 17.78 x 17.78 pocket the pin array drops into).
    """
    r = bit / 2.0
    xa, xb = x0 + r, x0 + w - r
    ya, yb = y0 + r, y0 + h - r
    # 1. perimeter outline -- step down in Z, trace the square each level
    g.rapid(xa, ya, z=SAFE_Z)
    z = 0.0
    while z > z_target + 1e-6:
        z = max(z - z_step, z_target)
        g.plunge(z, feed=PLUNGE_FEED)
        g.feed(xb, ya, feed=feed)
        g.feed(xb, yb, feed=feed)
        g.feed(xa, yb, feed=feed)
        g.feed(xa, ya, feed=feed)
        g.retract()
    # 2. interior zigzag pocket -- clear the inside at each Z level
    n = max(1, int(round((yb - ya) / stepover)))
    ys = [ya + i * (yb - ya) / n for i in range(n + 1)]
    g.rapid(xa, ya, z=SAFE_Z)
    z = 0.0
    while z > z_target + 1e-6:
        z = max(z - z_step, z_target)
        g.plunge(z, feed=PLUNGE_FEED)
        for i, yy in enumerate(ys):
            x_from = xa if (i % 2 == 0) else xb
            x_to = xb if (i % 2 == 0) else xa
            if i > 0:
                g.feed(x_from, yy, feed=feed)    # Y step-over (no lift between rows)
            g.feed(x_to, yy, feed=feed)          # cut across the pocket
        g.retract()
        g.rapid(xa, ya, z=SAFE_Z)


def serpentine_channel(g: G, ox: float, oy: float, n: int, pitch: float,
                       bit: float, z_target: float, z_step: float = 0.5,
                       feed: int = CUT_FEED) -> None:
    """Mill a bit-width serpentine channel through the ``n`` x ``n`` grid of
    points (``pitch`` mm, flush at origin ``ox,oy``), stepped down to ``z_target``
    (BLIND). The L2 entanglement-bus / waveguide template.

    The path is a boustrophedon (snake): row 0 left->right through all n points,
    drop to row 1 at the right end, row 1 right->left, drop to row 2 at the left
    end, ... so ONE continuous channel passes through all n*n grid points (the
    532 nm pump goes in at the bottom-left facet, the bus start). Vertical
    connectors at ALTERNATING ends link the rows, so the polyline never crosses
    itself. The channel is ``bit``-diameter wide (1 mm) and ``z_target`` deep
    (1 mm) -- later filled with TEOS + microdiamonds as the L2 entanglement bus.
    No through-cut (the bit stops at ``z_target``).
    """
    # build the waypoint polyline (the serpentine through all n*n grid points)
    pts: list[tuple[float, float]] = []
    for row in range(n):
        y = oy + row * pitch
        xs = [ox + i * pitch for i in range(n)]
        if row % 2 == 1:
            xs.reverse()
        if row == 0:
            pts.append((xs[0], y))           # bus start (bottom-left facet, pump in)
        for x in xs[1:]:
            pts.append((x, y))               # along the row
        if row < n - 1:
            pts.append((xs[-1], y + pitch))  # vertical connector to next row
    # step down in Z; at each level plunge at the start and trace the full polyline
    g.rapid(pts[0][0], pts[0][1], z=SAFE_Z)
    z = 0.0
    while z > z_target + 1e-6:
        z = max(z - z_step, z_target)
        g.plunge(z, feed=PLUNGE_FEED)
        for x, y in pts[1:]:
            g.feed(x, y, feed=feed)
        g.retract()
        g.rapid(pts[0][0], pts[0][1], z=SAFE_Z)


def gen_p1_pin_array_support() -> str:
    """P1 pin-scaffold support -- 8x8 circle-bored through-hole grid for PCB pins.

    The P1 fabrication uses COMMERCIAL pin-header pins as the per-pixel electrode
    AND the plug-in fan-out (no wire-bonding, no pogo clamshell, no magnet-wire
    fan-out). This toolpath circle-bores the 64 support holes one standard 2.54 mm
    male pin-header pin fits in, on a 20x20x3 mm FR4 (or clear acrylic) support
    that overlays the 20x20 die / ITO glass footprint.

      * 8x8 = 64 holes at 2.54 mm pitch (standard pin-header pitch; the molded
        strip fixes the pitch, so the grid MUST be 2.54, not the P0 2.5 mm).
        Centred on the 20x20 support (first pin at 1.11 mm).
      * 1.0 mm flat end-mill CIRCLE-BORES 1.5 mm through-holes (3 mm stock) by
        LINEAR-SEGMENT polygon circles (G1 XY moves, NOT G02 arcs -- some viewers
        misdraw the G02 I/J arc-centre offset as a diagonal chord, which made the
        grid look diagonal even though the hole centres are a square grid). The
        hole is bigger than the bit (1.5 > 1.0) so each hole is a VISIBLE circle
        in a gcode top view (a Z-only peck drill at 1.0 mm = bit diameter would
        show as a point, not a hole). A standard 0.64 mm square pin-header pin
        leaves ~0.43 mm radial clearance -- the paste annulus for both devices.
      * 4 corner fiducials + 20x20 outline with 4 bridges.

    DETECTOR use (pin-molded paste): fill each hole with graphene paste, insert
    the pin, let dry -> graphene skin on the pin; repeat with MoS2+PEDOT:PSS
    paste -> a coaxial pin/graphene/MoS2 pixel; the ITO glass under the support
    is the common back-electrode (DET_COMMON), the paste column between pin tip
    and ITO is the active photogating layer.
    EMITTER use (pin-molded CQD paste, symmetric to the detector): mold the CQD-LED
    stack around each pin in the hole, layer by layer -- ZnO paste (inner, ETL),
    dry, CQD paste (EML), dry, PEDOT:PSS paste (outer, HIL), dry -- pin = cathode,
    ITO glass under the support = EM_ANODE anode. Same pin-molding process as the
    detector; the paste interfaces interdiffuse (not as sharp as a spin-coated
    stack) so it is a weaker/rougher LED, but it emits and is symmetric. Each pin
    addressable from the back via a 2.54 mm female socket. No silver stencil, no
    magnet-wire fan-out, no spin-coater.
    """
    g = G()
    g.comment("QSSC P1 -- PIN-SCAFFOLD SUPPORT: 8x8 circle-bored hole grid for PCB pin headers")
    g.comment("GENERATED by tools/gen_qssc_gcode.py -- deterministic, auditable")
    g.comment(f"Stock: {P1_SUPPORT:.0f}x{P1_SUPPORT:.0f}x{P1_STOCK_Z:.0f} mm FR4 (or clear acrylic) | tool: 1.0 mm flat end-mill")
    g.comment(f"spindle {SPINDLE_RPM} rpm | bore feed 100 mm/min | through Z-{P1_STOCK_Z:.1f}")
    g.comment("PINS: standard 2.54 mm pitch male pin header, 0.64 mm square, tin-plated")
    g.comment("  (Mill-Max 1129 / Harwin M50 machined round gold pins = the upgrade for uniform coating).")
    g.comment(f"8x8 = 64 holes @ 2.54 mm pitch, centred (first pin {P1_PIN_START:.2f} mm), {P1_HOLE_R*2:.1f} mm dia.")
    g.comment(f"1 mm bit CIRCLE-BORES {P1_HOLE_R*2:.1f} mm holes (linear-segment polygon circles, visible in a top view) -> 0.43 mm radial clearance (0.64 mm pin).")
    g.comment("NOT a Z-only peck drill (invisible as a point) and NOT G02 arcs (some viewers misdraw I/J as a diagonal) -- a real polygon circle per hole.")
    g.comment("DETECTOR: paste-mold graphene then MoS2+PEDOT:PSS around each pin in the hole;")
    g.comment("  ITO glass under the support = common back-electrode (DET_COMMON).")
    g.comment("EMITTER: paste-mold ZnO then CQD then PEDOT:PSS around each pin -- symmetric")
    g.comment("  to the detector; pin = cathode, ITO glass under the support = EM_ANODE anode.")
    g.comment("COORDS: support-local. WORK ZERO = support bottom-left (0,0). 20x20 overlays the P0 die.")
    g.blank()
    g.lines.append(SAFE_START)
    g.blank()
    g.comment("-- support bounding box (engrave, visual frame) --")
    engrave_rect(g, 0.0, 0.0, P1_SUPPORT, P1_SUPPORT)
    g.comment("-- corner fiducials (registration) --")
    corner_fiducials(g, 0.0, 0.0, P1_SUPPORT, P1_SUPPORT)
    g.blank()
    g.comment(f"-- 8x8 = 64 pin holes, 2.54 mm pitch, {P1_HOLE_R*2:.1f} mm circle-bored through --")
    # boustrophedon (snake) row-major: row left->right, next row right->left, so every
    # between-hole travel is a short horizontal step (and a short vertical step between
    # rows) -- no long diagonal rapids crossing the matrix (the earlier column-up-then
    # -jump-down order drew 8 diagonal rapids that looked misaligned in a gcode viewer).
    for iy, cy in enumerate(P1_PIN_CENTRES):
        cols = list(enumerate(P1_PIN_CENTRES))
        if iy % 2:
            cols.reverse()
        for ix, cx in cols:
            g.comment(f"   pin ({ix + 1},{iy + 1}) support ({cx:.2f},{cy:.2f})")
            circle_bore(g, cx, cy, P1_HOLE_R, P1_PIN_BIT / 2.0,
                        z_target=-P1_STOCK_Z, z_step=0.5)
    g.blank()
    g.comment(f"-- [OUTLINE] through-cut {P1_SUPPORT:.0f}x{P1_SUPPORT:.0f} (4 bridges) --")
    outline_rect_with_bridges(g, 0.0, 0.0, P1_SUPPORT, P1_SUPPORT, bit=P1_PIN_BIT,
                              z_passes=[-1.0, -2.0, -P1_STOCK_Z], bridges=4)
    g.blank()
    g.lines.append(PROGRAM_END)
    return g.text()


def _p1_emit_holes(g: G, ox: float, oy: float) -> None:
    """Emit one 8x8 = 64-hole pattern at copy origin (ox,oy), flush (first pin
    at the corner), 2.0 mm BLIND, boustrophedon order, ONLY holes."""
    for iy in range(P1_PIN_N):
        cy = oy + iy * P1_PIN_PITCH
        cols = list(range(P1_PIN_N))
        if iy % 2:
            cols.reverse()
        for ix in cols:
            cx = ox + ix * P1_PIN_PITCH
            g.comment(f"   pin ({ix + 1},{iy + 1}) hole ({cx:.2f},{cy:.2f})")
            circle_bore(g, cx, cy, P1_HOLE_R, P1_PIN_BIT / 2.0,
                        z_target=-P1_HOLES_Z, z_step=0.5)


def _p1_emit_mold(g: G, ox: float, oy: float) -> None:
    """Emit one 17.78 x 17.78 'vaciado completo' (full pocket) at (ox,oy):
    1.0 mm-deep perimeter 'cuadrado de fuera' + interior zigzag 'vaciado de
    por dentro'."""
    rect_pocket(g, ox, oy, P1_ARRAY_SPAN, P1_ARRAY_SPAN,
                bit=P1_PIN_BIT, z_target=-P1_MOLD_Z, stepover=P1_MOLD_STEP,
                z_step=0.5)


def _p1_emit_waveguide(g: G, ox: float, oy: float) -> None:
    """Emit one 17.78 x 17.78 waveguide template at (ox,oy): a 1.0 mm-deep
    serpentine channel through all 64 grid points in boustrophedon order (the
    L2 entanglement bus, later filled with TEOS + microdiamonds)."""
    serpentine_channel(g, ox, oy, P1_PIN_N, P1_PIN_PITCH, bit=P1_PIN_BIT,
                       z_target=-P1_WG_Z, z_step=0.5)


def gen_p1_holes_and_mold() -> str:
    """P1 layer templates -- a 3-row x 4-col grid of the pin-array footprint, no through-cut.

    Three piece TYPES (one per row), 4 copies each, with NO through-cutting
    outline ("los rebordes ... no los quiero"):

      * Row 1 (Y 0..17.78): 4 HOLES pieces -- each the 8x8 = 64-hole PATTERN,
        circle-bored (linear-segment polygon circles, same method as
        p1_pin_array_support), BLIND 2.0 mm deep (NOT through). ONLY holes --
        no outline, no engrave, no fiducials ("sin rebordes"). Each array is
        FLUSH at the copy origin (first pin at the corner) so it aligns with a
        mold / waveguide cell -- hole (i,j) at (ox + i*pitch, oy + j*pitch).
      * Row 2 (Y 20.78..38.56): 4 MOLD pieces -- each a 17.78 x 17.78, 1.0 mm-deep
        square pocket (perimeter "cuadrado de fuera" + interior zigzag "vaciado
        de por dentro"), the pin-array footprint.
      * Row 3 (Y 41.56..59.34): 4 WAVEGUIDE templates -- each a 17.78 x 17.78, 1.0
        mm-deep SERPENTINE channel ("linea vaciada") connecting ALL 64 grid
        points in boustrophedon order (the L2 entanglement-bus layer, later
        filled with TEOS + microdiamonds; 532 nm pump in at the bottom-left
        facet, the bus start).

    4 of each type (12 pieces), 3 mm gap between copies, ~80 x 59 mm (fits a
    3018). No through-cut on any piece. Stock >= 3 mm: 2 mm blind holes leave a
    1 mm floor; the 1 mm mold / waveguide leave a 2 mm floor. The original
    p1_pin_array_support.gcode (through-holes + outline) is a SEPARATE file,
    kept unchanged.
    """
    grid_w = (P1_GRID_COLS - 1) * P1_COPY_PITCH + P1_ARRAY_SPAN   # ~80.12 mm
    grid_h = (P1_GRID_ROWS - 1) * P1_COPY_PITCH + P1_ARRAY_SPAN   # ~59.34 mm
    g = G()
    g.comment("QSSC P1 -- LAYER TEMPLATES: 3x4 grid (4 holes + 4 molds + 4 waveguides), NO through-cut")
    g.comment("GENERATED by tools/gen_qssc_gcode.py -- deterministic, auditable")
    g.comment(f"Stock >= 3 mm FR4/acrylic | tool: {P1_PIN_BIT:.1f} mm flat end-mill")
    g.comment(f"spindle {SPINDLE_RPM} rpm | feeds {PLUNGE_FEED} (plunge) / {CUT_FEED} (cut) mm/min")
    g.comment(f"Row 1 (Y 0..{P1_ARRAY_SPAN:.2f}): 4 HOLES pieces, 64 holes each, "
              f"2.54 mm pitch, 1.5 mm circle-bored, 2.0 mm DEEP (blind), sin rebordes.")
    g.comment(f"Row 2 (Y {P1_COPY_PITCH:.2f}..{P1_COPY_PITCH + P1_ARRAY_SPAN:.2f}): 4 MOLD pieces, "
              f"{P1_ARRAY_SPAN:.2f}x{P1_ARRAY_SPAN:.2f} mm, 1.0 mm-deep pocket (cuadrado + vaciado).")
    g.comment(f"Row 3 (Y {2*P1_COPY_PITCH:.2f}..{2*P1_COPY_PITCH + P1_ARRAY_SPAN:.2f}): 4 WAVEGUIDE templates, "
              f"{P1_ARRAY_SPAN:.2f}x{P1_ARRAY_SPAN:.2f} mm, 1.0 mm-deep SERPENTINE channel "
              f"(all 64 points, boustrophedon) -- L2 bus, TEOS + microdiamond fill.")
    g.comment(f"4 of each type (12 pieces), {P1_COPY_GAP:.0f} mm gap between copies, "
              f"~{grid_w:.1f}x{grid_h:.1f} mm. No through-cut.")
    g.comment("COORDS: stock-local. WORK ZERO = stock bottom-left (0,0).")
    g.blank()
    g.lines.append(SAFE_START)
    g.blank()

    # --- Row 1: 4 holes pieces (64 holes each, 2 mm blind, only holes) ---
    g.comment(f"-- Row 1: 4 holes pieces (X 0..{grid_w:.2f}), 2.0 mm deep, sin rebordes --")
    for c in range(P1_GRID_COLS):
        ox = c * P1_COPY_PITCH
        oy = 0.0
        g.comment(f"   -- holes piece ({c + 1}/{P1_GRID_COLS}) at origin ({ox:.2f},{oy:.2f}) --")
        _p1_emit_holes(g, ox, oy)
    g.blank()

    # --- Row 2: 4 mold pieces (17.78x17.78, 1 mm-deep pocket) ---
    g.comment(f"-- Row 2: 4 mold pieces (X 0..{grid_w:.2f}), 1.0 mm deep --")
    for c in range(P1_GRID_COLS):
        ox = c * P1_COPY_PITCH
        oy = P1_COPY_PITCH
        g.comment(f"   -- mold piece ({c + 1}/{P1_GRID_COLS}) at origin ({ox:.2f},{oy:.2f}) --")
        _p1_emit_mold(g, ox, oy)
    g.blank()

    # --- Row 3: 4 waveguide templates (17.78x17.78, 1 mm-deep serpentine) ---
    g.comment(f"-- Row 3: 4 waveguide templates (X 0..{grid_w:.2f}), 1.0 mm-deep serpentine --")
    for c in range(P1_GRID_COLS):
        ox = c * P1_COPY_PITCH
        oy = 2.0 * P1_COPY_PITCH
        g.comment(f"   -- waveguide piece ({c + 1}/{P1_GRID_COLS}) at origin ({ox:.2f},{oy:.2f}) --")
        _p1_emit_waveguide(g, ox, oy)
    g.blank()
    g.lines.append(PROGRAM_END)
    return g.text()


def _p1_single_header(g: G, title: str, body: str) -> None:
    """Common header for the single-piece P1 gcode files."""
    g.comment(title)
    g.comment("GENERATED by tools/gen_qssc_gcode.py -- deterministic, auditable")
    g.comment(f"Stock >= 3 mm FR4/acrylic | tool: {P1_PIN_BIT:.1f} mm flat end-mill")
    g.comment(f"spindle {SPINDLE_RPM} rpm | feeds {PLUNGE_FEED} (plunge) / {CUT_FEED} (cut) mm/min")
    g.comment(body)
    g.comment(f"Footprint {P1_ARRAY_SPAN:.2f}x{P1_ARRAY_SPAN:.2f} mm, flush at origin. No through-cut.")
    g.comment("COORDS: stock-local. WORK ZERO = stock bottom-left (0,0).")
    g.blank()
    g.lines.append(SAFE_START)
    g.blank()


def gen_p1_holes_single() -> str:
    """P1 holes -- ONE 64-hole pattern (17.78x17.78), 2.0 mm BLIND, sin rebordes.

    Standalone single-piece file: the 8x8 = 64-hole pattern, circle-bored
    (linear-segment polygon circles, no G02), BLIND 2.0 mm deep, ONLY holes (no
    outline / engrave / fiducials). Flush at the origin (first pin at the corner)
    so it aligns with a mold / waveguide cell. Same geometry as one grid copy.
    """
    g = G()
    _p1_single_header(
        g, "QSSC P1 -- HOLES (single): 64-hole pattern, 2.0 mm blind, sin rebordes",
        f"8x8 = 64 holes @ 2.54 mm pitch, {P1_HOLE_R*2:.1f} mm circle-bored, "
        f"2.0 mm DEEP (blind). ONLY holes -- no outline / engrave / fiducials.")
    g.comment("-- 64 pin holes, 2.54 mm pitch, 2.0 mm deep (blind) --")
    _p1_emit_holes(g, 0.0, 0.0)
    g.blank()
    g.lines.append(PROGRAM_END)
    return g.text()


def gen_p1_vaciado_single() -> str:
    """P1 vaciado -- ONE 17.78x17.78 'vaciado completo' (full pocket), 1.0 mm deep.

    Standalone single-piece file: the full square pocket -- perimeter 'cuadrado
    de fuera' (crisp wall) + interior boustrophedon zigzag 'vaciado de por
    dentro' (cleared floor). Same geometry as one mold cell of the grid.
    """
    g = G()
    _p1_single_header(
        g, "QSSC P1 -- VACIADO (single): 17.78x17.78 full pocket, 1.0 mm deep",
        f"{P1_ARRAY_SPAN:.2f}x{P1_ARRAY_SPAN:.2f} mm, 1.0 mm-deep pocket "
        f"(perimeter cuadrado + interior vaciado zigzag). Blind -- not through.")
    g.comment("-- full pocket (vaciado completo), 1.0 mm deep --")
    _p1_emit_mold(g, 0.0, 0.0)
    g.blank()
    g.lines.append(PROGRAM_END)
    return g.text()


def gen_p1_waveguide_single() -> str:
    """P1 waveguide -- ONE 17.78x17.78 serpentine channel, 1.0 mm deep.

    Standalone single-piece file: the L2 entanglement-bus template -- a 1.0 mm-
    deep, 1 mm-wide serpentine channel connecting ALL 64 grid points in
    boustrophedon order (532 nm pump in at the bottom-left facet, the bus start).
    Later filled with TEOS + microdiamonds. Same geometry as one grid copy.
    """
    g = G()
    _p1_single_header(
        g, "QSSC P1 -- WAVEGUIDE (single): 17.78x17.78 serpentine channel, 1.0 mm deep",
        f"SERPENTINE channel through all 64 grid points @ 2.54 mm pitch, 1.0 mm "
        f"deep, 1 mm wide. L2 entanglement bus (TEOS + microdiamond fill). Pump in at (0,0).")
    g.comment("-- serpentine waveguide channel, 1.0 mm deep (L2 bus) --")
    _p1_emit_waveguide(g, 0.0, 0.0)
    g.blank()
    g.lines.append(PROGRAM_END)
    return g.text()


# --- driver -----------------------------------------------------------------

FILES = {
    "square_chip_cnc.gcode": gen_square_chip,
    "cylinder_chip.gcode": gen_cylinder_chip,
    "fluorescence-sensor.gcode": gen_fluorescence_sensor,
    "p0_cathode_stencil.gcode": gen_p0_cathode_stencil,
    "p0_waveguide_channel.gcode": gen_p0_waveguide_channel,
    "p0_detector_stencil.gcode": gen_p0_detector_stencil,
    "p0_carrier_pcb.gcode": gen_p0_carrier,
    "p1_pin_array_support.gcode": gen_p1_pin_array_support,
    "p1_holes_and_mold.gcode": gen_p1_holes_and_mold,
    "p1_holes.gcode": gen_p1_holes_single,
    "p1_vaciado.gcode": gen_p1_vaciado_single,
    "p1_waveguide.gcode": gen_p1_waveguide_single,
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