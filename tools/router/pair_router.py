"""The L/Z orthogonal line router for a single net pair + its via-up
helpers. Reads ``_cfg.JUMP_LAYERS`` + ``_cfg._FAIL_REASON`` (the per-net
failure diagnostic) via the config module.
"""
from __future__ import annotations



from . import config as _cfg
from .config import (
    F_CU, GRID_NM, ROUTING_VIA_DIAM_NM, _DEBUG, _net_width_nm,
)
from .grid import (
    _add_track, _add_via, _seg_clear,
    _pad_own_cells, _via_footprint_free,
    _mark_radius_for_width,
)


def _try_layer(sx, sy, tx, ty, lay, kw, max_off=30):
    """L (one corner, both orientations) then expanded Z (a detour row or
    column) on ``lay``. The detour sweeps EVERY grid step from 0.3 mm up to
    ``max_off``*0.2 mm in both directions so the router can thread the 2.54 mm
    gaps between through-hole header pins (a fixed small offset set would land
    on pin rows and miss the gaps). ``max_off`` defaults to 30 (~6 mm) for
    signals; the power router passes ~50 (~10 mm) so a rail can detour AROUND
    the FPGA BGA block (~8 mm tall) to reach the regulator/decoupling cluster.
    Returns the placed point list (start..end) or None; no tracks are placed
    here -- callers emit + mark."""
    for (cx, cy) in ((tx, sy), (sx, ty)):
        if (_seg_clear(sx, sy, cx, cy, lay, **kw)
                and _seg_clear(cx, cy, tx, ty, lay, **kw)):
            return [(sx, sy), (cx, cy), (tx, ty)]
    for off in range(1, max_off + 1):
        for my in (sy + off * GRID_NM, sy - off * GRID_NM):   # row detour
            if (_seg_clear(sx, sy, sx, my, lay, **kw)
                    and _seg_clear(sx, my, tx, my, lay, **kw)
                    and _seg_clear(tx, my, tx, ty, lay, **kw)):
                return [(sx, sy), (sx, my), (tx, my), (tx, ty)]
        for mx in (sx + off * GRID_NM, sx - off * GRID_NM):   # column detour
            if (_seg_clear(sx, sy, mx, sy, lay, **kw)
                    and _seg_clear(mx, sy, mx, ty, lay, **kw)
                    and _seg_clear(mx, ty, tx, ty, lay, **kw)):
                return [(sx, sy), (mx, sy), (mx, ty), (tx, ty)]
    return None




def _emit_path(board, nc, path, lay, x0, y0, routed, width_nm, mark_r, own=None):
    for (p0, p1) in zip(path, path[1:]):
        _add_track(board, nc, p0[0], p0[1], p1[0], p1[1], lay, x0, y0, routed,
                   width_nm, mark_r, own)




def _via_candidates(pad, x0, y0, via2d, master, own_keepout, routed, allowed,
                    reserved=None):
    """Yield every clean via-up point off ``pad``'s centre (clears the 0.6 mm
    via2d pad keepout) on both layers, nearest first. ``reserved`` is an
    optional extra set of blocked (ix,iy) cells (e.g. the mate via)."""
    px, py = pad.GetCenter().x, pad.GetCenter().y
    blocked = via2d if reserved is None else via2d | reserved
    vr = _mark_radius_for_width(ROUTING_VIA_DIAM_NM)
    # Offsets from 1.0 mm out to 6.0 mm: the near ones (1.0-2.4 mm) suit a tight
    # BGA/decoupling-cap cluster; the larger ones (3.0-6.0 mm) give a power tie
    # via room to clear a large connector pad (USB-C VBUS) or a dense cap bank
    # where every near offset lands on a neighbour. Nearest first.
    for D in (1_200_000, 1_500_000, 1_800_000, 1_000_000, 2_100_000, 2_400_000,
              3_000_000, 3_600_000, 4_200_000, 5_000_000, 6_000_000):
        for (vx, vy) in ((px + D, py), (px - D, py), (px, py + D), (px, py - D)):
            vix = int((vx - x0) // GRID_NM); viy = int((vy - y0) // GRID_NM)
            if (vix, viy) in blocked:
                continue
            # A THROUGH via-up point must clear its FULL r3 footprint on all 4
            # signal layers (the barrel occupies them all): a centre-only check
            # would miss a neighbour net's track 0.1-0.6 mm off-centre and the
            # via would then short it. Planes In1/In2 are not signal layers and
            # are owned by route_qssc_pcb (anti-pads carved separately).
            if _via_footprint_free(vix, viy, vr, master, own_keepout, routed,
                                   allowed):
                yield (vx, vy)




def _bga_edge_via_candidates(ax, ay, bbox, x0, y0, via2d, master, own_keepout,
                             routed, allowed):
    """Yield clean THROUGH-via points just OUTSIDE the FPGA BGA perimeter
    (``bbox`` = (xmin, ymin, xmax, ymax) in nm), nearest to the ball (ax, ay)
    first. A top-row ball escapes upward, a right-edge ball rightward, etc. The
    via sits 1.0-2.0 mm beyond the BGA edge on a 0.2 mm grid, so the inner-layer
    escape from the ball is a SHORT run to the edge (it only threads the BGA
    field near the ball, not all the way to the destination). Points must clear
    the 0.6 mm via2d keepout and be free on all 4 signal layers (THROUGH barrel).
    """
    xmin, ymin, xmax, ymax = bbox
    vr = _mark_radius_for_width(ROUTING_VIA_DIAM_NM)
    # distance of the ball to each edge (nm); pick the nearest edge first.
    edges = sorted([
        (ay - ymin, "top"), (ymax - ay, "bottom"),
        (ax - xmin, "left"), (xmax - ax, "right"),
    ])
    for _d, side in edges:
        for off in (1_000_000, 1_200_000, 1_500_000, 1_800_000, 2_100_000,
                    2_400_000, 3_000_000):
            # a column/row of via points along the chosen edge, centered on the
            # ball's in-edge coordinate, stepping +/- along the edge.
            base = 0
            for step in (0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
                along = base + step * GRID_NM
                if side == "top":
                    vx, vy = ax + along, ymin - off
                elif side == "bottom":
                    vx, vy = ax + along, ymax + off
                elif side == "left":
                    vx, vy = xmin - off, ay + along
                else:
                    vx, vy = xmax + off, ay + along
                vix = int((vx - x0) // GRID_NM); viy = int((vy - y0) // GRID_NM)
                if (vix, viy) in via2d:
                    continue
                if _via_footprint_free(vix, viy, vr, master, own_keepout, routed,
                                       allowed):
                    yield (vx, vy)




def _route_pair(board, nc, net, a, b, master, own_keepout, routed, allowed,
                x0, y0, via2d, vip_a, vip_b, bga_bbox=None):
    """Place a clean orthogonal connection between pads a and b. Returns
    (n_tracks, n_vias) on success, None if no clean shape was found.

    Tries, in order of cost:
      1. BGA via-in-pad nets: the via-in-pad is already on the escape inner
         layer (In2.Cu / In3.Cu) at a's centre; route that layer (L/Z) to a
         via-up near b, then F.Cu to b. Tries every clean via-up point near b
         until one is reachable.
      2. non-BGA nets: F.Cu L then expanded Z (row/column detours).
      3. B.Cu two-via hop (any net): via-up near a, via-up near b, B.Cu L/Z
         between them, short F.Cu stubs pad<->via. General escape for nets F.Cu
         cannot place.
    """
    # Per-netclass track width + mark radius (Task 6): RF 0.3, Power 0.5, else
    # 0.2 mm; mark radius r3 for 0.2/0.3, r4 for 0.5 (WMAX-aware, no-short proof).
    width_nm = _net_width_nm(net)
    mark_r = _mark_radius_for_width(width_nm)
    if vip_b and not vip_a:                    # normalise: via-in-pad pad is `a`
        a, b = b, a
        vip_a, vip_b = vip_b, vip_a
    ax, ay = a.GetCenter().x, a.GetCenter().y
    bx, by = b.GetCenter().x, b.GetCenter().y
    F = F_CU
    kw = dict(x0=x0, y0=y0, master=master, own_keepout=own_keepout,
              routed=routed, allowed=allowed)

    # --- both pads BGA via-in-pad on the SAME escape/rail layer: route that
    # layer directly a-centre -> b-centre (both barrels already tie to it, so NO
    # via is needed). This is the +1V0/+1V8 rail daisy-chain (BGA balls in a
    # compact column on In3/In4) and same-layer signal escape hops. Falls through
    # to the via-up branch if no clean L/Z on the shared layer exists.
    if vip_a and vip_b and vip_a[0] == vip_b[0]:
        esc_lay = vip_a[0]
        dpath = _try_layer(ax, ay, bx, by, esc_lay, kw)
        if dpath is not None:
            _emit_path(board, nc, dpath, esc_lay, x0, y0, routed, width_nm, mark_r, allowed)
            return (len(dpath) - 1, 0)

    # --- BGA via-in-pad: escape on an inner/B.Cu layer to a via-up near b, then
    # F.Cu to b. The VIP is a THROUGH via, so its barrel ties a's pad to EVERY
    # signal layer -- the net can escape on esc_lay (preferred, tightest keepout)
    # OR on B.Cu / the other inner layer if esc_lay is congested. Try esc_lay
    # first, then the rest of the jump layers. vip_a is (esc_lay, seed_cells).
    if vip_a:
        esc_lay = vip_a[0]
        # escape layers: esc_lay first (its allowed override is seeded tightest),
        # then B.Cu and the other inner layer (barrel is there too, allowed seeded
        # by _bga_fanout on all non-F.Cu signal layers).
        esc_layers = [esc_lay] + [l for l in _cfg.JUMP_LAYERS if l != esc_lay]
        found_via = False
        for (vx, vy) in _via_candidates(b, x0, y0, via2d, master, own_keepout,
                                       routed, allowed):
            found_via = True
            if not _seg_clear(vx, vy, bx, by, F, **kw):
                continue
            for el in esc_layers:
                bpath = _try_layer(ax, ay, vx, vy, el, kw)   # escape-layer L/Z
                if bpath is not None:
                    _emit_path(board, nc, bpath, el, x0, y0, routed, width_nm, mark_r, allowed)
                    _add_via(board, nc, vx, vy, x0, y0, routed)
                    _add_track(board, nc, vx, vy, bx, by, F, x0, y0, routed, width_nm, mark_r, allowed)
                    return (len(bpath) - 1 + 1, 1)     # esc segs + 1 F.Cu + 1 via
        _cfg._FAIL_REASON = "BGA: no via-up reachable" if found_via else "BGA: no via-up near target"

        # --- phase 2b: escape to a via just OUTSIDE the BGA perimeter, then route
        # to b. For edge / near-edge balls the inner escape is a SHORT run to the
        # BGA edge (it threads only the few balls near `a`, not the whole field).
        # The edge via is a THROUGH via (barrel on every layer), so from it the
        # route to b tries: (a) F.Cu L/Z direct, then (b) a 1-via hop -- jump
        # layer L/Z edge_via->via_b + F.Cu stub via_b->b -- for each jump layer,
        # so a far `b` blocked on F.Cu can still be reached on B.Cu/inner. The
        # FULL route is dry-run before anything is placed (no partial-copper leak).
        if bga_bbox is not None:
            n_edge = 0
            n_esc_ok = 0
            # Bound the search: the hop fallback is a 3-deep dry-run loop, so cap
            # the edge vias tried for the hop (EDGE_HOP_CAP) and the via_b per edge
            # via (VIA_B_CAP) so a failing net finishes in seconds, not minutes.
            EDGE_HOP_CAP = 24
            VIA_B_CAP = 8
            own_keepout_b = own_keepout | _pad_own_cells(b, x0, y0)
            kw_b = dict(kw, own_keepout=own_keepout_b)
            for (vx, vy) in _bga_edge_via_candidates(ax, ay, bga_bbox, x0, y0,
                                                    via2d, master, own_keepout,
                                                    routed, allowed):
                n_edge += 1
                if n_edge > EDGE_HOP_CAP:
                    break
                # first esc layer that clears, then route to b from the edge via
                epath = None
                el_used = None
                for el in esc_layers:
                    ep = _try_layer(ax, ay, vx, vy, el, kw)
                    if ep is not None:
                        epath, el_used = ep, el
                        break
                if epath is None:
                    continue
                n_esc_ok += 1
                # (a) F.Cu L/Z edge_via -> b
                fpath = _try_layer(vx, vy, bx, by, F, kw)
                if fpath is not None:
                    _emit_path(board, nc, epath, el_used, x0, y0, routed, width_nm, mark_r, allowed)
                    _add_via(board, nc, vx, vy, x0, y0, routed)
                    _emit_path(board, nc, fpath, F, x0, y0, routed, width_nm, mark_r, allowed)
                    return (len(epath) - 1 + len(fpath) - 1 + 1, 1)
                # (b) 1-via hop from the edge via: jump layer edge_via->via_b,
                # F.Cu stub via_b->b. via_b near b (bounded); edge_via is a via.
                nvb = 0
                for (vbx, vby) in _via_candidates(b, x0, y0, via2d, master,
                                                 own_keepout_b, routed, allowed):
                    nvb += 1
                    if nvb > VIA_B_CAP:
                        break
                    if not _seg_clear(vbx, vby, bx, by, F, **kw_b):
                        continue
                    for jump in _cfg.JUMP_LAYERS:
                        jpath = _try_layer(vx, vy, vbx, vby, jump, kw_b)
                        if jpath is None:
                            continue
                        _emit_path(board, nc, epath, el_used, x0, y0, routed, width_nm, mark_r, allowed)
                        _add_via(board, nc, vx, vy, x0, y0, routed)
                        _emit_path(board, nc, jpath, jump, x0, y0, routed, width_nm, mark_r, allowed)
                        _add_via(board, nc, vbx, vby, x0, y0, routed)
                        _add_track(board, nc, vbx, vby, bx, by, F, x0, y0, routed, width_nm, mark_r, allowed)
                        return (len(epath) - 1 + len(jpath) - 1 + 1, 2)
            if _DEBUG:
                print(f"  DEBUG {net} edge2b: {n_edge} vias, {n_esc_ok} esc-ok, "
                      f"ball=({ax/1e6:.1f},{ay/1e6:.1f}) b=({bx/1e6:.1f},{by/1e6:.1f})")
            _cfg._FAIL_REASON = "BGA: no edge escape + surface route"

    # --- non-BGA: F.Cu L then expanded Z ---
    fpath = _try_layer(ax, ay, bx, by, F, kw)
    if fpath is not None:
        _emit_path(board, nc, fpath, F, x0, y0, routed, width_nm, mark_r, allowed)
        return (len(fpath) - 1, 0)

    # --- 2-via hop (general escape for any net): via-up near a, L/Z on a JUMP
    # layer, via-down near b, short F.Cu stubs pad<->via. Tries B.Cu first
    # (cleanest -- no SMD pads), then the inner signal layers In3/In4 (pad-free
    # under the BGA) so a hop blocked on B.Cu can still thread an inner layer.
    a_via = False
    b_via_any = False
    stub_ok = False
    for (vax, vay) in _via_candidates(a, x0, y0, via2d, master, own_keepout,
                                      routed, allowed):
        a_via = True
        vaix, vaiy = int((vax - x0) // GRID_NM), int((vay - y0) // GRID_NM)
        reserved = {(vaix + dx, vaiy + dy) for dx in range(-2, 3) for dy in range(-2, 3)}
        own_keepout_b = own_keepout | _pad_own_cells(b, x0, y0)
        b_via = False
        for (vbx, vby) in _via_candidates(b, x0, y0, via2d, master, own_keepout_b,
                                         routed, allowed, reserved=reserved):
            b_via = True
            b_via_any = True
            if not (_seg_clear(ax, ay, vax, vay, F, **kw)
                    and _seg_clear(vbx, vby, bx, by, F, **kw)):
                continue
            stub_ok = True
            kw_b = dict(kw, own_keepout=own_keepout_b)
            for jump in _cfg.JUMP_LAYERS:
                bpath = _try_layer(vax, vay, vbx, vby, jump, kw_b)
                if bpath is not None:
                    _add_track(board, nc, ax, ay, vax, vay, F, x0, y0, routed, width_nm, mark_r, allowed)
                    _add_via(board, nc, vax, vay, x0, y0, routed)
                    _emit_path(board, nc, bpath, jump, x0, y0, routed, width_nm, mark_r, allowed)
                    _add_via(board, nc, vbx, vby, x0, y0, routed)
                    _add_track(board, nc, vbx, vby, bx, by, F, x0, y0, routed, width_nm, mark_r, allowed)
                    return (2 + len(bpath) - 1, 2)
        if not b_via:
            _cfg._FAIL_REASON = "hop: no via-up near b"
            return None
    if not a_via:
        _cfg._FAIL_REASON = "hop: no via-up near a" + (" (F.Cu L/Z also failed)" if not vip_a else "")
    elif not b_via_any:
        _cfg._FAIL_REASON = "hop: vias exist but no clean F.Cu stub / jump path"
    elif not stub_ok:
        _cfg._FAIL_REASON = "hop: vias exist but no clean F.Cu stub"
    else:
        _cfg._FAIL_REASON = "hop: no clean jump path on any signal layer"
    return None




def _mst_order(points):
    """Prim's minimum-spanning-tree over a list of (x, y) points (nm). Returns a
    list of (i, j) index pairs -- the minimum-total-length tree connecting every
    point. Used by the power router so a rail with spread pads (regulator cluster
    + BGA column + a top-edge decoupling-cap bank) connects via its shortest
    edges, not the long stray jumps a nearest-neighbour daisy chain makes when it
    runs out of near neighbours and leaps across the board."""
    n = len(points)
    if n < 2:
        return []
    in_tree = [False] * n
    in_tree[0] = True
    edges = []
    for _ in range(n - 1):
        best = None
        for i in range(n):
            if not in_tree[i]:
                continue
            xi, yi = points[i]
            for j in range(n):
                if in_tree[j]:
                    continue
                xj, yj = points[j]
                d = (xi - xj) ** 2 + (yi - yj) ** 2
                if best is None or d < best[0]:
                    best = (d, i, j)
        edges.append((best[1], best[2]))
        in_tree[best[2]] = True
    return edges




def _via_point_clear(vx, vy, x0, y0, master, routed, via2d, allowed=None):
    """True if a THROUGH via at (vx, vy) clears its FULL r3 footprint -- the pad
    keepout (master), the placed-copper marks (routed), and the 0.6 mm via2d pad
    keepout -- on all 4 signal layers (a THROUGH barrel occupies them all). The
    full-footprint check (not centre-only) is what stops a fence via from landing
    0.1-0.6 mm off a non-GND track and then shorting it. ``allowed`` (optional)
    lets a same-net override pass -- unused for the GND fence (GND has no VIP
    allowed override on signal layers), so the fence vias must clear cleanly."""
    vix = int((vx - x0) // GRID_NM); viy = int((vy - y0) // GRID_NM)
    if (vix, viy) in via2d:
        return False
    vr = _mark_radius_for_width(ROUTING_VIA_DIAM_NM)
    al = allowed if allowed is not None else set()
    return _via_footprint_free(vix, viy, vr, master, set(), routed, al)


