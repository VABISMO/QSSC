#!/usr/bin/env python3
"""Close residual split/dangling nets with direct Manhattan tracks (no autorouter).

For each named net (or every non-plane split net when none are named), cluster its
pads+tracks+vias by copper connectivity; for every pair of unconnected clusters,
draw an L- or Z-shaped track on a shared SIGNAL layer between the closest pad
pair, clearance-checked against all other-net copper at 0.17 mm. Falls back to a
through-via + inner-layer hop when no same-layer Manhattan path clears.

This is the right tool for the residuals FreeRouting leaves when its optimizer
stalls: short 2- or 3-pin SMD nets (ADF4351 control pins, TIA feedback pairs, USB
config-channel) that need a plain same-layer track, NOT the via-based 3D A* of
maze_bridge_3d (which forces an endpoint via and reports "no clear via spot" on
dense QFN fans where there is no room for a 0.40 mm via but a 0.20 mm same-layer
track fits easily). Run AFTER FreeRouting + maze_bridge_3d; re-pour planes after.

Non-destructive: writes <board>.closed.kicad_pcb.  DRC + connectivity-check it,
then promote.

Usage: python3 tools/close_residuals.py <board.kicad_pcb> [net1,net2,...]
       python3 tools/close_residuals.py <board.kicad_pcb>            # all split nets
System python3 (pcbnew).  No .venv.
"""
from __future__ import annotations
import sys, math, os, signal
from pathlib import Path
import numpy as np

bp = Path(sys.argv[1]).resolve()
only = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
import pcbnew
import test_pcb_connectivity as tc

B = pcbnew.LoadBoard(str(bp))
NM = 1_000_000
F_Cu = pcbnew.F_Cu; B_Cu = pcbnew.B_Cu
# 4 signal layers (In1=GND, In2=+3V3 are poured planes -- never route here).
SIG_LAYERS = [F_Cu, B_Cu, pcbnew.In3_Cu, pcbnew.In4_Cu]
LNAME = {F_Cu: "F.Cu", B_Cu: "B.Cu", pcbnew.In3_Cu: "In3", pcbnew.In4_Cu: "In4"}
TRK_W = int(0.20 * NM)
REAL_CLEAR = int(0.17 * NM)
VIA_DIA = int(0.40 * NM); VIA_DRILL = int(0.20 * NM)
KEEP = REAL_CLEAR + TRK_W // 2


# ---- geometry helpers (segment/rect distance) ----
def _seg_dist2(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2


def _orient(ax, ay, bx, by, cx, cy):
    v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    return 1 if v > 0 else (-1 if v < 0 else 0)


def _on_seg(px, py, ax, ay, bx, by):
    return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)


def _seg_seg_dist2(ax, ay, bx, by, cx, cy, dx, dy):
    o1 = _orient(ax, ay, bx, by, cx, cy); o2 = _orient(ax, ay, bx, by, dx, dy)
    o3 = _orient(cx, cy, dx, dy, ax, ay); o4 = _orient(cx, cy, dx, dy, bx, by)
    if o1 != o2 and o3 != o4:
        return 0
    if o1 == 0 and _on_seg(cx, cy, ax, ay, bx, by): return 0
    if o2 == 0 and _on_seg(dx, dy, ax, ay, bx, by): return 0
    if o3 == 0 and _on_seg(ax, ay, cx, cy, dx, dy): return 0
    if o4 == 0 and _on_seg(bx, by, cx, cy, dx, dy): return 0
    return min(_seg_dist2(ax, ay, cx, cy, dx, dy), _seg_dist2(bx, by, cx, cy, dx, dy),
               _seg_dist2(cx, cy, ax, ay, bx, by), _seg_dist2(dx, dy, ax, ay, bx, by))


def _pt_rect_dist2(px, py, rx0, ry0, rx1, ry1):
    ddx = max(rx0 - px, 0, px - rx1); ddy = max(ry0 - py, 0, py - ry1)
    return ddx * ddx + ddy * ddy


def _seg_rect_dist2(x1, y1, x2, y2, rx0, ry0, rx1, ry1):
    # Liang-Barsky: if the segment CROSSES the rect, distance is 0. The
    # endpoint + corner checks below miss exactly this case -- a track that
    # passes straight THROUGH a pad (both endpoints outside, no corner the
    # closest point), e.g. two same-net pads straddling a collinear other-net
    # pad (UT13 DET_REF pad3/pad5 with +3V3 pad4 between). That returned
    # 0.45^2 here instead of 0 -> _trk_clear called it clear -> a shorting
    # track was added -> 8 DRC shorting_items. Intersection -> 0 -> rejected.
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
    d = min(_pt_rect_dist2(x1, y1, rx0, ry0, rx1, ry1),
            _pt_rect_dist2(x2, y2, rx0, ry0, rx1, ry1))
    for cx, cy in ((rx0, ry0), (rx1, ry0), (rx0, ry1), (rx1, ry1)):
        dd = _seg_dist2(cx, cy, x1, y1, x2, y2)
        if dd < d:
            d = dd
    return d


def _seg_bbox(x1, y1, x2, y2, pad):
    return (min(x1, x2) - pad, min(y1, y2) - pad, max(x1, x2) + pad, max(y1, y2) + pad)


# ---- clearance checks (other-net copper only) ----
def _trk_clear(x1, y1, x2, y2, layer, nc):
    """Track on `layer` clear of all OTHER-net copper on that layer (0.17 mm)."""
    hw = TRK_W // 2 + REAL_CLEAR; hw2 = hw * hw
    bx0, by0, bx1, by1 = _seg_bbox(x1, y1, x2, y2, hw)
    for t in B.GetTracks():
        if t.GetNetCode() == nc:
            continue
        if t.GetClass() == "PCB_VIA":
            pos = t.GetPosition()
            top, bot = t.TopLayer(), t.BottomLayer()
            if not (top <= layer <= bot):
                continue
            rr = hw + t.GetWidth() // 2
            if _seg_dist2(pos.x, pos.y, x1, y1, x2, y2) < rr * rr:
                return False
        else:
            if t.GetLayer() != layer:
                continue
            s = t.GetStart(); e = t.GetEnd()
            # bbox pre-filter
            tb0 = min(s.x, e.x) - hw; tb1 = max(s.x, e.x) + hw
            if tb1 < bx0 or tb0 > bx1:
                tb0y = min(s.y, e.y) - hw; tb1y = max(s.y, e.y) + hw
                if tb1y < by0 or tb0y > by1:
                    continue
            rr = hw + t.GetWidth() // 2
            if _seg_seg_dist2(x1, y1, x2, y2, s.x, s.y, e.x, e.y) < rr * rr:
                return False
    for fp in B.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == nc:
                continue
            players = tc._pad_layer_set(p)
            if players is not None and layer not in players:
                continue
            pb = p.GetBoundingBox()
            rx0, ry0 = pb.GetX(), pb.GetY()
            rx1, ry1 = pb.GetX() + pb.GetWidth(), pb.GetY() + pb.GetHeight()
            if _seg_rect_dist2(x1, y1, x2, y2, rx0, ry0, rx1, ry1) < hw2:
                return False
    return True


def _via_clear(x, y, nc):
    """Through via at (x,y) clear of all OTHER-net copper on every layer."""
    vr = VIA_DIA // 2 + KEEP; vr2 = vr * vr
    for t in B.GetTracks():
        if t.GetNetCode() == nc:
            continue
        if t.GetClass() == "PCB_VIA":
            pos = t.GetPosition(); rr = vr + t.GetWidth() // 2
            if (pos.x - x) ** 2 + (pos.y - y) ** 2 < rr * rr:
                return False
        else:
            s = t.GetStart(); e = t.GetEnd(); rr = vr + t.GetWidth() // 2
            if _seg_dist2(x, y, s.x, s.y, e.x, e.y) < rr * rr:
                return False
    for fp in B.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == nc:
                continue
            players = tc._pad_layer_set(p)
            if players is not None and not (F_Cu in players or B_Cu in players):
                continue
            pb = p.GetBoundingBox()
            if (pb.GetX() - vr) <= x <= (pb.GetX() + pb.GetWidth() + vr) and \
               (pb.GetY() - vr) <= y <= (pb.GetY() + pb.GetHeight() + vr):
                cx0, cy0 = pb.GetX(), pb.GetY()
                cx1, cy1 = pb.GetX() + pb.GetWidth(), pb.GetY() + pb.GetHeight()
                ddx = max(cx0 - x, 0, x - cx1); ddy = max(cy0 - y, 0, y - cy1)
                if ddx * ddx + ddy * ddy < vr2:
                    return False
    return True


def _board_rect():
    bb = B.GetBoardEdgesBoundingBox()
    return bb.GetX(), bb.GetY(), bb.GetX() + bb.GetWidth(), bb.GetY() + bb.GetHeight()


BX0, BY0, BX1, BY1 = _board_rect()


def _in_board(x, y, marg=KEEP):
    return BX0 + marg <= x <= BX1 - marg and BY0 + marg <= y <= BY1 - marg


# ---- cluster pads+tracks+vias by connectivity (reuse tc geometry) ----
def _cluster(nc):
    items = []
    for fp in B.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == nc:
                pos = p.GetPosition(); bbp = p.GetBoundingBox()
                items.append(("pad", {"kind": "pad", "x": pos.x, "y": pos.y,
                    "bx0": bbp.GetX(), "by0": bbp.GetY(),
                    "bx1": bbp.GetX() + bbp.GetWidth(), "by1": bbp.GetY() + bbp.GetHeight(),
                    "layers": tc._pad_layer_set(p),
                    "r": min(bbp.GetWidth(), bbp.GetHeight()) // 2, "it": p}))
    for t in B.GetTracks():
        if t.GetNetCode() != nc:
            continue
        if t.GetClass() == "PCB_VIA":
            pos = t.GetPosition()
            items.append(("via", {"kind": "via", "x": pos.x, "y": pos.y,
                "layers": tc._via_layer_set(B, t), "r": t.GetWidth() // 2, "it": t}))
        elif t.Type() == pcbnew.PCB_TRACE_T:
            s = t.GetStart(); e = t.GetEnd()
            items.append(("trk", {"kind": "trk", "ax": s.x, "ay": s.y, "bx": e.x, "by": e.y,
                "hw": t.GetWidth() // 2, "layers": {t.GetLayer()}, "it": t}))
    n = len(items); uf = tc._UF(n)
    for i in range(n):
        for j in range(i + 1, n):
            if tc._layers_share(items[i][1].get("layers"), items[j][1].get("layers")) \
               and tc._touch(items[i][1], items[j][1]):
                uf.union(i, j)
    cl = {}
    for i, (kind, g) in enumerate(items):
        cl.setdefault(uf.find(i), {"pads": [], "vias": [], "trks": []})
        cl[uf.find(i)]["pads" if kind == "pad" else "vias" if kind == "via" else "trks"].append(g)
    return [c for c in cl.values() if c["pads"]]


# ---- commit primitives ----
def _add_trk(x1, y1, x2, y2, layer, nc):
    t = pcbnew.PCB_TRACK(B)
    t.SetStart(pcbnew.VECTOR2I(int(x1), int(y1)))
    t.SetEnd(pcbnew.VECTOR2I(int(x2), int(y2)))
    t.SetWidth(TRK_W); t.SetLayer(layer); t.SetNetCode(nc); B.Add(t); return t


def _add_via(x, y, nc):
    v = pcbnew.PCB_VIA(B)
    v.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
    v.SetTopLayer(F_Cu); v.SetBottomLayer(B_Cu)
    v.SetDrill(VIA_DRILL); v.SetWidth(VIA_DIA); v.SetNetCode(nc)
    v.SetViaType(pcbnew.VIATYPE_THROUGH); B.Add(v); return v


# ---- single-layer 2D A* (winds around obstacles; no via needed) ----
CELL = int(0.10 * NM)
_MARG = int(0.4 * NM)
_GX0, _GY0 = BX0 + _MARG, BY0 + _MARG
_GX1, _GY1 = BX1 - _MARG, BY1 - _MARG
_NCOL = (_GX1 - _GX0) // CELL + 1
_NROW = (_GY1 - _GY0) // CELL + 1


def _to_cell(x, y):
    return (x - _GX0) // CELL, (y - _GY0) // CELL


def _to_xy(cx, cy):
    return _GX0 + cx * CELL, _GY0 + cy * CELL


# Cell-center coordinate arrays (constant) for numpy rasterization. Cell (cx,cy)
# center is at (_GX0 + cx*CELL, _GY0 + cy*CELL); the grid is row-major [cy, cx] so
# .tobytes() matches the flat index `ci = cy*_NCOL + cx` the A* uses.
_CXS = _GX0 + np.arange(_NCOL) * CELL
_CYS = _GY0 + np.arange(_NROW) * CELL


def _mark_seg_np(grid, sx, sy, ex, ey, rad):
    """Vectorized thick-segment rasterization: mark every cell whose center is
    within `rad` of the segment (sx,sy)-(ex,ey). One numpy op per track instead
    of stamping hundreds of overlapping discs -> ~50x faster on 1300 tracks."""
    dx, dy = ex - sx, ey - sy
    L2 = dx * dx + dy * dy
    j0 = int((min(sx, ex) - rad - _GX0) // CELL)
    j1 = int((max(sx, ex) + rad - _GX0) // CELL)
    i0 = int((min(sy, ey) - rad - _GY0) // CELL)
    i1 = int((max(sy, ey) + rad - _GY0) // CELL)
    j0 = max(0, j0); j1 = min(_NCOL - 1, j1)
    i0 = max(0, i0); i1 = min(_NROW - 1, i1)
    if j1 < j0 or i1 < i0:
        return
    sub_x = _CXS[j0:j1 + 1]
    sub_y = _CYS[i0:i1 + 1]
    X, Y = np.meshgrid(sub_x, sub_y)
    if L2 == 0:
        d = np.hypot(X - sx, Y - sy)
    else:
        t = ((X - sx) * dx + (Y - sy) * dy) / L2
        np.clip(t, 0.0, 1.0, out=t)
        d = np.hypot(X - (sx + t * dx), Y - (sy + t * dy))
    grid[i0:i1 + 1, j0:j1 + 1] |= (d <= rad)


def _mark_disc_np(grid, x, y, rad):
    """Vectorized disc rasterization (for vias / round pads)."""
    j0 = max(0, int((x - rad - _GX0) // CELL)); j1 = min(_NCOL - 1, int((x + rad - _GX0) // CELL))
    i0 = max(0, int((y - rad - _GY0) // CELL)); i1 = min(_NROW - 1, int((y + rad - _GY0) // CELL))
    if j1 < j0 or i1 < i0:
        return
    sub_x = _CXS[j0:j1 + 1]; sub_y = _CYS[i0:i1 + 1]
    X, Y = np.meshgrid(sub_x, sub_y)
    d = np.hypot(X - x, Y - y)
    grid[i0:i1 + 1, j0:j1 + 1] |= (d <= rad)


def _pad_slice(pb, keep):
    """Bbox cell slice [i0:i1+1, j0:j1+1] for a pad bounding box expanded by keep."""
    j0 = max(0, int((pb.GetX() - keep - _GX0) // CELL))
    j1 = min(_NCOL - 1, int((pb.GetX() + pb.GetWidth() + keep - _GX0) // CELL))
    i0 = max(0, int((pb.GetY() - keep - _GY0) // CELL))
    i1 = min(_NROW - 1, int((pb.GetY() + pb.GetHeight() + keep - _GY0) // CELL))
    return i0, i1, j0, j1


def _build_all_blocked_2d(layer):
    """numpy uint8 grid [_NROW, _NCOL]: 1 = blocked by ANY copper (all nets) on
    `layer`. Built ONCE per layer and cached -- the expensive O(all-tracks +
    all-pads) sweep, now vectorized with numpy (was >120 s stamping overlapping
    discs along 1300 tracks in pure Python; now a few seconds). Same-net copper
    is left blocked here; per-net we copy + clear the route net's own copper
    (``_unblock_net_np``) so a route may run along its existing tracks. The A*
    force-opens the start/goal pad cells on its copy."""
    grid = np.zeros((_NROW, _NCOL), dtype=np.uint8)
    keep = KEEP
    for fp in B.GetFootprints():
        for p in fp.Pads():
            players = tc._pad_layer_set(p)
            if players is not None and layer not in players:
                continue
            i0, i1, j0, j1 = _pad_slice(p.GetBoundingBox(), keep)
            if i1 >= i0 and j1 >= j0:
                grid[i0:i1 + 1, j0:j1 + 1] = 1
    for t in B.GetTracks():
        if t.GetClass() == "PCB_VIA":
            pos = t.GetPosition()
            top, bot = t.TopLayer(), t.BottomLayer()
            if not (top <= layer <= bot):
                continue
            _mark_disc_np(grid, pos.x, pos.y, keep + t.GetWidth() // 2)
        elif t.GetLayer() == layer:
            s = t.GetStart(); e = t.GetEnd()
            _mark_seg_np(grid, s.x, s.y, e.x, e.y, keep + t.GetWidth() // 2)
    return grid


def _unblock_net_np(grid, layer, nc):
    """In-place on a numpy grid COPY: clear nc's own copper on `layer` so a
    route may reuse / run along its existing tracks + pads."""
    keep = KEEP
    for fp in B.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() != nc:
                continue
            players = tc._pad_layer_set(p)
            if players is not None and layer not in players:
                continue
            i0, i1, j0, j1 = _pad_slice(p.GetBoundingBox(), keep)
            if i1 >= i0 and j1 >= j0:
                grid[i0:i1 + 1, j0:j1 + 1] = 0
    for t in B.GetTracks():
        if t.GetNetCode() != nc:
            continue
        if t.GetClass() == "PCB_VIA":
            pos = t.GetPosition()
            top, bot = t.TopLayer(), t.BottomLayer()
            if not (top <= layer <= bot):
                continue
            rad = keep + t.GetWidth() // 2
            j0 = max(0, int((pos.x - rad - _GX0) // CELL)); j1 = min(_NCOL - 1, int((pos.x + rad - _GX0) // CELL))
            i0 = max(0, int((pos.y - rad - _GY0) // CELL)); i1 = min(_NROW - 1, int((pos.y + rad - _GY0) // CELL))
            if i1 >= i0 and j1 >= j0:
                sub_x = _CXS[j0:j1 + 1]; sub_y = _CYS[i0:i1 + 1]
                X, Y = np.meshgrid(sub_x, sub_y)
                d = np.hypot(X - pos.x, Y - pos.y)
                grid[i0:i1 + 1, j0:j1 + 1] &= ~(d <= rad).astype(np.uint8)
        elif t.GetLayer() == layer:
            s = t.GetStart(); e = t.GetEnd()
            rad = keep + t.GetWidth() // 2
            dx, dy = e.x - s.x, e.y - s.y; L2 = dx * dx + dy * dy
            j0 = max(0, int((min(s.x, e.x) - rad - _GX0) // CELL)); j1 = min(_NCOL - 1, int((max(s.x, e.x) + rad - _GX0) // CELL))
            i0 = max(0, int((min(s.y, e.y) - rad - _GY0) // CELL)); i1 = min(_NROW - 1, int((max(s.y, e.y) + rad - _GY0) // CELL))
            if i1 < i0 or j1 < j0:
                continue
            sub_x = _CXS[j0:j1 + 1]; sub_y = _CYS[i0:i1 + 1]
            X, Y = np.meshgrid(sub_x, sub_y)
            if L2 == 0:
                d = np.hypot(X - s.x, Y - s.y)
            else:
                tt = ((X - s.x) * dx + (Y - s.y) * dy) / L2
                np.clip(tt, 0.0, 1.0, out=tt)
                d = np.hypot(X - (s.x + tt * dx), Y - (s.y + tt * dy))
            grid[i0:i1 + 1, j0:j1 + 1] &= ~(d <= rad).astype(np.uint8)
    return grid


def _astar2d(blocked, start, goal):
    """4-connectivity A* over the blocked grid, restricted to a search window
    around start+goal (pad bbox + ASTAR_MARGIN cells). Restricting the window
    keeps the search out of the 1.1 M-cell board when two pads are close -- a
    full-board A* crawls hundreds of thousands of cells in pure Python; the
    window caps a 10 mm net to ~25k cells. Returns list of (cx,cy) or None."""
    import heapq
    sx, sy = start; gx, gy = goal
    if not (0 <= sx < _NCOL and 0 <= sy < _NROW and 0 <= gx < _NCOL and 0 <= gy < _NROW):
        return None
    blocked = bytearray(blocked)  # private copy so force-open doesn't mutate cache
    M = int(os.environ.get("ASTAR_MARGIN", "80"))  # 8 mm detour room
    wj0 = max(0, min(sx, gx) - M); wj1 = min(_NCOL - 1, max(sx, gx) + M)
    wi0 = max(0, min(sy, gy) - M); wi1 = min(_NROW - 1, max(sy, gy) + M)
    si = sy * _NCOL + sx; gi = gy * _NCOL + gx
    blocked[si] = 0; blocked[gi] = 0
    N = _NCOL * _NROW
    g = [-1] * N; g[si] = 0
    came = [-1] * N
    closed = bytearray(N)
    h = lambda ci: abs(ci % _NCOL - gx) + abs(ci // _NCOL - gy)
    open_h = [(h(si), 0, si)]
    budget = int(os.environ.get("ASTAR_BUDGET", "60000"))
    expansions = 0
    while open_h:
        _, gc, ci = heapq.heappop(open_h)
        if ci == gi:
            path = [(ci % _NCOL, ci // _NCOL)]
            while came[ci] != -1:
                ci = came[ci]; path.append((ci % _NCOL, ci // _NCOL))
            path.reverse(); return path
        if closed[ci]:
            continue
        closed[ci] = 1
        expansions += 1
        if expansions > budget:
            return None  # hopeless (congested long net) -- bail, don't crawl
        cx, cy = ci % _NCOL, ci // _NCOL
        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if not (wj0 <= nx <= wj1 and wi0 <= ny <= wi1):
                continue
            ni = ny * _NCOL + nx
            if blocked[ni]:
                continue
            ng = gc + 1
            if g[ni] == -1 or ng < g[ni]:
                g[ni] = ng; came[ni] = ci
                heapq.heappush(open_h, (ng + h(ni), ng, ni))
    return None


def _simplify(cells):
    """Collapse collinear grid cells into orthogonal (x,y)-(x,y) segments."""
    if len(cells) < 2:
        return []
    segs = []
    s = cells[0]; p = cells[1]; dir0 = None
    for k in range(2, len(cells)):
        c = cells[k]
        d = (c[0] - p[0], c[1] - p[1])
        if dir0 is None:
            dir0 = d
        if (c[0] != p[0] and (p[0] != s[0])) or (c[1] != p[1] and (p[1] != s[1])) or \
           (dir0[0] != d[0] or dir0[1] != d[1]):
            segs.append((s[0], s[1], p[0], p[1]))
            s = p; dir0 = d
        p = c
    segs.append((s[0], s[1], p[0], p[1]))
    return segs


_all_blocked_cache = {}


def _get_all_blocked(layer):
    """Cached numpy uint8 grid [_NROW,_NCOL] of all-copper blocks on `layer`."""
    g = _all_blocked_cache.get(layer)
    if g is None:
        g = _build_all_blocked_2d(layer)
        _all_blocked_cache[layer] = g
    return g


def _get_blocked(layer, nc):
    """Per-net blocked grid as a bytearray: copy the all-copper numpy grid, clear
    this net's copper, then .tobytes() so the pure-Python A* indexes it flat."""
    grid = _get_all_blocked(layer).copy()
    _unblock_net_np(grid, layer, nc)
    return bytearray(grid.tobytes())


def _collapse_segs(segs, layer, nc, window=14):
    """Greedily merge a staircase of short orthogonal segs into long L-shaped
    runs. A grid A* path through a congested area is a 300+ step staircase; this
    walks it and, from each waypoint, jumps to the farthest later waypoint
    (within `window` segs) reachable by a 2-segment L whose both legs pass the
    real _trk_clear check. Collapses 342 segs -> ~10. DRC-safe by construction."""
    if len(segs) <= 2:
        return segs
    out = []
    i = 0
    n = len(segs)
    while i < n:
        ax, ay = segs[i][0], segs[i][1]
        jumped = False
        jmax = min(n - 1, i + window)
        for j in range(jmax, i, -1):
            bx, by = segs[j][2], segs[j][3]
            for cx, cy in ((bx, ay), (ax, by)):
                if _trk_clear(ax, ay, cx, cy, layer, nc) and \
                   _trk_clear(cx, cy, bx, by, layer, nc):
                    out.append((ax, ay, cx, cy, layer))
                    out.append((cx, cy, bx, by, layer))
                    i = j
                    jumped = True
                    break
            if jumped:
                break
        if not jumped:
            out.append(segs[i])
            i += 1
    # drop zero-length + merge consecutive collinear
    merged = []
    for s in out:
        if s[0] == s[2] and s[1] == s[3]:
            continue
        if merged and merged[-1][2] == s[0] and merged[-1][3] == s[1] and \
           merged[-1][0] == merged[-1][2] == s[0] == s[2]:
            merged[-1] = (merged[-1][0], merged[-1][1], s[2], s[3], layer)
        elif merged and merged[-1][2] == s[0] and merged[-1][3] == s[1] and \
             merged[-1][1] == merged[-1][3] == s[1] == s[3]:
            merged[-1] = (merged[-1][0], merged[-1][1], s[2], s[3], layer)
        else:
            merged.append(s)
    return merged


def _try_astar2d(ax, ay, bx, by, layer, nc):
    """2D A* on `layer` from pad a to pad b. Returns list of segs (x1,y1,x2,y2,layer)."""
    blocked = _get_blocked(layer, nc)
    sc = _to_cell(ax, ay); gc_ = _to_cell(bx, by)
    if not (0 <= sc[0] < _NCOL and 0 <= sc[1] < _NROW and
            0 <= gc_[0] < _NCOL and 0 <= gc_[1] < _NROW):
        return None
    path = _astar2d(blocked, sc, gc_)
    if not path:
        return None
    raw = []
    for (cx1, cy1, cx2, cy2) in _simplify(path):
        x1, y1 = _to_xy(cx1, cy1); x2, y2 = _to_xy(cx2, cy2)
        if x1 == x2 and y1 == y2:
            continue
        raw.append((x1, y1, x2, y2, layer))
    segs = _collapse_segs(raw, layer, nc)
    # validate every final segment with the real clearance check
    for (x1, y1, x2, y2, L) in segs:
        if not _trk_clear(x1, y1, x2, y2, L, nc):
            return None  # grid was conservative but real check fails -> abort
    return segs or None


# ---- path attempts: L, then Z (scanned jog), then 2D A*, then via-bridge ----
def _try_L(ax, ay, bx, by, layer, nc):
    """Two L orderings: via (bx,ay) or (ax,by). Return list of segs if clear."""
    for cx, cy in ((bx, ay), (ax, by)):
        s1 = (ax, ay, cx, cy, layer); s2 = (cx, cy, bx, by, layer)
        if _trk_clear(*s1, nc) and _trk_clear(*s2, nc):
            return [s1, s2]
    return None


def _try_Z(ax, ay, bx, by, layer, nc):
    """Z with a scanned jog. Tries BOTH orientations (vertical jog-bar at
    intermediate x, horizontal jog-bar at intermediate y) so the route can
    escape a blocked pad-row to a clear lane before running across."""
    step = int(0.20 * NM)
    # orientation 1: vertical bar at intermediate x  a->(x,ay)->(x,by)->b
    lo, hi = sorted((ax, bx))
    x = lo + step
    while x < hi - step // 2:
        for (jy1, jy2) in ((ay, by), (by, ay)):
            s1 = (ax, ay, x, jy1, layer); s2 = (x, jy1, x, jy2, layer); s3 = (x, jy2, bx, by, layer)
            if _trk_clear(*s1, nc) and _trk_clear(*s2, nc) and _trk_clear(*s3, nc):
                return [s1, s2, s3]
        x += step
    # orientation 2: horizontal bar at intermediate y  a->(ax,y)->(bx,y)->b
    lo, hi = sorted((ay, by))
    y = lo + step
    while y < hi - step // 2:
        for (jx1, jx2) in ((ax, bx), (bx, ax)):
            s1 = (ax, ay, jx1, y, layer); s2 = (jx1, y, jx2, y, layer); s3 = (jx2, y, bx, by, layer)
            if _trk_clear(*s1, nc) and _trk_clear(*s2, nc) and _trk_clear(*s3, nc):
                return [s1, s2, s3]
        y += step
    return None


def _find_via_spot(near_x, near_y, nc):
    """Spiral search for a through-via spot clear of copper + inside the board."""
    step = int(0.15 * NM)
    for ring in range(1, 16):
        for ang_deg in range(0, 360, 30):
            r = ring * step; a = math.radians(ang_deg)
            vx = int(near_x + r * math.cos(a)); vy = int(near_y + r * math.sin(a))
            if not _in_board(vx, vy):
                continue
            if _via_clear(vx, vy, nc):
                return vx, vy
    return None


def _try_via_bridge(ax, ay, bx, by, alay, blay, nc):
    """a(on alay)->via_a->straight on an inner layer->via_b->b(on blay)."""
    inner_choices = [L for L in SIG_LAYERS if L not in (alay, blay)] or [pcbnew.In3_Cu]
    va = _find_via_spot(ax, ay, nc)
    if va is None:
        return None
    vb = _find_via_spot(bx, by, nc)
    if vb is None:
        return None
    segs = []; vias = [va, vb]
    # a pad -> via_a on the pad's own layer
    s0 = (ax, ay, va[0], va[1], alay)
    if not _trk_clear(*s0, nc):
        return None
    segs.append(s0)
    for inner in inner_choices:
        s1 = (va[0], va[1], vb[0], vb[1], inner)
        if _trk_clear(*s1, nc):
            segs.append(s1)
            break
    else:
        return None
    s2 = (vb[0], vb[1], bx, by, blay)
    if not _trk_clear(*s2, nc):
        return None
    segs.append(s2)
    return (segs, vias)


def _shared_signal_layers(la, lb):
    """Signal layers shared by both pad layer-sets (None=all). Intersect with SIG."""
    cand = set(SIG_LAYERS)
    a = cand if la is None else (set(la) & cand)
    b = cand if lb is None else (set(lb) & cand)
    common = a & b
    # prefer the pads' own surface layer first (shortest, no via), then the other
    order = []
    for L in (F_Cu, B_Cu, pcbnew.In3_Cu, pcbnew.In4_Cu):
        if L in common and L not in order:
            order.append(L)
    return order


def _closest_pad_pair(clA, clB):
    best = None
    for pa in clA["pads"]:
        la = pa.get("layers")
        for pb in clB["pads"]:
            lb = pb.get("layers")
            if not tc._layers_share(la, lb):
                continue
            d = (pa["x"] - pb["x"]) ** 2 + (pa["y"] - pb["y"]) ** 2
            if best is None or d < best[0]:
                best = (d, pa, pb)
    return best


def _connect_net(nc, nm):
    """Greedily connect all clusters of net nc. Returns (added_segs, added_vias, log)."""
    log = []
    added_segs = 0; added_vias = 0
    for _ in range(64):  # bounded merge loop
        clusters = _cluster(nc)
        if len(clusters) <= 1:
            break
        # closest pair of clusters
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                cp = _closest_pad_pair(clusters[i], clusters[j])
                if cp is None:
                    continue
                if best is None or cp[0] < best[0]:
                    best = (cp[0], cp[1], cp[2])
        if best is None:
            log.append(f"  {nm}: no share-layer pad pair between {len(clusters)} clusters")
            break
        _, pa, pb = best
        ax, ay, bx, by = pa["x"], pa["y"], pb["x"], pb["y"]
        layers = _shared_signal_layers(pa.get("layers"), pb.get("layers"))
        done = None
        used = "Manhattan"
        # Z's jog scan is O(span/0.2mm) per layer -- cheap only for short nets.
        # For long nets skip it and let the A* (bounded, subsumes Z) do the work.
        short = max(abs(bx - ax), abs(by - ay)) < int(8 * NM)
        for L in layers:
            done = _try_L(ax, ay, bx, by, L, nc)
            if not done and short:
                done = _try_Z(ax, ay, bx, by, L, nc)
            if done:
                break
            done = _try_astar2d(ax, ay, bx, by, L, nc)
            if done:
                used = "A*2D"
                break
        if done:
            for s in done:
                _add_trk(*s, nc)
            added_segs += len(done)
            log.append(f"  {nm}: {len(done)}-seg {used} on {LNAME[L]} "
                       f"({ax/NM:.1f},{ay/NM:.1f})->({bx/NM:.1f},{by/NM:.1f})")
            continue
        # via-bridge fallback: use the two pads' own layers
        alay = pa.get("layers"); blay = pb.get("layers")
        aL = F_Cu if (alay is None or F_Cu in alay) else (next(iter(alay)) if alay else F_Cu)
        bL = F_Cu if (blay is None or F_Cu in blay) else (next(iter(blay)) if blay else F_Cu)
        vb = _try_via_bridge(ax, ay, bx, by, aL, bL, nc)
        if vb:
            segs, vias = vb
            for s in segs:
                _add_trk(*s, nc)
            for (vx, vy) in vias:
                _add_via(vx, vy, nc)
            added_segs += len(segs); added_vias += len(vias)
            log.append(f"  {nm}: via-bridge {len(segs)} segs+{len(vias)} vias "
                       f"({ax/NM:.1f},{ay/NM:.1f})->({bx/NM:.1f},{by/NM:.1f})")
            continue
        log.append(f"  {nm}: SKIP no clear path "
                   f"({ax/NM:.1f},{ay/NM:.1f})->({bx/NM:.1f},{by/NM:.1f})")
        break
    return added_segs, added_vias, log


# ---- target net selection ----
if __name__ == "__main__":
    netnames = {}
    for nm_str, n in B.GetNetsByName().items():
        if n:
            netnames[n.GetNetCode()] = n.GetNetname()

    if only is None:
        # all non-plane split nets
        split = tc._split_nets(bp)
        targets = [nm for nm, cnt in split.items() if cnt > 1]
    else:
        targets = sorted(only)

    print(f"== close_residuals: {bp.name} ==  {len(targets)} target net(s)")
    out = bp.with_suffix(".closed.kicad_pcb")

    # Per-net SIGALRM timeout: a pathological net (huge _collapse_segs O(N^2 *
    # _trk_clear), or a 60k-node A* that won't bail) can hang the whole run for
    # many minutes, and the board only saves at the END -- so one stuck net
    # loses ALL the closures already made. The alarm interrupts the stuck net's
    # pure-Python loop (heap / collapse / clearance checks) so no single net
    # exceeds NET_TIMEOUT; we log + skip it and carry on. Checkpoint-save every
    # CHK nets so a crash/kill mid-run keeps the closures already committed.
    class _NetTimeout(Exception):
        pass

    def _alarm(signum, frame):
        raise _NetTimeout

    signal.signal(signal.SIGALRM, _alarm)
    NET_TIMEOUT = int(os.environ.get("NET_TIMEOUT", "45"))
    CHK = int(os.environ.get("CHECKPOINT", "10"))

    total_segs = 0; total_vias = 0
    for idx, nm in enumerate(targets, 1):
        n = B.FindNet(nm)
        if n is None or n.GetNetCode() < 0:
            print(f"  {nm}: NO NET"); continue
        if nm in tc.PLANE_NETS:
            print(f"  {nm}: plane net (skip)"); continue
        try:
            signal.alarm(NET_TIMEOUT)
            sg, vi, log = _connect_net(n.GetNetCode(), nm)
            signal.alarm(0)
            total_segs += sg; total_vias += vi
            for line in log:
                print(line)
        except _NetTimeout:
            signal.alarm(0)
            print(f"  {nm}: TIMEOUT >{NET_TIMEOUT}s (skipped)")
        except Exception as e:  # noqa: BLE001 -- never let one net kill the run
            signal.alarm(0)
            print(f"  {nm}: ERROR {type(e).__name__}: {e}")
        if idx % CHK == 0:
            B.Save(str(out))
            print(f"  [checkpoint {idx}/{len(targets)}: saved -> {out.name}]")

    B.Save(str(out))
    print(f"== added {total_segs} track seg(s) + {total_vias} via(s) -> {out.name} ==")
    print("NEXT: re-pour planes + DRC + connectivity-check the .closed board.")