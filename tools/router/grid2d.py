"""Pure 2-D grid primitives for the residual closer.

:class:`Grid2D` holds only the grid geometry -- origin, cell size, dims, and the
coordinate arrays the vectorised rasterisers index -- plus the single-layer A*
search and the grid-path simplifier. It has NO board dependency: the
board-aware all-copper / per-net blocked-grid builders live on
:class:`tools.router.manhattan.ResidualCloser`, which drives a ``Grid2D`` to
rasterise pads/tracks and to search.

Split out of ``close_residuals.py`` (M5.4) so the pure grid search/raster code
is independently testable and the board/clearance state is not threaded through
it as module globals. ``astar2d`` and :func:`simplify` are pure (take the
blocked grid / cell list as args); ``mark_seg_np`` / ``mark_disc_np`` /
``pad_slice`` only touch the grid geometry.
"""
from __future__ import annotations

import heapq
import os

import numpy as np


def simplify(cells):
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


class Grid2D:
    """A regular 2-D grid over a board-rect sub-window.

    Cells are ``cell`` nm wide; the grid spans
    ``[gx0, gx0 + ncol*cell] x [gy0, gy0 + nrow*cell]``. ``cxs`` / ``cys`` are
    the precomputed cell-centre coordinate arrays the vectorised rasterisers
    index (built once at construction -- rebuilding per track was the hot spot
    of the original per-disc stamping loop)."""

    def __init__(self, gx0, gy0, cell, ncol, nrow):
        self.gx0 = gx0
        self.gy0 = gy0
        self.cell = cell
        self.ncol = ncol
        self.nrow = nrow
        self.cxs = gx0 + np.arange(ncol) * cell
        self.cys = gy0 + np.arange(nrow) * cell

    @classmethod
    def from_rect(cls, x0, y0, x1, y1, cell, marg):
        """Grid spanning [x0,y0]-[x1,y1] inset by ``marg`` on every side."""
        gx0, gy0 = x0 + marg, y0 + marg
        gx1, gy1 = x1 - marg, y1 - marg
        ncol = (gx1 - gx0) // cell + 1
        nrow = (gy1 - gy0) // cell + 1
        return cls(gx0, gy0, cell, ncol, nrow)

    def to_cell(self, x, y):
        return (x - self.gx0) // self.cell, (y - self.gy0) // self.cell

    def to_xy(self, cx, cy):
        return self.gx0 + cx * self.cell, self.gy0 + cy * self.cell

    def mark_seg_np(self, grid, sx, sy, ex, ey, rad):
        """Vectorized thick-segment rasterization: mark every cell whose center is
        within ``rad`` of the segment (sx,sy)-(ex,ey). One numpy op per track
        instead of stamping hundreds of overlapping discs -> ~50x faster on 1300
        tracks."""
        dx, dy = ex - sx, ey - sy
        L2 = dx * dx + dy * dy
        j0 = int((min(sx, ex) - rad - self.gx0) // self.cell)
        j1 = int((max(sx, ex) + rad - self.gx0) // self.cell)
        i0 = int((min(sy, ey) - rad - self.gy0) // self.cell)
        i1 = int((max(sy, ey) + rad - self.gy0) // self.cell)
        j0 = max(0, j0); j1 = min(self.ncol - 1, j1)
        i0 = max(0, i0); i1 = min(self.nrow - 1, i1)
        if j1 < j0 or i1 < i0:
            return
        sub_x = self.cxs[j0:j1 + 1]
        sub_y = self.cys[i0:i1 + 1]
        X, Y = np.meshgrid(sub_x, sub_y)
        if L2 == 0:
            d = np.hypot(X - sx, Y - sy)
        else:
            t = ((X - sx) * dx + (Y - sy) * dy) / L2
            np.clip(t, 0.0, 1.0, out=t)
            d = np.hypot(X - (sx + t * dx), Y - (sy + t * dy))
        grid[i0:i1 + 1, j0:j1 + 1] |= (d <= rad)

    def mark_disc_np(self, grid, x, y, rad):
        """Vectorized disc rasterization (for vias / round pads)."""
        j0 = max(0, int((x - rad - self.gx0) // self.cell)); j1 = min(self.ncol - 1, int((x + rad - self.gx0) // self.cell))
        i0 = max(0, int((y - rad - self.gy0) // self.cell)); i1 = min(self.nrow - 1, int((y + rad - self.gy0) // self.cell))
        if j1 < j0 or i1 < i0:
            return
        sub_x = self.cxs[j0:j1 + 1]; sub_y = self.cys[i0:i1 + 1]
        X, Y = np.meshgrid(sub_x, sub_y)
        d = np.hypot(X - x, Y - y)
        grid[i0:i1 + 1, j0:j1 + 1] |= (d <= rad)

    def pad_slice(self, pb, keep):
        """Bbox cell slice [i0:i1+1, j0:j1+1] for a pad bounding box expanded by keep."""
        j0 = max(0, int((pb.GetX() - keep - self.gx0) // self.cell))
        j1 = min(self.ncol - 1, int((pb.GetX() + pb.GetWidth() + keep - self.gx0) // self.cell))
        i0 = max(0, int((pb.GetY() - keep - self.gy0) // self.cell))
        i1 = min(self.nrow - 1, int((pb.GetY() + pb.GetHeight() + keep - self.gy0) // self.cell))
        return i0, i1, j0, j1

    def astar2d(self, blocked, start, goal):
        """4-connectivity A* over the blocked grid, restricted to a search window
        around start+goal (pad bbox + ASTAR_MARGIN cells). Restricting the window
        keeps the search out of the 1.1 M-cell board when two pads are close -- a
        full-board A* crawls hundreds of thousands of cells in pure Python; the
        window caps a 10 mm net to ~25k cells. Returns list of (cx,cy) or None."""
        sx, sy = start; gx, gy = goal
        if not (0 <= sx < self.ncol and 0 <= sy < self.nrow and 0 <= gx < self.ncol and 0 <= gy < self.nrow):
            return None
        blocked = bytearray(blocked)  # private copy so force-open doesn't mutate cache
        M = int(os.environ.get("ASTAR_MARGIN", "80"))  # 8 mm detour room
        wj0 = max(0, min(sx, gx) - M); wj1 = min(self.ncol - 1, max(sx, gx) + M)
        wi0 = max(0, min(sy, gy) - M); wi1 = min(self.nrow - 1, max(sy, gy) + M)
        si = sy * self.ncol + sx; gi = gy * self.ncol + gx
        blocked[si] = 0; blocked[gi] = 0
        N = self.ncol * self.nrow
        g = [-1] * N; g[si] = 0
        came = [-1] * N
        closed = bytearray(N)
        h = lambda ci: abs(ci % self.ncol - gx) + abs(ci // self.ncol - gy)
        open_h = [(h(si), 0, si)]
        budget = int(os.environ.get("ASTAR_BUDGET", "60000"))
        expansions = 0
        while open_h:
            _, gc, ci = heapq.heappop(open_h)
            if ci == gi:
                path = [(ci % self.ncol, ci // self.ncol)]
                while came[ci] != -1:
                    ci = came[ci]; path.append((ci % self.ncol, ci // self.ncol))
                path.reverse(); return path
            if closed[ci]:
                continue
            closed[ci] = 1
            expansions += 1
            if expansions > budget:
                return None  # hopeless (congested long net) -- bail, don't crawl
            cx, cy = ci % self.ncol, ci // self.ncol
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if not (wj0 <= nx <= wj1 and wi0 <= ny <= wi1):
                    continue
                ni = ny * self.ncol + nx
                if blocked[ni]:
                    continue
                ng = gc + 1
                if g[ni] == -1 or ng < g[ni]:
                    g[ni] = ng; came[ni] = ci
                    heapq.heappush(open_h, (ng + h(ni), ng, ni))
        return None