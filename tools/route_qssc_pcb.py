#!/usr/bin/env python3
"""Headless copper-plane foundation for the QSSC PCB (``hardware/pcb/qssc.kicad_pcb``).

This is NOT an autorouter. A quality DRC-clean autoroute of a 65-net board with
a 237-pad FPGA BGA cannot be produced headlessly: FreeRouting/topola are not
installed, and writing one from scratch in a script would leave DRC violations
(a "routed" board that fails DRC is *worse* than an unrouted one). The
FPGA's 120 power/ground balls ARE netted (79 GND, 30 +3V3, 3 +1V8, 8 +1V0) and
reach their planes via via-in-pad microvias placed by route_signals._bga_fanout;
the remaining ~116 balls are named signal / unused-IO pads from the XDC.

What this DOES do -- the copper-plane foundation for the 6-layer stackup
(F.Cu sig / In1.Cu GND / In2.Cu +3V3 (PWR) / In3.Cu sig / In4.Cu sig / B.Cu sig),
reproducibly and DRC-clean (copper pours auto-avoid pads of other nets):

  * **In1.Cu : solid GND plane** (full board) -- continuous ground reference.
  * **In2.Cu : solid +3V3 plane** (full board) -- the main digital rail
    (detected by the "PWR" layer label; the chip module also puts it on In2.Cu).
  * **Rail pad vias** -- every surface GND / +3V3 SMD pad (not on the BGA) gets
    an F.Cu tie trace to a through-via (or via-in-pad for the large QFN/LFCSP
    centre exposed-pads), so the pad actually reaches its inner plane.
  * **GND stitching vias** on a coarse grid, each pad-clearance-checked, as
    distributed signal-integrity return vias tying the plane to the surfaces.

BGA via-in-pad (GND/+3V3/signal/power) and ALL signal + non-plane power routing
are owned by ``tools/route_signals.py`` (run it FIRST). This script preserves
the small BGA VIPs and all signal copper when (re)building the planes.

This module is now a thin shim: the plane-builder logic lives in
:class:`tools.router.planes.PlaneBuilder` (rail-pad + straggler helpers in
:mod:`tools.router.rails`, shared geometry in :mod:`tools.geom`). It keeps the
layer IDs, via/track sizes and ``track_width_nm`` here because the whole
``tools.router`` package imports them from this module (preserving the existing
import direction and avoiding a cycle), and re-exports ``build`` / ``main`` /
``_fill_only`` so legacy callers and subprocess tests keep working.

Idempotent: it first strips any zones and its own wide GND/+3V3 vias AND tie
traces (kept: the small BGA VIPs and signal copper from route_signals), then
rebuilds, so re-running on an already-processed board is safe. It expects the
board produced by ``tools/gen_qssc_kicad.py`` (run that first if the board was
hand-edited).

Run:  python3 tools/route_qssc_pcb.py     (needs system python3 with pcbnew)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pcbnew
from _kicad_compat import patch_swig_iterators, track_width_nm, exit_cleanly  # noqa: F401 (track_width_nm re-exported for router.fanout)

patch_swig_iterators()

PCB_PATH = Path(
    os.environ.get(
        "QSSC_PCB_PATH",
        Path(__file__).resolve().parent.parent / "hardware" / "pcb" / "qssc.kicad_pcb",
    )
)

# layer ids. Copper indices are NOT fixed across KiCad releases: KiCad 8/9
# number F.Cu=0, In1.Cu=1 ... B.Cu=31, while KiCad 10 interleaves tech layers
# with copper (F.Cu=0, B.Cu=2, In1.Cu=4, In2.Cu=6, ...). Always derive them
# from the binding so the tools run on both. The GND and +3V3 *plane* layers
# are in addition detected dynamically from the board's layer labels (In1.Cu
# is labelled "GND", the +3V3 plane is labelled "PWR" -- In2.Cu on BOTH the
# main board and the chip module, post-HDI-pivot), so this script works for
# either 6-layer stackup.
F_CU = pcbnew.F_Cu
IN1_CU = pcbnew.In1_Cu      # GND plane (label "GND")
IN2_CU = pcbnew.In2_Cu      # +3V3 plane (label "PWR") on both boards (HDI: adjacent to F.Cu)
IN3_CU = pcbnew.In3_Cu      # signal
IN4_CU = pcbnew.In4_Cu      # signal
B_CU = pcbnew.B_Cu

GRID_STEP_NM = 10_000_000          # 10 mm stitching grid
VIA_DIAM_NM = 600_000              # 0.6 mm stitching / rail-pad via
VIA_DRILL_NM = 300_000             # 0.3 mm drill
# COPPER_GAP is the minimum copper-to-copper gap this script enforces between
# its own copper (via land / F.Cu tie trace) and any OTHER-net copper. The Power
# netclass DRC rule needs 0.15 mm; 0.16 mm keeps 0.01 mm of margin over it while
# NOT over-blocking dense 0.5 mm-pitch pads (the earlier 0.5 mm "min_clear"
# centre-to-centre check, sized for a 0.3 mm via against a circle of the pad's
# half-diagonal, falsely blocked a via that clears a 0.25 mm pad's narrow side
# by > 0.4 mm). With the rectangle-accurate pad model below, a 0.16 mm gap is the
# real, tight, DRC-safe clearance.
COPPER_GAP_NM = 160_000             # 0.16 mm copper-to-copper (Power rule 0.15)
# Through-via-in-pad VIPs placed by route_signals._bga_fanout are 0.20 mm; this
# script's own vias are 0.6 mm. _clear_routed strips only its own (wide) GND /
# +3V3 vias and preserves the small BGA VIPs (owned by route_signals).
VIP_MAX_NM = 300_000
RAIL_VIA_OFFSET_NM = 600_000       # 0.6 mm offset from a rail pad to its via
BGA_REF = "U1"                     # the FPGA BGA (skip its pads for rail-pad vias)
# A pad with both copper sides >= VIP_MIN_NM gets a via-in-pad at its centre:
# the only way to tie a QFN/LFCSP centre exposed-pad (3.5-5 mm of ground copper
# ringed by a dense pin field an offset via can never clear). Capped at 1.2 mm
# so component terminal pads (decoupling caps ~0.9 mm, where a centred via would
# wick solder) instead take the offset-spiral path.
VIP_MIN_NM = 1_200_000             # 1.2 mm -- via-in-pad only for large pads
# Drill-to-drill spacing: a new via's centre must be at least this far from any
# surviving via's centre so the two drills keep the board's min_hole_to_hole
# (0.25 mm edge-to-edge). New drill is 0.3 mm; worst-case surviving drill is
# 0.3 mm, so 0.25 + 0.3 = 0.55 mm centre would just meet 0.25 edge -- add 0.05
# mm margin for the 0.2495 mm DRC threshold. Also keeps two of THIS script's
# own rail vias (e.g. two pads sharing one centre, or adjacent pads fanned out
# toward each other) from co-locating.
DRILL_MIN_CENTER_NM = 600_000      # 0.6 mm min centre-to-centre between vias
RAIL_TRACK_NM = 400_000            # 0.4 mm F.Cu tie trace (pad-centre -> via)
# Dense SMT connector leads (0.3 mm wide on a 0.5 mm pitch -- Samtec LSHM-130)
# cannot host a 0.4 mm tie trace: the adjacent-row lead 0.5 mm away leaves only
# 0.5 - 0.2 (0.4 mm trace half) - 0.15 (lead half) = 0.15 mm < the 0.16 mm
# COPPER_GAP. A 0.2 mm trace (0.1 mm half) clears by 0.25 mm. The board's global
# min_track_width is 0.15 mm so 0.2 mm passes the width DRC. Idempotent because
# _clear_routed strips EVERY GND/+3V3 PCB_TRACK (route_signals routes none).
RAIL_TRACK_THIN_NM = 200_000       # 0.2 mm tie trace for <=0.3 mm-wide pads
RAIL_TRACK_THIN_MAX = 300_000       # pad narrow side <= this -> use the thin trace


def build(board, gnd, p33, bbox, pads, fill_zones=True) -> dict:
    """Add the copper-plane foundation to an ALREADY-LOADED board and return a
    stats dict. Does NOT load or save -- callers (main / tests) own the board
    lifetime. Split out so tests can run it repeatedly on one board without
    re-loading (KiCad's python binding can hand back bare SwigPyObjects for a
    board re-loaded after a SaveBoard/mutation, which breaks repeated runs).

    ``fill_zones=False`` skips the inline zone fill: on KiCad 10.x,
    ``ZONE_FILLER.Fill`` segfaults in a process that just added hundreds of
    tracks/vias via the swig layer, so ``main`` fills in a fresh ``--fill``
    subprocess after saving. Direct ``build`` callers that never mutate the
    board afterwards can keep ``fill_zones=True`` (works on every version)."""
    # Deferred import: router.planes imports this module for its constants, so a
    # top-level import would cycle. By the time build() runs this module is fully
    # initialized (constants defined), so the cycle resolves cleanly.
    from router.planes import PlaneBuilder

    return PlaneBuilder(board, gnd, p33, bbox, pads).run(fill_zones)


def main() -> dict:
    """Run the copper-plane foundation on the board at PCB_PATH, save it, and
    return a stats dict. Loads + saves the board; tests should prefer calling
    ``build`` on a board they load once (see module docstring)."""
    board = pcbnew.LoadBoard(str(PCB_PATH))
    # Capture everything we read from the board BEFORE any mutation: KiCad's
    # python binding returns raw SwigPyObjects (missing their python proxy
    # methods like GetX/GetCenter) for values fetched AFTER the board is
    # mutated, so read netcodes + the edge bbox up front.
    from router.planes import _find_netcode, _pad_obstacle

    gnd = _find_netcode(board, "GND")
    try:
        p33 = _find_netcode(board, "+3V3")
    except KeyError:
        p33 = None
    bbox = board.GetBoardEdgesBoundingBox()
    pads = _pad_obstacle(board)       # collect pad obstacles before mutation

    stats = build(board, gnd, p33, bbox, pads, fill_zones=False)

    board.SetModified()
    pcbnew.SaveBoard(str(PCB_PATH), board)

    # Zone fill happens in a FRESH process: in this process the swig layer
    # handed back unusable proxies after the ~700 board.Add() mutations above,
    # and ZONE_FILLER.Fill on such a mutated board segfaults (KiCad 10.0.6;
    # see _kicad_compat.fill_all_zones). Re-loading and filling there works on
    # every binding. Called as a function (from tests), the fill step is the
    # caller's: import _kicad_compat and run fill_all_zones on a fresh load.
    if __name__ == "__main__":
        fill_rc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--fill",
             str(PCB_PATH)],
        )
        if fill_rc.returncode != 0:
            raise SystemExit(f"zone fill subprocess failed: {fill_rc.returncode}")

    if __name__ != "__main__":      # called as a function (e.g. from tests)
        return stats
    # called as a script: print a human summary
    pwr = "+3V3" if p33 else "(none)"
    print(f"  zones: {len(stats['zones'])} (GND plane + {pwr} plane)")
    print(f"  rail + stitching vias: {stats['via_count']}")
    for z in stats["zones"]:
        if z["filled_mm2"]:
            print(f"    zone {z['net']:>6} on {z['layer']:>6}: "
                  f"{z['filled_mm2']:.1f} mm^2 filled")
        else:
            # fill_zones was False (fill ran in the --fill subprocess, whose
            # output above shows the real areas); GetFilledArea() is 0 here.
            print(f"    zone {z['net']:>6} on {z['layer']:>6}: placed "
                  f"(filled by the --fill subprocess)")
    print(f"  saved {PCB_PATH}")
    return stats


def _fill_only(pcb_path) -> int:
    """``--fill`` entry point: load a saved board and fill all its zones.

    Runs in its own process so the filler sees freshly-minted swig proxies
    (zone proxies from a heavily-mutated process are bare SwigPyObjects and
    the C++ filler segfaults on that state, KiCad 10.0.6). Works identically
    on older bindings, where an inline fill would also have been fine.
    """
    from _kicad_compat import fill_all_zones
    board = pcbnew.LoadBoard(str(pcb_path))
    zones = fill_all_zones(board)
    board.SetModified()
    pcbnew.SaveBoard(str(pcb_path), board)
    for z in zones:
        print(f"  filled {z.GetNetname():>6} on "
              f"{board.GetLayerName(z.GetFirstLayer()):>6}: "
              f"{z.GetFilledArea() / 1e12:.1f} mm^2")
    print(f"  saved {pcb_path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--fill":
        _rc = _fill_only(sys.argv[2] if len(sys.argv) > 2 else PCB_PATH)
        exit_cleanly(_rc)               # teardown GC is unsafe (see compat)
    main()