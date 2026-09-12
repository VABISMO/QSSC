#!/usr/bin/env python3
"""Pre-place a dogbone fanout via + F.Cu stub for a single pad, so the 3D maze
router can use it as an existing endpoint (it fails to find its own clear via
spot for dense-BGA balls once neighbouring grad nets are already routed).

Usage:
  env PYTHONPATH=$PWD/tools python3 tools/add_grad_fanout.py <board> <ref> <padname> <via_dx_mm> <via_dy_mm>
"""
from __future__ import annotations
import sys
import pcbnew
from _kicad_compat import patch_swig_iterators, exit_cleanly

patch_swig_iterators()
NM = 1_000_000
VIA_DIA = int(0.20 * NM)
VIA_DRILL = int(0.10 * NM)
TRK_W = int(0.20 * NM)
F_CU = pcbnew.F_Cu


def main(board_path, ref, padname, dx_mm, dy_mm):
    b = pcbnew.LoadBoard(board_path)
    target = None
    for m in b.GetFootprints():
        if m.GetReference() == ref:
            for p in m.Pads():
                if p.GetPadName() == padname:
                    target = (m, p)
                    break
    if target is None:
        print(f"pad {ref}.{padname} not found")
        return 1
    m, p = target
    pos = p.GetPosition()
    px, py = int(pos.x), int(pos.y)
    nc = p.GetNetCode()
    vx = px + int(dx_mm * NM)
    vy = py + int(dy_mm * NM)
    # F.Cu stub pad -> via
    tr = pcbnew.PCB_TRACK(b)
    tr.SetStart(pcbnew.VECTOR2I(px, py))
    tr.SetEnd(pcbnew.VECTOR2I(vx, vy))
    tr.SetLayer(F_CU); tr.SetWidth(TRK_W); tr.SetNetCode(nc)
    b.Add(tr)
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pcbnew.VECTOR2I(vx, vy))
    v.SetTopLayer(pcbnew.F_Cu); v.SetBottomLayer(pcbnew.B_Cu)
    v.SetDrill(VIA_DRILL); v.SetWidth(VIA_DIA); v.SetNetCode(nc)
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    b.Add(v)
    b.SetModified()
    pcbnew.SaveBoard(board_path, b)
    print(f"fanout {ref}.{padname} net={p.GetNetname()} via=({vx/NM:.2f},{vy/NM:.2f})")
    return 0


if __name__ == "__main__":
    rc = main(sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5]))
    exit_cleanly(rc)