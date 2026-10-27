#!/usr/bin/env python3
"""Headless, RESOURCE-LIGHT SIGNAL router for the QSSC PCB.

Uses ONLY the KiCad pcbnew Python API (no external program, no GUI, no windows).

This module is now a thin shim over the :mod:`tools.router` package (split for
readability): ``tools.router.config`` (layer sets, grid/via sizes, net
classifiers), ``tools.router.grid`` (pad/segment cell primitives), ``tools.router.pair_router``
(the L/Z line router), ``tools.router.power`` (power-rail MST + RF fence),
``tools.router.fanout`` (BGA via-in-pad + chip dog-bone fanout), and
:class:`tools.router.PcbRouter` (one routing pass). It re-exports the package
surface so legacy callers keep working:

* ``import route_signals as R`` in the tests (``R._capture_pads``,
  ``R._apply_hdi_rules``, ``R._clear_tracks``, ``R._bga_fanout``).
* ``python tools/route_signals.py`` as a subprocess (prints ``routed N / M``).

Run (HARD-capped so it cannot freeze the host):

  systemd-run --user --scope -p MemoryMax=300M -p CPUQuota=40% \\
      env PYTHONPATH=$PWD/tools python3 tools/route_signals.py
"""
from __future__ import annotations

import gc
import sys
from pathlib import Path

# KiCad 9's SWIG bindings register no destructors for the C++ proxies
# (see the "no destructor found" warnings) and gc.collect() while the pair
# router holds live VECTOR2/track proxies segfaults ("Fatal Python error ...
# Garbage-collecting" in grid._seg_cells). The cyclic GC buys nothing for
# this short-lived, reference-counted workload -- disable it for the run.
# The author's KiCad 10.0.6 does not need this (see HARDWARE_DESIGN gate
# notes); on 9.x this is what lets the repo autorouter run at all.
gc.disable()

sys.path.insert(0, str(Path(__file__).resolve().parent))

from router import *  # noqa: F401,F403
from router import (  # noqa: F401  (private + public names used by tests/callers)
    PcbRouter, build, main, _print_summary, set_layer_count,
    _capture_pads, _apply_hdi_rules, _clear_tracks, _bga_fanout, _chip_fanout,
    _build_master, _pad_layers, _pad_cells, _pad_own_cells, _mark, _free,
    _via_footprint_free, _seg_cells, _seg_clear, _add_track, _add_via,
    _try_layer, _emit_path, _via_candidates, _bga_edge_via_candidates,
    _route_pair, _mst_order, _via_point_clear, _route_power, _route_rf_fence,
    _is_chip_net, _net_class, _net_width_nm, _mark_radius_for_width,
    GRID_NM, PAD_KEEPOUT_NM, EXCLUDE_NETS, _RF_NETS, PLANE_NETS, POWER_NETS,
    POWER_ROUTER_LAYER, BGA_REF, PCB_PATH, _DEBUG,
)


# The per-board mutable layer tuples are reassigned by set_layer_count() inside
# the router package; resolve them live so ``route_signals.SIGNAL_LAYERS``
# reflects the current board (the original monolithic module's globals were
# live module attributes, not import-time snapshots).
def __getattr__(name):
    if name in {"SIGNAL_LAYERS", "PLANE_LAYERS", "JUMP_LAYERS", "_FAIL_REASON"}:
        from router import _cfg
        return getattr(_cfg, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    # main() builds + saves + returns stats; print only when run as a script
    # (importing the module never produces stdout). exit_cleanly skips the swig
    # teardown GC that segfaults KiCad >= 10 after a mutated board is saved.
    _print_summary(main())
    from _kicad_compat import exit_cleanly
    exit_cleanly(0)