"""Footprint builders + the component/net model for the QSSC board generator.

``COMPONENTS`` is the shared, ordered list of board parts (one dict per
component: ref, lib_id, value, footprint, x, y, rot, pins, nc_pins).
``add`` appends to it; the build_* functions in :mod:`tools.kicad.placement`
populate it for one board, then ``KicadGenerator`` snapshots it. Footprint
builders load KiCad's stock footprints via ``load_footprint_mod`` and patch
pads/uuids in place.
"""
from __future__ import annotations

from pathlib import Path

from tools.sexp import A, L, S, parse, u

from ._paths import FP_LIB
from .symbols import (
    BTB_PIN_NETS, GRID_HALF, GRID_PITCH, _chip_grid_xy,
    authored_symbols,
)

__all__ = ['COMPONENTS', 'SOCKET_BODY_HALF', 'SOCKET_COMMON_X', 'SOCKET_CRTYD_HALF', 'SOCKET_MNT', 'SOCKET_MNT_DRILL', 'SOCKET_MNT_PAD', 'SOCKET_MNT_POS', 'add', 'add_btb_connector', 'build_pincoords', 'chip_footprint', 'chip_footprint_det', 'chip_footprint_em', 'chip_socket_footprint_det', 'chip_socket_footprint_em', 'conn_02x10_footprint', 'fp_fresh_uuids', 'fp_net_pads', 'fp_set_at', 'fp_set_prop', 'grad_coil_footprint', 'load_footprint_mod', 'pin_endpoint']


def _chip_fp_body(lib_id, value, descr, pads, layer="F.Cu"):
    """Common (footprint ...) wrapper for a QSSC die face.  Fab/CrtYd rects land
    on the face's own silk/fab layer; 20x20 mm die courtyard."""
    fab = "F.Fab" if layer == "F.Cu" else "B.Fab"
    crtyd = "F.CrtYd" if layer == "F.Cu" else "B.CrtYd"
    return L(A("footprint"), S(lib_id),
             L(A("layer"), S(layer)),
             L(A("version"), A(20240108)),
             L(A("generator"), S("gen_qssc_kicad")),
             L(A("generator_version"), S("8.0")),
             L(A("descr"), S(descr)),
             L(A("attr"), A("smd")),
             L(A("property"), S("Reference"), S("U"),
               L(A("at"), A(0), A(-11), A(0)),
               L(A("effects"), L(A("font"), L(A("size"), A(0.8), A(0.8))))),
             L(A("property"), S("Value"), S(value),
               L(A("at"), A(0), A(11), A(0)),
               L(A("effects"), L(A("font"), L(A("size"), A(0.8), A(0.8))))),
             L(A("fp_rect"), L(A("start"), A(-10.2), A(-10.2)), L(A("end"), A(10.2), A(10.2)),
               L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))),
               L(A("fill"), A("none")), L(A("layer"), S(fab)), L(A("uuid"), S(u()))),
             L(A("fp_rect"), L(A("start"), A(-10.5), A(-10.5)), L(A("end"), A(10.5), A(10.5)),
               L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))),
               L(A("fill"), A("none")), L(A("layer"), S(crtyd)), L(A("uuid"), S(u()))),
             *pads)


def chip_footprint_em():
    """Footprint for the die BOTTOM (emitter) face on the MAIN board F.Cu: 64 EM
    cathode pads on an 8x8 grid across the 20x20 mm face (2.54 mm pitch) +
    EM_ANODE common-anode pad on the LEFT edge.  ACF direct-contact pads
    (F.Cu + F.Mask, NO F.Paste -- the die is clamped face-down, not soldered).
    EM_ANODE is on the left edge (clear of the 8x8 grid, whose leftmost column
    is at x=-8.89) so its fanout via does not collide with the grid dog-bones."""
    def pad(name, x, y, w=1.2, h=1.2):
        return L(A("pad"), S(name), A("smd"), A("rect"),
                 L(A("at"), A(round(x, 3)), A(round(y, 3))),
                 L(A("size"), A(w), A(h)),
                 L(A("layers"), S("F.Cu"), S("F.Mask")))
    pads = [pad(f"EM{i}", *_chip_grid_xy(i)) for i in range(64)]
    pads.append(L(A("pad"), S("EM_ANODE"), A("smd"), A("rect"),
                  L(A("at"), A(-9.9), A(0)),
                  L(A("size"), A(0.5), A(1.5)),
                  L(A("layers"), S("F.Cu"), S("F.Mask"))))
    return _chip_fp_body("qssc:QSSC_Chip_EM", "QSSC_Chip_EM",
                         "QSSC die BOTTOM face: 64 CQD emitter cathodes on an 8x8 grid + common anode; 20x20 mm ACF contact pads (2.54 mm pitch) on F.Cu",
                         pads, "F.Cu")


def chip_footprint_det():
    """Footprint for the die TOP (detector) face on the DETECTOR HEAD B.Cu: 64
    DET pads on an 8x8 grid across the 20x20 mm face (2.54 mm pitch) + 1
    DET_COMMON common-back-electrode pad on the LEFT edge (the continuous ITO
    film shared by all 64 graphene pixels -- edge-contacted, mirrors EM_ANODE
    on the emitter face).  ACF direct-contact pads (B.Cu + B.Mask, NO B.Paste
    -- the head is clamped face-down onto the die, not soldered).  Pads on B.Cu
    so the head's bottom face contacts the die's top face when the two boards
    are stacked.  DET_COMMON is on the left edge (clear of the 8x8 grid, whose
    leftmost column is at x=-8.89) so its fanout via does not collide with the
    grid dog-bones."""
    def pad(name, x, y, w=1.2, h=1.2):
        return L(A("pad"), S(name), A("smd"), A("rect"),
                 L(A("at"), A(round(x, 3)), A(round(y, 3))),
                 L(A("size"), A(w), A(h)),
                 L(A("layers"), S("B.Cu"), S("B.Mask")))
    pads = [pad(f"DET{i}", *_chip_grid_xy(i)) for i in range(64)]
    pads.append(L(A("pad"), S("DET_COMMON"), A("smd"), A("rect"),
                  L(A("at"), A(-9.9), A(0)),
                  L(A("size"), A(0.5), A(1.5)),
                  L(A("layers"), S("B.Cu"), S("B.Mask"))))
    return _chip_fp_body("qssc:QSSC_Chip_DET", "QSSC_Chip_DET",
                         "QSSC die TOP face: 64 graphene detector electrodes on an 8x8 grid + DET_COMMON ITO back-electrode; 20x20 mm ACF contact pads on B.Cu (2.54 mm pitch)",
                         pads, "B.Cu")


def chip_footprint():
    """Back-compat alias: the emitter face (the merged-board view was EM+DET on
    one face; the two-faced-die split puts EM on the main board)."""
    return chip_footprint_em()


# ---- Standard 2.54 mm female-header chip SOCKET (both boards) ---------------
# The chip is a P1-style pin-scaffold module: a double-ended carrier whose 2.54 mm
# male pin-header pins ARE the 64 per-pixel electrodes (CQD emitter paste molded
# on the bottom pins, graphene/MoS2 detector paste molded on the top pins) + 1
# common electrode, exactly the P1·0 prototype approach (see
# FABRICATION_PROTOCOL_P1.md) scaled to production.  It plugs BETWEEN the two
# boards: the MAIN board carries a 2.54 mm female-header socket on F.Cu (emitter
# face, 64 EM cathodes + EM_ANODE), the DETECTOR HEAD carries the matching 2.54 mm
# female-header socket on B.Cu (detector face, 64 DET + DET_COMMON).  2.54 mm is
# the standard pin-header pitch -- the 8x8 grid spans 17.78 mm on the 20 mm body,
# and the socket is built from off-the-shelf 1x8 / 2x... female-header strips (or
# individual machined socket receptacles) on a 2.54 mm grid: an ALLPCB-fabricable
# through-hole part, NOT a custom spring-pin socket.  Pad NAMES == QSSC_Chip_EM
# / QSSC_Chip_DET so chip_net_map_em/det() net every contact identically and
# route_signals._chip_fanout dog-bones them with the SAME escape geometry as the
# ACF die footprints.  The EM_ANODE / DET_COMMON common pad sits ONE GRID STEP
# left of the 8x8 grid (x = -GRID_HALF - GRID_PITCH = -11.43) so a standard 1.7 mm
# THT pad clears the leftmost grid column (0.84 mm edge-to-edge).  4 NPTH M2
# mount holes at the corners clamp + align the sandwich (head standoffs thread
# through these).
SOCKET_BODY_HALF = 12.5     # 25x25 mm body outline (+2.5 mm/side over the 20 mm die)
SOCKET_CRTYD_HALF = 13.5    # courtyard half-extent (contains body + 4 NPTH pads)
SOCKET_MNT = 11.5           # M2 mount-hole centre offset (just inside body corners)
SOCKET_MNT_DRILL = 2.5      # M2 screw clearance drill
SOCKET_MNT_PAD = 3.5        # NPTH keep-out pad diameter
SOCKET_MNT_POS = [(-SOCKET_MNT, -SOCKET_MNT), (+SOCKET_MNT, -SOCKET_MNT),
                  (-SOCKET_MNT, +SOCKET_MNT), (+SOCKET_MNT, +SOCKET_MNT)]
# common-electrode pad sits one 2.54 mm grid step left of the 8x8 grid's leftmost
# column (clears a 1.7 mm THT pad by 0.84 mm edge-to-edge).
SOCKET_COMMON_X = round(-GRID_HALF - GRID_PITCH, 3)   # -11.43 mm


def _chip_socket_pad(name, x, y):
    """One 2.54 mm female-header through-hole pad: 1.7 mm circle, 1.0 mm drill
    (standard for a 0.64 mm square pin-header / female-socket pin).  Plated
    through-hole on *.Cu + *.Mask so it solders from the opposite face from the
    socket body."""
    return L(A("pad"), S(name), A("thru_hole"), A("circle"),
             L(A("at"), A(round(x, 3)), A(round(y, 3))),
             L(A("size"), A(1.7), A(1.7)),
             L(A("drill"), A(1.0)),
             L(A("layers"), S("*.Cu"), S("*.Mask")))


def _chip_socket_body(lib_id, value, descr, pads, layer="F.Cu"):
    """(footprint ...) wrapper for a 2.54 mm female-header chip socket.  Fab /
    silk / courtyard land on the socket's own face layer (F.* for the main board
    emitter socket on F.Cu, B.* for the head detector socket on B.Cu).
    through_hole attr (off-the-shelf THT female-header part)."""
    fab = "F.Fab" if layer == "F.Cu" else "B.Fab"
    silk = "F.SilkS" if layer == "F.Cu" else "B.SilkS"
    crtyd = "F.CrtYd" if layer == "F.Cu" else "B.CrtYd"
    b, cy = SOCKET_BODY_HALF, SOCKET_CRTYD_HALF
    return L(A("footprint"), S(lib_id),
             L(A("layer"), S(layer)),
             L(A("version"), A(20240108)),
             L(A("generator"), S("gen_qssc_kicad")),
             L(A("generator_version"), S("8.0")),
             L(A("descr"), S(descr)),
             L(A("attr"), A("through_hole")),
             L(A("property"), S("Reference"), S("U"),
               L(A("at"), A(0), A(-cy), A(0)),
               L(A("effects"), L(A("font"), L(A("size"), A(0.8), A(0.8))))),
             L(A("property"), S("Value"), S(value),
               L(A("at"), A(0), A(cy), A(0)),
               L(A("effects"), L(A("font"), L(A("size"), A(0.8), A(0.8))))),
             L(A("fp_rect"), L(A("start"), A(-b), A(-b)), L(A("end"), A(b), A(b)),
               L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))),
               L(A("fill"), A("none")), L(A("layer"), S(fab)), L(A("uuid"), S(u()))),
             L(A("fp_rect"), L(A("start"), A(-b), A(-b)), L(A("end"), A(b), A(b)),
               L(A("stroke"), L(A("width"), A(0.12)), L(A("type"), A("solid"))),
               L(A("fill"), A("none")), L(A("layer"), S(silk)), L(A("uuid"), S(u()))),
             L(A("fp_rect"), L(A("start"), A(-cy), A(-cy)), L(A("end"), A(cy), A(cy)),
               L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))),
               L(A("fill"), A("none")), L(A("layer"), S(crtyd)), L(A("uuid"), S(u()))),
             *pads)


def _socket_mnt_pads():
    """4 NPTH M2 corner mount holes (solder-less hardware clamp/alignment; no
    net, no symbol pin)."""
    pads = []
    for mx, my in SOCKET_MNT_POS:
        pads.append(L(A("pad"), S(""), A("np_thru_hole"), A("circle"),
                      L(A("at"), A(round(mx, 3)), A(round(my, 3))),
                      L(A("size"), A(SOCKET_MNT_PAD), A(SOCKET_MNT_PAD)),
                      L(A("drill"), A(SOCKET_MNT_DRILL)),
                      L(A("layers"), S("*.Cu"), S("*.Mask"))))
    return pads


def chip_socket_footprint_em():
    """Standard 2.54 mm female-header chip SOCKET for the die EMITTER (bottom)
    face on the MAIN board F.Cu.  64 EM cathode through-hole pads on the 8x8 grid
    (2.54 mm pitch, 17.78 mm span) + EM_ANODE common-anode pad one grid step left
    of the grid, all 1.7 mm / 1.0 mm THT for a standard female-header strip /
    machined socket.  The P1-style pin-scaffold chip module plugs its male emitter
    pins DOWN into this socket.  4 NPTH M2 corner mount holes clamp + align the
    sandwich.  Pad NAMES == QSSC_Chip_EM so chip_net_map_em() nets every contact
    identically and route_signals._chip_fanout dog-bones them with the SAME
    escape geometry as the ACF die footprint."""
    pads = [_chip_socket_pad(f"EM{i}", *_chip_grid_xy(i)) for i in range(64)]
    pads.append(_chip_socket_pad("EM_ANODE", SOCKET_COMMON_X, 0))
    pads.extend(_socket_mnt_pads())
    return _chip_socket_body(
        "qssc:ChipSocket_2.54_EM", "ChipSocket_2.54_EM",
        "Standard 2.54 mm female-header chip socket for the QSSC die EMITTER (bottom) face on the main board F.Cu; 64 EM cathode + EM_ANODE through-hole pads on a 2.54 mm 8x8 grid (17.78 mm span) + 4 NPTH M2 mount holes; the P1-style pin-scaffold chip module plugs its male emitter pins into this socket",
        pads, "F.Cu")


def chip_socket_footprint_det():
    """Standard 2.54 mm female-header chip SOCKET for the die DETECTOR (top) face
    on the DETECTOR HEAD B.Cu.  64 DET through-hole pads on the 8x8 grid (2.54 mm
    pitch) + DET_COMMON common-back-electrode pad one grid step left of the grid,
    all 1.7 mm / 1.0 mm THT.  The socket body sits on B.Cu (the head's bottom
    face, which faces the chip); the P1-style pin-scaffold chip module plugs its
    male detector pins UP into it.  Solder-side is F.Cu (the head's top face) --
    the F.Cu area directly over the 8x8 grid must stay clear of SMD parts for
    through-hole soldering access (verified during placement regen).  4 NPTH M2
    corner mount holes.  Pad NAMES == QSSC_Chip_DET so chip_net_map_det() nets
    every contact identically."""
    pads = [_chip_socket_pad(f"DET{i}", *_chip_grid_xy(i)) for i in range(64)]
    pads.append(_chip_socket_pad("DET_COMMON", SOCKET_COMMON_X, 0))
    pads.extend(_socket_mnt_pads())
    return _chip_socket_body(
        "qssc:ChipSocket_2.54_DET", "ChipSocket_2.54_DET",
        "Standard 2.54 mm female-header chip socket for the QSSC die DETECTOR (top) face on the detector head B.Cu; 64 DET + DET_COMMON through-hole pads on a 2.54 mm 8x8 grid (17.78 mm span) + 4 NPTH M2 mount holes; the P1-style pin-scaffold chip module plugs its male detector pins into this socket",
        pads, "B.Cu")


def conn_02x10_footprint():
    """2x10 2.54 mm through-hole pin-header footprint.  THT pads on *.Cu +
    *.Mask (1.0 mm drill, 1.6 mm pad); pin 1 square.  One footprint serves both
    HOST (header) and DAUGHTER (socket) -- THT is symmetric, the two sides differ
    only in the value string (BTB_HOST / BTB_DAUGHTER)."""
    def pad(name, x, y, square=False):
        return L(A("pad"), S(name), A("thru_hole"),
                 A("rect" if square else "circle"),
                 L(A("at"), A(round(x, 3)), A(round(y, 3))),
                 L(A("size"), A(1.6), A(1.6)),
                 L(A("drill"), A(1.0)),
                 L(A("layers"), S("*.Cu"), S("*.Mask")))
    pads = []
    for i in range(20):
        col = i % 2
        row = i // 2
        x = -2.54 if col == 0 else 2.54
        y = round(11.43 - row * 2.54, 3)
        pads.append(pad(str(i + 1), x, y, square=(i == 0)))
    return L(A("footprint"), S("qssc:Conn_02x10_BTB"),
             L(A("layer"), S("F.Cu")),
             L(A("version"), A(20240108)),
             L(A("generator"), S("gen_qssc_kicad")),
             L(A("generator_version"), S("8.0")),
             L(A("descr"), S("Board-to-board 2x10 2.54mm pin header: 9-wire AD7616 SPI + 3V3/5V/GND between the main board and the detector head")),
             L(A("attr"), A("through_hole")),
             L(A("property"), S("Reference"), S("J"), L(A("at"), A(0), A(-13.5), A(0)),
               L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
             L(A("property"), S("Value"), S("Conn_02x10_BTB"), L(A("at"), A(0), A(13.5), A(0)),
               L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
             L(A("fp_rect"), L(A("start"), A(-3.81), A(-12.7)), L(A("end"), A(3.81), A(12.7)),
               L(A("stroke"), L(A("width"), A(0.1)), L(A("type"), A("solid"))),
               L(A("fill"), A("none")), L(A("layer"), S("F.Fab")), L(A("uuid"), S(u()))),
             L(A("fp_rect"), L(A("start"), A(-4.1), A(-12.95)), L(A("end"), A(4.1), A(12.95)),
               L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))),
               L(A("fill"), A("none")), L(A("layer"), S("F.CrtYd")), L(A("uuid"), S(u()))),
             *pads)


def grad_coil_footprint():
    """Field-gradient coil -- a real PCB copper trace on F.Cu
    running along one edge of the die socket.  Two SMD pads (1 = GRAD_COIL_P
    feed, 2 = coil return GRAD_COIL_N -> MOSFET drain) sit 20 mm apart; a
    filled 1.5 mm fp_poly on F.Cu between them IS the manufactured coil trace
    (2 oz Cu carries the ~2.2 A clean-addressing current).

    This is a KiCad *net-tie* footprint (net_tie_pad_groups "1, 2"): the two
    pads sit on DIFFERENT nets (GRAD_COIL_P and GRAD_COIL_N) and the footprint-
    internal F.Cu fp_poly bridges them.  KiCad's DRC allows the bridge because
    the pads are declared tied, and -- critically -- the tie is the ONLY point
    the two nets join, so the MOSFET drain (GRAD_COIL_N) cannot reach the +5V
    plane except through the coil trace.  +5V feeds GRAD_COIL_P through a
    separate 0-ohm resistor (Rgrad_feed) so the +5V plane and GRAD_COIL_N stay
    distinct nets (a direct +5V-to-pad1 tie would merge +5V with GRAD_COIL_N
    and let the MOSFET bypass the coil via the +5V plane -- a short).  Pattern
    copied from KiCad's canonical NetTie.pretty footprints."""
    def pad(name, x):
        # bare F.Cu copper pads (no paste -- nothing is soldered here; the
        # fp_poly is the "component"). Routes tie to the pad copper directly.
        return L(A("pad"), S(name), A("smd"), A("rect"),
                 L(A("at"), A(x), A(0)),
                 L(A("size"), A(2.0), A(2.0)),
                 L(A("layers"), S("F.Cu"), S("F.Mask")))
    return L(A("footprint"), S("qssc:GradCoil"),
             L(A("layer"), S("F.Cu")),
             L(A("version"), A(20240108)),
             L(A("generator"), S("gen_qssc_kicad")),
             L(A("generator_version"), S("8.0")),
             L(A("descr"), S("Field-gradient coil: 1.5 mm PCB copper trace on F.Cu along the die socket edge (~2.2 A); KiCad net-tie (pads 1,2 tied by the fp_poly)")),
             L(A("attr"), A("smd"), A("exclude_from_pos_files"),
               A("allow_missing_courtyard")),
             # net tie: pads 1,2 are joined by the footprint's own F.Cu fp_poly;
             # DRC allows the inter-pad copper AND the two nets stay joined only here.
             L(A("net_tie_pad_groups"), S("1, 2")),
             L(A("property"), S("Reference"), S("L"), L(A("at"), A(0), A(-4), A(0)),
               L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
             L(A("property"), S("Value"), S("GradCoil"), L(A("at"), A(0), A(4), A(0)),
               L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
             # the coil trace: a 20 mm x 1.5 mm filled F.Cu polygon pad-centre to
             # pad-centre. This IS the manufactured 2-oz copper coil -- not a fab
             # drawing.  As a footprint-internal copper item in a net-tie group it
             # is DRC-exempt from clearance checks against the tied pads.
             L(A("fp_poly"),
               L(A("pts"),
                 L(A("xy"), A(-10.0), A(-0.75)),
                 L(A("xy"), A(10.0), A(-0.75)),
                 L(A("xy"), A(10.0), A(0.75)),
                 L(A("xy"), A(-10.0), A(0.75))),
               L(A("stroke"), L(A("width"), A(0)), L(A("type"), A("solid"))),
               L(A("fill"), A("solid")),
               L(A("layer"), S("F.Cu")), L(A("uuid"), S(u()))),
             # Fab outline (documents the coil geometry for the layout drawing)
             L(A("fp_rect"), L(A("start"), A(-12.0), A(-2.0)), L(A("end"), A(12.0), A(2.0)),
              L(A("stroke"), L(A("width"), A(0.1)), L(A("type"), A("solid"))),
              L(A("fill"), A("none")), L(A("layer"), S("F.Fab")), L(A("uuid"), S(u()))),
             L(A("fp_rect"), L(A("start"), A(-12.5), A(-2.5)), L(A("end"), A(12.5), A(2.5)),
              L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))),
              L(A("fill"), A("none")), L(A("layer"), S("F.CrtYd")), L(A("uuid"), S(u()))),
             pad("1", -10.0), pad("2", 10.0))


def add_btb_connector(side):
    """Add J12 -- the board-to-board connector carrying the 9-wire AD7616 SPI +
    power between the main board (side='host') and the detector head
    (side='daughter').  Both sides use identical net names (BTB_PIN_NETS); the
    mating pins bridge the two boards' same-named nets."""
    val = "BTB_HOST" if side == "host" else "BTB_DAUGHTER"
    add("J12", "qssc:Conn_02x10_BTB", val, "qssc:Conn_02x10_BTB",
        0, 0, pins=dict(BTB_PIN_NETS))




# authored pin coords lookup (lib_id -> {pin_number: (x,y,rot)})
_PINCOORDS = {}


def build_pincoords():
    for s in authored_symbols():
        lib_id = s[1][1]  # the S("lib:name") -> ("str", "lib:name")
        # find pins in the _0_1 sub-symbol
        coords = {}
        for child in s:
            if isinstance(child, list) and child and child[0] == ("atom", "symbol") and \
               child[1][1].endswith("_0_1"):
                for sub in child:
                    if isinstance(sub, list) and sub and sub[0] == ("atom", "pin"):
                        # (pin etype style (at x y rot) (length L) (name ...) (number "n" ...))
                        at = [c for c in sub if isinstance(c, list) and c[0] == ("atom", "at")][0]
                        num = [c for c in sub if isinstance(c, list) and c[0] == ("atom", "number")][0]
                        coords[num[1][1]] = (float(at[1][1]), float(at[2][1]), int(at[3][1]))
        _PINCOORDS[lib_id] = coords
    # power symbols: single pin "1" at (0,0)
    for n in ["+3V3", "+1V8", "+1V0", "+5V", "+5V_A", "+5V_RF", "+12V_EMIT", "GND",
              "PWR_FLAG"]:
        _PINCOORDS[f"power:{n}"] = {"1": (0.0, 0.0, 90 if n != "GND" else 270)}


# --------------------------------------------------------------------------
# Component + net model
# --------------------------------------------------------------------------
# Each component: (ref, lib_id, value, footprint, x, y, rot, pins={pinnum: net})
COMPONENTS = []


def add(ref, lib_id, value, fp, x, y, rot=0, pins=None, nc_pins=None):
    COMPONENTS.append(dict(ref=ref, lib_id=lib_id, value=value, fp=fp,
                           x=x, y=y, rot=rot, pins=pins or {},
                           nc_pins=list(nc_pins) if nc_pins else []))


def pin_endpoint(comp, pinnum):
    """Absolute (x,y,rot) of a pin endpoint for a placed symbol.

    KiCad symbol-internal coordinates are y-up while the schematic sheet is
    y-down, so a pin with local (lx, ly) placed at symbol (cx, cy) lands at
    sheet position (cx + lx, cy - ly).  Using cy + ly (no flip) places every
    label/power-symbol on the wrong pin for any pin with ly != 0, which
    silently corrupts net connectivity.  Verified empirically against kicad-cli
    ERC (vertical pins report at the flipped y; horizontal pins have ly=0)."""
    pc = _PINCOORDS[comp["lib_id"]][pinnum]
    return (comp["x"] + pc[0], comp["y"] - pc[1], pc[2])




# --------------------------------------------------------------------------
# PCB emitter
# --------------------------------------------------------------------------
def load_footprint_mod(fp_lib_id):
    """Read a .kicad_mod from the system lib, return parsed (footprint ...) list."""
    if fp_lib_id == "qssc:QSSC_Chip_EM":
        return chip_footprint_em()          # die bottom/emitter face (main F.Cu)
    if fp_lib_id == "qssc:QSSC_Chip_DET":
        return chip_footprint_det()         # die top/detector face (head B.Cu)
    if fp_lib_id == "qssc:QSSC_Chip":       # back-compat alias
        return chip_footprint_em()
    if fp_lib_id == "qssc:Conn_02x10_BTB":  # board-to-board connector (authored)
        return conn_02x10_footprint()
    if fp_lib_id == "qssc:GradCoil":        # Gradient coil trace (authored)
        return grad_coil_footprint()
    if fp_lib_id == "qssc:ChipSocket_2.54_EM":  # 2.54 mm female-header emitter socket (authored)
        return chip_socket_footprint_em()
    if fp_lib_id == "qssc:ChipSocket_2.54_DET":  # 2.54 mm female-header detector socket (authored)
        return chip_socket_footprint_det()
    lib, name = fp_lib_id.split(":", 1)
    p = Path(FP_LIB) / f"{lib}.pretty" / f"{name}.kicad_mod"
    return parse(p.read_text())


def fp_set_at(fp, x, y, rot):
    # remove existing top-level (at ...) and insert after (footprint "name") + layer
    fp = [c for c in fp if not (isinstance(c, list) and c and c[0] == ("atom", "at"))]
    # insert (at x y rot) as 4th element (after footprint name, layer, uuid?)
    insert_at = 1
    # find layer / uuid positions
    for i, c in enumerate(fp[1:], 1):
        if isinstance(c, list) and c and c[0] in (("atom", "layer"), ("atom", "uuid")):
            insert_at = i + 1
    fp.insert(insert_at, L(A("at"), A(x), A(y), A(rot)))
    return fp


def fp_set_prop(fp, name, value):
    for c in fp:
        if isinstance(c, list) and c and c[0] == ("atom", "property") and c[1][1] == name:
            c[2] = S(value)
    return fp


def fp_net_pads(fp, netmap, net_index):
    """Insert (net idx "name") into each pad whose name is in netmap."""
    for c in fp:
        if isinstance(c, list) and c and c[0] == ("atom", "pad"):
            padname = c[1][1]
            if padname in netmap:
                net = netmap[padname]
                idx = net_index[net]
                # remove existing net
                c[:] = [x for x in c if not (isinstance(x, list) and x and x[0] == ("atom", "net"))]
                # insert (net idx "name") right after the pad head (after pad name, type, shape)
                # find position after (layers ...) if present else after (size ...)
                ins = 4  # after (pad "n" type shape)
                # place after layers block if exists
                for j, x in enumerate(c[2:], 2):
                    if isinstance(x, list) and x and x[0] == ("atom", "layers"):
                        ins = j + 1
                        break
                c.insert(ins, L(A("net"), A(idx), S(net)))
    return fp


def fp_fresh_uuids(fp):
    """Replace every ``(uuid ...)`` value inside this footprint instance with a
    fresh uuid4.

    A ``.kicad_mod`` template carries fixed uuids (one on the footprint itself,
    one per pad, one per property). ``load_footprint_mod`` returns that same
    parsed tree for every component, so placing a footprint N times without this
    pass yields N pads -- across different footprints -- that share one uuid.
    KiCad's DRC spatial index corrupts on those duplicates and starts reporting
    PHANTOM clearance violations between unrelated nets (e.g. a DET_IN2 fanout
    stub "clearing" a TIA output pad 32 mm away). Connectivity is by NET +
    endpoint coincidence, NOT by uuid, so regenerating uuids cannot change the
    netlist -- it only restores unique identity. See memory
    kicad-cli-drc-stale-zone-pour / the 4032-dup chip_module audit.
    """
    def _walk(node):
        for c in node:
            if isinstance(c, list) and c:
                if c[0] == ("atom", "uuid") and len(c) >= 2:
                    c[1] = S(u())
                else:
                    _walk(c)
    _walk(fp)
    return fp


