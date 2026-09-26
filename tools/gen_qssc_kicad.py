#!/usr/bin/env python3
"""Generate the QSSC KiCad schematic + PCB from a single component/net model.

Outputs (relative to repo root):
  hardware/pcb/qssc.kicad_sch   - ERC-clean flat schematic (nets by label + power syms)
  hardware/pcb/qssc.kicad_pcb   - DRC-clean, placed + netted 6-layer board
  hardware/pcb/qssc-lib.kicad_sym - custom symbol library (FPGA, GVA-84, AMS1117, +5V_RF)
  hardware/pcb/qssc.kicad_pro   - project with net classes + design rules
  hardware/pcb/qssc-grad.*      - extended main board (+ field-gradient coil + DAC)
  hardware/pcb/chip_module.*    - detector head board (TIA + AD7616 + die detector face)

KiCad 8 ``kicad-cli`` cannot export Specctra DSN or import SES (GUI-only), so
the board is placed + netted + rule-checked but NOT signal-routed. The
copper-plane foundation + signal routing are produced by the companion
router scripts (``tools/route_qssc_pcb.py``, ``tools/route_signals.py``).

This module is now a thin shim over the :mod:`tools.kicad` package (split for
readability). It re-exports the package's public surface so legacy callers
(``import gen_qssc_kicad as g`` in ``tools/audit_netlist.py`` /
``tools/gen_bom.py``) keep working.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.kicad import *  # noqa: F401,F403
from tools.kicad import (  # noqa: F401  (explicit re-export for the legacy contract)
    COMPONENTS, add, build_pincoords, pin_endpoint, load_footprint_mod,
    build_main_board, build_gradient_board, build_detector_head,
    build_detector_analog, build_emitter_driver,
    assign_pcb_positions, assign_pcb_positions_gradient,
    assign_pcb_positions_head, assign_sch_positions,
    emit_schematic, emit_pcb, emit_pro,
    MAIN_CFG, EXT_CFG, HEAD_CFG,
    authored_symbols, KicadGenerator, ROOT, PCB_DIR,
)


if __name__ == "__main__":
    from tools.kicad.__main__ import main
    main()
