"""Power-rail MST router + RF return-via fence. Reads ``_cfg._FAIL_REASON``
for the per-net failure diagnostic.
"""
from __future__ import annotations


from . import config as _cfg
from .config import (
    F_CU, GRID_NM, ROUTING_VIA_DIAM_NM, _DEBUG,
)
from .grid import (
    _add_track, _add_via, _seg_clear, _mark_radius_for_width,
)
from .pair_router import (
    _mst_order, _via_point_clear, _via_candidates, _try_layer, _emit_path,
)


def _route_rf_fence(board, gnd_nc, rf_pads, x0, y0, master, routed, via2d):
    """Place a GND via fence alongside the RF signal traces on F.Cu (Task 8).
    For each routed RF net, recompute its pad daisy order and the L-path between
    consecutive pads on F.Cu, then place GND through-vias at +/- 0.6 mm
    perpendicular offset every ~1 mm along the path -- the distributed return
    vias that keep the microstrip's ground reference continuous and suppress
    parasitic radiation. Every via is clearance-checked (pads + placed copper +
    via2d) so the fence never shorts or violates DRC. The trace itself is owned
    by the signal loop; the exact via pitch / offset is GUI-tunable. Returns the
    number of fence vias placed."""
    if gnd_nc is None:
        return 0
    nv = 0
    OFF = 600_000          # 0.6 mm perpendicular offset from the trace
    PITCH = 1_000_000      # ~1 mm along-trace via spacing
    for net, pads in rf_pads.items():
        if len(pads) < 2:
            continue
        # nearest-neighbour daisy order (same as the signal loop)
        order = [pads[0]]; rem = list(pads[1:])
        while rem:
            last = order[-1]
            best = min(rem, key=lambda q: (q.GetCenter().x - last.GetCenter().x) ** 2
                       + (q.GetCenter().y - last.GetCenter().y) ** 2)
            rem.remove(best); order.append(best)
        for a, b in zip(order, order[1:]):
            ax, ay = a.GetCenter().x, a.GetCenter().y
            bx, by = b.GetCenter().x, b.GetCenter().y
            # the L-path the F.Cu trace most likely took: corner at (bx, ay)
            # then (ax, by); fence BOTH candidate L segments (one will match the
            # placed trace, the other is a harmless off-trace row of GND vias).
            for (cx, cy) in ((bx, ay), (ax, by)):
                for (sx, sy, ex, ey) in ((ax, ay, cx, cy), (cx, cy, bx, by)):
                    dx, dy = ex - sx, ey - sy
                    length = (dx * dx + dy * dy) ** 0.5
                    if length < 1:
                        continue
                    # unit direction + perpendicular
                    ux, uy = dx / length, dy / length
                    px, py = -uy, ux
                    n = max(1, int(length // PITCH))
                    for k in range(1, n + 1):
                        t = k * PITCH
                        if t > length:
                            t = length
                        rx, ry = sx + ux * t, sy + uy * t
                        for sgn in (1, -1):
                            vx, vy = rx + px * OFF * sgn, ry + py * OFF * sgn
                            if _via_point_clear(vx, vy, x0, y0, master, routed, via2d):
                                _add_via(board, gnd_nc, vx, vy, x0, y0, routed)
                                nv += 1
    return nv




def _route_power(board, nc, net, pads, rail_lay, master, own_keepout, routed,
                 allowed, x0, y0, via2d, pad_vip, width_nm, mark_r):
    """Route one power rail on a dedicated inner/bottom layer (Task 7). Every
    SMD pad gets a through-via tie to ``rail_lay`` plus a short F.Cu stub; a BGA
    power ball already has a through-via barrel on ``rail_lay`` (pad_vip) so it
    is tied at its own centre. The rail is an MST of L/Z tracks on ``rail_lay``
    -- which carries NO SMD pads (inner layers are pad-free under the BGA; B.Cu
    has no discretes) -- so it runs clear of the surface GND / other-net pad
    keepouts that defeat the F.Cu line router.

    BEST-EFFORT / PARTIAL: a pad that cannot be tied (e.g. a USB-C VBUS pad at
    the board edge with no room for a through-via) is SKIPPED, and an MST edge
    with no clean L/Z (e.g. two 5V rails' decoupling caps interleaved in the cap
    bank cross on the shared B.Cu layer) is SKIPPED. A tie via is placed ONLY for
    a pad that is an endpoint of a successfully-routed MST edge (no isolated
    dangling vias). Skipped pads/edges are left to GUI. Nothing is
    placed until the dry-run resolves which edges route (a half-placed rail would
    pollute the shared layer for the next rail). Returns
    (n_tracks, n_vias, n_skip_pad, n_skip_edge): n_skip==0 -> the whole rail
    routed; >0 -> partial (some pads/edges GUI); (0,0,*,0) -> nothing routable."""
    kw = dict(x0=x0, y0=y0, master=master, own_keepout=own_keepout,
              routed=routed, allowed=allowed)
    vr = _mark_radius_for_width(ROUTING_VIA_DIAM_NM)
    ties = []          # (x, y) rail-layer tie points, parallel to pads order
    tie_info = []      # (pad, (vx,vy)) for SMD pads; None for a BGA barrel tie
    ties_pad_idx = []  # pads[i] index for each ties[] entry (skipped pads absent)
    pad_centers = [(p.GetCenter().x, p.GetCenter().y) for p in pads]
    reserved = set()   # 2D cells already claimed by a chosen tie via (no stacking)
    skip_pad = 0

    # Phase A -- find a clean through-via tie for every SMD pad (place nothing).
    # BGA power balls are tied at their own barrel (already on rail_lay). A pad
    # with no clean tie via is skipped (left to the Phase-D daisy fallback, then
    # GUI), not added to `ties`.
    for i, p in enumerate(pads):
        cx, cy = pad_centers[i]
        key = (int(cx), int(cy))
        if key in pad_vip and pad_vip[key][0] == rail_lay:
            ties.append((cx, cy))
            tie_info.append(None)
            ties_pad_idx.append(i)
            continue
        via_pos = None
        for (vx, vy) in _via_candidates(p, x0, y0, via2d, master, own_keepout,
                                        routed, allowed, reserved=reserved):
            if _seg_clear(cx, cy, vx, vy, F_CU, **kw):   # F.Cu stub pad->via
                via_pos = (vx, vy)
                break
        if via_pos is None:
            skip_pad += 1
            if _DEBUG:
                print(f"  DEBUG {net} skip pad ({cx/1e6:.1f},{cy/1e6:.1f}): no tie via")
            continue
        vix, viy = int((vx - x0) // GRID_NM), int((vy - y0) // GRID_NM)
        for dx in range(-2, 3):                          # reserve so the next pad
            for dy in range(-2, 3):                      # does not co-locate on it
                reserved.add((vix + dx, viy + dy))
        ties.append(via_pos)
        tie_info.append((p, via_pos))
        ties_pad_idx.append(i)
    if len(ties) < 2:
        _cfg._FAIL_REASON = f"power {net}: fewer than 2 tieable pads"
        return (0, 0, skip_pad, 0)

    # Phase B -- let the rail's OWN track pass each tie via (rail layer + F.Cu),
    # then dry-run every MST edge, SKIPPING blocked ones (GUI). _try_layer places
    # nothing, so skipped/blocked edges leave the board + global `routed` clean.
    for info in tie_info:
        if info is None:
            continue
        vx, vy = info[1]
        vix, viy = int((vx - x0) // GRID_NM), int((vy - y0) // GRID_NM)
        for lay in (rail_lay, F_CU):                     # NOT the other 2 layers:
            for dx in range(-vr, vr + 1):                # a same-net via cannot
                for dy in range(-vr, vr + 1):            # stack (needs all 4 free)
                    allowed.add((vix + dx, viy + dy, lay))
    paths = []          # list of (i, j, path) for successful MST edges
    skip_edge = 0
    for i, j in _mst_order(ties):
        ax, ay = ties[i]
        bx, by = ties[j]
        path = _try_layer(ax, ay, bx, by, rail_lay, kw, max_off=50)
        if path is None:
            skip_edge += 1
            if _DEBUG:
                print(f"  DEBUG {net} skip edge ({ax/1e6:.1f},{ay/1e6:.1f})"
                      f"->({bx/1e6:.1f},{by/1e6:.1f}) lay={rail_lay}")
            continue
        paths.append((i, j, path))

    # Phase C -- place: a tie via + F.Cu stub ONLY for pads that are an endpoint
    # of a routed MST edge (no isolated dangling vias), then the rail tracks.
    used = {i for i, j, _ in paths} | {j for i, j, _ in paths}
    nt = 0
    nv = 0
    for k in used:
        info = tie_info[k]
        if info is None:
            continue                      # BGA barrel already tied
        p, (vx, vy) = info
        cx, cy = p.GetCenter().x, p.GetCenter().y
        _add_track(board, nc, cx, cy, vx, vy, F_CU, x0, y0, routed,
                   width_nm, mark_r, allowed)
        _add_via(board, nc, vx, vy, x0, y0, routed)
        nt += 1
        nv += 1
    for i, j, path in paths:
        _emit_path(board, nc, path, rail_lay, x0, y0, routed,
                   width_nm, mark_r, allowed)
        nt += len(path) - 1

    # Phase D -- daisy-chain fallback for pads the MST / tie-via skipped: a
    # direct F.Cu L/Z from the straggler pad to the NEAREST already-connected
    # pad of the same net (which already has its via to the rail) ties the
    # straggler to the rail through its neighbour. No new via; the F.Cu track is
    # the pad's own net (its own_keepout / allowed override passes its own pad +
    # the target pad, both same-net), and _try_layer's _seg_clear keeps it off
    # every other net, so it cannot short. Catches decoupling caps in the dense
    # cap bank / a module-connector +5V pin whose own tie via did not fit (the
    # via-footprint r3 check is conservative). Tried on F.Cu only (an SMD pad
    # lives on F.Cu; a rail-layer track from it would not connect without a via,
    # which is exactly what did not fit). Still-unconnected pads are left to GUI.
    connected_idx = {ties_pad_idx[k] for k in used}
    conn_centers = [pad_centers[k] for k in connected_idx]
    for i in range(len(pads)):
        if i in connected_idx:
            continue
        sx, sy = pad_centers[i]
        # nearest connected pad first (a short F.Cu hop is most likely clean)
        targets = sorted(conn_centers,
                         key=lambda c: (c[0] - sx) ** 2 + (c[1] - sy) ** 2)
        for (tx, ty) in targets:
            if abs(tx - sx) < 1 and abs(ty - sy) < 1:
                continue                  # same cell (stacked pads) -- skip
            dpath = _try_layer(sx, sy, tx, ty, F_CU, kw, max_off=20)
            if dpath is not None:
                _emit_path(board, nc, dpath, F_CU, x0, y0, routed,
                           width_nm, mark_r, allowed)
                nt += len(dpath) - 1
                connected_idx.add(i)
                skip_pad -= 1
                break
    _cfg._FAIL_REASON = "" if (nt or skip_pad or skip_edge) else f"power {net}: no edges"
    return (nt, nv, skip_pad, skip_edge)


