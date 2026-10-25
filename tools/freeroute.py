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
import os, sys, subprocess, glob, re
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

# ---- strip poured-plane nets (GND, +3V3) from the DSN before autorouting. ----
# The GND plane (In1.Cu) and +3V3 plane (In2.Cu/In4.Cu "PWR") are poured as full-
# board copper zones by route_qssc_pcb, so those nets are connected by the pour,
# NOT by tracks. KiCad's ExportSpecctraDSN does NOT export zone copper, so every
# plane-net pin (GND alone is ~305 pins on main, ~89 for +3V3) would otherwise
# become an unrouted pin-pair the maze autorouter tries to close with tracks --
# ~400 power/ground connections that overwhelm the autorouter and stall it at
# the "Auto-routing stage started" line with no pass ever completing (the
# observed hang). Dropping the plane-net blocks from the (network ...) section
# removes that routing requirement: the pads stay as physical obstacles in the
# component/padstack section (signal routes still avoid them), only the
# routing REQUIREMENT is removed. The KiCad board keeps the pad nets; the SES
# import only adds signal tracks and never touches plane connectivity. This is
# the standard Specctra flow -- power/ground are poured, never autorouted.
PLANE_NETS = {"GND", "+3V3"}

def _truncate_plane_net_pins(dsn_path, plane_nets):
    """For each poured-plane net, keep its (net NAME ...) block (so pre-routed
    (wire ...) siblings that carry (net NAME) still reference a declared net and
    FreeRouting sees that copper as obstacles) but truncate its (pins ...) list
    to the FIRST pin only. A net with one declared pin has a zero-length
    spanning tree = 0 unrouted connections, so the autorouter never tries to
    route the ~400 GND/+3V3 pin-pairs as tracks. The other plane-net pads keep
    their net in the KiCad board and are tied by the poured zone; in the DSN
    they are simply no-longer-listed, so FreeRouting treats them as plain
    obstacles (signal routes still avoid every pad). Returns pin refs dropped."""
    txt = dsn_path.read_text()
    i_net = txt.find("(network")
    if i_net == -1:
        return 0
    depth, j, net_end = 0, i_net, None
    while j < len(txt):
        if txt[j] == "(":
            depth += 1
        elif txt[j] == ")":
            depth -= 1
            if depth == 0:
                net_end = j + 1
                break
        j += 1
    if net_end is None:
        return 0
    net_section = txt[i_net:net_end]
    dropped = 0
    for nm in plane_nets:
        pat = f"(net {nm}"
        k = net_section.find(pat)
        while k != -1:
            after = k + len(pat)
            # name must END here (next char whitespace/newline) so "+3V3" does
            # not also match a hypothetical "+3V3_FOO" net.
            if after < len(net_section) and not net_section[after].isspace():
                k = net_section.find(pat, k + 1)
                continue
            # depth-walk from "(net NAME" to its matching ")".
            d, m = 1, k + 1
            while m < len(net_section) and d > 0:
                if net_section[m] == "(":
                    d += 1
                elif net_section[m] == ")":
                    d -= 1
                m += 1
            block = net_section[k:m]
            # find the (pins ...) sub-block inside this net block.
            ip = block.find("(pins")
            if ip == -1:
                k = net_section.find(pat, k + 1)
                continue
            dp, mp = 1, ip + 5
            while mp < len(block) and dp > 0:
                if block[mp] == "(":
                    dp += 1
                elif block[mp] == ")":
                    dp -= 1
                mp += 1
            pins_block = block[ip:mp]              # "(pins C58-1 U3-4 ... )"
            pins = re.findall(r"\b[A-Za-z0-9_.@+-]+-[A-Za-z0-9@]+\b", pins_block)
            if len(pins) <= 1:
                k = net_section.find(pat, k + 1)
                continue
            new_pins = f"(pins {pins[0]})"
            dropped += len(pins) - 1
            new_block = block[:ip] + new_pins + block[mp:]
            net_section = net_section[:k] + new_block + net_section[m:]
            k = net_section.find(pat, k + len(new_block))
    dsn_path.write_text(txt[:i_net] + net_section + txt[net_end:])
    return dropped

def _strip_plane_blocks(dsn_path, plane_nets):
    """Remove the ``(plane <net> (polygon ...))`` blocks from the DSN's
    ``(structure ...)`` section. KiCad's ExportSpecctraDSN emits the zone
    OUTLINE polygons as Specctra ``(plane ...)`` forms; FreeRouting 2.4.1
    turns each into a ConductionArea and tries to fill/render it, which on a
    full-board plane hits a NullPointerException in
    ``ConductionArea.ensureDetailedFillCache`` (ShapeSearchTree.overlapping-
    TreeEntries, currentObject null) in the Swing paint thread. A route that
    finishes with unresolved violations then writes a 0-byte SES, so the whole
    cycle is wasted. Power/ground are poured by route_qssc_pcb AFTER
    autorouting (the standard Specctra flow -- planes are never autorouted),
    so FreeRouting does not need the plane polygons at all: stripping them
    (a) avoids the fill/render NPE, (b) removes plane-clearance violations
    from the autorouter's DRC count so the optimizer can clean the remaining
    signal-to-signal ones, and (c) frees the full-board routing corridor the
    plane keepout was reserving. The KiCad board keeps its zones; the SES
    import only adds signal tracks, and route_qssc_pcb re-pours fresh planes
    around them afterwards. Returns the number of plane blocks removed."""
    txt = dsn_path.read_text()
    removed = 0
    for nm in plane_nets:
        pat = f"(plane {nm}"
        k = txt.find(pat)
        while k != -1:
            after = k + len(pat)
            if after < len(txt) and not txt[after].isspace():
                k = txt.find(pat, k + 1)
                continue
            d, m = 1, k + 1
            while m < len(txt) and d > 0:
                if txt[m] == "(":
                    d += 1
                elif txt[m] == ")":
                    d -= 1
                m += 1
            txt = txt[:k] + txt[m:]
            removed += 1
            k = txt.find(pat, k)
    dsn_path.write_text(txt)
    return removed


def _loosen_clearance(dsn_path, minclr):
    """Align FreeRouting's clearance floor to the board's actual min_clearance.

    KiCad's ExportSpecctraDSN writes the netclass/default clearance (often
    150 um = 0.15 mm) which can be STRICTER than the board's min_clearance
    (e.g. 0.10 mm). FreeRouting then flags every pad/track pair that sits
    between min_clearance and the netclass clearance as a "violation" -- on a
    dense fine-pitch board that is ~1000 constant violations the optimizer
    can never clean (they are KiCad-legal placements, not routing faults), and
    a route that finishes with unresolved violations writes a 0-byte SES.

    Setting MINCLR=<um> (e.g. MINCLR=100 for a 0.10 mm min_clearance board)
    rewrites every (clearance X) with X > MINCLR down to MINCLR, so the
    autorouter uses the board's REAL design rule. Imported routes still pass
    KiCad DRC because MINCLR equals (never undercuts) min_clearance. Leave
    MINCLR unset to keep the DSN clearances as exported (boards that already
    route clean). Returns the number of clearance values lowered.
    """
    import re as _re

    txt = dsn_path.read_text()
    n = 0

    def _sub(m):
        nonlocal n
        x = float(m.group(1))
        if x > minclr:
            n += 1
            return f"(clearance {minclr:g}"
        return m.group(0)

    txt = _re.sub(r"\(clearance\s+([\d.]+)", _sub, txt)
    dsn_path.write_text(txt)
    return n


def _loosen_width(dsn_path, minwidth):
    """Align FreeRouting's width floor to the board's actual min_track_width.

    Symmetric to :func:`_loosen_clearance`. KiCad's ExportSpecctraDSN writes the
    netclass/default track width (often 200 um = 0.20 mm) which can be STRICTER
    than the board's min_track_width (e.g. 0.10 mm). FreeRouting then flags every
    pre-routed (or freshly routed) track narrower than the netclass width as a
    "violation" -- on a board with hundreds of 0.10/0.12/0.15 mm die-fanout
    escapes that is ~1000 constant width violations the optimizer can never
    clean (you can't widen a fixed pre-routed escape without risking a short),
    and unresolved violations can block the SES write.

    Setting MINWIDTH=<um> (e.g. MINWIDTH=100 for a 0.10 mm min_track_width
    board) rewrites every (width X) with X > MINWIDTH down to MINWIDTH, so the
    autorouter uses the board's REAL width floor. Imported routes still pass
    KiCad DRC because MINWIDTH equals (never undercuts) min_track_width. Leave
    MINWIDTH unset to keep the DSN widths as exported. Returns the number of
    width values lowered.
    """
    import re as _re

    txt = dsn_path.read_text()
    n = 0

    def _sub(m):
        nonlocal n
        x = float(m.group(1))
        if x > minwidth:
            n += 1
            return f"(width {minwidth:g}"
        return m.group(0)

    txt = _re.sub(r"\(width\s+([\d.]+)", _sub, txt)
    dsn_path.write_text(txt)
    return n


dropped = _truncate_plane_net_pins(dsn, PLANE_NETS)
print(f"  truncated {dropped} plane-net pin refs ({', '.join(sorted(PLANE_NETS))}) "
      f"to 1 each in DSN -> autorouter routes signals only")
stripped = _strip_plane_blocks(dsn, PLANE_NETS)
print(f"  stripped {stripped} (plane ...) polygon blocks from DSN "
      f"-> FreeRouting routes signals only, planes re-poured after")

_minclr_env = os.environ.get("MINCLR")
if _minclr_env:
    _minclr = float(_minclr_env)
    _lowered = _loosen_clearance(dsn, _minclr)
    print(f"  loosened {_lowered} DSN clearance rules > {_minclr:g}um -> {_minclr:g}um "
          f"(MINCLR env; aligns autorouter to board min_clearance so KiCad-legal "
          f"placements stop registering as FreeRouting violations)")

_minw_env = os.environ.get("MINWIDTH")
if _minw_env:
    _minw = float(_minw_env)
    _wlowered = _loosen_width(dsn, _minw)
    print(f"  loosened {_wlowered} DSN width rules > {_minw:g}um -> {_minw:g}um "
          f"(MINWIDTH env; aligns autorouter to board min_track_width so "
          f"KiCad-legal narrow tracks stop registering as FreeRouting violations)")

# FreeRouting 2.4.1 CLI: -de <dsn> -do <ses> -mp <passes> -mt <threads>.
# -mp makes it autoroute (without -mp it loads + exits). -mt 1 = single-thread
# shove-trace optimizer (closes dense fans + cleans violations; avoids the
# 2.1.0 -mt 2 two-thread crash). Do NOT pass -drc here: in 2.4.1 that means
# "DRC-only and exit" and skips routing.
print(f"== FreeRouting ({jar.name}) java={java_exe} ==")
# By default run headless on a virtual framebuffer (xvfb-run -a) so the autorouter
# never grabs the user's screen. Set NO_XVFB=1 to drop xvfb-run and run FreeRouting's
# own Swing GUI on the inherited $DISPLAY instead -- the routing window appears and
# you can watch the fanout + autoroute passes draw live. Still resource-capped by the
# systemd-run caller, so it cannot block the machine either way.
# Optional -us (updating_strategy: Greedy/Global/Hybrid) and -is (selection:
# Sequential/random/prioritized) via env US=/IS=. Global + random escapes the
# local minimum where the Greedy optimizer stalls at 0% on fine-pitch escapes
# blocked by the autorouter's own fanout vias -- Global re-routes whole nets
# (rip-up), not just shove, so it can tear out a blocking 0.60 mm via and re-close
# the net it was strangling.
extra = []
if os.environ.get("US"):
    extra += ["-us", os.environ["US"]]
if os.environ.get("IS"):
    extra += ["-is", os.environ["IS"]]
if os.environ.get("NO_XVFB"):
    cmd = [java_exe, "-jar", str(jar),
           "-de", str(dsn), "-do", str(ses), "-mt", MT, "-mp", MP] + extra
    print(f"  NO_XVFB=1 -> GUI on DISPLAY={os.environ.get('DISPLAY', '(none)')}")
else:
    cmd = ["xvfb-run", "-a", java_exe, "-jar", str(jar),
           "-de", str(dsn), "-do", str(ses), "-mt", MT, "-mp", MP] + extra
print("  ", " ".join(map(str, cmd)))

# Delete any STALE session file before launching FreeRouting. If FreeRouting
# fails to run (missing jar, crash, bad DSN) it produces no new SES, and the
# check below would otherwise see LAST cycle's SES still on disk and silently
# re-import those old routes on top of the current board -- duplicating tracks
# and churning the file. A failed run must abort, not paper over with stale.
if ses.exists():
    ses.unlink()
    print(f"  removed stale {ses.name} (will abort if FreeRouting produces none)")

with open(log, "w", encoding="utf-8") as lf:
    p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT)
print(f"  freerouting exit: {p.returncode}  ses exists: {ses.exists()}  size:",
      ses.stat().st_size if ses.exists() else 0)
if not ses.exists():
    print("  --- log tail ---")
    print(open(log, encoding="utf-8").read()[-2000:])
    sys.exit("FreeRouting produced no SES")

print(f"== importing SES -> {board_path.name} ==")
ok2 = pcbnew.ImportSpecctraSES(board, str(ses))
print("  ImportSpecctraSES ->", ok2)
board.Save(str(board_path))
print(f"  saved {board_path}")
print("DONE")
