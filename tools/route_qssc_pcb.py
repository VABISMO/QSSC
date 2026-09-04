#!/usr/bin/env python3
"""Headless copper-plane foundation for the QSSC PCB (``hardware/pcb/qssc.kicad_pcb``).

This is NOT an autorouter. A quality DRC-clean autoroute of a 65-net board with
a 237-pad FPGA BGA cannot be produced headlessly: FreeRouting/topola are not
installed, and writing one from scratch in a script would leave DRC violations
(a "routed" board that fails DRC is *worse* than an honestly-unrouted one). The
FPGA's 120 power/ground balls ARE netted (79 GND, 30 +3V3, 3 +1V8, 8 +1V0) and
reach their planes via via-in-pad microvias placed by route_signals._bga_fanout;
the remaining ~116 balls are named signal / unused-IO pads from the XDC.

What this DOES do -- the copper-plane foundation for the 6-layer stackup
(F.Cu sig / In1.Cu GND / In2.Cu +3V3 (PWR) / In3.Cu sig / In4.Cu sig / B.Cu sig),
reproducibly and DRC-clean (copper pours auto-avoid pads of other nets):

  * **In1.Cu : solid GND plane** (full board) -- continuous ground reference.
  * **In2.Cu : solid +3V3 plane** (full board) -- the main digital rail
    (detected by the "PWR" layer label; the chip module also puts it on In2.Cu).
  * **Rail pad vias** -- every surface GND / +3V3 SMD pad (not on the BGA) gets
    an F.Cu tie trace to a through-via (or via-in-pad for the large QFN/LFCSP
    centre exposed-pads), so the pad actually reaches its inner plane. The 6-
    layer stackup has no F.Cu/B.Cu GND/+3V3 flood, so a surface rail pad needs an
    explicit via to reach the plane; the tie trace is what connects it (an
    offset via alone leaves the pad floating -- hidden by unconnected_items:
    ignore). A rectangle-accurate pad model + a 0.16 mm copper gap let the trace
    + via clear a 0.5 mm-pitch neighbour (the half-diagonal circle model that
    preceded it falsely blocked a via that clears the pad's narrow side).
  * **GND stitching vias** on a coarse grid, each pad-clearance-checked, as
    distributed signal-integrity return vias tying the plane to the surfaces.

BGA via-in-pad (GND/+3V3/signal/power) and ALL signal + non-plane power routing
are owned by ``tools/route_signals.py`` (run it FIRST). This script preserves
the small BGA VIPs and all signal copper when (re)building the planes.

Still GUI-pending (honestly): 50-ohm RF microstrip impedance verification with a
solver, USB-C diff-pair length-skew tuning, analog guard-ring final geometry,
and the dense 0.5 mm Samtec connector power-pin fanout (J11.55 +3V3, J11.57 /
J1.57 +5V to the module, TIA_REF) the headless router leaves for the GUI.

Idempotent: it first strips any zones and its own wide GND/+3V3 vias AND tie
traces (kept: the small BGA VIPs and signal copper from route_signals), then
rebuilds, so re-running on an already-processed board is safe (the +3V3 rail
copper is treated as this script's own -- skipped in _signal_obstacles -- so a
prior run's +3V3 tie traces/vias do not ghost-block the next +3V3 pass, which
otherwise oscillates the via count 0/40/0/40 across runs). It expects the board
produced by ``tools/gen_qssc_kicad.py`` (run that first if the board was
hand-edited).

Run:  python3 tools/route_qssc_pcb.py     (needs system python3 with pcbnew)
"""
from __future__ import annotations

import os
from pathlib import Path

import pcbnew

PCB_PATH = Path(
    os.environ.get(
        "QSSC_PCB_PATH",
        Path(__file__).resolve().parent.parent / "hardware" / "pcb" / "qssc.kicad_pcb",
    )
)

# layer ids (KiCad 8 fixed copper indices). The GND and +3V3 *plane* layers are
# detected dynamically from the board's layer labels (In1.Cu is labelled "GND",
# the +3V3 plane is labelled "PWR" -- In2.Cu on BOTH the main board and the chip
# module, post-HDI-pivot), so this script works for either 6-layer stackup.
F_CU = 0
IN1_CU = 1      # GND plane (label "GND")
IN2_CU = 2      # +3V3 plane (label "PWR") on both boards (HDI: adjacent to F.Cu)
IN3_CU = 3      # signal
IN4_CU = 4      # signal
B_CU = 31

GRID_STEP_NM = 10_000_000          # 10 mm stitching grid
VIA_DIAM_NM = 600_000              # 0.6 mm stitching / rail-pad via
VIA_DRILL_NM = 300_000             # 0.3 mm drill
# COPPER_GAP is the minimum copper-to-copper gap this script enforces between
# its own copper (via land / F.Cu tie trace) and any OTHER-net copper. The Power
# netclass DRC rule needs 0.15 mm; 0.16 mm keeps 0.01 mm of margin over it while
# NOT over-blocking dense 0.5 mm-pitch pads (the earlier 0.5 mm "min_clear"
# centre-to-centre check, sized for a 0.3 mm via against a circle of the pad's
# half-diagonal, falsely blocked a via that clears a 0.25 mm pad's narrow side
# by > 0.4 mm). With the rectangle-accurate pad model below, a 0.16 mm gap is the
# real, tight, DRC-safe clearance.
COPPER_GAP_NM = 160_000             # 0.16 mm copper-to-copper (Power rule 0.15)
# Through-via-in-pad VIPs placed by route_signals._bga_fanout are 0.20 mm; this
# script's own vias are 0.6 mm. _clear_routed strips only its own (wide) GND /
# +3V3 vias and preserves the small BGA VIPs (owned by route_signals).
VIP_MAX_NM = 300_000
RAIL_VIA_OFFSET_NM = 600_000       # 0.6 mm offset from a rail pad to its via
BGA_REF = "U1"                     # the FPGA BGA (skip its pads for rail-pad vias)
# A pad with both copper sides >= VIP_MIN_NM gets a via-in-pad at its centre:
# the only way to tie a QFN/LFCSP centre exposed-pad (3.5-5 mm of ground copper
# ringed by a dense pin field an offset via can never clear). Capped at 1.2 mm
# so component terminal pads (decoupling caps ~0.9 mm, where a centred via would
# wick solder) instead take the offset-spiral path.
VIP_MIN_NM = 1_200_000             # 1.2 mm -- via-in-pad only for large pads
# Drill-to-drill spacing: a new via's centre must be at least this far from any
# surviving via's centre so the two drills keep the board's min_hole_to_hole
# (0.25 mm edge-to-edge). New drill is 0.3 mm; worst-case surviving drill is
# 0.3 mm, so 0.25 + 0.3 = 0.55 mm centre would just meet 0.25 edge -- add 0.05
# mm margin for the 0.2495 mm DRC threshold. Also keeps two of THIS script's
# own rail vias (e.g. two pads sharing one centre, or adjacent pads fanned out
# toward each other) from co-locating.
DRILL_MIN_CENTER_NM = 600_000      # 0.6 mm min centre-to-centre between vias
RAIL_TRACK_NM = 400_000            # 0.4 mm F.Cu tie trace (pad-centre -> via)
# Dense SMT connector leads (0.3 mm wide on a 0.5 mm pitch -- Samtec LSHM-130)
# cannot host a 0.4 mm tie trace: the adjacent-row lead 0.5 mm away leaves only
# 0.5 - 0.2 (0.4 mm trace half) - 0.15 (lead half) = 0.15 mm < the 0.16 mm
# COPPER_GAP. A 0.2 mm trace (0.1 mm half) clears by 0.25 mm. The board's global
# min_track_width is 0.15 mm so 0.2 mm passes the width DRC. Idempotent because
# _clear_routed strips EVERY GND/+3V3 PCB_TRACK (route_signals routes none).
RAIL_TRACK_THIN_NM = 200_000       # 0.2 mm tie trace for <=0.3 mm-wide pads
RAIL_TRACK_THIN_MAX = 300_000       # pad narrow side <= this -> use the thin trace


def _find_netcode(board, name):
    for code, net in board.GetNetInfo().NetsByNetcode().items():
        if net.GetNetname() == name:
            return code
    raise KeyError(f"net {name!r} not in board netlist")


def _find_plane_layer(board, label):
    """Return the layer ID of the copper layer whose user label == `label`
    ("GND" or "PWR"), or None. The main board labels In1.Cu="GND" and
    In4.Cu="PWR"; the chip module labels In1.Cu="GND" and In2.Cu="PWR". Avoids
    CuStack() (a bare SwigPyObject in this build after board mutation)."""
    n = board.GetCopperLayerCount()
    cands = list(range(n - 1)) + [pcbnew.B_Cu]
    for lid in cands:
        try:
            if board.GetLayerName(lid) == label:
                return lid
        except Exception:
            pass
    return None


def _clear_routed(board):
    """Idempotent reset of the plane foundation: remove every zone (planes) and
    every WIDE GND/+3V3 track/via this script owns (the stitching mesh + rail
    pad vias), but KEEP small BGA via-in-pad VIPs (<= VIP_MAX_NM, owned by
    route_signals._bga_fanout) and all non-plane signal/power tracks/vias so a
    prior ``route_signals.py`` pass is preserved when the plane foundation is
    (re)built on top of it.

    The swig track proxy is iterable by for-loop but ``list()`` of it can raise
    ``SwigPyObject not iterable`` after the board is mutated, so we collect
    references via comprehensions (which iterate, unlike ``list()``) first.
    """
    zones = [z for z in board.Zones()]
    for z in zones:
        board.Remove(z)
    tracks = [t for t in board.Tracks()]
    for t in tracks:
        nm = t.GetNetname()
        if nm not in ("GND", "+3V3"):
            continue
        if isinstance(t, pcbnew.PCB_VIA):
            # strip only this script's own WIDE vias (0.6 mm stitching / rail-pad);
            # KEEP the <= VIP_MAX_NM via-in-pad VIPs owned by route_signals
            # (GND 0.2 mm microvias, +3V3 0.3 mm THRU vias on the BGA).
            if t.GetWidth() > VIP_MAX_NM:
                board.Remove(t)
        else:
            # PCB_TRACK: every GND/+3V3 tie trace is THIS script's (route_signals
            # routes no GND/+3V3 tracks -- those nets are inner planes + BGA
            # via-in-pad), so strip a tie trace at ANY width -- including the
            # 0.2 mm dense-connector trace, which the > VIP_MAX_NM width test
            # would leave behind as a ghost obstacle on a re-run.
            board.Remove(t)


def _add_gnd_or_pwr_zone(board, layer, netcode, bbox):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNetCode(netcode)
    z.SetAssignedPriority(0)
    # solid (full) pad connection -- these are plane/flood pours, so we want a
    # direct copper connection, not thermal-relief spokes that can starve at
    # edge/adjacent pads and trip the starved_thermal DRC rule.
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    x0, y0 = bbox.GetX(), bbox.GetY()
    x1, y1 = x0 + bbox.GetWidth(), y0 + bbox.GetHeight()
    for cx, cy in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
        # AppendCorner(corner, holeIdx); holeIdx = -1 for the main outline
        z.AppendCorner(pcbnew.VECTOR2I(int(cx), int(cy)), -1)
    board.Add(z)
    return z


def _pad_obstacle(board):
    """Return a list of (cx, cy, hw, hh, netcode) pad RECTANGLES for
    via/trace-clearance checks -- each pad's centre plus the half-extents (hw,
    hh) of its copper (GetSize), used by _rect_dist2 / _rect_seg_dist2.

    The rectangle model is what makes dense 0.5 mm-pitch fanout viable: a
    0.25 x 0.88 mm QFN lead is 0.125 mm off its narrow side but 0.44 mm off its
    long side, and a via 0.58 mm perpendicular to the lead (which clears the
    0.25 mm side by 0.45 mm) was FALSely blocked by the earlier circle of the
    pad's half-diagonal (a 0.46 mm "radius" reaching 0.33 mm past the narrow
    side). The rectangle reaches only 0.125 mm off the narrow side, so that
    same via clears it by 0.45 mm and places. (Accurate for 0/90-degree pads,
    the common case; the bbox is a slight over-estimate for a round/oval pad,
    which is safe.)

    Only pads that actually carry COPPER on one of the board's copper layers
    are included: some footprints carry non-copper "pads" (courtyard /
    documentation / paste-only geometry with an empty copper layer-set, e.g. the
    16 unnetted interior cells inside the chip-module MUX's exposed pad) which
    do not short to anything and must NOT block a via -- counting them would
    falsely forbid a via-in-pad at a real copper pad they merely overlap."""
    cu_ids = set(range(board.GetCopperLayerCount() - 1)) | {pcbnew.B_Cu}
    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            ls = pad.GetLayerSet()
            if not any(ls.Contains(l) for l in cu_ids):
                continue  # non-copper pad -- not a real obstacle
            pos = pad.GetCenter()
            sz = pad.GetSize()
            is_pth = pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
            pads.append((pos.x, pos.y, int(sz.x) / 2, int(sz.y) / 2,
                         pad.GetNetCode(), is_pth))
    return pads


def _signal_obstacles(board):
    """Return (circles, segs, segs_fcu) for SIGNAL copper already on the board --
    vias as (cx, cy, r) circles and tracks as (x1, y1, x2, y2, hw) segments. GND
    and unnetted copper are skipped: a GND stitching via on a GND track is fine,
    and unnetted copper has no net to short. Used so the stitching-via grid never
    pierces a signal track/via (the original pad-only check shorted ~20 nets).

    ``segs`` is every signal track on every layer (a through via pierces all
    layers, so the via check must see inner-layer tracks too). ``segs_fcu`` is
    the F.Cu-only subset: the F.Cu tie trace only touches F.Cu, so checking it
    against an In3.Cu track it physically cannot reach would falsely block it --
    the trace check uses segs_fcu, the via check uses segs."""
    circles = []
    segs = []
    segs_fcu = []
    for t in board.Tracks():
        nm = t.GetNetname()
        # Skip GND AND +3V3: both are this router's domain (the two planes it
        # rebuilds + the tie traces it owns). route_signals never routes them
        # (they are plane nets), so every GND/+3V3 track or via on the board is
        # a leftover from a PREVIOUS run of this script. They are captured HERE,
        # before _clear_routed strips them, so keeping them as "signal" obstacles
        # would make the next pass block its own replacement copper -- a ghost
        # obstacle that oscillates the +3V3 via count 0/40/0/40 across runs.
        if not nm or nm in ("GND", "+3V3"):
            continue
        if isinstance(t, pcbnew.PCB_VIA):
            c = t.GetPosition()
            circles.append((c.x, c.y, t.GetWidth() / 2))
        else:
            s = t.GetStart()
            e = t.GetEnd()
            hw = t.GetWidth() / 2
            seg = (s.x, s.y, e.x, e.y, hw)
            segs.append(seg)
            if t.GetLayer() == pcbnew.F_Cu:
                segs_fcu.append(seg)
    return circles, segs, segs_fcu


def _surviving_via_centers(board):
    """(x, y, netcode) of every via that ``_clear_routed`` will KEEP -- i.e. all
    non-GND/+3V3 routing vias plus the small GND/+3V3 BGA via-in-pad VIPs (width
    <= VIP_MAX_NM). The wide GND/+3V3 stitching/rail vias this script owns are
    removed by ``_clear_routed`` and so are excluded. Must be snapshotted BEFORE
    ``_clear_routed`` mutates the board (board.Tracks() returns a broken swig
    proxy afterwards). Used for via-to-via spacing: SAME-net vias need only
    drill spacing (0.6 mm centre); DIFFERENT-net vias need copper clearance
    (0.8 mm centre for the 0.15 mm Power netclass), so the netcode is carried."""
    centres = []
    for t in board.Tracks():
        if not isinstance(t, pcbnew.PCB_VIA):
            continue
        nm = t.GetNetname()
        if nm in ("GND", "+3V3") and t.GetWidth() > VIP_MAX_NM:
            continue  # this script's own wide via -- removed by _clear_routed
        c = t.GetPosition()
        centres.append((c.x, c.y, t.GetNetCode()))
    return centres


def _seg_dist2(px, py, x1, y1, x2, y2):
    """Squared distance from point (px,py) to segment (x1,y1)-(x2,y2), in nm^2."""
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return (px - x1) ** 2 + (py - y1) ** 2
    tt = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    if tt < 0:
        tt = 0
    elif tt > 1:
        tt = 1
    cx = x1 + tt * dx
    cy = y1 + tt * dy
    return (px - cx) ** 2 + (py - cy) ** 2


def _rect_dist2(px, py, cx, cy, hw, hh):
    """Squared distance from point (px,py) to an axis-aligned rectangle centred
    at (cx,cy) with half-extents (hw,hh). 0 if the point is inside. Accurate for
    0/90-degree pads (the common case) -- unlike a circle of the half-diagonal,
    which grossly over-reaches a long thin pad's narrow side and falsely blocks a
    via that clears it by plenty."""
    dx = abs(px - cx) - hw
    if dx < 0:
        dx = 0
    dy = abs(py - cy) - hh
    if dy < 0:
        dy = 0
    return dx * dx + dy * dy


def _rect_seg_dist2(x1, y1, x2, y2, cx, cy, hw, hh):
    """Squared distance from a segment to an axis-aligned rectangle, by sampling
    the segment at ~0.1 mm steps (a tie trace is <= 3 mm, so <= 30 samples). The
    via endpoint already cleared the pad; this guards the trace body against
    skirting a different-net pad's long side."""
    length = int(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
    n = max(2, length // 100_000 + 1)
    best = 9_000_000_000_000_000
    for i in range(n + 1):
        t = i / n
        px = x1 + t * (x2 - x1)
        py = y1 + t * (y2 - y1)
        d = _rect_dist2(px, py, cx, cy, hw, hh)
        if d < best:
            best = d
    return best


def _seg_seg_dist2(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    """Squared distance between two segments A and B. 0 if they cross -- so a
    tie trace that crosses a signal track is flagged as blocked. Otherwise the
    min of each endpoint to the other segment (the true seg-seg distance)."""
    # crossing test via orientation
    def cross(ox, oy, px, py, qx, qy):
        return (px - ox) * (qy - oy) - (py - oy) * (qx - ox)
    d1 = cross(bx1, by1, bx2, by2, ax1, ay1)
    d2 = cross(bx1, by1, bx2, by2, ax2, ay2)
    d3 = cross(ax1, ay1, ax2, ay2, bx1, by1)
    d4 = cross(ax1, ay1, ax2, ay2, bx2, by2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0
    return min(
        _seg_dist2(ax1, ay1, bx1, by1, bx2, by2),
        _seg_dist2(ax2, ay2, bx1, by1, bx2, by2),
        _seg_dist2(bx1, by1, ax1, ay1, ax2, ay2),
        _seg_dist2(bx2, by2, ax1, ay1, ax2, ay2),
    )


def _is_bga(fp):
    """True if the footprint is a ball-grid array -- i.e. it has many INTERIOR
    pads (pads whose centres sit well off the footprint perimeter). This is what
    distinguishes the FPGA BGA on the main board (236 balls, ~150 interior) from
    the QFN MUXes on the chip module (perimeter pads + at most one centre thermal
    pad), which ``BGA_REF="U1"`` alone cannot -- on the chip module U1 is a QFN
    MUX, not the FPGA, so a reference-designator match would wrongly skip its
    power/ground pads and leave the MUXes dead. Uses a 30% edge margin (a BGA's
    central balls sit >30% in from every edge; a QFN/LFCSP's perimeter leads and
    single centre thermal pad do not) and requires >= 10 such deep-interior pads,
    so a 42-pad LFCSP (2 interior) and a 65-pad QFN (5 interior) are never mistaken
    for the 236-ball FPGA BGA (46 interior)."""
    pads = list(fp.Pads())
    if len(pads) < 20:
        return False
    xs = [p.GetCenter().x for p in pads]
    ys = [p.GetCenter().y for p in pads]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    mx = (xmax - xmin) * 0.30
    my = (ymax - ymin) * 0.30
    n_interior = 0
    for p in pads:
        c = p.GetCenter()
        if (xmin + mx < c.x < xmax - mx) and (ymin + my < c.y < ymax - my):
            n_interior += 1
            if n_interior >= 10:
                return True
    return False


def _capture_rail_pads(board):
    """Snapshot every pad's (centre, netcode, attribute) and the BGA pad centres
    BEFORE any board mutation. KiCad's swig binding returns a bare non-iterable
    SwigPyObject for ``board.GetFootprints()``/``Footprints()`` once
    ``board.Remove()`` runs in ``_clear_routed``, so ``_add_rail_pad_vias``
    cannot iterate footprints after the reset. Returns
    (bga_xy_set, {netcode: [(x, y, attribute), ...]}) for non-BGA pads only.

    A pad is skipped only on a real BGA (``_is_bga``), whose GND/+3V3 balls
    already carry via-in-pad from ``route_signals._bga_fanout`` -- not by
    reference designator, which collides between the boards (U1 = FPGA BGA on
    the main board but a QFN MUX on the chip module)."""
    bga_xy = set()
    by_net = {}
    for fp in board.GetFootprints():
        is_bga = _is_bga(fp)
        for pad in fp.Pads():
            c = pad.GetCenter()
            x, y = int(c.x), int(c.y)
            if is_bga:
                bga_xy.add((x, y))
                continue
            sz = pad.GetSize()
            by_net.setdefault(pad.GetNetCode(), []).append(
                (x, y, pad.GetAttribute(), int(sz.x), int(sz.y)))
    return bga_xy, by_net


def _add_rail_pad_vias(board, netcode, bga_xy, rail_pads, pads, sig_circles,
                      sig_segs, sig_segs_fcu, via_centers, tie_segs):
    """For every SMD pad on this rail net (GND or +3V3) that is NOT on the BGA
    (the FPGA -- its plane balls get via-in-pad from route_signals._bga_fanout)
    and not a PTH pin, place one through-via so the surface pad reaches the
    inner plane (In1.Cu GND or the PWR +3V3 plane). With the 6-layer stackup
    F.Cu/B.Cu carry no GND/+3V3 flood, so a surface rail pad needs an explicit
    via to its plane. Returns the vias.

    Two placement strategies, by pad size:

    * **Via-in-pad** for a large pad (min copper side >= VIP_MIN_NM): the via
      sits AT the pad centre. This is the only way to tie a QFN/LFCSP centre
      exposed-pad (the chips' main ground return, 3.5-5 mm of copper) -- an
      offset via can never clear the dense perimeter pin field around it, and
      a centre via in a big same-net pad needs no clearance to that pad.
    * **Offset spiral** for a small pad (a 0.25 mm QFN lead): try 0.6-3.0 mm
      offsets in 8 directions. The pad being tied is EXCLUDED from the obstacle
      check -- the via IS the pad's connection to the plane, so it may sit as
      close to its own pad as needed; only OTHER nets' copper must clear.

    Every candidate is also checked for drill-to-drill spacing against
    ``via_centers`` (the surviving vias, snapshotted before _clear_routed) AND
    this call's already-placed vias, so two rail vias never co-locate (two pads
    sharing a centre, e.g. a connector's top/bottom GND lugs) or sit too close to
    a BGA VIP's drill -- the hole_near_hole / holes_co_located DRC rules.

    ``bga_xy``/``rail_pads`` are a pre-mutation snapshot from
    ``_capture_rail_pads`` (board.GetFootprints() is unreliable after
    ``_clear_routed`` mutates the board). ``rail_pads`` carries each pad's
    copper size (sx, sy) for the via-in-pad decision."""
    via_r = VIA_DIAM_NM / 2
    gap = COPPER_GAP_NM
    self_eps = 50_000  # 0.05 mm -- a pad is "self" if within this of its centre
    drill_min2 = DRILL_MIN_CENTER_NM ** 2
    # Rectangle pad model: a candidate clears a pad when the (squared) distance
    # from the via CENTRE to the pad's copper RECTANGLE >= (via_r + gap)^2, i.e.
    # the via land keeps >= gap (0.16 mm) of copper gap to the pad. Each check
    # below adds the OTHER object's half-width so the gap is copper-to-copper:
    #   via vs pad rect  : via_r + gap            (rect already is the copper edge)
    #   via vs signal via: via_r + cr + gap       (both lands)
    #   via vs signal trk: via_r + hw + gap
    #   via vs diff via  : 2*via_r + gap          (centre-to-centre)
    via_pad2 = (via_r + gap) ** 2
    via_via2 = (2 * via_r + gap) ** 2
    track_hw = RAIL_TRACK_NM / 2
    # via_centers is the shared, mutable via-spacing list threaded through
    # build(): it starts with the surviving (pre-_clear_routed) via centres and
    # every via this call places is appended to it, so the +3V3 pass sees the
    # GND vias just placed and the stitching pass sees both.

    def _clears(vx, vy, sx, sy):
        for ox, oy, hw, hh, onc, opth in pads:
            if onc == netcode and not opth:
                continue  # same-net SMD pad: the via may overlap it (no DRC issue)
            if onc == netcode and opth:
                continue  # same-net PTH pad: own via-in-pad handles plane tie
            if abs(ox - sx) < self_eps and abs(oy - sy) < self_eps:
                continue  # the pad being tied (defensive: also same net)
            if _rect_dist2(vx, vy, ox, oy, hw, hh) < via_pad2:
                return False
        for cx, cy, cr in sig_circles:
            req = via_r + cr + gap
            if (vx - cx) ** 2 + (vy - cy) ** 2 < req * req:
                return False
        # the via is THROUGH -- it pierces every layer, so check signal tracks on
        # ALL layers (sig_segs), not just F.Cu.
        for x1s, y1s, x2s, y2s, hw in sig_segs:
            req = via_r + hw + gap
            if _seg_dist2(vx, vy, x1s, y1s, x2s, y2s) < req * req:
                return False
        # the via sits on F.Cu too, so it must clear a DIFFERENT-net tie trace
        # placed by the other rail pass (a +3V3 via over a GND trace would short).
        # Same-net tie traces (this pass's own, appended below) are skipped.
        for tx1, ty1, tx2, ty2, tnc, thw in tie_segs:
            if tnc == netcode:
                continue
            req = via_r + thw + gap
            if _seg_dist2(vx, vy, tx1, ty1, tx2, ty2) < req * req:
                return False
        # via-to-via spacing vs surviving vias + this call's placed vias:
        # same net -> drill spacing only (copper may overlap); different net ->
        # copper clearance (0.76 mm centre keeps the 0.16 mm gap to a 0.6 mm via).
        for cx, cy, cnc in via_centers:
            d2 = (vx - cx) ** 2 + (vy - cy) ** 2
            if cnc == netcode:
                if d2 < drill_min2:
                    return False
            elif d2 < via_via2:
                return False
        return True

    def _trace_clears(x1, y1, x2, y2, sx, sy):
        """The F.Cu tie trace pad-centre -> via-centre must keep >= gap of copper
        gap to every OTHER-net pad (rect-to-segment), signal via, signal track,
        and different-net via. Same-net copper may be crossed. The via endpoint
        already cleared via _clears; this guards the trace body.

        The trace is on F.Cu ONLY, so it is checked against sig_segs_fcu (F.Cu
        tracks) -- never against In3/In4/B.Cu tracks it physically cannot reach
        (the earlier all-layer check falsely blocked half the traces). It is
        still checked against ALL signal vias, because a through via's land sits
        on F.Cu too."""
        trk_pad2 = (track_hw + gap) ** 2
        for ox, oy, hw, hh, onc, opth in pads:
            if onc == netcode:
                continue
            if abs(ox - sx) < self_eps and abs(oy - sy) < self_eps:
                continue
            if _rect_seg_dist2(x1, y1, x2, y2, ox, oy, hw, hh) < trk_pad2:
                return False
        for cx, cy, cr in sig_circles:
            req = track_hw + cr + gap
            if _seg_dist2(cx, cy, x1, y1, x2, y2) < req * req:
                return False
        for x1s, y1s, x2s, y2s, hw in sig_segs_fcu:
            req = track_hw + hw + gap
            if _seg_seg_dist2(x1, y1, x2, y2, x1s, y1s, x2s, y2s) < req * req:
                return False
        # a tie trace must keep copper gap to a DIFFERENT-net tie trace placed by
        # the other rail pass (parallel GND/+3V3 fanouts 0.5 mm apart would
        # otherwise leave a 0.1 mm gap < the 0.15 mm Power rule). Same-net traces
        # (this pass's own, appended below) may run parallel -- no DRC issue.
        for tx1, ty1, tx2, ty2, tnc, thw in tie_segs:
            if tnc == netcode:
                continue
            req = track_hw + thw + gap
            if _seg_seg_dist2(x1, y1, x2, y2, tx1, ty1, tx2, ty2) < req * req:
                return False
        for cx, cy, cnc in via_centers:
            if cnc == netcode:
                continue
            req = track_hw + via_r + gap
            if _seg_dist2(cx, cy, x1, y1, x2, y2) < req * req:
                return False
        return True

    dirs = ((1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (1, -1), (-1, 1), (-1, -1))
    radii = (RAIL_VIA_OFFSET_NM, 800_000, 1_000_000, 1_200_000,
             1_600_000, 2_000_000, 2_500_000, 3_000_000)
    vias = []
    skipped = 0
    vip = 0
    for (px, py, attrib, psz_x, psz_y) in rail_pads.get(netcode, []):
        if attrib == pcbnew.PAD_ATTRIB_PTH:
            continue
        if (px, py) in bga_xy:
            continue
        # Per-pad tie-trace width: a dense SMT connector lead (<= 0.3 mm narrow
        # side on a 0.5 mm pitch) cannot host a 0.4 mm trace alongside the
        # adjacent-row lead (0.15 mm gap < COPPER_GAP), so use the 0.2 mm thin
        # trace there. The closures below read `track_hw` late (cell binding), so
        # reassigning it here makes _clears / _trace_clears use this pad's width.
        if min(psz_x, psz_y) <= RAIL_TRACK_THIN_MAX:
            track_hw = RAIL_TRACK_THIN_NM / 2
            this_trk_w = RAIL_TRACK_THIN_NM
        else:
            track_hw = RAIL_TRACK_NM / 2
            this_trk_w = RAIL_TRACK_NM
        placed = False
        # Via-in-pad for a large pad (centre exposed-pad / big connector lug):
        # a 0.6 mm via centred in a >= VIP_MIN_NM pad clears its own pad's edges
        # and -- being the pad's own net -- needs no clearance to it.
        if min(psz_x, psz_y) >= VIP_MIN_NM:
            if _clears(px, py, px, py):
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pcbnew.VECTOR2I(int(px), int(py)))
                v.SetNetCode(netcode)
                v.SetViaType(pcbnew.VIATYPE_THROUGH)
                v.SetDrill(VIA_DRILL_NM)
                v.SetWidth(VIA_DIAM_NM)
                board.Add(v)
                vias.append(v)
                via_centers.append((px, py, netcode))
                vip += 1
                placed = True
        if not placed:
            for r in radii:
                for dxn, dyn in dirs:
                    vx, vy = px + int(dxn * r), py + int(dyn * r)
                    if not _clears(vx, vy, px, py):
                        continue
                    # an offset via only ties its pad if an F.Cu trace connects
                    # them -- without it the pad floats off the plane (hidden by
                    # the board's unconnected_items:ignore). Require the trace
                    # body to clear other nets before committing the via+trace.
                    if not _trace_clears(px, py, vx, vy, px, py):
                        continue
                    v = pcbnew.PCB_VIA(board)
                    v.SetPosition(pcbnew.VECTOR2I(int(vx), int(vy)))
                    v.SetNetCode(netcode)
                    v.SetViaType(pcbnew.VIATYPE_THROUGH)
                    v.SetDrill(VIA_DRILL_NM)
                    v.SetWidth(VIA_DIAM_NM)
                    board.Add(v)
                    vias.append(v)
                    via_centers.append((vx, vy, netcode))
                    # F.Cu tie trace: pad-centre -> via-centre. This is what
                    # actually connects the surface rail pad to its plane via.
                    t = pcbnew.PCB_TRACK(board)
                    t.SetStart(pcbnew.VECTOR2I(int(px), int(py)))
                    t.SetEnd(pcbnew.VECTOR2I(int(vx), int(vy)))
                    t.SetLayer(pcbnew.F_Cu)
                    t.SetWidth(this_trk_w)
                    t.SetNetCode(netcode)
                    board.Add(t)
                    # record the trace (+ its half-width) so the other rail pass
                    # keeps copper gap to it (see _clears / _trace_clears tie_segs
                    # checks, which use this trace's real half-width).
                    tie_segs.append((px, py, vx, vy, netcode, track_hw))
                    placed = True
                    break
                if placed:
                    break
        if not placed:
            skipped += 1
    if skipped:
        print(f"  rail-pad via: {len(vias)} placed ({vip} via-in-pad), {skipped} "
              f"SMD pads could not be tied (no clear via site within 3 mm)")
    return vias


def _add_stitching_vias(board, netcode, bbox, pads, sig_circles, sig_segs,
                       via_centers, tie_segs):
    """Place GND through-vias on a coarse grid, skipping any point too close to
    a pad OR an existing signal track/via. Ties the F.Cu/In1.Cu/B.Cu GND copper
    into one mesh. Returns the list of created via objects (kept so callers can
    verify without re-loading the board -- KiCad's python binding can return
    bare SwigPyObjects for a board re-loaded after mutation).

    Each grid point is also checked for drill-to-drill spacing against
    ``via_centers`` (surviving vias + the rail-pad vias just placed, threaded in
    from build()) and the already-placed stitching vias, so a stitching via
    never co-locates with a BGA GND VIP or a rail-pad via (hole_near_hole /
    holes_co_located).

    ``sig_circles``/``sig_segs`` are the signal via-circles / track-segments
    snapshoted BEFORE _clear_routed (the swig proxy layer breaks board.Tracks()
    after board.Remove(), so callers capture them up front)."""
    via_r = VIA_DIAM_NM / 2
    gap = COPPER_GAP_NM
    via_pad2 = (via_r + gap) ** 2
    via_via2 = (2 * via_r + gap) ** 2  # 0.76 mm: diff-net copper clearance
    drill_min2 = DRILL_MIN_CENTER_NM ** 2
    margin = 2_000_000  # 2 mm in from the board edge
    x0 = bbox.GetX() + margin
    y0 = bbox.GetY() + margin
    x1 = bbox.GetX() + bbox.GetWidth() - margin
    y1 = bbox.GetY() + bbox.GetHeight() - margin
    # merge surviving vias + rail-pad vias into one spacing list, then append
    # each stitching via as it is placed.
    all_via_centers = list(via_centers)
    vias = []
    x = x0
    while x <= x1:
        y = y0
        while y <= y1:
            clear = True
            for px, py, hw, hh, onc, opth in pads:
                if onc == netcode and not opth:
                    continue  # same-net SMD pad: a GND stitching via may overlap
                if onc == netcode and opth:
                    # same-net PTH pad (e.g. UE1 HTSSOP thermal-pad vias): the pad
                    # already carries its own drill + plane connection, so a
                    # stitching via landing on it co-locates drills (hole_near_hole,
                    # actual 0.0 mm). Require copper clearance so the via sits OFF
                    # the pad instead of on its hole.
                    if _rect_dist2(x, y, px, py, hw, hh) < via_pad2:
                        clear = False
                        break
                    continue
                if _rect_dist2(x, y, px, py, hw, hh) < via_pad2:
                    clear = False
                    break
            if clear:
                for cx, cy, cr in sig_circles:           # signal vias
                    req = via_r + cr + gap
                    if (x - cx) ** 2 + (y - cy) ** 2 < req * req:
                        clear = False
                        break
            if clear:
                for x1s, y1s, x2s, y2s, hw in sig_segs:   # signal tracks (all layers)
                    req = via_r + hw + gap
                    if _seg_dist2(x, y, x1s, y1s, x2s, y2s) < req * req:
                        clear = False
                        break
            if clear:
                # a GND stitching via must clear a DIFFERENT-net (e.g. +3V3) tie
                # trace (it sits on F.Cu too); same-net (GND) traces are fine.
                for tx1, ty1, tx2, ty2, tnc, thw in tie_segs:
                    if tnc == netcode:
                        continue
                    req = via_r + thw + gap
                    if _seg_dist2(x, y, tx1, ty1, tx2, ty2) < req * req:
                        clear = False
                        break
            if clear:
                for cx, cy, cnc in all_via_centers:        # via-to-via spacing
                    d2 = (x - cx) ** 2 + (y - cy) ** 2
                    if cnc == netcode:
                        if d2 < drill_min2:                # same net: drill only
                            clear = False
                            break
                    elif d2 < via_via2:                   # diff net: copper
                        clear = False
                        break
            if clear:
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
                v.SetNetCode(netcode)
                v.SetViaType(pcbnew.VIATYPE_THROUGH)
                v.SetDrill(VIA_DRILL_NM)
                v.SetWidth(VIA_DIAM_NM)
                board.Add(v)
                vias.append(v)
                all_via_centers.append((x, y, netcode))
            y += GRID_STEP_NM
        x += GRID_STEP_NM
    return vias


# Non-plane power nets whose dense SMT connector pins the line router
# (route_signals) often cannot tie -- a 0.5 mm pitch field defeats its 0.3 mm
# keepout ring, so e.g. J11.57 / J1.57 (+5V main->module power) end up ISOLATED
# from the +5V rail and the module gets no power. This router's 0.16 mm
# copper-gap rectangle model CAN fit a 0.2 mm fanout there, so it ties the
# isolated stragglers here: thin F.Cu stub pad->via (reuses _add_rail_pad_vias)
# then a thin B.Cu L-trace via->nearest same-net rail copper. Idempotent: the
# pad is detected ISOLATED via connectivity, so a re-run (which does NOT strip
# +5V) skips an already-tied pad; a full pipeline re-run has route_signals
# strip+rebuild the +5V rail first, so this re-ties.
STRAGGLER_POWER_NETS = ("+5V", "+5V_RF", "+5V_IN", "+12V_EMIT", "+12V_EMIT_IN")


def _seg_nearest(px, py, x1, y1, x2, y2):
    """Squared distance from point (px,py) to segment (x1,y1)-(x2,y2) and the
    nearest point on that segment, in nm."""
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return (px - x1) ** 2 + (py - y1) ** 2, (x1, y1)
    tt = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    if tt < 0:
        tt = 0
    elif tt > 1:
        tt = 1
    cx = x1 + tt * dx
    cy = y1 + tt * dy
    return (px - cx) ** 2 + (py - cy) ** 2, (cx, cy)


def _straggler_obstacles(board):
    """Snapshot (segs, circles) carrying netcode (+ layer for tracks) for EVERY
    track/via on the board, taken BEFORE _clear_routed (board.Tracks() is only
    iterable then). The B.Cu straggler tie trace must reach the existing same-net
    rail, so the netcode lets the clearance check SKIP same-net copper -- the
    GND/+3V3 rail-pass obstacles (sig_segs/sig_circles, no netcode) cannot, so
    this richer snapshot is kept separate and only used by the straggler pass."""
    segs = []     # (x1, y1, x2, y2, hw, netcode, layer)
    circles = []  # (cx, cy, r, netcode)
    for t in board.Tracks():
        nc = t.GetNetCode()
        if isinstance(t, pcbnew.PCB_VIA):
            c = t.GetPosition()
            circles.append((c.x, c.y, t.GetWidth() / 2, nc))
        else:
            s = t.GetStart()
            e = t.GetEnd()
            segs.append((s.x, s.y, e.x, e.y, t.GetWidth() / 2, nc, t.GetLayer()))
    return segs, circles


def _detect_straggler_pads(board, netcodes_set, bga_xy):
    """{netcode: [(x,y,attr,sx,sy),...]} of SMD, non-BGA pads on each straggler
    power net that are ISOLATED -- no same-net track/via touches them (the line
    router could not tie them). Uses BuildConnectivity on the intact board
    (before _clear_routed). Pads already tied by a prior run of this pass are
    NOT isolated, so they are skipped -- the idempotency for a route_qssc_pcb
    -only re-run."""
    board.BuildConnectivity()
    conn = board.GetConnectivity()
    out = {}
    for fp in board.GetFootprints():
        is_bga = _is_bga(fp)
        for pad in fp.Pads():
            nc = pad.GetNetCode()
            if nc not in netcodes_set:
                continue
            if is_bga:
                continue
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH:
                continue
            if len(list(conn.GetConnectedTracks(pad))) == 0:
                c = pad.GetCenter()
                sz = pad.GetSize()
                out.setdefault(nc, []).append(
                    (c.x, c.y, pad.GetAttribute(), int(sz.x), int(sz.y)))
    return out


def _nearest_same_net_point(vx, vy, netcode, obs_segs, obs_circles):
    """Nearest (x, y) point of existing same-net copper (a via centre or the
    nearest point on a same-net track) to (vx, vy), or None."""
    best_d2 = float("inf")
    best_pt = None
    for sx1, sy1, sx2, sy2, shw, snc, slay in obs_segs:
        if snc != netcode:
            continue
        d2, pt = _seg_nearest(vx, vy, sx1, sy1, sx2, sy2)
        if d2 < best_d2:
            best_d2, best_pt = d2, pt
    for cx, cy, cr, cnc in obs_circles:
        if cnc != netcode:
            continue
        d2 = (vx - cx) ** 2 + (vy - cy) ** 2
        if d2 < best_d2:
            best_d2, best_pt = d2, (cx, cy)
    return best_pt


def _bcu_seg_clear(x1, y1, x2, y2, skip_nc, obs_segs, obs_circles, hw, gap):
    """True if a B.Cu trace segment clears every OTHER-net B.Cu track and
    through-via barrel by >= gap. Same-net (skip_nc) copper may be touched (the
    tie trace ends ON the rail). Through-via barrels sit on B.Cu regardless of
    their own layer span, so all non-same-net vias are obstacles (conservative
    for a microvia that does not reach B.Cu -- only over-blocks, never shorts)."""
    for sx1, sy1, sx2, sy2, shw, snc, slay in obs_segs:
        if snc == skip_nc or slay != pcbnew.B_Cu:
            continue
        if _seg_seg_dist2(x1, y1, x2, y2, sx1, sy1, sx2, sy2) < (hw + shw + gap) ** 2:
            return False
    for cx, cy, cr, cnc in obs_circles:
        if cnc == skip_nc:
            continue
        if _seg_dist2(cx, cy, x1, y1, x2, y2) < (hw + cr + gap) ** 2:
            return False
    return True


def _tie_stragglers_to_rail(board, netcode, via_positions, obs_segs, obs_circles):
    """For each straggler via just placed (a THROUGH via tying an isolated power
    pad via its F.Cu stub), route a thin 0.2 mm B.Cu L-trace from the via to the
    nearest existing same-net rail copper so the pad joins the rail. B.Cu carries
    no SMD pads, so an L-path (two orthogonal segments) is usually clear; if both
    L-orders are blocked the pad is left honestly to GUI. ``via_positions`` is
    the list of (vx, vy, nc) the rail-pad spiral appended to via_centers (the via
    OBJECTS' GetPosition() returns a bare SwigPyObject after board mutation).
    Returns n_tracks."""
    hw = RAIL_TRACK_THIN_NM / 2
    gap = COPPER_GAP_NM
    nt = 0
    for vx, vy, vc in via_positions:
        if vc != netcode:
            continue
        tgt = _nearest_same_net_point(vx, vy, netcode, obs_segs, obs_circles)
        if tgt is None:
            continue
        tx, ty = tgt
        if (tx - vx) ** 2 + (ty - vy) ** 2 <= (hw * 4) ** 2:
            continue  # via already sits on / within touching distance of rail
        for mx, my in ((tx, vy), (vx, ty)):
            segs = ((vx, vy, mx, my), (mx, my, tx, ty))
            if all(_bcu_seg_clear(a, b, c, d, netcode, obs_segs, obs_circles, hw, gap)
                   for a, b, c, d in segs):
                for x1, y1, x2, y2 in segs:
                    t = pcbnew.PCB_TRACK(board)
                    t.SetStart(pcbnew.VECTOR2I(int(x1), int(y1)))
                    t.SetEnd(pcbnew.VECTOR2I(int(x2), int(y2)))
                    t.SetLayer(pcbnew.B_Cu)
                    t.SetWidth(RAIL_TRACK_THIN_NM)
                    t.SetNetCode(netcode)
                    board.Add(t)
                    nt += 1
                break
    return nt


def build(board, gnd, p33, bbox, pads) -> dict:
    """Add the copper-plane foundation to an ALREADY-LOADED board and return a
    stats dict. Does NOT load or save -- callers (main / tests) own the board
    lifetime. Split out so tests can run it repeatedly on one board without
    re-loading (KiCad's python binding can hand back bare SwigPyObjects for a
    board re-loaded after a SaveBoard/mutation, which breaks repeated runs)."""
    # Capture signal tracks/vias AND footprint pads BEFORE _clear_routed mutates
    # the board: the swig proxy layer breaks (board.Tracks()/GetFootprints()
    # return bare SwigPyObjects) once board.Remove() runs in _clear_routed, so
    # the via grids + rail-pad vias must be built from obstacles snapshotted up
    # front. _clear_routed keeps signal copper and the small BGA VIPs (only
    # removes zones + its own wide GND/+3V3 vias).
    sig_circles, sig_segs, sig_segs_fcu = _signal_obstacles(board)
    bga_xy, rail_pads = _capture_rail_pads(board)
    # Surviving via centres (BGA VIPs + signal routing vias) snapshotted BEFORE
    # _clear_routed -- used for drill-to-drill spacing. Mutable list threaded
    # through every via-placement pass: each appends its placed vias so later
    # passes (+3V3 rail, then stitching) keep spacing from earlier ones.
    via_centers = _surviving_via_centers(board)
    # tie_segs is the shared, mutable list of every F.Cu tie trace this script
    # places (px, py, vx, vy, netcode), threaded through the rail passes so a
    # +3V3 trace keeps copper gap to a GND trace the first pass placed (and the
    # via it lands clears it too). Without it, parallel 0.5 mm-pitch GND/+3V3
    # fanouts leave a 0.1 mm gap < the 0.15 mm Power rule.
    tie_segs = []
    # Straggler power pads (e.g. +5V connector pins the line router could not
    # tie -> module gets no power): snapshot obstacles + detect the ISOLATED
    # pads BEFORE _clear_routed (board.Tracks() / BuildConnectivity need the
    # intact board; a re-run skips a pad a prior run already tied -> idempotent).
    straggler_nc = {}
    for nm in STRAGGLER_POWER_NETS:
        try:
            straggler_nc[_find_netcode(board, nm)] = nm
        except KeyError:
            pass
    straggler_obs = _straggler_obstacles(board) if straggler_nc else ([], [])
    straggler_pads = (_detect_straggler_pads(board, set(straggler_nc), bga_xy)
                      if straggler_nc else {})
    _clear_routed(board)

    # Detect the plane layers from the board's layer labels (works for both the
    # main board In4.Cu="PWR" and the chip module In2.Cu="PWR").
    gnd_layer = _find_plane_layer(board, "GND")
    pwr_layer = _find_plane_layer(board, "PWR")
    if gnd_layer is None:
        gnd_layer = IN1_CU   # fallback

    zones = [_add_gnd_or_pwr_zone(board, gnd_layer, gnd, bbox)]          # GND plane
    if p33 is not None and pwr_layer is not None:
        zones.append(_add_gnd_or_pwr_zone(board, pwr_layer, p33, bbox))  # +3V3 plane

    # Rail pad vias: tie every surface GND / +3V3 SMD pad (not on the BGA) to
    # its inner plane. The 6-layer stackup has no F.Cu/B.Cu GND flood, so
    # surface rail pads need an explicit via. Added BEFORE filling so the +3V3
    # plane carves anti-pad clearances around each GND via barrel. via_centers
    # and tie_segs are mutated: the GND pass's placed vias/traces are seen by the
    # +3V3 pass.
    vias = list(_add_rail_pad_vias(board, gnd, bga_xy, rail_pads, pads,
                                  sig_circles, sig_segs, sig_segs_fcu,
                                  via_centers, tie_segs))
    if p33 is not None and pwr_layer is not None:
        vias += list(_add_rail_pad_vias(board, p33, bga_xy, rail_pads, pads,
                                       sig_circles, sig_segs, sig_segs_fcu,
                                       via_centers, tie_segs))

    # GND stitching vias across the ground mesh (coarse grid of return vias
    # tying the plane to the surface layers for signal-integrity returns).
    vias += _add_stitching_vias(board, gnd, bbox, pads, sig_circles, sig_segs,
                                via_centers, tie_segs)

    # Tie isolated straggler power pads (e.g. +5V J11.57 / J1.57) to their rail:
    # a thin 0.2 mm F.Cu stub -> 0.6 mm via (the rail-pad spiral, which fits the
    # 0.5 mm connector pitch the line router's 0.3 mm keepout ring cannot), then
    # a 0.2 mm B.Cu L-trace from that via to the nearest existing same-net rail
    # copper. Run before zone fill so the GND/+3V3 planes carve anti-pads around
    # the new through-via barrels.
    straggler_vias = []
    straggler_tracks = 0
    straggler_summary = []
    for nc, nm in straggler_nc.items():
        iso = straggler_pads.get(nc, [])
        if not iso:
            continue
        n_before = len(via_centers)
        placed = list(_add_rail_pad_vias(board, nc, bga_xy, {nc: iso}, pads,
                                        sig_circles, sig_segs, sig_segs_fcu,
                                        via_centers, tie_segs))
        straggler_vias.extend(placed)
        nt2 = _tie_stragglers_to_rail(board, nc, via_centers[n_before:],
                                     straggler_obs[0], straggler_obs[1])
        straggler_tracks += nt2
        straggler_summary.append(f"{nm}:{len(placed)}via/{nt2}trk")
    if straggler_vias:
        print(f"  straggler power tie: {len(straggler_vias)} via(s), "
              f"{straggler_tracks} B.Cu tie track(s) ({', '.join(straggler_summary)})")
    vias += straggler_vias

    # fill all zones now that vias exist (ZONE_FILLER.Fill takes a vector)
    vec = pcbnew.ZONES()
    for z in zones:
        vec.append(z)
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(vec)

    # stats, built from the in-memory objects we hold (the zone/via proxies we
    # created stay valid; re-loading the board after mutation is unreliable in
    # this swig build).
    return {
        "zones": [
            {
                "layer_id": z.GetFirstLayer(),
                "layer": board.GetLayerName(z.GetFirstLayer()),   # user name
                "net": z.GetNetname(),
                "filled_mm2": z.GetFilledArea() / 1e12,   # nm^2 -> mm^2
            }
            for z in zones
        ],
        "via_count": len(vias),
        "via_nets": {v.GetNetname() for v in vias},
    }


def main() -> dict:
    """Run the copper-plane foundation on the board at PCB_PATH, save it, and
    return a stats dict. Loads + saves the board; tests should prefer calling
    ``build`` on a board they load once (see module docstring)."""
    board = pcbnew.LoadBoard(str(PCB_PATH))
    # Capture everything we read from the board BEFORE any mutation: KiCad's
    # python binding returns raw SwigPyObjects (missing their python proxy
    # methods like GetX/GetCenter) for values fetched AFTER the board is
    # mutated, so read netcodes + the edge bbox up front.
    gnd = _find_netcode(board, "GND")
    try:
        p33 = _find_netcode(board, "+3V3")
    except KeyError:
        p33 = None
    bbox = board.GetBoardEdgesBoundingBox()
    pads = _pad_obstacle(board)       # collect pad obstacles before mutation

    stats = build(board, gnd, p33, bbox, pads)

    board.SetModified()
    pcbnew.SaveBoard(str(PCB_PATH), board)

    if __name__ != "__main__":      # called as a function (e.g. from tests)
        return stats
    # called as a script: print a human summary
    pwr = "+3V3" if p33 else "(none)"
    print(f"  zones: {len(stats['zones'])} (GND plane + {pwr} plane)")
    print(f"  rail + stitching vias: {stats['via_count']}")
    for z in stats["zones"]:
        print(f"    zone {z['net']:>6} on {z['layer']:>6}: "
              f"{z['filled_mm2']:.1f} mm^2 filled")
    print(f"  saved {PCB_PATH}")
    return stats


if __name__ == "__main__":
    main()