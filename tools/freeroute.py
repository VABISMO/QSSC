#!/usr/bin/env python3
"""Export a QSSC board to Specctra DSN, run FreeRouting, import the SES back.

Usage: python3 tools/freeroute.py <board.kicad_pcb> [--jar <freerouting.jar>]

Resource-capped by the caller (systemd-run, ~2000M). Uses system python3 (pcbnew).

Why -mt 0: FreeRouting 2.1.0/2.4.1 crashes in the shove-trace OPTIMIZER
(`MazeShoveTraceAlgo.check_shove_trace_line` ArrayIndexOutOfBounds) when the
optimizer runs with >0 threads (-mt 2). The config default is
`multi_threading=false`; -mt 2 overrides it and trips the off-by-one. -mt 0
disables the crashing optimizer entirely -- the autorouter (-mp) still runs
and produces a complete SES; we lose only post-route pull-tight/length-tuning
(GUI-optimize later). Verified against the head board that crashed at 47 min on -mt 2. NB: the 2.4.1 jar needs Java 25 (class v69); this box has Java 21/23, so we run the Java-21-built 2.1.0 jar (tools/bin/freerouting.jar) with -mt 0.
"""
import sys, subprocess, os
from pathlib import Path
import pcbnew

board_path = Path(sys.argv[1]).resolve()
jar = (Path(sys.argv[2]).resolve() if len(sys.argv) > 2
       else Path(__file__).resolve().parent / "bin" / "freerouting.jar")
stem = board_path.with_suffix("")
dsn = stem.with_suffix(".dsn")
ses = stem.with_suffix(".ses")
log = stem.with_suffix(".freeroute.log")
drc = stem.with_suffix(".freeroute.drc.json")

print(f"== loading {board_path.name} ==")
board = pcbnew.LoadBoard(str(board_path))

print(f"== exporting DSN -> {dsn.name} ==")
ok = pcbnew.ExportSpecctraDSN(board, str(dsn))
print("  ExportSpecctraDSN ->", ok, "size:",
      dsn.stat().st_size if dsn.exists() else 0)
if not dsn.exists():
    sys.exit("DSN export failed")

# FreeRouting 2.4.1 CLI: -de <dsn> -do <ses>.  -mt 0 disables the crashing
# shove-trace optimizer (autorouter -mp still runs).  -drc captures remaining
# unconnected nets for a targeted cleanup pass.
print(f"== FreeRouting ({jar.name}) ==")
cmd = ["xvfb-run", "-a", "java", "-jar", str(jar),
       "-de", str(dsn), "-do", str(ses), "-mt", "0", "-drc", str(drc)]
print("  ", " ".join(map(str, cmd)))
with open(log, "w") as lf:
    p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT)
print(f"  freerouting exit: {p.returncode}  ses exists: {ses.exists()}  size:",
      ses.stat().st_size if ses.exists() else 0)
if not ses.exists():
    print("  --- log tail ---")
    print(open(log).read()[-2000:])
    sys.exit("FreeRouting produced no SES")

print(f"== importing SES -> {board_path.name} ==")
ok2 = pcbnew.ImportSpecctraSES(board, str(ses))
print("  ImportSpecctraSES ->", ok2)
board.Save(str(board_path))
print(f"  saved {board_path}")
print("DONE")
