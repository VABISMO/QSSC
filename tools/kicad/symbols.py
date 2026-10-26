"""Symbol primitives + hand-authored IC / BTB pin maps for the QSSC KiCad
symbol library.

Pure data + sexp builders (no filesystem, no footprint loading). The
authored symbols are emitted once into ``qssc-lib.kicad_sym`` and their pin
coordinates are harvested by ``build_pincoords`` for schematic net wiring.
"""
from __future__ import annotations

import json

from tools.sexp import A, L, S

from ._paths import ROOT

__all__ = ['AD7616_FP', 'BTB_PIN_NETS', 'GRID_HALF', 'GRID_N', 'GRID_PITCH', 'GVA84_NETS', 'GVA84_PINS', 'POWER_PINS', 'PWR_FLAG_def', 'SYM_NAME', 'ad7616_pin_list', 'ams1117_pins', 'ap2127k_pins', 'authored_symbols', 'chip_net_map', 'chip_net_map_det', 'chip_net_map_em', 'chip_pin_list', 'chip_pin_list_det', 'chip_pin_list_em', 'conn_02x10_pin_list', 'custom_symbol_def', 'fpga_net_map', 'fpga_pin_list', 'irlml2502_pin_list', 'lm358_pin_list', 'lmv339_pin_list', 'make_pin', 'mcp4725_pin_list', 'mcp4921_pin_list', 'power_symbol_def', 'replicated_symbol', 'simple_two_pin_vertical', 'std_pins', 'tlc5947_pin_list', 'tlv9004_pin_list']


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


def fpga_net_map(grad=False):
    """{ball_name: net} for every FPGA pad that carries a net.

    All power/ground/config/JTAG pads plus the used user-IO signals (9-pin ADC
    SPI bus + 4 em_SPI + 13 control) are netted.  Spare general-purpose IO
    balls are intentionally LEFT OUT (no net = NC), which is correct practice
    for unused BGA IO (the pad is still soldered for mechanical integrity, just
    carries no signal).  XADC VP/VN stay NC -- the detector readout is now 4x
    AD7616 parallel ADCs over SPI (true analog intensity), not the XADC.  This
    nets ~204 of the 324 CSG324 pads -- all the ones that matter for power
    integrity, config boot, and the board's I/O.

    grad=True (extended qssc-grad board only) also nets the 4 gradient-coil DAC pins (grad_cs/sclk/mosi/en) so they connect to Ugrad.
    On the base board (grad=False) these stay spare/NC -> the base qssc.*
    files are byte-identical whether or not the gradient variant is built."""
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
    _GRAD_SIGS = {"grad_cs", "grad_sclk", "grad_mosi", "grad_en"}
    for net, ball in s["signals"].items():
        if net in _GRAD_SIGS and not grad:
            continue                   # base board: grad pins stay spare/NC
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


def fpga_pin_list(grad=False):
    """Schematic pins for every netted FPGA ball, laid out on 4 sides at
    1.27 mm pitch.  Matches fpga_net_map() 1:1 so every PCB pad that is netted
    also appears on the schematic (datasheet-complete symbol).

    grad=False builds the base symbol (qssc:XC7A35T_CSG324) used by the base
    board -- no gradient pins, byte-identical to the pre-variant generator
    output.  grad=True builds the extended symbol (qssc:XC7A35T_CSG324_GRAD)
    used only by the qssc-grad board, adding the 4 gradient-coil
    DAC pins (grad_cs/sclk/mosi/en) so the FPGA ball labels connect to Ugrad.
    Both symbols share the same Xilinx_CSG324 footprint."""
    nm = fpga_net_map(grad=grad)
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
# The QSSC die is a TRUE two-faced die, NO TSVs: 64
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
    is the shared detector common masa, edge-contacted.  The die carries 130
    contacts: 64 DET + 64 EM-cathode + 2 ITO commons.  This face contacts the DETECTOR HEAD's B.Cu.  1.0 mm SYMBOL pitch
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


def mcp4921_pin_list():
    """MCP4921 12-bit SPI DAC, 8-pin SOIC/PDIP/MSOP (Microchip DS21897B /
    DS22248A -- pinout verified against the downloaded datasheet PDF).
    1=VDD 2=!CS 3=SCK 4=SDI 5=!LDAC 6=VREF 7=AVSS 8=VOUTA.
    NOTE: the 8-pin single MCP4921 has NO !SHDN pin -- shutdown is SPI write
    word bit 12 (the dac_ctrl framing {4'b0011, code} keeps bit 12=1 = active;
    the coil is cut by writing code 0 -> VOUTA=0 -> op-amp -> MOSFET off).
    Drives the field-gradient coil current driver: VOUTA -> LM358
    -> IRLML2502 MOSFET, I_coil = Vdac/Rsense. !LDAC tied to GND (transparent:
    output updates on CS rising edge); VREF tied to +5V (VDD) for rail-to-rail
    Vout = 5V * code/4096 (1x gain)."""
    return [dict(number="1", name="VDD", etype="power_in", x=-7.62, y=5.08, rot=0, length=2.54),
            dict(number="2", name="!CS", etype="input", x=-7.62, y=2.54, rot=0, length=2.54),
            dict(number="3", name="SCK", etype="input", x=-7.62, y=0, rot=0, length=2.54),
            dict(number="4", name="SDI", etype="input", x=-7.62, y=-2.54, rot=0, length=2.54),
            dict(number="5", name="!LDAC", etype="input", x=-7.62, y=-5.08, rot=0, length=2.54),
            dict(number="6", name="VREF", etype="input", x=7.62, y=-2.54, rot=180, length=2.54),
            dict(number="7", name="AVSS", etype="power_in", x=7.62, y=-5.08, rot=180, length=2.54),
            dict(number="8", name="VOUTA", etype="output", x=7.62, y=5.08, rot=180, length=2.54)]


def lm358_pin_list():
    """LM358 dual op-amp, SOIC-8 (TI). 1=OUTA 2=INA- 3=INA+ 4=VCC 5=INB+
    6=INB- 7=OUTB 8=GND(V-). Used as the gradient-coil current servo: INA+
    = DAC VOUT (GRAD_DAC_OUT), INA- = MOSFET source sense (GRAD_SENSE), OUTA
    = MOSFET gate (GRAD_GATE); the loop holds V_sense = V_dac -> I_coil =
    Vdac/Rsense. Amp B is parked as a unity follower (INB+ = GND, INB- = OUTB)
    so the unused half does not oscillate."""
    return [dict(number="1", name="OUTA", etype="output", x=-7.62, y=5.08, rot=0, length=2.54),
            dict(number="2", name="INA-", etype="input", x=-7.62, y=2.54, rot=0, length=2.54),
            dict(number="3", name="INA+", etype="input", x=-7.62, y=0, rot=0, length=2.54),
            dict(number="4", name="VCC", etype="power_in", x=0, y=-7.62, rot=90, length=2.54),
            dict(number="5", name="INB+", etype="input", x=7.62, y=0, rot=180, length=2.54),
            dict(number="6", name="INB-", etype="input", x=7.62, y=2.54, rot=180, length=2.54),
            dict(number="7", name="OUTB", etype="output", x=7.62, y=5.08, rot=180, length=2.54),
            dict(number="8", name="GND", etype="power_in", x=0, y=7.62, rot=270, length=2.54)]


def irlml2502_pin_list():
    """IRLML2502 N-channel logic-level MOSFET, SOT-23-3 (IR). 1=G 2=S 3=D.
    Low-side switch for the gradient coil: D = coil return (GRAD_COIL_N),
    S = sense node (GRAD_SENSE -> Rsense -> GND), G = op-amp drive (GRAD_GATE).
    Vgs(th) ~1V, logic-level -- the LM358 on +5V drives it to ~2.2A (the clean
    per-NV addressing current) within rails."""
    return [dict(number="1", name="G", etype="input", x=-5.08, y=2.54, rot=0, length=2.54),
            dict(number="2", name="S", etype="passive", x=-5.08, y=-2.54, rot=0, length=2.54),
            dict(number="3", name="D", etype="passive", x=5.08, y=0, rot=180, length=2.54)]


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
    # Extended qssc-grad variant: same FPGA, same footprint, PLUS the 4 gradient-coil DAC pins (grad_cs/sclk/mosi/en) drawn on the right column so
    # the grad board's FPGA ball labels connect to Ugrad.  The base symbol above
    # is unchanged (no grad pins) -> base qssc.* stays byte-identical.
    syms.append(custom_symbol_def("qssc", "XC7A35T_CSG324_GRAD", "U",
                                  fpga_pin_list(grad=True),
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
    # ---- Field-gradient coil current driver (extended board) ----
    # MCP4921 12-bit SPI DAC -> LM358 op-amp servo -> IRLML2502 N-MOSFET -> coil.
    # The coil itself is a generic qssc:L inductor with the custom qssc:GradCoil
    # footprint (a 1.5 mm F.Cu trace); no separate coil symbol is needed.
    syms.append(custom_symbol_def("qssc", "MCP4921", "U", mcp4921_pin_list(),
                                  (-7.62, -7.62, 7.62, 7.62), value="MCP4921"))
    syms.append(custom_symbol_def("qssc", "LM358", "U", lm358_pin_list(),
                                  (-7.62, -7.62, 7.62, 7.62), value="LM358"))
    syms.append(custom_symbol_def("qssc", "IRLML2502", "Q", irlml2502_pin_list(),
                                  (-5.08, -5.08, 5.08, 5.08), value="IRLML2502"))
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
    "qssc:XC7A35T_CSG324_GRAD": "XC7A35T_CSG324_GRAD",
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
    "qssc:MCP4921": "MCP4921", "qssc:LM358": "LM358", "qssc:IRLML2502": "IRLML2502",
}

