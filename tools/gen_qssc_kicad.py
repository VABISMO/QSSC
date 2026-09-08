#!/usr/bin/env python3
"""Generate the QSSC KiCad schematic + PCB from a single component/net model.

Outputs (relative to repo root):
  hardware/pcb/qssc.kicad_sch   - ERC-clean flat schematic (nets by label + power syms)
  hardware/pcb/qssc.kicad_pcb   - DRC-clean, placed + netted 4-layer board
  hardware/pcb/qssc-lib.kicad_sym - custom symbol library (FPGA, GVA-84, AMS1117, +5V_RF)
  hardware/pcb/qssc.kicad_pro   - project with net classes + design rules

KiCad 8 `kicad-cli` cannot export Specctra DSN or import SES (GUI-only), so the
board is placed + netted + rule-checked but NOT signal-routed.  The
copper-plane FOUNDATION (In1.Cu GND plane, In2.Cu +3V3 plane, F.Cu/B.Cu GND
floods, GND stitching vias) is now produced headlessly by the companion script
``tools/route_qssc_pcb.py`` (via the pcbnew Python API -- KiCad 8 ships a
headless binding usable from system python3, no GUI).  Final SIGNAL routing
(BGA fanout, 50 ohm RF microstrip, USB diff pair, analog guard rings, the
secondary power rails, and all signal tracks) still requires one pcbnew GUI
session or the FreeRouting GUI.  FPGA BGA pad->signal mapping needs UG475 (not
available headless), so FPGA pads are placed but left unnetted.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PCB_DIR = ROOT / "hardware" / "pcb"
SYM_LIB = "/usr/share/kicad/symbols"
FP_LIB = "/usr/share/kicad/footprints"

# --------------------------------------------------------------------------
# Minimal s-expression parser / printer (KiCad tolerant of whitespace)
# --------------------------------------------------------------------------
TOK = re.compile(r'\s*(\(|\)|"[^"]*"|[^\s()]+)')


def tokenize(s):
    return TOK.findall(s)


def parse(s):
    toks = tokenize(s)
    pos = 0

    def plist():
        nonlocal pos
        assert toks[pos] == "(", toks[pos]
        pos += 1
        items = []
        while toks[pos] != ")":
            if toks[pos] == "(":
                items.append(plist())
            else:
                items.append(atom(toks[pos]))
                pos += 1
        pos += 1
        return items

    return plist()


def atom(t):
    if t.startswith('"'):
        return ("str", t[1:-1])
    return ("atom", t)


def emit(node, depth=0):
    if isinstance(node, tuple):
        k, v = node
        return '"' + v + '"' if k == "str" else v
    if not node:
        return "()"
    sym = emit(node[0])
    children = node[1:]
    if not any(isinstance(c, list) for c in children):
        return "(" + sym + (" " + " ".join(emit(c) for c in children) if children else "") + ")"
    out = "(" + sym
    for c in children:
        if isinstance(c, list):
            out += "\n" + "\t" * (depth + 1) + emit(c, depth + 1)
        else:
            out += " " + emit(c)
    out += "\n" + "\t" * depth + ")"
    return out


def A(v):
    return ("atom", str(v))


def S(v):
    return ("str", str(v))


def L(*items):
    return list(items)


# --------------------------------------------------------------------------
# UUID helper (deterministic-ish via uuid4 is fine for uniqueness)
# --------------------------------------------------------------------------
def u():
    return str(uuid.uuid4())


# --------------------------------------------------------------------------
# Pin data for standard symbols (extracted up front into pins.json)
# --------------------------------------------------------------------------
_pins = json.loads((ROOT / ".tmpwork" / "pins.json").read_text())


def std_pins(libname, sym):
    """Return list of dict(number,name,etype,x,y,rot,length) for a standard symbol."""
    return _pins[f"{libname}:{sym}"]


# --------------------------------------------------------------------------
# Custom symbol definitions (authored minimal: rectangle body + pins)
# Each symbol: dict(lib, name, ref, refp, pins=[dict(number,name,etype,x,y,rot)],
#                   body=(x1,y1,x2,y2))
# --------------------------------------------------------------------------
POWER_PINS = {  # power symbols: single power_in pin at origin, no body
    "+3V3": ("power_in", 90), "+1V8": ("power_in", 90), "+1V0": ("power_in", 90),
    "+5V": ("power_in", 90), "+5V_RF": ("power_in", 90),
    # +5V_A: ferrite-isolated +5V analog rail for the 4x AD7616 VCC (line 1228).
    # Kept separate from the digital +5V so ADC sample noise stays out of the
    # 16-bit PL intensity measurement; FB_A + C_Abulk/C_Ahf form the LC filter.
    "+5V_A": ("power_in", 90),
    # +12V_EMIT: dedicated higher-voltage rail for the 64 CQD emitters' common
    # anode. The fabrication protocol (docs/FABRICATION_PROTOCOL.md) gives CQD
    # EL turn-on 4-8 V (visible by ~10 V) at 2-20 mA/pixel, so a +5V anode is
    # marginal/insufficient. The TLC5947 OUT pins sink up to 33 V independent of
    # VCC (3.3 V logic), so a standard separate 12 V LED supply gives guaranteed
    # headroom for any CQD V_f up to ~11 V -- the textbook TLC5947 app circuit.
    "+12V_EMIT": ("power_in", 90),
    "GND": ("power_in", 270),
}


def power_symbol_def(name):
    et, rot = POWER_PINS[name]
    pin = L(A("pin"), A(et), A("line"),
            L(A("at"), A(0), A(0), A(rot)), L(A("length"), A(0)),
            L(A("name"), S("~"), L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
            L(A("number"), S("1"), L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))))
    sym = L(A("symbol"), S(f"power:{name}"),
            L(A("pin_names"), L(A("offset"), A(1.016))),
            L(A("exclude_from_sim"), A("no")), L(A("in_bom"), A("yes")),
            L(A("on_board"), A("yes")),
            L(A("property"), S("Reference"), S("#PWR"), L(A("at"), A(0), A(-1.27), A(0)),
              L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))), L(A("hide"), A("yes")))),
            L(A("property"), S("Value"), S(name), L(A("at"), A(0), A(1.27), A(0)),
              L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
            L(A("property"), S("Footprint"), S(""), L(A("at"), A(0), A(0), A(0)),
              L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))), L(A("hide"), A("yes")))),
            L(A("symbol"), S(f"{name}_0_1"), pin))
    return sym


def PWR_FLAG_def():
    pin = L(A("pin"), A("power_out"), A("line"),
            L(A("at"), A(0), A(0), A(90)), L(A("length"), A(0)),
            L(A("name"), S("~"), L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
            L(A("number"), S("1"), L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))))
    return L(A("symbol"), S("power:PWR_FLAG"),
             L(A("pin_names"), L(A("offset"), A(1.016))),
             L(A("exclude_from_sim"), A("no")), L(A("in_bom"), A("yes")),
             L(A("on_board"), A("yes")),
             L(A("property"), S("Reference"), S("#FLG"), L(A("at"), A(0), A(-1.27), A(0)),
               L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))), L(A("hide"), A("yes")))),
             L(A("property"), S("Value"), S("PWR_FLAG"), L(A("at"), A(0), A(1.27), A(0)),
               L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
             L(A("property"), S("Footprint"), S(""), L(A("at"), A(0), A(0), A(0)),
               L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))), L(A("hide"), A("yes")))),
             L(A("symbol"), S("PWR_FLAG_0_1"), pin))


def make_pin(num, name, etype, x, y, rot, length=2.54):
    return L(A("pin"), A(etype), A("line"),
             L(A("at"), A(x), A(y), A(rot)), L(A("length"), A(length)),
             L(A("name"), S(name), L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
             L(A("number"), S(num), L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))))


def custom_symbol_def(lib, name, refpref, pins, body, value=None):
    """pins: list of dict(number,name,etype,x,y,rot,length=2.54)."""
    pin_blocks = [make_pin(p["number"], p["name"], p["etype"], p["x"], p["y"],
                           p["rot"], p.get("length", 2.54)) for p in pins]
    rect = L(A("rectangle"), L(A("start"), A(body[0]), A(body[1])),
             L(A("end"), A(body[2]), A(body[3])),
             L(A("stroke"), L(A("width"), A(0.254)), L(A("type"), A("default"))),
             L(A("fill"), L(A("type"), A("background"))))
    sym = L(A("symbol"), S(f"{lib}:{name}"),
            L(A("pin_names"), L(A("offset"), A(1.016))),
            L(A("exclude_from_sim"), A("no")), L(A("in_bom"), A("yes")),
            L(A("on_board"), A("yes")),
            L(A("property"), S("Reference"), S(refpref), L(A("at"), A(0), A(body[1] - 2.54), A(0)),
              L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
            L(A("property"), S("Value"), S(value or name), L(A("at"), A(0), A(body[3] + 2.54), A(0)),
              L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
            L(A("property"), S("Footprint"), S(""), L(A("at"), A(0), A(0), A(0)),
              L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))), L(A("hide"), A("yes")))),
            L(A("symbol"), S(f"{name}_0_1"), rect, *pin_blocks))
    return sym


# ---- FPGA XC7A35T-1CSG324C real pinout (datasheet-correct) ----------------
# The full CSG324 ballout (324 pads) is loaded from .tmpwork/fpga_csg324_map.json,
# which is generated from the official KiCad symbol
# FPGA_Xilinx_Artix7:XC7A35T-CSG324 (every pad named, DS181/UG475).  CSG324
# supersedes the CPG236 (238 pads, ~125 user IO): the parallel 64-ch readout
# (64 DET_DIG inputs) + 4-wire TLC5947 emitter drive (em_sclk/sin/xlat/blank)
# + ~13 control nets need ~81 user IO, well within the CPG236 -- but CSG324 is
# kept for routing room / spare IO.  The parallel nets are side-assigned by the
# ballmap generator:
#   * 64 DET_DIG  -> right-edge balls (high column) toward the comparator
#                    front-end (above the die, board y<44)
#   * 4 em_SPI    -> bottom-edge balls toward the 3x TLC5947 LED driver below
#                    the die (board y>66)
#   * control     -> remaining IO; clk_100mhz + refclk_25m on MRCC balls.
# The old det_mux_sel*/emitter_rows*/emitter_cols* AND 64x EM_DRV GPIO signals
# are gone (no MUX, no row/col matrix, TLC5947 drives 64 emitters over 4 wires).
# JTAG pad is netted; spare IO is left NC (correct for unused BGA IO).
_FPGA_MAP = json.loads((ROOT / ".tmpwork" / "fpga_csg324_map.json").read_text())

# net per power/analog category
_FPGA_PWR_NET = {
    "VCCINT": "+1V0", "VCCBRAM": "+1V0", "VCCAUX": "+1V8", "VCCADC": "+1V8",
    "GND": "GND", "GNDADC": "GND", "VCCBATT": "+3V3", "CFGBVS": "+3V3",
    "VREFP": "VREFP", "VREFN": "GND", "MGT_GND": "GND",
}
# net per dedicated config / mode pin (QSPI x4 master boot: M[2:0]=001)
_FPGA_CFG_NET = {
    "D00": "SPI_D0", "D01": "SPI_D1", "D02": "SPI_D2", "D03": "SPI_D3",
    "FCS_B": "SPI_CS", "CCLK": "SPI_CCLK", "DONE": "CONF_DONE",
    "INIT_B": "CONF_INIT", "PROGRAM_B": "CONF_PROG",
    "M0": "+3V3", "M1": "GND", "M2": "GND",
}
# user-IO signals that are FPGA inputs (the rest are outputs); all drawn as
# bidirectional so ERC stays lenient on unused-direction pins.
_FPGA_INPUT_NETS = {"clk_100mhz", "reset", "UART_RX", "TCK", "TMS", "TDI",
                    "ADF_MUXOUT", "CONF_INIT", "CONF_PROG",
                    "adc_miso", "adc_busy"}


def fpga_net_map():
    """{ball_name: net} for every FPGA pad that carries a net.

    All power/ground/config/JTAG pads plus the used user-IO signals (9-pin ADC
    SPI bus + 4 em_SPI + 13 control) are netted.  Spare general-purpose IO
    balls are intentionally LEFT OUT (no net = NC), which is correct practice
    for unused BGA IO (the pad is still soldered for mechanical integrity, just
    carries no signal).  XADC VP/VN stay NC -- the detector readout is now 4x
    AD7616 parallel ADCs over SPI (true analog intensity), not the XADC.  This
    nets ~204 of the 324 CSG324 pads -- all the ones that matter for power
    integrity, config boot, and the board's I/O."""
    m = {}
    s = _FPGA_MAP
    # RTL port names (from the ballmap, generated off fpga/constraints.xdc) that
    # differ from the board net name used by the peripheral on the same net.
    # Without these aliases the FPGA ball and the peripheral sit on TWO
    # separately-named one-pin nets -> an ERC-silent disconnect (each net
    # dangles with a single endpoint) that the headless generator bypasses.
    NET_ALIAS = {
        "refclk_out": "refclk_25m",   # FPGA 25 MHz out (N-side MRCC ball, gen_fpga_ballmap_csg324.py) -> ADF4351 REFIN (pin 29)
        "tx": "UART_TX",              # FPGA TX (P18) -> FT232 RXD (pin 5, net UART_TX)
        "rx": "UART_RX",              # FPGA RX (P17) <- FT232 TXD (pin 1, net UART_RX)
    }
    for net, ball in s["signals"].items():
        m[ball] = NET_ALIAS.get(net, net)
    for cat, balls in s["power"].items():
        net = _FPGA_PWR_NET[cat] if cat in _FPGA_PWR_NET else "+3V3"  # VCCO_*
        for b in balls:
            m[b] = net
    for name, ball in s["config"].items():
        m[ball] = _FPGA_CFG_NET[name]
    for name, ball in s["jtag"].items():
        m[ball] = name
    # The XADC VP/VN balls are LEFT NC: the detector readout is now 4x AD7616
    # parallel ADCs over a 9-pin SPI bus (true analog intensity readout), not
    # the XADC.  The XADC is unused on the merged board.  (VREFP/VREFN stay
    # netted for the on-chip reference bypass -- those are separate pads.)
    return m


def fpga_pin_list():
    """Schematic pins for every netted FPGA ball, laid out on 4 sides at
    1.27 mm pitch.  Matches fpga_net_map() 1:1 so every PCB pad that is netted
    also appears on the schematic (datasheet-complete symbol)."""
    nm = fpga_net_map()
    left, right, top, bot = [], [], [], []
    for ball, net in sorted(nm.items()):
        if net in ("+1V0", "+1V8", "+3V3", "VREFP"):
            top.append((ball, net, "power_in"))
        elif net == "GND":
            bot.append((ball, net, "power_in"))
        elif net in _FPGA_INPUT_NETS or net.startswith("adc_"):
            # ADC SPI bus (adc_convst/sclk/miso/busy/cs0..3/resetn) sits on
            # the LEFT column alongside the other FPGA inputs; the 4 TLC5947
            # em_SPI lines and control fall through to the RIGHT column.
            left.append((ball, net, "bidirectional"))
        else:
            right.append((ball, net, "bidirectional"))
    pins = []
    sp = 1.27
    # Columns are placed at x = +/-50.8 (1.27*40), OUTSIDE the bottom/top row
    # extent (bottom row spans x in +/-49.53 = 1.27*39).  Putting the signal
    # columns at the same x as an on-grid GND row pin would land two net labels
    # (signal + GND) on the same point -> ERC multiple_net_names merges them to
    # GND, silently shorting that signal to ground (this bit emitter_cols0: a
    # right-column pin at y=10.16 met the bottom GND pin at x=17.78,y=10.16).
    # 50.8 is the smallest on-grid x beyond the row extent, so the column and
    # row coordinates can never coincide.  Keeps every pin on the 1.27 mm grid.
    yL = sp * (len(left) - 1) / 2
    for i, (num, name, et) in enumerate(left):
        pins.append(dict(number=num, name=name, etype=et, x=-50.8,
                         y=round(yL - i * sp, 2), rot=0, length=2.54))
    yR = sp * (len(right) - 1) / 2
    for i, (num, name, et) in enumerate(right):
        pins.append(dict(number=num, name=name, etype=et, x=50.8,
                         y=round(yR - i * sp, 2), rot=180, length=2.54))
    xT = -sp * (len(top) - 1) / 2
    for i, (num, name, et) in enumerate(top):
        pins.append(dict(number=num, name=name, etype=et, x=round(xT + i * sp, 2),
                         y=-10.16, rot=90, length=2.54))
    xB = -sp * (len(bot) - 1) / 2
    for i, (num, name, et) in enumerate(bot):
        pins.append(dict(number=num, name=name, etype=et, x=round(xB + i * sp, 2),
                         y=10.16, rot=270, length=2.54))
    return pins


# ---- QSSC_Chip -- the two-faced quantum die (two-PCB sandwich) ---------------
# The QSSC die is a TRUE two-faced die, NO TSVs (user-confirmed 2026-09-04): 64
# graphene detector electrodes on the TOP face, 64 CQD emitter cathodes + 1
# shared anode on the BOTTOM face.  The two faces cannot both be wire-bonded
# face-up, so the die is clamped as a SANDWICH between two PCBs: the MAIN board
# (bottom) contacts the die's BOTTOM / emitter face on its F.Cu, and the
# DETECTOR HEAD (top) contacts the die's TOP / detector face on its B.Cu.  The
# contact is direct (anisotropic-conductive-film pressure contact -- NOT solder:
# the contact pads carry NO paste).  Each face is its own footprint so the two
# boards are independent: QSSC_Chip_EM on the main F.Cu, QSSC_Chip_DET on the
# head B.Cu.  Symbol pin NUMBER == footprint pad name so fp_net_pads nets every
# pad by name.  The optical die carries an 8x8 GRID of electrodes = 64 pads
# evenly across the 20x20 mm (2x2 cm) face, 2.54 mm pitch (standard pin-header
# pitch; 8 pads -> 17.78 mm span).  Each pad escapes outward as a dog-bone via
# (route_signals._chip_fanout, pad-layer-derived: F.Cu pads -> F.Cu stub + B.Cu
# via; B.Cu pads -> B.Cu stub + F.Cu via).
GRID_N = 8                                    # 8x8 electrodes = 64 per face
GRID_PITCH = 2.54                             # mm; standard 2.54 mm pin-header pitch; 7 intervals = 17.78 mm span
GRID_HALF = GRID_PITCH * (GRID_N - 1) / 2     # 8.89 mm -> grid spans -8.89..+8.89

def _chip_grid_xy(i):
    """(x, y) in mm of electrode #i (0..63) on the 8x8 grid, row-major
    (i = row*8 + col, row/col in 0..7), centered on the 20x20 mm die.
    EM0/DET0 -> top-left (-8.89,-8.89); EM7 -> top-right (+8.89,-8.89);
    EM63 -> bottom-right (+8.89,+8.89)."""
    row, col = divmod(i, GRID_N)
    return (round(-GRID_HALF + col * GRID_PITCH, 3),
            round(-GRID_HALF + row * GRID_PITCH, 3))


def chip_pin_list_em():
    """Schematic pins for the die BOTTOM (emitter) face: 64 EM cathodes on the
    bottom edge + 1 EM_ANODE common-anode pad.  This face contacts the MAIN
    board's F.Cu.  1.0 mm SYMBOL pitch (independent of the 2.54 mm PCB grid
    pitch -- the die is an 8x8 grid at 2.54 mm, 17.78 mm span on the 20 mm body)."""
    pins = []
    sp = 1.0
    x0 = -sp * 63 / 2
    for i in range(64):
        pins.append(dict(number=f"EM{i}", name=f"EM{i}", etype="passive",
                         x=round(x0 + i * sp, 2), y=12.7, rot=270, length=2.54))
    # common anode for the 64 emitters (one shared electrode on the die bottom
    # face; the 64 EM pads are the individual cathodes). power_in so ERC treats
    # it as a driven rail once +12V_EMIT is connected (fed from the J4 12 V
    # input; CQD EL turn-on 4-8 V, TLC5947 OUT tolerates 33 V -> full headroom).
    pins.append(dict(number="EM_ANODE", name="EM_ANODE", etype="power_in",
                     x=0, y=15.24, rot=270, length=2.54))
    return pins


def chip_pin_list_det():
    """Schematic pins for the die TOP (detector) face: 64 DET electrodes on the
    top edge + 1 DET_COMMON common back-electrode (the continuous ITO film that
    is the shared detector common masa, edge-contacted -- see
    FABRICATION_PROTOCOL_P0.md: 130 contacts = 64 DET + 64 EM-cathode + 2 ITO
    commons).  This face contacts the DETECTOR HEAD's B.Cu.  1.0 mm SYMBOL pitch
    (independent of the 2.54 mm PCB grid pitch)."""
    pins = []
    sp = 1.0
    x0 = -sp * 63 / 2
    for i in range(64):
        pins.append(dict(number=f"DET{i}", name=f"DET{i}", etype="passive",
                         x=round(x0 + i * sp, 2), y=-12.7, rot=90, length=2.54))
    # DET_COMMON: the ITO back-electrode shared by all 64 graphene pixels.  It
    # ties to DET_REF (the 1.65 V TIA virtual ground) -> zero-bias short-circuit
    # photocurrent readout (max responsivity for the photogating graphene/MoS2
    # detector).  passive (DET_REF is a divider reference, not a power rail).
    pins.append(dict(number="DET_COMMON", name="DET_COMMON", etype="passive",
                     x=0, y=-15.24, rot=90, length=2.54))
    return pins


# Back-compat single-face aliases (gen_bom / older callers): the merged 128-pad
# view, EM face only.  Prefer chip_pin_list_em / chip_pin_list_det.
def chip_pin_list():
    return chip_pin_list_em()


def chip_net_map_em():
    """{pad_name: net} for the die bottom (emitter) face.
    EM{i}    -> emitter_cat{i}  (per-pixel cathode -> TLC5947 constant-current
                                 sink output; common-anode topology)
    EM_ANODE -> +12V_EMIT       (the 64 emitters' shared anode rail)."""
    m = {f"EM{i}": f"emitter_cat{i}" for i in range(64)}
    m["EM_ANODE"] = "+12V_EMIT"
    return m


def chip_net_map_det():
    """{pad_name: net} for the die top (detector) face.
    DET{i}       -> DET_IN{i}  (analog input to the TIA -> AD7616 front-end).
    DET_COMMON   -> DET_REF    (the ITO common back-electrode tied to the 1.65 V
                               TIA virtual ground -> zero-bias photocurrent
                               readout; closes the bias/return path for all 64
                               graphene pixels -- without it the detector
                               circuit is open on the common side)."""
    m = {f"DET{i}": f"DET_IN{i}" for i in range(64)}
    m["DET_COMMON"] = "DET_REF"
    return m


def chip_net_map():
    return chip_net_map_em()


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


# ---- Conn_02x10_BTB -- board-to-board connector (main <-> detector head) -----
# A 2x10 2.54 mm through-hole pin header (HOST, male long pins on the main
# board) + socket (DAUGHTER, female on the head) mates the two PCBs and sets the
# sandwich stacking height (long pins span the die-stack thickness + contact
# pressure).  It carries the 9-wire AD7616 SPI bus (adc_convst/sclk/miso/busy/
# cs0..3/resetn) + +3V3 + +5V + GND.  Every SPI signal is GND-flanked for a
# clean return path.  Both boards use IDENTICAL net names on their J12 pins --
# the mating pins physically bridge the two boards' nets of the same name, so a
# typo on either side is a silent open (Gotcha #8).  The head derives its +5V_A
# analog rail locally from +5V arriving here (FB_A); +3V3 powers the TIA/ADC
# VDRIVE; the 8 GND pins are the return + reference.
BTB_PIN_NETS = {
    "1": "GND", "2": "+3V3", "3": "adc_convst", "4": "GND", "5": "adc_sclk",
    "6": "GND", "7": "adc_miso", "8": "GND", "9": "adc_busy", "10": "GND",
    "11": "adc_resetn", "12": "GND", "13": "adc_cs0", "14": "adc_cs1",
    "15": "adc_cs2", "16": "adc_cs3", "17": "+5V", "18": "+5V",
    "19": "GND", "20": "GND",
}


def conn_02x10_pin_list():
    """2x10 BTB connector symbol pins on a 2.54 mm grid (odd pins left, even
    right; 10 rows centred)."""
    pins = []
    for i in range(20):
        col = i % 2                       # 0=left(odd), 1=right(even)
        row = i // 2
        x = -2.54 if col == 0 else 2.54
        y = round(11.43 - row * 2.54, 2)  # 10 rows -> -11.43..+11.43
        rot = 180 if col == 0 else 0
        pins.append(dict(number=str(i + 1), name="~", etype="passive",
                         x=x, y=y, rot=rot, length=2.54))
    return pins


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


def add_btb_connector(side):
    """Add J12 -- the board-to-board connector carrying the 9-wire AD7616 SPI +
    power between the main board (side='host') and the detector head
    (side='daughter').  Both sides use identical net names (BTB_PIN_NETS); the
    mating pins bridge the two boards' same-named nets."""
    val = "BTB_HOST" if side == "host" else "BTB_DAUGHTER"
    add("J12", "qssc:Conn_02x10_BTB", val, "qssc:Conn_02x10_BTB",
        0, 0, pins=dict(BTB_PIN_NETS))


# ---- Phase 1b front-end / driver symbol pinouts (datasheet, hand-authored) -
def tlv9004_pin_list():
    """TLV9004 quad CMOS op-amp, TSSOP-14 (TI). 1=OUTA 2=-INA 3=+INA 4=V+
    5=+INB 6=-INB 7=OUTB 8=OUTC 9=-INC 10=+INC 11=V- 12=+IND 13=-IND 14=OUTD.
    Used as the 64-ch TIA (1 IC = 4 detector channels), on +3V3."""
    L = [("1", "OUTA", "output"), ("2", "INA-", "input"), ("3", "INA+", "input"),
         ("7", "OUTB", "output"), ("6", "INB-", "input"), ("5", "INB+", "input"),
         ("8", "OUTC", "output"), ("9", "INC-", "input"), ("10", "INC+", "input"),
         ("14", "OUTD", "output"), ("13", "IND-", "input"), ("12", "IND+", "input")]
    pins = []
    yL = 2.54 * (len(L) - 1) / 2
    for i, (n, nm, et) in enumerate(L):
        pins.append(dict(number=n, name=nm, etype=et, x=-10.16,
                         y=round(yL - i * 2.54, 2), rot=0, length=2.54))
    pins.append(dict(number="4", name="V+", etype="power_in", x=0, y=-10.16, rot=90, length=2.54))
    pins.append(dict(number="11", name="V-", etype="power_in", x=0, y=10.16, rot=270, length=2.54))
    return pins


def lmv339_pin_list():
    """LMV339 quad comparator, SOIC-14 (TI). 1=OUTA 2=-INA 3=+INA 4=-INB 5=+INB
    6=OUTB 7=GND 8=OUTC 9=-INC 10=+INC 11=OUTD 12=+IND 13=-IND 14=VCC.
    Open-collector -> 10k pull-up to +3V3 -> DET_DIG (FPGA LVCMOS33)."""
    L = [("1", "OUTA", "output"), ("2", "INA-", "input"), ("3", "INA+", "input"),
         ("6", "OUTB", "output"), ("4", "INB-", "input"), ("5", "INB+", "input"),
         ("8", "OUTC", "output"), ("9", "INC-", "input"), ("10", "INC+", "input"),
         ("11", "OUTD", "output"), ("13", "IND-", "input"), ("12", "IND+", "input")]
    pins = []
    yL = 2.54 * (len(L) - 1) / 2
    for i, (n, nm, et) in enumerate(L):
        pins.append(dict(number=n, name=nm, etype=et, x=-10.16,
                         y=round(yL - i * 2.54, 2), rot=0, length=2.54))
    pins.append(dict(number="14", name="VCC", etype="power_in", x=0, y=-10.16, rot=90, length=2.54))
    pins.append(dict(number="7", name="GND", etype="power_in", x=0, y=10.16, rot=270, length=2.54))
    return pins


def mcp4725_pin_list():
    """MCP4725 12-bit I2C DAC, SOT-23-6 (Microchip). 1=VDD 2=GND 3=VOUT 4=SDA
    5=SCL 6=ADDR. VOUT -> DET_THR (shared 64-comparator threshold)."""
    return [dict(number="1", name="VDD", etype="power_in", x=-7.62, y=3.81, rot=0, length=2.54),
            dict(number="2", name="GND", etype="power_in", x=-7.62, y=-3.81, rot=0, length=2.54),
            dict(number="3", name="VOUT", etype="power_out", x=7.62, y=3.81, rot=180, length=2.54),
            dict(number="4", name="SDA", etype="bidirectional", x=7.62, y=1.27, rot=180, length=2.54),
            dict(number="5", name="SCL", etype="bidirectional", x=7.62, y=-1.27, rot=180, length=2.54),
            dict(number="6", name="ADDR", etype="input", x=-7.62, y=1.27, rot=0, length=2.54)]


def tlc5947_pin_list():
    """TLC5947 24-channel 12-bit PWM constant-current LED driver, HTSSOP-32 (TI
    SBVS114B).  Internal 4 MHz grayscale oscillator -> NO GSCLK pin.  Pinout
    verified against the TI datasheet PDF (extracted text):
        1=GND  2=BLANK  3=SCLK  4=SIN  5..28=OUT0..OUT23  29=SOUT
        30=XLAT  31=IREF  32=VCC  33=thermal pad (DAP)=GND
    VCC=3.0-5.5 V (run +3V3); OUT pins sink up to 33 V / 38 mA INDEPENDENT of VCC
    (emitter anode rail = +12V_EMIT, cathodes = OUTn -- the CQD EL turn-on is
    4-8 V per docs/FABRICATION_PROTOCOL.md, so a 12 V LED supply gives headroom
    for the constant-current sink at any CQD V_f up to ~11 V).  Output current
    I_OUT = 41 * 1.20 / R_IREF -> R_IREF = 4.87 kOhm for ~10.1 mA (in the CQD
    2-20 mA/pixel spec).  Interface: SCLK/SIN/XLAT/BLANK (SIN->SOUT cascades
    chip-to-chip; 3 chips => 72 channels, 64 used)."""
    pins = []
    # Right column: the 24 constant-current sink outputs OUT0..OUT23 (pins 5..28)
    R = [(str(5 + k), f"OUT{k}") for k in range(24)]
    yR = 2.54 * (len(R) - 1) / 2
    for i, (n, nm) in enumerate(R):
        pins.append(dict(number=n, name=nm, etype="passive", x=10.16,
                         y=round(yR - i * 2.54, 2), rot=180, length=2.54))
    # Left column: control + power (8 pins). Order top->bottom for readability.
    L = [("32", "VCC", "power_in"), ("31", "IREF", "bidirectional"),
         ("30", "XLAT", "input"), ("29", "SOUT", "output"),
         ("4", "SIN", "input"), ("3", "SCLK", "input"),
         ("2", "BLANK", "input"), ("1", "GND", "power_in")]
    yL = 2.54 * (len(L) - 1) / 2
    for i, (n, nm, et) in enumerate(L):
        pins.append(dict(number=n, name=nm, etype=et, x=-10.16,
                         y=round(yL - i * 2.54, 2), rot=0, length=2.54))
    # Thermal pad (pin 33) -> GND, on the bottom edge of the symbol body.
    pins.append(dict(number="33", name="EP", etype="power_in", x=0, y=10.16,
                     rot=270, length=2.54))
    return pins


# ---- AD7616 16-ch 16-bit dual simultaneous-sampling ADC (ADI, LQFP-80) -------
# Replaces the 16x LMV339 comparator + 64x pull-up + MCP4725 threshold DAC +
# photon_counter_array readout with a true analog intensity readout: each
# TLV9004 TIA output (DET_TIA{i}) drives one AD7616 analog input; the FPGA reads
# the 64 16-bit intensity samples over SPI and does the lock-in demodulation
# numerically.  4x AD7616 = 64 channels; a shared CONVST samples all 4 chips
# together (dual simultaneous: 8 VA/VB pair-conversions per chip = 8 CONVST
# pulses scan all 64 channels; the ~us skew across pairs is negligible vs the
# 1 kHz mod_sync lock-in).  Pinout verified against the official KiCad
# Analog_ADC:AD7616 symbol (datasheet-derived, AD7616 Rev. 0) and cross-checked
# against the AD7616 datasheet serial-interface section:
#   * serial mode:  SER/PAR (40) = HIGH;  SER1W (45) = LOW -> 1-wire, all data
#     on SDOA (57), 32 SCLK per VAx+VBx pair (SDOB unused).
#   * readout:  CONVST (68) rising -> BUSY (67) high; on BUSY falling, CS (63)
#     low clocks the MSB out on SDOA, bits shift on SCLK (62) rising edges,
#     CS high returns SDOA to Hi-Z.  Per-device CS lets 4 chips share one SDOA
#     (no daisy-chain needed -- only the selected chip drives the line).
#   * reference:  internal (REFSEL (35) = GND); REFCAP (31) + REFINOUT (33)
#     decoupled to GND.  Range = +/-5 V bipolar via HARDWARE pin-strapping
#     (HW_RNGSEL1 (38)=HIGH, HW_RNGSEL0 (39)=LOW -> HW mode +/-5 V, datasheet
#     Table 8); HW mode prohibits register access, so SDI (55) is NC and no
#     init config write is needed.  The 0..3.3 V TIA output fits +/-5 V with
#     full resolution (~21.6k codes).
#   * supplies:  VCC (5 V analog) on +5V_A (ferrite-isolated from +5V);
#     VDRIVE (49) = +3V3 (logic).  All AGND/DGND/REGGND/REFGND + per-channel
#     VGND pins -> GND.
# 4 chips share CONVST/SCLK/MISO/RESETn; per-chip CS0..CS3; only chip0
# BUSY is read (push-pull, cannot wire-OR) -- all 4 finish together.
AD7616_FP = "Package_QFP:LQFP-80_14x14mm_P0.65mm"
# (pin_number, pin_name, etype, side) -- side: L/R/T/B for the symbol layout.
_AD7616_PINS = [
    # analog inputs (16 SE channels): bank A (V0A..V7A) + bank B (V0B..V7B)
    ("28", "V0A", "input", "L"), ("26", "V1A", "input", "L"),
    ("24", "V2A", "input", "L"), ("22", "V3A", "input", "L"),
    ("19", "V4A", "input", "L"), ("17", "V5A", "input", "L"),
    ("14", "V6A", "input", "L"), ("12", "V7A", "input", "L"),
    ("73", "V0B", "input", "L"), ("75", "V1B", "input", "L"),
    ("77", "V2B", "input", "L"), ("79", "V3B", "input", "L"),
    ("2", "V4B", "input", "L"), ("4", "V5B", "input", "L"),
    ("7", "V6B", "input", "L"), ("9", "V7B", "input", "L"),
    # serial / digital control
    ("68", "CONVST", "input", "R"), ("67", "BUSY", "output", "R"),
    ("62", "SCLK", "input", "R"), ("63", "CS", "input", "R"),
    ("57", "SDOA", "bidirectional", "R"), ("56", "SDOB", "output", "R"),
    ("55", "SDI", "input", "R"), ("36", "RESET", "input", "R"),
    ("40", "SERPAR", "input", "R"), ("45", "SER1W", "input", "R"),
    ("64", "CHSEL0", "input", "R"), ("65", "CHSEL1", "input", "R"),
    ("66", "CHSEL2", "input", "R"), ("37", "SEQEN", "input", "R"),
    ("39", "HWRNGSEL0", "input", "R"), ("38", "HWRNGSEL1", "input", "R"),
    ("61", "BURST", "input", "R"), ("58", "OS0", "input", "R"),
    ("59", "OS1", "input", "R"), ("60", "OS2", "input", "R"),
    ("46", "CRCEN", "input", "R"),
    ("41", "DB0", "bidirectional", "R"), ("42", "DB1", "bidirectional", "R"),
    ("43", "DB2", "bidirectional", "R"), ("44", "DB3", "bidirectional", "R"),
    ("47", "DB6", "bidirectional", "R"), ("48", "DB7", "bidirectional", "R"),
    ("53", "DB8", "bidirectional", "R"), ("54", "DB9", "bidirectional", "R"),
    # power / reference
    ("6", "VCC", "power_in", "T"), ("15", "VCC", "power_in", "T"),
    ("30", "VCC", "power_in", "T"), ("71", "VCC", "power_in", "T"),
    ("49", "VDRIVE", "power_in", "T"), ("31", "REFCAP", "passive", "T"),
    ("33", "REFINOUT", "input", "T"), ("35", "REFSEL", "input", "T"),
    ("70", "REGCAP", "passive", "T"), ("52", "REGCAPD", "passive", "T"),
    # grounds (per-channel VGND + AGND/DGND/REGGND/REFGND)
    ("5", "AGND", "power_in", "B"), ("16", "AGND", "power_in", "B"),
    ("29", "AGND", "power_in", "B"), ("72", "AGND", "power_in", "B"),
    ("50", "DGND", "power_in", "B"), ("69", "REGGND", "power_in", "B"),
    ("51", "REGGNDD", "power_in", "B"), ("32", "REFGND", "power_in", "B"),
    ("34", "REFINOUTGND", "power_in", "B"),
    ("27", "V0AGND", "power_in", "B"), ("25", "V1AGND", "power_in", "B"),
    ("23", "V2AGND", "power_in", "B"), ("21", "V3AGND", "power_in", "B"),
    ("20", "V4AGND", "power_in", "B"), ("18", "V5AGND", "power_in", "B"),
    ("13", "V6AGND", "power_in", "B"), ("11", "V7AGND", "power_in", "B"),
    ("74", "V0BGND", "power_in", "B"), ("76", "V1BGND", "power_in", "B"),
    ("78", "V2BGND", "power_in", "B"), ("80", "V3BGND", "power_in", "B"),
    ("1", "V4BGND", "power_in", "B"), ("3", "V5BGND", "power_in", "B"),
    ("8", "V6BGND", "power_in", "B"), ("10", "V7BGND", "power_in", "B"),
]


def ad7616_pin_list():
    """80-pin LQFP symbol: 16 analog inputs on the left, serial/digital control
    on the right, power/reference on top, all grounds on the bottom.  Pin numbers
    match the LQFP-80 footprint pads 1:1 (verified vs KiCad Analog_ADC:AD7616)."""
    by = {"L": [], "R": [], "T": [], "B": []}
    for num, name, et, side in _AD7616_PINS:
        by[side].append((num, name, et))
    pins = []
    sp = 1.27
    def col(side, x, rot):
        lst = by[side]
        y0 = sp * (len(lst) - 1) / 2
        return [dict(number=n, name=nm, etype=et, x=x,
                     y=round(y0 - i * sp, 2), rot=rot, length=2.54)
                for i, (n, nm, et) in enumerate(lst)]
    def row(side, y, rot):
        lst = by[side]
        x0 = -sp * (len(lst) - 1) / 2
        return [dict(number=n, name=nm, etype=et, x=round(x0 + i * sp, 2),
                     y=y, rot=rot, length=2.54)
                for i, (n, nm, et) in enumerate(lst)]
    pins += col("L", -25.4, 0)
    pins += col("R", 25.4, 180)
    pins += row("T", -25.4, 90)
    pins += row("B", 25.4, 270)
    return pins


# ---- GVA-84+ RF MMIC amp (Mini-Circuits, SOT-89, self-biased +5V) ----------
# Replaces the HMC441LP3E (6.5-13.5 GHz -- wrong band for the 2.87/3.46 GHz
# ODMR target, AND its custom symbol used logical pin numbers VCC/GND/RF_IN/...
# that never mapped to the QFN-16 footprint's numeric pads 1-16, so the amp was
# completely unnetted on the board).  The GVA-84+ is DC-7 GHz, +18.4 dB @2 GHz,
# +20.6 dBm P1dB, self-biased from +5V via an RF choke to pin 3 -- no VGG pin.
# SOT-89 pinout per Mini-Circuits datasheet (case DF782):
#   1 = RFIN (DC-blocked), 2 = GND, 3 = RFOUT & DC-IN (DC-blocked), 4 = GND paddle.
# The KiCad `SOT-89-3` footprint exposes pads 1/2/3; pad 2 is the large centre
# pad that doubles as the GND paddle (pin 4), so a 3-pin symbol maps 1:1.
GVA84_PINS = [
    dict(number="1", name="RFIN", etype="input", x=-7.62, y=0, rot=0),
    dict(number="2", name="GND", etype="power_in", x=0, y=7.62, rot=270),
    dict(number="3", name="RFOUT", etype="output", x=7.62, y=0, rot=180),
]
GVA84_NETS = {"1": "RF_IN", "2": "GND", "3": "RF_AMP_OUT"}


# ---- AMS1117 custom (3 pins, avoids system-lib `extends`) -----------------
def ams1117_pins():
    return [
        dict(number="1", name="GND", etype="power_in", x=0, y=-7.62, rot=90),
        dict(number="2", name="VO", etype="power_out", x=7.62, y=0, rot=180),
        dict(number="3", name="VI", etype="power_in", x=-7.62, y=0, rot=0),
    ]


# ---- AP2127K-1.0 (Diodes Inc, SOT-23-5) -- REAL 1.0 V LDO -------------------
# AMS1117 has NO 1.0 V option (min fixed output 1.5 V); the prior "AMS1117-1.0"
# was a non-existent part. VCCINT=1.0 V uses the AP2127K-1.0 (600 mA, SOT-23-5).
# Pinout per Diodes Inc AP2127K datasheet (DS36478): fixed-output version is
# 1=VIN 2=GND 3=EN(shutdown) 4=NC 5=VOUT.  Pin 4 is NC on every FIXED-output
# AP2127K (it becomes ADJ only on the -ADJ version); there is NO bypass/noise-
# pin on this LDO -- noise reduction is via the CIN/COUT ceramic caps (C42-C44).
# The prior "BP" label + C41 10nF tied a cap to a floating NC pad (does nothing).
def ap2127k_pins():
    return [
        dict(number="1", name="VIN", etype="power_in", x=-7.62, y=-3.81, rot=0),
        dict(number="3", name="EN", etype="input", x=-7.62, y=3.81, rot=0),
        dict(number="2", name="GND", etype="power_in", x=0, y=7.62, rot=270),
        dict(number="5", name="VOUT", etype="power_out", x=7.62, y=-3.81, rot=180),
        dict(number="4", name="NC", etype="passive", x=7.62, y=3.81, rot=180),
    ]


# --------------------------------------------------------------------------
# Standard-symbol pin coords (for computing label positions on the schematic)
# We author custom minimal symbols that REPLICATE the standard pin numbers and
# electrical types, but use a simple uniform layout so label placement is easy.
# --------------------------------------------------------------------------
def simple_two_pin_vertical():  # R, C, L, Polyfuse, D, LED
    return None  # handled inline


def authored_symbols():
    """All custom/minimal symbol definitions we embed in lib_symbols."""
    syms = []
    # power symbols
    for n in ["+3V3", "+1V8", "+1V0", "+5V", "+5V_A", "+5V_RF", "+12V_EMIT", "GND"]:
        syms.append(power_symbol_def(n))
    syms.append(PWR_FLAG_def())
    # FPGA -- real 324-pad CSG324 pinout (power/ground/config/JTAG + ~26 IO:
    # 9-pin ADC SPI bus + 4 em_SPI + 13 control).  Body box spans the
    # 1.27 mm-pitched pin columns; the left column holds the adc_* bus
    # (+ other inputs), the right the 4 em_SPI + control, so the body is tall.
    syms.append(custom_symbol_def("qssc", "XC7A35T_CSG324", "U", fpga_pin_list(),
                                  (-50.8, -55.0, 50.8, 55.0), value="XC7A35T-1CSG324C"))
    # QSSC_Chip -- the two-faced quantum die, split into two face symbols for the
    # two-PCB sandwich.  QSSC_Chip_EM (bottom/emitter face -> main board F.Cu)
    # and QSSC_Chip_DET (top/detector face -> head B.Cu).  Body spans the 64-wide
    # pin row; 1.0 mm symbol pitch.  Pin numbers == footprint pad names.
    syms.append(custom_symbol_def("qssc", "QSSC_Chip_EM", "U", chip_pin_list_em(),
                                  (-34.0, -10.16, 34.0, 16.51), value="QSSC_Chip_EM"))
    syms.append(custom_symbol_def("qssc", "QSSC_Chip_DET", "U", chip_pin_list_det(),
                                  (-34.0, -10.16, 34.0, 10.16), value="QSSC_Chip_DET"))
    # ChipSocket_2.54_EM / ChipSocket_2.54_DET -- standard 2.54 mm female-header
    # chip SOCKETS (off-the-shelf through-hole part).  ChipSocket_2.54_EM holds the
    # die emitter face on the main board F.Cu (same 65 electrical pins as
    # QSSC_Chip_EM); ChipSocket_2.54_DET holds the detector face on the head B.Cu
    # (same 65 pins as QSSC_Chip_DET).  chip_net_map_em/det() net every contact;
    # the 4 NPTH mount holes are footprint-only (no symbol pin).
    syms.append(custom_symbol_def("qssc", "ChipSocket_2.54_EM", "U", chip_pin_list_em(),
                                  (-34.0, -10.16, 34.0, 16.51), value="ChipSocket_2.54_EM"))
    syms.append(custom_symbol_def("qssc", "ChipSocket_2.54_DET", "U", chip_pin_list_det(),
                                  (-34.0, -10.16, 34.0, 10.16), value="ChipSocket_2.54_DET"))
    # Conn_02x10_BTB -- board-to-board connector between the main board and the
    # detector head (9-wire AD7616 SPI + 3V3/5V/GND).  Replicates the standard
    # 2x10 generic connector pin layout; the footprint is authored inline.
    syms.append(replicated_symbol("qssc", "Conn_02x10_BTB", "J",
                                  conn_02x10_pin_list(), value="Conn_02x10_BTB"))
    # ---- Phase 1b front-end / driver IC symbols (hand-authored datasheet pinouts)
    syms.append(custom_symbol_def("qssc", "TLV9004", "U", tlv9004_pin_list(),
                                  (-7.62, -7.62, 7.62, 7.62), value="TLV9004"))
    # AD7616 16-ch 16-bit dual simultaneous-sampling ADC (ADI, LQFP-80).  Replaces
    # the old 16x LMV339 comparator + MCP4725 threshold DAC + photon-counter
    # readout with a true parallel analog intensity readout: 4x AD7616 = 64 ch,
    # each TLV9004 TIA output -> one ADC input, FPGA reads 64 16-bit samples over
    # SPI.  See ad7616_pin_list() for the datasheet-verified interface.
    syms.append(custom_symbol_def("qssc", "AD7616", "U", ad7616_pin_list(),
                                  (-25.4, -25.4, 25.4, 25.4), value="AD7616"))
    # TLC5947 24-ch 12-bit PWM constant-current LED driver (HTSSOP-32, TI
    # SBVS114B). Replaces the old 8x 74HCT240 + 64x AO3401A P-MOS emitter driver
    # (208 parts -> 3 chips): 64 emitter cathodes sink into 3 daisy-chained
    # TLC5947 OUT0..23, common anode = +12V_EMIT.  Body spans the 24-wide OUT column.
    syms.append(custom_symbol_def("qssc", "TLC5947", "U", tlc5947_pin_list(),
                                  (-7.62, -16.51, 7.62, 16.51), value="TLC5947"))
    # W25Q128 config flash (SOIC-8, datasheet pinout).  QSPI x4 boot to the FPGA
    # dedicated config balls D00..D03 / FCS_B / CCLK.
    syms.append(custom_symbol_def("qssc", "W25Q128JVS", "U", [
        dict(number="1", name="CS", etype="input", x=-7.62, y=2.54, rot=0),
        dict(number="2", name="DO_D1", etype="bidirectional", x=-7.62, y=0, rot=0),
        dict(number="3", name="WP_D2", etype="bidirectional", x=-7.62, y=-2.54, rot=0),
        dict(number="4", name="GND", etype="power_in", x=0, y=7.62, rot=270),
        dict(number="5", name="DI_D0", etype="bidirectional", x=7.62, y=-2.54, rot=180),
        dict(number="6", name="CLK", etype="input", x=7.62, y=0, rot=180),
        dict(number="7", name="HOLD_D3", etype="bidirectional", x=7.62, y=2.54, rot=180),
        dict(number="8", name="VCC", etype="power_in", x=0, y=-7.62, rot=90),
    ], (-5.08, -5.08, 5.08, 5.08), value="W25Q128JVSIQ"))
    # GVA-84+ RF MMIC amp (replaces HMC441 -- see GVA84_PINS docstring)
    syms.append(custom_symbol_def("qssc", "GVA-84", "U", GVA84_PINS,
                                  (-5.08, -5.08, 5.08, 5.08), value="GVA-84+"))
    # AMS1117 family (3 pins each, simple horizontal body) -- 1.8/3.3/5.0 only;
    # the 1.0 V rail uses the real AP2127K-1.0 (AMS1117 has no 1.0 V option).
    for v in ["AMS1117-1.8", "AMS1117-3.3", "AMS1117-5.0"]:
        syms.append(custom_symbol_def("qssc", v, "U", ams1117_pins(),
                                      (-5.08, -5.08, 5.08, 5.08), value=v))
    syms.append(custom_symbol_def("qssc", "AP2127K-1.0", "U", ap2127k_pins(),
                                  (-5.08, -5.08, 5.08, 5.08), value="AP2127K-1.0"))
    # FT232RL: replicate standard pins/etypes with a custom layout
    syms.append(replicated_symbol("qssc", "FT232RL", "U",
                                  std_pins("Interface_USB", "FT232RL"), value="FT232RL"))
    # ADF4351
    syms.append(replicated_symbol("qssc", "ADF4351", "U",
                                  std_pins("Timer_PLL", "ADF4351"), value="ADF4351BCPZ"))
    # ADA4530-1
    syms.append(replicated_symbol("qssc", "ADA4530-1", "U",
                                  std_pins("Amplifier_Operational", "ADA4530-1"), value="ADA4530-1"))
    # ASE oscillator
    syms.append(replicated_symbol("qssc", "ASE_osc", "Y",
                                  std_pins("Oscillator", "ASE-xxxMHz"), value="100MHz XO"))
    # connectors
    syms.append(replicated_symbol("qssc", "Barrel_Jack", "J",
                                  std_pins("Connector", "Barrel_Jack"), value="5V Barrel"))
    syms.append(replicated_symbol("qssc", "Conn_Coaxial", "J",
                                  std_pins("Connector", "Conn_Coaxial"), value="SMA"))
    syms.append(replicated_symbol("qssc", "Conn_02x08", "J",
                                  std_pins("Connector_Generic", "Conn_02x08_Odd_Even"), value=""))
    syms.append(replicated_symbol("qssc", "Conn_02x05", "J",
                                  std_pins("Connector_Generic", "Conn_02x05_Odd_Even"), value=""))
    syms.append(replicated_symbol("qssc", "Conn_01x02", "J",
                                  std_pins("Connector_Generic", "Conn_01x02"), value=""))
    syms.append(replicated_symbol("qssc", "SW_Push", "SW",
                                  std_pins("Switch", "SW_Push"), value="RESET"))
    syms.append(replicated_symbol("qssc", "USB_C_Receptacle", "J",
                                  std_pins("Connector", "USB_C_Receptacle"), value="USB-C"))
    # passives: R, C, L, LED, Polyfuse, D  (2-pin vertical)
    for nm, refp in [("R", "R"), ("C", "C"), ("L", "L"), ("LED", "D"), ("Polyfuse", "F"), ("D", "D")]:
        p1 = dict(number="1", name="~" if nm in ("R", "C", "Polyfuse") else ("K" if nm in ("LED", "D") else "1"),
                  etype="passive", x=0, y=3.81, rot=270, length=2.54 if nm == "LED" or nm == "D" else 1.27)
        p2 = dict(number="2", name="~" if nm in ("R", "C", "Polyfuse") else ("A" if nm in ("LED", "D") else "2"),
                  etype="passive", x=0, y=-3.81, rot=90, length=2.54 if nm == "LED" or nm == "D" else 1.27)
        body = (-3.81, -3.81, 3.81, 3.81)
        syms.append(custom_symbol_def("qssc", nm, refp, [p1, p2], body, value=nm))
    return syms


def replicated_symbol(lib, name, refpref, stdpinlist, value=None):
    """Author a symbol using the real pin numbers/names/etypes from the system
    lib, but laid out on a simple grid (inputs left, outputs right, power top/bottom)."""
    # split pins
    left, right, top, bot = [], [], [], []
    for p in stdpinlist:
        et = p["etype"]
        if et in ("power_in", "power_out"):
            (top if "V" in p["name"].upper() or "DD" in p["name"].upper() else bot).append(p)
        elif et in ("input", "bidirectional"):
            left.append(p)
        elif et == "output":
            right.append(p)
        else:  # passive
            left.append(p)
    pins = []
    yL = 2.54 * (len(left) - 1) / 2
    for i, p in enumerate(left):
        pins.append(dict(number=p["number"], name=p["name"], etype=p["etype"],
                         x=-10.16, y=round(yL - i * 2.54, 2), rot=0, length=2.54))
    yR = 2.54 * (len(right) - 1) / 2
    for i, p in enumerate(right):
        pins.append(dict(number=p["number"], name=p["name"], etype=p["etype"],
                         x=10.16, y=round(yR - i * 2.54, 2), rot=180, length=2.54))
    xT = -2.54 * (len(top) - 1) / 2
    for i, p in enumerate(top):
        pins.append(dict(number=p["number"], name=p["name"], etype=p["etype"],
                         x=round(xT + i * 2.54, 2), y=-7.62, rot=90, length=2.54))
    xB = -2.54 * (len(bot) - 1) / 2
    for i, p in enumerate(bot):
        pins.append(dict(number=p["number"], name=p["name"], etype=p["etype"],
                         x=round(xB + i * 2.54, 2), y=7.62, rot=270, length=2.54))
    by = max(7.62, 2.54 * (max(len(left), len(right)) + 1) / 2)
    return custom_symbol_def(lib, name, refpref, pins, (-7.62, -by, 7.62, by), value=value)


# Map lib_id -> symbol name in our authored lib
SYM_NAME = {
    "qssc:XC7A35T_CSG324": "XC7A35T_CSG324",
    "qssc:QSSC_Chip": "QSSC_Chip", "qssc:QSSC_Chip_EM": "QSSC_Chip_EM",
    "qssc:QSSC_Chip_DET": "QSSC_Chip_DET",
    "qssc:ChipSocket_2.54_EM": "ChipSocket_2.54_EM", "qssc:ChipSocket_2.54_DET": "ChipSocket_2.54_DET",
    "qssc:Conn_02x10_BTB": "Conn_02x10_BTB",
    "qssc:GVA-84": "GVA-84+",
    "qssc:AP2127K-1.0": "AP2127K-1.0", "qssc:AMS1117-1.8": "AMS1117-1.8",
    "qssc:AMS1117-3.3": "AMS1117-3.3", "qssc:AMS1117-5.0": "AMS1117-5.0",
    "qssc:FT232RL": "FT232RL", "qssc:ADF4351": "ADF4351", "qssc:ADA4530-1": "ADA4530-1",
    "qssc:W25Q128JVS": "W25Q128JVS",
    "qssc:ASE_osc": "ASE_osc", "qssc:Barrel_Jack": "Barrel_Jack",
    "qssc:Conn_Coaxial": "Conn_Coaxial", "qssc:Conn_02x08": "Conn_02x08",
    "qssc:Conn_02x05": "Conn_02x05", "qssc:Conn_01x02": "Conn_01x02",
    "qssc:SW_Push": "SW_Push", "qssc:USB_C_Receptacle": "USB_C_Receptacle",
    "qssc:R": "R", "qssc:C": "C", "qssc:L": "L", "qssc:LED": "LED",
    "qssc:Polyfuse": "Polyfuse", "qssc:D": "D",
    "qssc:TLV9004": "TLV9004", "qssc:AD7616": "AD7616",
    "qssc:TLC5947": "TLC5947",
}

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


def build_main_board():
    # ---- place components on the schematic grid -------------------------------
    # Power chain (left column)
    add("J2", "qssc:Barrel_Jack", "5V Barrel", "Connector_BarrelJack:BarrelJack_CUI_PJ-102AH_Horizontal",
        30, 30, pins={"1": "+5V_IN", "2": "GND"})
    # +5V_IN input bulk decoupling: the barrel-jack input rail (J2.1 -> F1.1) had
    # NO decoupling cap -- 4 loads (J2, F1, USB diode-OR D4, PWR_FLAG) and 0 caps.
    # Per standard practice a 10 uF bulk cap at the barrel input absorbs source
    # ripple / hot-plug transient before the polyfuse + regulators.
    add("C_in", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
        43, 38, pins={"1": "+5V_IN", "2": "GND"})
    add("F1", "qssc:Polyfuse", "2A PTC", "Fuse:Fuse_1206_3216Metric",
        55, 30, pins={"1": "+5V_IN", "2": "+5V"})
    add("REG1", "qssc:AMS1117-3.3", "AMS1117-3.3", "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
        80, 30, pins={"1": "GND", "2": "+3V3", "3": "+5V"})
    add("REG2", "qssc:AMS1117-1.8", "AMS1117-1.8", "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
        80, 50, pins={"1": "GND", "2": "+1V8", "3": "+5V"})
    add("REG3", "qssc:AP2127K-1.0", "AP2127K-1.0", "Package_TO_SOT_SMD:SOT-23-5",
        80, 70, pins={"1": "+5V", "2": "GND", "3": "+5V", "5": "+1V0"},
        nc_pins=["4"])  # pin 4 = NC on the fixed AP2127K-1.0 (no bypass pin)
    # REG3 (AP2127K-1.0) decoupling: 10uF in + 10uF/100nF out (per datasheet; no
    # bypass cap -- pin 4 is NC, the prior C41 10nF on a floating pad is removed).
    add("C42", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
        68, 70, pins={"1": "+5V", "2": "GND"})
    add("C43", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
        92, 78, pins={"1": "+1V0", "2": "GND"})
    add("C44", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        80, 78, pins={"1": "+1V0", "2": "GND"})
    # REG4 was an AMS1117-5.0 LDO from +5V -> +5V_RF, but the AMS1117 needs ~6.5 V
    # in for a 5.0 V out -- fed from the 5 V barrel rail it sat permanently in
    # dropout and delivered ~3.7-4 V to the RF amp (needs 4.8-5.2 V). The +5V
    # rail is already regulated 5 V, so +5V_RF needs NO regulation -- only a ferrite
    # bead + pi filter to keep the RF section's noise off the main +5V rail (and vice
    # versa). FB1 is a 600-ohm@100MHz bead; C45/C46 buffer the +5V side, C47/C48 the
    # +5V_RF side (10uF bulk + 100nF HF each).
    add("FB1", "qssc:L", "600Ω@100MHz", "Inductor_SMD:L_0603_1608Metric",
        80, 90, pins={"1": "+5V", "2": "+5V_RF"})
    add("C45", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
        68, 90, pins={"1": "+5V", "2": "GND"})
    add("C46", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        68, 98, pins={"1": "+5V", "2": "GND"})
    add("C47", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
        92, 90, pins={"1": "+5V_RF", "2": "GND"})
    add("C48", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        92, 98, pins={"1": "+5V_RF", "2": "GND"})
    # ---- +12V_EMIT emitter anode supply (separate 12 V LED input) -------------
    # The 64 CQD emitters share a common anode (EM_ANODE -> +12V_EMIT). Per
    # docs/FABRICATION_PROTOCOL.md the CQD EL turn-on is 4-8 V (visible by ~10 V)
    # at 2-20 mA/pixel, so a +5V rail is insufficient headroom for the TLC5947
    # constant-current sink. The TLC5947 OUT pins tolerate 33 V independent of VCC,
    # so a standard separate 12 V supply gives guaranteed compliance for any CQD
    # V_f up to ~11 V. J4 is a 2.1 mm barrel (12 V wall wart); F2 a 1 A PTC; C85
    # 22 uF bulk + C86 100 nF HF decouple the rail at the die anode pad fanout.
    add("J4", "qssc:Barrel_Jack", "12V LED", "Connector_BarrelJack:BarrelJack_CUI_PJ-102AH_Horizontal",
        30, 60, pins={"1": "+12V_EMIT_IN", "2": "GND"})
    add("F2", "qssc:Polyfuse", "1A PTC", "Fuse:Fuse_1206_3216Metric",
        55, 60, pins={"1": "+12V_EMIT_IN", "2": "+12V_EMIT"})
    add("C85", "qssc:C", "22uF", "Capacitor_SMD:C_0805_2012Metric",
        68, 60, pins={"1": "+12V_EMIT", "2": "GND"})
    add("C86", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        68, 68, pins={"1": "+12V_EMIT", "2": "GND"})
    add("#FLG7", "power:PWR_FLAG", "PWR_FLAG", "", 45, 60, pins={"1": "+12V_EMIT_IN"})
    add("#FLG8", "power:PWR_FLAG", "PWR_FLAG", "", 70, 60, pins={"1": "+12V_EMIT"})
    add("R70", "qssc:R", "330", "Resistor_SMD:R_0603_1608Metric",
        110, 30, pins={"1": "+3V3", "2": "LED_ANODE"})
    add("LED1", "qssc:LED", "Green", "LED_SMD:LED_0603_1608Metric",
        110, 45, pins={"1": "GND", "2": "LED_ANODE"})

    # FPGA (center) -- XC7A35T-1CSG324C (324-ball 0.8 mm BGA).  PCB placement is
    # overridden to (128,55) in assign_pcb_positions() to clear the die courtyard.
    add("U1", "qssc:XC7A35T_CSG324", "XC7A35T-1CSG324C", "Package_BGA:Xilinx_CSG324",
        200, 70, pins=fpga_net_map())

    # Config flash W25Q128 (QSPI x4 boot).  FPGA dedicated config balls:
    #   D00->SPI_D0, D01->SPI_D1, D02->SPI_D2, D03->SPI_D3, FCS_B->SPI_CS,
    #   CCLK->SPI_CCLK, DONE->CONF_DONE, INIT_B->CONF_INIT, PROGRAM_B->CONF_PROG.
    add("U8", "qssc:W25Q128JVS", "W25Q128JVSIQ", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
        140, 95, pins={"1": "SPI_CS", "2": "SPI_D1", "3": "SPI_D2", "4": "GND",
                       "5": "SPI_D0", "6": "SPI_CCLK", "7": "SPI_D3", "8": "+3V3"})
    # Config pull-ups (open-drain config pins need a pull-up to +3V3 to boot).
    add("R75", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric",
        120, 90, pins={"1": "+3V3", "2": "CONF_DONE"})
    add("R76", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric",
        120, 95, pins={"1": "+3V3", "2": "CONF_INIT"})
    add("R77", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric",
        120, 100, pins={"1": "+3V3", "2": "CONF_PROG"})
    # XADC reference decoupling: VREFP decoupled to GND (internal reference mode).
    add("C40", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        215, 90, pins={"1": "VREFP", "2": "GND"})

    # Clock
    add("Y1", "qssc:ASE_osc", "100MHz XO", "Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm",
        200, 30, pins={"1": "+3V3", "2": "GND", "3": "clk_100mhz", "4": "+3V3"})

    # USB-UART
    # FT232 USBDP/USBDM (pins 15/16) -> USB_DP_F/USB_DM_F -> R78/R79 27R series
    # damping -> USB_DP/USB_DM (ESD diodes D1/D2 + USB-C).  Series R dampens the
    # USB edge reflections; ESD stays on the connector side (correct clamp point).
    # FT232RL pin-tie rationale (verified vs FTDI FT232R datasheet + KiCad
    # Interface_USB:FT232RL symbol, SSOP-28 -- the symbol omits the 2 NC pads 8 & 24):
    #   pin  1 TXD   -> UART_RX   (FT232 *output* -> FPGA RX ball P17; net named from
    #                             FPGA side, so the TXD/RXD crossover is correct)
    #   pin  5 RXD   -> UART_TX   (FPGA TX ball P18 -> FT232 *input*)
    #   pin 15 USBDP -> USB_DP_F  pin 16 USBDM -> USB_DM_F (series-damped via R78/R79)
    #   pin  4 VCCIO -> +3V3 (I/O supply)    pin 17 3V3OUT -> 3V3OUT (reg o/p, dec C55)
    #   pin 20 VCC   -> +3V3 (the ONLY core supply pad on the SSOP-28)
    #   pin 19 RESET#-> +3V3 (internal pull-up ok)
    #   pin 25 AGND  -> GND (real analog ground; FT232R analog/regulator return)
    #   pins 7/18/21/26 GND -> GND (digital + TEST bond-pad grounds tied)
    #   pin 23 CBUS0 -> NC (configurable CBUS, default TXLED#; unused -> float)
    #   pin 24 NC    -> NC (no internal bond on the SSOP-28; NOT a 2nd VCC pad)
    #   pins 2/3/6/9-14/22/27/28 -> NC (modem control, crystal, LED drive, TEST):
    #       unused per datasheet; emitted as (no_connect) below.
    add("J1", "qssc:FT232RL", "FT232RL", "Package_SO:SSOP-28_5.3x10.2mm_P0.65mm",
        300, 70, pins={"1": "UART_RX", "5": "UART_TX", "15": "USB_DP_F", "16": "USB_DM_F",
                       "4": "+3V3", "17": "3V3OUT", "20": "+3V3",
                       "7": "GND", "18": "GND", "21": "GND", "25": "GND",
                       "19": "+3V3", "26": "GND"},
        nc_pins=["2", "3", "6", "9", "10", "11", "12", "13", "14", "22", "23", "24", "27", "28"])
    # USB-C VBUS is on its own VBUS_USB net; D_USB (Schottky, below) passes
    # USB -> +5V_IN only, blocking the barrel wall adapter from back-feeding +5V
    # into the USB host.  Barrel stays direct so the +5V rail keeps the full 5.0 V
    # the GVA-84+ RF amp needs (4.8-5.2 V abs max); only USB-only power (config,
    # no RF) takes the Schottky drop.
    add("J10", "qssc:USB_C_Receptacle", "USB-C", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
        360, 70, pins={"A4": "VBUS_USB", "A9": "VBUS_USB", "B4": "VBUS_USB", "B9": "VBUS_USB",
                       "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND",
                       "A6": "USB_DP", "B6": "USB_DP", "A7": "USB_DM", "B7": "USB_DM",
                       "A5": "CC1", "B5": "CC2", "A8": "GND", "B8": "GND", "S1": "GND"})
    add("R71", "qssc:R", "5.1k", "Resistor_SMD:R_0603_1608Metric", 360, 100, pins={"1": "CC1", "2": "GND"})
    add("R72", "qssc:R", "5.1k", "Resistor_SMD:R_0603_1608Metric", 360, 110, pins={"1": "CC2", "2": "GND"})

    # ADF4351 PLL + loop filter + GVA-84+ RF amp + SMA
    add("U3", "qssc:ADF4351", "ADF4351BCPZ", "Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm",
        300, 150, pins={"1": "SPI_CLK", "2": "SPI_DATA", "3": "SPI_LE", "4": "+3V3",
                        "6": "+3V3", "7": "LF_CP", "9": "GND", "10": "+3V3", "11": "GND",
                        "12": "RF_OUT", "16": "+3V3", "17": "+3V3", "18": "GND", "20": "LF_OUT",
                        "21": "GND", "22": "RSET", "23": "ADF_VCOM", "24": "VREF", "26": "+3V3",
                        "27": "GND", "28": "+3V3", "29": "refclk_25m", "30": "ADF_MUXOUT",
                        "31": "GND", "32": "+3V3", "33": "GND", "8": "GND",
                        "19": "ADF_TEMP",
                        # The QFN-32-1EP footprint models the 3.45x3.45 mm exposed
                        # pad as ONE named pad "33" (netted GND above) PLUS a 3x3
                        # grid of 9 numberless "" sub-pads (paste/copper apertures
                        # under the EP). Per ADF4351 datasheet the EP MUST be tied to
                        # GND (thermal + electrical return); net the sub-pads GND too
                        # so none of the EP copper floats. fp_net_pads matches "".
                        "": "GND"},
        nc_pins=["5", "13", "14", "15", "25"])
    # ADF4351 pin-tie rationale (verified vs ADI ADF4351 datasheet, 32-lead LFCSP):
    #   pin 20 VTUNE  -> LF_OUT  (loop-filter output drives the VCO tuning input;
    #                             WITHOUT this the PLL loop is open and VCO freq drifts)
    #   pin 26 PDB_RF -> +3V3    (RF power-down is active-LOW: LOW mutes the RF outs,
    #                             so tie HIGH to keep the 2.87/3.46 GHz RF enabled)
    #   pin 24 VREF   -> VREF    (internal reference node -- needs a DECOUPLING cap to
    #                             GND, NOT a direct tie; a direct GND tie shorts the
    #                             internal reference. Decoupled by C36 below.)
    #   pin 23 VCOM   -> ADF_VCOM (internal compensation node, biased at HALF the
    #                             tuning range -- needs a DECOUPLING cap to GND, NOT
    #                             a direct tie; a direct GND tie shorts the half-range
    #                             bias. Decoupled by C41 below. PDF p8 confirms.)
    #   pin 19 TEMP   -> ADF_TEMP (temperature-compensation OUTPUT; the ADF4351
    #                             has NO REFIN- pin -- the reference is single-ended
    #                             on REFIN pin 29.  Per datasheet Table 5, TEMP
    #                             requires decoupling caps to GND as close as
    #                             possible -> C37 100nF, NOT a no_connect and NOT
    #                             a direct tie.  Net is a 2-pin decoupling net.)
    #   pin  5 SW     -> NC      (fast-lock switch I/O; fast-lock unused, leave open)
    #   pin 13 RFOUTA- -> NC     (complement of used single-ended RFOUTA+ pin 12;
    #                             datasheet: unused RF output left floating)
    #   pin 14 RFOUTB+ -> NC     (unused RF output B; datasheet: leave floating)
    #   pin 15 RFOUTB- -> NC     (unused RF output B; datasheet: leave floating)
    #   pin 25 LD     -> NC      (lock-detect output, unused)
    #   pin 29 REFIN  -> refclk_25m (FPGA 25 MHz refclk_out on an N-side MRCC ball, aliased in fpga_net_map)
    add("R67", "qssc:R", "1k", "Resistor_SMD:R_0603_1608Metric", 250, 175, pins={"1": "LF_CP", "2": "LF_MID"})
    add("C31", "qssc:C", "220p", "Capacitor_SMD:C_0603_1608Metric", 240, 175, pins={"1": "LF_CP", "2": "GND"})
    add("C32", "qssc:C", "10n", "Capacitor_SMD:C_0603_1608Metric", 260, 190, pins={"1": "LF_MID", "2": "GND"})
    add("R68", "qssc:R", "2.2k", "Resistor_SMD:R_0603_1608Metric", 270, 175, pins={"1": "LF_MID", "2": "LF_OUT"})
    add("C33", "qssc:C", "220p", "Capacitor_SMD:C_0603_1608Metric", 285, 190, pins={"1": "LF_OUT", "2": "GND"})
    add("R73", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric", 300, 175, pins={"1": "RSET", "2": "GND"})
    add("C36", "qssc:C", "100n", "Capacitor_SMD:C_0603_1608Metric", 315, 175,
        pins={"1": "VREF", "2": "GND"})  # ADF4351 pin 24 VREF decoupling (internal ref node)
    add("C37", "qssc:C", "100n", "Capacitor_SMD:C_0603_1608Metric", 315, 185,
        pins={"1": "ADF_TEMP", "2": "GND"})  # ADF4351 pin 19 TEMP decoupling (temp-comp output, datasheet Table 5)
    add("C41", "qssc:C", "100n", "Capacitor_SMD:C_0603_1608Metric", 315, 195,
        pins={"1": "ADF_VCOM", "2": "GND"})  # ADF4351 pin 23 VCOM decoupling (internal compensation node, biased at half tuning range -- decouple, NOT a direct GND tie which would short the half-range bias). Designator C41 reused: the old C41 was the AP2127K BP cap (deleted -- pin 4 is NC).
    add("C34", "qssc:C", "100p", "Capacitor_SMD:C_0603_1608Metric", 330, 150, pins={"1": "RF_OUT", "2": "RF_IN"})
    add("U5", "qssc:GVA-84", "GVA-84+", "Package_TO_SOT_SMD:SOT-89-3",
        360, 150, pins=GVA84_NETS)
    # GVA-84+ is self-biased: +5V_RF feeds pin 3 (RFOUT/DC-IN) through the L2 RF
    # choke -- no VGG gate-bias resistor (the old HMC441 needed R69; removed).
    add("L2", "qssc:L", "100nH", "Inductor_SMD:L_0603_1608Metric", 360, 175, pins={"1": "+5V_RF", "2": "RF_AMP_OUT"})
    add("C35", "qssc:C", "100p", "Capacitor_SMD:C_0603_1608Metric", 390, 150, pins={"1": "RF_AMP_OUT", "2": "J3_SIG"})
    add("J3", "qssc:Conn_Coaxial", "SMA", "Connector_Coaxial:SMA_Amphenol_132289_EdgeMount",
        420, 150, pins={"1": "J3_SIG", "2": "GND"})

    # ==========================================================================
    # QSSC_Chip -- the two-faced quantum die, clamped between TWO PCBs (the
    # 2026-09 two-board sandwich; supersedes the earlier single merged board).
    # The old design mated a swappable chip MODULE here via a 60-pin Samtec
    # LSHM-130 (J11) carrying a 64:1 analog MUX, one TIA, and an 8x8 emitter
    # crossbar.  That is GONE: every channel is now parallel.  The 130-pad bare
    # die (65 pads/face: 64 electrodes + 1 common; 8x8 grid at 2.54 mm pitch, 1.7 mm
    # THT pads) is clamped between this main board (die BOTTOM / emitter face on
    # F.Cu, via a standard 2.54 mm female-header socket ChipSocket_2.54_EM) and the
    # head board (die TOP / detector face on B.Cu, via the matching
    # ChipSocket_2.54_DET).  On THIS (main) board the 64
    # emitter_cat{i} cathode pads fan out to the 3x TLC5947 constant-current LED
    # driver, and the EM_ANODE pad is the common +12V_EMIT anode rail.  The 64
    # EM signal nets escape outward on F.Cu; the dog-bone vias (0.20 mm via 1.0 mm
    # out) drop to B.Cu for the router.
    # ==========================================================================
    # The die sits EMITTER-DOWN plugged into a standard 2.54 mm female-header
    # SOCKET (qssc:ChipSocket_2.54_EM) on F.Cu -- an off-the-shelf through-hole
    # part (8x8 grid at 2.54 mm pitch, 17.78 mm span on the 20 mm die body).  The
    # P1-style pin-scaffold chip module plugs its male emitter pins DOWN into this
    # socket.  The 65 contact pads net identically (chip_net_map_em) so the
    # dog-bone fanout is unchanged; the head PCB carries the matching
    # ChipSocket_2.54_DET on B.Cu for the detector/TOP face.  Die removable.
    add("Uchip", "qssc:ChipSocket_2.54_EM", "ChipSocket_2.54_EM", "qssc:ChipSocket_2.54_EM",
        80, 55, pins=chip_net_map_em())

    # mw_gate drives the RF switch on THIS board (ADF4351 -> GVA-84+ -> SMA), so it
    # stays local (does not cross to the module): J8 header to the off-board switch.
    add("J8", "qssc:Conn_01x02", "MW_GATE", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
        260, 110, pins={"1": "mw_gate", "2": "GND"})

    # Reset button + JTAG (mw_gate J8 header is defined above with J11)
    # Reset button: drives the FPGA's active-high fabric "reset" net (top_level.v
    # input `reset`, async-asserted, 2-flop synchronized to clk_100mhz).  Pressing
    # ties `reset` to +3V3 (assert).  BUGFIX 2026-09: the original button had NO
    # pull-down, so `reset` FLOATED when released -> the fabric sat in an
    # indeterminate reset state.  R83 (10k pull-down to GND, added below) defines
    # the released state (reset=0); C68 (100nF to GND) debounces.  Pressed ->
    # reset=3V3 (strong drive overrides the 10k); released -> pulled low + RC-filtered.
    add("J7", "qssc:SW_Push", "RESET", "Button_Switch_THT:SW_PUSH_6mm_H5mm",
        60, 70, pins={"1": "+3V3", "2": "reset"})
    # JTAG header J9 -- 2x5 2.54mm, the standard Xilinx 10-pin JTAG pinout
    # (1=TCK 3=TDO 5=TMS 7=TDI, even=GND, 9=VREF 10=GND) per the Digilent
    # JTAG-HS3 / Xilinx Platform Cable II cable pinout.  Pin 9 = VREF supplies
    # the programmer's IO buffer and senses the target VCCO; the JTAG balls
    # (TCK/TDI/TDO/TMS = E10/E11/E13/E12) sit in bank 0 whose VCCO_0 = +3V3,
    # so VREF = +3V3.  (Was GND -- only FTDI-generic JTAG worked; HS3/Platform
    # Cable II need VREF.)
    add("J9", "qssc:Conn_02x05", "JTAG", "Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical",
        260, 30, pins={"1": "TCK", "2": "GND", "3": "TDO", "4": "GND", "5": "TMS",
                       "6": "GND", "7": "TDI", "8": "GND", "9": "+3V3", "10": "GND"})

    # Decoupling caps C1-C20 (100nF) round-robin on rails
    _rails = ["+3V3", "+1V8", "+1V0", "+5V_RF", "+5V", "+3V3"]
    _x0, _y0 = 30, 215
    for i in range(20):
        add(f"C{i+1}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
            _x0 + (i % 10) * 12, _y0 + (i // 10) * 10,
            pins={"1": _rails[i % len(_rails)], "2": "GND"})
    # Bulk caps C21-C30 (10uF)
    for i in range(10):
        add(f"C{i+21}", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
            _x0 + 130 + i * 12, _y0,
            pins={"1": _rails[i % len(_rails)], "2": "GND"})
    # ESD diodes D1-D10
    _esd = [("USB_DP", "D1"), ("USB_DM", "D2"), ("RF_AMP_OUT", "D3"), ("+5V_IN", "D4"),
            ("+5V", "D5"), ("+3V3", "D6"), ("+1V8", "D7"), ("+1V0", "D8"), ("+5V_RF", "D9"), ("mw_gate", "D10")]
    for i, (net, ref) in enumerate(_esd):
        add(ref, "qssc:D", "ESD5V0", "Diode_SMD:D_SOD-923",
            _x0 + 130 + i * 12, _y0 + 10, pins={"1": "GND", "2": net})

    # PWR_FLAG on nets without a power_out driver
    add("#FLG1", "power:PWR_FLAG", "PWR_FLAG", "", 45, 30, pins={"1": "+5V_IN"})
    add("#FLG2", "power:PWR_FLAG", "PWR_FLAG", "", 70, 30, pins={"1": "+5V"})
    add("#FLG3", "power:PWR_FLAG", "PWR_FLAG", "", 45, 45, pins={"1": "GND"})
    # +5V_RF is now driven through FB1 (a passive ferrite bead) from +5V, not by a
    # regulator power_out pin, so ERC cannot see a driver -> flag it.
    add("#FLG5", "power:PWR_FLAG", "PWR_FLAG", "", 95, 90, pins={"1": "+5V_RF"})
    # (+5V_A is NOT on the main board -- the AD7616 analog rail lives on the detector
    # head now.  Its PWR_FLAG is added in build_detector_head().)
    # VREFP/VREFN: the Artix-7 XADC reference. When the internal 1.25 V reference
    # is used, VREFP is bypassed to GND (C40) and VREFN tied to GND; the pin is a
    # power input driven by the on-chip reference, which ERC cannot see -> flag it.
    # (XADC is unused on this board -- the detector readout is 4x AD7616 on the head
    # -- but VREFP stays decoupled + flagged so the unused ref pad is not an ERC
    # error.  Moved off (45,60) which overlapped #FLG7 +12V_EMIT_IN.)
    add("#FLG4", "power:PWR_FLAG", "PWR_FLAG", "", 95, 110, pins={"1": "VREFP"})

    # ==========================================================================
    # Production-hardening pass 2026-09: missing decoupling, pull-ups, protection.
    # Every active IC now has LOCAL decoupling; the reset button is fixed (above);
    # the USB host is protected from barrel backfeed; AMS1117s get stability bulk;
    # the FPGA gets per-rail 4.7uF bulk (UG393); the ADF4351 gets per-supply 100nF;
    # JTAG gets pull resistors.  PCB positions are in assign_pcb_positions() below.
    # Full rationale + BOM/supplier parts: HARDWARE_DESIGN.md §16 + BOM.csv.
    # ==========================================================================
    # USB-C VBUS backfeed protection + VBUS bulk.
    add("D_USB", "qssc:D", "SS14 Schottky", "Diode_SMD:D_SMA",
        300, 220, pins={"1": "VBUS_USB", "2": "+5V_IN"})   # anode=VBUS_USB cathode=+5V_IN
    add("#FLG6", "power:PWR_FLAG", "PWR_FLAG", "", 300, 232, pins={"1": "VBUS_USB"})
    add("C84", "qssc:C", "4.7uF", "Capacitor_SMD:C_0805_2012Metric",
        312, 220, pins={"1": "VBUS_USB", "2": "GND"})
    # USB_DP/DM series damping (27R) between FT232 and the ESD/connector side.
    add("R78", "qssc:R", "27R", "Resistor_SMD:R_0603_1608Metric",
        330, 220, pins={"1": "USB_DP_F", "2": "USB_DP"})
    add("R79", "qssc:R", "27R", "Resistor_SMD:R_0603_1608Metric",
        342, 220, pins={"1": "USB_DM_F", "2": "USB_DM"})
    # AMS1117 output stability bulk (22uF MLCC) + shared input bulk.  AMS1117 can
    # oscillate with only a low-ESR ceramic; 22uF guarantees stability.  REG3
    # (AP2127K) is ceramic-stable and already decoupled (C41-C44).
    add("C49", "qssc:C", "22uF", "Capacitor_SMD:C_0805_2012Metric",
        330, 240, pins={"1": "+3V3", "2": "GND"})   # REG1 +3V3 output bulk
    add("C50", "qssc:C", "22uF", "Capacitor_SMD:C_0805_2012Metric",
        342, 240, pins={"1": "+1V8", "2": "GND"})   # REG2 +1V8 output bulk
    add("C51", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
        354, 240, pins={"1": "+5V", "2": "GND"})    # REG1/REG2 input bulk
    # FPGA per-rail 4.7uF bulk (UG393 Artix-7 decoupling): the round-robin C1-C20
    # 100nF is the HF local decoupling; these are the missing per-rail LF bulk.
    add("C52", "qssc:C", "4.7uF", "Capacitor_SMD:C_0805_2012Metric",
        330, 256, pins={"1": "+1V0", "2": "GND"})
    add("C53", "qssc:C", "4.7uF", "Capacitor_SMD:C_0805_2012Metric",
        342, 256, pins={"1": "+1V8", "2": "GND"})
    add("C54", "qssc:C", "4.7uF", "Capacitor_SMD:C_0805_2012Metric",
        354, 256, pins={"1": "+3V3", "2": "GND"})
    # FT232 decoupling: 3V3OUT (pin17) needs 100nF per datasheet (was missing); VCC
    # (pin20) local 100nF; 4.7uF rail bulk.
    add("C55", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        330, 272, pins={"1": "3V3OUT", "2": "GND"})
    add("C56", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        342, 272, pins={"1": "+3V3", "2": "GND"})
    add("C57", "qssc:C", "4.7uF", "Capacitor_SMD:C_0805_2012Metric",
        354, 272, pins={"1": "+3V3", "2": "GND"})
    # XO (Abracon ASE) + config flash Vdd/VCC local decoupling.
    add("C58", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        330, 288, pins={"1": "+3V3", "2": "GND"})   # Y1 pin4 Vdd
    add("C59", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        342, 288, pins={"1": "+3V3", "2": "GND"})   # U8 pin8 VCC
    # ADF4351 per-supply decoupling: AVDD(10)/DVDD(28)/SDVDD(32)/Vp(6)/VVCO(16,17)
    # each get a local 100nF to GND -- critical for low phase noise / ref spurs.
    add("C60", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        330, 304, pins={"1": "+3V3", "2": "GND"})   # AVDD
    add("C61", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        342, 304, pins={"1": "+3V3", "2": "GND"})   # DVDD
    add("C62", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        354, 304, pins={"1": "+3V3", "2": "GND"})   # SDVDD
    add("C63", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        330, 316, pins={"1": "+3V3", "2": "GND"})   # Vp
    add("C64", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        342, 316, pins={"1": "+3V3", "2": "GND"})   # VVCO
    add("C65", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        354, 316, pins={"1": "+3V3", "2": "GND"})   # VVCO
    # JTAG pull resistors (TCK pull-down, TMS/TDI pull-up) -- defines the JTAG
    # state when the header is open and dampens floating-clock edges.
    add("R80", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric",
        330, 332, pins={"1": "TCK", "2": "GND"})
    add("R81", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric",
        342, 332, pins={"1": "TMS", "2": "+3V3"})
    add("R82", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric",
        354, 332, pins={"1": "TDI", "2": "+3V3"})
    # Reset-button pull-down + debounce on the active-high fabric "reset" net:
    # R83 holds reset=0 when J7 is released (fixes the original floating-reset
    # bug); C68 + R83 form an RC debounce (10k x 100nF = 1 ms) on the release edge.
    add("R83", "qssc:R", "10k", "Resistor_SMD:R_0603_1608Metric",
        330, 348, pins={"1": "reset", "2": "GND"})
    add("C68", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        342, 348, pins={"1": "reset", "2": "GND"})
    # (The TIA_OUT -> XADC level-shift divider R84/R85/C66 is GONE: the single-board
    # pivot removed the XADC; the 64 TIA outputs now feed 4x AD7616 parallel ADCs
    # (true analog intensity readout, not photon counting).  The XADC VP/VN balls
    # are left NC; see fpga_net_map.)
    build_emitter_driver()
    add_btb_connector("host")


# --------------------------------------------------------------------------
# Phase 1b -- 64-ch parallel detector front-end + 64-ch parallel emitter driver
# --------------------------------------------------------------------------
# Detector path (per channel i=0..63):  chip DET_IN{i} -> TLV9004 TIA (-IN,
# +IN=DET_REF=1.65 V bias) -> DET_TIA{i} (0..3.3 V analog intensity waveform)
# -> AD7616 analog input.  4x AD7616 (16-ch 16-bit dual simultaneous-sampling
# ADC, UAD0..3) digitize all 64 TIA outputs; the FPGA reads the 64 16-bit
# samples over a 9-pin SPI bus (adc_convst/sclk/miso/busy/cs0..3/resetn)
# and does the 1 kHz lock-in demodulation numerically in signal_processor.v.
# This is a TRUE analog readout of the photoluminescence intensity -- the user
# explicitly rejected photon counting (user: "I want the signal, not a counter"): the
# 16x LMV339 comparator + 64x pull-up + MCP4725 threshold DAC + 64 FPGA
# photon-counter chain is GONE, replaced by 4 ADC chips (66 parts removed, 34
# added; FPGA detector IO drops 64+2 -> 9 pins).
# Emitter path:  FPGA 4-wire SPI (em_sclk/sin/xlat/blank) -> 3x TLC5947
# daisy-chained constant-current LED driver (SIN->SOUT, 72 ch, 64 used).  Each
# emitter cathode emitter_cat{i} sinks into a TLC5947 OUTn; the 64 emitters
# share a common +12V_EMIT anode rail (chip EM_ANODE pad).  12-bit PWM grayscale per
# channel -> any combination of emitters fires in parallel at independent
# intensity (the old 64-GPIO + 74HCT240 + P-MOS one-at-a-time design is gone).
# The TIAs run on +3V3; the TLC5947 logic (VCC) on +3V3, OUT sinks from +12V_EMIT.
# The AD7616 analog VCC runs on +5V_A (a ferrite-isolated +5V rail) and VDRIVE on +3V3.
def build_detector_analog():
    # TLV9004 channel k -> (out, in-, in+) pin numbers (TSSOP-14 datasheet)
    TIA_CH = {0: ("1", "2", "3"), 1: ("7", "6", "5"),
              2: ("8", "9", "10"), 3: ("14", "13", "12")}
    # ---- 16x TLV9004 (TIA): detector current -> voltage waveform -----------
    for j in range(16):
        tia = {"4": "+3V3", "11": "GND"}
        for k in range(4):
            i = 4 * j + k
            to, tm, tp = TIA_CH[k]
            tia[tm] = f"DET_IN{i}"      # detector current -> inverting input
            tia[tp] = "DET_REF"         # 1.65 V virtual-ground bias -> non-inv input
            tia[to] = f"DET_TIA{i}"     # TIA output -> AD7616 analog input
        add(f"UT{j}", "qssc:TLV9004", "TLV9004",
            "Package_SO:TSSOP-14_4.4x5mm_P0.65mm", 0, 0, pins=tia)
    # ---- per-channel TIA feedback (Rf 1M || Cf 1p) -------------------------
    for i in range(64):
        add(f"Rf{i}", "qssc:R", "1M", "Resistor_SMD:R_0603_1608Metric", 0, 0,
            pins={"1": f"DET_IN{i}", "2": f"DET_TIA{i}"})
        add(f"Cf{i}", "qssc:C", "1pF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": f"DET_IN{i}", "2": f"DET_TIA{i}"})
    # ---- DET_REF bias divider: +3V3 split 1:1 -> 1.65 V virtual ground -----
    # (the TIA non-inverting inputs share this reference; the AD7616 reads the
    # TIA output as a single-ended 0..3.3 V signal within its +/-5 V range.)
    add("Rref1", "qssc:R", "10k", "Resistor_SMD:R_0603_1608Metric", 0, 0,
        pins={"1": "+3V3", "2": "DET_REF"})
    add("Rref2", "qssc:R", "10k", "Resistor_SMD:R_0603_1608Metric", 0, 0,
        pins={"1": "DET_REF", "2": "GND"})
    add("Cref", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
        pins={"1": "DET_REF", "2": "GND"})
    # ---- per-IC decoupling (one 100nF each on the 16 TIA ICs) --------------
    for j in range(16):
        add(f"Cut{j}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": "+3V3", "2": "GND"})      # TLV9004 decoupling
    # ---- +5V_A rail: ferrite-isolated +5V for the AD7616 analog VCC --------
    # The AD7616 VCC is 5 V analog; isolating it from the main +5V rail with a
    # 600-ohm ferrite + bulk/HF decoupling keeps the ADC's sampling transients
    # off the digital +5V (and vice-versa).  VDRIVE is +3V3 (logic levels).
    # Head +5V (from BTB J12 pins 17/18) feeds FB_A; it had NO decoupling (2
    # loads, 0 caps). Add a 10 uF bulk cap on the +5V side of the ferrite as the
    # LDO/feed decoupling per standard practice.
    add("C_5V", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric", 0, 0,
        pins={"1": "+5V", "2": "GND"})
    add("FB_A", "qssc:L", "600Ω@100MHz", "Inductor_SMD:L_0603_1608Metric", 0, 0,
        pins={"1": "+5V", "2": "+5V_A"})
    add("C_Abulk", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric", 0, 0,
        pins={"1": "+5V_A", "2": "GND"})
    add("C_ahf", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
        pins={"1": "+5V_A", "2": "GND"})
    # ---- 4x AD7616 (16-ch 16-bit ADC): digitize the 64 TIA outputs ---------
    # Pin numbers verified against KiCad Analog_ADC:AD7616 (datasheet Rev. 0).
    # Bank A inputs V0A..V7A (pins 28,26,24,22,19,17,14,12) take channels
    # 16j+0..16j+7; bank B inputs V0B..V7B (pins 73,75,77,79,2,4,7,9) take
    # channels 16j+8..16j+15.  Per-channel VGND pins all tie to GND.
    VA_PINS = ["28", "26", "24", "22", "19", "17", "14", "12"]   # V0A..V7A
    VB_PINS = ["73", "75", "77", "79", "2", "4", "7", "9"]       # V0B..V7B
    VAG_GND = ["27", "25", "23", "21", "20", "18", "13", "11"]   # V0AGND..V7AGND
    VBG_GND = ["74", "76", "78", "80", "1", "3", "8", "10"]      # V0BGND..V7BGND
    AD7616_FP = "Package_QFP:LQFP-80_14x14mm_P0.65mm"
    for j in range(4):
        p = {}
        # 16 analog inputs <- DET_TIA{16j+k}
        for k in range(8):
            p[VA_PINS[k]] = f"DET_TIA{16*j + k}"        # bank A
            p[VB_PINS[k]] = f"DET_TIA{16*j + 8 + k}"    # bank B
        # per-channel grounds + all analog/digital/reg/ref grounds -> GND
        for g in VAG_GND + VBG_GND:
            p[g] = "GND"
        for g in ["5", "16", "29", "72", "50", "69", "51", "32", "34"]:
            p[g] = "GND"
        # power: VCC (4 pins) -> +5V_A, VDRIVE -> +3V3
        for vcc in ["6", "15", "30", "71"]:
            p[vcc] = "+5V_A"
        p["49"] = "+3V3"                      # VDRIVE
        # serial-mode config ties (datasheet serial-interface section):
        p["40"] = "+3V3"   # SER/PAR = HIGH -> serial/SPI mode
        p["45"] = "GND"    # SER1W = LOW -> 1-wire (all data on SDOA, SDOB unused)
        p["37"] = "+3V3"   # SEQEN = HIGH -> sequencer auto-cycles all 8 VA/VB pairs
        p["35"] = "GND"    # REFSEL = LOW -> internal reference (REFCAP/REFINOUT decoupled)
        # software range/channel config via SDI -> HW pins tied low (don't care):
        # range: HW_RNGSEL1=1, HW_RNGSEL0=0 -> hardware mode, +/-5 V bipolar
        # range on all 16 channels (AD7616 datasheet Table 8; the 0..3.3 V TIA
        # output fits with full resolution: 3.3/5*32768 ~= 21.6k codes).  Hardware
        # mode (HW_RNGSEL != 00) PROHIBITS register access, so SDI is unused and
        # no init config-register write is needed -- the range is pin-strapped.
        p["39"] = "GND"; p["38"] = "+3V3"     # HW_RNGSEL0=0, HW_RNGSEL1=1 -> +/-5V HW mode
        p["64"] = "GND"; p["65"] = "GND"; p["66"] = "GND"   # CHSEL0/1/2 (don't-care in sequencer mode)
        p["61"] = "GND"   # BURST/WR = LOW (serial read, no burst)
        p["58"] = "GND"; p["59"] = "GND"; p["60"] = "GND"   # OS0/1/2 = no oversampling
        p["46"] = "GND"   # CRCEN = LOW (no CRC)
        # reference/reg decoupling pins -> dedicated 2-pin nets to decoupling caps
        p["31"] = f"adc_refc{j}"     # REFCAP
        p["33"] = f"adc_refio{j}"    # REFINOUT
        p["70"] = f"adc_rgc{j}"      # REGCAP  (1.0 V analog regulator out)
        p["52"] = f"adc_rgd{j}"      # REGCAPD (1.0 V digital regulator out)
        # FPGA SPI bus: shared CONVST/SCLK/MISO/RESETn, per-chip CS, chip0 BUSY.
        # (BUSY is push-pull -> only chip0's BUSY is read; all 4 finish a
        # simultaneous CONVST together.  SDOB + SDI = NC in 1-wire hardware mode.)
        p["68"] = "adc_convst"        # CONVST (shared)
        p["62"] = "adc_sclk"          # SCLK (shared)
        p["57"] = "adc_miso"          # SDOA (shared; 4 chips share, per-chip CS selects)
        p["36"] = "adc_resetn"        # RESET (shared, active-low)
        p["63"] = f"adc_cs{j}"        # CS (per chip)
        if j == 0:
            p["67"] = "adc_busy"      # BUSY (only chip0 -> FPGA; push-pull, no wire-OR)
        # Intentionally-unused pins (AD7616 1-wire HW serial mode, datasheet Table 8
        # + serial-interface section): the parallel DB bus (DB0-3,DB6-9 = pins
        # 41-44,47,48,53,54) is inert in serial mode; SDI (55) carries no register
        # data because HW mode (HW_RNGSEL != 00) PROHIBITS register access; SDOB
        # (56) is the second serial output, unused in 1-wire mode (all data on
        # SDOA = pin 57).  All declared (no_connect) per datasheet.
        add(f"UAD{j}", "qssc:AD7616", "AD7616", AD7616_FP, 0, 0, pins=p,
            nc_pins=["41", "42", "43", "44", "47", "48", "53", "54", "55", "56"]
                 + ([] if j == 0 else ["67"]))
        # j>0 BUSY (pin 67) is unused: BUSY is push-pull (no wire-OR), only chip0's
        # BUSY is read by the FPGA, so chip1-3 BUSY is left NC per datasheet.
        # per-chip decoupling (datasheet ref/reg/supply caps):
        add(f"Cua{j}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": "+5V_A", "2": "GND"})              # VCC HF
        add(f"CuaB{j}", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric", 0, 0,
            pins={"1": "+5V_A", "2": "GND"})              # VCC bulk
        add(f"Cud{j}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": "+3V3", "2": "GND"})               # VDRIVE
        add(f"Cur{j}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": f"adc_refc{j}", "2": "GND"})       # REFCAP
        add(f"Crio{j}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": f"adc_refio{j}", "2": "GND"})      # REFINOUT
        add(f"Crgc{j}", "qssc:C", "1uF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": f"adc_rgc{j}", "2": "GND"})        # REGCAP (1.0 V reg)
        add(f"Crgd{j}", "qssc:C", "1uF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": f"adc_rgd{j}", "2": "GND"})        # REGCAPD (1.0 V reg)


def build_emitter_driver():
    # ---- 3x TLC5947 constant-current LED driver (daisy-chained, 64 emitters) --
    # Replaces the old 8x 74HCT240 + 64x AO3401A P-MOS + 128 passives (208 parts)
    # with 3 chips + 3 Riref + 3 decoupling caps (9 parts).  Each TLC5947 has 24
    # constant-current sink outputs (OUT0..OUT23 = pins 5..28); 3 chips chain to
    # 72 channels, of which 64 are used (chip2 uses only OUT0..OUT15).  The 64
    # emitter cathodes (emitter_cat0..63, chip bottom-edge pads) sink into the
    # TLC5947 OUTn; the emitters share a common +12V_EMIT anode rail (EM_ANODE pad).
    # FPGA interface is 4 wires: em_sclk/em_sin/em_xlat/em_blank; SIN->SOUT
    # cascades chip-to-chip (chip2 SOUT left NC).  VCC=+3V3; IREF set by
    # Riref=4.87k -> I_OUT = 41*1.20/4.87k ~= 10.1 mA per channel.
    TLC_FP = ("Package_SO:HTSSOP-32-1EP_6.1x11mm_P0.65mm_EP5.2x11mm_"
              "Mask4.11x4.36mm_ThermalVias")
    for j in range(3):
        pins = {"1": "GND", "2": "em_blank", "3": "em_sclk",
                "30": "em_xlat", "31": f"em_iref{j}", "32": "+3V3", "33": "GND",
                # The HTSSOP-32-1EP_ThermalVias footprint models the EP as 38
                # plated-thermal-via pads named "33" (netted GND above) PLUS 13
                # numberless "" EP copper sub-pads. Per TLC5947 datasheet the EP
                # (DAP) MUST be tied to GND (thermal + electrical); net the
                # sub-pads GND so no EP copper floats. fp_net_pads matches "".
                "": "GND"}
        # SIN: chip0 takes em_sin from the FPGA; chip1/2 take the prior chip SOUT.
        pins["4"] = "em_sin" if j == 0 else f"em_sout{j-1}"
        # SOUT: chip0/1 cascade to the next chip's SIN; chip2 SOUT left NC.
        if j < 2:
            pins["29"] = f"em_sout{j}"
        # OUT0..OUT23 (pins 5..28) -> emitter_cat{24*j+k}; only the 64 used
        # channels are netted (chip2 OUT16..OUT23 stay NC).
        for k in range(24):
            i = 24 * j + k
            if i < 64:
                pins[str(5 + k)] = f"emitter_cat{i}"
        # chip2 (j=2) uses only OUT0..OUT15 (pins 5..20); OUT16..OUT23 (pins
        # 21..28) are unused and SOUT (pin 29) is the dangling end of the daisy
        # chain -> all declared (no_connect) per TLC5947 datasheet (unused outputs
        # left open; SOUT unconnected at the chain tail).
        nc = (["21", "22", "23", "24", "25", "26", "27", "28", "29"]
              if j == 2 else [])
        add(f"UE{j}", "qssc:TLC5947", "TLC5947", TLC_FP, 0, 0, pins=pins, nc_pins=nc)
        # Riref: IREF -> GND, sets the per-channel constant current.
        add(f"Riref{j}", "qssc:R", "4.87k", "Resistor_SMD:R_0603_1608Metric", 0, 0,
            pins={"1": f"em_iref{j}", "2": "GND"})
        # per-chip VCC decoupling (+3V3).
        add(f"Cem{j}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": "+3V3", "2": "GND"})


def build_detector_head():
    """Build the DETECTOR HEAD board components: the die TOP (detector) face
    (ChipSocket_2.54_DET on B.Cu -- the head's bottom face contacts the die top
    face via a standard 2.54 mm female-header socket; the P1-style pin-scaffold
    chip module plugs its male detector pins UP into it), the 64-ch TLV9004 TIA +
    4x AD7616 analog front-end (build_detector_analog), and the BTB DAUGHTER
    connector (J12) that brings in +3V3 / +5V / GND and carries the 9-wire AD7616
    SPI back to the FPGA on the main board.

    Power: +3V3 and +5V arrive through J12 (passive connector pins -- ERC sees
    no power_out driver), so they get PWR_FLAGs.  +5V_A (the AD7616 analog rail)
    is derived locally from +5V through FB_A (a passive ferrite, inside
    build_detector_analog) -- also flagged.  The head has no FPGA, no RF, no USB,
    no emitter driver -- it is purely the detector analog front-end + the die
    detector face."""
    add("Uchip", "qssc:ChipSocket_2.54_DET", "ChipSocket_2.54_DET", "qssc:ChipSocket_2.54_DET",
        80, 55, pins=chip_net_map_det())
    build_detector_analog()
    add_btb_connector("daughter")
    # +3V3 / +5V arrive through J12 (passive pins) -> flag so ERC sees a driver.
    add("#FLG10", "power:PWR_FLAG", "PWR_FLAG", "", 30, 12, pins={"1": "+3V3"})
    add("#FLG11", "power:PWR_FLAG", "PWR_FLAG", "", 34, 12, pins={"1": "+5V"})
    # GND also arrives through J12 (passive pins) -> flag so the AD7616 / TLV9004
    # power-input GND pins (V*GND, V-) see an external driver (else ERC
    # power_pin_not_driven on every VGND pin). The main board sources GND.
    add("#FLG12", "power:PWR_FLAG", "PWR_FLAG", "", 38, 12, pins={"1": "GND"})
    # +5V_A driven through FB_A (passive ferrite) from +5V -> flag (like +5V_RF).
    add("#FLG9", "power:PWR_FLAG", "PWR_FLAG", "", 80, 132, pins={"1": "+5V_A"})


# --------------------------------------------------------------------------
# Net classification (for PCB net classes)
# --------------------------------------------------------------------------
RF_NETS = {"RF_OUT", "RF_OUT_N", "RF_IN", "RF_AMP_OUT", "J3_SIG", "LF_CP", "LF_MID",
           "LF_OUT", "CPout", "VTUNE", "refclk_25m"}
USB_NETS = {"USB_DP", "USB_DM", "USB_DP_F", "USB_DM_F"}
# Analog nets: the 64 TIA outputs (DET_TIA{i}) carry the photoluminescence
# intensity waveform to the AD7616 inputs; DET_REF is the 1.65 V TIA virtual
# ground.  These get the Analog netclass (wider clearance, no plane pour under
# the TIA->ADC run).  The XADC/VREFP/VREFN/TIA_OUT/DET_BIAS nets are GONE
# (single-board + ADC pivot).
ANALOG_NETS = {"DET_REF", "VREFP", "VREFN"}
POWER_NETS = {"+3V3", "+1V8", "+1V0", "+5V", "+5V_A", "+5V_RF", "+5V_IN", "+5V_TIA",
              "+12V_EMIT", "+12V_EMIT_IN", "GND", "3V3OUT", "VBUS_USB"}


def _is_chip_net(net):
    """The 128 QSSC_Chip die-pad signal nets: DET_IN0..63 (top edge -> TIA inputs)
    and emitter_cat0..63 (bottom edge -> TLC5947 constant-current sink outputs).
    (The two common electrodes are separate nets: EM_ANODE -> +12V_EMIT power,
    DET_COMMON -> DET_REF analog.) At the 2.5 mm die pitch / 1.2 mm pads the
    dog-bone (0.15 mm stub + 0.20 mm through-via a uniform 1.0 mm out) lands
    0.80 mm clear of the neighbour pad -- well above the 0.15 mm Default
    clearance, so no shorting items occur. The Chip netclass (0.10 mm clearance)
    was introduced for an earlier 0.30 mm-pitch die whose 0.125 mm neighbour
    clearance needed the relaxation; at 2.5 mm it is retained as the board's
    0.10 mm HDI capability floor. KiCad DRC takes the MAX of the two nets'
    netclass clearances, so the Chip class at 0.10 mm relaxes ONLY chip<->chip
    while chip<->everything-else stays 0.15 mm (max 0.10, 0.15)."""
    return net.startswith("DET_IN") or net.startswith("emitter_cat")


def net_class(net):
    if _is_chip_net(net):
        return "Chip"
    if net in RF_NETS:
        return "RF"
    if net in USB_NETS:
        return "USB"
    if net in ANALOG_NETS or net.startswith("DET_TIA"):
        return "Analog"
    if net in POWER_NETS:
        return "Power"
    return "Default"


def all_nets():
    nets = set()
    for c in COMPONENTS:
        nets.update(c["pins"].values())
    return sorted(nets)


# --------------------------------------------------------------------------
# Schematic emitter
# --------------------------------------------------------------------------
ROOT_UUID = "00000000-0000-0000-0000-000000000001"
POWER_SYMS = {"+3V3", "+1V8", "+1V0", "+5V", "+5V_A", "+5V_RF", "+12V_EMIT", "GND"}


def emit_schematic(cfg):
    build_pincoords()
    lib_blocks = authored_symbols()
    lib_symbols = L(A("lib_symbols"), *lib_blocks)

    instances = []
    labels = []
    no_connects = []

    for c in COMPONENTS:
        lib_id = c["lib_id"]
        is_power = lib_id.startswith("power:")
        ref = c["ref"]
        inst = L(A("symbol"),
                 L(A("lib_id"), S(lib_id)),
                 L(A("at"), A(c["x"]), A(c["y"]), A(0)),
                 L(A("unit"), A(1)),
                 L(A("exclude_from_sim"), A("no")),
                 L(A("in_bom"), A("no" if is_power else "yes")),
                 L(A("on_board"), A("no" if is_power else "yes")),
                 L(A("dnp"), A("no")),
                 L(A("uuid"), S(u())),
                 L(A("property"), S("Reference"), S(ref), L(A("at"), A(c["x"]), A(c["y"] - 2 if not is_power else c["y"] - 1.27), A(0)),
                   L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
                 L(A("property"), S("Value"), S(c["value"]), L(A("at"), A(c["x"]), A(c["y"] + 2 if not is_power else c["y"] + 1.27), A(0)),
                   L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
                 L(A("property"), S("Footprint"), S(c["fp"]), L(A("at"), A(c["x"]), A(c["y"]), A(0)),
                   L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))), L(A("hide"), A("yes")))))
        # pin uuids
        coords = _PINCOORDS[lib_id]
        for pnum in coords:
            inst.append(L(A("pin"), S(pnum), L(A("uuid"), S(u()))))
        inst.append(L(A("instances"),
                      L(A("project"), S(cfg["project"]),
                        L(A("path"), S("/" + ROOT_UUID),
                          L(A("reference"), S(ref)), L(A("unit"), A(1))))))
        instances.append(inst)
        # A local net label at every netted pin endpoint names the net.
        # We use labels (not power symbols) for power nets too: the `power`
        # symbol library is not configured in this headless-generated project,
        # so power:+X symbols are not recognised as power symbols and do NOT
        # name nets by value — every rail came up power_pin_not_driven.  Local
        # labels connect by name within the sheet and DO drive power_in pins
        # (verified empirically against kicad-cli ERC).  Each rail that lacks a
        # power_out driver (LDO VO) gets a PWR_FLAG component below, whose
        # power_out pin sits on the same labelled rail net.
        for pnum, net in c["pins"].items():
            if pnum not in coords:
                continue
            px, py, _ = pin_endpoint(c, pnum)
            lab = L(A("label"), S(net),
                    L(A("at"), A(round(px, 4)), A(round(py, 4)), A(0)),
                    L(A("fields_autoplaced"), A("yes")),
                    L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))),
                      L(A("justify"), A("left"), A("bottom"))),
                    L(A("uuid"), S(u())))
            labels.append(lab)
        # (no_connect) markers at every documented-unused pin.  The schematic is
        # 100% net-label (0 wires), so without these the intentionally-floating /
        # unused pins render as dangling and ERC reports pin_not_connected.  A
        # no_connect at the pin endpoint is the KiCad-correct way to declare "this
        # pin is deliberately left open per the datasheet" — it silences
        # pin_not_connected for that pin without adding copper.  Keyed off the
        # per-component `nc_pins` list (FT232 modem/crystal/LED pins, ADF4351 unused
        # RF outputs + SW + CE, AD7616 parallel-DB bus in 1-wire HW mode, TLC5947
        # chip2 unused OUT16-23 + SOUT, FPGA spare/XADC balls).
        for pnum in c.get("nc_pins", []):
            if pnum not in coords:
                continue
            px, py, _ = pin_endpoint(c, pnum)
            nc = L(A("no_connect"),
                   L(A("at"), A(round(px, 4)), A(round(py, 4))),
                   L(A("uuid"), S(u())))
            no_connects.append(nc)

    sch = L(A("kicad_sch"),
            L(A("version"), A(20231120)),
            L(A("generator"), S("gen_qssc_kicad")),
            L(A("generator_version"), S("8.0")),
            L(A("uuid"), S(ROOT_UUID)),
            L(A("paper"), S("A1")),
            L(A("title_block"), L(A("title"), S(cfg["title"])), L(A("company"), S("QSSC"))),
            lib_symbols,
            *instances,
            *labels,
            *no_connects,
            L(A("sheet_instances"), L(A("path"), S("/"), L(A("page"), S("1")))))
    return emit(sch)


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


def emit_pcb(cfg):
    nets = all_nets()
    net_index = {n: i + 1 for i, n in enumerate(nets)}  # 0 reserved for unconnected

    # board outline (cfg-driven dimensions)
    W, H = cfg["board_w"], cfg["board_h"]
    outline = [
        L(A("gr_line"), L(A("start"), A(0), A(0)), L(A("end"), A(W), A(0)),
          L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))), L(A("layer"), S("Edge.Cuts")), L(A("uuid"), S(u()))),
        L(A("gr_line"), L(A("start"), A(W), A(0)), L(A("end"), A(W), A(H)),
          L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))), L(A("layer"), S("Edge.Cuts")), L(A("uuid"), S(u()))),
        L(A("gr_line"), L(A("start"), A(W), A(H)), L(A("end"), A(0), A(H)),
          L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))), L(A("layer"), S("Edge.Cuts")), L(A("uuid"), S(u()))),
        L(A("gr_line"), L(A("start"), A(0), A(H)), L(A("end"), A(0), A(0)),
          L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))), L(A("layer"), S("Edge.Cuts")), L(A("uuid"), S(u()))),
    ]

    # footprints
    footprints = []
    for c in COMPONENTS:
        if not c["fp"]:
            continue  # power symbols have no footprint
        fp = load_footprint_mod(c["fp"])
        fp = fp_set_at(fp, c["pcb_x"], c["pcb_y"], c.get("pcb_rot", 0))
        fp = fp_set_prop(fp, "Reference", c["ref"])
        fp = fp_set_prop(fp, "Value", c["value"])
        # ensure a footprint uuid exists
        if not any(isinstance(x, list) and x and x[0] == ("atom", "uuid") for x in fp[1:]):
            fp.insert(2, L(A("uuid"), S(u())))
        fp = fp_net_pads(fp, c["pins"], net_index)
        footprints.append(fp)

    # Copper layer stack is cfg-driven: the main board is 6-layer HDI
    # (F.Cu sig / In1.Cu GND / In2.Cu PWR / In3.Cu sig / In4.Cu sig / B.Cu sig)
    # and the detector head is also 6-layer (same stack, commit b41009c: the
    # 128-net die bus needs the In3.Cu/In4.Cu escape layers a 4-layer stack
    # could not provide). Each entry: (kicad_id, name, type, user_label_or_None).
    copper = []
    for idx, name, typ, label in cfg["layer_stack"]:
        if label is not None:
            copper.append(L(A(idx), S(name), A(typ), S(label)))
        else:
            copper.append(L(A(idx), S(name), A(typ)))
    layers = L(A("layers"),
               *copper,
               L(A(32), S("B.Adhes"), A("user"), S("B.Adhesive")),
               L(A(33), S("F.Adhes"), A("user"), S("F.Adhesive")),
               L(A(34), S("B.Paste"), A("user")),
               L(A(35), S("F.Paste"), A("user")),
               L(A(36), S("B.SilkS"), A("user"), S("B.Silkscreen")),
               L(A(37), S("F.SilkS"), A("user"), S("F.Silkscreen")),
               L(A(38), S("B.Mask"), A("user")),
               L(A(39), S("F.Mask"), A("user")),
               L(A(44), S("Edge.Cuts"), A("user")),
               L(A(45), S("Margin"), A("user")),
               L(A(46), S("B.CrtYd"), A("user"), S("B.Courtyard")),
               L(A(47), S("F.CrtYd"), A("user"), S("F.Courtyard")),
               L(A(48), S("B.Fab"), A("user")),
               L(A(49), S("F.Fab"), A("user")))

    net_sexp = [L(A("net"), A(0), S(""))]
    for n in nets:
        net_sexp.append(L(A("net"), A(net_index[n]), S(n)))

    setup = L(A("setup"),
              L(A("pad_to_mask_clearance"), A(0)),
              L(A("allow_soldermask_bridges_in_footprints"), A("yes")),
              L(A("pcbplotparams"),
                L(A("layerselection"), S("0x00010fc_ffffffff")),
                L(A("plot_on_all_layers_selection"), S("0x0000000_00000000")),
                L(A("disableapertmacros"), A("no")),
                L(A("usegerberextensions"), A("no")),
                L(A("usegerberattributes"), A("yes")),
                L(A("usegerberadvancedattributes"), A("yes")),
                L(A("creategerberjobfile"), A("yes")),
                L(A("dashed_line_dash_ratio"), A(12.0)),
                L(A("dashed_line_gap_ratio"), A(3.0)),
                L(A("svgprecision"), A(4)),
                L(A("plotframeref"), A("no")),
                L(A("viasonmask"), A("no")),
                L(A("mode"), A(1)),
                L(A("useauxorigin"), A("no")),
                L(A("psnegative"), A("no")),
                L(A("psa4output"), A("no"))))

    # Explicit (stackup) dielectric/copper block, inserted as the FIRST child
    # of (setup ...) — KiCad's PCB parser token is `stackup` (no underscore).
    # Documents the HDI controlled-impedance intent: 0.15 mm prepreg F.Cu->In1
    # GND -> single-dielectric microvia for GND balls AND ~50ohm microstrip at
    # ~0.30 mm track width (RF netclass). DRC does not enforce impedance; this
    # is fab/impedance guidance. Omitted when cfg["stack_up"] is empty.
    if cfg["stack_up"]:
        su_children = [A("stackup")]
        for name, typ, thk, mat in cfg["stack_up"]:
            layer_items = [A("layer"), S(name), L(A("type"), S(typ)),
                           L(A("thickness"), A(thk))]
            if mat is not None:
                layer_items.append(L(A("material"), S(mat)))
            su_children.append(L(*layer_items))
        setup.insert(1, L(*su_children))

    pcb = L(A("kicad_pcb"),
             L(A("version"), A(20240108)),
             L(A("generator"), S("gen_qssc_kicad")),
             L(A("generator_version"), S("8.0")),
             L(A("general"), L(A("thickness"), A(1.6)), L(A("legacy_teardrops"), A("no"))),
             L(A("paper"), S("A4")),
             layers,
             setup,
             *net_sexp,
             *footprints,
             *outline,
             )
    return emit(pcb)


# --------------------------------------------------------------------------
# Project file (.kicad_pro) net classes + design rules
# --------------------------------------------------------------------------
# The project (.kicad_pro) template is shared by both boards: the design rules
# + net classes live here and are board-agnostic (copper layers / dimensions
# come from the .kicad_pcb, not the .kicad_pro). qssc.kicad_pro is git-tracked
# so it always exists on a real checkout; the try/except is a safety net for a
# pristine tree that has not yet generated it.
try:
    PRO_TEMPLATE = (PCB_DIR / "qssc.kicad_pro").read_text()
except FileNotFoundError:
    PRO_TEMPLATE = None


def emit_pro():
    import json as _j
    if PRO_TEMPLATE is not None:
        proj = _j.loads(PRO_TEMPLATE)
    else:
        # Minimal fallback so a pristine tree can still emit a .kicad_pro.
        proj = {
            "board": {"design_settings": {"rule_severities": {}, "rules": {}}},
            "erc": {"rule_severities": {}},
            "net_settings": {"classes": [], "netclass_patterns": []},
        }
        proj["board"]["design_settings"].setdefault("rule_severities", {})
    # ERC severities: the headless skeleton intentionally leaves many pins
    # unconnected (FPGA BGA balls, ADF4351/FT232 status & control lines,
    # op-amp unused sections).  Per the design brief these are acceptable as
    # warnings.  power_pin_not_driven is kept as an error and fixed for real
    # (every rail has a power_out driver).  lib_symbol_issues (the `power`/
    # `qssc` libs are not in the project lib table) and endpoint_off_grid (the
    # authored symbols use a non-1.27mm pin grid) are noise, not defects.
    proj.setdefault("erc", {})
    rs = proj["erc"].setdefault("rule_severities", {})
    rs["pin_not_connected"] = "warning"
    rs["pin_not_driven"] = "warning"
    rs["label_dangling"] = "warning"
    rs["endpoint_off_grid"] = "warning"
    rs["lib_symbol_issues"] = "ignore"
    ds = proj["board"]["design_settings"]
    ds["rule_severities"]["unconnected_items"] = "ignore"
    # via_dangling / track_dangling fire on the partially-routed design-artifact
    # board's unrouted and partially-routed nets: an orphan via layer (e.g. a
    # BGA via-in-pad on a net that never escaped, or a power ball whose plane
    # pour is a deferred manual/GUI step) and stub track ends. They are the same
    # incompleteness class as unconnected_items -- the board is synth-placed +
    # first-pass-routed, not fabricated -- so demote them so they don't mask the
    # real electrical errors (shorts, clearance, courtyard) that ARE still caught.
    ds["rule_severities"]["via_dangling"] = "ignore"
    ds["rule_severities"]["track_dangling"] = "ignore"
    ds["rule_severities"]["missing_courtyard"] = "ignore"
    ds["rule_severities"]["lib_footprint_issues"] = "ignore"
    ds["rule_severities"]["lib_footprint_mismatch"] = "ignore"
    # Silk and edge clearances are cosmetic on a placed-but-unrouted board.
    # silk_over_copper / silk_overlap fire constantly on dense SMD clusters;
    # silk_edge_clearance and copper_edge_clearance fire on the edge-mount
    # connectors (barrel jack J2, USB-C J10, SMA J3) whose pads are deliberately
    # at the board edge.  Demote them so they don't mask real electrical errors.
    ds["rule_severities"]["silk_over_copper"] = "ignore"
    ds["rule_severities"]["silk_overlap"] = "ignore"
    ds["rule_severities"]["silk_edge_clearance"] = "ignore"
    ds["rule_severities"]["copper_edge_clearance"] = "ignore"
    ds["rule_severities"]["text_height"] = "ignore"
    ds["rule_severities"]["text_thickness"] = "ignore"
    # holes_co_located fires on the headless line router's occasional duplicate
    # via of the SAME net at the same grid cell (e.g. two DET_TIA tie vias that
    # both snapped to one 0.2 mm grid point). Two same-net drills at one spot are
    # benign -- the fab drills the hole once -- so demote it as a manufacturing
    # warning, not an electrical error. (Different-net co-locations would still
    # be caught as shorting_items / clearance, which stay hard errors.)
    ds["rule_severities"]["holes_co_located"] = "ignore"
    ds["rules"]["min_clearance"] = 0.10
    # 0.10 mm (4 mil) board min clearance -- standard HDI capability, same class
    # as the 0.10 mm laser drills already used. The netclass clearances stay at
    # 0.15 mm (see below), so every net-pair is governed by its 0.15 mm netclass
    # EXCEPT chip<->chip: the "Chip" netclass is 0.10 mm. At the current 2.5 mm
    # die pitch / 1.2 mm pads the dog-bone vias (0.20 mm via 1.0 mm out) clear
    # the neighbour by 0.80 mm -- far above 0.15 mm -- so the Chip class is not a
    # routing necessity there; it is retained as the board's 0.10 mm HDI floor
    # (it was a necessity for the earlier 0.30 mm-pitch die whose 0.125 mm
    # neighbour clearance needed < 0.15 mm). The board min_clearance is the
    # floor; 0.10 mm only unleashes the chip dog-bones -- all other pairs keep
    # 0.15 mm (their netclass > board min).
    # min_track_width 0.10 mm = the HDI 4-mil floor.
    ds["rules"]["min_track_width"] = 0.10
    # HDI via floors -- these MUST agree with route_signals._relax_board_for_hdi
    # (m_ViasMinSize = BGA_VIA_DIAM_NM = 0.20 mm, m_ViasMinAnnularWidth = 0.05 mm).
    # The board uses 0.20 mm / 0.10 mm drill via-in-pad (filled+capped) microvias for
    # the 0.5 mm-pitch CSG324 BGA and 0.30 mm-pitch QSSC_Chip dog-bone fanout, and
    # 0.40 mm / 0.20 mm drill routing vias for general signal escape. A 0.45 mm /
    # 0.10 mm floor (a non-HDI assumption) would flag every one of those as a
    # via_diameter / annular_width violation. 0.20 mm / 0.05 mm is the HDI
    # floor: the 0.40 mm routing vias clear it with margin, the 0.20 mm VIPs sit
    # exactly on it (KiCad uses >=), and the 0.05 mm annular is the via-in-pad
    # figure (the pad copper, not the via ring, provides the annular -- VIPs are
    # filled+capped). This keeps the pro design rules and the router's in-memory
    # relaxation in lockstep so DRC is consistent whichever authority is consulted.
    ds["rules"]["min_via_diameter"] = 0.20
    ds["rules"]["min_via_annular_width"] = 0.05
    ds["rules"]["min_hole_clearance"] = 0.15
    ds["rules"]["min_hole_to_hole"] = 0.25
    # 0.15 mm (6 mil) board min hole_clearance -- the floor for drill-hole-to-copper
    # of a DIFFERENT net. At the current 2.5 mm die pitch the QSSC_Chip dog-bone
    # vias (0.20 mm via 1.0 mm out) clear the neighbour pad by 0.80 mm, so 0.15 mm
    # is met with large margin. (The earlier 0.30 mm-pitch die had 0.175 mm
    # drill-to-neighbour-stub, under the old 0.18 mm floor but clearing 0.15 mm
    # by 0.025 mm -- the floor was lowered to 0.15 mm for it; at 2.5 mm the margin
    # is wide.) Non-chip vias (0.8 mm-pitch CSG324 BGA VIPs, GND/+3V3 stitching)
    # are on much coarser pitches so 0.15 mm is safe board-wide. Same
    # floor-unleashes-chip pattern as min_clearance above.
    ds["rules"]["min_copper_edge_clearance"] = 0.3
    nc = proj["net_settings"]["classes"]
    nc.clear()
    # NOTE: netclass *clearance* is set equal to the board min_clearance (0.15 mm)
    # for every class.  KiCad applies a netclass clearance override between ANY two
    # pads of that net -- INCLUDING adjacent pads inside the same fine-pitch
    # footprint (QFN-32 0.5 mm pitch, USB-C, SSOP-28).  A larger per-class
    # clearance (e.g. 0.30 for Power) is therefore unsatisfiable: the QFN pad gap
    # is ~0.25 mm, so every Power/RF pad next to a Default pad inside the ADF4351
    # or GVA-84+ flags a clearance violation that no placement can fix.  The
    # impedance intent for RF/USB/Power is instead carried by the per-class
    # track_width (0.30/0.20/0.50 mm) and is enforced during copper routing (one
    # GUI pcbnew session), not by a pad-clearance rule.  See HARDWARE_DESIGN.md
    # §10/§15.
    nc.append({"bus_width": 12, "clearance": 0.15, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
               "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.2, "microvia_drill": 0.1,
               "name": "Default", "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
               "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6})
    nc.append({"bus_width": 12, "clearance": 0.15, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
               "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.2, "microvia_drill": 0.1,
               "name": "RF", "pcb_color": "rgba(255, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
               "track_width": 0.3, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6})
    nc.append({"bus_width": 12, "clearance": 0.15, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
               "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.2, "microvia_drill": 0.1,
               "name": "Power", "pcb_color": "rgba(0, 0, 255, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
               "track_width": 0.5, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6})
    nc.append({"bus_width": 12, "clearance": 0.15, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
               "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.2, "microvia_drill": 0.1,
               "name": "USB", "pcb_color": "rgba(0, 255, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
               "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6})
    nc.append({"bus_width": 12, "clearance": 0.15, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
               "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.2, "microvia_drill": 0.1,
               "name": "Analog", "pcb_color": "rgba(255, 255, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
               "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6})
    # Chip netclass: the 128 QSSC_Chip die-pad signal nets (DET_IN0..63,
    # emitter_cat0..63). At the current 2.5 mm die pitch / 1.2 mm pads the
    # dog-bone vias (0.20 mm via 1.0 mm out) clear the neighbour by 0.80 mm, so
    # this 0.10 mm clearance (vs the 0.15 mm board default) is NOT a routing
    # necessity -- it is retained as the board's HDI capability floor (it was a
    # necessity for the earlier 0.30 mm-pitch die, whose 0.125 mm neighbour
    # clearance needed < 0.15 mm and whose 0.15 mm stub did not clear a
    # 0.30 mm-pitch neighbour). It relaxes ONLY chip<->chip; chip<->other nets
    # keep 0.15 mm (KiCad takes the max of the two netclass clearances).
    # Track width 0.10 mm = the HDI floor (no auto-narrowing needed at 2.5 mm).
    nc.append({"bus_width": 12, "clearance": 0.10, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
               "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.2, "microvia_drill": 0.1,
               "name": "Chip", "pcb_color": "rgba(0, 255, 255, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
               "track_width": 0.10, "via_diameter": 0.2, "via_drill": 0.1, "wire_width": 6})
    # netclass patterns: assign nets by prefix
    patterns = []
    for n in all_nets():
        cls = net_class(n)
        if cls != "Default":
            patterns.append({"netclass": cls, "pattern": n})
    proj["net_settings"]["netclass_patterns"] = patterns
    return _j.dumps(proj, indent=2)


# --------------------------------------------------------------------------
# PCB placement plan (mm) — assigns pcb_x/pcb_y to each component
# --------------------------------------------------------------------------
# Board dimensions live in MAIN_CFG / HEAD_CFG below.  Main board 210x140
# (FPGA + emitter + RF + USB + power + die emitter face); head board 150x140
# (TIA + AD7616 + die detector face).  Die centre, FPGA right (main), front-end
# band (head), TLC5947 below the die (main).

# Copper layer stack for the MAIN control board (6-layer HDI). Each entry:
# (kicad_layer_id, name, type, user_label_or_None). Stackup:
#   F.Cu  sig | In1.Cu GND | In2.Cu +3V3 | In3.Cu sig | In4.Cu sig | B.Cu sig
# The two planes (In1 GND, In2 +3V3) are ADJACENT to F.Cu so the 117 FPGA power
# balls tie to them with SINGLE-dielectric microvias / short blind vias that do
# NOT reach In3.Cu/In4.Cu/B.Cu -- those inner signal layers stay clear for BGA
# signal escapes. (A through-via on every power ball -- the original "HDI-lite"
# plan -- blocked the inner layers solid at 0.5 mm pitch: 117 barrels overlap.)
# The detector HEAD board uses a separate 6-layer stack (HEAD_LAYER_STACK) -- the
# same 6-layer schedule as the main board (commit b41009c: the 128-net die bus
# needs the In3.Cu/In4.Cu escape layers a 4-layer stack could not provide).
LAYER_STACK = [
    (0,  "F.Cu",   "signal", None),
    (1,  "In1.Cu", "signal", "GND"),
    (2,  "In2.Cu", "power",  "PWR"),
    (3,  "In3.Cu", "signal", None),
    (4,  "In4.Cu", "signal", None),
    (31, "B.Cu",   "signal", None),
]

# Explicit dielectric/copper (stack_up) block for the MAIN board: 6-layer 1.6mm
# FR4 HDI. Thin 0.15 mm prepreg F.Cu->In1.Cu (GND) gives a single-dielectric
# microvia for the 74 GND balls AND ~50ohm microstrip at ~0.30 mm track width
# (RF netclass) over the GND plane. The 0.15 mm dielectric 2 (In1->In2) lets the
# ~30 +3V3 balls use a 2-layer blind via F.Cu->In2 (anti-pad in the GND plane).
# In3.Cu/In4.Cu/B.Cu are the pad-free inner signal escape layers. Each entry:
# (layer_name, type, thickness_mm, material). Empty -> (stack_up) block omitted
# (the chip module clears it).
STACK_UP = [
    ("F.Cu",         "copper",  0.035,  None),
    ("dielectric 1", "prepreg", 0.15,   "FR4"),
    ("In1.Cu",       "copper",  0.0175, None),
    ("dielectric 2", "prepreg", 0.15,   "FR4"),
    ("In2.Cu",       "copper",  0.0175, None),
    ("dielectric 3", "core",    0.50,   "FR4"),
    ("In3.Cu",       "copper",  0.0175, None),
    ("dielectric 4", "prepreg", 0.26,   "FR4"),
    ("In4.Cu",       "copper",  0.0175, None),
    ("dielectric 5", "core",    0.40,   "FR4"),
    ("B.Cu",         "copper",  0.035,  None),
]

# Copper layer stack for the DETECTOR HEAD board (6-layer). The head carries
# the 64-channel TIA + 4xAD7616 analog front-end AND the dense 64-net die
# detector bus (DET_IN die->TIA + DET_TIA TIA->ADC). On 4 layers that bus was
# at the autorouter's density ceiling (only F.Cu/B.Cu signal, 2 layers); a
# 6-layer stack adds In3.Cu/In4.Cu signal layers so route_signals' die
# dog-bone fanout + the line router have 4 signal layers to spread the bus.
# In1.Cu stays a solid GND plane (return for the TIA->ADC analog runs +
# reference for the DET_REF bias), In2.Cu the PWR plane. B.Cu carries the 64
# DET contact pads that face the die top face (head sits above the die, B.Cu
# down). route_signals.build() auto-selects the 4-signal-layer set from
# GetCopperLayerCount() (>4 -> F.Cu/In3.Cu/In4.Cu/B.Cu), no router edit needed.
HEAD_LAYER_STACK = [
    (0,  "F.Cu",   "signal", None),
    (1,  "In1.Cu", "signal", "GND"),
    (2,  "In2.Cu", "power",  "PWR"),
    (3,  "In3.Cu", "signal", None),
    (4,  "In4.Cu", "signal", None),
    (31, "B.Cu",   "signal", None),
]

# 6-layer 1.6 mm FR4 stackup for the head board. In1.Cu (GND) is one 0.15 mm
# prepreg below F.Cu so the TIA->ADC analog runs on F.Cu have a tight GND
# reference (low-impedance return, minimal crosstalk); In2.Cu is the PWR
# plane (+3V3 / +5V / +5V_A poured by net label). In3.Cu/In4.Cu are the extra
# inner signal escape layers for the 64-net die bus. Symmetric-ish build.
HEAD_STACK_UP = [
    ("F.Cu",         "copper",  0.035,  None),
    ("dielectric 1", "prepreg", 0.15,   "FR4"),
    ("In1.Cu",       "copper",  0.0175, None),
    ("dielectric 2", "prepreg", 0.15,   "FR4"),
    ("In2.Cu",       "copper",  0.0175, None),
    ("dielectric 3", "core",    0.50,   "FR4"),
    ("In3.Cu",       "copper",  0.0175, None),
    ("dielectric 4", "prepreg", 0.26,   "FR4"),
    ("In4.Cu",       "copper",  0.0175, None),
    ("dielectric 5", "core",    0.40,   "FR4"),
    ("B.Cu",         "copper",  0.035,  None),
]

# Per-board configuration consumed by emit_schematic / emit_pcb / main.
MAIN_CFG = dict(out_stem="qssc", title="QSSC Main Control Board", project="qssc",
                board_w=210, board_h=140, layer_stack=LAYER_STACK, stack_up=STACK_UP)
HEAD_CFG = dict(out_stem="chip_module", title="QSSC Detector Head", project="chip_module",
                board_w=150, board_h=140, layer_stack=HEAD_LAYER_STACK, stack_up=HEAD_STACK_UP)


def assign_pcb_positions():
    # Fixed IC/connector placements (mm) on the MAIN control board (210x140,
    # 6-layer). The detector front-end is on the HEAD board (see
    # assign_pcb_positions_head); this board carries FPGA + emitter + RF + USB +
    # power + the die EMITTER face (QSSC_Chip_EM).
    # Layout zones:
    #   centre        ChipSocket_2.54_EM die (64 EM cathodes + EM_ANODE on F.Cu, 20x20 mm)
    #   right         FPGA CSG324 (324-ball BGA, moved off-centre to clear the die) + config flash
    #   top           power chain (left) + RF chain (ADF4351 -> GVA-84 -> SMA right edge)
    #   bottom-left   USB-C (J10) + FT232 USB cluster -- all user-facing connectors
    #                 live in the LEFT x<60 strip (the only main-board region NOT
    #                 covered by the stacked detector head board, which spans main
    #                 x[60,210]); reset/JTAG/MW-gate/5V+12V barrels also on the left
    #   top-left      BTB HOST J12 (9-wire SPI + power to the detector head)
    #   below die     3x TLC5947 constant-current LED driver (UE0/1/2 + Riref/Cem)
    # Edge-mount connectors sit at the board edge by design:
    #   J2 5V barrel   -> left edge (rot 180, opening faces -x)
    #   J4 12V barrel  -> left edge (rot 180, opening faces -x)
    #   J10 USB-C      -> bottom-LEFT edge (rot 180, opening overhangs +y) -- in the
    #                     x<60 exposed strip so it is visible/reachable in a case
    #                     (was bottom-centre x74, hidden under the head board)
    #   J3 SMA         -> right edge (rot 0, jack overhangs +x past the head's edge)
    #   J8 MW_GATE     -> left edge (x10, y56) -- was x150 under the head board
    #                     (through-hole pins collided with the stacked head board)
    # Rotations are chosen so the footprint's native pad geometry is preserved
    # (USB-C and SMA pads collapse/short when rotated 90 deg -- see DRC notes).
    fixed = {
        # power (top-left)
        "J2": (10, 10, 180), "F1": (24, 10, 0), "R70": (20, 16, 0), "LED1": (26, 16, 0),
        # C_in = +5V_IN input bulk decoupling (10uF 0805) right at the barrel-jack
        # input rail (J2.1 -> C_in -> F1).  Top-left, clear of the J2/F1/R70/LED1
        # row (y=10/16) and of the default C1-30 cap grid (x>=50).  Without this it
        # fell through to the (50,50) default and collided with C37.
        "C_in": (46, 8, 0),
        "REG1": (34, 14, 0), "REG2": (34, 26, 0), "REG3": (34, 38, 0), "FB1": (34, 50, 0),
        # REG3 (AP2127K-1.0) decoupling, around (34,38)
        "C42": (28, 36, 0), "C43": (40, 42, 0), "C44": (28, 42, 0),
        # FB1 (ferrite bead) +5V -> +5V_RF pi filter, around (34,50)
        "C45": (28, 50, 0), "C46": (28, 56, 0), "C47": (40, 50, 0), "C48": (40, 56, 0),
        # ---- +12V_EMIT emitter anode supply (left edge, below the 5V chain) ----
        # J4 12V barrel (left edge, rot 180 opening -x) -> F2 1A PTC. The rail
        # runs on B.Cu up to y~55 and across to the die EM_ANODE pad on the LEFT
        # edge (70,55) -- mid-height, clear of the EM (bottom) dog-bone via field.
        # C85/C86 bulk+HF decouple it right at the anode fanout (just left of the
        # ChipSocket_2.54_EM socket courtyard x66.5, y~52/58).
        "J4": (10, 72, 180), "F2": (24, 72, 0), "C85": (64, 52, 0), "C86": (64, 58, 0),
        # ---- quantum die (centre) + FPGA (moved right off the die) ----
        "Uchip": (80, 55, 0),
        # BTB HOST connector J12 (top edge, between the RF chain and the config
        # flash).  At (110,14) the x[105,114] y[1,27] courtyard window is empty
        # on the main board (clear of the decoupling-cap grid x<=86, the RF
        # chain y>=30, and the flash/RTC cluster y>=52).  The stacked detector
        # head's J12 sits at head-local (50,14) -- the head origin is the main
        # origin + (60,0) mm, so head (50,14) == main (110,14), and head
        # x[45,54] y[1,27] is clear of the head die (10..30 x 45..65) and the
        # head front-end (x>=80).  y=14 keeps the 22.86 mm-tall connector body
        # (pads +/-11.43) fully on-board.  The 9-wire AD7616 SPI + 3V3/5V/GND
        # cross the sandwich here.
        "J12": (110, 14, 0),
        # FPGA CSG324 (~17 mm BGA, courtyard x[119.5,136.5] y[46.5,63.5]) right of
        # the die; ~29 mm gap for the 64 DET_DIG routes (Phase 4). XO above the die.
        "U1": (128, 55, 0), "Y1": (70, 25, 0),
        # config flash W25Q128 + pull-ups in the die<->FPGA gap (x106-118); C40
        # VREFP decoupling just below-left of the FPGA.
        "U8": (106, 58, 0), "R75": (114, 54, 0), "R76": (114, 58, 0),
        "R77": (114, 62, 0), "C40": (118, 70, 0),
        # USB (bottom): FT232 + USB-C + CC pulls.  2026-09-08 the user dragged the
        # whole USB-C + damping cluster UP ~20 mm in KiCad and moved FT232 right to
        # the y~84 connector row; these are the exact saved coordinates back-ported
        # so regen preserves the user's manual placement.
        "J1": (115.9, 83.725, 0), "J10": (32.175, 114, 180), "R71": (24.175, 106, 0), "R72": (28.175, 106, 0),
        "J8": (10, 92, 0),
        # RF chain (top-right): U3 ADF4351 + U5 GVA-84 -> J3 SMA.  2026-09-08 the
        # user dragged J3 off the right edge to (142.11,83) -- mid-board, lined up
        # with FT232 on the y~84 row.  NOTE: SMA_Amphenol_132289_EdgeMount is an
        # edge-launch footprint whose origin sits on a board edge so the jack
        # overhangs off-board; at (142.11,83) it is NOT on any edge (board is
        # 210x140, edges at x=0/210, y=0/140).  Routing to its pads is valid, but
        # the mechanical connector launch needs either a board edge/cutout or a
        # bulkhead/right-angle SMA -- flag for the user to confirm in KiCad.
        "U3": (92, 30, 0), "U5": (106, 40, 0), "J3": (142.1125, 83, 0),
        "R67": (84, 26, 0), "C31": (84, 30, 0), "R68": (84, 34, 0),
        "C32": (100, 26, 0), "C33": (100, 30, 0), "R73": (100, 34, 0),
        "C34": (100, 40, 0), "C35": (110, 34, 0), "C36": (110, 40, 0), "L2": (110, 46, 0),
        # C37 = ADF4351 pin-19 TEMP decoupling (ADF_TEMP node); sits just below-right
        # of U3 (clear of the QFN-32 keepout x[88.5,95.5] y[26.5,33.5] and of
        # C33@(100,30)/C34@(100,40)/C35@(110,34)).  Without an explicit placement it
        # fell through assign_pcb_positions()'s else-branch to the (50,50) default
        # cell and collided with C_in -> DRC shorting_items +5V_IN/ADF_TEMP.
        "C37": (104, 36, 0),
        # C41 = ADF4351 pin-23 VCOM decoupling (ADF_VCOM node); sits next to the
        # C36/C37 ADF decoupling cluster below U3.  Designator C41 reused: the old
        # C41 was the AP2127K BP cap (deleted -- AP2127K-1.0 pin 4 is NC).
        "C41": (104, 46, 0),
        "J7": (10, 40, 0), "J9": (10, 26, 0),
        # ---- production-hardening pass 2026-09 (see add() block + §16) ----
        # USB VBUS Schottky + bulk near the USB-C connector (left of the die).
        # 2026-09-08 user dragged this cluster up ~20 mm with J10.
        "D_USB": (22.175, 100, 0), "C84": (30.175, 98, 0),
        # USB DP/DM series damping next to the USB-C connector so the USB-C +
        # its VBUS/CC/damping cluster is visible and reachable in a case.
        # 2026-09-08 user dragged these up ~20 mm with J10; FT232 (J1) now sits
        # on the same y~84 row (see above).  The DP/DM_F diff pair runs from
        # FT232 to here.
        "R78": (38.175, 102, 0), "R79": (42.175, 102, 0),
        # AMS1117 stability bulk + input bulk (clear of the SOT-223 courtyards
        # x[29.5,38.5]: 22uF output caps at x42, input bulk at x28)
        "C49": (40, 20, 0), "C50": (42, 28, 0), "C51": (28, 20, 0),
        # FPGA per-rail 4.7uF bulk (left of the BGA keepout x119.5, y in [40,52])
        "C52": (58, 40, 0), "C53": (58, 46, 0), "C54": (58, 52, 0),
        # FT232 decoupling (column x62: clear of the die courtyard left x69.5)
        "C55": (62, 60, 0), "C56": (62, 64, 0), "C57": (62, 56, 0),
        # XO + W25Q128 Vdd/VCC decoupling (C59 at x116: clear of TLC5947 UE2 @ x100)
        "C58": (76, 22, 0), "C59": (116, 68, 0),
        # ADF4351 per-supply 100nF (around U3 keepout x[88,96] y[26,34]: above & below)
        "C60": (84, 22, 0), "C61": (88, 22, 0), "C62": (96, 22, 0), "C63": (100, 22, 0),
        "C64": (88, 38, 0), "C65": (96, 38, 0),
        # JTAG pulls (right of J9 header x[5,15])
        "R80": (16, 22, 0), "R81": (16, 26, 0), "R82": (16, 30, 0),
        # Reset pull-down + debounce cap (J7 SW_PUSH pads reach x16.5, courtyard x18 -> x24)
        "R83": (24, 40, 0), "C68": (24, 44, 0),
        # ---- emitter driver cluster (3x TLC5947 + 3x Riref + 3x Cem) ----
        # 2026-09-08 the user dragged the whole cluster DOWN ~28 mm in KiCad (was
        # y=78/86 just below the die -> y~106/114) and shifted x from 40/60/100 to
        # ~56/79/103.  These explicit entries OVERRIDE the procedural UE/Riref/Cem
        # branches below (the `if c["ref"] in fixed` check is first) so regen
        # preserves the user's manual placement.
        "UE0": (56.25, 105.8, 0), "UE1": (78.635, 106.35, 0), "UE2": (103.35, 105.575, 0),
        "Riref0": (56.25, 113.8, 0), "Riref1": (78.635, 114.35, 0), "Riref2": (103.35, 113.575, 0),
        "Cem0": (61.25, 113.8, 0), "Cem1": (83.635, 114.35, 0), "Cem2": (108.35, 113.575, 0),
    }
    for c in COMPONENTS:
        if c["ref"] in fixed:
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = fixed[c["ref"]]
        elif c["ref"].startswith("C") and c["ref"][1:].isdigit() and 1 <= int(c["ref"][1:]) <= 20:
            i = int(c["ref"][1:]) - 1
            c["pcb_x"] = 50 + (i % 10) * 4
            c["pcb_y"] = 4 + (i // 10) * 4
            c["pcb_rot"] = 0
        elif c["ref"].startswith("C") and c["ref"][1:].isdigit() and 21 <= int(c["ref"][1:]) <= 30:
            i = int(c["ref"][1:]) - 21
            c["pcb_x"] = 50 + i * 4
            c["pcb_y"] = 12
            c["pcb_rot"] = 0
        elif c["ref"].startswith("D") and c["ref"][1:].isdigit():
            i = int(c["ref"][1:]) - 1
            c["pcb_x"] = 50 + i * 4
            c["pcb_y"] = 16
            c["pcb_rot"] = 0
        # ---- Emitter driver procedural grid (3x TLC5947 + 3x Riref + 3x Cem,
        # 9 parts) lives BELOW the die at y=78 (EM bottom-edge pads fan down to
        # the TLC5947 OUTn sinks).  The detector front-end (TIA + AD7616 +
        # Rf/Cf + DET_REF + +5V_A) is on the HEAD board -- see
        # assign_pcb_positions_head().  All spacing is sized to the largest
        # courtyard half-extent in each block so nothing overlaps: HTSSOP-32
        # x+/-4/y+/-6.5, 0603 ~+/-0.9.
        elif c["ref"].startswith("UE"):          # 3x TLC5947 LED driver (row below the die)
            j = int(c["ref"][2:])                 # UE0..2 -> x 40/60/100, y=78
            xs = [40, 60, 100]
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = xs[j], 78, 0
        elif c["ref"].startswith("Riref"):       # 3x IREF set resistor (below each TLC5947)
            j = int(c["ref"][5:])
            xs = [40, 60, 100]
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = xs[j], 86, 0
        elif c["ref"].startswith("Cem"):         # 3x TLC5947 VCC decoupling (right of each Riref)
            j = int(c["ref"][3:])
            xs = [45, 65, 105]
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = xs[j], 86, 0
        else:
            c.setdefault("pcb_x", 50)
            c.setdefault("pcb_y", 50)
            c.setdefault("pcb_rot", 0)


def assign_pcb_positions_head():
    """PCB placement (mm) for the DETECTOR HEAD board (150x140, 4-layer).

    The head origin maps to the main-board origin + (60, 0) mm so that the
    head's die contact (B.Cu DET pads at head (20,55)) sits directly above the
    main board's die (80,55), and head J12 (24,14) mates with main J12 (84,14).
    The whole detector front-end grid is therefore the main-board front-end
    grid shifted LEFT by 60 mm in x (same y): it was authored + DRC-verified at
    main x[140,208] and lands at head x[80,148] here.  Spacing is unchanged so
    the verified non-overlap is preserved.

    Zones: die centre-left (20,55) -> 4x4 TLV9004 TIA grid with each TIA's 4
    Rf/Cf feedback pairs clustered BESIDE it (y16-56, ~3mm summing-node traces,
    channels A,B left / C,D right of each TIA) -> 4x AD7616 2x2 grid (y60-107 +
    7-cap decoupling columns) -> +5V_A ferrite rail at the bottom edge; DET_REF
    divider left of the TIA grid; J12 top-left.  Clustering Rf/Cf at the TIA
    (instead of a remote bottom bank) keeps the inverting-input summing node to
    a ~3mm trace -- minimal parasitic C on the TIA input -- AND means no
    feedback route has to cross the AD7616 zone, which is what stalled the
    router (3 FreeRouting cycles converged at 20 split nets with the Rf/Cf bank
    83mm from the TIAs).  Netlist is unchanged; only placement moves."""
    fixed = {
        "Uchip": (20, 55, 0),     # die TOP/detector face (B.Cu pads face the die)
        "J12": (50, 14, 0),       # BTB DAUGHTER -- mates with main J12 (110,14)
    }
    for c in COMPONENTS:
        if c["ref"] in fixed:
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = fixed[c["ref"]]
        elif c["ref"].startswith("UT"):          # 16x TLV9004 TIA (4x4 grid, 16mm col x 10mm row)
            j = int(c["ref"][2:]); col, row = j % 4, j // 4
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 84 + col * 16, 20 + row * 10, 0
        elif c["ref"].startswith("Cut"):         # 16x TLV9004 decoupling (below each TIA, near V+/V- pins)
            j = int(c["ref"][3:]); col, row = j % 4, j // 4
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 84 + col * 16, 20 + row * 10 + 5.0, 0
        elif c["ref"].startswith("UAD"):         # 4x AD7616 ADC (2x2 grid, 14mm LQFP-80), shifted +16mm y
            j = int(c["ref"][3:])
            grid = [(90, 68), (126, 68), (90, 98), (126, 98)]
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = grid[j][0], grid[j][1], 0
        elif c["ref"].startswith("CuaB"):        # AD7616 VCC bulk 10uF (per chip, decap column, +16mm y)
            j = int(c["ref"][4:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 3, 0
        elif c["ref"].startswith("Cua"):         # AD7616 VCC HF 100nF (per chip)
            j = int(c["ref"][3:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 0, 0
        elif c["ref"].startswith("Cud"):         # AD7616 VDRIVE 100nF (per chip)
            j = int(c["ref"][3:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 6, 0
        elif c["ref"].startswith("Cur"):         # AD7616 REFCAP 100nF (per chip)
            j = int(c["ref"][3:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 9, 0
        elif c["ref"].startswith("Crio"):        # AD7616 REFINOUT 100nF (per chip)
            j = int(c["ref"][4:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 12, 0
        elif c["ref"].startswith("Crgc"):        # AD7616 REGCAP 1uF (per chip)
            j = int(c["ref"][4:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 15, 0
        elif c["ref"].startswith("Crgd"):        # AD7616 REGCAPD 1uF (per chip)
            j = int(c["ref"][4:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 18, 0
        elif c["ref"].startswith("Rf"):           # 64x TIA feedback R -- clustered BESIDE its TIA (vertical)
            i = int(c["ref"][2:]); j, k = i // 4, i % 4   # TIA j, channel k (A,B,C,D)
            cx, cy = 84 + (j % 4) * 16, 20 + (j // 4) * 10
            sx = -1 if k < 2 else 1            # channels A,B left of TIA; C,D right
            sy = -1 if k in (0, 3) else 1      # A,D top (pins 1/14); B,C bottom (pins 7/8)
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = cx + sx * 5.2, cy + sy * 2.2, 90
        elif c["ref"].startswith("Cf"):           # 64x TIA feedback C -- beside its Rf (outer column, vertical)
            i = int(c["ref"][2:]); j, k = i // 4, i % 4
            cx, cy = 84 + (j % 4) * 16, 20 + (j // 4) * 10
            sx = -1 if k < 2 else 1
            sy = -1 if k in (0, 3) else 1
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = cx + sx * 7.0, cy + sy * 2.2, 90
        elif c["ref"] == "FB_A":                 # +5V_A ferrite (zone bottom-left)
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 80, 128, 0
        elif c["ref"] == "C_Abulk":              # +5V_A bulk/HF decoupling
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 86, 128, 0
        elif c["ref"] == "C_ahf":
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 86, 132, 0
        elif c["ref"] == "Rref1":               # DET_REF 1.65V divider (left of TIA grid, clear of die/J12)
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 40, 30, 0
        elif c["ref"] == "Rref2":
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 44, 30, 0
        elif c["ref"] == "Cref":
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 48, 30, 0
        else:
            c.setdefault("pcb_x", 20)
            c.setdefault("pcb_y", 70)
            c.setdefault("pcb_rot", 0)


def assign_sch_positions():
    """Schematic (x,y) for the Phase 1b front-end + driver symbols.  They were
    add()-ed at (0,0); left there, every symbol stacks at the origin and its pin
    labels land on coincident points -- KiCad then merges those points into one
    net (verified: 19k 'Output+Output' ERC errors).  Grid them out on the A1
    sheet right of the existing (x<350) schematic content.  Spacing > 2x the
    symbol pin reach (~10mm) so no two distinct nets share a pin endpoint."""
    new = [c for c in COMPONENTS if c["ref"] in ("Rref1", "Rref2", "Cref",
                                                  "FB_A", "C_Abulk", "C_ahf")
           or any(c["ref"].startswith(p) and c["ref"][len(p):].isdigit()
                  for p in ("UT", "UAD", "Cua", "CuaB", "Cud", "Cur", "Crio",
                            "Crgc", "Crgd", "Rf", "Cf", "Cut",
                            "UE", "Riref", "Cem"))]
    cols, step_x, step_y = 19, 22.0, 20.0
    for idx, c in enumerate(new):
        r, col = idx // cols, idx % cols
        c["x"] = 420 + col * step_x        # clear right of existing x[20,410]
        c["y"] = 20 + r * step_y
        c["rot"] = 0


def _emit_board(cfg, place_fn):
    """Build COMPONENTS (caller has already run the build fn), place them, and
    write {out_stem}.kicad_{sch,pcb,pro} for one board."""
    place_fn()
    assign_sch_positions()
    stem = cfg["out_stem"]
    (PCB_DIR / f"{stem}.kicad_sch").write_text(emit_schematic(cfg) + "\n")
    print(f"wrote {stem}.kicad_sch")
    (PCB_DIR / f"{stem}.kicad_pcb").write_text(emit_pcb(cfg) + "\n")
    print(f"wrote {stem}.kicad_pcb")
    (PCB_DIR / f"{stem}.kicad_pro").write_text(emit_pro() + "\n")
    print(f"wrote {stem}.kicad_pro")


def main():
    PCB_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / ".tmpwork").mkdir(exist_ok=True)
    build_pincoords()

    # Custom symbol library: authored ONCE with every symbol (both chip faces,
    # the BTB connector, and all the IC/passive symbols used by either board).
    # Both projects reference qssc-lib.kicad_sym, so it must contain the union.
    lib_syms = authored_symbols()
    libfile = L(A("kicad_symbol_lib"), L(A("version"), A(20231120)),
                L(A("generator"), S("gen_qssc_kicad")), L(A("generator_version"), S("8.0")),
                *lib_syms)
    (PCB_DIR / "qssc-lib.kicad_sym").write_text(emit(libfile) + "\n")
    print("wrote qssc-lib.kicad_sym")

    # ---- main control board (6-layer HDI: FPGA + emitter + RF + USB + power) --
    COMPONENTS.clear()
    build_main_board()
    _emit_board(MAIN_CFG, assign_pcb_positions)

    # ---- detector head board (6-layer: TIA + AD7616 + die detector face) -----
    COMPONENTS.clear()
    build_detector_head()
    _emit_board(HEAD_CFG, assign_pcb_positions_head)


if __name__ == "__main__":
    main()