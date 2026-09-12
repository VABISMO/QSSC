#!/usr/bin/env python3
"""Fix qssc-grad Uchip pad stackup to match the routed main board.

The generator (gen_qssc_kicad.py) emits the Uchip die pads as 1.7 mm PTH
(copper on all 6 layers, 1.0 mm drill).  That is wrong for the two-faced
no-TSV die: every pad is a SURFACE pad -- 64 EM_* cathode pads on F.Cu only,
a few EM_ANODE / DET_COMMON through-pads on F.Cu+B.Cu -- and must NOT pierce
the inner planes.  The main checkout's qssc.kicad_pcb has the correct stackup
(1.2 mm SMD, drill 0, F.Cu-only layer set) because it was fixed during the
routing work; the freshly-regenerated grad board inherits the generator's
wrong PTH pads.

With PTH pads the annular rings on In1/In2/In3/In4 short to every base track
on those layers (94 shorting_items) and the 1.0 mm drill holes trip
hole_clearance.  This script copies, per pad name, the geometry + layer set +
drill + attribute from the main board's Uchip into the grad board's Uchip so
the grad board's chip pads are byte-identical to the routed main board's.

The main board is opened READ-ONLY (never written).  Nets are left untouched
(matched by pad name; the base netlist is identical between the two boards).

Usage:
  env PYTHONPATH=$PWD/tools python3 tools/fix_uchip_pads.py \
      /path/to/qssc.kicad_pcb  hardware/pcb/qssc-grad.kicad_pcb
"""
from __future__ import annotations
import sys
import pcbnew
from route_qssc_pcb import patch_swig_iterators

patch_swig_iterators()


def _uchip(board) -> object:
    for m in board.GetFootprints():
        if m.GetReference() == "Uchip":
            return m
    raise KeyError("no Uchip footprint")


def fix(main_path: str, grad_path: str) -> dict:
    mb = pcbnew.LoadBoard(main_path)        # read-only source
    gb = pcbnew.LoadBoard(grad_path)        # board to fix
    mp = {p.GetPadName(): p for p in _uchip(mb).Pads()}
    gf = _uchip(gb)

    fixed = skipped = 0
    for p in gf.Pads():
        src = mp.get(p.GetPadName())
        if src is None:
            skipped += 1
            continue
        pos = src.GetPosition()
        p.SetPosition(pcbnew.VECTOR2I(int(pos.x), int(pos.y)))
        sz = src.GetSize()
        p.SetSize(pcbnew.VECTOR2I(int(sz.x), int(sz.y)))
        dr = src.GetDrillSize()
        p.SetDrillSize(pcbnew.VECTOR2I(int(dr.x), int(dr.y)))
        p.SetAttribute(src.GetAttribute())      # SMD (1) vs PTH (0)
        p.SetLayerSet(src.GetLayerSet())        # the copper-layer mask
        p.SetShape(src.GetShape())
        fixed += 1

    gb.SetModified()
    pcbnew.SaveBoard(grad_path, gb)
    return dict(fixed=fixed, skipped=skipped, total=len(gf.Pads()))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: fix_uchip_pads.py <main_board> <grad_board>")
    st = fix(sys.argv[1], sys.argv[2])
    print(f"fix_uchip_pads -> {sys.argv[2]}:")
    print(f"  pads fixed: {st['fixed']} / {st['total']}  (skipped {st['skipped']})")