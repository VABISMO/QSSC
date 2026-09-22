"""Grid primitives for the QSSC signal router: pad/segment cell mapping,
the master obstacle grid, keepout marking/free queries, track + via
emission. The mutable layer tuples are read via ``_cfg.SIGNAL_LAYERS``.
"""
from __future__ import annotations

import pcbnew

from . import config as _cfg
from .config import (
    GRID_NM, PAD_KEEPOUT_NM, VIA_KEEPOUT_NM, ROUTING_VIA_DIAM_NM,
    ROUTING_VIA_DRILL_NM, _mark_radius_for_width,
)


def _pad_layers(pad):
    # A pad with a DRILL HOLE (PTH *or* NPTH) is an obstacle on EVERY signal
    # layer: a PTH barrel is copper on all layers, and an NPTH mounting hole
    # still needs hole clearance on every layer (a track routed over the hole on
    # an inner layer trips hole_clearance even though the hole is unplated). An
    # SMD pad (no drill) lives on one surface only -- with the 6-layer stackup a
    # BGA ball on F.Cu is only an obstacle on F.Cu, so In3/In4 stay pad-free
    # under the BGA and interior balls can escape there. The inner planes
    # (In1.Cu GND / In2.Cu +3V3) are owned by route_qssc_pcb and carve their own
    # anti-pads around the hole barrel, so they are not listed here.
    if pad.has_hole:
        return list(_cfg.SIGNAL_LAYERS)
    # SMD pad: its REAL copper layer(s) from the layer set. GetLayer() returns
    # F.Cu for a pad on a flipped (back-side) footprint, so it would mark a
    # B.Cu die pad as an F.Cu obstacle and leave B.Cu unguarded -> the router
    # could lay a track straight through the die pad on B.Cu (a short). Use the
    # captured cu_layers (defaults to [GetLayer()] for non-proxied pads).
    cu = getattr(pad, "cu_layers", None)
    if cu:
        # restrict to the board's signal layers (a pad on an inner plane layer
        # is not a signal-routing obstacle; the plane carves its own anti-pad).
        return [L for L in cu if L in _cfg.SIGNAL_LAYERS] or [pad.GetLayer()]
    return [pad.GetLayer()]




def _pad_cells(pad, x0, y0, expand):
    """Grid cells (ix,iy,lay) covered by pad bbox + expand, on the pad's layers."""
    bb = pad.GetBoundingBox()
    out = set()
    ix0 = int((bb.GetX() - expand - x0) // GRID_NM)
    iy0 = int((bb.GetY() - expand - y0) // GRID_NM)
    ix1 = int((bb.GetX() + bb.GetWidth() + expand - x0) // GRID_NM)
    iy1 = int((bb.GetY() + bb.GetHeight() + expand - y0) // GRID_NM)
    for lay in _pad_layers(pad):
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                out.add((ix, iy, lay))
    return out




def _pad_own_cells(pad, x0, y0):
    """Grid cells (ix,iy,lay) on the pad's own bbox (no expand) -- the own-net
    passable override (a pad's own keepout ring must be passable for its own
    net, else a track could never leave the pad)."""
    return _pad_cells(pad, x0, y0, 0)




def _build_master(fps, x0, y0, gx, gy):
    """One-time obstacle sets: master_blocked (pad+0.3 mm, 3D), via2d
    (pad+0.6 mm, 2D), and all_pad_copper (pad bbox, expand 0, 3D) -- every pad
    of every net. Own-net pads are still in here; per-net we pass an
    own-keepout override of the net's own pad cells. all_pad_copper lets build()
    strip OTHER pads' copper from a net's own_keepout so the own-ring override
    can never let a track enter a neighbour pad's copper (the no-short-by-
    construction proof: two same-row BGA balls 0.5 mm apart have overlapping
    0.3 mm keepout rings, and without this strip the ring of one ball's net
    would override the master keepout of the adjacent ball's copper).
    ``fps`` is a pre-captured list of footprints (KiCad's swig binding breaks
    board.GetFootprints() once the board is mutated, so callers capture it first)."""
    master_blocked = set()
    via2d = set()
    all_pad_copper = set()
    # cell -> set of netcodes whose PAD_KEEPOUT ring covers it. A cell covered by
    # the keepout rings of TWO DIFFERENT nets (e.g. two same-footprint pads of
    # different nets 0.84 mm apart -- an ESD diode's GND + signal pads) is a cell
    # the per-net own-ring override must NEVER mark free: a track centred there
    # is within PAD_KEEPOUT of a neighbour pad's copper, and with the track width
    # its edge shorts / clearance-violates that neighbour (the mw_gate->GND
    # short on D10: the signal pad's 0.3 mm ring bleeds into a cell that also
    # lies in the GND pad's ring, the bare-copper strip below misses it because
    # the cell holds no GND copper -- only the GND keepout ring). `multi_keepout`
    # is the set of such contested cells; build() subtracts it from every net's
    # own_keepout so the override can only ever relax a cell blocked SOLELY by the
    # net's own pads. Unnetted pads (spare BGA IO) use netcode -1 so any cell near
    # one is contested (never overridable).
    cell_nets: dict[tuple, set] = {}
    m = 1_000_000
    me = int(m // GRID_NM)
    for fp in fps:
        for p in fp.Pads():
            kcells = _pad_cells(p, x0, y0, PAD_KEEPOUT_NM)
            master_blocked |= kcells
            all_pad_copper |= _pad_cells(p, x0, y0, 0)
            nc = p.GetNetCode()
            key = nc if nc is not None and nc >= 0 else -1
            for c in kcells:
                cell_nets.setdefault(c, set()).add(key)
            for (ix, iy, _l) in _pad_cells(p, x0, y0, VIA_KEEPOUT_NM):
                via2d.add((ix, iy))
    multi_keepout = {c for c, nets in cell_nets.items() if len(nets) > 1}
    for lay in _cfg.SIGNAL_LAYERS:                       # 1 mm board-edge keepout
        for ix in range(0, gx):
            for iy in range(0, gy):
                if ix <= me or iy <= me or ix >= gx - me or iy >= gy - me:
                    master_blocked.add((ix, iy, lay))
    return master_blocked, via2d, all_pad_copper, multi_keepout




def _mark(routed, ix, iy, lay, radius, own=None):
    """Mark a radius*radius block of cells into ``routed`` (the global obstacle
    set). If ``own`` is given, also add the same cells there -- a per-net
    accumulator passed as the net's ``allowed`` override so a net's OWN tracks/
    vias do not block its own later daisy-chain pairs (power rails, multi-pad
    signals)."""
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            c = (ix + dx, iy + dy, lay)
            routed.add(c)
            if own is not None:
                own.add(c)


# ---- light orthogonal ("line") router -------------------------------------



def _free(cell, master, own_keepout, routed, allowed):
    return ((cell not in master or cell in own_keepout)
            and (cell not in routed or cell in allowed))




def _via_footprint_free(vix, viy, vr, master, own_keepout, routed, allowed):
    """True if the FULL via mark-footprint -- a (2*vr+1)^2 block of cells on every
    signal layer -- is free, so placing a THROUGH via at (vix, viy) cannot claim
    cells another net already holds. The centre-only check tests only
    (vix, viy) per layer: a track 0.1-0.6 mm
    off-centre (an adjacent cell) was invisible, the via was placed, and _add_via
    then marked the r3 block over it -> a short / 0.10 mm clearance (the
    CONF_DONE via vs CONF_INIT track on In4.Cu; the TIA_ADC via vs VREFP track on
    B.Cu). The own_keepout/allowed overrides let the via's OWN net's pads/tracks
    pass (a via-up next to its own pad or its own escape track is fine); cells of
    any OTHER net -- pad keepout ring, placed track, placed via -- stay blocked.
    This is the via-side twin of multi_keepout: both stop the per-net override
    from relaxing a cell a neighbour already owns."""
    for dx in range(-vr, vr + 1):
        for dy in range(-vr, vr + 1):
            for lay in _cfg.SIGNAL_LAYERS:
                if not _free((vix + dx, viy + dy, lay),
                            master, own_keepout, routed, allowed):
                    return False
    return True




def _seg_cells(ax, ay, bx, by, lay, x0, y0):
    """Centre cells of an axis-aligned segment, or None if not axis-aligned."""
    ix0 = int((ax - x0) // GRID_NM); iy0 = int((ay - y0) // GRID_NM)
    ix1 = int((bx - x0) // GRID_NM); iy1 = int((by - y0) // GRID_NM)
    cells = set()
    if ix0 == ix1:
        for iy in range(min(iy0, iy1), max(iy0, iy1) + 1):
            cells.add((ix0, iy, lay))
    elif iy0 == iy1:
        for ix in range(min(ix0, ix1), max(ix0, ix1) + 1):
            cells.add((ix, iy0, lay))
    else:
        return None
    return cells




def _seg_clear(ax, ay, bx, by, lay, x0, y0, master, own_keepout, routed, allowed):
    cells = _seg_cells(ax, ay, bx, by, lay, x0, y0)
    if cells is None:
        return False
    return all(_free(c, master, own_keepout, routed, allowed) for c in cells)




def _add_track(board, nc, ax, ay, bx, by, lay, x0, y0, routed, width_nm, mark_r, own=None):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(int(ax), int(ay)))
    t.SetEnd(pcbnew.VECTOR2I(int(bx), int(by)))
    t.SetLayer(lay)
    t.SetWidth(width_nm)
    t.SetNetCode(nc)
    board.Add(t)
    for (ix, iy, _l) in _seg_cells(ax, ay, bx, by, lay, x0, y0) or []:
        _mark(routed, ix, iy, lay, mark_r, own)   # per-netclass keepout (r3/r4)




def _add_via(board, nc, vx, vy, x0, y0, routed, own=None):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(int(vx), int(vy)))
    v.SetNetCode(nc)
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetDrill(ROUTING_VIA_DRILL_NM)
    v.SetWidth(ROUTING_VIA_DIAM_NM)
    board.Add(v)
    ix = int((vx - x0) // GRID_NM); iy = int((vy - y0) // GRID_NM)
    # A THROUGH via barrel is copper on every layer, so its keepout must be
    # marked on all 4 signal layers -- not just F.Cu/B.Cu. The inner planes
    # (In1.Cu/In2.Cu) are owned by route_qssc_pcb and carve their own anti-pad
    # clearances around the via barrel, so they are not marked here. The via mark
    # radius uses the same WMAX-aware formula as tracks (a 0.4 mm via -> r3).
    vr = _mark_radius_for_width(ROUTING_VIA_DIAM_NM)
    for lay in _cfg.SIGNAL_LAYERS:
        _mark(routed, ix, iy, lay, vr, own)


