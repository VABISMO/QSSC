"""Shared 2-D geometry primitives for the PCB router tools.

Squared-distance helpers between points, segments, and axis-aligned rectangles
(in board nm units). All return squared distances so the callers compare against
a squared clearance threshold without a ``sqrt`` per check. The rectangle API is
corner-based (``rx0, ry0, rx1, ry1``); :func:`seg_rect_dist2` uses Liang-Barsky
clipping so a segment that passes straight *through* a rectangle (both endpoints
outside, no corner the nearest point) correctly returns 0 -- the endpoint+corner
fallback alone misses that case and would flag a collinear straddling track as
clear (a short). :func:`make_cell_funcs` returns the ``(to_cell, to_xy)`` pair a
grid router uses, parameterized by origin + cell size.
"""
from __future__ import annotations


def seg_dist2(px, py, ax, ay, bx, by):
    """Squared distance from point (px,py) to segment (ax,ay)-(bx,by)."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2


def orient(ax, ay, bx, by, cx, cy):
    """Sign of the cross product of AB and AC: +1 / -1 / 0 (collinear)."""
    v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    return 1 if v > 0 else (-1 if v < 0 else 0)


def on_seg(px, py, ax, ay, bx, by):
    """True if (px,py) lies within the axis-aligned bounding box of AB."""
    return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)


def seg_seg_dist2(ax, ay, bx, by, cx, cy, dx, dy):
    """Squared distance between segments AB and CD (0 if they intersect)."""
    o1 = orient(ax, ay, bx, by, cx, cy); o2 = orient(ax, ay, bx, by, dx, dy)
    o3 = orient(cx, cy, dx, dy, ax, ay); o4 = orient(cx, cy, dx, dy, bx, by)
    if o1 != o2 and o3 != o4:
        return 0
    if o1 == 0 and on_seg(cx, cy, ax, ay, bx, by): return 0
    if o2 == 0 and on_seg(dx, dy, ax, ay, bx, by): return 0
    if o3 == 0 and on_seg(ax, ay, cx, cy, dx, dy): return 0
    if o4 == 0 and on_seg(bx, by, cx, cy, dx, dy): return 0
    return min(seg_dist2(ax, ay, cx, cy, dx, dy), seg_dist2(bx, by, cx, cy, dx, dy),
               seg_dist2(cx, cy, ax, ay, bx, by), seg_dist2(dx, dy, ax, ay, bx, by))


def pt_rect_dist2(px, py, rx0, ry0, rx1, ry1):
    """Squared distance from point (px,py) to rectangle [rx0,ry0]-[rx1,ry1]."""
    ddx = max(rx0 - px, 0, px - rx1); ddy = max(ry0 - py, 0, py - ry1)
    return ddx * ddx + ddy * ddy


def seg_rect_dist2(x1, y1, x2, y2, rx0, ry0, rx1, ry1):
    """Squared distance from segment (x1,y1)-(x2,y2) to rectangle [rx0,ry0]-[rx1,ry1].

    Liang-Barsky first: if the segment crosses (or touches) the rect, return 0.
    Otherwise fall back to the min of the endpoint and corner distances.
    """
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - rx0), (dx, rx1 - x1), (-dy, y1 - ry0), (dy, ry1 - y1)):
        if p == 0:
            if q < 0:          # parallel to this slab, entirely outside
                t0 = 2.0      # mark "no intersection"; fall through to dist
                break
        else:
            r = q / p
            if p < 0:
                if r > t1:
                    t0 = 2.0; break
                if r > t0:
                    t0 = r
            else:
                if r < t0:
                    t0 = 2.0; break
                if r < t1:
                    t1 = r
    if t0 <= t1:
        return 0              # segment crosses (or touches) the rect
    d = min(pt_rect_dist2(x1, y1, rx0, ry0, rx1, ry1),
            pt_rect_dist2(x2, y2, rx0, ry0, rx1, ry1))
    for cx, cy in ((rx0, ry0), (rx1, ry0), (rx0, ry1), (rx1, ry1)):
        dd = seg_dist2(cx, cy, x1, y1, x2, y2)
        if dd < d:
            d = dd
    return d


def pt_rect_center_dist2(px, py, cx, cy, hw, hh):
    """Squared distance from point (px,py) to a rectangle given by its centre
    (cx,cy) and half-extents (hw,hh). Exact transform of :func:`pt_rect_dist2`
    (the copper-pad model used by the plane builder: pads are stored as centre +
    GetSize half-extents, not corners)."""
    return pt_rect_dist2(px, py, cx - hw, cy - hh, cx + hw, cy + hh)


def seg_rect_center_dist2(x1, y1, x2, y2, cx, cy, hw, hh):
    """Squared distance from segment (x1,y1)-(x2,y2) to a rectangle (centre +
    half-extents), by sampling the segment at ~0.1 mm steps (a tie trace is
    <= 3 mm, so <= 30 samples). The via endpoint already cleared the pad; this
    guards the trace body against skirting a different-net pad's long side.

    Sampling (not Liang-Barsky) is kept to match the plane builder's original
    behaviour exactly: the sample step and point-to-rect formula are identical
    to the inlined helper it replaced, so via/trace placement is unchanged."""
    length = int(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
    n = max(2, length // 100_000 + 1)
    best = 9_000_000_000_000_000
    for i in range(n + 1):
        t = i / n
        px = x1 + t * (x2 - x1)
        py = y1 + t * (y2 - y1)
        d = pt_rect_center_dist2(px, py, cx, cy, hw, hh)
        if d < best:
            best = d
    return best


def seg_bbox(x1, y1, x2, y2, pad):
    """Inflated axis-aligned bounding box of segment (x1,y1)-(x2,y2) by `pad`."""
    return (min(x1, x2) - pad, min(y1, y2) - pad, max(x1, x2) + pad, max(y1, y2) + pad)


def make_cell_funcs(origin_x, origin_y, cell):
    """Return ``(to_cell, to_xy)`` for a grid anchored at origin with cell size `cell`.

    ``to_cell(x, y) -> (cx, cy)`` maps a board point to its grid cell;
    ``to_xy(cx, cy) -> (x, y)`` maps a cell back to its board-coordinate corner.
    """
    def to_cell(x, y):
        return (x - origin_x) // cell, (y - origin_y) // cell

    def to_xy(cx, cy):
        return origin_x + cx * cell, origin_y + cy * cell

    return to_cell, to_xy