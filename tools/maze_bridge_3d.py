#!/usr/bin/env python3
"""3D (multi-layer) A* maze bridge for residual split nets on the head board.

Routes a single named net across the 4 signal layers (B.Cu, In3, In4, F.Cu) with
through-hole via transitions at clear cells. Routes ONLY the named net -- it does
NOT re-fanout or disturb any other net (unlike FreeRouting, which re-fans-out
every SMD pin). Endpoint vias are placed by this script (spiral clear-spot
search, deferred until a path is confirmed).

The single-layer head_maze_bridge.py could not close the 106 mm DET_IN10 net on
any one layer (via field fully blocks In4; In3 path genuinely breaches 0.17 mm;
B.Cu blocked by J12). A 3D search that hops layers around the congestion is the
principled closure.

Non-destructive: writes <board>.maze3d.kicad_pcb. DRC the result to validate.

Usage: python3 tools/maze_bridge_3d.py <board.kicad_pcb> [net1,net2,...] [--keep-power[=NET,..]]

By default every net in tc.PLANE_NETS is skipped (planes are poured, not routed).
--keep-power lets power-plane nets through the maze instead of treating them as
already-poured planes -- used by the gradient-variant pipeline where +5V is a
poured plane on the base board but must also reach the gradient coil as a routed
spur on qssc-grad. With no `=NET,..` list, keeps +5V (the grad-coil supply).

This module is now a thin orchestrator: the routing logic lives on
:class:`tools.router.maze.MazeBridge3D` (board + env-config + 3-D grid + per-net
index, replacing the former ``b`` / ``KEEP_POWER`` / ``by_net`` module globals),
and the pure 3-D A* search + path simplification are on
:mod:`tools.router.astar3d`. ``main`` here loads the board, builds the per-net
index, and runs the per-net maze-bridge loop, checkpoint-saving every 16 routed
nets.
"""
from __future__ import annotations
import argparse
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
import pcbnew
import test_pcb_connectivity as tc
from _kicad_compat import patch_swig_iterators
from router.maze import MazeBridge3D

patch_swig_iterators()


def main():
    """Parse args, load the board, then maze-bridge split nets via MazeBridge3D."""
    ap = argparse.ArgumentParser(
        description="3D (multi-layer) A* maze bridge for residual split nets.")
    ap.add_argument("board", type=Path, help="<board>.kicad_pcb to maze-bridge")
    ap.add_argument("nets", nargs="?", default=None,
                    help="comma-separated net names to bridge (default: all non-plane nets)")
    ap.add_argument("--keep-power", nargs="?", const="+5V", default=None,
                    metavar="NET,..",
                    help="let these plane nets through the maze instead of skipping them "
                         "(bare flag keeps +5V, the gradient-coil supply)")
    args = ap.parse_args()

    bp = args.board.resolve()
    only = set(args.nets.split(",")) if args.nets else None
    if args.keep_power is not None and args.keep_power.strip():
        keep_power = {n.strip() for n in args.keep_power.split(",") if n.strip()}
    elif args.keep_power is not None:
        keep_power = {"+5V"}
    else:
        keep_power = set()

    board = pcbnew.LoadBoard(str(bp))
    mb = MazeBridge3D(board, keep_power)
    mb.build_index()

    n_nets = len(mb.by_net)
    _maze_out = bp.with_suffix(".maze3d.kicad_pcb")
    report = []
    for idx, (nc, _items) in enumerate(mb.by_net.items(), 1):
        nm = mb.netnames.get(nc, "")
        if not nm or (nm in tc.PLANE_NETS and nm not in mb.KEEP_POWER):
            continue
        if only and nm not in only:
            continue
        rep, log = mb._bridge_net(nc, nm, idx, n_nets)
        for line in log:
            print(line, flush=True)
        # single-cluster nets return rep=None (already connected -- no summary
        # entry, matching the original which `continue`d before report.append).
        if rep is not None:
            report.append(rep)
            # checkpoint every 16 ROUTED nets: a crash/kill never loses more than
            # 16. Skipped nets (rep[1] is None) fall through without checkpoint,
            # matching the original; the final save below captures everything.
            if rep[1] is not None and idx % 16 == 0:
                board.Save(str(_maze_out))
                print(f"   [checkpoint {idx}/{n_nets} -> {_maze_out.name}]", flush=True)

    out = _maze_out
    board.Save(str(out))
    print(f"== 3D maze-bridged -> {out.name} ==")
    ok = 0
    for nm, plen, x1, y1, x2, y2, reason in report:
        if plen is None:
            print(f"  {nm}: SKIP ({reason})  ({x1/mb.NM:.1f},{y1/mb.NM:.1f})->({x2/mb.NM:.1f},{y2/mb.NM:.1f})")
        else:
            print(f"  {nm}: 3D path {plen:.1f}mm {reason}  ({x1/mb.NM:.1f},{y1/mb.NM:.1f})->({x2/mb.NM:.1f},{y2/mb.NM:.1f})"); ok += 1
    print(f"bridged {ok}/{len(report)}; saved {out}")
    print("NEXT: refill + DRC the .maze3d board in a project context.")
    print("DONE")


if __name__ == "__main__":
    main()