#!/usr/bin/env python3
"""Sync every shared footprint's placement from the routed main board into grad.

The generator (gen_qssc_kicad.py) places components at deterministic default
positions; the main checkout's qssc.kicad_pcb has those parts hand-tuned during
the routing work on phase2-fpga-csg324-parallel.  inject_base_routing.py copies
the main board's TRACKS (at the main board's coordinates) into the grad board,
so the grad board's pads must sit at the SAME absolute coordinates or the
injected tracks land in empty space / cross the misplaced pads (the 27 baseline
violations: J1, UE0/UE1/UE2, J10, R/C/D parts all at different coords).

This script, for every footprint that exists in BOTH boards (matched by
reference), copies the footprint position + orientation and each pad's relative
position (Pos0) + size + drill + attribute + layer-set + shape from main into
grad, so the shared parts become placement-identical to the routed main board.
Grad-only footprints (Ugrad/Udrv/Qgrad/Rsense/Lgrad/C_grad1/C_grad2) are left
untouched.  Nets are left alone (matched by pad name; base netlist identical).

The main board is opened READ-ONLY (never written).

Usage:
  env PYTHONPATH=$PWD/tools python3 tools/sync_placement.py \
      /path/to/qssc.kicad_pcb  hardware/pcb/qssc-grad.kicad_pcb
"""
from __future__ import annotations
import sys
import pcbnew
from route_qssc_pcb import patch_swig_iterators

patch_swig_iterators()

# grad-only footprints the generator adds -- never synced (no main counterpart)
GRAD_ONLY = {"Ugrad", "Udrv", "Qgrad", "Rsense", "Lgrad", "C_grad1", "C_grad2"}


def _fpads(m):
    return {p.GetPadName(): p for p in m.Pads()}


def sync(main_path: str, grad_path: str) -> dict:
    mb = pcbnew.LoadBoard(main_path)
    gb = pcbnew.LoadBoard(grad_path)
    mfps = {m.GetReference(): m for m in mb.GetFootprints()}

    synced = skipped = 0
    pads_synced = pads_missing = 0
    for g in gb.GetFootprints():
        ref = g.GetReference()
        if ref in GRAD_ONLY or ref not in mfps:
            skipped += 1
            continue
        src = mfps[ref]
        # footprint transform first (pads are children and follow it)
        g.SetPosition(src.GetPosition())
        g.SetOrientation(src.GetOrientation())
        # pad geometry (Pos0 = relative to footprint, so absolute = fp + rotate)
        # match pads by name; blank-named pads (e.g. Uchip's 4 NPTH mounting
        # holes) collide under name matching (all map to one), so match those
        # by ordinal index within the blank-name subsequence instead.
        src_pads = list(src.Pads())
        grad_pads = list(g.Pads())
        sp = _fpads(src)
        src_blank = [p for p in src_pads if p.GetPadName() == ""]
        blank_idx = 0
        for p in grad_pads:
            nm = p.GetPadName()
            if nm == "":
                # blank-named pads (NPTH mounting holes): name matching collapses
                # them (sp[""] is the last src blank pad), so match by ordinal
                # index within the blank subsequence instead.
                s = src_blank[blank_idx] if blank_idx < len(src_blank) else None
                blank_idx += 1
            else:
                s = sp.get(nm)
            if s is None:
                pads_missing += 1
                continue
            # absolute pad position; SetPosition converts to relative via the
            # footprint transform (already set above), so this stores the same
            # relative offset as the main pad.
            pos = s.GetPosition()
            p.SetPosition(pcbnew.VECTOR2I(int(pos.x), int(pos.y)))
            sz = s.GetSize()
            p.SetSize(pcbnew.VECTOR2I(int(sz.x), int(sz.y)))
            dr = s.GetDrillSize()
            p.SetDrillSize(pcbnew.VECTOR2I(int(dr.x), int(dr.y)))
            p.SetAttribute(s.GetAttribute())
            p.SetLayerSet(s.GetLayerSet())
            p.SetShape(s.GetShape())
            pads_synced += 1
        synced += 1

    gb.SetModified()
    pcbnew.SaveBoard(grad_path, gb)
    return dict(synced=synced, skipped=skipped,
                pads_synced=pads_synced, pads_missing=pads_missing)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: sync_placement.py <main_board> <grad_board>")
    st = sync(sys.argv[1], sys.argv[2])
    print(f"sync_placement -> {sys.argv[2]}:")
    print(f"  footprints synced: {st['synced']}  (skipped grad-only/absent: {st['skipped']})")
    print(f"  pads synced: {st['pads_synced']}  (missing in main: {st['pads_missing']})")