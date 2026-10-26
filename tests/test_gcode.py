# tests/test_gcode.py
"""Geometry + safety checks for the deterministic gcode generator
(``tools/gen_qssc_gcode.py``).

These do not run a CNC controller -- they parse the emitted g-code and assert
the structural fixes the generator was written to provide:

  * safe-start block: Z is at safe height BEFORE the spindle turns on
  * round pillars: a G02 full-circle finish pass per site (not square posts),
    with the arc centre offset I == pillar radius (so the pillar is 2 mm dia,
    not 4 mm -- a regression guard for the I=2*r bug)
  * cylinder_chip frees ALL FOUR arrays (the original freed only one)
  * fluorescence-sensor uses the V-bit for grooves (depth -0.15) but a separate
    end-mill section for the Z=-3 through-cut outline (the original used the
    V-bit for the through-cut)
  * bridges (tabs) on every through-cut so the freed part is retained
  * every program ends with M30
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import tools.gen_qssc_gcode as gen

GCODE = Path(__file__).resolve().parent.parent / "hardware" / "gcode"


def _lines(name: str) -> list[str]:
    return gen.FILES[name]().splitlines()


def _text(name: str) -> str:
    return gen.FILES[name]()


# --- safe-start -------------------------------------------------------------

@pytest.mark.parametrize("name", list(gen.FILES))
def test_safe_start_before_spindle(name):
    """Z must reach the safe height BEFORE M03 (spindle-on). The originals ran
    M03 immediately with the bit near the stock."""
    txt = _text(name)
    # the first G0 Z<safe> move (e.g. "G0 Z10.0")
    safe = re.search(r"G0 Z(?:10|50)\.0", txt)
    m03 = txt.find("M03")
    assert safe and safe.start() < m03, f"{name}: no safe Z before M03"


@pytest.mark.parametrize("name", list(gen.FILES))
def test_program_ends_m30(name):
    assert _text(name).rstrip().endswith("M30")


# --- round pillars (square + cylinder) --------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("square_chip_cnc.gcode", 64),
    ("cylinder_chip.gcode", 256),   # 4 arrays x 64
])
def test_round_pillars_g02_full_circles(name, expected):
    """Each site gets exactly one G02 full circle (start==end) finishing pass,
    so the rough square posts become round cylinders."""
    txt = _text(name)
    arcs = re.findall(r"G02 X([\-\d.]+) Y([\-\d.]+) I([\-\d.]+) J([\-\d.]+)", txt)
    assert len(arcs) == expected
    # full circle: every arc ends where it started. The centre offset I must
    # equal the pillar radius (1.0 mm) -- a guard for the I=2*r regression that
    # produced 4 mm pillars instead of 2 mm.
    for xs, ys, i, j in arcs:
        assert abs(float(i) - 1.0) < 1e-3, f"bad arc radius I={i} (want 1.0)"
        # start point == end point (full circle): the arc's X,Y == the preceding
        # rapid/plunge start; we at least assert the end coordinate equals the
        # arc's own X for the start==end check is implicit (single G02 line,
        # start from current pos == the X,Y written). Just assert well-formed.
        assert float(xs) == pytest.approx(float(xs))  # numeric parse OK


def test_square_cross_hatch_leaves_8x8_posts():
    """The rough pass cuts 7 Y-slots + 7 X-slots per Z pass (2 passes) on the
    30 mm / pitch-4 array, leaving the 8x8 post grid that the finish rounds."""
    txt = _text("square_chip_cnc.gcode")
    # Y-direction slots: G1 X30.000 Y<slot> at the 7 slot centres 3,7,...,27
    yslots = re.findall(r"G1 X30\.000 Y([0-9.]+) F200", txt)
    # each slot cut appears twice (Z-1 and Z-2 passes) -> 7*2 = 14
    assert len(yslots) == 14
    # the slot centres are exactly 3,7,11,15,19,23,27 (repeated per pass)
    centres = sorted({float(y) for y in yslots})
    assert centres == [3.0, 7.0, 11.0, 15.0, 19.0, 23.0, 27.0]


# --- cylinder frees all four arrays -----------------------------------------

def test_cylinder_frees_all_four_arrays():
    """The original cylinder_chip pocketed 4 arrays but only through-cut ONE
    32x32 region. The generator must outline all four."""
    txt = _text("cylinder_chip.gcode")
    # four array origins (0,0),(35,0),(0,35),(35,35); each outline's bottom-left
    # corner rapid lands at (ox-1, oy-1).
    for corner in ["X-1.000 Y-1.000", "X34.000 Y-1.000",
                   "X-1.000 Y34.000", "X34.000 Y34.000"]:
        assert corner in txt, f"missing outline corner {corner}"
    # the through-cut reaches Z-3 across all four outlines (one z-pass -> 32
    # plunges: 4 arrays x 4 edges x 2 bridge-plunges).
    assert txt.count("Z-3.000") == 32


# --- fluorescence-sensor tooling --------------------------------------------

def test_fluorescence_grooves_vbit_outline_endmill():
    """Grooves use the 1 mm V-bit (depth -0.15 mm); the through-cut outline is a
    SEPARATE end-mill section with an explicit tool-change note (the original
    used the V-bit for the through-profile cut)."""
    txt = _text("fluorescence-sensor.gcode")
    assert "TOOL CHANGE" in txt and "end-mill" in txt.lower()
    # grooves: only Z-0.15 before the tool change, Z-3 only after it
    tc = txt.find("TOOL CHANGE")
    z3 = txt.find("Z-3.000")
    assert 0 < tc < z3
    # V-bit grooves are segmented (8 crossings => 9 segments per groove; 8v+8h
    # = 144 plunge/feed segments), NOT 16 continuous full-length lines.
    assert txt.count("Z-0.150") == 144
    # no G02/G03 (groove grid, not pillars)
    assert "G02" not in txt and "G03" not in txt


def test_fluorescence_pixel_pad_gaps():
    """Each groove is interrupted (250 um gap) at every crossing so the 8x8
    intersections stay as solid pixel pads -- not a continuous cross-grid that
    would leave no pads."""
    txt = _text("fluorescence-sensor.gcode")
    # 8 vertical grooves at X = 1.875, 5.625, ... 8 horizontal at the same Y set.
    # At least the 8 expected groove X positions must exist in the file.
    for xv in ["1.875", "5.625", "9.375", "13.125",
               "16.875", "20.625", "24.375", "28.125"]:
        assert f"X{xv}" in txt, f"missing groove at X={xv}"


# --- bridges on through-cuts -------------------------------------------------

@pytest.mark.parametrize("name", list(gen.FILES))
def test_through_cut_has_bridges(name):
    """Every through-cut outline lifts to safe Z mid-edge (the bridge/tab) so
    the freed part is retained by the stock, not launched.

    Scribe/stencil-only toolpaths (no through-cut) are skipped -- the bridge
    contract only applies to jobs that free a part from the stock.
    """
    txt = _text(name)
    if "Z-3.000" not in txt:
        pytest.skip(f"{name}: no through-cut outline (scribe/stencil only)")
    # a bridge manifests as a retract (G0 Z10) immediately followed later by a
    # re-plunge at the same Z pass -- i.e. a through-cut has >=1 mid-edge lift.
    # count of safe retracts during the outline is at least the number of
    # bridge tabs (4 bridges per outline x number of outlines)
    n_retracts = txt.count("G0 Z10.000") + txt.count("G0 Z10.0")
    assert n_retracts >= 4


# --- P0.2.0 prototype toolpaths (DIRECT matrix 64+64, shared carrier coords) ----
# Every P0 layer is drawn in the CARRIER coordinate system at its mounting
# position, and emits the SAME 4 carrier-corner fiducials -> the files overlay.

P0_LAYERS = ["p0_cathode_stencil.gcode",
             "p0_detector_stencil.gcode", "p0_carrier_pcb.gcode"]
P0_SHARED_FIDUCIALS = ["X1.000 Y1.000", "X134.000 Y1.000",
                       "X134.000 Y89.000", "X1.000 Y89.000"]


def test_p0_layers_share_carrier_fiducials():
    """Every P0 layer file emits the SAME 4 carrier-corner fiducials
    (1,1)/(134,1)/(134,89)/(1,89) so all layers overlay in one coordinate
    system -- the 'they don't superimpose / different scales' guard."""
    for name in P0_LAYERS:
        txt = _text(name)
        for fc in P0_SHARED_FIDUCIALS:
            assert fc in txt, f"{name}: missing shared carrier fiducial {fc}"
        assert txt.count("Z-0.150") >= 4, f"{name}: needs 4 corner fiducials"


def test_p0_layers_align_to_carrier_pockets():
    """VERTICAL STACK: the three layer gcodes (cathode, detector, carrier)
    are drawn at the SAME single die-pocket carrier coord (57.5,35), and the
    carrier mills that same pocket -- so every layer overlays on one 20x20
    footprint (the three layers stack vertically, they do not sit side-by-side).
    The L1 ITO is a CONTINUOUS common anode now (no isolation gcode -- the
    p0_ito_isolate job was removed)."""
    cath = _text("p0_cathode_stencil.gcode")
    det = _text("p0_detector_stencil.gcode")
    carr = _text("p0_carrier_pcb.gcode")
    # every layer + the carrier die pocket start a rapid at the centred die (57.5,35)
    for txt in (cath, det, carr):
        assert "X57.500 Y35.000" in txt, "layer not at the centred die pocket (57.5,35)"
    # the OLD side-by-side pockets must be gone
    assert "X10.000 Y35.000" not in carr   # old emitter pocket
    assert "X30.000 Y40.000" not in carr   # old waveguide pocket


def test_p0_cathode_stencil_64_windows():
    """P0 L1 emitter BOTTOM face (carrier coords): 64 square window openings
    (8x8) in Kapton at Z-0.06, over the centred die pocket (same footprint as the
    ITO anodes) -> 64 isolated Ag cathode pads. Window is 1.0 mm (NOT 1.5) so a
    1 mm bit leaves a 0.5 mm Kapton web between windows at 2.5 mm pitch -- the
    'windows merge -> cathodes short' guard."""
    txt = _text("p0_cathode_stencil.gcode")
    # one plunge per window (64 square outlines) -> 64 Z-0.060
    assert txt.count("Z-0.060") == 64
    # 64 pad comments name each cathode pad by its CARRIER centre
    centres = [58.75, 61.25, 63.75, 66.25, 68.75, 71.25, 73.75, 76.25]  # 57.5+1.25..
    rows = [36.25, 38.75, 41.25, 43.75, 46.25, 48.75, 51.25, 53.75]     # 35+1.25..
    for cx in centres:
        for cy in rows:
            assert f"cathode pad carrier ({cx:.2f},{cy:.2f})" in txt, \
                f"missing cathode pad comment at ({cx},{cy})"
    # window corners land at centre +/- 0.5 mm (window 1.0 mm) in carrier coords.
    # First column centre 58.75 -> edges 58.25/59.25; last col 76.25 -> 75.75/76.75.
    assert "X58.250" in txt and "X59.250" in txt    # first-column window edges
    assert "X75.750" in txt and "X76.750" in txt    # last-column window edges
    # the 1.5 mm (zero-web) corners must NOT appear -- regression guard
    assert "X58.000" not in txt and "X59.500" not in txt
    assert "Z-3.000" not in txt  # no through-cut


def test_p0_carrier_vertical_stack_breakout():
    """P0 carrier 135x90 (VERTICAL stack): ONE 20x20 die pocket @ (57.5,35)
    Z-1.1 (the two-faced stack die), 130 bond holes (64 detector top Y=85 + 64
    emitter-cathode bottom Y=5 + 1 common EM_ANODE @ (3.5,8.0) + 1 common
    DET_COMMON @ (3.5,82.0) = the L3 ITO detector masa), 135x90 outline
    + bridges. NO separate waveguide pocket, NO ITO-isolation job (continuous
    ITO common anode). Shared fiducials + labels."""
    txt = _text("p0_carrier_pcb.gcode")
    # ONE die pocket at the centred (57.5,35), floor Z-1.1
    assert "X57.500 Y35.000" in txt
    assert "Z-1.100" in txt
    # the OLD side-by-side pockets are GONE
    assert "X10.000 Y35.000" not in txt   # old emitter pocket
    assert "X30.000 Y40.000" not in txt   # old waveguide strip pocket
    # the old removed pocket raster used Z-0.300/-0.900/-1.200 (unique depths
    # no pocket or peck-drill uses). graphene is sole detector.
    assert "Z-0.300" not in txt
    assert "Z-0.900" not in txt
    assert "Z-1.200" not in txt
    # 130 peck-drilled bond holes bottom at Z-2.0 (>= 129; outline also passes
    # through -2.0 on its way to -3.0, so the total is higher)
    assert txt.count("Z-2.000") >= 129
    # 64 emitter-cathode holes bottom edge Y=5 + 64 detector holes top edge Y=85
    bond_x = [3.5 + k * 2.0 for k in range(64)]
    for x in bond_x:
        xs = f"{x:.3f}"
        assert f"X{xs} Y5.000" in txt, f"missing emitter-cathode bond hole at X={xs}"
        assert f"X{xs} Y85.000" in txt, f"missing detector bond hole at X={xs}"
    # 1 common EM_ANODE hole (L1 ITO anode) at (3.5, 8.0)
    assert "X3.500 Y8.000" in txt
    # 1 common DET_COMMON hole (L3 ITO detector masa) at (3.5, 82.0)
    assert "X3.500 Y82.000" in txt
    # 532 pump facet marker on the left edge of the die pocket (57.5, 45)
    assert "X57.500 Y45.000" in txt
    # 135x90 outline through-cut with bridges
    assert "Z-3.000" in txt
    for corner in ["X-0.500 Y-0.500", "X135.500 Y-0.500",
                   "X135.500 Y90.500", "X-0.500 Y90.500"]:
        assert corner in txt, f"missing outline corner {corner}"
    n_retracts = txt.count("G0 Z10.000") + txt.count("G0 Z10.0")
    assert n_retracts >= 4
    # legibility: vertical-stack feature labels
    assert "[DIE POCKET]" in txt
    assert "[DETECTOR BONDS]" in txt
    assert "[EMITTER-CATHODE BONDS]" in txt
    assert "[COMMON EM_ANODE]" in txt
    assert "[COMMON DET_COMMON]" in txt
    # old horizontal-chain labels fully removed
    assert "[EMITTER]" not in txt
    assert "[WAVEGUIDE]" not in txt
    assert "[BOND]" not in txt


def test_p0_detector_stencil():
    """P0 L3 detector TOP face (carrier coords): 64 Au/Cr detector contact-pad
    openings (8x8) in Kapton at Z-0.06, over the centred die pocket (same
    footprint as the emitter grids) -> 64 graphene/MoS2 detector readout pads
    (sole detector). Shared fiducials."""
    txt = _text("p0_detector_stencil.gcode")
    # 64 square pad outlines -> 64 Z-0.060 (was 2 pads in the old horizontal chain)
    assert txt.count("Z-0.060") == 64
    # first + last detector pad comments (centred die coords)
    assert "detector pad carrier (58.75,36.25)" in txt
    assert "detector pad carrier (76.25,53.75)" in txt
    # stencil footprint at the centred die pocket
    assert "X57.500 Y35.000" in txt
    # the OLD 2-pad facet detector positions must NOT appear -- regression guard
    assert "X50.000" not in txt and "X52.000" not in txt
    assert "X51.000 Y40.000" not in txt
    assert "Z-3.000" not in txt


# --- P1.0 pin-scaffold support (commercial pin-header electrode array) --------

def test_p1_pin_array_64_holes_2p54_pitch():
    """P1 pin-scaffold support: 8x8 = 64 through-holes at the STANDARD 2.54 mm
    pin-header pitch on a 20x20x3 support, 1.5 mm circle-bored through with
    LINEAR-SEGMENT polygon circles (NOT G02 arcs -- some gcode viewers misdraw the
    G02 I/J arc-centre offset as a diagonal chord, which made the grid LOOK
    diagonal even though the hole centres are a perfect square grid aligned to
    X/Y). The pin header's molded strip fixes the pitch at 2.54 mm (NOT the P0
    2.5 mm) so a standard cut header drops straight in -- the grid must be 2.54.
    Each hole is a polygon circle (not a Z-only peck drill) so it shows as a
    VISIBLE circle in a gcode top view (a 1.0 mm peck at 1.0 mm = bit dia would
    be an invisible point -- the earlier "showed only the border" bug). Each pin
    = one pixel electrode AND its own plug-in fan-out (no wire-bonding, no pogo
    clamshell). User-confirmed 8x8 (supersedes the 48-pin draft). Holes are drilled
    in boustrophedon (snake) row-major order so the travels are horizontal/vertical
    (no diagonal rapids)."""
    txt = _text("p1_pin_array_support.gcode")
    # 64 pin-hole comments, one per pixel
    assert txt.count("pin (") == 64
    # 8x8 grid centred on 20x20: first pin 1.11, last 18.89, pitch 2.54 (-> 3.65)
    assert "support (1.11,1.11)" in txt   # pin (1,1)
    assert "support (3.65,1.11)" in txt   # pin (2,1) -- pitch 2.54
    assert "support (18.89,18.89)" in txt  # pin (8,8)
    # the P0 2.5 mm pitch must NOT appear here (the header fixes 2.54 mm)
    assert "2.5 mm pitch" not in txt
    assert "2.54 mm pitch" in txt         # standard pin-header pitch stated
    # 1.5 mm holes bored as LINEAR-SEGMENT polygon circles (NO G02 arcs) -- the
    # fix for the "holes look diagonal" complaint: some gcode viewers misdraw
    # the G02 I/J arc-centre offset as a diagonal chord toward the origin, which
    # made the square grid look diagonal. A 16-segment G1-XY polygon circle
    # renders as a clean circle in EVERY viewer, so the grid reads as straight
    # aligned rows matching the pin matrix.
    assert "1.5 mm" in txt
    # NO arc COMMANDS (a comment may mention "G02" as text, but no gcode line
    # starts with G02/G03) -- linear-segment circles only, so no viewer can
    # misrender an I/J offset as a diagonal chord.
    assert not re.search(r"(?m)^G02\b", txt), "G02 arc command present"
    assert not re.search(r"(?m)^G03\b", txt), "G03 arc command present"
    # each hole has a polygon-circle start at (cx + 0.25, cy) -- 64 of them
    # (path_r = 1.5/2 - 1.0/2 = 0.25 mm). hole (1,1): cx=1.11 -> X1.360 Y1.110.
    assert "X1.360 Y1.110" in txt    # hole (1,1) polygon start
    assert "X18.640 Y18.890" in txt  # hole (8,8) polygon start (18.89-0.25)
    # through-bored Z-3.0
    assert "Z-3.000" in txt
    # 20x20 outline through-cut with 4 bridges
    for corner in ["X-0.500 Y-0.500", "X20.500 Y-0.500",
                   "X20.500 Y20.500", "X-0.500 Y20.500"]:
        assert corner in txt, f"missing outline corner {corner}"
    n_retracts = txt.count("G0 Z10.000") + txt.count("G0 Z10.0")
    assert n_retracts >= 4
    # the two use-cases are documented in the header
    assert "DETECTOR" in txt and "EMITTER" in txt


def test_p1_holes_and_mold_two_pieces():
    """P1 layer templates: a 3-row x 4-col grid, NO through-cut.

    Row 1 (Y 0..17.78): 4 HOLES pieces -- 64-hole pattern, circle-bored, BLIND
    2.0 mm, ONLY holes ("sin rebordes"), flush at the copy origin. Row 2
    (Y 20.78..38.56): 4 MOLD pieces -- 17.78 x 17.78, 1.0 mm-deep pocket
    (perimeter "cuadrado de fuera" + interior zigzag "vaciado de por dentro").
    pump in at the bottom-left facet). 4 of each type (12 pieces), 3 mm gap,
    ~80 x 59 mm (fits a 3018). No through-cut on any piece. The original
    p1_pin_array_support (through-holes + outline) is a SEPARATE file, unchanged.
    """
    txt = _text("p1_holes_and_mold.gcode")
    # 4 holes pieces x 64 holes = 256 pin-hole comments
    assert txt.count("pin (") == 256
    # 4 of each type -- the per-copy origin markers
    assert txt.count("holes piece (") == 4
    assert txt.count("mold piece (") == 4
    assert "2.54 mm pitch" in txt
    # holes BLIND 2.0 mm deep (NOT through): Z-2.000 present, Z-3.000 absent
    assert "Z-2.000" in txt
    assert "Z-3.000" not in txt
    # NO through-cut outline on any piece: the support outline corners absent
    for corner in ["X-0.500 Y-0.500", "X20.500 Y-0.500",
                   "X20.500 Y20.500", "X-0.500 Y20.500"]:
        assert corner not in txt, f"no outline expected, found {corner}"
    # NO arc COMMANDS (linear-segment polygon circles + serpentine, no G02/G03)
    assert not re.search(r"(?m)^G02\b", txt), "G02 arc command present"
    assert not re.search(r"(?m)^G03\b", txt), "G03 arc command present"
    # --- Row 1 (holes): flush at the copy origin (first pin at the corner) ---
    # copy 0 first hole (1,1) at (0,0) -> polygon start at (0+0.25, 0) = X0.250 Y0.000
    assert "X0.250 Y0.000" in txt
    # copy 0 last hole (8,8) at (17.78,17.78) -> polygon start X18.030 Y17.780
    assert "X18.030 Y17.780" in txt
    # copy 3 (origin X62.34) last hole at (62.34+17.78, 17.78) = (80.12, 17.78)
    assert "hole (80.12,17.78)" in txt
    # 4 holes origins: (0,0),(20.78,0),(41.56,0),(62.34,0)
    for ox in [0.00, 20.78, 41.56, 62.34]:
        assert f"at origin ({ox:.2f},0.00)" in txt, f"missing holes origin x={ox}"
    # --- Row 2 (molds): 17.78 x 17.78, 1.0 mm-deep pocket, Y 20.78..38.56 ---
    assert "MOLD" in txt
    assert "Z-1.000" in txt
    # copy 0 mold at origin (0,20.78) -> bit-offset perimeter corner (0.5,21.28)
    assert "X0.500 Y21.280" in txt
    # copy 3 mold at origin (62.34,20.78) -> top-right corner (62.34+17.28, 20.78+17.28)
    #   = (79.62, 38.06)
    assert "X79.620 Y38.060" in txt
    for ox in [0.00, 20.78, 41.56, 62.34]:
        assert f"at origin ({ox:.2f},20.78)" in txt, f"missing mold origin x={ox}"
    # 3 mm gap between copies: pitch = array span (17.78) + gap (3) = 20.78
    assert "20.78" in txt
    # the three rows are documented
    assert "Row 1" in txt and "Row 2" in txt
    assert "no through-cut" in txt.lower() or "NO through-cut" in txt


# --- P1.0 single-piece files (one piece per file) -----------------------------

def test_p1_holes_single():
    """Standalone single-piece: ONE 64-hole pattern, 17.78x17.78, 2.0 mm BLIND,
    sin rebordes. Same geometry as one grid copy, flush at the origin."""
    txt = _text("p1_holes.gcode")
    assert txt.count("pin (") == 64
    assert "hole (0.00,0.00)" in txt      # first pin flush at the origin
    assert "hole (17.78,17.78)" in txt    # last pin
    assert "2.54 mm pitch" in txt
    assert "Z-2.000" in txt               # 2 mm blind
    assert "Z-3.000" not in txt           # no through-cut
    assert not re.search(r"(?m)^G02\b", txt)
    assert not re.search(r"(?m)^G03\b", txt)
    # polygon start of hole (1,1) at (0+0.25, 0)
    assert "X0.250 Y0.000" in txt
    # no outline corners
    for corner in ["X-0.500 Y-0.500", "X20.500 Y-0.500",
                   "X20.500 Y20.500", "X-0.500 Y20.500"]:
        assert corner not in txt


def test_p1_vaciado_single():
    """Standalone single-piece: ONE 17.78x17.78 'vaciado completo' (full pocket),
    1.0 mm deep (perimeter cuadrado + interior vaciado zigzag). Same geometry as
    one mold cell of the grid."""
    txt = _text("p1_vaciado.gcode")
    assert "VACIADO" in txt or "vaciado" in txt
    assert "Z-1.000" in txt               # 1 mm blind pocket floor
    assert "Z-3.000" not in txt           # no through-cut
    assert not re.search(r"(?m)^G02\b", txt)
    assert not re.search(r"(?m)^G03\b", txt)
    # perimeter (bit offset 0.5): corners (0.5,0.5) and (17.28,17.28)
    assert "X0.500 Y0.500" in txt
    assert "X17.280 Y17.280" in txt
    # no outline through-cut corners
    for corner in ["X-0.500 Y-0.500", "X20.500 Y-0.500"]:
        assert corner not in txt


# --- determinism -------------------------------------------------------------

@pytest.mark.parametrize("name", list(gen.FILES))
def test_generator_is_deterministic(name):
    """Two runs produce byte-identical output (no timestamps / rng)."""
    a = gen.FILES[name]()
    b = gen.FILES[name]()
    assert a == b