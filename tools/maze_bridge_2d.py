"""2D (single-layer) A* maze bridge for residual split nets the 3D bridge cannot
close.

maze_bridge_3d requires a transition via near every SMD endpoint pad; on a
congested board no clear via spot exists ("SKIP no clear via spot") and the net
stays split. This tool routes pad-to-pad on the pad's OWN layer (no via needed):
both endpoint pads are same-net so they are passable in the obstacle grid, and
the A* only needs a clear corridor on that one layer out of one pad and into the
other. For two SMD pads on the same layer that is the natural route; for a
through-hole pad (all layers) the pad's layer set is the full stack and any
signal layer is tried.

Non-destructive: writes <board>.maze2d.kicad_pcb. DRC the result to validate.

Usage: python3 tools/maze_bridge_2d.py <board.kicad_pcb> [net1,net2,...]
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
In3_Cu = pcbnew.In3_Cu; In4_Cu = pcbnew.In4_Cu
SIG_LAYERS = [B_Cu, In3_Cu, In4_Cu, F_Cu]
LNAME = {B_Cu: "B.Cu", In3_Cu: "In3", In4_Cu: "In4", F_Cu: "F.Cu"}
TRK_W = int(float(os.environ.get("MAZE_TRK_W", "0.20")) * NM)
CLEAR = int(float(os.environ.get("MAZE_CLEAR", "0.17")) * NM)
HALF_W = TRK_W // 2
KEEP = CLEAR + HALF_W
REAL_CLEAR = int(float(os.environ.get("MAZE_REAL_CLEAR", "0.17")) * NM)
CELL = int(0.10 * NM)

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

def add_trk(board, x1, y1, x2, y2, layer, nc):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(x1, y1)); t.SetEnd(pcbnew.VECTOR2I(x2, y2))
    t.SetWidth(TRK_W); t.SetLayer(layer); t.SetNetCode(nc)
    board.Add(t)

# ---- geometry helpers (copied from maze_bridge_3d) ----
def _seg_dist2(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0: return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2

def _seg_seg_dist2(ax, ay, bx, by, cx, cy, dx, dy):
    d1 = _seg_dist2(ax, ay, bx, by, cx, cy); d2 = _seg_dist2(ax, ay, bx, by, dx, dy)
    d3 = _seg_dist2(cx, cy, dx, dy, ax, ay); d4 = _seg_dist2(cx, cy, dx, dy, bx, by)
    return min(d1, d2, d3, d4)

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

def build_blocked_layer(netcode, layer, endpoint_xy_set):
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
            players = tc._pad_layer_set(p)
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

def astar2d(blocked, start, goal):
    """8-connected 2D A* from start=(cx,cy) to goal=(cx,cy)."""
    INF = 1 << 30
    g = {start: 0.0}; came = {}
    pq = []
    h = math.hypot(start[0] - goal[0], start[1] - goal[1])
    heapq.heappush(pq, (h, start))
    nodes = 0; MAXNODES = 8_000_000
    while pq:
        _, cur = heapq.heappop(pq)
        if cur == goal:
            path = [cur]
            while cur in came:
                cur = came[cur]; path.append(cur)
            return path[::-1]
        gc = g[cur]; cx, cy = cur
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < NCOL and 0 <= ny < NROW): continue
            if blocked[ny * NCOL + nx]: continue
            if dx and dy and (blocked[cy * NCOL + nx] or blocked[ny * NCOL + cx]): continue
            step = 1.4142 if (dx and dy) else 1.0
            nb = (nx, ny)
            ng = gc + step
            if ng < g.get(nb, INF):
                g[nb] = ng; came[nb] = cur
                heapq.heappush(pq, (ng + math.hypot(nx - goal[0], ny - goal[1]), nb))
                nodes += 1
                if nodes > MAXNODES: return None
    return None

def simplify2d(path):
    if len(path) <= 2: return path
    out = [path[0]]
    for i in range(1, len(path) - 1):
        p0 = out[-1]; p1 = path[i]; p2 = path[i + 1]
        x0, y0 = p0; x1, y1 = p1; x2, y2 = p2
        if (x2 - x0) * (y1 - y0) == (x1 - x0) * (y2 - y0) and \
           (x2 - x0) * (x1 - x0) >= 0 and (y2 - y0) * (y1 - y0) >= 0:
            continue
        out.append(path[i])
    out.append(path[-1])
    return out

def pad_layers(pad):
    """Layer set for a pad; None = drilled (all layers)."""
    return tc._pad_layer_set(pad)

def shared_layers(pa, pb):
    """Layers both pads exist on (for pad-to-pad same-layer routing)."""
    la, lb = pa["layers"], pb["layers"]
    if la is None and lb is None: return list(SIG_LAYERS)
    if la is None: return [L for L in SIG_LAYERS if L in lb or lb is None] or list(SIG_LAYERS)
    if lb is None: return [L for L in SIG_LAYERS if L in la or la is None] or list(SIG_LAYERS)
    return [L for L in SIG_LAYERS if L in la and L in lb]

report = []
n_nets = len(by_net)
_maze_out = bp.with_suffix(".maze2d.kicad_pcb")
for idx, (nc, items) in enumerate(by_net.items(), 1):
    nm = netnames.get(nc, "")
    if not nm or nm in tc.PLANE_NETS: continue
    if only and nm not in only: continue
    clusters, _ = cluster_pads_and_vias(items)
    if len(clusters) < 2:
        print(f"[{idx}/{n_nets}] {nm}: single-cluster (skip)", flush=True); continue
    print(f"[{idx}/{n_nets}] {nm}: routing ({len(clusters)} clusters)...", flush=True)
    # closest pad pair across two distinct clusters
    best = None
    for a in clusters:
        for bb_ in clusters:
            if a is bb_: continue
            for pa in a["pads"]:
                for pb in bb_["pads"]:
                    d = (pa["x"] - pb["x"]) ** 2 + (pa["y"] - pb["y"]) ** 2
                    if best is None or d < best[0]: best = (d, pa, pb)
    _, pa, pb = best
    layers = shared_layers(pa, pb)
    eps = [(pa["x"], pa["y"]), (pb["x"], pb["y"])]
    done = False
    for L in layers:
        blocked = build_blocked_layer(nc, L, eps)
        sc = to_cell(pa["x"], pa["y"]); gc_ = to_cell(pb["x"], pb["y"])
        if not (0 <= sc[0] < NCOL and 0 <= sc[1] < NROW and 0 <= gc_[0] < NCOL and 0 <= gc_[1] < NROW):
            continue
        # ensure endpoint pad cells are open (same-net pads already passable,
        # but clear them explicitly in case an other-net pad overlaps the cell)
        blocked[sc[1] * NCOL + sc[0]] = 0; blocked[gc_[1] * NCOL + gc_[0]] = 0
        path = astar2d(blocked, sc, gc_)
        if not path:
            continue
        path = simplify2d(path)
        # validate every segment at REAL_CLEAR; commit
        segs = []; bad = False
        prev = path[0]
        for p in path[1:]:
            x1, y1 = to_xy(prev[0], prev[1]); x2, y2 = to_xy(p[0], p[1])
            if not _trk_clear(x1, y1, x2, y2, L, nc):
                bad = True; fail = f"trk {LNAME[L]} ({round(pcbnew.ToMM(x1),2)},{round(pcbnew.ToMM(y1),2)})->({round(pcbnew.ToMM(x2),2)},{round(pcbnew.ToMM(y2),2)})"
                print(f"   2D fails: {fail}", flush=True); break
            segs.append((x1, y1, x2, y2)); prev = p
        if bad:
            continue
        # snap first/last to exact pad centres
        if segs:
            segs[0] = (pa["x"], pa["y"], segs[0][2], segs[0][3])
            segs[-1] = (segs[-1][0], segs[-1][1], pb["x"], pb["y"])
        # collapse collinear duplicates
        for x1, y1, x2, y2 in segs:
            if (x1, y1) != (x2, y2):
                add_trk(b, x1, y1, x2, y2, L, nc)
        nseg = len(segs)
        mm = lambda v: round(pcbnew.ToMM(v), 2)
        print(f"   OK {LNAME[L]} {nseg} segs {mm(pa['x'])},{mm(pa['y'])}->{mm(pb['x'])},{mm(pb['y'])}", flush=True)
        report.append((nm, LNAME[L], pa["x"], pa["y"], pb["x"], pb["y"], f"2D ok {nseg}segs"))
        done = True; break
    if not done:
        print(f"   SKIP no 2D path on any shared layer", flush=True)
        report.append((nm, None, pa["x"], pa["y"], pb["x"], pb["y"], "no 2D path"))

b.Save(str(_maze_out))
print(f"== 2D maze-bridged -> {_maze_out} ==")
for nm, lay, x1, y1, x2, y2, why in report:
    s = f"  {nm}: {why} ({round(pcbnew.ToMM(x1),1)},{round(pcbnew.ToMM(y1),1)})->({round(pcbnew.ToMM(x2),1)},{round(pcbnew.ToMM(y2),1)})"
    print(s if lay is not None else s.replace(why, f"SKIP ({why})"))
nok = sum(1 for r in report if r[1] is not None)
print(f"bridged {nok}/{len(report)}; saved {_maze_out}")
print("NEXT: refill + DRC the .maze2d board in a project context.")
print("DONE")