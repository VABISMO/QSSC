#!/usr/bin/env python3
"""G-code for a DIY spin-coater chuck milled from 1 cm MDF on a CNC 3018.

The 3018's spindle becomes the spin-coater drive: a plain 3.175 mm (1/8") shaft
(or the shank end of a bit) is chucked in the ER11, and the two MDF pieces below
form a horizontal platter that spins with it.  No spin-coater is bought -- the
CNC spindle IS the motor.

TWO pieces, both Ø80 mm x 10 mm MDF, milled with a single 2 mm flat end-mill
(every feature is vertical/Z-axis, the only kind a 3018's fixed vertical spindle
can cut).  The disk outline is cut LAST with NO tabs, so each finished piece
drops free of the block -- clamp or tape the stock so it does not shift on the
final pass.

  PIECE A -- collet lid ("bandeja enchufe CNC"), ``spincoater_collet.gcode``:
    - Ø80 mm disk, plain.
    - central Ø10 mm AXIAL hole -- the bit / drive shaft enters here.
    - 4 x Ø2 mm tie-screw holes at the VERTICES of the 40x40 square (±20, ±20),
      identical to piece B, so an M2 screw passes A-through-B and clamps them.

  PIECE B -- crystal tray ("bandeja soporte de cristales"), ``spincoater_tray.gcode``:
    - Ø80 mm disk, plain (no neck).
    - 40 x 40 mm central pocket, 1 mm deep -- the ITO / glass slide sits recessed
      in it so centrifugal force does not fling it off (a 1.1 mm ITO glass stands
      0.1 mm proud, coating spreads on the exposed top face).
    - 4 x Ø2 mm tie-screw holes at the VERTICES of the 40x40 square (±20, ±20),
      matching piece A.

ASSEMBLY: piece A (collet) up, gripping the shaft; piece B (crystal) below; four
M2 screws through both with M2 spacers between (the "separador" -- ~15 mm nylon
spacers give side access to pipette solution onto the glass), nuts below.  Lower
the CNC Z to load the glass into the pocket, dispense, raise Z, run the spindle.

All cuts are LINEAR-SEGMENT (no G02 arcs -- some 3018 viewers misrender I/J as a
diagonal chord).  Work zero = disk centre (0,0).  Stock: any MDF sheet > 82 mm.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.gcode.emitter import (
    CUT_FEED,
    PLUNGE_FEED,
    SAFE_Z,
    G,
    circle_bore,
    rect_pocket,
)

# --- stock / tool -----------------------------------------------------------
STOCK_Z = 10.0          # 1 cm MDF
SPIN_BIT = 2.0          # 2 mm flat end-mill (single bit, every feature)
OUT_Z_STEP = 1.0        # Z step per pass for the disk outline (MDF cuts easily)

# --- geometry ---------------------------------------------------------------
DISK_R = 40.0           # finished disk radius -> Ø80 mm
AXIAL_HOLE_R = 5.0      # Ø10 mm central hole -- the bit / drive shaft enters here
# 4 x Ø2 mm tie-screw holes at the VERTICES of the 40x40 support square (±20, ±20),
# identical on both pieces so an M2 screw passes A-through-B and clamps them.
M2_HOLES = [(20.0, 20.0), (-20.0, 20.0), (20.0, -20.0), (-20.0, -20.0)]
POCKET = 40.0           # 40 x 40 mm central pocket (piece B)
POCKET_Z = 1.0          # pocket depth -- 1 mm blind
POCKET_STEPOVER = 1.0   # = bit width
# NO tabs -- the full circle is cut through (last op) so the piece drops out of
# the block. Clamp/tape the stock so it does not shift on the final pass.

SPINDLE_RPM = 12000     # 3018 775 spindle (MDF)


def plunge_hole(g: G, cx: float, cy: float, z_target: float,
                z_step: float = 0.5, feed: int = PLUNGE_FEED) -> None:
    """Plunge a bit-diameter hole straight through (for the Ø2 = bit M2 holes)."""
    g.rapid(cx, cy, z=SAFE_Z)
    z = 0.0
    while z > z_target + 1e-6:
        z = max(z - z_step, z_target)
        g.plunge(z, feed=feed)
    g.retract()


def disk_cutout(g: G, cx: float, cy: float, R: float, bit: float,
                z_target: float, z_step: float = 1.0,
                tabs: list[tuple[float, float]] | None = None,
                n_seg: int = 72, feed: int = CUT_FEED) -> None:
    """Through-cut a disk of finished radius ``R`` with optional ``tabs``.

    The bit-centre path runs at ``R + bit/2`` so the inner edge of the kerf
    lands on ``R`` (the finished disk).  Linear-segment polygon (no G02).
    Tabs = list of ``(angle_deg, half_width_deg)`` left as uncut bridges so the
    disk stays in the sheet; the bit lifts to SAFE_Z over each tab span.
    """
    bit_r = bit / 2.0
    path_r = R + bit_r
    zones = [(math.radians(a - hw), math.radians(a + hw)) for a, hw in (tabs or [])]

    def in_tab(a: float) -> bool:
        return any(lo <= a <= hi for lo, hi in zones)

    angs = [2.0 * math.pi * k / n_seg for k in range(n_seg + 1)]
    sx, sy = cx + path_r, cy
    g.rapid(sx, sy, z=SAFE_Z)
    z = 0.0
    while z > z_target + 1e-6:
        z = max(z - z_step, z_target)
        g.plunge(z, feed=PLUNGE_FEED)
        cutting = True
        for k in range(1, n_seg + 1):
            a = angs[k]
            x = cx + path_r * math.cos(a)
            y = cy + path_r * math.sin(a)
            t = in_tab(a)
            if t and cutting:
                g.retract()                       # lift over the tab
                cutting = False
            elif (not t) and (not cutting):
                g.rapid(x, y, z=SAFE_Z)            # drop in past the tab
                g.plunge(z, feed=PLUNGE_FEED)
                cutting = True
            if cutting:
                g.feed(x, y, feed=feed)
        g.retract()
        g.rapid(sx, sy, z=SAFE_Z)


def _header(g: G, title: str, body: str) -> None:
    g.comment(title)
    g.comment(f"MDF {STOCK_Z:.1f} mm | bit Ø{SPIN_BIT:.1f} mm | disk Ø{2*DISK_R:.0f} mm | spindle {SPINDLE_RPM} rpm")
    g.comment(f"feeds {PLUNGE_FEED} (plunge) / {CUT_FEED} (cut) mm/min | work zero = disk centre (0,0)")
    g.comment(body)
    g.blank()
    g.comment("G0 Z10.000  ; safe Z FIRST -- zero the bit on the MDF top surface, then run")


def gen_spincoater_collet() -> str:
    """Piece A -- collet lid (the bit/shaft passes through the central Ø10 hole)."""
    g = G()
    _header(
        g,
        "QSSC DIY SPIN-COATER  --  PIECE A: collet lid (bandeja enchufe CNC)",
        "Ø80 disk | central Ø10 shaft hole | 4x Ø2 tie holes at (±20,±20) "
        "(match piece B) | outline cut LAST, no tabs -- drops out of the block",
    )
    g.blank()

    g.comment("--- 1. central Ø10 axial hole (bit / drive shaft enters here) ---")
    circle_bore(g, 0.0, 0.0, AXIAL_HOLE_R, SPIN_BIT / 2.0, z_target=-STOCK_Z)
    g.blank()

    g.comment("--- 2. four Ø2 mm tie-screw holes at the square vertices (±20, ±20) ---")
    for hx, hy in M2_HOLES:
        g.comment(f"    M2 tie hole @ ({hx:.1f}, {hy:.1f})  -- matches piece B")
        plunge_hole(g, hx, hy, z_target=-STOCK_Z)
    g.blank()

    g.comment("--- 3. disk outline, through-cut, NO tabs (LAST -- frees the piece) ---")
    disk_cutout(g, 0.0, 0.0, DISK_R, SPIN_BIT, z_target=-STOCK_Z,
                z_step=OUT_Z_STEP, tabs=None)
    g.blank()
    g.comment("end of piece A -- the disk is cut free, separate it from the block")
    return g.text()


def gen_spincoater_tray() -> str:
    """Piece B -- crystal tray (holds the glass in a 40x40 pocket)."""
    g = G()
    _header(
        g,
        "QSSC DIY SPIN-COATER  --  PIECE B: crystal tray (bandeja soporte de cristales)",
        "Ø80 disk | 40x40 mm x 1 mm central pocket (glass sits recessed) | "
        "4x Ø2 tie holes at (±20,±20) (match piece A) | outline cut LAST, no tabs",
    )
    g.blank()

    g.comment("--- 1. 40 x 40 mm central pocket, 1 mm deep (glass support) ---")
    rect_pocket(g, -POCKET / 2.0, -POCKET / 2.0, POCKET, POCKET, SPIN_BIT,
                z_target=-POCKET_Z, stepover=POCKET_STEPOVER)
    g.blank()

    g.comment("--- 2. four Ø2 mm tie-screw holes at the square vertices (±20, ±20) ---")
    for hx, hy in M2_HOLES:
        g.comment(f"    M2 tie hole @ ({hx:.1f}, {hy:.1f})  -- matches piece A")
        plunge_hole(g, hx, hy, z_target=-STOCK_Z)
    g.blank()

    g.comment("--- 3. disk outline, through-cut, NO tabs (LAST -- frees the piece) ---")
    disk_cutout(g, 0.0, 0.0, DISK_R, SPIN_BIT, z_target=-STOCK_Z,
                z_step=OUT_Z_STEP, tabs=None)
    g.blank()
    g.comment("end of piece B -- the disk is cut free, separate it from the block")
    return g.text()


FILES = {
    "spincoater_collet.gcode": gen_spincoater_collet,
    "spincoater_tray.gcode": gen_spincoater_tray,
}


def main() -> None:
    from pathlib import Path

    out_dir = Path(__file__).resolve().parent.parent / "hardware" / "gcode"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, fn in FILES.items():
        text = fn()
        (out_dir / name).write_text(text)
        print(f"  wrote {out_dir/name}  ({text.count(chr(10))} lines)")


if __name__ == "__main__":
    main()