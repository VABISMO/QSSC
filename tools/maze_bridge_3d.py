"""3D (multi-layer) A* maze bridge for residual split nets on the head board.

Routes a single named net across the 4 signal layers (B.Cu, In3, In4, F.Cu) with
through-hole via transitions at clear cells. Routes ONLY the named net — it does
NOT re-fanout or disturb any other net (unlike FreeRouting, which re-fans-out
every SMD pin). Endpoint vias are placed by this script (spiral clear-spot search,
deferred until a path is confirmed).

The single-layer head_maze_bridge.py could not close the 106mm DET_IN10 net on
any one layer (via field fully blocks In4; In3 path genuinely breaches 0.17mm;
B.Cu blocked by J12). A 3D search that hops layers around the congestion is the
principled closure.

Non-destructive: writes <board>.maze3d.kicad_pcb. DRC the result to validate.

Usage: python3 tools/maze_bridge_3d.py <board.kicad_pcb> [net1,net2,...]
"""
import sys, math, heapq, os
from pathlib import Path
bp = Path(sys.argv[1]).resolve()
only = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
import pcbnew
import test_pcb_connectivity as tc

b = pcbnew.LoadBoard(str(bp))
NM = 1_000_000
F_Cu = pcbnew.F_Cu; B_Cu = pcbnew.B_Cu
# 4 signal layers (In1=GND, In2=+3V3 are planes -- skip)
SIG_LAYERS = [B_Cu, pcbnew.In3_Cu, pcbnew.In4_Cu, F_Cu]
LNAME = {B_Cu: "B.Cu", pcbnew.In3_Cu: "In3", pcbnew.In4_Cu: "In4", F_Cu: "F.Cu"}
NL = len(SIG_LAYERS)
VIA_DIA = int(0.40 * NM); VIA_DRILL = int(0.20 * NM)
TRK_W = int(0.20 * NM)
CLEAR = int(float(os.environ.get("MAZE_CLEAR", "0.16")) * NM)
HALF_W = TRK_W // 2
KEEP = CLEAR + HALF_W
REAL_CLEAR = int(0.17 * NM)
CELL = int(0.10 * NM)
VIA_COST = 4.0   # penalty for one layer transition (a through-hole via)

bb = b.GetBoardEdgesBoundingBox()
X0, Y0 = bb.GetX(), bb.GetY()
X1, Y1 = X0 + bb.GetWidth(), Y0 + bb.GetHeight()
MARG = int(0.5 * NM)
GX0, GY0 = X0 + MARG, Y0 + MARG
GX1, GY1 = X1 - MARG, Y1 - MARG
NCOL = (GX1 - GX0) // CELL + 1
NROW = (GY1 - GY0) // CELL + 1

def to_cell(x, y): return (x - GX0) // CELL, (y - GY0) // CELL
def to_xy(cx, cy): return GX0 + cx * CELL, GY0 + cy * CELL

# ---- group items by netcode ----
by_net = {}
for fp in b.GetFootprints():
    for p in fp.Pads():
        nc = p.GetNetCode()
        if nc <= 0: continue
        by_net.setdefault(nc, []).append(("pad", p))
for t in b.GetTracks():
    nc = t.GetNetCode()
    if nc <= 0: continue
    by_net.setdefault(nc, []).append(("via", t) if t.GetClass() == "PCB_VIA" else ("trk", t))
netnames = {c: ni.GetNetname() for c, ni in b.GetNetInfo().NetsByNetcode().items()}

def _ref(pad):
    fp = pad.GetParentFootprint() if hasattr(pad, "GetParentFootprint") else pad.GetParent()
    try: return fp.GetReference() if fp else "?"
    except Exception: return "?"

def cluster_pads_and_vias(items):
    n = len(items); uf = tc._UF(n); geom = []
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
            geom.append({"kind": "via", "x": pos.x, "y": pos.y, "layers": tc._via_layer_set(b, it),
                "r": it.GetWidth() // 2, "it": it})
        else:
            s = it.GetStart(); e = it.GetEnd()
            geom.append({"kind": "trk", "ax": s.x, "ay": s.y, "bx": e.x, "by": e.y,
                "hw": it.GetWidth() // 2, "layers": {it.GetLayer()}, "it": it})
    for i in range(n):
        for j in range(i + 1, n):
            if tc._layers_share(geom[i].get("layers"), geom[j].get("layers")) and tc._touch(geom[i], geom[j]):
                uf.union(i, j)
    cl = {}
    for i, g in enumerate(geom):
        cl.setdefault(uf.find(i), {"pads": [], "vias": [], "trks": []})
        cl[uf.find(i)]["pads" if g["kind"] == "pad" else "vias" if g["kind"] == "via" else "trks"].append(g)
    return [c for c in cl.values() if c["pads"]], geom

def add_via(board, x, y, nc):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(x, y))
    v.SetTopLayer(F_Cu); v.SetBottomLayer(B_Cu)
    v.SetDrill(VIA_DRILL); v.SetWidth(VIA_DIA); v.SetNetCode(nc)
    v.SetViaType(pcbnew.VIATYPE_THROUGH); board.Add(v); return v
def add_trk(board, x1, y1, x2, y2, layer, nc):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(x1, y1)); t.SetEnd(pcbnew.VECTOR2I(x2, y2))
    t.SetWidth(TRK_W); t.SetLayer(layer); t.SetNetCode(nc); board.Add(t); return t

def _seg_dist2(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0: return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2

def _orient(ax, ay, bx, by, cx, cy):
    v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if v > 0: return 1
    if v < 0: return -1
    return 0

def _on_seg(px, py, ax, ay, bx, by):
    return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)

def _seg_seg_dist2(ax, ay, bx, by, cx, cy, dx, dy):
    o1 = _orient(ax, ay, bx, by, cx, cy); o2 = _orient(ax, ay, bx, by, dx, dy)
    o3 = _orient(cx, cy, dx, dy, ax, ay); o4 = _orient(cx, cy, dx, dy, bx, by)
    if o1 != o2 and o3 != o4: return 0
    if o1 == 0 and _on_seg(cx, cy, ax, ay, bx, by): return 0
    if o2 == 0 and _on_seg(dx, dy, ax, ay, bx, by): return 0
    if o3 == 0 and _on_seg(ax, ay, cx, cy, dx, dy): return 0
    if o4 == 0 and _on_seg(bx, by, cx, cy, dx, dy): return 0
    return min(_seg_dist2(ax, ay, cx, cy, dx, dy), _seg_dist2(bx, by, cx, cy, dx, dy),
               _seg_dist2(cx, cy, ax, ay, bx, by), _seg_dist2(dx, dy, ax, ay, bx, by))

def _via_clear(x, y, nc):
    """Through-hole via at (x,y) clear of all OTHER-net copper on all layers?"""
    vr = VIA_DIA // 2 + KEEP; vr2 = vr * vr
    for t in b.GetTracks():
        if t.GetNetCode() == nc: continue
        if t.GetClass() == "PCB_VIA":
            pos = t.GetPosition(); rr = vr + t.GetWidth() // 2
            if (pos.x - x) ** 2 + (pos.y - y) ** 2 < rr * rr: return False
        else:
            s = t.GetStart(); e = t.GetEnd(); rr = vr + t.GetWidth() // 2
            if _seg_dist2(x, y, s.x, s.y, e.x, e.y) < rr * rr: return False
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == nc: continue
            players = tc._pad_layer_set(p)   # None = drilled -> ALL layers
            if players is not None and not (F_Cu in players or B_Cu in players): continue
            pb = p.GetBoundingBox()
            if (pb.GetX() - vr) <= x <= (pb.GetX() + pb.GetWidth() + vr) and \
               (pb.GetY() - vr) <= y <= (pb.GetY() + pb.GetHeight() + vr):
                cx0, cy0 = pb.GetX(), pb.GetY()
                cx1, cy1 = pb.GetX() + pb.GetWidth(), pb.GetY() + pb.GetHeight()
                ddx = max(cx0 - x, 0, x - cx1); ddy = max(cy0 - y, 0, y - cy1)
                if ddx * ddx + ddy * ddy < vr2: return False
    return True

def _pt_rect_dist2(px, py, rx0, ry0, rx1, ry1):
    dx = max(rx0 - px, 0, px - rx1); dy = max(ry0 - py, 0, py - ry1)
    return dx * dx + dy * dy

def _seg_rect_dist2(x1, y1, x2, y2, rx0, ry0, rx1, ry1):
    d = min(_pt_rect_dist2(x1, y1, rx0, ry0, rx1, ry1),
            _pt_rect_dist2(x2, y2, rx0, ry0, rx1, ry1))
    for cx, cy in ((rx0, ry0), (rx1, ry0), (rx0, ry1), (rx1, ry1)):
        dd = _seg_dist2(cx, cy, x1, y1, x2, y2)
        if dd < d: d = dd
    return d

def _trk_clear(x1, y1, x2, y2, layer, nc):
    """Track on `layer` clear of other-net copper on that layer (REAL DRC clearance)."""
    hw = TRK_W // 2 + REAL_CLEAR; hw2 = hw * hw
    for t in b.GetTracks():
        if t.GetNetCode() == nc: continue
        if t.GetClass() == "PCB_VIA":
            pos = t.GetPosition(); top, bot = t.TopLayer(), t.BottomLayer()
            if not (top <= layer <= bot): continue
            rr = hw + t.GetWidth() // 2
            if _seg_dist2(pos.x, pos.y, x1, y1, x2, y2) < rr * rr: return False
        else:
            if t.GetLayer() != layer: continue
            s = t.GetStart(); e = t.GetEnd(); rr = hw + t.GetWidth() // 2
            if _seg_seg_dist2(x1, y1, x2, y2, s.x, s.y, e.x, e.y) < rr * rr: return False
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == nc: continue
            players = tc._pad_layer_set(p)
            if players is not None and layer not in players: continue
            pb = p.GetBoundingBox()
            rx0, ry0 = pb.GetX(), pb.GetY()
            rx1, ry1 = pb.GetX() + pb.GetWidth(), pb.GetY() + pb.GetHeight()
            if _seg_rect_dist2(x1, y1, x2, y2, rx0, ry0, rx1, ry1) < hw2: return False
    return True

def _find_clear_via_spot(cluster, nc):
    pads = cluster["pads"]
    px = sum(p["x"] for p in pads) // len(pads)
    py = sum(p["y"] for p in pads) // len(pads)
    padlay = min(pads[0]["layers"])
    step = int(0.15 * NM)
    for ring in range(0, 22):
        for ang_deg in range(0, 360, 30):
            r = ring * step; a = math.radians(ang_deg)
            vx = int(px + r * math.cos(a)); vy = int(py + r * math.sin(a))
            if not _via_clear(vx, vy, nc): continue
            cp = min(pads, key=lambda p: (p["x"] - vx) ** 2 + (p["y"] - vy) ** 2)
            if _trk_clear(cp["x"], cp["y"], vx, vy, padlay, nc):
                return vx, vy, padlay, cp["x"], cp["y"]
    return None

def endpoint_plan(cluster, nc):
    if cluster["vias"]:
        px = sum(p["x"] for p in cluster["pads"]) // len(cluster["pads"])
        py = sum(p["y"] for p in cluster["pads"]) // len(cluster["pads"])
        v = min(cluster["vias"], key=lambda v: (v["x"] - px) ** 2 + (v["y"] - py) ** 2)
        return {"x": v["x"], "y": v["y"], "needs_via": False}
    spot = _find_clear_via_spot(cluster, nc)
    if spot is None: return None
    vx, vy, padlay, padx, pady = spot
    return {"x": vx, "y": vy, "needs_via": True, "padlay": padlay, "padx": padx, "pady": pady}

def endpoint_commit(ep, nc):
    if not ep["needs_via"]: return
    add_trk(b, ep["padx"], ep["pady"], ep["x"], ep["y"], ep["padlay"], nc)
    add_via(b, ep["x"], ep["y"], nc)

# ---- per-layer obstacle grids + a combined via-clearable grid ----
def build_blocked_layer(netcode, layer, endpoint_xy_set):
    """Blocked cells on ONE layer for routing net `netcode`."""
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
            block_disc(*to_cell(ax, ay), (hw + KEEP) / CELL); return
        steps = int(length / (CELL / 2)) + 1; rad = (hw + KEEP) / CELL
        for s in range(steps + 1):
            t = s / steps; x = ax + ddx * t; y = ay + ddy * t
            block_disc(*to_cell(int(x), int(y)), rad)
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == netcode: continue
            players = tc._pad_layer_set(p)   # None = drilled -> ALL layers
            if players is not None and layer not in players: continue
            pb = p.GetBoundingBox()
            cx0, cy0 = to_cell(pb.GetX() - KEEP, pb.GetY() - KEEP)
            cx1, cy1 = to_cell(pb.GetX() + pb.GetWidth() + KEEP, pb.GetY() + pb.GetHeight() + KEEP)
            for cy in range(max(0, cy0), min(NROW, cy1 + 1)):
                for cx in range(max(0, cx0), min(NCOL, cx1 + 1)):
                    blocked[cy * NCOL + cx] = 1
    for t in b.GetTracks():
        if t.GetNetCode() == netcode: continue
        if t.GetClass() == "PCB_VIA":
            pos = t.GetPosition(); top, bot = t.TopLayer(), t.BottomLayer()
            if not (top <= layer <= bot): continue
            cx, cy = to_cell(pos.x, pos.y)
            block_disc(cx, cy, (t.GetWidth() / 2 + KEEP) / CELL)
        else:
            if t.GetLayer() != layer: continue
            s = t.GetStart(); e = t.GetEnd()
            block_seg(s.x, s.y, e.x, e.y, t.GetWidth() / 2)
    for (ex, ey) in endpoint_xy_set:
        cx, cy = to_cell(ex, ey)
        if 0 <= cx < NCOL and 0 <= cy < NROW:
            blocked[cy * NCOL + cx] = 0
    return blocked

def build_via_clearable(netcode):
    """2D grid: cell is 1 if a through-hole via can be placed there clear of all
    other-net copper on ANY layer (a through-hole via penetrates every layer, so
    any copper anywhere at that xy within KEEP+via_r blocks it). Computed by
    rasterizing every other-net track/via/pad once (fast). Every obstacle is
    expanded by VIA_R = VIA_DIA/2 + KEEP (the via's own keepout) PLUS the
    obstacle's half-width -- matching _via_clear exactly so a grid-clear cell
    also passes the precise _via_clear validation."""
    g = bytearray(NCOL * NROW)
    clear = bytearray(NCOL * NROW)   # 0 = clearable by default
    VIA_R = VIA_DIA // 2 + KEEP       # via keepout radius (nm)
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
            block_disc(*to_cell(ax, ay), (hw + VIA_R) / CELL); return
        steps = int(length / (CELL / 2)) + 1; rad = (hw + VIA_R) / CELL
        for s in range(steps + 1):
            t = s / steps; x = ax + ddx * t; y = ay + ddy * t
            block_disc(*to_cell(int(x), int(y)), rad)
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == netcode: continue
            # ANY pad (SMD on outer OR drilled on all) blocks a through-hole via
            # because the via penetrates the pad's layer(s). Expand bbox by VIA_R.
            pb = p.GetBoundingBox()
            cx0, cy0 = to_cell(pb.GetX() - VIA_R, pb.GetY() - VIA_R)
            cx1, cy1 = to_cell(pb.GetX() + pb.GetWidth() + VIA_R, pb.GetY() + pb.GetHeight() + VIA_R)
            for cy in range(max(0, cy0), min(NROW, cy1 + 1)):
                for cx in range(max(0, cx0), min(NCOL, cx1 + 1)):
                    clear[cy * NCOL + cx] = 1
    for t in b.GetTracks():
        if t.GetNetCode() == netcode: continue
        if t.GetClass() == "PCB_VIA":
            pos = t.GetPosition()
            block_disc(*to_cell(pos.x, pos.y), (t.GetWidth() / 2 + VIA_R) / CELL)
        else:
            # a through-hole via penetrates EVERY copper layer (incl. the In1/In2
            # planes), so a track on ANY layer blocks a via at that xy. _via_clear
            # checks all tracks regardless of layer; the grid must match it.
            s = t.GetStart(); e = t.GetEnd()
            block_seg(s.x, s.y, e.x, e.y, t.GetWidth() / 2)
    # invert: clearable = where clear==0
    for i in range(NCOL * NROW):
        g[i] = 0 if clear[i] == 0 else 1
    return g

def astar3d(blocked_layers, via_clear, starts, goals):
    """3D A* over (layer_idx, cx, cy). Layer transitions at via-clear cells."""
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
    nodes = 0; MAXNODES = 12_000_000
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
            if not (0 <= nx < NCOL and 0 <= ny < NROW): continue
            if bl[ny * NCOL + nx]: continue
            if dx and dy:
                if bl[cy * NCOL + nx] or bl[ny * NCOL + cx]: continue
            step = 1.4142 if (dx and dy) else 1.0
            nb = (li, nx, ny)
            ng = gc + step
            if ng < g.get(nb, INF):
                g[nb] = ng; came[nb] = cur
                h = min(math.hypot(nx - gg[1], ny - gg[2]) for gg in goals)
                heapq.heappush(pq, (ng + h, nb))
                nodes += 1
                if nodes > MAXNODES: return None
        # layer transitions at this cell (if via-clearable here)
        ci = cy * NCOL + cx
        if via_clear[ci] == 0:   # 0 = clearable
            for nl in range(NL):
                if nl == li: continue
                nb = (nl, cx, cy)
                ng = gc + VIA_COST
                if ng < g.get(nb, INF):
                    g[nb] = ng; came[nb] = cur
                    h = min(math.hypot(cx - gg[1], cy - gg[2]) for gg in goals)
                    heapq.heappush(pq, (ng + h, nb))
                    nodes += 1
                    if nodes > MAXNODES: return None
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

report = []
for nc, items in by_net.items():
    nm = netnames.get(nc, "")
    if not nm or nm in tc.PLANE_NETS: continue
    if only and nm not in only: continue
    clusters, _ = cluster_pads_and_vias(items)
    if len(clusters) < 2: continue
    best = None
    for a in clusters:
        for bb_ in clusters:
            if a is bb_: continue
            for pa in a["pads"]:
                for pb in bb_["pads"]:
                    d = (pa["x"] - pb["x"]) ** 2 + (pa["y"] - pb["y"]) ** 2
                    if best is None or d < best[0]: best = (d, a, bb_)
    _, ca, cb = best
    e1 = endpoint_plan(ca, nc)
    e2 = endpoint_plan(cb, nc)
    if e1 is None or e2 is None:
        report.append((nm, None, 0, 0, 0, 0, "no clear via spot")); continue
    eps = [(e1["x"], e1["y"]), (e2["x"], e2["y"])]
    # build per-layer blocked grids + via-clearable grid
    blocked_layers = [build_blocked_layer(nc, L, eps) for L in SIG_LAYERS]
    via_clear = build_via_clearable(nc)
    # starts/goals: endpoint cells on EVERY layer (through-hole vias reach all)
    sc = to_cell(e1["x"], e1["y"]); gc_ = to_cell(e2["x"], e2["y"])
    starts = [(li, sc[0], sc[1]) for li in range(NL)]
    goals = [(li, gc_[0], gc_[1]) for li in range(NL)]
    # ensure endpoint cells are open on every layer
    for li in range(NL):
        if blocked_layers[li][sc[1] * NCOL + sc[0]]: blocked_layers[li][sc[1] * NCOL + sc[0]] = 0
        if blocked_layers[li][gc_[1] * NCOL + gc_[0]]: blocked_layers[li][gc_[1] * NCOL + gc_[0]] = 0
    path = astar3d(blocked_layers, via_clear, starts, goals)
    if not path:
        report.append((nm, None, e1["x"], e1["y"], e2["x"], e2["y"], "no 3D path")); continue
    path = simplify3d(path)
    # VALIDATE every same-layer segment at REAL_CLEAR + every via-transition spot
    segs = []; vias_needed = []; bad = False; fail_info = ""
    prev = path[0]
    for cell in path[1:]:
        if cell[0] == prev[0]:   # same-layer segment
            x1, y1 = to_xy(prev[1], prev[2]); x2, y2 = to_xy(cell[1], cell[2])
            if not _trk_clear(x1, y1, x2, y2, SIG_LAYERS[cell[0]], nc):
                bad = True; fail_info = f"trk {LNAME[SIG_LAYERS[cell[0]]]} ({x1/NM:.2f},{y1/NM:.2f})->({x2/NM:.2f},{y2/NM:.2f})"; break
            segs.append(("trk", x1, y1, x2, y2, SIG_LAYERS[cell[0]]))
        else:
            # layer transition: place a through-hole via at this xy
            vx, vy = to_xy(cell[1], cell[2])
            if not _via_clear(vx, vy, nc):
                bad = True; fail_info = f"via ({vx/NM:.2f},{vy/NM:.2f}) {LNAME[SIG_LAYERS[prev[0]]]}->{LNAME[SIG_LAYERS[cell[0]]]}"; break
            vias_needed.append((vx, vy))
        prev = cell
    if bad:
        report.append((nm, None, e1["x"], e1["y"], e2["x"], e2["y"], f"3D fails: {fail_info}")); continue
    # COMMIT
    endpoint_commit(e1, nc)
    endpoint_commit(e2, nc)
    placed_vias = set()
    for (vx, vy) in vias_needed:
        if (vx, vy) not in placed_vias:
            add_via(b, vx, vy, nc); placed_vias.add((vx, vy))
    for (kind, x1, y1, x2, y2, layer) in segs:
        add_trk(b, x1, y1, x2, y2, layer, nc)
    plen = sum(math.hypot(path[i + 1][1] - path[i][1], path[i + 1][2] - path[i][2])
               for i in range(len(path) - 1)) * CELL / NM
    layers_used = sorted({LNAME[SIG_LAYERS[c[0]]] for c in path})
    report.append((nm, plen, e1["x"], e1["y"], e2["x"], e2["y"],
                   f"ok layers={','.join(layers_used)} vias={len(placed_vias)}"))

out = bp.with_suffix(".maze3d.kicad_pcb")
b.Save(str(out))
print(f"== 3D maze-bridged -> {out.name} ==")
ok = 0
for nm, plen, x1, y1, x2, y2, reason in report:
    if plen is None:
        print(f"  {nm}: SKIP ({reason})  ({x1/NM:.1f},{y1/NM:.1f})->({x2/NM:.1f},{y2/NM:.1f})")
    else:
        print(f"  {nm}: 3D path {plen:.1f}mm {reason}  ({x1/NM:.1f},{y1/NM:.1f})->({x2/NM:.1f},{y2/NM:.1f})"); ok += 1
print(f"bridged {ok}/{len(report)}; saved {out}")
print("NEXT: refill + DRC the .maze3d board in a project context.")
print("DONE")