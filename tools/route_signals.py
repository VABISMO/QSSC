#!/usr/bin/env python3
"""Headless, RESOURCE-LIGHT SIGNAL router for the QSSC PCB.

Uses ONLY the KiCad pcbnew Python API (no external program, no GUI, no windows).

ROUTING ENGINE -- a light ORTHOGONAL ("line") router, NOT a maze/A* router.
A* on a ~550k-cell grid in pure python is memory-happy and slow (hundreds of
MB, many minutes) -- it froze the host. A line router is O(track length) per
net: no exploration, no big dicts, so it runs in seconds at ~50 MB. It places a
clean L (one corner) or Z (two corners, a detour) shape, collision-checked
against pads + already-routed copper; BGA via-in-pad nets escape on B.Cu. Nets
it cannot place cleanly are left HONESTLY unrouted, never force-routed.

DRC-clean by construction:
  * pad keepout = clearance + half-track-width, so a track CENTRE kept outside
    it guarantees the netclass clearance to pad copper.
  * every routed feature (0.2 mm track OR 0.4 mm routing via) marks a uniform
    radius-3 (0.6 mm) keepout; the worst-case edge clearance is 0.20 mm (via-via),
    >= the 0.15 mm netclass clearance.
  * track endpoints snap to real pad centres (no dangling ends).
  * via-in-pad vias on nets that never escape are REMOVED (else via_dangling).

FPGA BGA fanout (the user's first priority): the 0.5 mm-pitch CPG236 has 23
netted signal balls on its right edge; balls shadowed by a neighbour one column
toward the escape edge cannot escape on F.Cu (BGA pads are F.Cu SMD, so B.Cu
under the BGA is open). A 0.20/0.10 mm via-in-pad (HDI-lite) gives each shadowed
ball a B.Cu seed to escape rightward. 0.5 mm pitch / 0.275 mm pads -> the
via-in-pad keeps >0.26 mm clearance to the neighbouring ball, DRC-clean at the
0.15 mm netclass clearance. 215 unnetted FPGA balls stay unnetted (need UG475).

Power/GND planes + GND stitching mesh come from route_qssc_pcb.py (run this
FIRST on the clean board, then the plane script). Idempotent: clears every
non-GND track/via first (GND vias belong to the plane script).

Run (HARD-capped so it cannot freeze the host -- belt-and-suspenders with the
light engine above):

  systemd-run --user --scope -p MemoryMax=300M -p CPUQuota=40% \
      env PYTHONPATH=$PWD/tools python3 tools/route_signals.py
"""
from __future__ import annotations

import os
from pathlib import Path

import pcbnew

from route_qssc_pcb import (
    F_CU, B_CU,
)

PCB_PATH = Path(
    os.environ.get(
        "QSSC_PCB_PATH",
        Path(__file__).resolve().parent.parent / "hardware" / "pcb" / "qssc.kicad_pcb",
    )
)

_DEBUG = os.environ.get("QSSC_ROUTE_DEBUG")        # per-net failure reason
_FAIL_REASON = "init"

GRID_NM = 200_000                  # 0.2 mm grid: fine enough to thread 1.27 mm
                                   # header-pin gaps (0.67 mm) yet still light -- a
                                   # line router is O(track length), not O(area)
TRACK_W_NM = 200_000
PAD_KEEPOUT_NM = 300_000          # clearance + half-track
VIA_KEEPOUT_NM = 600_000          # via radius 0.3 + clearance 0.2 + margin

# Routing vias (signal F.Cu<->B.Cu hops / BGA escapes) are 0.40/0.20 mm -- HDI
# and smaller than the 0.6 mm plane-stitching vias. With 0.2 mm tracks + 0.4 mm
# routing vias, a uniform mark radius of 3 (0.6 mm) keeps the 0.15 mm netclass
# clearance for every feature pair:
#   track(0.1)-track(0.1): 0.6-0.1-0.1 = 0.40 mm
#   track(0.1)-via(0.2):   0.6-0.1-0.2 = 0.30 mm
#   via(0.2)-via(0.2):     0.6-0.2-0.2 = 0.20 mm   (all >= 0.15)
# radius-2 (0.4 mm) is DRC-safe with 0.2 mm vias but routed FEWER nets (11 vs 15)
# -- the wider radius-3 corridors fail BGA escapes fast (no partial placement)
# and leave B.Cu open for the non-BGA nets, which routes more overall.
ROUTING_VIA_DIAM_NM = 400_000
ROUTING_VIA_DRILL_NM = 200_000
MARK_RADIUS = 3

# Critical nets the autorouter must NOT touch -- left honestly unrouted for a
# manual GUI session: the USB-C diff pair needs 90-ohm controlled routing + length
# matching a line router cannot do. (Pad keepout alone is not enough: the two
# 0.5 mm-pitch SMD pads sit so close that a track leaving one clips the other.)
EXCLUDE_NETS = {"USB_DP", "USB_DM"}

# ---- FPGA BGA fanout (HDI-lite via-in-pad) ----
BGA_REF = "U1"
BGA_PITCH_NM = 500_000            # 0.5 mm CPG236 pitch
BGA_VIA_DIAM_NM = 200_000         # 0.20 mm via-in-pad (fits 0.275 mm pad)
BGA_VIA_DRILL_NM = 100_000         # 0.10 mm HDI drill


# ---- pre-mutation pad snapshot ---------------------------------------------
# KiCad's swig binding returns bare SwigPyObjects (no .GetCenter/.Pads) for
# board.GetFootprints()/fp.Pads() AFTER the board is mutated by board.Remove()
# (used by _clear_tracks). To stay single-load (a reload OOMs under the hard
# MemoryMax cap), we snapshot every pad's geometry into plain-python proxies
# BEFORE any mutation, then build from the snapshot + board.Add() only (Add
# does not break the proxy layer). The proxies quack like pcbnew pads/footprints
# so _pad_cells / _pad_layers / _route_pair etc. are unchanged.

class _V:
    """2D point proxy (mimics pcbnew.VECTOR2I .x/.y)."""
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y


class _Box:
    """Bbox proxy (mimics pcbnew GetBoundingBox .GetX/.GetY/.GetWidth/.GetHeight)."""
    __slots__ = ("_x", "_y", "_w", "_h")

    def __init__(self, x, y, w, h):
        self._x = x
        self._y = y
        self._w = w
        self._h = h

    def GetX(self):
        return self._x

    def GetY(self):
        return self._y

    def GetWidth(self):
        return self._w

    def GetHeight(self):
        return self._h


class _PadProxy:
    """Plain-python snapshot of one pad's geometry + net."""
    __slots__ = ("cx", "cy", "bx", "by", "bw", "bh",
                 "netname", "netcode", "is_pth", "layer")

    def __init__(self, cx, cy, bx, by, bw, bh,
                 netname, netcode, is_pth, layer):
        self.cx = cx
        self.cy = cy
        self.bx = bx
        self.by = by
        self.bw = bw
        self.bh = bh
        self.netname = netname
        self.netcode = netcode
        self.is_pth = is_pth
        self.layer = layer

    def GetCenter(self):
        return _V(self.cx, self.cy)

    def GetPosition(self):
        return _V(self.cx, self.cy)

    def GetNetname(self):
        return self.netname

    def GetNetCode(self):
        return self.netcode

    def GetAttribute(self):
        return pcbnew.PAD_ATTRIB_PTH if self.is_pth else 0

    def GetLayer(self):
        return self.layer

    def GetBoundingBox(self):
        return _Box(self.bx, self.by, self.bw, self.bh)


class _FpProxy:
    """Plain-python snapshot of one footprint (reference + its pad snapshots)."""
    __slots__ = ("ref", "pads")

    def __init__(self, ref, pads):
        self.ref = ref
        self.pads = pads

    def GetReference(self):
        return self.ref

    def Pads(self):
        return self.pads


def _capture_pads(board):
    """Snapshot every footprint's pads into plain-python proxies BEFORE the
    board is mutated (see module note above)."""
    fps = []
    for fp in board.GetFootprints():
        pads = []
        for p in fp.Pads():
            pos = p.GetPosition()
            bb = p.GetBoundingBox()
            pads.append(_PadProxy(
                cx=pos.x, cy=pos.y,
                bx=bb.GetX(), by=bb.GetY(), bw=bb.GetWidth(), bh=bb.GetHeight(),
                netname=p.GetNetname(), netcode=p.GetNetCode(),
                is_pth=(p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH),
                layer=p.GetLayer(),
            ))
        fps.append(_FpProxy(fp.GetReference(), pads))
    return fps


def _pad_layers(pad):
    return [F_CU, B_CU] if pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH else [pad.GetLayer()]


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
    """One-time obstacle sets: master_blocked (pad+0.3 mm, 3D) and via2d
    (pad+0.6 mm, 2D) -- every pad of every net. Own-net pads are still in here;
    per-net we pass an own-keepout override of the net's own pad cells.
    ``fps`` is a pre-captured list of footprints (KiCad's swig binding breaks
    board.GetFootprints() once the board is mutated, so callers capture it first)."""
    master_blocked = set()
    via2d = set()
    m = 1_000_000
    me = int(m // GRID_NM)
    for fp in fps:
        for p in fp.Pads():
            master_blocked |= _pad_cells(p, x0, y0, PAD_KEEPOUT_NM)
            for (ix, iy, _l) in _pad_cells(p, x0, y0, VIA_KEEPOUT_NM):
                via2d.add((ix, iy))
    for lay in (F_CU, B_CU):                       # 1 mm board-edge keepout
        for ix in range(0, gx):
            for iy in range(0, gy):
                if ix <= me or iy <= me or ix >= gx - me or iy >= gy - me:
                    master_blocked.add((ix, iy, lay))
    return master_blocked, via2d


def _mark(routed, ix, iy, lay, radius):
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            routed.add((ix + dx, iy + dy, lay))


# ---- light orthogonal ("line") router -------------------------------------

def _free(cell, master, own_keepout, routed, allowed):
    return ((cell not in master or cell in own_keepout)
            and (cell not in routed or cell in allowed))


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


def _add_track(board, nc, ax, ay, bx, by, lay, x0, y0, routed):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(int(ax), int(ay)))
    t.SetEnd(pcbnew.VECTOR2I(int(bx), int(by)))
    t.SetLayer(lay)
    t.SetWidth(TRACK_W_NM)
    t.SetNetCode(nc)
    board.Add(t)
    for (ix, iy, _l) in _seg_cells(ax, ay, bx, by, lay, x0, y0) or []:
        _mark(routed, ix, iy, lay, MARK_RADIUS)   # 0.6 mm radius track keepout


def _add_via(board, nc, vx, vy, x0, y0, routed):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(int(vx), int(vy)))
    v.SetNetCode(nc)
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetDrill(ROUTING_VIA_DRILL_NM)
    v.SetWidth(ROUTING_VIA_DIAM_NM)
    board.Add(v)
    ix = int((vx - x0) // GRID_NM); iy = int((vy - y0) // GRID_NM)
    _mark(routed, ix, iy, F_CU, MARK_RADIUS)     # 0.6 mm radius via keepout
    _mark(routed, ix, iy, B_CU, MARK_RADIUS)


def _try_layer(sx, sy, tx, ty, lay, kw):
    """L (one corner, both orientations) then expanded Z (a detour row or
    column) on ``lay``. The detour sweeps EVERY grid step from 0.3 mm to ~9 mm
    in both directions so the router can thread the 2.54 mm gaps between
    through-hole header pins (a fixed small offset set would land on pin rows
    and miss the gaps). Returns the placed point list (start..end) or None; no
    tracks are placed here -- callers emit + mark."""
    for (cx, cy) in ((tx, sy), (sx, ty)):
        if (_seg_clear(sx, sy, cx, cy, lay, **kw)
                and _seg_clear(cx, cy, tx, ty, lay, **kw)):
            return [(sx, sy), (cx, cy), (tx, ty)]
    for off in range(1, 31):
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


def _emit_path(board, nc, path, lay, x0, y0, routed):
    for (p0, p1) in zip(path, path[1:]):
        _add_track(board, nc, p0[0], p0[1], p1[0], p1[1], lay, x0, y0, routed)


def _via_candidates(pad, x0, y0, via2d, master, own_keepout, routed, allowed,
                    reserved=None):
    """Yield every clean via-up point off ``pad``'s centre (clears the 0.6 mm
    via2d pad keepout) on both layers, nearest first. ``reserved`` is an
    optional extra set of blocked (ix,iy) cells (e.g. the mate via)."""
    px, py = pad.GetCenter().x, pad.GetCenter().y
    blocked = via2d if reserved is None else via2d | reserved
    for D in (1_200_000, 1_500_000, 1_800_000, 1_000_000, 2_100_000, 2_400_000):
        for (vx, vy) in ((px + D, py), (px - D, py), (px, py + D), (px, py - D)):
            vix = int((vx - x0) // GRID_NM); viy = int((vy - y0) // GRID_NM)
            if (vix, viy) in blocked:
                continue
            if (_free((vix, viy, F_CU), master, own_keepout, routed, allowed)
                    and _free((vix, viy, B_CU), master, own_keepout, routed, allowed)):
                yield (vx, vy)


def _route_pair(board, nc, a, b, master, own_keepout, routed, allowed,
                x0, y0, via2d, vip_a, vip_b):
    """Place a clean orthogonal connection between pads a and b. Returns
    (n_tracks, n_vias) on success, None if no clean shape was found.

    Tries, in order of cost:
      1. BGA via-in-pad nets: the via-in-pad is already on B.Cu at a's centre;
         route B.Cu (L/Z) to a via-up near b, then F.Cu to b. Tries every
         clean via-up point near b until one is reachable.
      2. non-BGA nets: F.Cu L then expanded Z (row/column detours).
      3. B.Cu two-via hop (any net): via-up near a, via-up near b, B.Cu L/Z
         between them, short F.Cu stubs pad<->via. General escape for nets F.Cu
         cannot place.
    """
    global _FAIL_REASON
    if vip_b and not vip_a:                    # normalise: via-in-pad pad is `a`
        a, b = b, a
        vip_a, vip_b = vip_b, vip_a
    ax, ay = a.GetCenter().x, a.GetCenter().y
    bx, by = b.GetCenter().x, b.GetCenter().y
    F, B = F_CU, B_CU
    kw = dict(x0=x0, y0=y0, master=master, own_keepout=own_keepout,
              routed=routed, allowed=allowed)

    # --- BGA via-in-pad: escape on B.Cu to a via-up near b, then F.Cu to b ---
    if vip_a:
        found_via = False
        for (vx, vy) in _via_candidates(b, x0, y0, via2d, master, own_keepout,
                                       routed, allowed):
            found_via = True
            bpath = _try_layer(ax, ay, vx, vy, B, kw)        # B.Cu L/Z
            if bpath is not None and _seg_clear(vx, vy, bx, by, F, **kw):
                _emit_path(board, nc, bpath, B, x0, y0, routed)
                _add_via(board, nc, vx, vy, x0, y0, routed)
                _add_track(board, nc, vx, vy, bx, by, F, x0, y0, routed)
                return (len(bpath) - 1 + 1, 1)     # B.Cu segs + 1 F.Cu + 1 via
        _FAIL_REASON = "BGA: no via-up reachable" if found_via else "BGA: no via-up near target"

    # --- non-BGA: F.Cu L then expanded Z ---
    fpath = _try_layer(ax, ay, bx, by, F, kw)
    if fpath is not None:
        _emit_path(board, nc, fpath, F, x0, y0, routed)
        return (len(fpath) - 1, 0)

    # --- B.Cu two-via hop (general escape for any net) ---
    a_via = False
    b_via_any = False
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
            if (_seg_clear(ax, ay, vax, vay, F, **kw)
                    and _seg_clear(vbx, vby, bx, by, F, **kw)):
                kw_b = dict(kw, own_keepout=own_keepout_b)
                bpath = _try_layer(vax, vay, vbx, vby, B, kw_b)
                if bpath is not None:
                    _add_track(board, nc, ax, ay, vax, vay, F, x0, y0, routed)
                    _add_via(board, nc, vax, vay, x0, y0, routed)
                    _emit_path(board, nc, bpath, B, x0, y0, routed)
                    _add_via(board, nc, vbx, vby, x0, y0, routed)
                    _add_track(board, nc, vbx, vby, bx, by, F, x0, y0, routed)
                    return (2 + len(bpath) - 1, 2)
        if not b_via:
            _FAIL_REASON = "hop: no via-up near b"
            return None
    if not a_via:
        _FAIL_REASON = "hop: no via-up near a" + (" (F.Cu L/Z also failed)" if not vip_a else "")
    elif not b_via_any:
        _FAIL_REASON = "hop: vias exist but no clean F.Cu stub / B.Cu path"
    else:
        _FAIL_REASON = "hop: no clean B.Cu path between vias"
    return None


def _clear_tracks(board):
    for t in [t for t in board.Tracks()]:
        if t.GetNetname() != "GND":
            board.Remove(t)


def _apply_hdi_rules(board):
    """Relax the board's minimum through-via / drill / annular to HDI-lite so the
    BGA via-in-pad fanout (0.20 mm / 0.10 mm drill) passes DRC. The 0.5 mm-pitch
    CPG236 cannot be fanned out with the KiCad default 0.45 mm / 0.30 mm
    through-via; a 0.10 mm drill is standard HDI laser-drill capability, and a
    0.05 mm via annular is the standard via-in-pad figure (the PAD copper, not
    the via ring, provides the annular -- via-in-pad is filled+capped)."""
    br = board.GetDesignSettings()
    br.m_ViasMinSize = BGA_VIA_DIAM_NM
    br.m_MinThroughDrill = BGA_VIA_DRILL_NM
    br.m_ViasMinAnnularWidth = 50_000            # 0.05 mm via-in-pad annular


def _bga_fanout(board, x0, y0, routed, fps=None):
    """Place a 0.20/0.10 mm via-in-pad on the SHADOWED netted FPGA balls only --
    those with another netted ball one pitch toward the +X escape edge at the
    same row, so they cannot escape rightward on F.Cu.

    Why NOT every ball: a 0.5 mm-pitch BGA with a 0.20 mm via-in-pad on every
    ball puts a radius-0.4 mm B.Cu keepout on a 0.5 mm grid -- the keepouts
    OVERLAP, turning B.Cu under the BGA into a solid block that traps every
    via (0.1 mm via radius + 0.15 mm clearance + 0.1 mm track + 0.15 mm
    clearance + 0.1 mm via radius = 0.6 mm > 0.5 mm pitch -- a track cannot
    pass between two vias on one layer). So via-in-pad is reserved for the
    balls that genuinely cannot escape on F.Cu; the unshadowed balls escape on
    F.Cu and B.Cu under the BGA stays open for the shadowed vias to route out.
    0.20 mm via in a 0.275 mm pad keeps >0.26 mm clearance to the neighbouring
    pad (DRC-clean at 0.15 mm).

    Returns (vip_net, pad_vip, vip_vias):

      vip_net : net_name -> set of B.Cu cells in the via keepout (radius 3);
                added to that net's ``allowed`` override so the net can route
                through its own via-in-pad area while OTHER nets are kept clear.
      pad_vip : (x,y) pad-centre -> set of B.Cu seed cells; marks a pad as
                having a via-in-pad so the router escapes it on B.Cu. Keyed by
                pad centre (not id(p)) -- pcbnew returns a fresh Python proxy
                on every fp.Pads() call so id(p) is not stable across passes.
      vip_vias: net_name -> the PCB_VIA object placed in-pad, so build() can
                REMOVE the via if the net ends up UNROUTED (a via-in-pad on a
                net that never escaped dangles on B.Cu -> via_dangling warning).
                Removing unrouted vias keeps the DRC report clean.

    The via cells are also marked radius-3 in ``routed`` so other nets keep
    clearance to the 0.20 mm via barrel. 215 unnetted balls stay bare
    (need UG475 pinout).
    """
    fpga = next((fp for fp in (fps if fps is not None else board.GetFootprints())
                 if fp.GetReference() == BGA_REF), None)
    if fpga is None:
        return {}, {}, {}
    # Only the FPGA BGA (CPG236, 238 pads) gets HDI via-in-pad fanout. On the
    # chip module, U1 is the ADG732 (a 49-pad QFN-48, not a BGA) -- a via-in-pad
    # on a QFN pad is a fabrication defect (solder wicks through the unplugged
    # via, starving the joint), so skip fanout there. Guard on pad count:
    # < 100 pads is not the FPGA BGA.
    if len(list(fpga.Pads())) < 100:
        return {}, {}, {}
    by_row = {}
    for p in list(fpga.Pads()):
        nm = p.GetNetname()
        if nm and nm != "GND":
            by_row.setdefault(p.GetPosition().y, []).append(p)
    vip_net, pad_vip, vip_vias = {}, {}, {}
    for y, pads in by_row.items():
        xs = sorted({p.GetPosition().x for p in pads})
        for p in pads:
            x = p.GetPosition().x
            if (x + BGA_PITCH_NM) not in xs:
                continue                       # not shadowed -> escapes on F.Cu
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
            v.SetNetCode(p.GetNetCode())
            v.SetViaType(pcbnew.VIATYPE_THROUGH)
            v.SetDrill(BGA_VIA_DRILL_NM)
            v.SetWidth(BGA_VIA_DIAM_NM)
            board.Add(v)
            ix = int((x - x0) // GRID_NM)
            iy = int((y - y0) // GRID_NM)
            cells = {(ix + dx, iy + dy, B_CU)
                     for dx in range(-MARK_RADIUS, MARK_RADIUS + 1)
                     for dy in range(-MARK_RADIUS, MARK_RADIUS + 1)}
            _mark(routed, ix, iy, B_CU, MARK_RADIUS)  # own-net override via allowed
            vip_net.setdefault(p.GetNetname(), set()).add((ix, iy, B_CU))
            vip_net[p.GetNetname()] |= cells
            pad_vip[(int(x), int(y))] = cells
            vip_vias.setdefault(p.GetNetname(), []).append(v)
    return vip_net, pad_vip, vip_vias


def build(board) -> dict:
    # Snapshot EVERYTHING read from the board BEFORE any mutation: KiCad's swig
    # binding returns bare SwigPyObjects (no .GetCenter/.Pads) for
    # board.GetFootprints()/fp.Pads() after board.Remove() runs in _clear_tracks.
    # A reload would dodge this but OOMs under the hard MemoryMax cap, so we
    # stay single-load: snapshot pads+bbox+netcodes up front, mutate (rules +
    # clear), then build from the snapshot + board.Add() only (Add does not
    # break the proxy layer). Idempotent: _clear_tracks strips any prior run.
    bb = board.GetBoardEdgesBoundingBox()
    fps = _capture_pads(board)
    netcodes = {net.GetNetname(): code
                for code, net in board.GetNetInfo().NetsByNetcode().items()}
    _apply_hdi_rules(board)
    _clear_tracks(board)
    x0, y0 = bb.GetX(), bb.GetY()
    gx = int(bb.GetWidth() // GRID_NM)
    gy = int(bb.GetHeight() // GRID_NM)
    master_blocked, via2d = _build_master(fps, x0, y0, gx, gy)

    routed = set()
    vip_net, pad_vip, vip_vias = _bga_fanout(board, x0, y0, routed, fps=fps)
    bga_vias = len(pad_vip)              # one via-in-pad per shadowed ball

    net_pads = {}
    for fp in fps:
        for p in fp.Pads():
            nm = p.GetNetname()
            if nm and nm not in ("GND", "+3V3") and nm not in EXCLUDE_NETS:
                net_pads.setdefault(nm, []).append(p)

    done, fail, single, nt, nv = [], [], [], 0, 0
    diag = {} if _DEBUG else None

    def _span(ps):
        """Max pairwise centre distance of a net's pads -- a proxy for how much
        room a net needs. Long crossings (esp. BGA->connector) sort FIRST so
        they claim the (still-empty) B.Cu escape layer before short local nets
        fill it and block them."""
        cs = [p.GetCenter() for p in ps]
        best = 0
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                d = (cs[i].x - cs[j].x) ** 2 + (cs[i].y - cs[j].y) ** 2
                if d > best:
                    best = d
        return best

    # BGA-escape nets first (longest crossing first within that group): they
    # need long B.Cu runs and must claim the (still-empty) B.Cu before short
    # local nets fill it. Then non-BGA nets, shortest first.
    for net in sorted(net_pads,
                      key=lambda n: (0 if n in vip_net else 1, -_span(net_pads[n]),
                                     len(net_pads[n]))):
        pads = net_pads[net]
        if len(pads) < 2:
            single.append(net)            # 1-pad net: nothing to connect (not a
            continue                       # routing failure -- e.g. a clock crystal)
        try:
            nc = netcodes[net]
        except KeyError:
            fail.append(net)
            continue
        allowed = set()
        own_keepout = set()
        for p in pads:
            allowed |= _pad_own_cells(p, x0, y0)
            own_keepout |= _pad_cells(p, x0, y0, PAD_KEEPOUT_NM)
        if net in vip_net:                  # own via-in-pad area is passable
            allowed |= vip_net[net]
        order = [pads[0]]
        rem = list(pads[1:])
        while rem:
            last = order[-1]
            best = min(rem, key=lambda q: (q.GetCenter().x - last.GetCenter().x) ** 2
                       + (q.GetCenter().y - last.GetCenter().y) ** 2)
            rem.remove(best)
            order.append(best)
        ok = True
        for a, b in zip(order, order[1:]):
            va = a.GetPosition(); vb = b.GetPosition()
            r = _route_pair(board, nc, a, b, master_blocked, own_keepout, routed,
                           allowed, x0, y0, via2d,
                           pad_vip.get((va.x, va.y)),
                           pad_vip.get((vb.x, vb.y)))
            if r is None:
                ok = False
                if diag is not None:
                    diag[net] = _FAIL_REASON
                break
            nt += r[0]
            nv += r[1]
        (done if ok else fail).append(net)
    if diag is not None:
        for net, reason in sorted(diag.items()):
            print(f"  DEBUG {net}: {reason}")

    # Remove via-in-pad vias on nets that were ROUTED-ATTEMPTED but FAILED: a
    # via-in-pad whose net never escaped dangles on B.Cu (via_dangling warning).
    # PLANE nets (GND/+3V3 -- skipped from line routing at line 566) KEEP their
    # via-in-pads: those vias are through-hole, so they reach the In1.Cu GND /
    # In2.Cu +3V3 plane (added by route_qssc_pcb) and are connected on 2 layers
    # -- NOT dangling. Removing only the FAILED-net via-in-pads leaves the
    # power balls connected to their planes and the failed balls as honest
    # unrouted F.Cu SMDs, and keeps the DRC report free of dangling-via
    # warnings. Done nets keep theirs (the escape track uses the via on B.Cu).
    fail_set = set(fail)
    for net, vs in vip_vias.items():
        if net in fail_set:
            for v in vs:
                board.Remove(v)
            bga_vias -= len(vs)
    return {"routed": done, "unrouted": fail, "single": single, "tracks": nt,
            "vias": nv, "bga_fanout_vias": bga_vias, "excluded": sorted(EXCLUDE_NETS)}


def main() -> dict:
    board = pcbnew.LoadBoard(str(PCB_PATH))
    stats = build(board)
    board.SetModified()
    pcbnew.SaveBoard(str(PCB_PATH), board)
    if __name__ != "__main__":
        return stats
    n = len(stats['routed']) + len(stats['unrouted'])
    print(f"routed {len(stats['routed'])} / {n} | "
          f"tracks {stats['tracks']} vias {stats['vias']} bga-fanout-vias {stats['bga_fanout_vias']} "
          f"| single-pad nets {len(stats['single'])} | excluded {len(stats['excluded'])}")
    if stats["unrouted"]:
        print(f"UNROUTED ({len(stats['unrouted'])}): {', '.join(sorted(stats['unrouted']))}")
    return stats


if __name__ == "__main__":
    main()