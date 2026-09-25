"""Repo + KiCad library paths for the QSSC board generator package.

Inserts the repo root on ``sys.path`` so ``from tools.sexp import ...``
resolves whether the package is imported as ``tools.kicad`` or reached
through the legacy ``gen_qssc_kicad`` shim.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PCB_DIR = ROOT / "hardware" / "pcb"
SYM_LIB = "/usr/share/kicad/symbols"
FP_LIB = "/usr/share/kicad/footprints"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

__all__ = ["ROOT", "PCB_DIR", "SYM_LIB", "FP_LIB"]
