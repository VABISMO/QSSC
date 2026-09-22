"""Pure 3-D (multi-layer) A* maze-search primitives.

``astar3d`` searches a 4-layer routing grid (layer index, cell-x, cell-y) for a
shortest path from any start cell to any goal cell, hopping layers at
via-clearable cells for a ``via_cost`` penalty (one through-hole via per hop).
``simplify3d`` drops intermediate cells that are collinear on the same layer.

Both functions are board-agnostic: the caller passes the grid dimensions
(``ncol`` / ``nrow`` / ``nl``), the per-layer blocked-cell bytearrays, the
via-clearable bytearray, and the start/goal cell lists. They hold no pcbnew
dependency and no module-level state, so they are independently testable and
reusable -- the analogue of :mod:`tools.router.grid2d` for the single-layer 2-D
A* used by the residual closer.
"""
from __future__ import annotations

import heapq
import math

# A* node-expansion budget. A pathological net (a fully blocked layer field with
# one winding gap) can expand tens of millions of cells before exhausting; cap it
# so the search returns None instead of consuming the whole run. Overridable via
# the MAZE_MAXNODES env by the caller if desired.
_DEFAULT_MAXNODES = 12_000_000


def astar3d(blocked_layers, via_clear, starts, goals, ncol, nrow, nl,
            via_cost=4.0, max_nodes=_DEFAULT_MAXNODES):
    """3-D A* over (layer_idx, cx, cy). Layer transitions at via-clear cells.

    ``blocked_layers`` is a list of ``nl`` bytearrays of length ``ncol * nrow``
    (1 = blocked on that layer). ``via_clear`` is one bytearray of the same
    length (0 = a through-hole via can be placed at that cell). ``starts`` /
    ``goals`` are lists of ``(li, cx, cy)`` tuples. Returns the path as a list
    of ``(li, cx, cy)`` cells from the reached start to the reached goal, or
    ``None`` if no path exists within the ``max_nodes`` expansion budget.
    """
    INF = 1 << 30
    g = {}; came = {}
    pq = []
    for s in starts:
        g[s] = 0.0
        sx, sy = s[1], s[2]
        # heuristic: min 2D dist to any goal cell (layer-agnostic)
        h = min(math.hypot(sx - gg[1], sy - gg[2]) for gg in goals)
        heapq.heappush(pq, (h, s))
    goals_g = {gg: True for gg in goals}
    nodes = 0
    while pq:
        _, cur = heapq.heappop(pq)
        if cur in goals_g:
            path = [cur]
            while cur in came:
                cur = came[cur]; path.append(cur)
            return path[::-1]
        li, cx, cy = cur; gc = g[cur]
        bl = blocked_layers[li]
        # 8-connected same-layer
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < ncol and 0 <= ny < nrow): continue
            if bl[ny * ncol + nx]: continue
            if dx and dy:
                if bl[cy * ncol + nx] or bl[ny * ncol + cx]: continue
            step = 1.4142 if (dx and dy) else 1.0
            nb = (li, nx, ny)
            ng = gc + step
            if ng < g.get(nb, INF):
                g[nb] = ng; came[nb] = cur
                h = min(math.hypot(nx - gg[1], ny - gg[2]) for gg in goals)
                heapq.heappush(pq, (ng + h, nb))
                nodes += 1
                if nodes > max_nodes: return None
        # layer transitions at this cell (if via-clearable here)
        ci = cy * ncol + cx
        if via_clear[ci] == 0:   # 0 = clearable
            for nl_ in range(nl):
                if nl_ == li: continue
                nb = (nl_, cx, cy)
                ng = gc + via_cost
                if ng < g.get(nb, INF):
                    g[nb] = ng; came[nb] = cur
                    h = min(math.hypot(cx - gg[1], cy - gg[2]) for gg in goals)
                    heapq.heappush(pq, (ng + h, nb))
                    nodes += 1
                    if nodes > max_nodes: return None
    return None


def simplify3d(path):
    """Drop intermediate cells that are collinear AND on the same layer."""
    if len(path) <= 2: return path
    out = [path[0]]
    for i in range(1, len(path) - 1):
        p0 = out[-1]; p1 = path[i]; p2 = path[i + 1]
        if p0[0] == p1[0] == p2[0]:   # same layer
            x0, y0 = p0[1], p0[2]; x1, y1 = p1[1], p1[2]; x2, y2 = p2[1], p2[2]
            if (x2 - x0) * (y1 - y0) == (x1 - x0) * (y2 - y0) and \
               (x2 - x0) * (x1 - x0) >= 0 and (y2 - y0) * (y1 - y0) >= 0:
                continue
        out.append(path[i])
    out.append(path[-1])
    return out