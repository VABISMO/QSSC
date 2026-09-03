#!/usr/bin/env python3
"""Headless copper-plane foundation for the QSSC PCB (``hardware/pcb/qssc.kicad_pcb``).

This is NOT an autorouter. A quality DRC-clean autoroute of a 65-net board with
a 237-pad FPGA BGA cannot be produced headlessly: FreeRouting/topola are not
installed, and writing one from scratch in a script would leave DRC violations
(a "routed" board that fails DRC is *worse* than an honestly-unrouted one). The
FPGA BGA's power/GND pads also have no netlist without UG475, so 275 of 549 pads
are unnetted and unroutable headlessly.

What this DOES do -- the copper-plane foundation for the 6-layer stackup
(F.Cu sig / In1.Cu GND / In2.Cu sig / In3.Cu sig / In4.Cu +3V3 / B.Cu sig),
reproducibly and DRC-clean (copper pours auto-avoid pads of other nets):

  * **In1.Cu : solid GND plane** (full board) -- continuous ground reference.
  * **In4.Cu : solid +3V3 plane** (full board) -- the main digital rail
    (detected by the "PWR" layer label; In2.Cu on the chip module).
  * **Rail pad vias** -- every surface GND / +3V3 SMD pad (not on the BGA) gets
    a through-via to its inner plane. The 6-layer stackup has no F.Cu/B.Cu GND
    flood, so surface rail pads need an explicit via to reach the plane.
  * **GND stitching vias** on a coarse grid, each pad-clearance-checked, as
    distributed signal-integrity return vias tying the plane to the surfaces.

BGA via-in-pad (GND/+3V3/signal/power) and ALL signal + non-plane power routing
are owned by ``tools/route_signals.py`` (run it FIRST). This script preserves
the small BGA VIPs and all signal copper when (re)building the planes.

Still GUI-pending (honestly): 50-ohm RF microstrip impedance verification with a
solver, USB-C diff-pair length-skew tuning, analog guard-ring final geometry.

Idempotent: it first strips any zones and its own wide GND/+3V3 vias (kept: the
small BGA VIPs and signal copper from route_signals), then rebuilds, so
re-running on an already-processed board is safe. It expects the board produced
by ``tools/gen_qssc_kicad.py`` (run that first if the board was hand-edited).

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
STITCH_PAD_CLEARANCE_NM = 200_000  # 0.2 mm extra keepout from pads
# Through-via-in-pad VIPs placed by route_signals._bga_fanout are 0.20 mm; this
# script's own vias are 0.6 mm. _clear_routed strips only its own (wide) GND /
# +3V3 vias and preserves the small BGA VIPs (owned by route_signals).
VIP_MAX_NM = 300_000
RAIL_VIA_OFFSET_NM = 600_000       # 0.6 mm offset from a rail pad to its via
BGA_REF = "U1"                     # the FPGA BGA (skip its pads for rail-pad vias)


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
        if nm in ("GND", "+3V3") and t.GetWidth() > VIP_MAX_NM:
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
    """Return a list of (cx, cy, r) pad circles for via-clearance checks
    (pad centre + half its bounding-box diagonal as an approximated radius)."""
    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            pos = pad.GetCenter()
            bb = pad.GetBoundingBox()
            r = max(bb.GetWidth(), bb.GetHeight()) / 2
            pads.append((pos.x, pos.y, r))
    return pads


def _signal_obstacles(board):
    """Return (circles, segs) for SIGNAL copper already on the board -- vias as
    (cx, cy, r) circles and tracks as (x1, y1, x2, y2, hw) segments. GND and
    unnetted copper are skipped: a GND stitching via on a GND track is fine, and
    unnetted copper has no net to short. Used so the stitching-via grid never
    pierces a signal track/via (the original pad-only check shorted ~20 nets)."""
    circles = []
    segs = []
    for t in board.Tracks():
        nm = t.GetNetname()
        if not nm or nm == "GND":
            continue
        if isinstance(t, pcbnew.PCB_VIA):
            c = t.GetPosition()
            circles.append((c.x, c.y, t.GetWidth() / 2))
        else:
            s = t.GetStart()
            e = t.GetEnd()
            segs.append((s.x, s.y, e.x, e.y, t.GetWidth() / 2))
    return circles, segs


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


def _capture_rail_pads(board):
    """Snapshot every pad's (centre, netcode, attribute) and the BGA pad centres
    BEFORE any board mutation. KiCad's swig binding returns a bare non-iterable
    SwigPyObject for ``board.GetFootprints()``/``Footprints()`` once
    ``board.Remove()`` runs in ``_clear_routed``, so ``_add_rail_pad_vias``
    cannot iterate footprints after the reset. Returns
    (bga_xy_set, {netcode: [(x, y, attribute), ...]}) for non-BGA pads only."""
    bga_xy = set()
    by_net = {}
    for fp in board.GetFootprints():
        is_bga = fp.GetReference() == BGA_REF
        for pad in fp.Pads():
            c = pad.GetCenter()
            x, y = int(c.x), int(c.y)
            if is_bga:
                bga_xy.add((x, y))
                continue
            by_net.setdefault(pad.GetNetCode(), []).append(
                (x, y, pad.GetAttribute()))
    return bga_xy, by_net


def _add_rail_pad_vias(board, netcode, bga_xy, rail_pads, pads, sig_circles, sig_segs):
    """For every SMD pad on this rail net (GND or +3V3) that is NOT on the BGA
    (U1 -- its plane balls get via-in-pad from route_signals._bga_fanout) and
    not a PTH pin, place one through-via offset RAIL_VIA_OFFSET_NM from the pad
    centre so the surface pad reaches the inner plane (In1.Cu GND or the PWR
    +3V3 plane). With the 6-layer stackup F.Cu/B.Cu carry no GND flood, so a
    surface rail pad needs an explicit via to its plane. Returns the vias.

    ``bga_xy``/``rail_pads`` are a pre-mutation snapshot from
    ``_capture_rail_pads`` (board.GetFootprints() is unreliable after
    ``_clear_routed`` mutates the board)."""
    via_r = VIA_DIAM_NM / 2
    min_clear = via_r + STITCH_PAD_CLEARANCE_NM
    vias = []
    for (px, py, attrib) in rail_pads.get(netcode, []):
        if attrib == pcbnew.PAD_ATTRIB_PTH:
            continue
        if (px, py) in bga_xy:
            continue
        # try 4 orthogonal offsets, pick the first that clears pads + sig
        for dx, dy in ((RAIL_VIA_OFFSET_NM, 0), (-RAIL_VIA_OFFSET_NM, 0),
                       (0, RAIL_VIA_OFFSET_NM), (0, -RAIL_VIA_OFFSET_NM)):
            vx, vy = px + dx, py + dy
            ok = True
            for ox, oy, pr in pads:
                if (vx - ox) ** 2 + (vy - oy) ** 2 < (min_clear + pr) ** 2:
                    ok = False
                    break
            if ok:
                for cx, cy, cr in sig_circles:
                    if (vx - cx) ** 2 + (vy - cy) ** 2 < (min_clear + cr) ** 2:
                        ok = False
                        break
            if ok:
                for x1s, y1s, x2s, y2s, hw in sig_segs:
                    if _seg_dist2(vx, vy, x1s, y1s, x2s, y2s) < (min_clear + hw) ** 2:
                        ok = False
                        break
            if ok:
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pcbnew.VECTOR2I(int(vx), int(vy)))
                v.SetNetCode(netcode)
                v.SetViaType(pcbnew.VIATYPE_THROUGH)
                v.SetDrill(VIA_DRILL_NM)
                v.SetWidth(VIA_DIAM_NM)
                board.Add(v)
                vias.append(v)
                break
    return vias


def _add_stitching_vias(board, netcode, bbox, pads, sig_circles, sig_segs):
    """Place GND through-vias on a coarse grid, skipping any point too close to
    a pad OR an existing signal track/via. Ties the F.Cu/In1.Cu/B.Cu GND copper
    into one mesh. Returns the list of created via objects (kept so callers can
    verify without re-loading the board -- KiCad's python binding can return
    bare SwigPyObjects for a board re-loaded after mutation).

    ``sig_circles``/``sig_segs`` are the signal via-circles / track-segments
    snapshoted BEFORE _clear_routed (the swig proxy layer breaks board.Tracks()
    after board.Remove(), so callers capture them up front)."""
    via_r = VIA_DIAM_NM / 2
    min_clear = via_r + STITCH_PAD_CLEARANCE_NM
    margin = 2_000_000  # 2 mm in from the board edge
    x0 = bbox.GetX() + margin
    y0 = bbox.GetY() + margin
    x1 = bbox.GetX() + bbox.GetWidth() - margin
    y1 = bbox.GetY() + bbox.GetHeight() - margin
    vias = []
    x = x0
    while x <= x1:
        y = y0
        while y <= y1:
            clear = True
            for px, py, pr in pads:
                dx = x - px
                dy = y - py
                if dx * dx + dy * dy < (min_clear + pr) ** 2:
                    clear = False
                    break
            if clear:
                for cx, cy, cr in sig_circles:           # signal vias
                    dx = x - cx
                    dy = y - cy
                    if dx * dx + dy * dy < (min_clear + cr) ** 2:
                        clear = False
                        break
            if clear:
                for x1s, y1s, x2s, y2s, hw in sig_segs:   # signal tracks
                    if _seg_dist2(x, y, x1s, y1s, x2s, y2s) < (min_clear + hw) ** 2:
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
            y += GRID_STEP_NM
        x += GRID_STEP_NM
    return vias


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
    sig_circles, sig_segs = _signal_obstacles(board)
    bga_xy, rail_pads = _capture_rail_pads(board)
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
    # plane carves anti-pad clearances around each GND via barrel.
    vias = list(_add_rail_pad_vias(board, gnd, bga_xy, rail_pads, pads, sig_circles, sig_segs))
    if p33 is not None and pwr_layer is not None:
        vias += list(_add_rail_pad_vias(board, p33, bga_xy, rail_pads, pads, sig_circles, sig_segs))

    # GND stitching vias across the ground mesh (coarse grid of return vias
    # tying the plane to the surface layers for signal-integrity returns).
    vias += _add_stitching_vias(board, gnd, bbox, pads, sig_circles, sig_segs)

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