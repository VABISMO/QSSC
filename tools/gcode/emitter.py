"""Generic G-code emitter + machining helpers for a GRBL 3018-class CNC.

Shared by the QSSC carrier/substrate toolpath generator
(``tools/gen_qssc_gcode.py``) and the spin-coater chuck generator
(``tools/gen_spincoater_gcode.py``). Everything here is machine-level
(mm scale, end-mill / V-bit) and independent of any particular part geometry:
the ``G`` emitter tracks position so arcs / polygon circles are well-formed,
and the helper functions build common primitives (pockets, outlines with
bridges, peck-drilled holes, fiducials, serpentine channels) on top of it.

These are MACRO carrier/substrate toolpaths, not microchip features (2.5 um
pitch, made by lithography). The helpers do NOT know a machine's tool offsets,
backlash, fixturing or workpiece zero -- verify feeds, fixturing, tool offsets
and workpiece zero in a CAM/controller before every run.
"""
from __future__ import annotations

import math

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


def segments(lo: float, hi: float, breaks: list[float],
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
# Every toolpath gets a light bounding-box engrave + corner fiducials so the
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

      1. PERIMETER outline -- trace the square at each Z level so the mold has a
         crisp square wall. The bit center stays ``bit/2`` inside the boundary so
         the cut edge reaches the square.
      2. INTERIOR zigzag raster -- clear the inside at each Z level with a
         boustrophedon (snake) zigzag, step-over ``stepover``.

    No through-cut: the bit stops at ``z_target`` (leaves a floor).
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
    (BLIND).

    The path is a boustrophedon (snake): row 0 left->right through all n points,
    drop to row 1 at the right end, row 1 right->left, drop to row 2 at the left
    end, ... so ONE continuous channel passes through all n*n grid points.
    Vertical connectors at ALTERNATING ends link the rows, so the polyline never
    crosses itself. The channel is ``bit``-diameter wide and ``z_target`` deep.
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