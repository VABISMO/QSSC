#!/usr/bin/env python3
"""Headless, RESOURCE-LIGHT SIGNAL router for the QSSC PCB.

Uses ONLY the KiCad pcbnew Python API (no external program, no GUI, no windows).

ROUTING ENGINE -- a light ORTHOGONAL ("line") router, NOT a maze/A* router.
A* on a ~550k-cell grid in pure python is memory-happy and slow (hundreds of
MB, many minutes) -- it froze the host. A line router is O(track length) per
net: no exploration, no big dicts, so it runs in seconds at ~50 MB. It places a
clean L (one corner) or Z (two corners, a detour) shape, collision-checked
against pads + already-routed copper; BGA via-in-pad nets escape on B.Cu. Nets
it cannot place cleanly are left unrouted, never force-routed.

DRC-clean by construction:
  * pad keepout = clearance + half-track-width, so a track CENTRE kept outside
    it guarantees the netclass clearance to pad copper.
  * every routed feature (0.2 mm track OR 0.4 mm routing via) marks a uniform
    radius-3 (0.6 mm) keepout; the worst-case edge clearance is 0.20 mm (via-via),
    >= the 0.15 mm netclass clearance.
  * track endpoints snap to real pad centres (no dangling ends).
  * via-in-pad vias on nets that never escape are REMOVED (else via_dangling).

FPGA BGA fanout (the user's first priority): the 0.8 mm-pitch CSG324 has ~141
netted balls; balls shadowed by a neighbour one column toward the escape edge
cannot escape on F.Cu (BGA pads are F.Cu SMD, so the pad-free inner signal
layers In3/In4 and B.Cu are open under the BGA). A 0.20/0.10 mm via-in-pad
(HDI-lite) gives each shadowed ball an inner-layer seed to escape. 0.8 mm
pitch / 0.40 mm pads -> the via-in-pad keeps >0.20 mm clearance to the
neighbouring ball, DRC-clean at the 0.15 mm netclass clearance. Unnetted balls
are spare IO and stay bare; the netted balls are GND/+3V3/+1V0/+1V8/signal and
all tie via microvia/blind via/through-VIP or escape on F.Cu.

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
import math

import pcbnew

from route_qssc_pcb import (
    F_CU, IN1_CU, IN2_CU, IN3_CU, IN4_CU, B_CU, VIP_MAX_NM,
)

# 6-layer HDI stackup (F.Cu sig / In1.Cu GND / In2.Cu +3V3 / In3.Cu sig /
# In4.Cu sig / B.Cu sig). In1.Cu/In2.Cu are the GND/+3V3 PLANES (adjacent to
# F.Cu so the 117 FPGA power balls tie to them with microvias / short blind
# vias that do NOT reach the signal layers). In3.Cu/In4.Cu/B.Cu are the pad-free
# inner signal escape layers. A THROUGH routing via pierces every copper layer,
# so its keepout is marked on every SIGNAL layer; the planes (In1/In2) are owned
# by route_qssc_pcb and carry no signal tracks here.
SIGNAL_LAYERS = (F_CU, IN3_CU, IN4_CU, B_CU)
PLANE_LAYERS = (IN1_CU, IN2_CU)
# Jump layers for the 2-via hop (Task 5): the hop places a THROUGH via near each
# pad (F.Cu stub pad<->via) and L/Z-routes between the vias on a JUMP layer. B.Cu
# is tried first -- it carries no SMD pads (every discrete is on F.Cu), so it is
# the cleanest jump. If B.Cu is blocked (dense BGA through-via barrels / other
# hops), the inner signal layers In3/In4 are tried: they are pad-free UNDER the
# BGA, so a hop that cannot thread B.Cu may still thread an inner layer. In3/In4
# also carry BGA escape tracks + the +1V0/+1V8 rails, so they are denser -- hence
# tried after B.Cu. F.Cu is NOT a jump layer (it is the stub layer).
JUMP_LAYERS = (B_CU, IN3_CU, IN4_CU)

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

# Nets the autorouter must NOT touch -- left unrouted for a manual GUI
# session. The USB-C diff pair (USB_DP/USB_DM) needs 90-ohm controlled-impedance
# geometry + length-skew tuning a line router cannot do, and its USB-C edge pads
# (J10 at the 85 mm board edge) defeat via-up -- so it is GUI. (Pad
# keepout alone is not enough: the 0.5 mm-pitch SMD pads sit so close that a
# track leaving one clips the other.)
EXCLUDE_NETS = {"USB_DP", "USB_DM"}

# ---- per-netclass track width + mark radius (Task 6) -----------------------
# Net->netclass map (mirrors gen_qssc_kicad.net_class -- replicated locally to
# avoid importing the heavy generator module into the pcbnew process). The
# netclass->width map mirrors the track_width values authored in the .kicad_pro
# netclasses (Default/USB/Analog 0.2, RF 0.3, Power 0.5 mm).
_RF_NETS = {"RF_OUT", "RF_OUT_N", "RF_IN", "RF_AMP_OUT", "J3_SIG", "LF_CP", "LF_MID",
            "LF_OUT", "CPout", "VTUNE", "refclk_25m"}
_USB_NETS = {"USB_DP", "USB_DM"}
_ANALOG_NETS = {"VREFP", "VREFN"}   # TIA/XADC analog nets removed in the
                                    # single-board parallel pivot.
# Chip die-pad nets (DET_IN0..63, emitter_cat0..63) get the 0.10 mm-clearance
# "Chip" netclass authored in gen_qssc_kicad so the 0.30 mm-pitch dog-bone vias
# (0.125 mm neighbour clearance) pass DRC. Mirror gen_qssc_kicad._is_chip_net.
# NOTE: the nets are emitter_cat{i} (per-pixel CATHODES sunk by the TLC5947),
# NOT emitter_anode -- the anode is the single shared EM_ANODE -> +12V_EMIT pad.
_NETCLASS_WIDTH_NM = {"Default": 200_000, "RF": 300_000, "Power": 500_000,
                      "USB": 200_000, "Analog": 200_000, "Chip": 150_000}


def _is_chip_net(net):
    return net.startswith("DET_IN") or net.startswith("emitter_cat")


def _net_class(net):
    if _is_chip_net(net):
        return "Chip"
    if net in _RF_NETS:
        return "RF"
    if net in _USB_NETS:
        return "USB"
    if net in _ANALOG_NETS:
        return "Analog"
    return "Default"


def _net_width_nm(net):
    return _NETCLASS_WIDTH_NM[_net_class(net)]


# Widest track on the board (Power 0.5 mm). The mark radius for a feature of
# width w is ceil((w/2 + CLEARANCE + WMAX/2)/GRID): the feature's mark covers its
# own half-width + clearance + the WIDEST possible neighbour's half-width, so
# when a later feature of ANY width (<= WMAX) checks _free against this mark its
# centre is provably >= w/2 + clearance + w_new/2 away -- the no-short-by-
# construction proof holds for mixed widths without needing the neighbour's
# width at check time.  w=0.2/0.3 -> r3, w=0.5 -> r4; a 0.4 mm via (w=0.4) -> r3.
WMAX_NM = 500_000
CLEARANCE_NM = 150_000


def _mark_radius_for_width(w_nm):
    return math.ceil((w_nm / 2 + CLEARANCE_NM + WMAX_NM / 2) / GRID_NM)

# ---- FPGA BGA fanout (HDI-lite via-in-pad, 6-layer) ----
BGA_REF = "U1"
BGA_PITCH_NM = 800_000            # 0.8 mm CSG324 pitch (XC7A35T-1CSG324C)
BGA_VIA_DIAM_NM = 200_000         # 0.20 mm via-in-pad (fits 0.40 mm CSG324 ball)
BGA_VIA_DRILL_NM = 100_000         # 0.10 mm HDI drill

# 6-layer HDI BGA fanout. The FPGA's ~140 netted balls tie to their nets by
# via TYPE, not all through-vias (117 through-vias blocked the inner signal
# layers solid at 0.5 mm pitch):
#   * GND (76 balls): MICROVIA F.Cu->In1.Cu (one dielectric, 0.15 mm). The
#     barrel reaches ONLY F.Cu + In1 (GND plane) -- it does NOT occupy In2/In3/
#     In4/B.Cu, so it marks NO keepout on the signal escape layers.
#   * +3V3 (29 balls): BLIND via F.Cu->In2.Cu (through In1, anti-pad in the GND
#     plane). Reaches ONLY F.Cu + In1 + In2 (+3V3 plane) -- no keepout on the
#     signal escape layers.
#   * +1V0/+1V8 (11 balls): THROUGH via-in-pad whose barrel reaches In3/In4/B.Cu
#     so the rail TRACK (Task 7, on the rail layer) can connect the balls. These
#     are TIE vias (no escape track); they mark keepout on the signal layers and
#     get a vip_net allowed override so the Task-7 rail can pass their own area.
#   * shadowed signals/config/JTAG: THROUGH via-in-pad escaping on In3/In4 (pad-
#     free under the BGA), column parity (even col -> In3, odd -> In4) so a +X
#     escape passes under same-row neighbours that sit on the OTHER inner layer.
#   * unshadowed signals: no VIP, escape on F.Cu as before.
# In3/In4/B.Cu thus carry only ~27 through-VIPs (11 power + ~16 shadowed
# signals), not 117 -- sparse enough for the line router to thread escapes.
PLANE_NETS = {"GND", "+3V3"}
POWER_NETS = {"+1V0", "+1V8", "+5V", "+5V_RF", "+5V_IN",
              "+12V_EMIT", "+12V_EMIT_IN"}
# Fixed rail layer for the two non-plane FPGA power rails (no plane of their
# own -- Task 7 routes them as 0.5 mm tracks on this layer). +1V0 on In3, +1V8
# on In4 keeps the two rails on separate layers so their through-via barrels do
# not force the other rail to route around them on the same layer.
POWER_RAIL_LAYER = {"+1V0": IN3_CU, "+1V8": IN4_CU}
# Dedicated routing layer for EVERY power rail (Task 7 power router). The two
# BGA core/aux rails route on their POWER_RAIL_LAYER (In3/In4) where their
# through-via barrels already sit. The three surface 5V rails route on B.Cu --
# which carries NO SMD pads (every discrete is on F.Cu), so a 0.5 mm rail runs
# clear of the surface GND / other-net pad keepouts that defeat the F.Cu line
# router (the decoupling caps' own GND pads sit side-by-side with their +rail
# pad on F.Cu and block any same-row rail). Only sparse PTH pins and the BGA
# through-via barrels are obstacles on B.Cu. Routed in span order so the
# longest crossing claims the (empty) layer first; later rails L/Z-detour the
# earlier ones' marks.
POWER_ROUTER_LAYER = {"+1V0": IN3_CU, "+1V8": IN4_CU,
                      "+5V": B_CU, "+5V_RF": B_CU, "+5V_IN": B_CU,
                      "+12V_EMIT": B_CU, "+12V_EMIT_IN": B_CU}
# BGA through-VIPs are 0.20 mm (0.10 mm radius) -- smaller than the 0.40 mm
# routing vias -- so their keepout on the ESCAPE layer is tighter (radius 2 =
# 0.4 mm). A 0.2 mm track can then pass between two same-layer VIPs at 1.0 mm
# spacing: the midpoint is 0.5 mm from each VIP, > the 0.35 mm the track needs
# (0.10 via radius + 0.15 clearance + 0.10 half track). Edge clearance
# 0.4 - 0.10 - 0.10 = 0.20 mm >= 0.15 mm netclass. The other signal layers keep
# MARK_RADIUS 3 (no escape track there, blocking crossing tracks is free).
BGA_VIP_MARK_RADIUS = 2


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
                 "netname", "netcode", "is_pth", "layer", "attrib", "has_hole")

    def __init__(self, cx, cy, bx, by, bw, bh,
                 netname, netcode, is_pth, layer, attrib, has_hole):
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
        self.attrib = attrib
        self.has_hole = has_hole

    def GetCenter(self):
        return _V(self.cx, self.cy)

    def GetPosition(self):
        return _V(self.cx, self.cy)

    def GetNetname(self):
        return self.netname

    def GetNetCode(self):
        return self.netcode

    def GetAttribute(self):
        # Return the REAL pad attribute, not a synthesised 0/PTH. In KiCad 8
        # PAD_ATTRIB_PTH == 0 and PAD_ATTRIB_SMD == 1, so returning 0 for a
        # non-PTH pad would make _pad_layers treat every SMD pad as through-hole
        # and mark it on ALL four signal layers -- blocking the inner layers
        # with surface-pad keepouts that should not be there (the bug that
        # defeated inner-layer power routing: an SMD diode's pad blocked In3).
        return self.attrib

    def GetLayer(self):
        return self.layer

    def GetBoundingBox(self):
        return _Box(self.bx, self.by, self.bw, self.bh)


class _FpProxy:
    """Plain-python snapshot of one footprint (reference + position + pad
    snapshots).  Position is the footprint origin (die body centre for the
    QSSC_Chip, the BGA array centre for the FPGA) -- captured BEFORE
    _clear_tracks so swig still resolves GetPosition()."""
    __slots__ = ("ref", "pads", "x", "y")

    def __init__(self, ref, pads, x=0, y=0):
        self.ref = ref
        self.pads = pads
        self.x = x
        self.y = y

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
        fpos = fp.GetPosition()
        for p in fp.Pads():
            pos = p.GetPosition()
            bb = p.GetBoundingBox()
            pads.append(_PadProxy(
                cx=pos.x, cy=pos.y,
                bx=bb.GetX(), by=bb.GetY(), bw=bb.GetWidth(), bh=bb.GetHeight(),
                netname=p.GetNetname(), netcode=p.GetNetCode(),
                is_pth=(p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH),
                layer=p.GetLayer(),
                attrib=p.GetAttribute(),
                has_hole=(p.GetDrillSize().x > 0),
            ))
        fps.append(_FpProxy(fp.GetReference(), pads, fpos.x, fpos.y))
    return fps


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
        return list(SIGNAL_LAYERS)
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
    for lay in SIGNAL_LAYERS:                       # 1 mm board-edge keepout
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
    cells another net already holds. The centre-only check the via-placement
    helpers used previously only tested (vix, viy) per layer: a track 0.1-0.6 mm
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
            for lay in SIGNAL_LAYERS:
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
    for lay in SIGNAL_LAYERS:
        _mark(routed, ix, iy, lay, vr, own)


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
    global _FAIL_REASON
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
        esc_layers = [esc_lay] + [l for l in JUMP_LAYERS if l != esc_lay]
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
        _FAIL_REASON = "BGA: no via-up reachable" if found_via else "BGA: no via-up near target"

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
                    for jump in JUMP_LAYERS:
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
            _FAIL_REASON = "BGA: no edge escape + surface route"

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
            for jump in JUMP_LAYERS:
                bpath = _try_layer(vax, vay, vbx, vby, jump, kw_b)
                if bpath is not None:
                    _add_track(board, nc, ax, ay, vax, vay, F, x0, y0, routed, width_nm, mark_r, allowed)
                    _add_via(board, nc, vax, vay, x0, y0, routed)
                    _emit_path(board, nc, bpath, jump, x0, y0, routed, width_nm, mark_r, allowed)
                    _add_via(board, nc, vbx, vby, x0, y0, routed)
                    _add_track(board, nc, vbx, vby, bx, by, F, x0, y0, routed, width_nm, mark_r, allowed)
                    return (2 + len(bpath) - 1, 2)
        if not b_via:
            _FAIL_REASON = "hop: no via-up near b"
            return None
    if not a_via:
        _FAIL_REASON = "hop: no via-up near a" + (" (F.Cu L/Z also failed)" if not vip_a else "")
    elif not b_via_any:
        _FAIL_REASON = "hop: vias exist but no clean F.Cu stub / jump path"
    elif not stub_ok:
        _FAIL_REASON = "hop: vias exist but no clean F.Cu stub"
    else:
        _FAIL_REASON = "hop: no clean jump path on any signal layer"
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
    global _FAIL_REASON
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
        _FAIL_REASON = f"power {net}: fewer than 2 tieable pads"
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
    _FAIL_REASON = "" if (nt or skip_pad or skip_edge) else f"power {net}: no edges"
    return (nt, nv, skip_pad, skip_edge)


def _clear_tracks(board):
    """Idempotent reset of this router's own copper. Strip every track/via that
    is NOT a wide plane via owned by route_qssc_pcb -- i.e. keep only the GND /
    +3V3 tracks/vias WIDER than VIP_MAX_NM (the 0.6 mm stitching mesh + rail-pad
    vias). Everything else (signal tracks, the 0.20 mm BGA via-in-pad VIPs this
    router owns, power-rail tracks) is removed so a re-run rebuilds it cleanly.
    This keeps a prior route_qssc_pcb plane foundation intact when route_signals
    is re-run on top of it (the inverse of route_qssc_pcb._clear_routed, which
    keeps the small VIPs and signal copper)."""
    for t in [t for t in board.Tracks()]:
        nm = t.GetNetname()
        if nm in ("GND", "+3V3") and t.GetWidth() > VIP_MAX_NM:
            continue                      # wide plane via/track -- not ours
        board.Remove(t)


def _apply_hdi_rules(board):
    """Relax the board's minimum via / drill / annular to HDI so the BGA fanout
    passes DRC: microvias (0.20 mm / 0.10 mm drill, F.Cu->In1 GND), blind vias
    (0.30 mm / 0.15 mm drill, F.Cu->In2 +3V3) and through via-in-pad (0.20 mm /
    0.10 mm). The 0.8 mm-pitch CSG324 BGA fanout is comfortable with these HDI
    sizes. A 0.10 mm drill is standard HDI laser-drill capability; a 0.05 mm
    annular is the standard via-in-pad figure (the PAD copper, not the via ring,
    provides the annular -- via-in-pad is filled+capped). Blind/buried + microvias
    must be explicitly allowed in the design rules."""
    br = board.GetDesignSettings()
    br.m_ViasMinSize = BGA_VIA_DIAM_NM
    br.m_MinThroughDrill = BGA_VIA_DRILL_NM
    br.m_ViasMinAnnularWidth = 50_000            # 0.05 mm via-in-pad annular
    br.m_BlindBuriedViaAllowed = True
    br.m_MicroViasMinSize = BGA_VIA_DIAM_NM       # 0.20 mm microvia
    br.m_MicroViasMinDrill = BGA_VIA_DRILL_NM     # 0.10 mm laser drill


def _bga_fanout(board, x0, y0, routed, fps=None):
    """HDI via-in-pad fanout for the 0.8 mm-pitch FPGA BGA (CSG324) on the
    6-layer stackup. Each netted ball ties to its net by via TYPE (see the
    PLANE_NETS / POWER_RAIL_LAYER / column-parity notes above):

      * GND  -> microvia  F.Cu->In1.Cu  (no signal-layer keepout)
      * +3V3 -> blind via F.Cu->In2.Cu  (no signal-layer keepout)
      * +1V0/+1V8 -> through via-in-pad tying to the rail layer (In3/In4)
      * shadowed signal/config/JTAG -> through via-in-pad escaping on In3/In4
      * unshadowed signal -> no VIP (F.Cu escape)

    Returns (vip_net, pad_vip, vip_vias):

      vip_net : net_name -> set of (ix,iy,layer) cells (radius
                BGA_VIP_MARK_RADIUS) on the net's escape/rail layer; added to
                that net's ``allowed`` override so its own track can pass its
                via-in-pad area while OTHER nets are kept clear. PLANE_NETS
                (GND/+3V3) are absent -- their microvias/blind vias do not
                occupy any signal layer, so no keepout is needed.
      pad_vip : (x,y) pad-centre -> (esc_lay, seed_cells) for SHADOWED SIGNAL
                balls; tells _route_pair which inner layer to escape on. Power
                and plane balls are NOT here (no _route_pair escape).
      vip_vias: net_name -> [PCB_VIA, ...] for every via placed, so build() can
                REMOVE a SIGNAL net's vias if it ends up UNROUTED (a through
                via-in-pad whose net never escaped dangles on In3/In4/B.Cu).
                PLANE-net and POWER-net vias are never removed (not in
                fail_set): GND/+3V3 tie to a plane; +1V0/+1V8 tie to a rail the
                Task-7 power-router will connect.

    Through-via barrels mark keepout in ``routed`` on the inner signal layers +
    B.Cu (radius BGA_VIP_MARK_RADIUS on the escape/rail layer, MARK_RADIUS on
    the others; F.Cu is skipped -- the BGA pad's 0.3 mm PAD_KEEPOUT already
    protects the barrel there). Unnetted balls stay bare (spare IO).
    """
    fpga = next((fp for fp in (fps if fps is not None else board.GetFootprints())
                 if fp.GetReference() == BGA_REF), None)
    if fpga is None:
        return {}, {}, {}
    # Only the FPGA BGA (CSG324, 324 pads) gets HDI via-in-pad fanout. Guard on
    # pad count: < 100 pads is not the FPGA BGA (the QSSC_Chip die faces are
    # fine-pitch QFN-style edge-pad dies, handled by _chip_fanout, not here).
    if len(list(fpga.Pads())) < 100:
        return {}, {}, {}
    pads = list(fpga.Pads())
    netted = [p for p in pads if p.GetNetname()]
    # BGA column origin for column-parity escape-layer assignment (even col ->
    # In3.Cu, odd col -> In4.Cu). Same-layer escape VIPs then sit 2 columns
    # (1.0 mm) apart -- wide enough to thread with BGA_VIP_MARK_RADIUS.
    xs_all = sorted({p.GetPosition().x for p in netted})
    x_min = xs_all[0] if xs_all else 0

    # Group ALL netted balls by row (incl. GND/+3V3) so a signal ball shadowed
    # by a plane ball is still detected as shadowed.
    by_row = {}
    for p in netted:
        by_row.setdefault(p.GetPosition().y, []).append(p)

    def _microvia(px, py, p, top, bot):
        """F.Cu->bot single-dielectric microvia (laser drill, 0.10 mm)."""
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(int(px), int(py)))
        v.SetNetCode(p.GetNetCode())
        v.SetViaType(pcbnew.VIATYPE_MICROVIA)
        v.SetDrill(BGA_VIA_DRILL_NM)
        v.SetWidth(BGA_VIA_DIAM_NM)
        ls = pcbnew.LSET()
        ls.AddLayer(top); ls.AddLayer(bot)
        v.SetLayerSet(ls)
        board.Add(v)
        return v

    def _blind_via(px, py, p, layers):
        """F.Cu->In2 blind via (through In1, anti-pad carved by the GND plane).
        Slightly larger than the microvia (0.30 mm / 0.15 mm) per HDI norm."""
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(int(px), int(py)))
        v.SetNetCode(p.GetNetCode())
        v.SetViaType(pcbnew.VIATYPE_BLIND_BURIED)
        v.SetDrill(150_000)             # 0.15 mm blind drill
        v.SetWidth(300_000)             # 0.30 mm blind via
        ls = pcbnew.LSET()
        for lay in layers:
            ls.AddLayer(lay)
        v.SetLayerSet(ls)
        board.Add(v)
        return v

    def _through_vip(px, py, p):
        """0.20/0.10 mm through via-in-pad (reaches every copper layer)."""
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(int(px), int(py)))
        v.SetNetCode(p.GetNetCode())
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetDrill(BGA_VIA_DRILL_NM)
        v.SetWidth(BGA_VIA_DIAM_NM)
        board.Add(v)
        return v

    vip_net, pad_vip, vip_vias = {}, {}, {}
    # cell -> set of netcodes whose VIP barrel mark (r2 on In3/In4/B.Cu) covers
    # it. A cell in the r2 marks of TWO DIFFERENT nets -- the 0.3 mm overlap in
    # the gap between two 0.5 mm-pitch neighbour balls -- is a cell the per-net
    # vip_net allowed override must NEVER relax: a track or via-up centred there
    # is ~0.3 mm from the neighbour's VIP barrel -> 0.10 mm clearance (the
    # CONF_DONE via vs CONF_INIT track on B.Cu; TIA_ADC vs VREFP). This is the
    # VIP-side twin of multi_keepout: both stop a per-net override from
    # relaxing a cell a neighbour already owns.
    vip_cell_nets: dict[tuple, set] = {}
    for y, rowpads in by_row.items():
        xs = sorted({p.GetPosition().x for p in rowpads})
        for p in rowpads:
            nm = p.GetNetname()
            x = p.GetPosition().x
            ix = int((x - x0) // GRID_NM)
            iy = int((y - y0) // GRID_NM)

            # --- PLANE nets: microvia / blind via, NO signal-layer keepout ---
            if nm == "GND":
                v = _microvia(x, y, p, pcbnew.F_Cu, pcbnew.In1_Cu)
                vip_vias.setdefault(nm, []).append(v)
                continue
            if nm == "+3V3":
                v = _blind_via(x, y, p, (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu))
                vip_vias.setdefault(nm, []).append(v)
                continue

            # --- power rails (+1V0/+1V8): through via tying to a fixed rail layer
            if nm in POWER_RAIL_LAYER:
                esc_lay = POWER_RAIL_LAYER[nm]
                v = _through_vip(x, y, p)
            else:                                 # signal / config / JTAG / VREFP
                if (x + BGA_PITCH_NM) not in xs:
                    continue                      # unshadowed -> escapes on F.Cu
                col = (x - x_min) // BGA_PITCH_NM
                esc_lay = IN3_CU if (col % 2 == 0) else IN4_CU
                v = _through_vip(x, y, p)

            # THROUGH barrel keepout on the inner signal layers + B.Cu. F.Cu is
            # NOT marked (BGA pad 0.3 mm PAD_KEEPOUT already protects the barrel
            # there; marking it needlessly blocks non-BGA F.Cu routes past the
            # BGA). ALL non-F.Cu signal layers get the tight BGA_VIP_MARK_RADIUS
            # (2 = 0.4 mm): the VIP barrel is 0.20 mm, and on the 0.2 mm grid the
            # closest free cell to a radius-2 mark is 0.6 mm from the barrel
            # centre -- >= the 0.5 mm a 0.5 mm power track needs (w/2 0.25 +
            # clearance 0.15 + barrel 0.10), so even the widest rail stays clear.
            # The old MARK_RADIUS (3 = 0.6 mm) on non-escape layers over-blocked:
            # an opposite-parity neighbour ball (1.0 mm away on the OTHER inner
            # layer) marked THIS layer at radius 3 and reached into the escape
            # column of the ball between them, defeating the 1 mm edge escape.
            for lay in SIGNAL_LAYERS:
                if lay == F_CU:
                    continue
                _mark(routed, ix, iy, lay, BGA_VIP_MARK_RADIUS)
            r = BGA_VIP_MARK_RADIUS
            # The VIP is a THROUGH via -- its barrel ties a's pad to EVERY signal
            # layer, not just esc_lay. Seed the net's `allowed` override on all
            # non-F.Cu signal layers (F.Cu is not marked in `routed` -- the BGA
            # pad's own-cells handle it) so the net's own track can ESCAPE its VIP
            # on B.Cu or the other inner layer when esc_lay is congested (Task 5).
            cells = {(ix + dx, iy + dy, lay)
                     for lay in SIGNAL_LAYERS if lay != F_CU
                     for dx in range(-r, r + 1) for dy in range(-r, r + 1)}
            vip_net.setdefault(nm, set()).add((ix, iy, esc_lay))
            vip_net[nm] |= cells
            nc = p.GetNetCode()
            key = nc if nc is not None and nc >= 0 else -1
            for c in cells:
                vip_cell_nets.setdefault(c, set()).add(key)
            # Both signals AND the +1V0/+1V8 power rails get an escape pad on
            # their rail layer: the rail's 0.5 mm track (Task 7) daisy-chains the
            # through-via barrels on In3.Cu (+1V0) / In4.Cu (+1V8) and reaches the
            # regulator output via a through-via hop. Without this entry the rail
            # balls would be TIE-only (no escape) and the rail could never connect
            # to the regulator -> +1V0/+1V8 would stay unconnected.
            pad_vip[(int(x), int(y))] = (esc_lay, cells)
            vip_vias.setdefault(nm, []).append(v)
    # Strip the contested overlap cells from every net's allowed override: a
    # VIP cell two nets' marks share is never safe for either's own track/via.
    multi_vip = {c for c, nets in vip_cell_nets.items() if len(nets) > 1}
    for nm in vip_net:
        vip_net[nm] -= multi_vip
    return vip_net, pad_vip, vip_vias


def _chip_fanout(board, x0, y0, routed, fps=None):
    """Die-face fanout for the QSSC optical chip.  The die carries an 8x8 GRID
    of 64 electrodes across the 20x20 mm face at ~2.5 mm pitch (1.2 mm pads ->
    1.3 mm inter-pad gaps).  At this COARSE pitch a 0.2 mm track escapes each
    pad directly through the gaps, so NO dog-bone via fanout is needed: the
    earlier 0.30 mm perimeter-row design required a staggered dog-bone (the line
    router could not thread the 0.30 mm field), but that stub+via geometry SHORTS
    neighboring pads when applied to an interior 8x8 grid (the per-pad escape
    direction lands a via on the adjacent-row pad -> DRC shorting_items between
    emitter_cat{i} and emitter_cat{i+8}).  So this is now a NO-OP: the 64 die
    nets are left bare and force-failed in the line router (see `structured_chip`
    in build()), and FreeRouting escapes the 8x8 grid directly -- a standard
    autorouter job at 2.5 mm pitch.

    Returns the empty 5-tuple so the caller's merges are no-ops and the chip
    pads remain in net_pads (the force-fail skips them for the line router;
    FreeRouting's DSN export carries every net regardless of net_pads/fail)."""
    return set(), {}, {}, {}, {}


def build(board) -> dict:
    # Recompute the layer sets for THIS board's copper count. The main control
    # board is 6-layer HDI (signal layers F.Cu/In3.Cu/In4.Cu/B.Cu, planes
    # In1.Cu/In2.Cu); the detector head is 4-layer (signal layers F.Cu/B.Cu,
    # planes In1.Cu/In2.Cu). The hardcoded 6-layer module globals would route on
    # non-existent In3/In4 copper IDs on the head, so override them here. JUMP
    # layers lose In3/In4 on the head (only B.Cu remains); the per-via escape
    # layer is still added separately in _route_pair, so F.Cu<->B.Cu hops work.
    global SIGNAL_LAYERS, PLANE_LAYERS, JUMP_LAYERS
    if board.GetCopperLayerCount() <= 4:
        SIGNAL_LAYERS = (F_CU, B_CU)
        PLANE_LAYERS = (IN1_CU, IN2_CU)
        JUMP_LAYERS = (B_CU,)
    else:
        SIGNAL_LAYERS = (F_CU, IN3_CU, IN4_CU, B_CU)
        PLANE_LAYERS = (IN1_CU, IN2_CU)
        JUMP_LAYERS = (B_CU, IN3_CU, IN4_CU)
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
    master_blocked, via2d, all_pad_copper, multi_keepout = _build_master(fps, x0, y0, gx, gy)

    routed = set()
    vip_net, pad_vip, vip_vias = _bga_fanout(board, x0, y0, routed, fps=fps)
    # QSSC_Chip die-face fanout: 0.15 mm stub (on the pad's own layer) + a
    # staggered through-via per pad, escaping on the opposite surface. Adds via
    # pseudo-pads (via_pads) and merges its vip dicts. Main board: 65 EM/ANODE
    # pads on F.Cu escape B.Cu; head board: 64 DET pads on B.Cu escape F.Cu.
    chip_pad_centers, via_pads, cv_net, cv_pad_vip, cv_vias = _chip_fanout(
        board, x0, y0, routed, fps=fps)
    vip_net.update(cv_net)
    pad_vip.update(cv_pad_vip)
    for nm, vs in cv_vias.items():
        vip_vias.setdefault(nm, []).extend(vs)
    # The chip dog-bone vias are a COMPLETE fanout (pad -> 0.15 mm stub ->
    # through-via) that the line router finishes on the escape surface; unlike a
    # BGA via-in-pad that never escaped (a true dangling barrel), a dog-bone via
    # is a valid net tie the line router routes FROM. So the failed-net VIP
    # removal block below must NEVER tear them down (else dead stubs end in
    # mid-air). Track them by (x,y) position (swig proxies re-wrap on access, so
    # id() is unreliable) so the removal loop can skip them while still dropping
    # BGA VIPs of nets that genuinely never escaped.
    chip_via_pos = {(int(v.GetX()), int(v.GetY()))
                    for vs in cv_vias.values() for v in vs}
    # BGA pad bounding box (nm) for the edge-escape phase 2b: the FPGA footprint
    # from the snapshot. None if there is no BGA -> phase 2b skips.
    bga_bbox = None
    fpga_fp = next((fp for fp in fps if fp.GetReference() == BGA_REF), None)
    if fpga_fp is not None and len(fpga_fp.Pads()) >= 100:
        pxs = [p.GetPosition().x for p in fpga_fp.Pads()]
        pys = [p.GetPosition().y for p in fpga_fp.Pads()]
        bga_bbox = (min(pxs), min(pys), max(pxs), max(pys))
    # every via placed by _bga_fanout + _chip_fanout; the removal block below
    # decrements the through-vias of FAILED signal nets.
    bga_vias = sum(len(vs) for vs in vip_vias.values())

    # _route_power routes the five power rails FIRST on dedicated inner/bottom
    # layers (POWER_ROUTER_LAYER), then _route_pair line-routes the signals.
    # Plane nets (GND/+3V3) tie to their inner planes via microvias/blind vias
    # and stay excluded (no tracks). Each power rail is a 0.5 mm Power-class MST
    # of L/Z tracks on its rail layer: +1V0/+1V8 on In3/In4 (their BGA through-via
    # barrels already sit there), +5V/+5V_RF/+5V_IN on B.Cu (no SMD pads there, so
    # the rail clears the surface GND/other-net pad keepouts that block it on
    # F.Cu). Every SMD rail pad gets a through-via tie to the rail layer; the BGA
    # power balls are tied at their own barrel. Routed in span order (longest
    # crossing first) so each rail claims the still-empty layer. Power VIPs are
    # never removed as "dangling" (a rail net only fails if a SEGMENT is
    # unreachable -- the BGA VIPs themselves always tie).
    net_pads = {}
    for fp in fps:
        for p in fp.Pads():
            nm = p.GetNetname()
            if nm and nm not in PLANE_NETS and nm not in EXCLUDE_NETS:
                # skip the 128-pad chip die pads: _chip_fanout already tied each
                # to a staggered dog-bone via (a via_pads pseudo-pad). Routing
                # from the chip pad itself would duplicate the stub and hit the
                # 0.30 mm-pitch keepout overlap; route from the via instead.
                if (int(p.GetPosition().x), int(p.GetPosition().y)) in chip_pad_centers:
                    continue
                net_pads.setdefault(nm, []).append(p)
    # add the chip-fanout via pseudo-pads as routing endpoints (the stub already
    # connects each chip pad to its via; the line router routes via<->target).
    for nm, ps in via_pads.items():
        net_pads.setdefault(nm, []).extend(ps)

    done, partial, fail, single, nt, nv = [], [], [], [], 0, 0
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

    # ---- power rails FIRST (Task 7): dedicated inner/bottom layer, MST L/Z ----
    # Routed before signals so each rail's 0.5 mm track claims the still-empty
    # rail layer. Span order (longest crossing first): the rail that must cross
    # the board (e.g. +5V_IN VBUS bottom->fuse top, +5V_RF FB1->RF section)
    # claims B.Cu before shorter rails fill it. A failed rail is left
    # to GUI; its BGA through-via barrels stay (not removed below).
    for net in sorted((n for n in net_pads if n in POWER_ROUTER_LAYER),
                      key=lambda n: -_span(net_pads[n])):
        pads = net_pads[net]
        if len(pads) < 2:
            single.append(net)
            continue
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
        if net in vip_net:
            allowed |= vip_net[net]
        own_keepout -= multi_keepout            # never override a cell a neighbour pad's ring also covers
        own_keepout -= all_pad_copper - allowed
        width_nm = _net_width_nm(net)
        mark_r = _mark_radius_for_width(width_nm)
        r = _route_power(board, nc, net, pads, POWER_ROUTER_LAYER[net],
                         master_blocked, own_keepout, routed, allowed,
                         x0, y0, via2d, pad_vip, width_nm, mark_r)
        # _route_power now returns a 4-tuple (nt, nv, skip_pad, skip_edge) and
        # NEVER returns None: skip_pad/skip_edge count pads/edges left to GUI.
        # nt==0  -> nothing routable at all .......................... fail
        # skip==0 -> whole rail routed ............................... done
        # skip>0  -> partial (some pads/edges GUI) ........... partial
        nt_r, nv_r, skip_pad, skip_edge = r
        if nt_r == 0:
            fail.append(net)
            if diag is not None:
                diag[net] = _FAIL_REASON or f"power {net}: no edges routed"
        else:
            nt += nt_r
            nv += nv_r
            if skip_pad or skip_edge:
                partial.append(net)
                if diag is not None:
                    diag[net] = (f"power {net}: {skip_pad} pad(s) + "
                                 f"{skip_edge} edge(s) GUI")
            else:
                done.append(net)

    # ---- signals: _route_pair line router ----
    # BGA-escape nets first (longest crossing first within that group): they
    # need long B.Cu runs and must claim the (still-empty) B.Cu before short
    # local nets fill it. Then non-BGA nets, shortest first. Power rails are
    # already routed above and skipped here.
    # `structured_chip` lists the chip nets already closed by a deterministic
    # structured fanout. Empty here => ALL chip nets are deferred (appended to
    # `fail`) for FreeRouting. The 8x8 die grid is 2.5 mm pitch (1.3 mm gaps) so
    # _chip_fanout is a no-op (no dog-bone vias) and FreeRouting escapes the
    # bare pads directly; the line router's 0.2 mm grid cannot do the long
    # crossing pad->target fan without blocking every short net.
    structured_chip = set()
    for net in sorted(net_pads,
                      key=lambda n: (0 if n in vip_net else 1, -_span(net_pads[n]),
                                     len(net_pads[n]))):
        if net in POWER_ROUTER_LAYER:
            continue
        if _is_chip_net(net) and net not in structured_chip:
            # Chip nets are closed by the deterministic _structured_bus_fanout
            # (crossing-free by construction) BEFORE this loop; any chip net NOT
            # in `structured_chip` was not pairable there and is left for
            # FreeRouting. The line router's 0.2 mm grid cannot do the long
            # crossing breakout->target fan (chip nets sort first as vip_net
            # members and crisscross the board, blocking every short net), so do
            # NOT attempt them here. The dog-bone vias are preserved by the
            # chip_via_pos guard in the VIP-removal block below.
            fail.append(net)
            continue
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
        # The own-ring override (own_keepout) lets a track leave its own pad
        # through the 0.3 mm keepout ring. But two same-row BGA balls 0.5 mm
        # apart have overlapping rings, so one ball's ring can extend INTO the
        # neighbour ball's copper -- and without this strip the override would
        # mark that copper free and short the two nets (the clk_100mhz A16 ->
        # mod_sync A15 short). Strip every OTHER pad's bare copper (expand 0)
        # from the override; own pad copper (in `allowed`) is kept, so the
        # track can still leave its own pad, just not through a neighbour.
        # `multi_keepout` is the stronger guard: it removes any cell covered by
        # the keepout ring of a DIFFERENT net (or an unnetted pad), so a track
        # can never be centred within 0.3 mm of a neighbour pad's copper -- the
        # bare-copper strip alone missed the case where the own ring bleeds into
        # a cell that holds no neighbour COPPER but does sit in the neighbour's
        # keepout ring (the D10 mw_gate->GND short).
        own_keepout -= multi_keepout
        own_keepout -= all_pad_copper - allowed
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
            r = _route_pair(board, nc, net, a, b, master_blocked, own_keepout, routed,
                           allowed, x0, y0, via2d,
                           pad_vip.get((va.x, va.y)),
                           pad_vip.get((vb.x, vb.y)),
                           bga_bbox=bga_bbox)
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

    # ---- RF via fence (Task 8): GND return vias alongside routed RF traces ----
    # Only nets that actually routed (in `done`) get a fence -- an unrouted RF
    # net has no F.Cu trace to companion. The fence recomputes the pad L-path
    # (b.Tracks() is not iterable post-mutation) and places clearance-checked
    # GND through-vias at +/-0.6 mm offset every ~1 mm, tying the microstrip's
    # ground reference to In1.Cu. Final via pitch/offset + 50-ohm impedance
    # verification are GUI-tunable; this is the headless SI-fence baseline.
    gnd_nc = netcodes.get("GND")
    rf_done = {n: net_pads[n] for n in _RF_NETS
               if n in net_pads and n in done}
    if rf_done:
        nv_fence = _route_rf_fence(board, gnd_nc, rf_done, x0, y0,
                                   master_blocked, routed, via2d)
        nv += nv_fence
        if _DEBUG:
            print(f"  DEBUG rf_fence: {nv_fence} GND return vias")

    # Remove through via-in-pad vias on SIGNAL nets that were attempted but
    # FAILED: a through VIP whose net never escaped dangles on In3/In4/B.Cu
    # (via_dangling). Plane nets (GND/+3V3) are never in fail_set (excluded from
    # net_pads). Power rails (+1V0/+1V8/+5V/+5V_RF/+5V_IN) CAN now be in fail_set
    # (a rail segment may be unreachable), but their BGA through-vias MUST stay --
    # they tie the FPGA power balls to the rail layer regardless of whether the
    # rail track reached the regulator, and removing them would disconnect the
    # balls. So only SIGNAL-net VIPs are removed. Done nets keep theirs.
    # EXCEPTION: the 128 QSSC_Chip dog-bone vias are a complete fanout (pad +
    # 0.15 mm stub + through-via) the GUI finishes on B.Cu, NOT a dangling
    # via-in-pad -- tearing them down would leave 128 dead stubs in mid-air. So
    # they are kept regardless of whether the line router escaped them.
    fail_set = set(fail)
    for net, vs in vip_vias.items():
        if net in fail_set and net not in POWER_NETS:
            for v in vs:
                if (int(v.GetX()), int(v.GetY())) in chip_via_pos:
                    continue
                board.Remove(v)
                bga_vias -= 1
    return {"routed": done, "unrouted": fail, "partial_power": partial,
            "single": single, "tracks": nt, "vias": nv,
            "bga_fanout_vias": bga_vias, "excluded": sorted(EXCLUDE_NETS)}


def main() -> dict:
    board = pcbnew.LoadBoard(str(PCB_PATH))
    stats = build(board)
    board.SetModified()
    pcbnew.SaveBoard(str(PCB_PATH), board)
    if __name__ != "__main__":
        return stats
    n = len(stats['routed']) + len(stats['unrouted']) + len(stats['partial_power'])
    print(f"routed {len(stats['routed'])} / {n} | "
          f"tracks {stats['tracks']} vias {stats['vias']} bga-fanout-vias {stats['bga_fanout_vias']} "
          f"| single-pad nets {len(stats['single'])} | excluded {len(stats['excluded'])}")
    if stats["partial_power"]:
        print(f"PARTIAL POWER ({len(stats['partial_power'])}) [some pads/edges GUI]: "
              f"{', '.join(sorted(stats['partial_power']))}")
    if stats["unrouted"]:
        print(f"UNROUTED ({len(stats['unrouted'])}): {', '.join(sorted(stats['unrouted']))}")
    return stats


if __name__ == "__main__":
    main()