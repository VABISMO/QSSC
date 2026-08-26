#!/usr/bin/env python3
"""Generate the QSSC swappable chip MODULE -- the analog "socket for the chip".

Mates the main control board at J11 (Samtec LSHM-130 N) via J1 (LSHM-130 S).
On-module front-end:

  - 2x ADG732BCPZ 32:1 analog MUX -> 64:1 over the 64 on-chip graphene
    photodetector electrodes (DET0..DET63). A 6th select bit drives U1.EN
    directly and, through a 74LVC1G04 inverter, U2.EN -- so sel5=0 reads
    channels 0..31 (U1) and sel5=1 reads 32..63 (U2).
  - ADA4530-1 electrometer TIA (Rf 1 MOhm, Cf 1.5 pF) -> TIA_OUT crosses the
    connector back to the Artix-7 XADC.
  - The CQD emitter 8x8 matrix rows/cols (ER0..7 / EC0..7) pass straight
    through J1 to the FPGA; the emitter high-voltage driver + per-pixel set
    resistors land in a follow-up batch (the +7V_EMIT rail is brought across
    and flagged now).
  - A 10 k NTC thermistor divider gives a THRM sense line back to the FPGA.

Only digital selects, the one TIA analog output, detector bias, the
modulation reference, the thermistor sense, and power rails cross J1; the 64
detector electrodes stay on the module -- exactly the "Modulo hija con
front-end analogico" the user chose.

Reuses gen_qssc_kicad's sexp machinery (emit/parse, custom_symbol_def,
replicated_symbol, power_symbol_def, fp_net_pads, emit_schematic/emit_pcb/
emit_pro) by swapping the component list + authored-symbol set. No duplicated
emitter code. Headless, KiCad pcbnew-free (this only writes text).

Datasheet pinouts (all real, none invented):
  ADG732BCPZ -- Analog Devices 48-lead LFCSP (CP-48-1, 7x7 mm, EP 5.15x5.15).
  Footprint Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.15x5.15mm (49 pads,
  EP="49"); the generic QFN-48 numbers pin 1 top-left CCW, matching the ADI
  CP-48-1 pinout (verified against the footprint pad coordinates).  Channel
  -> pad map per the ADG732 datasheet (see ADG732_CHAN_PIN_48).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import gen_qssc_kicad as g  # noqa: E402

PCB_DIR = ROOT / "hardware" / "pcb"

# --------------------------------------------------------------------------
# ADG732BCPZ 48-lead LFCSP (ADI CP-48-1) -- datasheet-correct channel/pad map.
# --------------------------------------------------------------------------
# Per the ADG732 datasheet (48-lead LFCSP pin assignment):
#   pad 1..12  = S16,S15,...,S5
#   pad 13,14  = VDD
#   pad 15..19 = A0..A4
#   pad 20=CS  21=WR  22=EN  23=GND  24=VSS
#   pad 25..40 = S17..S32
#   pad 41,42,44 = NC   pad 43 = D (drain)   pad 49 (EP) = GND
#   pad 45..48 = S4,S3,S2,S1
# Channel index 0 = S1 ... index 31 = S32; value = pad number string.
ADG732_CHAN_PIN_48 = [
    # S1..S4  : pads 48, 47, 46, 45
    48, 47, 46, 45,
    # S5..S16 : pads 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1
    12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1,
    # S17..S32: pads 25..40
    25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40,
]
# control / power pads (48-lead LFCSP):
#   13=VDD 14=VDD 15=A0 16=A1 17=A2 18=A3 19=A4 20=CS 21=WR 22=EN
#   23=GND 24=VSS 41=NC 42=NC 43=D 44=NC  49(EP)=GND
ADG732_CTRL = {
    "13": "VDD", "14": "VDD",
    "15": "A0", "16": "A1", "17": "A2", "18": "A3", "19": "A4",
    "20": "CS", "21": "WR", "22": "EN", "23": "GND", "24": "VSS",
    "43": "D", "49": "EPAD",
}
# (ADG732_CTRL is kept for reference; net mapping is built in adg732_net_map.)


def adg732_pin_list():
    """Schematic pins for the 48-lead ADG732, 4 sides at 1.27 mm pitch."""
    sp = 1.27
    pins = []
    # right column: 32 channels S1..S32 top->bottom
    yR = sp * (32 - 1) / 2  # 19.685
    for i in range(32):
        pad = str(ADG732_CHAN_PIN_48[i])
        pins.append(dict(number=pad, name=f"S{i+1}", etype="passive",
                         x=15.0, y=round(yR - i * sp, 2), rot=180, length=2.54))
    # left column: A0..A4, CS, WR, EN, D  (9 pins)
    ctrl = [("15", "A0", "input"), ("16", "A1", "input"), ("17", "A2", "input"),
            ("18", "A3", "input"), ("19", "A4", "input"),
            ("20", "CS", "input"), ("21", "WR", "input"), ("22", "EN", "input"),
            ("43", "D", "bidirectional")]
    yL = sp * (len(ctrl) - 1) / 2  # 5.08
    for i, (num, nm, et) in enumerate(ctrl):
        pins.append(dict(number=num, name=nm, etype=et, x=-15.0,
                         y=round(yL - i * sp, 2), rot=0, length=2.54))
    # top: VDD (2 pins)
    for i, num in enumerate(["13", "14"]):
        pins.append(dict(number=num, name="VDD", etype="power_in",
                         x=round(-sp / 2 + i * sp, 2), y=-10.16, rot=90, length=2.54))
    # bottom: GND(23), VSS(24), EPAD(49)
    bot = [("23", "GND"), ("24", "VSS"), ("49", "EPAD")]
    xB = -sp * (len(bot) - 1) / 2
    for i, (num, nm) in enumerate(bot):
        pins.append(dict(number=num, name=nm, etype="power_in",
                         x=round(xB + i * sp, 2), y=10.16, rot=270, length=2.54))
    return pins


def adg732_net_map(off):
    """{pad: net} for one ADG732.  off=0 -> DET_IN0..31, off=32 -> DET_IN32..63."""
    m = {}
    for i in range(32):
        m[str(ADG732_CHAN_PIN_48[i])] = f"DET_IN{off + i}"
    m["15"] = "det_mux_sel0"
    m["16"] = "det_mux_sel1"
    m["17"] = "det_mux_sel2"
    m["18"] = "det_mux_sel3"
    m["19"] = "det_mux_sel4"
    m["20"] = "GND"        # CS tied low (always selected)
    m["21"] = "GND"        # WR tied low (transparent)
    m["22"] = "det_mux_sel5" if off == 0 else "MUX_EN2"
    m["43"] = "TIA_IN"     # mux drain -> TIA inverting input
    m["13"] = "+3V3"
    m["14"] = "+3V3"
    m["23"] = "GND"
    m["24"] = "GND"
    m["49"] = "GND"        # exposed pad
    return m


# --------------------------------------------------------------------------
# QSSC_Chip -- the quantum die: 64 detector electrodes + 8 emitter rows/cols.
# 80 pads.  Symbol pins == footprint pad names == {"DET0".."DET63",
# "ER0".."ER7", "EC0".."EC7"}.
# --------------------------------------------------------------------------
def chip_pin_list():
    sp = 1.27
    pins = []
    # right: DET0..DET31
    yR = sp * 31 / 2
    for i in range(32):
        pins.append(dict(number=f"DET{i}", name=f"DET{i}", etype="passive",
                         x=20.0, y=round(yR - i * sp, 2), rot=180, length=2.54))
    # left: DET32..DET63
    yL = sp * 31 / 2
    for i in range(32):
        pins.append(dict(number=f"DET{i+32}", name=f"DET{i+32}", etype="passive",
                         x=-20.0, y=round(yL - i * sp, 2), rot=0, length=2.54))
    # top: ER0..ER7 (emitter row anode-enables)
    xT = -sp * 7 / 2
    for i in range(8):
        pins.append(dict(number=f"ER{i}", name=f"ER{i}", etype="passive",
                         x=round(xT + i * sp, 2), y=-22.0, rot=90, length=2.54))
    # bottom: EC0..EC7 (emitter col cathode-enables)
    xB = -sp * 7 / 2
    for i in range(8):
        pins.append(dict(number=f"EC{i}", name=f"EC{i}", etype="passive",
                         x=round(xB + i * sp, 2), y=22.0, rot=270, length=2.54))
    return pins


def chip_net_map():
    m = {}
    for i in range(64):
        m[f"DET{i}"] = f"DET_IN{i}"
    for i in range(8):
        m[f"ER{i}"] = f"emitter_rows{i}"
        m[f"EC{i}"] = f"emitter_cols{i}"
    return m


def chip_footprint():
    """Inline (footprint ...) sexp for the QSSC die: 80 pads on F.Cu.

    64 detector pads in an 8x8 grid (0.5 mm pitch, 0.15 mm square) centred at
    (0,0); 16 emitter pads around the die periphery.  Pad names match the
    symbol pin numbers so fp_net_pads can net every pad.  The emitter pads sit
    OUTSIDE the detector grid (y = +/-2.4 mm vs the grid's +/-1.75 mm) so the
    two never short."""
    A, S, L = g.A, g.S, g.L
    u = g.u
    pads = []
    # 8x8 detector grid, 0.5 mm pitch, centred -> spans +/-1.75 mm
    pitch = 0.5
    half = 3.5 * pitch  # 1.75 (8 pads, 7 intervals)
    for r in range(8):
        for c in range(8):
            idx = r * 8 + c
            x = round(-half + c * pitch, 3)
            y = round(-half + r * pitch, 3)
            pads.append(L(A("pad"), S(f"DET{idx}"), A("smd"), A("rect"),
                         L(A("at"), A(x), A(y)),
                         L(A("size"), A(0.15), A(0.15)),
                         L(A("layers"), S("F.Cu"), S("F.Paste"), S("F.Mask"))))
    # emitter pads on the periphery, well outside the +/-1.75 mm detector grid
    ep = 2.4
    for i in range(8):
        e = round(-half + i * pitch, 3)
        # ER0..ER7 along the top edge
        pads.append(L(A("pad"), S(f"ER{i}"), A("smd"), A("rect"),
                     L(A("at"), A(e), A(-ep)),
                     L(A("size"), A(0.15), A(0.15)),
                     L(A("layers"), S("F.Cu"), S("F.Paste"), S("F.Mask"))))
        # EC0..EC7 along the bottom edge
        pads.append(L(A("pad"), S(f"EC{i}"), A("smd"), A("rect"),
                     L(A("at"), A(e), A(ep)),
                     L(A("size"), A(0.15), A(0.15)),
                     L(A("layers"), S("F.Cu"), S("F.Paste"), S("F.Mask"))))
    fp = L(A("footprint"), S("qssc:QSSC_Chip"),
            L(A("layer"), S("F.Cu")),
            L(A("version"), A(20240108)),
            L(A("generator"), S("gen_chip_module")),
            L(A("generator_version"), S("8.0")),
            L(A("descr"), S("QSSC quantum die: 64 graphene detector electrodes + 8x8 CQD emitter matrix")),
            L(A("attr"), A("smd")),
            L(A("property"), S("Reference"), S("U"),
              L(A("at"), A(0), A(-3.2), A(0)),
              L(A("effects"), L(A("font"), L(A("size"), A(0.6), A(0.6))))),
            L(A("property"), S("Value"), S("QSSC_Chip"),
              L(A("at"), A(0), A(3.2), A(0)),
              L(A("effects"), L(A("font"), L(A("size"), A(0.6), A(0.6))))),
            # die outline on F.Fab + courtyard (encompass the +/-2.4 mm emitters)
            L(A("fp_rect"), L(A("start"), A(-2.6), A(-2.6)), L(A("end"), A(2.6), A(2.6)),
              L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))),
              L(A("fill"), A("none")), L(A("layer"), S("F.Fab")), L(A("uuid"), S(u()))),
            L(A("fp_rect"), L(A("start"), A(-2.9), A(-2.9)), L(A("end"), A(2.9), A(2.9)),
              L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))),
              L(A("fill"), A("none")), L(A("layer"), S("F.CrtYd")), L(A("uuid"), S(u()))),
            *pads)
    return fp


# --------------------------------------------------------------------------
# Module symbol set + monkeypatch of gen_qssc_kicad
# --------------------------------------------------------------------------
_FP_CHIP = None  # built lazily (after g imported)


def _chip_fp_load(fp_lib_id):
    if fp_lib_id == "qssc:QSSC_Chip":
        return chip_footprint()
    return _ORIG_LOAD_FP(fp_lib_id)


_ORIG_LOAD_FP = g.load_footprint_mod


def module_authored_symbols():
    """Full symbol set the module schematic embeds in lib_symbols."""
    syms = []
    # power symbols (module uses +3V3, +5V, +7V_EMIT, GND; keep +1V8/+1V0/+5V_RF
    # too so the shared lib is consistent -- they cost nothing and are unused)
    g.POWER_PINS["+7V_EMIT"] = ("power_in", 90)
    for n in ["+3V3", "+1V8", "+1V0", "+5V", "+5V_RF", "+7V_EMIT", "GND"]:
        syms.append(g.power_symbol_def(n))
    syms.append(g.PWR_FLAG_def())
    # ADG732 (datasheet-authored 48-pin symbol, real LFCSP-48 footprint)
    syms.append(g.custom_symbol_def("qssc", "ADG732", "U", adg732_pin_list(),
                                    (-16.0, -12.7, 16.0, 12.7), value="ADG732BCPZ"))
    # QSSC die (datasheet-authored 80-pad symbol, inline footprint)
    syms.append(g.custom_symbol_def("qssc", "QSSC_Chip", "U", chip_pin_list(),
                                    (-21.0, -24.0, 21.0, 24.0), value="QSSC_Chip"))
    # replicated standard symbols the module reuses
    syms.append(g.replicated_symbol("qssc", "ADA4530-1", "U",
                                    g.std_pins("Amplifier_Operational", "ADA4530-1"),
                                    value="ADA4530-1"))
    syms.append(g.replicated_symbol("qssc", "74LVC1G04", "U",
                                    g.std_pins("74xGxx", "74LVC1G04"),
                                    value="74LVC1G04"))
    syms.append(g.replicated_symbol("qssc", "Conn_02x30_Odd_Even", "J",
                                    g.std_pins("Connector_Generic", "Conn_02x30_Odd_Even"),
                                    value="Samtec LSHM-130"))
    # passives R, C
    for nm, refp in [("R", "R"), ("C", "C")]:
        p1 = dict(number="1", name="~", etype="passive", x=0, y=3.81, rot=270, length=1.27)
        p2 = dict(number="2", name="~", etype="passive", x=0, y=-3.81, rot=90, length=1.27)
        syms.append(g.custom_symbol_def("qssc", nm, refp, [p1, p2],
                                        (-3.81, -3.81, 3.81, 3.81), value=nm))
    return syms


def module_build_pincoords():
    """Same as g.build_pincoords but the power list includes +7V_EMIT."""
    for s in module_authored_symbols():
        lib_id = s[1][1]
        coords = {}
        for child in s:
            if isinstance(child, list) and child and child[0] == ("atom", "symbol") and \
               child[1][1].endswith("_0_1"):
                for sub in child:
                    if isinstance(sub, list) and sub and sub[0] == ("atom", "pin"):
                        at = [c for c in sub if isinstance(c, list) and c[0] == ("atom", "at")][0]
                        num = [c for c in sub if isinstance(c, list) and c[0] == ("atom", "number")][0]
                        coords[num[1][1]] = (float(at[1][1]), float(at[2][1]), int(at[3][1]))
        g._PINCOORDS[lib_id] = coords
    for n in ["+3V3", "+1V8", "+1V0", "+5V", "+5V_RF", "+7V_EMIT", "GND", "PWR_FLAG"]:
        g._PINCOORDS[f"power:{n}"] = {"1": (0.0, 0.0, 90 if n != "GND" else 270)}


# net-class sets: extend the analog set with the module's detector nets
g.ANALOG_NETS = g.ANALOG_NETS | {f"DET_IN{i}" for i in range(64)} | {
    "TIA_IN", "TIA_REF", "MUX_EN2", "DET_BIAS", "THRM"}
g.POWER_NETS = g.POWER_NETS | {"+7V_EMIT"}


# --------------------------------------------------------------------------
# Components (module board)
# --------------------------------------------------------------------------
# J1 mirrors the main-board J11 pin-for-pin (same J11P).  Reuse g.J11P if the
# import populated it, else rebuild identically.
def _j1_pins():
    p = {}
    for i in range(8):
        p[str(1 + 2 * i)] = f"emitter_rows{i}"
    for i in range(8):
        p[str(17 + 2 * i)] = f"emitter_cols{i}"
    for i in range(6):
        p[str(33 + 2 * i)] = f"det_mux_sel{i}"
    p["45"] = "mod_sync"
    p["47"] = "TIA_OUT"
    p["49"] = "TIA_REF"
    p["51"] = "DET_BIAS"
    p["53"] = "THRM"
    p["55"] = "+3V3"
    p["57"] = "+5V"
    p["59"] = "+7V_EMIT"
    for i in range(1, 61):
        if i % 2 == 0:
            p[str(i)] = "GND"
    # Samtec LSHM-130 S-variant has 2 shield pads named "SH" -> tie to GND
    p["SH"] = "GND"
    return p


def _addm(ref, lib_id, value, fp, sch_xy, rot, pins, pcb):
    g.add(ref, lib_id, value, fp, sch_xy[0], sch_xy[1], rot, pins)
    c = g.COMPONENTS[-1]
    c["pcb_x"], c["pcb_y"], c["pcb_rot"] = pcb


def build_module_components():
    g.COMPONENTS[:] = []  # replace, not extend

    # J1 -- mating B2B (Samtec LSHM-130 S).  Schematic near top, PCB bottom
    # edge (the module mates the main board here).  Samtec NPTH mounting holes
    # sit at x = +/-8.5 mm from origin -> keep caps away from x 26.5 / 43.5.
    _addm("J1", "qssc:Conn_02x30_Odd_Even", "Samtec LSHM-130",
          "Connector_Samtec:Samtec_LSHM-130-xx.x-x-DV-S_2x30-1SH_P0.50mm_Vertical",
          (170, 40), 0, _j1_pins(), (35, 44, 0))

    # U1/U2 -- two ADG732 32:1 MUX (48-lead LFCSP 7x7 mm).  Flanking the chip.
    _addm("U1", "qssc:ADG732", "ADG732BCPZ",
          "Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.15x5.15mm",
          (120, 60), 0, adg732_net_map(0), (14, 20, 0))
    _addm("U2", "qssc:ADG732", "ADG732BCPZ",
          "Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.15x5.15mm",
          (220, 60), 0, adg732_net_map(32), (56, 20, 0))

    # U4 -- 74LVC1G04 inverter: A=det_mux_sel5 -> Y=MUX_EN2 (drives U2.EN)
    _addm("U4", "qssc:74LVC1G04", "74LVC1G04",
          "Package_TO_SOT_SMD:SOT-23-5",
          (220, 40), 0, {"2": "det_mux_sel5", "4": "MUX_EN2",
                         "5": "+3V3", "3": "GND"}, (56, 10, 0))

    # U3 -- ADA4530-1 TIA.  +IN=TIA_REF, -IN=TIA_IN, OUT=TIA_OUT, V+=+3V3, V-=GND.
    _addm("U3", "qssc:ADA4530-1", "ADA4530-1",
          "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
          (170, 90), 0,
          {"1": "TIA_REF", "8": "TIA_IN", "6": "TIA_OUT", "5": "+3V3", "4": "GND"},
          (35, 30, 0))

    # TIA feedback: Rf 1M + Cf 1.5p across TIA_IN -- TIA_OUT
    _addm("Rf", "qssc:R", "1M", "Resistor_SMD:R_0603_1608Metric",
          (170, 100), 0, {"1": "TIA_IN", "2": "TIA_OUT"}, (28, 36, 0))
    _addm("Cf", "qssc:C", "1.5p", "Capacitor_SMD:C_0603_1608Metric",
          (182, 100), 0, {"1": "TIA_IN", "2": "TIA_OUT"}, (31, 36, 0))

    # QSSC die -- 80 pads, centred above the TIA
    _addm("U5", "qssc:QSSC_Chip", "QSSC_Chip", "qssc:QSSC_Chip",
          (170, 120), 0, chip_net_map(), (35, 18, 0))

    # Thermistor divider: +3V3 -> R_THM -> THRM -> NTC -> GND (sense line to J1)
    _addm("R_THM", "qssc:R", "10k", "Resistor_SMD:R_0603_1608Metric",
          (120, 40), 0, {"1": "+3V3", "2": "THRM"}, (8, 36, 0))
    _addm("TH1", "qssc:R", "10k NTC", "Resistor_SMD:R_0603_1608Metric",
          (132, 40), 0, {"1": "THRM", "2": "GND"}, (8, 40, 0))

    # Decoupling: 100n per IC VCC (U1,U2,U3,U4) + 100n on +5V and +7V_EMIT +
    # 10uF bulk on +3V3.  C5/C6 kept clear of the J1 NPTH holes (x 26.5 / 43.5).
    _addm("C1", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
          (120, 70), 0, {"1": "+3V3", "2": "GND"}, (14, 27, 0))   # U1
    _addm("C2", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
          (220, 70), 0, {"1": "+3V3", "2": "GND"}, (56, 27, 0))   # U2
    _addm("C3", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
          (170, 80), 0, {"1": "+3V3", "2": "GND"}, (35, 36, 0))    # U3
    _addm("C4", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
          (200, 40), 0, {"1": "+3V3", "2": "GND"}, (52, 10, 0))    # U4
    _addm("C5", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
          (150, 40), 0, {"1": "+5V", "2": "GND"}, (22, 40, 0))
    _addm("C6", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
          (190, 40), 0, {"1": "+7V_EMIT", "2": "GND"}, (48, 40, 0))
    _addm("C7", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
          (170, 70), 0, {"1": "+3V3", "2": "GND"}, (20, 30, 0))   # +3V3 bulk

    # PWR_FLAG on every rail so the connector-fed power_in pins are "driven"
    _addm("#FLG1", "power:PWR_FLAG", "PWR_FLAG", "", (100, 30), 0,
          {"1": "+3V3"}, (6, 4, 0))
    _addm("#FLG2", "power:PWR_FLAG", "PWR_FLAG", "", (100, 40), 0,
          {"1": "+5V"}, (6, 9, 0))
    _addm("#FLG3", "power:PWR_FLAG", "PWR_FLAG", "", (100, 50), 0,
          {"1": "+7V_EMIT"}, (6, 14, 0))
    _addm("#FLG4", "power:PWR_FLAG", "PWR_FLAG", "", (100, 60), 0,
          {"1": "GND"}, (6, 19, 0))


# --------------------------------------------------------------------------
# Emit
# --------------------------------------------------------------------------
def main():
    PCB_DIR.mkdir(parents=True, exist_ok=True)

    # monkeypatch gen_qssc_kicad for the module
    g.authored_symbols = module_authored_symbols
    g.build_pincoords = module_build_pincoords
    g.load_footprint_mod = _chip_fp_load
    g.BOARD_W, g.BOARD_H = 70, 50

    build_module_components()
    module_build_pincoords()

    # symbol lib (embedded in schematic anyway, but write for completeness)
    lib_syms = module_authored_symbols()
    libfile = g.L(g.A("kicad_symbol_lib"),
                  g.L(g.A("version"), g.A(20231120)),
                  g.L(g.A("generator"), g.S("gen_chip_module")),
                  g.L(g.A("generator_version"), g.S("8.0")),
                  *lib_syms)
    (PCB_DIR / "chip_module-lib.kicad_sym").write_text(g.emit(libfile) + "\n")

    sch = g.emit_schematic().replace("QSSC Control Board", "QSSC Chip Module")
    (PCB_DIR / "chip_module.kicad_sch").write_text(sch + "\n")
    print("wrote chip_module.kicad_sch")

    (PCB_DIR / "chip_module.kicad_pcb").write_text(g.emit_pcb() + "\n")
    print("wrote chip_module.kicad_pcb")

    (PCB_DIR / "chip_module.kicad_pro").write_text(g.emit_pro() + "\n")
    print("wrote chip_module.kicad_pro")


if __name__ == "__main__":
    main()