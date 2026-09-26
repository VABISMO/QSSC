"""The residual net closer: Manhattan L/Z tracks + 2-D A* + via-bridge fallback.

:class:`ResidualCloser` holds the loaded board, the route config (track/via
widths, clearances, signal layers), and the per-layer blocked-grid obstacle
index, replacing the module globals (``B`` / ``NM`` / ``TRK_W`` / ``REAL_CLEAR``
/ ``SIG_LAYERS`` / the grid-geometry globals) the original
``close_residuals.py`` threaded through every helper. The pure 2-D grid
primitives (rasterisation, A*, path simplification) are on
:class:`tools.router.grid2d.Grid2D`, which this class drives.

For each named net (or every non-plane split net when none are named), cluster
its pads+tracks+vias by copper connectivity; for every pair of unconnected
clusters, draw an L- or Z-shaped track on a shared SIGNAL layer between the
closest pad pair, clearance-checked against all other-net copper at 0.17 mm.
Falls back to a through-via + inner-layer hop when no same-layer Manhattan path
clears. This is the right tool for the short 2-/3-pin SMD residuals FreeRouting
leaves when its optimizer stalls; run AFTER FreeRouting + maze_bridge_3d, then
re-pour planes.
"""
from __future__ import annotations

import math

import numpy as np
import pcbnew

from geom import (
    seg_dist2 as _seg_dist2,
    seg_seg_dist2 as _seg_seg_dist2,
    seg_rect_dist2 as _seg_rect_dist2,
    seg_bbox as _seg_bbox,
)
from _kicad_compat import track_width_nm
import test_pcb_connectivity as tc
from .grid2d import Grid2D, simplify


class ResidualCloser:
    """Close residual split/dangling nets on one loaded board with direct
    Manhattan tracks (no autorouter).

    Config (widths / clearances / signal layers) is held as class attributes so
    the routing methods read ``self.TRK_W`` etc. instead of the former module
    globals; the board handle, board rect, the :class:`Grid2D`, and the
    per-layer blocked-grid cache are instance state populated in ``__init__``.
    """

    NM = 1_000_000
    F_Cu = pcbnew.F_Cu
    B_Cu = pcbnew.B_Cu
    # 4 signal layers (In1=GND, In2=+3V3 are poured planes -- never route here).
    SIG_LAYERS = [F_Cu, B_Cu, pcbnew.In3_Cu, pcbnew.In4_Cu]
    LNAME = {F_Cu: "F.Cu", B_Cu: "B.Cu", pcbnew.In3_Cu: "In3", pcbnew.In4_Cu: "In4"}
    TRK_W = int(0.20 * NM)
    REAL_CLEAR = int(0.17 * NM)
    VIA_DIA = int(0.40 * NM)
    VIA_DRILL = int(0.20 * NM)
    KEEP = REAL_CLEAR + TRK_W // 2
    # single-layer 2D A* cell size + board-rect inset margin
    CELL = int(0.10 * NM)
    MARG = int(0.4 * NM)

    def __init__(self, board):
        self.board = board
        bb = board.GetBoardEdgesBoundingBox()
        self.bx0 = bb.GetX(); self.by0 = bb.GetY()
        self.bx1 = self.bx0 + bb.GetWidth(); self.by1 = self.by0 + bb.GetHeight()
        self.grid = Grid2D.from_rect(self.bx0, self.by0, self.bx1, self.by1,
                                     self.CELL, self.MARG)
        self._all_blocked_cache = {}

    # ---- clearance checks (other-net copper only) ----
    def _trk_clear(self, x1, y1, x2, y2, layer, nc):
        """Track on `layer` clear of all OTHER-net copper on that layer (0.17 mm)."""
        hw = self.TRK_W // 2 + self.REAL_CLEAR; hw2 = hw * hw
        bx0, by0, bx1, by1 = _seg_bbox(x1, y1, x2, y2, hw)
        for t in self.board.GetTracks():
            if t.GetNetCode() == nc:
                continue
            if t.GetClass() == "PCB_VIA":
                pos = t.GetPosition()
                top, bot = t.TopLayer(), t.BottomLayer()
                if not (top <= layer <= bot):
                    continue
                rr = hw + track_width_nm(t) // 2
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
                rr = hw + track_width_nm(t) // 2
                if _seg_seg_dist2(x1, y1, x2, y2, s.x, s.y, e.x, e.y) < rr * rr:
                    return False
        for fp in self.board.GetFootprints():
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

    def _via_clear(self, x, y, nc):
        """Through via at (x,y) clear of all OTHER-net copper on every layer."""
        vr = self.VIA_DIA // 2 + self.KEEP; vr2 = vr * vr
        for t in self.board.GetTracks():
            if t.GetNetCode() == nc:
                continue
            if t.GetClass() == "PCB_VIA":
                pos = t.GetPosition(); rr = vr + track_width_nm(t) // 2
                if (pos.x - x) ** 2 + (pos.y - y) ** 2 < rr * rr:
                    return False
            else:
                s = t.GetStart(); e = t.GetEnd(); rr = vr + track_width_nm(t) // 2
                if _seg_dist2(x, y, s.x, s.y, e.x, e.y) < rr * rr:
                    return False
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetCode() == nc:
                    continue
                players = tc._pad_layer_set(p)
                if players is not None and not (self.F_Cu in players or self.B_Cu in players):
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

    def _in_board(self, x, y, marg=None):
        if marg is None:
            marg = self.KEEP
        return self.bx0 + marg <= x <= self.bx1 - marg and self.by0 + marg <= y <= self.by1 - marg

    # ---- cluster pads+tracks+vias by connectivity (reuse tc geometry) ----
    def _cluster(self, nc):
        items = []
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetCode() == nc:
                    pos = p.GetPosition(); bbp = p.GetBoundingBox()
                    items.append(("pad", {"kind": "pad", "x": pos.x, "y": pos.y,
                        "bx0": bbp.GetX(), "by0": bbp.GetY(),
                        "bx1": bbp.GetX() + bbp.GetWidth(), "by1": bbp.GetY() + bbp.GetHeight(),
                        "layers": tc._pad_layer_set(p),
                        "r": min(bbp.GetWidth(), bbp.GetHeight()) // 2, "it": p}))
        for t in self.board.GetTracks():
            if t.GetNetCode() != nc:
                continue
            if t.GetClass() == "PCB_VIA":
                pos = t.GetPosition()
                items.append(("via", {"kind": "via", "x": pos.x, "y": pos.y,
                    "layers": tc._via_layer_set(self.board, t), "r": track_width_nm(t) // 2, "it": t}))
            elif t.Type() == pcbnew.PCB_TRACE_T:
                s = t.GetStart(); e = t.GetEnd()
                items.append(("trk", {"kind": "trk", "ax": s.x, "ay": s.y, "bx": e.x, "by": e.y,
                    "hw": track_width_nm(t) // 2, "layers": {t.GetLayer()}, "it": t}))
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
    def _add_trk(self, x1, y1, x2, y2, layer, nc):
        t = pcbnew.PCB_TRACK(self.board)
        t.SetStart(pcbnew.VECTOR2I(int(x1), int(y1)))
        t.SetEnd(pcbnew.VECTOR2I(int(x2), int(y2)))
        t.SetWidth(self.TRK_W); t.SetLayer(layer); t.SetNetCode(nc); self.board.Add(t); return t

    def _add_via(self, x, y, nc):
        v = pcbnew.PCB_VIA(self.board)
        v.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
        v.SetTopLayer(self.F_Cu); v.SetBottomLayer(self.B_Cu)
        v.SetDrill(self.VIA_DRILL); v.SetWidth(self.VIA_DIA); v.SetNetCode(nc)
        v.SetViaType(pcbnew.VIATYPE_THROUGH); self.board.Add(v); return v

    # ---- blocked-grid obstacle index ----
    def _build_all_blocked_2d(self, layer):
        """numpy uint8 grid [nrow, ncol]: 1 = blocked by ANY copper (all nets) on
        `layer`. Built ONCE per layer and cached -- the expensive O(all-tracks +
        all-pads) sweep, now vectorized with numpy (was >120 s stamping overlapping
        discs along 1300 tracks in pure Python; now a few seconds). Same-net copper
        is left blocked here; per-net we copy + clear the route net's own copper
        (``_unblock_net_np``) so a route may run along its existing tracks. The A*
        force-opens the start/goal pad cells on its copy."""
        grid = np.zeros((self.grid.nrow, self.grid.ncol), dtype=np.uint8)
        keep = self.KEEP
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                players = tc._pad_layer_set(p)
                if players is not None and layer not in players:
                    continue
                i0, i1, j0, j1 = self.grid.pad_slice(p.GetBoundingBox(), keep)
                if i1 >= i0 and j1 >= j0:
                    grid[i0:i1 + 1, j0:j1 + 1] = 1
        for t in self.board.GetTracks():
            if t.GetClass() == "PCB_VIA":
                pos = t.GetPosition()
                top, bot = t.TopLayer(), t.BottomLayer()
                if not (top <= layer <= bot):
                    continue
                self.grid.mark_disc_np(grid, pos.x, pos.y, keep + track_width_nm(t) // 2)
            elif t.GetLayer() == layer:
                s = t.GetStart(); e = t.GetEnd()
                self.grid.mark_seg_np(grid, s.x, s.y, e.x, e.y, keep + track_width_nm(t) // 2)
        return grid

    def _unblock_net_np(self, grid, layer, nc):
        """In-place on a numpy grid COPY: clear nc's own copper on `layer` so a
        route may reuse / run along its existing tracks + pads."""
        keep = self.KEEP
        g = self.grid
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetCode() != nc:
                    continue
                players = tc._pad_layer_set(p)
                if players is not None and layer not in players:
                    continue
                i0, i1, j0, j1 = g.pad_slice(p.GetBoundingBox(), keep)
                if i1 >= i0 and j1 >= j0:
                    grid[i0:i1 + 1, j0:j1 + 1] = 0
        for t in self.board.GetTracks():
            if t.GetNetCode() != nc:
                continue
            if t.GetClass() == "PCB_VIA":
                pos = t.GetPosition()
                top, bot = t.TopLayer(), t.BottomLayer()
                if not (top <= layer <= bot):
                    continue
                rad = keep + track_width_nm(t) // 2
                j0 = max(0, int((pos.x - rad - g.gx0) // g.cell)); j1 = min(g.ncol - 1, int((pos.x + rad - g.gx0) // g.cell))
                i0 = max(0, int((pos.y - rad - g.gy0) // g.cell)); i1 = min(g.nrow - 1, int((pos.y + rad - g.gy0) // g.cell))
                if i1 >= i0 and j1 >= j0:
                    sub_x = g.cxs[j0:j1 + 1]; sub_y = g.cys[i0:i1 + 1]
                    X, Y = np.meshgrid(sub_x, sub_y)
                    d = np.hypot(X - pos.x, Y - pos.y)
                    grid[i0:i1 + 1, j0:j1 + 1] &= ~(d <= rad).astype(np.uint8)
            elif t.GetLayer() == layer:
                s = t.GetStart(); e = t.GetEnd()
                rad = keep + track_width_nm(t) // 2
                dx, dy = e.x - s.x, e.y - s.y; L2 = dx * dx + dy * dy
                j0 = max(0, int((min(s.x, e.x) - rad - g.gx0) // g.cell)); j1 = min(g.ncol - 1, int((max(s.x, e.x) + rad - g.gx0) // g.cell))
                i0 = max(0, int((min(s.y, e.y) - rad - g.gy0) // g.cell)); i1 = min(g.nrow - 1, int((max(s.y, e.y) + rad - g.gy0) // g.cell))
                if i1 < i0 or j1 < j0:
                    continue
                sub_x = g.cxs[j0:j1 + 1]; sub_y = g.cys[i0:i1 + 1]
                X, Y = np.meshgrid(sub_x, sub_y)
                if L2 == 0:
                    d = np.hypot(X - s.x, Y - s.y)
                else:
                    tt = ((X - s.x) * dx + (Y - s.y) * dy) / L2
                    np.clip(tt, 0.0, 1.0, out=tt)
                    d = np.hypot(X - (s.x + tt * dx), Y - (s.y + tt * dy))
                grid[i0:i1 + 1, j0:j1 + 1] &= ~(d <= rad).astype(np.uint8)
        return grid

    def _get_all_blocked(self, layer):
        """Cached numpy uint8 grid [nrow,ncol] of all-copper blocks on `layer`."""
        g = self._all_blocked_cache.get(layer)
        if g is None:
            g = self._build_all_blocked_2d(layer)
            self._all_blocked_cache[layer] = g
        return g

    def _get_blocked(self, layer, nc):
        """Per-net blocked grid as a bytearray: copy the all-copper numpy grid, clear
        this net's copper, then .tobytes() so the pure-Python A* indexes it flat."""
        grid = self._get_all_blocked(layer).copy()
        self._unblock_net_np(grid, layer, nc)
        return bytearray(grid.tobytes())

    def _collapse_segs(self, segs, layer, nc, window=14):
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
                    if self._trk_clear(ax, ay, cx, cy, layer, nc) and \
                       self._trk_clear(cx, cy, bx, by, layer, nc):
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

    def _try_astar2d(self, ax, ay, bx, by, layer, nc):
        """2D A* on `layer` from pad a to pad b. Returns list of segs (x1,y1,x2,y2,layer)."""
        blocked = self._get_blocked(layer, nc)
        sc = self.grid.to_cell(ax, ay); gc_ = self.grid.to_cell(bx, by)
        if not (0 <= sc[0] < self.grid.ncol and 0 <= sc[1] < self.grid.nrow and
                0 <= gc_[0] < self.grid.ncol and 0 <= gc_[1] < self.grid.nrow):
            return None
        path = self.grid.astar2d(blocked, sc, gc_)
        if not path:
            return None
        raw = []
        for (cx1, cy1, cx2, cy2) in simplify(path):
            x1, y1 = self.grid.to_xy(cx1, cy1); x2, y2 = self.grid.to_xy(cx2, cy2)
            if x1 == x2 and y1 == y2:
                continue
            raw.append((x1, y1, x2, y2, layer))
        segs = self._collapse_segs(raw, layer, nc)
        # validate every final segment with the real clearance check
        for (x1, y1, x2, y2, L) in segs:
            if not self._trk_clear(x1, y1, x2, y2, L, nc):
                return None  # grid was conservative but real check fails -> abort
        return segs or None

    # ---- path attempts: L, then Z (scanned jog), then 2D A*, then via-bridge ----
    def _try_L(self, ax, ay, bx, by, layer, nc):
        """Two L orderings: via (bx,ay) or (ax,by). Return list of segs if clear."""
        for cx, cy in ((bx, ay), (ax, by)):
            s1 = (ax, ay, cx, cy, layer); s2 = (cx, cy, bx, by, layer)
            if self._trk_clear(*s1, nc) and self._trk_clear(*s2, nc):
                return [s1, s2]
        return None

    def _try_Z(self, ax, ay, bx, by, layer, nc):
        """Z with a scanned jog. Tries BOTH orientations (vertical jog-bar at
        intermediate x, horizontal jog-bar at intermediate y) so the route can
        escape a blocked pad-row to a clear lane before running across."""
        step = int(0.20 * self.NM)
        # orientation 1: vertical bar at intermediate x  a->(x,ay)->(x,by)->b
        lo, hi = sorted((ax, bx))
        x = lo + step
        while x < hi - step // 2:
            for (jy1, jy2) in ((ay, by), (by, ay)):
                s1 = (ax, ay, x, jy1, layer); s2 = (x, jy1, x, jy2, layer); s3 = (x, jy2, bx, by, layer)
                if self._trk_clear(*s1, nc) and self._trk_clear(*s2, nc) and self._trk_clear(*s3, nc):
                    return [s1, s2, s3]
            x += step
        # orientation 2: horizontal bar at intermediate y  a->(ax,y)->(bx,y)->b
        lo, hi = sorted((ay, by))
        y = lo + step
        while y < hi - step // 2:
            for (jx1, jx2) in ((ax, bx), (bx, ax)):
                s1 = (ax, ay, jx1, y, layer); s2 = (jx1, y, jx2, y, layer); s3 = (jx2, y, bx, by, layer)
                if self._trk_clear(*s1, nc) and self._trk_clear(*s2, nc) and self._trk_clear(*s3, nc):
                    return [s1, s2, s3]
            y += step
        return None

    def _find_via_spot(self, near_x, near_y, nc):
        """Spiral search for a through-via spot clear of copper + inside the board."""
        step = int(0.15 * self.NM)
        for ring in range(1, 16):
            for ang_deg in range(0, 360, 30):
                r = ring * step; a = math.radians(ang_deg)
                vx = int(near_x + r * math.cos(a)); vy = int(near_y + r * math.sin(a))
                if not self._in_board(vx, vy):
                    continue
                if self._via_clear(vx, vy, nc):
                    return vx, vy
        return None

    def _try_via_bridge(self, ax, ay, bx, by, alay, blay, nc):
        """a(on alay)->via_a->straight on an inner layer->via_b->b(on blay)."""
        inner_choices = [L for L in self.SIG_LAYERS if L not in (alay, blay)] or [pcbnew.In3_Cu]
        va = self._find_via_spot(ax, ay, nc)
        if va is None:
            return None
        vb = self._find_via_spot(bx, by, nc)
        if vb is None:
            return None
        segs = []; vias = [va, vb]
        # a pad -> via_a on the pad's own layer
        s0 = (ax, ay, va[0], va[1], alay)
        if not self._trk_clear(*s0, nc):
            return None
        segs.append(s0)
        for inner in inner_choices:
            s1 = (va[0], va[1], vb[0], vb[1], inner)
            if self._trk_clear(*s1, nc):
                segs.append(s1)
                break
        else:
            return None
        s2 = (vb[0], vb[1], bx, by, blay)
        if not self._trk_clear(*s2, nc):
            return None
        segs.append(s2)
        return (segs, vias)

    def _shared_signal_layers(self, la, lb):
        """Signal layers shared by both pad layer-sets (None=all). Intersect with SIG."""
        cand = set(self.SIG_LAYERS)
        a = cand if la is None else (set(la) & cand)
        b = cand if lb is None else (set(lb) & cand)
        common = a & b
        # prefer the pads' own surface layer first (shortest, no via), then the other
        order = []
        for L in (self.F_Cu, self.B_Cu, pcbnew.In3_Cu, pcbnew.In4_Cu):
            if L in common and L not in order:
                order.append(L)
        return order

    def _closest_pad_pair(self, clA, clB):
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

    def _connect_net(self, nc, nm):
        """Greedily connect all clusters of net nc. Returns (added_segs, added_vias, log)."""
        log = []
        added_segs = 0; added_vias = 0
        for _ in range(64):  # bounded merge loop
            clusters = self._cluster(nc)
            if len(clusters) <= 1:
                break
            # closest pair of clusters
            best = None
            for i in range(len(clusters)):
                for j in range(i + 1, len(clusters)):
                    cp = self._closest_pad_pair(clusters[i], clusters[j])
                    if cp is None:
                        continue
                    if best is None or cp[0] < best[0]:
                        best = (cp[0], cp[1], cp[2])
            if best is None:
                log.append(f"  {nm}: no share-layer pad pair between {len(clusters)} clusters")
                break
            _, pa, pb = best
            ax, ay, bx, by = pa["x"], pa["y"], pb["x"], pb["y"]
            layers = self._shared_signal_layers(pa.get("layers"), pb.get("layers"))
            done = None
            used = "Manhattan"
            # Z's jog scan is O(span/0.2mm) per layer -- cheap only for short nets.
            # For long nets skip it and let the A* (bounded, subsumes Z) do the work.
            short = max(abs(bx - ax), abs(by - ay)) < int(8 * self.NM)
            for L in layers:
                done = self._try_L(ax, ay, bx, by, L, nc)
                if not done and short:
                    done = self._try_Z(ax, ay, bx, by, L, nc)
                if done:
                    break
                done = self._try_astar2d(ax, ay, bx, by, L, nc)
                if done:
                    used = "A*2D"
                    break
            if done:
                for s in done:
                    self._add_trk(*s, nc)
                added_segs += len(done)
                log.append(f"  {nm}: {len(done)}-seg {used} on {self.LNAME[L]} "
                           f"({ax/self.NM:.1f},{ay/self.NM:.1f})->({bx/self.NM:.1f},{by/self.NM:.1f})")
                continue
            # via-bridge fallback: use the two pads' own layers
            alay = pa.get("layers"); blay = pb.get("layers")
            aL = self.F_Cu if (alay is None or self.F_Cu in alay) else (next(iter(alay)) if alay else self.F_Cu)
            bL = self.F_Cu if (blay is None or self.F_Cu in blay) else (next(iter(blay)) if blay else self.F_Cu)
            vb = self._try_via_bridge(ax, ay, bx, by, aL, bL, nc)
            if vb:
                segs, vias = vb
                for s in segs:
                    self._add_trk(*s, nc)
                for (vx, vy) in vias:
                    self._add_via(vx, vy, nc)
                added_segs += len(segs); added_vias += len(vias)
                log.append(f"  {nm}: via-bridge {len(segs)} segs+{len(vias)} vias "
                           f"({ax/self.NM:.1f},{ay/self.NM:.1f})->({bx/self.NM:.1f},{by/self.NM:.1f})")
                continue
            log.append(f"  {nm}: SKIP no clear path "
                       f"({ax/self.NM:.1f},{ay/self.NM:.1f})->({bx/self.NM:.1f},{by/self.NM:.1f})")
            break
        return added_segs, added_vias, log