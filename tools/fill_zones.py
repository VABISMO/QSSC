#!/usr/bin/env python3
"""Re-pour every zone on a board (fresh-process zone fill).

ZONE_FILLER.Fill is unsafe in the same process that created tracks/vias via
board.Add() on KiCad 10.x (segfault); this script reloads the board in a clean
process, fills all zones, and saves. Run AFTER route_grad_nets.py so the new
through vias get anti-pads carved in the GND/+3V3 planes.

Usage:
  env PYTHONPATH=$PWD/tools python3 tools/fill_zones.py <board.kicad_pcb>
"""
from __future__ import annotations
import sys
import pcbnew
from _kicad_compat import patch_swig_iterators, fill_all_zones, exit_cleanly

patch_swig_iterators()


def fill(board_path: str) -> int:
    board = pcbnew.LoadBoard(board_path)
    zones = fill_all_zones(board)
    board.SetModified()
    pcbnew.SaveBoard(board_path, board)
    return len(zones)


if __name__ == "__main__":
    bp = sys.argv[1]
    n = fill(bp)
    print(f"fill_zones -> {bp}: {n} zones poured")
    exit_cleanly(0)