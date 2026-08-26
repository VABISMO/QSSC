#!/usr/bin/env python3
"""Headless copper-plane foundation for the QSSC PCB (``hardware/pcb/qssc.kicad_pcb``).

This is NOT an autorouter. A quality DRC-clean autoroute of a 65-net board with
a 237-pad FPGA BGA cannot be produced headlessly: FreeRouting/topola are not
installed, and writing one from scratch in a script would leave DRC violations
(a "routed" board that fails DRC is *worse* than an honestly-unrouted one). The
FPGA BGA's power/GND pads also have no netlist without UG475, so 275 of 549 pads
are unnetted and unroutable headlessly.

What this DOES do -- the copper-plane foundation that any future GUI routing
session builds on, reproducibly and DRC-clean (copper pours auto-avoid pads of
other nets):

  * **In1.Cu : solid GND plane** (full board) -- continuous ground reference.
  * **In2.Cu : solid +3V3 plane** (full board) -- the main digital rail; the
    stackup's "PWR" layer is repurposed to +3V3 (the highest-pad-count rail).
  * **F.Cu + B.Cu : GND flood pours** -- surface GND pads connect to a local
    ground pour immediately; improves EMI/signal-integrity on the outer layers.
  * **GND stitching vias** on a coarse grid, each pad-clearance-checked, tying
    the F.Cu flood, the In1.Cu plane and the B.Cu flood into ONE connected
    ground mesh across all copper layers.

Still GUI-pending (honestly): BGA fanout, 50-ohm RF microstrip, USB-C diff pair,
analog guard rings, and the +5V / +5V_RF / +5V_IN / +1V8 / +1V0 power rails and
all signal routing -- these need an autorouter (FreeROUTING GUI) or a manual
pcbnew session. This script adds the plane + ground-mesh foundation only.

Idempotent: it first strips any zones/tracks it (or a prior run) added, then
rebuilds, so re-running on an already-processed board is safe. It expects the
unrouted board produced by ``tools/gen_qssc_kicad.py`` (run that first if the
board was hand-edited).

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

# layer ids (KiCad 8 fixed copper indices)
F_CU = 0
IN1_CU = 1      # GND plane
IN2_CU = 2      # +3V3 plane
B_CU = 31

GRID_STEP_NM = 10_000_000          # 10 mm stitching grid
VIA_DIAM_NM = 600_000              # 0.6 mm via
VIA_DRILL_NM = 300_000             # 0.3 mm drill
STITCH_PAD_CLEARANCE_NM = 200_000  # 0.2 mm extra keepout from pads


def _find_netcode(board, name):
    for code, net in board.GetNetInfo().NetsByNetcode().items():
        if net.GetNetname() == name:
            return code
    raise KeyError(f"net {name!r} not in board netlist")


def _clear_routed(board):
    """Idempotent reset of the plane foundation: remove every zone (planes +
    floods) and every GND track/via (the stitching mesh this script owns), but
    KEEP non-GND signal tracks/vias so a prior ``route_signals.py`` pass (BGA
    fanout + signal routing) is preserved when the plane foundation is
    (re)built on top of it. The generated board has no tracks, so on a clean
    board this only drops the zones; on a re-run it rebuilds the planes + mesh
    while leaving signal routing intact.

    The swig track proxy is iterable by for-loop but ``list()`` of it can raise
    ``SwigPyObject not iterable`` after the board is mutated, so we collect
    references via comprehensions (which iterate, unlike ``list()``) first.
    """
    zones = [z for z in board.Zones()]
    for z in zones:
        board.Remove(z)
    tracks = [t for t in board.Tracks()]
    for t in tracks:
        if t.GetNetname() == "GND":
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
    # Capture signal tracks/vias BEFORE _clear_routed mutates the board: the
    # swig proxy layer breaks (board.Tracks() returns a bare SwigPyObject) once
    # board.Remove() runs in _clear_routed, so the stitching-via grid must be
    # built from obstacles snapshoted up front. _clear_routed keeps signal
    # copper (only removes zones + GND tracks/vias), so the snapshot is valid.
    sig_circles, sig_segs = _signal_obstacles(board)
    _clear_routed(board)

    zones = [_add_gnd_or_pwr_zone(board, IN1_CU, gnd, bbox)]            # GND plane
    if p33 is not None:
        zones.append(_add_gnd_or_pwr_zone(board, IN2_CU, p33, bbox))    # +3V3 plane
    zones.append(_add_gnd_or_pwr_zone(board, F_CU, gnd, bbox))          # GND flood top
    zones.append(_add_gnd_or_pwr_zone(board, B_CU, gnd, bbox))         # GND flood bot

    # GND stitching vias across the ground mesh (added BEFORE filling so the
    # +3V3 plane carves anti-pad clearances around each GND via barrel).
    vias = _add_stitching_vias(board, gnd, bbox, pads, sig_circles, sig_segs)

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
    print(f"  zones: {len(stats['zones'])} (In1.Cu=GND, "
          f"In2.Cu={'+3V3' if p33 else 'GND'}, F.Cu=GND, B.Cu=GND)")
    print(f"  GND stitching vias: {stats['via_count']}")
    for z in stats["zones"]:
        print(f"    zone {z['net']:>6} on {z['layer']:>6}: "
              f"{z['filled_mm2']:.1f} mm^2 filled")
    print(f"  saved {PCB_PATH}")
    return stats


if __name__ == "__main__":
    main()