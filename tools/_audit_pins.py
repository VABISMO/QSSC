#!/usr/bin/env python3
import pcbnew, sys
from pathlib import Path
# Resolve the repo root from this file's own location: a hardcoded absolute
# path breaks on every machine except the author's (issue #167).
REPO = Path(__file__).resolve().parents[1]
board = sys.argv[1] if len(sys.argv) > 1 else "main"
path = REPO / "hardware/pcb" / ("qssc.kicad_pcb" if board == "main" else "chip_module.kicad_pcb")
refs = sys.argv[2].split(",") if len(sys.argv) > 2 else []
b = pcbnew.LoadBoard(str(path))
for ref in refs:
    fp = next((f for f in b.GetFootprints() if f.GetReference() == ref), None)
    if not fp:
        print(f"(no {ref})")
        continue
    print(f"\n=== {ref} ({fp.GetValue()}) {len(list(fp.Pads()))} pads ===")
    for p in sorted(fp.Pads(), key=lambda q: (q.GetNumber() == "", q.GetNumber())):
        n = p.GetNumber() or "(EP)"
        nm = p.GetNetname() or "(UNNETTED)"
        print(f"  {ref}.{n:6s} -> {nm}")