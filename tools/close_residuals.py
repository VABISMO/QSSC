#!/usr/bin/env python3
"""Close residual split/dangling nets with direct Manhattan tracks (no autorouter).

For each named net (or every non-plane split net when none are named), cluster its
pads+tracks+vias by copper connectivity; for every pair of unconnected clusters,
draw an L- or Z-shaped track on a shared SIGNAL layer between the closest pad
pair, clearance-checked against all other-net copper at 0.17 mm. Falls back to a
through-via + inner-layer hop when no same-layer Manhattan path clears.

This is the right tool for the residuals FreeRouting leaves when its optimizer
stalls: short 2- or 3-pin SMD nets (ADF4351 control pins, TIA feedback pairs, USB
config-channel) that need a plain same-layer track, NOT the via-based 3D A* of
maze_bridge_3d (which forces an endpoint via and reports "no clear via spot" on
dense QFN fans where there is no room for a 0.40 mm via but a 0.20 mm same-layer
track fits easily). Run AFTER FreeRouting + maze_bridge_3d; re-pour planes after.

Non-destructive: writes <board>.closed.kicad_pcb.  DRC + connectivity-check it,
then promote.

Usage: python3 tools/close_residuals.py <board.kicad_pcb> [net1,net2,...]
       python3 tools/close_residuals.py <board.kicad_pcb>            # all split nets
System python3 (pcbnew).  No .venv.

This module is now a thin orchestrator: the routing logic lives on
:class:`tools.router.manhattan.ResidualCloser` (board + config + obstacle index,
replacing the former module globals), and the pure 2-D grid primitives (A*,
rasterisation, path simplification) are on :class:`tools.router.grid2d.Grid2D`.
``main`` here loads the board, selects target nets, and runs the per-net
SIGALRM-bounded close loop.
"""
from __future__ import annotations
import argparse, os, signal
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
import pcbnew
import test_pcb_connectivity as tc
from _kicad_compat import patch_swig_iterators
from router.manhattan import ResidualCloser

patch_swig_iterators()


def main():
    """Parse args, load the board, then close residual nets via ResidualCloser."""
    ap = argparse.ArgumentParser(
        description="Close residual split/dangling nets with direct Manhattan tracks.")
    ap.add_argument("board", type=Path, help="<board>.kicad_pcb to close nets on")
    ap.add_argument("--only", default=None,
                    help="comma-separated net names to close (default: all non-plane split nets)")
    args = ap.parse_args()

    bp = args.board
    only = None
    if args.only is not None and args.only.strip():
        only = sorted({s.strip() for s in args.only.split(",") if s.strip()})

    board = pcbnew.LoadBoard(str(bp))
    closer = ResidualCloser(board)

    if only is None:
        # all non-plane split nets
        split = tc._split_nets(bp)
        targets = [nm for nm, cnt in split.items() if cnt > 1]
    else:
        targets = sorted(only)

    print(f"== close_residuals: {bp.name} ==  {len(targets)} target net(s)")
    out = bp.with_suffix(".closed.kicad_pcb")

    # Per-net SIGALRM timeout: a pathological net (huge _collapse_segs O(N^2 *
    # _trk_clear), or a 60k-node A* that won't bail) can hang the whole run for
    # many minutes, and the board only saves at the END -- so one stuck net
    # loses ALL the closures already made. The alarm interrupts the stuck net's
    # pure-Python loop (heap / collapse / clearance checks) so no single net
    # exceeds NET_TIMEOUT; we log + skip it and carry on. Checkpoint-save every
    # CHK nets so a crash/kill mid-run keeps the closures already committed.
    class _NetTimeout(Exception):
        pass

    def _alarm(signum, frame):
        raise _NetTimeout

    signal.signal(signal.SIGALRM, _alarm)
    NET_TIMEOUT = int(os.environ.get("NET_TIMEOUT", "45"))
    CHK = int(os.environ.get("CHECKPOINT", "10"))

    total_segs = 0; total_vias = 0
    for idx, nm in enumerate(targets, 1):
        n = board.FindNet(nm)
        if n is None or n.GetNetCode() < 0:
            print(f"  {nm}: NO NET"); continue
        # Plane nets are no longer skipped unconditionally: ResidualCloser now
        # clusters against the filled zone regions as well (see the zone-aware
        # step in router.manhattan._cluster), so an explicit --only GND/+3V3
        # targets exactly the fragments the pour failed to reach instead of
        # flooding pairs the pour already connects. The default target selection
        # (tc._split_nets) still excludes plane nets, so the bare invocation
        # behaves as before.
        try:
            signal.alarm(NET_TIMEOUT)
            sg, vi, log = closer._connect_net(n.GetNetCode(), nm)
            signal.alarm(0)
            total_segs += sg; total_vias += vi
            for line in log:
                print(line)
        except _NetTimeout:
            signal.alarm(0)
            print(f"  {nm}: TIMEOUT >{NET_TIMEOUT}s (skipped)")
        except Exception as e:  # noqa: BLE001 -- never let one net kill the run
            signal.alarm(0)
            print(f"  {nm}: ERROR {type(e).__name__}: {e}")
        if idx % CHK == 0:
            board.Save(str(out))
            print(f"  [checkpoint {idx}/{len(targets)}: saved -> {out.name}]")

    board.Save(str(out))
    print(f"== added {total_segs} track seg(s) + {total_vias} via(s) -> {out.name} ==")
    print("NEXT: re-pour planes + DRC + connectivity-check the .closed board.")


if __name__ == "__main__":
    main()