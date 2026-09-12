#!/usr/bin/env python3
"""Inject main's routed copper (tracks + vias + zone outlines) into qssc-grad.

The grad board = base main board + 7 gradient parts, with the base footprints
at IDENTICAL positions (verified: 0 diffs over 126 refs).  So main's full
2148-track / 80-via routing transfers cleanly -- we copy every track/via/zone
from main's routed qssc.kicad_pcb into qssc-grad.kicad_pcb, remapping nets by
NAME (netcodes differ between the two boards).  After this, qssc-grad has the
complete base routing + the 7 unrouted grad nets on top; a FreeRouting
push-and-shove pass then routes the 7 new nets and shoves any base track that
collides with a new grad pad.

Usage:
  python3 tools/inject_base_routing.py <main_routed.kicad_pcb> <qssc-grad.kicad_pcb>

Idempotent: strips any tracks/vias/zones already on the grad board first (the
generator leaves it at 0 tracks / 2 empty zone outlines), so re-running is
safe.
"""
from __future__ import annotations
import sys
import pcbnew
from _kicad_compat import patch_swig_iterators, track_width_nm, exit_cleanly

patch_swig_iterators()


def _netmap(board):
    """{netname: netcode} for a board."""
    m = {}
    for _code, net in board.GetNetInfo().NetsByNetcode().items():
        m[net.GetNetname()] = net.GetNetCode()
    return m


def _clear_tracks_vias_zones(board):
    """Strip every track, via, and zone so the inject is clean (idempotent).

    Capture BOTH lists before any removal: on KiCad 10.x the swig proxy layer
    stops handing out usable zone proxies after thousands of board.Remove()
    calls, so building the zone list AFTER the track loop crashes with
    'SwigPyObject is not iterable'.  Building both first keeps the proxies
    fresh.
    """
    ts = [t for t in board.Tracks()]
    zs = [z for z in board.Zones()]
    for t in ts:
        board.Remove(t)
    for z in zs:
        board.Remove(z)


def _copy_zone(dst_board, src_zone, netcode):
    """Clone a zone's outline + properties onto dst_board with a remapped net."""
    z = pcbnew.ZONE(dst_board)
    z.SetLayer(src_zone.GetFirstLayer())
    z.SetNetCode(netcode)
    z.SetAssignedPriority(src_zone.GetAssignedPriority())
    try:
        z.SetLocalClearance(src_zone.GetLocalClearance())
    except Exception:
        pass
    try:
        z.SetPadConnection(src_zone.GetPadConnection())
    except Exception:
        pass
    # copy the main outline corners (SHAPE_POLY_SET -> SHAPE_LINE_CHAIN)
    poly = src_zone.Outline()
    if poly is not None and poly.OutlineCount() > 0:
        chain = poly.GetOutline(0)
        npt = chain.PointCount()
        # drop the duplicated closing point if present (KiCad repeats point 0)
        last = None
        for i in range(npt):
            pt = chain.GetPoint(i)
            key = (int(pt.x), int(pt.y))
            if key == last:
                continue
            last = key
            z.AppendCorner(pcbnew.VECTOR2I(int(pt.x), int(pt.y)), -1)
    dst_board.Add(z)
    return z


def _extract(main):
    """Snapshot every track/via on ``main`` into plain-python tuples BEFORE
    any board mutation.  On KiCad 10.x the swig proxy layer corrupts after
    thousands of board.Remove() calls, so we cannot touch a SWIG container
    (main.Tracks(), a pad proxy, a layer-set proxy) once mutation has begun.
    Extracting to ints/strs up front sidesteps that entirely.
    """
    out = []
    for t in main.Tracks():
        nm = t.GetNetname()
        if isinstance(t, pcbnew.PCB_VIA):
            p = t.GetPosition()
            try:
                vt = int(t.GetViaType())
            except Exception:
                vt = int(pcbnew.VIATYPE_THROUGH)
            # capture the FULL layer set (CuStack -> list of copper layer IDs
            # in stack order).  LSET has no TopLayer/BottomLayer on these
            # bindings, and the via type alone (MICROVIA/BLIND) does not encode
            # which layers it spans -- a microvia F.Cu->In1 must keep that
            # 2-layer set or it pierces In3/In4 and shorts every signal track.
            try:
                layers = tuple(int(i) for i in t.GetLayerSet().CuStack())
            except Exception:
                layers = ()
            out.append(("via", nm, int(p.x), int(p.y), int(t.GetDrill()),
                        int(track_width_nm(t)), vt, layers))
        else:
            s = t.GetStart(); e = t.GetEnd()
            out.append(("trk", nm, int(s.x), int(s.y), int(e.x), int(e.y),
                        int(t.GetLayer()), int(track_width_nm(t))))
    return out


def inject(main_path: str, grad_path: str) -> dict:
    main = pcbnew.LoadBoard(main_path)
    # extract main's copper to plain-python tuples BEFORE any mutation: the
    # swig proxy layer corrupts after the heavy _clear removals below.
    snap = _extract(main)
    del main  # drop the main board proxy; we only need the snapshot now

    grad = pcbnew.LoadBoard(grad_path)
    gmap = _netmap(grad)

    _clear_tracks_vias_zones(grad)

    n_trk = n_via = n_zone = n_skip = 0
    # zones: NOT copied here.  route_qssc_pcb.py rebuilds the GND/+3V3 planes
    # idempotently, carving anti-pads around the 7 new grad pads/vias too.
    # Copying main's filled zones would just be stripped and re-poured anyway.

    for item in snap:
        kind = item[0]
        nm = item[1]
        nc = gmap.get(nm)
        if nc is None:
            n_skip += 1
            continue
        if kind == "via":
            _, _nm, x, y, drill, w, vt, layers = item
            v = pcbnew.PCB_VIA(grad)
            v.SetPosition(pcbnew.VECTOR2I(x, y))
            v.SetNetCode(nc)
            v.SetViaType(vt)
            v.SetDrill(drill)
            v.SetWidth(w)
            # reconstruct the exact copper stack (microvia F.Cu->In1 stays a
            # 2-layer via; through vias keep all 6).  THROUGH vias default to
            # the full stack, so only set an explicit set when we captured a
            # partial one (blind/micro) -- layers is empty only if CuStack
            # failed, in which case the default full stack is the safe guess.
            if layers and vt != int(pcbnew.VIATYPE_THROUGH):
                try:
                    ls = pcbnew.LSET()
                    for lid in layers:
                        ls.AddLayer(int(lid))
                    v.SetLayerSet(ls)
                except Exception:
                    pass
            grad.Add(v)
            n_via += 1
        else:
            _, _nm, sx, sy, ex, ey, layer, w = item
            tr = pcbnew.PCB_TRACK(grad)
            tr.SetStart(pcbnew.VECTOR2I(sx, sy))
            tr.SetEnd(pcbnew.VECTOR2I(ex, ey))
            tr.SetLayer(layer)
            tr.SetWidth(w)
            tr.SetNetCode(nc)
            grad.Add(tr)
            n_trk += 1

    grad.SetModified()
    pcbnew.SaveBoard(grad_path, grad)
    return dict(tracks=n_trk, vias=n_via, zones=n_zone, skipped=n_skip)


if __name__ == "__main__":
    main_path = sys.argv[1]
    grad_path = sys.argv[2]
    st = inject(main_path, grad_path)
    print(f"injected into {grad_path}:")
    print(f"  tracks={st['tracks']}  vias={st['vias']}  zones={st['zones']}  skipped={st['skipped']}")
    # skip interpreter finalization: the swig proxy teardown segfaults after
    # adding thousands of tracks/vias (see _kicad_compat.exit_cleanly)
    exit_cleanly(0)