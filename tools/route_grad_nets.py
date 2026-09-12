#!/usr/bin/env python3
"""Targeted manual router for the Invention-5 gradient nets on qssc-grad.

FreeRouting cannot autoroute the dense 1500-track qssc-grad board in a
memory-constrained host (it OOMs before writing a SES). This script routes
ONLY the gradient nets the variant adds, on top of the injected base routing,
with simple DRC-clean manhattan tracks.

Layer strategy (6-layer stack: F.Cu / In1 GND / In2 +3V3 / In3 / In4 / B.Cu):
  * B.Cu is completely empty on the grad board (no base tracks at all), so it
    is the dedicated layer for the LONG nets -- the 3 FPGA SPI lines
    (grad_cs/sclk/mosi from BGA row-V balls V6/V9/V12 down to Ugrad) and the
    GRAD_COIL_N coil return (Lgrad on the die top edge -> Qgrad drain) and the
    +5V coil feed (D5 -> Lgrad pad1).  Each end gets a 0.20 mm through via
    OFFSET off the pad (dogbone) so there is no via-in-pad, with a short F.Cu
    stub tying pad to via.  The through vias pierce the In1/In2 planes; a zone
    re-pour carves the anti-pads.
  * The 4 SHORT analog nets (GRAD_DAC_OUT, GRAD_GATE, GRAD_SENSE, GRAD_NFB)
    route directly on F.Cu in the grad-component cluster (bottom-right, only 3
    base GND tracks there) -- manhattan L-tracks, no vias.
  * grad_en is a 1-pad FPGA STATUS output, NC on the PCB -- left unrouted.

Idempotent: strips any existing tracks/vias on the grad nets first. Run
route_qssc_pcb.py afterward to re-pour the GND/+3V3 planes (carving anti-pads
around the new vias), then DRC.

Usage:
  env PYTHONPATH=$PWD/tools python3 tools/route_grad_nets.py
"""
from __future__ import annotations
import sys
import pcbnew
from route_qssc_pcb import patch_swig_iterators, F_CU, B_CU
from _kicad_compat import exit_cleanly

patch_swig_iterators()

# 6-layer HDI via floors (match route_signals): 0.20 mm via, 0.10 mm drill.
VIA_W_NM = 200_000
VIA_DRILL_NM = 100_000
PWR_VIA_W_NM = 400_000        # 0.40 mm via for the 2.2 A coil-current nets
PWR_VIA_DRILL_NM = 200_000
SIG_W_NM = 200_000            # 0.20 mm signal track
PWR_W_NM = 500_000            # 0.50 mm track for the 2.2 A coil feed/return

# nets that route on B.Cu (long) vs F.Cu (short, grad cluster)
LONG_NETS = ("grad_cs", "grad_sclk", "grad_mosi", "GRAD_COIL_N")
SHORT_NETS = ("GRAD_DAC_OUT", "GRAD_GATE", "GRAD_NFB", "GRAD_SENSE",
              "GRAD_COIL_P")
# nets this script OWNS and re-routes from scratch (idempotent clear target).
# +5V is intentionally EXCLUDED: it is a shared power rail with many pads we
# do not own -- clearing it would wipe the rail and orphan every +5V pad.  We
# only ADD one feed stub to +5V (_route_5v_feed), never clear it.
GRAD_NETS = LONG_NETS + SHORT_NETS

NM = 1_000_000  # 1 mm in nm


def _clear_grad(board):
    """Strip every track/via on a grad net (idempotent)."""
    gone = 0
    for t in [t for t in board.Tracks()]:
        if t.GetNetname() in GRAD_NETS:
            board.Remove(t)
            gone += 1
    return gone


def _add_track(board, x1, y1, x2, y2, layer, netcode, w=SIG_W_NM):
    tr = pcbnew.PCB_TRACK(board)
    tr.SetStart(pcbnew.VECTOR2I(int(x1), int(y1)))
    tr.SetEnd(pcbnew.VECTOR2I(int(x2), int(y2)))
    tr.SetLayer(layer)
    tr.SetWidth(int(w))
    tr.SetNetCode(netcode)
    board.Add(tr)
    return tr


def _add_via(board, x, y, netcode, w=VIA_W_NM, drill=VIA_DRILL_NM):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
    v.SetNetCode(netcode)
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetDrill(int(drill))
    v.SetWidth(int(w))
    board.Add(v)
    return v


def _capture_pads(board):
    """Snapshot every pad on the board into {netname: [(ref,padname,x,y), ...]}
    BEFORE any board.Remove().  On KiCad 10.x board.GetFootprints() and the pad
    iterators corrupt once _clear_grad starts removing tracks, so pad lookups
    must read from this pre-captured plain-python dict, not the live board."""
    idx = {}
    for m in board.GetFootprints():
        ref = m.GetReference()
        for p in m.Pads():
            nm = p.GetNetname()
            if not nm:
                continue
            pos = p.GetPosition()
            idx.setdefault(nm, []).append((ref, p.GetPadName(),
                                           int(pos.x), int(pos.y)))
    return idx


def _pads(pad_index, netname):
    """All pads on netname, from the pre-captured snapshot."""
    return list(pad_index.get(netname, []))


def _netmap(board):
    """{netname: netcode} captured BEFORE any board.Remove() -- on KiCad 10.x
    the swig proxy for NetsByNetcode() corrupts after _clear_grad's Remove
    calls, so this must run once, up front."""
    m = {}
    for _code, net in board.GetNetInfo().NetsByNetcode().items():
        m[net.GetNetname()] = net.GetNetCode()
    return m


def _netcode(netmap, netname):
    if netname not in netmap:
        raise KeyError(netname)
    return netmap[netname]


def _manhattan(board, x1, y1, x2, y2, layer, nc, vfirst=True):
    """Two-segment L-track. vfirst=True -> vertical then horizontal."""
    if vfirst:
        _add_track(board, x1, y1, x1, y2, layer, nc)
        _add_track(board, x1, y2, x2, y2, layer, nc)
    else:
        _add_track(board, x1, y1, x2, y1, layer, nc)
        _add_track(board, x2, y1, x2, y2, layer, nc)


# ---- via-offset policy: place each through via OFF the pad (dogbone) so no
# via-in-pad warning.  Offset direction depends on the pad's component so the
# via lands in clear copper, not on a neighbour pad.
def _via_offset(ref, padname, px, py, tx, ty):
    """Return (via_x, via_y, stub_x, stub_y) for a pad at (px,py) routing
    toward (tx,ty).  The via is offset off the pad; the stub endpoint is the
    pad-centre (the F.Cu stub runs pad-centre -> via)."""
    if ref == "U1":
        # BGA ball (0.40 mm pad): dogbone south, outside the FPGA south edge.
        return (px, py + int(0.60 * NM))
    if ref == "Ugrad":
        # SOIC left-side pins (x=123.53): via to the LEFT of the row.
        return (px - int(1.30 * NM), py)
    if ref == "Qgrad":
        # SOT-23 drain (pin3, right side): via to the RIGHT.
        return (px + int(1.40 * NM), py)
    if ref == "Lgrad":
        # 2 mm coil pad: via below (south), away from the die.
        return (px, py + int(1.30 * NM))
    if ref == "D5":
        # diode pad: via to the right.
        return (px + int(1.10 * NM), py)
    # default: offset toward the target
    dx = 1 if tx >= px else -1
    dy = 1 if ty >= py else -1
    return (px + dx * int(0.6 * NM), py + dy * int(0.6 * NM))


def _route_long(board, netmap, pad_index, netname):
    """Through via (dogbone) at each end + manhattan on B.Cu.  Coil-current
    nets (GRAD_COIL_N) use the wider 0.50 mm track + 0.40 mm via."""
    nc = _netcode(netmap, netname)
    pads = _pads(pad_index, netname)
    if len(pads) < 2:
        return 0
    pads.sort(key=lambda p: p[3])  # by y, top->bottom
    (r0, n0, x0, y0), (r1, n1, x1, y1) = pads[0], pads[-1]
    vx0, vy0 = _via_offset(r0, n0, x0, y0, x1, y1)
    vx1, vy1 = _via_offset(r1, n1, x1, y1, x0, y0)
    pwr = netname == "GRAD_COIL_N"
    tw = PWR_W_NM if pwr else SIG_W_NM
    vw = PWR_VIA_W_NM if pwr else VIA_W_NM
    vd = PWR_VIA_DRILL_NM if pwr else VIA_DRILL_NM
    # F.Cu stubs pad-centre -> via (tie the surface pad to the dogbone via)
    _add_track(board, x0, y0, vx0, vy0, F_CU, nc, w=tw)
    _add_track(board, x1, y1, vx1, vy1, F_CU, nc, w=tw)
    _add_via(board, vx0, vy0, nc, w=vw, drill=vd)
    _add_via(board, vx1, vy1, nc, w=vw, drill=vd)
    # B.Cu manhattan between the two vias (vertical first)
    _add_track(board, vx0, vy0, vx0, vy1, B_CU, nc, w=tw)
    _add_track(board, vx0, vy1, vx1, vy1, B_CU, nc, w=tw)
    return 2


def _route_5v_feed(board, netmap, pad_index):
    """Connect Rgrad_feed pad1 (+5V) to the existing +5V rail on B.Cu.

    The coil is a KiCad net-tie (Lgrad pads 1,2 merged by its fp_poly), so
    Lgrad no longer carries the +5V net -- pin1 is GRAD_COIL_P.  A 0R feed
    resistor Rgrad_feed bridges +5V (pin1) -> GRAD_COIL_P (pin2 -> Lgrad.1),
    keeping +5V and the coil nets electrically separate everywhere except at
    the resistor.  This stub feeds only the Rgrad_feed +5V pad to the rail.

    The +5V rail runs as a B.Cu track along the top of the board at y~16.8 mm
    (x 67.6->106).  Rgrad_feed.1 is placed at (70,82) on F.Cu, inside the
    rail's x-range.  A 0.40 mm through via drops the pad to B.Cu, then a 0.50
    mm B.Cu track runs straight up to the rail at (70,16.8), T-joining it
    (same net -> a clean junction, no DRC).  The 0.50 mm track carries the
    2.2 A coil current on 2 oz Cu.
    """
    nc = _netcode(netmap, "+5V")
    rpads = [p for p in _pads(pad_index, "+5V") if p[0] == "Rgrad_feed"]
    if not rpads:
        return 0
    _, _, px, py = rpads[0]  # Rgrad_feed.1 (+5V pad)
    rail_x, rail_y = px, int(16.8 * NM)  # +5V rail crosses x=px at y=16.8
    vx, vy = px, py - int(1.30 * NM)     # via north of the resistor pad
    _add_track(board, px, py, vx, vy, F_CU, nc, w=PWR_W_NM)
    _add_via(board, vx, vy, nc, w=PWR_VIA_W_NM, drill=PWR_VIA_DRILL_NM)
    _add_track(board, vx, vy, rail_x, rail_y, B_CU, nc, w=PWR_W_NM)
    return 1


def _route_short(board, netmap, pad_index, netname):
    """Manhattan L-track on F.Cu between the pads (no vias). Chain >2 pads."""
    nc = _netcode(netmap, netname)
    pads = _pads(pad_index, netname)
    if len(pads) < 2:
        return 0
    pads.sort(key=lambda p: p[3])
    segs = 0
    for i in range(len(pads) - 1):
        x0, y0 = pads[i][2], pads[i][3]
        x1, y1 = pads[i + 1][2], pads[i + 1][3]
        _manhattan(board, x0, y0, x1, y1, F_CU, nc, vfirst=True)
        segs += 1
    return segs


def route(board_path: str) -> dict:
    board = pcbnew.LoadBoard(board_path)
    # capture the netcode map + pad index BEFORE any board.Remove(): the swig
    # proxies for NetsByNetcode() and GetFootprints() corrupt once _clear_grad
    # starts removing tracks.
    netmap = _netmap(board)
    pad_index = _capture_pads(board)
    cleared = _clear_grad(board)
    n_long = n_short = n_5v = 0
    missing = []
    for nm in LONG_NETS:
        try:
            n_long += _route_long(board, netmap, pad_index, nm)
        except KeyError:
            missing.append(nm)
    for nm in SHORT_NETS:
        try:
            n_short += _route_short(board, netmap, pad_index, nm)
        except KeyError:
            missing.append(nm)
    try:
        n_5v = _route_5v_feed(board, netmap, pad_index)
    except KeyError:
        missing.append("+5V")
    # NB: do NOT call fill_all_zones() here. On KiCad 10.x the ZONE_FILLER
    # segfaults inside the same process that just created tracks/vias via
    # board.Add(), and the board.Zones() swig iterator is already corrupt by
    # this point. Save the routed board now and re-pour the GND/+3V3 planes
    # in a FRESH process (route() -> save; a separate `fill` step reloads,
    # fills, saves). The DRC run that follows re-pours via that step.
    board.SetModified()
    pcbnew.SaveBoard(board_path, board)
    return dict(cleared=cleared, long_vias=n_long, short_segs=n_short,
                feed_5v=n_5v, missing=missing)


if __name__ == "__main__":
    bp = sys.argv[1] if len(sys.argv) > 1 else "hardware/pcb/qssc-grad.kicad_pcb"
    st = route(bp)
    print(f"route_grad_nets -> {bp}:")
    print(f"  cleared existing grad tracks/vias: {st['cleared']}")
    print(f"  long-net vias placed: {st['long_vias']}")
    print(f"  short-net L-segments: {st['short_segs']}")
    print(f"  +5V coil-feed vias: {st['feed_5v']}")
    if st['missing']:
        print(f"  missing nets (not in netlist): {st['missing']}")
    exit_cleanly(0)