#!/usr/bin/env python3
"""Remove every track/via on a named net (idempotent clear for re-routing).

Usage:
  env PYTHONPATH=$PWD/tools python3 tools/clear_net.py <board> <net1,net2,...>
"""
from __future__ import annotations
import sys
import pcbnew
from _kicad_compat import patch_swig_iterators, exit_cleanly

patch_swig_iterators()


def clear(board_path, nets):
    b = pcbnew.LoadBoard(board_path)
    nets = set(nets)
    gone = 0
    for t in [t for t in b.Tracks()]:
        if t.GetNetname() in nets:
            b.Remove(t)
            gone += 1
    b.SetModified()
    pcbnew.SaveBoard(board_path, b)
    return gone


if __name__ == "__main__":
    n = clear(sys.argv[1], sys.argv[2].split(","))
    print(f"clear_net -> {sys.argv[1]}: removed {n} tracks/vias on {sys.argv[2]}")
    exit_cleanly(0)