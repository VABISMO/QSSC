#!/usr/bin/env python3
"""Copy every zone OUTLINE from main's routed board onto grad (remapped by net
name), then save.  Run AFTER inject_base_routing.py (which copies tracks/vias
but deliberately skips zones).  Pour them afterward with fill_zones.py.

inject_base_routing skips zones because route_qssc_pcb.py was meant to rebuild
them -- but that rebuild also re-stitches 297 power vias and re-traces rails
that conflict with the injected signal tracks.  Copying main's own zone
outlines instead keeps the injected routing intact; only the copper fill is
re-derived (anti-pads carved around the injected + new grad vias).

Usage:
  env PYTHONPATH=$PWD/tools python3 tools/copy_zones.py <main.kicad_pcb> <grad.kicad_pcb>
"""
from __future__ import annotations
import sys
import pcbnew
from _kicad_compat import patch_swig_iterators, exit_cleanly

patch_swig_iterators()


def _netmap(board):
    m = {}
    for _code, net in board.GetNetInfo().NetsByNetcode().items():
        m[net.GetNetname()] = net.GetNetCode()
    return m


def _copy_zone(dst, src_zone, netcode):
    z = pcbnew.ZONE(dst)
    z.SetLayer(src_zone.GetFirstLayer())
    z.SetNetCode(netcode)
    try:
        z.SetAssignedPriority(src_zone.GetAssignedPriority())
    except Exception:
        pass
    try:
        z.SetLocalClearance(src_zone.GetLocalClearance())
    except Exception:
        pass
    try:
        z.SetPadConnection(src_zone.GetPadConnection())
    except Exception:
        pass
    poly = src_zone.Outline()
    if poly is not None and poly.OutlineCount() > 0:
        chain = poly.Outline(0)
        last = None
        for i in range(chain.PointCount()):
            pt = chain.GetPoint(i)
            key = (int(pt.x), int(pt.y))
            if key == last:
                continue
            last = key
            z.AppendCorner(pcbnew.VECTOR2I(int(pt.x), int(pt.y)), -1)
    dst.Add(z)
    return z


def copy_zones(main_path: str, grad_path: str) -> int:
    main = pcbnew.LoadBoard(main_path)
    gmap = _netmap(pcbnew.LoadBoard(grad_path))  # fresh load for netcodes
    grad = pcbnew.LoadBoard(grad_path)
    n = 0
    for sz in main.Zones():
        nm = sz.GetNetname()
        nc = gmap.get(nm)
        if nc is None:
            continue
        _copy_zone(grad, sz, nc)
        n += 1
    grad.SetModified()
    pcbnew.SaveBoard(grad_path, grad)
    return n


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: copy_zones.py <main_board> <grad_board>")
    n = copy_zones(sys.argv[1], sys.argv[2])
    print(f"copy_zones -> {sys.argv[2]}: {n} zone outlines copied")
    exit_cleanly(0)