#!/usr/bin/env python3
"""Tie the USB-C reversible D+/D- pad pairs that the autorouter leaves split.

A USB-C receptacle exposes D+ on A6 AND B6 (D- on A7 AND B7).  The plug only
uses one side per orientation, so BOTH pads of each pair MUST be on the net or
USB fails in one orientation -- a production defect, not a cosmetic gap.

FreeRouting routes the side it can reach (the one with a clear F.Cu escape to a
via) and leaves the other pad of the pair isolated: the dense 0.5 mm USB-C pad
field blocks any straight same-row tie (A6--B6 would cross A7 = D-, shorting
D+ to D-), and the isolated pad has no via, so the autorouter has no way in.

This ties the isolated pad to its partner's trunk via on an inner plane layer
(In1.Cu = GND plane): a 0.20/0.10 mm through via-in-pad at the isolated pad,
then a short L-track on In1.Cu to the partner's trunk via (the nearest through
via on the net).  The contact pads are F.Cu SMT, so In1.Cu past their columns
is empty except for the GND plane; route_qssc_pcb re-pours the plane afterwards,
carving clearance around the new via + track (no short to GND).  F.Cu is boxed
in (inward = opposite-pair D-/D+ escapes + vias; outward = VBUS_USB rail; a
same-row tie crosses the intervening pad), so the inner-layer via tie is the
only DRC-clean path.  Run route_qssc_pcb AFTER this to re-pour the plane.

Run AFTER FreeRouting + widen_tracks + route_qssc_pcb, then re-DRC:

    python3 tools/usb_c_reversible_tie.py [board.kicad_pcb]

System ``python3`` (pcbnew).  No .venv.  Idempotent: skips a pair whose isolated
pad already touches a same-net track.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOARD = ROOT / "hardware" / "pcb" / "qssc.kicad_pcb"

J10_REF = "J10"
# USB-C reversible pairs: (pad_a, pad_b, netname).  Both pads are the same net.
PAIRS = [("A6", "B6", "USB_DP"), ("A7", "B7", "USB_DM")]
TRACK_WIDTH = 200_000          # 0.20 mm (USB netclass)
VIA_DIAM = 200_000             # 0.20 mm HDI via-in-pad (fits ~0.45 mm USB-C pad)
VIA_DRILL = 100_000            # 0.10 mm drill
ENDPOINT_TOL2 = (0.06 * 1e6) ** 2   # 0.06 mm endpoint touch tolerance


def _pad(fp, name):
    return next((p for p in fp.Pads() if p.GetPadName() == name), None)


def _pad_touches_net_track(board, pad, netname):
    """True if any same-net track/via endpoint is within tol of the pad centre."""
    px, py = pad.GetPosition().x, pad.GetPosition().y
    for t in board.GetTracks():
        n = t.GetNet()
        if not n or n.GetNetname() != netname:
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            p = t.GetPosition()
            if (p.x - px) ** 2 + (p.y - py) ** 2 <= ENDPOINT_TOL2:
                return True
        elif t.Type() == pcbnew.PCB_TRACE_T:
            for ep in (t.GetStart(), t.GetEnd()):
                if (ep.x - px) ** 2 + (ep.y - py) ** 2 <= ENDPOINT_TOL2:
                    return True
    return False


def _partner_escape_y(board, partner_pad, netname):
    """Unused (kept for reference) -- the outward route does not need it."""
    return None


def _add_track(board, netcode, layer, start, end):
    t = pcbnew.PCB_TRACK(board)
    t.SetNetCode(netcode)
    t.SetLayer(layer)
    t.SetWidth(TRACK_WIDTH)
    t.SetStart(pcbnew.VECTOR2I(int(start[0]), int(start[1])))
    t.SetEnd(pcbnew.VECTOR2I(int(end[0]), int(end[1])))
    board.Add(t)
    return t


def tie(board_path: Path) -> int:
    board = pcbnew.LoadBoard(str(board_path))
    j10 = next((f for f in board.GetFootprints() if f.GetReference() == J10_REF), None)
    if j10 is None:
        print(f"usb_c_reversible_tie: no {J10_REF} on {board_path.name}")
        return 0
    added = 0
    for pad_a, pad_b, netname in PAIRS:
        pa, pb = _pad(j10, pad_a), _pad(j10, pad_b)
        if pa is None or pb is None:
            continue
        net = board.FindNet(netname)
        if net is None or net.GetNetCode() < 0:
            continue
        nc = net.GetNetCode()
        a_touch = _pad_touches_net_track(board, pa, netname)
        b_touch = _pad_touches_net_track(board, pb, netname)
        # Both already tied -> nothing to do.  Both isolated -> not the pattern
        # this tool fixes (FreeRouting always reaches one side).  Tie the
        # isolated one into the connected one's escape.
        if a_touch and b_touch:
            continue
        if not a_touch and not b_touch:
            print(f"  {netname}: both {pad_a}+{pad_b} isolated -- skip (no partner escape)")
            continue
        iso, part = (pa, pb) if not a_touch else (pb, pa)
        ix, iy = iso.GetPosition().x, iso.GetPosition().y
        px, py = part.GetPosition().x, part.GetPosition().y
        # The isolated pad is boxed in on F.Cu: the inward side carries the
        # opposite-pair (D-/D+) F.Cu escapes + vias, the outward side carries
        # the VBUS_USB F.Cu rail, and a straight same-row tie crosses the
        # intervening pad (A6<->B6 crosses A7 = D-).  F.Cu has no clear path.
        # Tie on an inner plane layer instead: add a 0.20/0.10 mm through
        # via-in-pad at the isolated pad, then an L-track on In1.Cu to the
        # partner's trunk via (the through via on this net nearest the partner
        # pad -- it already ties the partner pad to the net trunk).  In1.Cu is
        # the GND plane; route_qssc_pcb re-pours it afterwards, carving
        # clearance around the new via + track so there is no short to GND.
        # The SMT contact pads live on F.Cu only, so In1.Cu past their columns
        # is empty except for the plane (which the re-pour clears).
        trunk = None
        best = None
        for t in board.GetTracks():
            if t.Type() != pcbnew.PCB_VIA_T:
                continue
            n = t.GetNet()
            if not n or n.GetNetname() != netname:
                continue
            tp = t.GetPosition()
            d = (tp.x - px) ** 2 + (tp.y - py) ** 2
            if best is None or d < best:
                best, trunk = d, (tp.x, tp.y)
        if trunk is None:
            print(f"  {netname}: no trunk via near {part.GetPadName()} -- skip")
            continue
        tx, ty = trunk
        # through via-in-pad at the isolated pad (HDI 0.20/0.10, fits the ~0.45
        # mm USB-C pad, clears the 0.5 mm-spaced neighbour pad by ~0.17 mm).
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(int(ix), int(iy)))
        v.SetNetCode(nc)
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetDrill(VIA_DRILL)
        v.SetWidth(VIA_DIAM)
        board.Add(v)
        # L-track on In1.Cu: iso via -> (iso_x, trunk_y) -> trunk via.
        _add_track(board, nc, pcbnew.In1_Cu, (ix, iy), (ix, ty))
        _add_track(board, nc, pcbnew.In1_Cu, (ix, ty), (tx, ty))
        added += 3
        print(f"  {netname}: tied {iso.GetPadName()} -> {part.GetPadName()} "
              f"via In1.Cu (via@{ix/1e6:.3f},{iy/1e6:.3f} -> trunk@{tx/1e6:.3f},{ty/1e6:.3f})")
    if added:
        board.Save(str(board_path))
    print(f"usb_c_reversible_tie: added {added} track(s) on {board_path.name}")
    return added


def main() -> int:
    board = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BOARD
    tie(board)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())