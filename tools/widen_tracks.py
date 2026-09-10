#!/usr/bin/env python3
"""Widen sub-min-width tracks to the board minimum width.

FreeRouting routes at its own default width (0.075 mm) and ignores KiCad
netclass widths, so Chip-netclass tracks (board min 0.10 mm) come back below
the DRC floor. This post-process reads the board-wide ``min_track_width`` rule
from the project and lifts every track/via-arc below it up to the floor.

Run AFTER each FreeRouting cycle (and before re-pouring planes / re-running DRC):

    python3 tools/widen_tracks.py [board.kicad_pcb]

Uses the system ``python3`` (pcbnew). No .venv.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOARD = ROOT / "hardware" / "pcb" / "qssc.kicad_pcb"


def board_min_track_width_nm(board: pcbnew.BOARD) -> int:
    """Return the board-wide min track width in nm (fallback 0.10 mm)."""
    pro = Path(board.GetFileName()).with_suffix(".kicad_pro")
    if pro.exists():
        try:
            cfg = json.loads(pro.read_text())
            rules = cfg["board"]["design_settings"]["rules"]
            mm = float(rules.get("min_track_width", 0.10))
            return int(round(mm * 1e6))
        except Exception:
            pass
    return 100000  # 0.10 mm


def widen(board_path: Path) -> int:
    board = pcbnew.LoadBoard(str(board_path))
    floor = board_min_track_width_nm(board)
    changed = 0
    for t in board.GetTracks():
        w = t.GetWidth()
        if 0 < w < floor:
            t.SetWidth(floor)
            changed += 1
    if changed:
        board.Save(str(board_path))
    print(f"widen_tracks: floor={floor/1e6:.3f}mm widened {changed} tracks in {board_path.name}")
    return changed


def main() -> int:
    board = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BOARD
    widen(board)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())