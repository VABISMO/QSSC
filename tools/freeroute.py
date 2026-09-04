#!/usr/bin/env python3
"""Export a QSSC board to Specctra DSN, run FreeRouting, import the SES back.

Usage: python3 tools/freeroute.py <board.kicad_pcb> [--jar <freerouting.jar>]

Resource-capped by the caller (systemd-run, ~2000M). Uses system python3 (pcbnew).

Jar / Java selection:
  - Default jar = tools/bin/freerouting-2.4.1.jar (FreeRouting 2.4.1, built with
    Java 25 -> class v69). Override with --jar <path> or the JAR env var.
  - Default java = the portable Temurin JDK 25 at ~/.local/jdk25/jdk-25*/bin/java
    (no system install; outside the repo so it never touches git). Override with
    the JAVA env var (a path to a java executable). Falls back to system `java`
    only if neither is set/found -- but system java is 21/23 and CANNOT run the
    2.4.1 jar (UnsupportedClassVersionError), so set JAVA or keep the portable
    JDK 25 in place.

Why -mt 0: FreeRouting 2.1.0 crashes on BOTH -mt 2 (MazeShoveTraceAlgo.
check_shove_trace_line ArrayIndexOutOfBounds in the shove-trace OPTIMIZER) and
-mt 0 (maze_search_algo.find_connection NullPointerException, target_shape
null, in the autorouter itself). 2.4.1 on Java 25 is the path that actually
runs. -mt 1 runs the SINGLE-thread shove-trace optimizer (push-and-shove):
it closes dense fans in few passes AND cleans the clearance violations the
bare maze autorouter leaves behind. The 2.1.0 crash was specifically the
2-THREAD optimizer (-mt 2, MazeShoveTraceAlgo off-by-one); -mt 1 avoids that
race, and 2.4.1's autorouter is already proven (pass 1 completes). -mt 0
(bare maze, no optimizer) is TOO SLOW (~92 min/pass at 40% cap) and leaves
hundreds of unfixable violations -- do not use it for a real route. Override
with MT=0 only to probe the autorouter alone.

2.4.1 CLI flags (from -help): -de <dsn> load, -do <ses> save session at end,
-mp <passes> max AUTO-ROUTING passes (THIS is what makes it route -- without
-mp it just loads + exits), -mt <threads> route-OPTIMIZATION thread pool
(default cores-1; 0 = no optimization, just the maze autorouter). NB: -drc
<file> in 2.4.1 means "write a DRC report and EXIT" (DRC-only mode) -- do NOT
pass it when you want to autoroute, or it skips routing entirely.
"""
import os, sys, subprocess, glob
from pathlib import Path
import pcbnew

board_path = Path(sys.argv[1]).resolve()
MT = os.environ.get("MT", "1")
# Auto-routing passes (-mp): the autorouter loop count. 64 is enough for a
# dense fan (head ~150 nets, main ~120); raise via MP= if unrouted remain.
MP = os.environ.get("MP", "64")

# ---- jar: 2.4.1 by default, override via arg2 or JAR env ----
_here = Path(__file__).resolve().parent
if len(sys.argv) > 2 and not sys.argv[2].startswith("--"):
    jar = Path(sys.argv[2]).resolve()
elif os.environ.get("JAR"):
    jar = Path(os.environ["JAR"]).resolve()
else:
    j24 = _here / "bin" / "freerouting-2.4.1.jar"
    jar = j24 if j24.exists() else (_here / "bin" / "freerouting.jar")

# ---- java: portable JDK 25 by default, override via JAVA env ----
def _find_java():
    env_java = os.environ.get("JAVA")
    if env_java and Path(env_java).exists():
        return env_java
    # portable Temurin JDK 25 extracted under ~/.local/jdk25
    for p in sorted(glob.glob(str(Path.home() / ".local" / "jdk25" / "jdk-25*" / "bin" / "java"))):
        if Path(p).exists():
            return p
    return "java"  # fallback (system; will fail on the 2.4.1 jar)
java_exe = _find_java()
stem = board_path.with_suffix("")
dsn = stem.with_suffix(".dsn")
ses = stem.with_suffix(".ses")
log = stem.with_suffix(".freeroute.log")

print(f"== loading {board_path.name} ==")
board = pcbnew.LoadBoard(str(board_path))

print(f"== exporting DSN -> {dsn.name} ==")
ok = pcbnew.ExportSpecctraDSN(board, str(dsn))
print("  ExportSpecctraDSN ->", ok, "size:",
      dsn.stat().st_size if dsn.exists() else 0)
if not dsn.exists():
    sys.exit("DSN export failed")

# FreeRouting 2.4.1 CLI: -de <dsn> -do <ses> -mp <passes> -mt <threads>.
# -mp makes it autoroute (without -mp it loads + exits). -mt 1 = single-thread
# shove-trace optimizer (closes dense fans + cleans violations; avoids the
# 2.1.0 -mt 2 two-thread crash). Do NOT pass -drc here: in 2.4.1 that means
# "DRC-only and exit" and skips routing.
print(f"== FreeRouting ({jar.name}) java={java_exe} ==")
cmd = ["xvfb-run", "-a", java_exe, "-jar", str(jar),
       "-de", str(dsn), "-do", str(ses), "-mt", MT, "-mp", MP]
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
