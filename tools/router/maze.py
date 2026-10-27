"""The 3-D maze bridge: multi-layer A* closure of residual split nets.

:class:`MazeBridge3D` holds the loaded board, the route config (via/track/cell
sizes read from the ``MAZE_*`` environment), the 4-signal-layer 3-D routing
grid, and the per-net pad/track/via index -- replacing the module globals
(``b`` / ``KEEP_POWER`` / ``by_net`` / ``netnames`` / ``GX0`` / ``NCOL`` ...) the
original ``maze_bridge_3d.py`` threaded through every helper. The pure 3-D A*
search + path simplification are on :mod:`tools.router.astar3d`, which this
class drives.

Routes a single named net across the 4 signal layers (B.Cu, In3, In4, F.Cu) with
through-hole via transitions at clear cells. Routes ONLY the named net -- it
does NOT re-fanout or disturb any other net (unlike FreeRouting, which re-fans-
out every SMD pin). Endpoint vias are placed by this class (spiral clear-spot
search, deferred until a path is confirmed).

The single-layer ``head_maze_bridge.py`` could not close the 106 mm DET_IN10 net
on any one layer (via field fully blocks In4; In3 path genuinely breaches
0.17 mm; B.Cu blocked by J12). A 3-D search that hops layers around the
congestion is the principled closure. Run AFTER FreeRouting; re-pour planes
after. Non-destructive: the thin ``maze_bridge_3d.py`` shim writes
``<board>.maze3d.kicad_pcb``.
"""
from __future__ import annotations

import math
import os

import pcbnew

from geom import (
    seg_dist2 as _seg_dist2,
    seg_seg_dist2 as _seg_seg_dist2,
    seg_rect_dist2 as _seg_rect_dist2,
)
from _kicad_compat import track_width_nm
import test_pcb_connectivity as tc
from .astar3d import astar3d, simplify3d


def _ref(pad):
    fp = pad.GetParentFootprint() if hasattr(pad, "GetParentFootprint") else pad.GetParent()
    try:
        return fp.GetReference() if fp else "?"
    except Exception:
        return "?"


class MazeBridge3D:
    """Close residual split nets on one loaded board with a 3-D multi-layer A*.

    Config (via/track/cell sizes, clearances, signal layers, via-cost) is held as
    class attributes -- read from the ``MAZE_*`` environment at class-definition
    (import) time, exactly as the original module read them at module load -- so
    the routing methods read ``self.TRK_W`` etc. instead of the former module
    globals. The board handle, board rect, the 3-D grid origin/dims, the per-net
    index, and the keep-power set are instance state populated in ``__init__`` /
    :meth:`build_index`.
    """

    NM = 1_000_000
    F_Cu = pcbnew.F_Cu
    B_Cu = pcbnew.B_Cu
    # 4 signal layers (In1=GND, In2=+3V3 are poured planes -- never route here).
    SIG_LAYERS = [B_Cu, pcbnew.In3_Cu, pcbnew.In4_Cu, F_Cu]
    LNAME = {B_Cu: "B.Cu", pcbnew.In3_Cu: "In3", pcbnew.In4_Cu: "In4", F_Cu: "F.Cu"}
    NL = len(SIG_LAYERS)
    VIA_DIA = int(float(os.environ.get("MAZE_VIA_DIA", "0.40")) * NM)
    VIA_DRILL = int(float(os.environ.get("MAZE_VIA_DRILL", "0.20")) * NM)
    TRK_W = int(float(os.environ.get("MAZE_TRK_W", "0.20")) * NM)
    CLEAR = int(float(os.environ.get("MAZE_CLEAR", "0.16")) * NM)
    HALF_W = TRK_W // 2
    KEEP = CLEAR + HALF_W
    REAL_CLEAR = int(float(os.environ.get("MAZE_REAL_CLEAR", "0.17")) * NM)
    CELL = int(float(os.environ.get("MAZE_CELL", "0.10")) * NM)
    VIA_COST = 4.0   # penalty for one layer transition (a through-hole via)
    MARG = int(0.5 * NM)

    def __init__(self, board, keep_power=None):
        self.board = board
        bb = board.GetBoardEdgesBoundingBox()
        X0, Y0 = bb.GetX(), bb.GetY()
        X1, Y1 = X0 + bb.GetWidth(), Y0 + bb.GetHeight()
        self.GX0, self.GY0 = X0 + self.MARG, Y0 + self.MARG
        self.GX1, self.GY1 = X1 - self.MARG, Y1 - self.MARG
        self.NCOL = (self.GX1 - self.GX0) // self.CELL + 1
        self.NROW = (self.GY1 - self.GY0) // self.CELL + 1
        # per-net index populated by build_index()
        self.by_net = {}
        self.netnames = {}
        self.KEEP_POWER = set(keep_power) if keep_power else set()

    # ---- grid cell mapping ----
    def to_cell(self, x, y):
        return (x - self.GX0) // self.CELL, (y - self.GY0) // self.CELL

    def to_xy(self, cx, cy):
        return self.GX0 + cx * self.CELL, self.GY0 + cy * self.CELL

    # ---- per-net index ----
    def build_index(self):
        """Group every pad/track/via by netcode + snapshot netcode->netname."""
        by_net = {}
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                nc = p.GetNetCode()
                if nc <= 0:
                    continue
                by_net.setdefault(nc, []).append(("pad", p))
        for t in self.board.GetTracks():
            nc = t.GetNetCode()
            if nc <= 0:
                continue
            by_net.setdefault(nc, []).append(
                ("via", t) if t.GetClass() == "PCB_VIA" else ("trk", t))
        self.by_net = by_net
        self.netnames = {c: ni.GetNetname()
                         for c, ni in self.board.GetNetInfo().NetsByNetcode().items()}

    # ---- pad/via/track clustering by copper connectivity ----
    def cluster_pads_and_vias(self, items):
        n = len(items)
        # ---- zone fragments as copper nodes (zone-aware clustering) ----
        # A poured plane's filled regions are copper too (same rationale as the
        # residual closer's _cluster): without them a GND plane shreds into
        # hundreds of false splits and the bridge targets already-poured pairs
        # instead of the genuinely stranded fragments. Nets without zones add
        # no nodes -- signal-net behavior is unchanged.
        nc0 = items[0][1].GetNetCode() if items else -1
        frags = []  # (layer, polyset, outline idx, outline pts, bbox)
        for z in self.board.Zones():
            if z.GetNetCode() != nc0:
                continue
            for lyr in z.GetLayerSet().CuStack():
                poly = z.GetFilledPolysList(lyr)
                for fi in range(poly.OutlineCount()):
                    o = poly.Outline(fi)
                    bb = o.BBox()
                    pts = [(o.CPoint(k).x, o.CPoint(k).y)
                           for k in range(o.PointCount())]
                    if len(pts) < 3:
                        continue
                    frags.append((lyr, poly, fi, pts,
                                  (bb.GetX(), bb.GetY(),
                                   bb.GetX() + bb.GetWidth(),
                                   bb.GetY() + bb.GetHeight())))
        uf = tc._UF(n + len(frags)); geom = []
        for kind, it in items:
            if kind == "pad":
                pos = it.GetPosition(); bbp = it.GetBoundingBox()
                geom.append({"kind": "pad", "x": pos.x, "y": pos.y,
                    "bx0": bbp.GetX(), "by0": bbp.GetY(),
                    "bx1": bbp.GetX() + bbp.GetWidth(), "by1": bbp.GetY() + bbp.GetHeight(),
                    "layers": tc._pad_layer_set(it), "r": min(bbp.GetWidth(), bbp.GetHeight()) // 2,
                    "it": it, "ref": _ref(it), "pad": it.GetPadName()})
            elif kind == "via":
                pos = it.GetPosition()
                geom.append({"kind": "via", "x": pos.x, "y": pos.y, "layers": tc._via_layer_set(self.board, it),
                    "r": track_width_nm(it) // 2, "it": it})
            else:
                s = it.GetStart(); e = it.GetEnd()
                geom.append({"kind": "trk", "ax": s.x, "ay": s.y, "bx": e.x, "by": e.y,
                    "hw": track_width_nm(it) // 2, "layers": {it.GetLayer()}, "it": it})

        def _edge2(ax, ay, bx, by, pts):
            m = None
            for k in range(len(pts)):
                x1, y1 = pts[k]; x2, y2 = pts[(k + 1) % len(pts)]
                d = tc._seg_seg_dist2(ax, ay, bx, by, x1, y1, x2, y2)
                if m is None or d < m:
                    m = d
            return m

        def _pt_edge2(px, py, pts):
            m = None
            for k in range(len(pts)):
                x1, y1 = pts[k]; x2, y2 = pts[(k + 1) % len(pts)]
                d = tc._seg_point_dist2(x1, y1, x2, y2, px, py)
                if m is None or d < m:
                    m = d
            return m

        for i, g in enumerate(geom):
            gl = g.get("layers")
            for fi, (lyr, poly, fidx, pts, fbb) in enumerate(frags):
                if g["kind"] == "trk":
                    if lyr not in gl:
                        continue
                    if (max(g["ax"], g["bx"]) + g["hw"] < fbb[0]
                            or min(g["ax"], g["bx"]) - g["hw"] > fbb[2]
                            or max(g["ay"], g["by"]) + g["hw"] < fbb[1]
                            or min(g["ay"], g["by"]) - g["hw"] > fbb[3]):
                        continue
                    hit = (poly.Contains(pcbnew.VECTOR2I(int(g["ax"]), int(g["ay"])), fidx)
                           or poly.Contains(pcbnew.VECTOR2I(int(g["bx"]), int(g["by"])), fidx)
                           or (_edge2(g["ax"], g["ay"], g["bx"], g["by"], pts) or 0)
                           <= g["hw"] * g["hw"])
                else:
                    if gl is not None and lyr not in gl:
                        continue
                    px, py, reach = g["x"], g["y"], g["r"]
                    if (px < fbb[0] - reach or px > fbb[2] + reach
                            or py < fbb[1] - reach or py > fbb[3] + reach):
                        continue
                    hit = (poly.Contains(pcbnew.VECTOR2I(int(px), int(py)), fidx)
                           or (_pt_edge2(px, py, pts) or 0) <= reach * reach)
                if hit:
                    uf.union(i, n + fi)

        for i in range(n):
            for j in range(i + 1, n):
                if tc._layers_share(geom[i].get("layers"), geom[j].get("layers")) and tc._touch(geom[i], geom[j]):
                    uf.union(i, j)
        cl = {}
        for i, g in enumerate(geom):
            cl.setdefault(uf.find(i), {"pads": [], "vias": [], "trks": []})
            cl[uf.find(i)]["pads" if g["kind"] == "pad" else "vias" if g["kind"] == "via" else "trks"].append(g)
        return [c for c in cl.values() if c["pads"]], geom

    # ---- track / via emission ----
    def add_via(self, x, y, nc):
        v = pcbnew.PCB_VIA(self.board)
        v.SetPosition(pcbnew.VECTOR2I(x, y))
        v.SetTopLayer(self.F_Cu); v.SetBottomLayer(self.B_Cu)
        v.SetDrill(self.VIA_DRILL); v.SetWidth(self.VIA_DIA); v.SetNetCode(nc)
        v.SetViaType(pcbnew.VIATYPE_THROUGH); self.board.Add(v); return v

    def add_trk(self, x1, y1, x2, y2, layer, nc):
        t = pcbnew.PCB_TRACK(self.board)
        t.SetStart(pcbnew.VECTOR2I(x1, y1)); t.SetEnd(pcbnew.VECTOR2I(x2, y2))
        t.SetWidth(self.TRK_W); t.SetLayer(layer); t.SetNetCode(nc); self.board.Add(t); return t

    # ---- clearance checks (other-net copper only) ----
    def _via_clear(self, x, y, nc):
        """Through-hole via at (x,y) clear of all OTHER-net copper on all layers?"""
        # via radius + REAL_CLEAR only: KEEP already embeds TRK_W/2 for the
        # track-path grids, and adding it again (plus the track half below)
        # overstated every via spot by ~TRK_W/2 and rejected legal sites.
        vr = self.VIA_DIA // 2 + self.REAL_CLEAR; vr2 = vr * vr
        for t in self.board.GetTracks():
            if t.GetNetCode() == nc: continue
            if t.GetClass() == "PCB_VIA":
                pos = t.GetPosition(); rr = vr + track_width_nm(t) // 2
                if (pos.x - x) ** 2 + (pos.y - y) ** 2 < rr * rr: return False
            else:
                s = t.GetStart(); e = t.GetEnd(); rr = vr + track_width_nm(t) // 2
                if _seg_dist2(x, y, s.x, s.y, e.x, e.y) < rr * rr: return False
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetCode() == nc: continue
                players = tc._pad_layer_set(p)   # None = drilled -> ALL layers
                if players is not None and not (self.F_Cu in players or self.B_Cu in players): continue
                if (p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
                        and not list(p.GetLayerSet().CuStack())):
                    # NPTH has no copper: the hole-to-hole loop below already
                    # covers its drill; there is no copper rect to clear.
                    continue
                pb = p.GetBoundingBox()
                if (pb.GetX() - vr) <= x <= (pb.GetX() + pb.GetWidth() + vr) and \
                   (pb.GetY() - vr) <= y <= (pb.GetY() + pb.GetHeight() + vr):
                    cx0, cy0 = pb.GetX(), pb.GetY()
                    cx1, cy1 = pb.GetX() + pb.GetWidth(), pb.GetY() + pb.GetHeight()
                    ddx = max(cx0 - x, 0, x - cx1); ddy = max(cy0 - y, 0, y - cy1)
                    if ddx * ddx + ddy * ddy < vr2: return False
        # hole-to-hole: DRC's min_hole_to_hole applies between ANY two drills --
        # same net included. The copper loops above skip same-net items, so
        # without this a new via can land on an existing same-net drill
        # (hole_to_hole, actual 0.0 mm). Centre floor = new drill/2 + existing
        # drill/2 + 0.21 mm (board min 0.2, +10 um over KiCad's 0.1995).
        r_new = self.VIA_DRILL // 2
        gap = 210_000
        for t in self.board.GetTracks():
            if t.GetClass() != "PCB_VIA":
                continue
            pos = t.GetPosition()
            rr = r_new + t.GetDrillValue() // 2 + gap
            if (pos.x - x) ** 2 + (pos.y - y) ** 2 < rr * rr:
                return False
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                dr = p.GetDrillSize()
                if dr.x <= 0 and dr.y <= 0:
                    continue
                pos = p.GetPosition()
                rr = r_new + max(dr.x, dr.y) // 2 + gap
                if (pos.x - x) ** 2 + (pos.y - y) ** 2 < rr * rr:
                    return False
        return True

    def _trk_clear(self, x1, y1, x2, y2, layer, nc):
        """Track on `layer` clear of other-net copper on that layer (REAL DRC clearance)."""
        hw = self.TRK_W // 2 + self.REAL_CLEAR; hw2 = hw * hw
        for t in self.board.GetTracks():
            if t.GetNetCode() == nc: continue
            if t.GetClass() == "PCB_VIA":
                pos = t.GetPosition()
                # IsOnLayer(): numeric inner-layer IDs (In3=8 under F=0/B=2)
                # are outside [TopLayer(), BottomLayer()], so a span comparison
                # silently skipped through-vias for inner-layer tracks -> the
                # validation passed and DRC found the short.
                if not t.IsOnLayer(layer):
                    continue
                rr = hw + track_width_nm(t) // 2
                if _seg_dist2(pos.x, pos.y, x1, y1, x2, y2) < rr * rr: return False
            else:
                if t.GetLayer() != layer: continue
                s = t.GetStart(); e = t.GetEnd(); rr = hw + track_width_nm(t) // 2
                if _seg_seg_dist2(x1, y1, x2, y2, s.x, s.y, e.x, e.y) < rr * rr: return False
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetCode() == nc: continue
                players = tc._pad_layer_set(p)
                if players is not None and layer not in players: continue
                if (p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
                        and not list(p.GetLayerSet().CuStack())):
                    # NPTH: no copper -- only the hole constrains, at
                    # hole_clearance (0.15 mm), not a copper rect + copper gap.
                    dr = p.GetDrillSize()
                    pos = p.GetPosition()
                    need = max(dr.x, dr.y) // 2 + 150_000 + self.TRK_W // 2
                    if _seg_dist2(pos.x, pos.y, x1, y1, x2, y2) < need * need:
                        return False
                    continue
                pb = p.GetBoundingBox()
                rx0, ry0 = pb.GetX(), pb.GetY()
                rx1, ry1 = pb.GetX() + pb.GetWidth(), pb.GetY() + pb.GetHeight()
                if _seg_rect_dist2(x1, y1, x2, y2, rx0, ry0, rx1, ry1) < hw2: return False
        return True

    def _find_clear_via_spot(self, cluster, nc):
        pads = cluster["pads"]
        px = sum(p["x"] for p in pads) // len(pads)
        py = sum(p["y"] for p in pads) // len(pads)
        # through-hole / drilled pads report layers=None (= ALL layers); route the
        # pad->via track on the bottom (valid for an all-layer pad), else the first
        # SMD pad's lowest layer. min(None) crashed here on the first TH pad cluster.
        padlay = next((min(p["layers"]) for p in pads if p["layers"] is not None), self.B_Cu)
        step = int(0.15 * self.NM)
        for ring in range(0, 22):
            for ang_deg in range(0, 360, 30):
                r = ring * step; a = math.radians(ang_deg)
                vx = int(px + r * math.cos(a)); vy = int(py + r * math.sin(a))
                # the via spot MUST lie inside the routing grid (board edge - 0.5 mm
                # margin) or to_cell() maps it out of the blocked_layers bytearray
                # and the endpoint index access crashes (IndexError on edge pads).
                vcx, vcy = self.to_cell(vx, vy)
                if not (0 <= vcx < self.NCOL and 0 <= vcy < self.NROW): continue
                if not self._via_clear(vx, vy, nc): continue
                cp = min(pads, key=lambda p: (p["x"] - vx) ** 2 + (p["y"] - vy) ** 2)
                if self._trk_clear(cp["x"], cp["y"], vx, vy, padlay, nc):
                    return vx, vy, padlay, cp["x"], cp["y"]
        return None

    def endpoint_plan(self, cluster, nc, anchor=None, want=4):
        """Candidate endpoints for one cluster side, as a LIST the bridge can
        retry: existing cluster vias first (nearest the opposite cluster's
        centroid when `anchor` is given, so a long bridge does not start at the
        cluster's far edge), else up to `want` spiral clear-spots (first valid
        spot PER RING, each with its verified pad stub). One boxed first-choice
        spot -- e.g. a via trapped in a dense escape field at the board edge --
        must not fail the whole net."""
        pads = cluster["pads"]
        px = sum(p["x"] for p in pads) // len(pads)
        py = sum(p["y"] for p in pads) // len(pads)
        if cluster["vias"]:
            ax_, ay_ = anchor if anchor is not None else (px, py)
            cands = []
            for v in sorted(cluster["vias"],
                            key=lambda v: (v["x"] - ax_) ** 2 + (v["y"] - ay_) ** 2):
                cands.append({"x": v["x"], "y": v["y"], "needs_via": False})
                if len(cands) >= want:
                    break
            return cands
        # through-hole / drilled pads report layers=None (= ALL layers); route the
        # pad->via track on the bottom (valid for an all-layer pad), else the first
        # SMD pad's lowest layer. min(None) crashed here on the first TH pad cluster.
        padlay = next((min(p["layers"]) for p in pads if p["layers"] is not None), self.B_Cu)
        step = int(0.15 * self.NM)
        cands = []
        for ring in range(0, 22):
            spot = None
            for ang_deg in range(0, 360, 30):
                r = ring * step; a = math.radians(ang_deg)
                vx = int(px + r * math.cos(a)); vy = int(py + r * math.sin(a))
                # the via spot MUST lie inside the routing grid (board edge - 0.5 mm
                # margin) or to_cell() maps it out of the blocked_layers bytearray
                # and the endpoint index access crashes (IndexError on edge pads).
                vcx, vcy = self.to_cell(vx, vy)
                if not (0 <= vcx < self.NCOL and 0 <= vcy < self.NROW): continue
                if not self._via_clear(vx, vy, nc): continue
                cp = min(pads, key=lambda p: (p["x"] - vx) ** 2 + (p["y"] - vy) ** 2)
                if self._trk_clear(cp["x"], cp["y"], vx, vy, padlay, nc):
                    spot = (vx, vy, padlay, cp["x"], cp["y"])
                    break
            if spot is None:
                continue
            vx, vy, padlay_s, padx, pady = spot
            cands.append({"x": vx, "y": vy, "needs_via": True, "padlay": padlay_s,
                          "padx": padx, "pady": pady})
            if len(cands) >= want:
                break
        return cands

    def endpoint_commit(self, ep, nc):
        if not ep["needs_via"]: return
        self.add_trk(ep["padx"], ep["pady"], ep["x"], ep["y"], ep["padlay"], nc)
        self.add_via(ep["x"], ep["y"], nc)

    # ---- per-layer obstacle grids + a combined via-clearable grid ----
    def build_blocked_layer(self, netcode, layer, endpoint_xy_set):
        """Blocked cells on ONE layer for routing net `netcode`."""
        NCOL, NROW = self.NCOL, self.NROW
        blocked = bytearray(NCOL * NROW)
        def block_disc(cx, cy, rad_cells):
            r = int(math.ceil(rad_cells))
            for dy in range(-r, r + 1):
                yy = cy + dy
                if yy < 0 or yy >= NROW: continue
                dxr = int(math.sqrt(max(0, r * r - dy * dy)))
                for dx in range(-dxr, dxr + 1):
                    xx = cx + dx
                    if 0 <= xx < NCOL: blocked[yy * NCOL + xx] = 1
        def block_seg(ax, ay, bx, by, hw):
            ddx, ddy = bx - ax, by - ay; length = math.hypot(ddx, ddy)
            if length == 0:
                block_disc(*self.to_cell(ax, ay), (hw + self.KEEP) / self.CELL); return
            steps = int(length / (self.CELL / 2)) + 1; rad = (hw + self.KEEP) / self.CELL
            for s in range(steps + 1):
                t = s / steps; x = ax + ddx * t; y = ay + ddy * t
                block_disc(*self.to_cell(int(x), int(y)), rad)
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetCode() == netcode: continue
                players = tc._pad_layer_set(p)   # None = drilled -> ALL layers
                if players is not None and layer not in players: continue
                if (p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
                        and not list(p.GetLayerSet().CuStack())):
                    # NPTH (hole only): block the true hole+clearance DISC (cell
                    # centre distance) -- the bounding square over-blocks corner
                    # cells and can seal a landing spot DRC permits.
                    dr = p.GetDrillSize()
                    pos = p.GetPosition()
                    r = max(dr.x, dr.y) // 2 + 150_000 + self.TRK_W // 2
                    r2 = r * r
                    cx0, cy0 = self.to_cell(pos.x - r, pos.y - r)
                    cx1, cy1 = self.to_cell(pos.x + r, pos.y + r)
                    for cy in range(max(0, cy0), min(NROW, cy1 + 1)):
                        for cx in range(max(0, cx0), min(NCOL, cx1 + 1)):
                            wx, wy = self.to_xy(cx, cy)
                            if (wx - pos.x) ** 2 + (wy - pos.y) ** 2 <= r2:
                                blocked[cy * NCOL + cx] = 1
                    continue
                pb = p.GetBoundingBox()
                cx0, cy0 = self.to_cell(pb.GetX() - self.KEEP, pb.GetY() - self.KEEP)
                cx1, cy1 = self.to_cell(pb.GetX() + pb.GetWidth() + self.KEEP, pb.GetY() + pb.GetHeight() + self.KEEP)
                for cy in range(max(0, cy0), min(NROW, cy1 + 1)):
                    for cx in range(max(0, cx0), min(NCOL, cx1 + 1)):
                        blocked[cy * NCOL + cx] = 1
        for t in self.board.GetTracks():
            if t.GetNetCode() == netcode: continue
            if t.GetClass() == "PCB_VIA":
                pos = t.GetPosition()
                # IsOnLayer(): inner-layer numeric IDs fall outside the
                # TopLayer()/BottomLayer() span (see _trk_clear) -- a plain
                # numeric comparison would leave through-vias unblocked on
                # inner layers and route straight through them.
                if not t.IsOnLayer(layer):
                    continue
                cx, cy = self.to_cell(pos.x, pos.y)
                block_disc(cx, cy, (track_width_nm(t) / 2 + self.KEEP) / self.CELL)
            else:
                if t.GetLayer() != layer: continue
                s = t.GetStart(); e = t.GetEnd()
                block_seg(s.x, s.y, e.x, e.y, track_width_nm(t) / 2)
        for (ex, ey) in endpoint_xy_set:
            cx, cy = self.to_cell(ex, ey)
            if 0 <= cx < NCOL and 0 <= cy < NROW:
                blocked[cy * NCOL + cx] = 0
        return blocked

    def build_via_clearable(self, netcode):
        """2D grid: cell is 1 if a through-hole via can be placed there clear of all
        other-net copper on ANY layer (a through-hole via penetrates every layer, so
        any copper anywhere at that xy within KEEP+via_r blocks it). Computed by
        rasterizing every other-net track/via/pad once (fast). Every obstacle is
        expanded by VIA_R = VIA_DIA/2 + KEEP (the via's own keepout) PLUS the
        obstacle's half-width -- matching _via_clear exactly so a grid-clear cell
        also passes the precise _via_clear validation."""
        NCOL, NROW = self.NCOL, self.NROW
        g = bytearray(NCOL * NROW)
        clear = bytearray(NCOL * NROW)   # 0 = clearable by default
        VIA_R = self.VIA_DIA // 2 + self.KEEP       # via keepout radius (nm)
        def block_disc(cx, cy, rad_cells):
            r = int(math.ceil(rad_cells))
            for dy in range(-r, r + 1):
                yy = cy + dy
                if yy < 0 or yy >= NROW: continue
                dxr = int(math.sqrt(max(0, r * r - dy * dy)))
                for dx in range(-dxr, dxr + 1):
                    xx = cx + dx
                    if 0 <= xx < NCOL: clear[yy * NCOL + xx] = 1
        def block_seg(ax, ay, bx, by, hw):
            ddx, ddy = bx - ax, by - ay; length = math.hypot(ddx, ddy)
            if length == 0:
                block_disc(*self.to_cell(ax, ay), (hw + VIA_R) / self.CELL); return
            steps = int(length / (self.CELL / 2)) + 1; rad = (hw + VIA_R) / self.CELL
            for s in range(steps + 1):
                t = s / steps; x = ax + ddx * t; y = ay + ddy * t
                block_disc(*self.to_cell(int(x), int(y)), rad)
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetCode() == netcode: continue
                # ANY pad (SMD on outer OR drilled on all) blocks a through-hole via
                # because the via penetrates the pad's layer(s). Expand bbox by VIA_R.
                pb = p.GetBoundingBox()
                cx0, cy0 = self.to_cell(pb.GetX() - VIA_R, pb.GetY() - VIA_R)
                cx1, cy1 = self.to_cell(pb.GetX() + pb.GetWidth() + VIA_R, pb.GetY() + pb.GetHeight() + VIA_R)
                for cy in range(max(0, cy0), min(NROW, cy1 + 1)):
                    for cx in range(max(0, cx0), min(NCOL, cx1 + 1)):
                        clear[cy * NCOL + cx] = 1
        for t in self.board.GetTracks():
            if t.GetNetCode() == netcode: continue
            if t.GetClass() == "PCB_VIA":
                pos = t.GetPosition()
                block_disc(*self.to_cell(pos.x, pos.y), (track_width_nm(t) / 2 + VIA_R) / self.CELL)
            else:
                # a through-hole via penetrates EVERY copper layer (incl. the In1/In2
                # planes), so a track on ANY layer blocks a via at that xy. _via_clear
                # checks all tracks regardless of layer; the grid must match it.
                s = t.GetStart(); e = t.GetEnd()
                block_seg(s.x, s.y, e.x, e.y, track_width_nm(t) / 2)
        # invert: clearable = where clear==0
        for i in range(NCOL * NROW):
            g[i] = 0 if clear[i] == 0 else 1
        return g

    # ---- per-net 3-D maze bridge ----
    def _bridge_net(self, nc, nm, idx=0, n_nets=0):
        """Maze-bridge ONE net across the 4 signal layers.

        Returns ``(report, log)`` where ``report`` is the tuple
        ``(nm, plen, x1, y1, x2, y2, reason)`` (``plen is None`` for a skip,
        ``reason`` describes why; the routed length in mm for a success,
        ``reason`` carries ``layers=.. vias=..``) and ``log`` is the list of
        progress lines to print. Mutates ``self.board`` only on a confirmed,
        fully-validated path.
        """
        SIG_LAYERS, LNAME, NL = self.SIG_LAYERS, self.LNAME, self.NL
        NCOL, NROW, CELL, NM = self.NCOL, self.NROW, self.CELL, self.NM
        tag = f"[{idx}/{n_nets}] {nm}" if n_nets else nm
        items = self.by_net.get(nc, [])
        clusters, _ = self.cluster_pads_and_vias(items)
        if len(clusters) < 2:
            # single-cluster nets print a skip line but, matching the original,
            # do NOT enter the report list (report is None) -- they are already
            # connected and excluded from the bridged/skip summary counts.
            return None, [f"{tag}: single-cluster (skip)"]
        # closest pad pair across the two nearest unconnected clusters
        log = [f"{tag}: routing..."]
        best = None
        for a in clusters:
            for bb_ in clusters:
                if a is bb_: continue
                for pa in a["pads"]:
                    for pb in bb_["pads"]:
                        d = (pa["x"] - pb["x"]) ** 2 + (pa["y"] - pb["y"]) ** 2
                        if best is None or d < best[0]: best = (d, a, bb_)
        _, ca, cb = best
        anchor_b = (sum(p["x"] for p in cb["pads"]) // len(cb["pads"]),
                    sum(p["y"] for p in cb["pads"]) // len(cb["pads"]))
        anchor_a = (sum(p["x"] for p in ca["pads"]) // len(ca["pads"]),
                    sum(p["y"] for p in ca["pads"]) // len(ca["pads"]))
        e1s = self.endpoint_plan(ca, nc, anchor=anchor_b)
        e2s = self.endpoint_plan(cb, nc, anchor=anchor_a)
        if not e1s or not e2s:
            log.append("   SKIP no clear via spot")
            return (nm, None, 0, 0, 0, 0, "no clear via spot"), log
        eps = [(e["x"], e["y"]) for e in e1s] + [(e["x"], e["y"]) for e in e2s]
        # build per-layer blocked grids + via-clearable grid ONCE; each attempt
        # force-opens its own endpoint cells (a cell that will HOST a via is
        # enterable by definition).
        blocked_layers = [self.build_blocked_layer(nc, L, eps) for L in SIG_LAYERS]
        via_clear = self.build_via_clearable(nc)
        # starts/goals: endpoint cells on EVERY layer (through-hole vias reach all)
        path = None; e1 = e2 = None
        for a_ep in e1s:
            for b_ep in e2s:
                sc = self.to_cell(a_ep["x"], a_ep["y"])
                gc_ = self.to_cell(b_ep["x"], b_ep["y"])
                if not (0 <= sc[0] < NCOL and 0 <= sc[1] < NROW
                        and 0 <= gc_[0] < NCOL and 0 <= gc_[1] < NROW):
                    continue
                starts = [(li, sc[0], sc[1]) for li in range(NL)]
                goals = [(li, gc_[0], gc_[1]) for li in range(NL)]
                for li in range(NL):
                    if blocked_layers[li][sc[1] * NCOL + sc[0]]:
                        blocked_layers[li][sc[1] * NCOL + sc[0]] = 0
                    if blocked_layers[li][gc_[1] * NCOL + gc_[0]]:
                        blocked_layers[li][gc_[1] * NCOL + gc_[0]] = 0
                path = astar3d(blocked_layers, via_clear, starts, goals,
                               NCOL, NROW, NL, self.VIA_COST)
                if path:
                    e1, e2 = a_ep, b_ep
                    break
            if path:
                break
        if not path:
            log.append(f"   SKIP no 3D path ({len(e1s)}x{len(e2s)} endpoint pairs)")
            r1 = e1s[0]; r2 = e2s[0]
            return (nm, None, r1["x"], r1["y"], r2["x"], r2["y"], "no 3D path"), log
        path = simplify3d(path)
        # VALIDATE every same-layer segment at REAL_CLEAR + every via-transition spot
        segs = []; vias_needed = []; bad = False; fail_info = ""
        prev = path[0]
        for cell in path[1:]:
            if cell[0] == prev[0]:   # same-layer segment
                x1, y1 = self.to_xy(prev[1], prev[2]); x2, y2 = self.to_xy(cell[1], cell[2])
                if not self._trk_clear(x1, y1, x2, y2, SIG_LAYERS[cell[0]], nc):
                    bad = True; fail_info = f"trk {LNAME[SIG_LAYERS[cell[0]]]} ({x1/NM:.2f},{y1/NM:.2f})->({x2/NM:.2f},{y2/NM:.2f})"; break
                segs.append(("trk", x1, y1, x2, y2, SIG_LAYERS[cell[0]]))
            else:
                # layer transition: place a through-hole via at this xy
                vx, vy = self.to_xy(cell[1], cell[2])
                if not self._via_clear(vx, vy, nc):
                    bad = True; fail_info = f"via ({vx/NM:.2f},{vy/NM:.2f}) {LNAME[SIG_LAYERS[prev[0]]]}->{LNAME[SIG_LAYERS[cell[0]]]}"; break
                vias_needed.append((vx, vy))
            prev = cell
        if bad:
            log.append(f"   SKIP 3D fails: {fail_info}")
            return (nm, None, e1["x"], e1["y"], e2["x"], e2["y"], f"3D fails: {fail_info}"), log
        # COMMIT
        self.endpoint_commit(e1, nc)
        self.endpoint_commit(e2, nc)
        placed_vias = set()
        for (vx, vy) in vias_needed:
            if (vx, vy) not in placed_vias:
                self.add_via(vx, vy, nc); placed_vias.add((vx, vy))
        for (kind, x1, y1, x2, y2, layer) in segs:
            self.add_trk(x1, y1, x2, y2, layer, nc)
        plen = sum(math.hypot(path[i + 1][1] - path[i][1], path[i + 1][2] - path[i][2])
                   for i in range(len(path) - 1)) * CELL / NM
        layers_used = sorted({LNAME[SIG_LAYERS[c[0]]] for c in path})
        log.append(f"   ok layers={','.join(layers_used)} vias={len(placed_vias)} len={plen:.1f}mm")
        return (nm, plen, e1["x"], e1["y"], e2["x"], e2["y"],
                f"ok layers={','.join(layers_used)} vias={len(placed_vias)}"), log