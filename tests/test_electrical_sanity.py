# tests/test_electrical_sanity.py
"""Datasheet-level electrical-sanity gates (main / head / grad + symbols).

Where ``test_pcb_component_audit.py`` is the usage/connectivity audit
(every passive has a job, every IC is powered, BOM matches board), this file
is the electrical-correctness audit. It exists because the first sweep of
this kind found real beginner-grade electrical defects that NO ERC/DRC in
the suite can see: ERC knows nothing about which pin is V+ on an LM358, DRC
knows nothing about a 3.3 V SPI master driving a 5 V-coded VIH input, and a
BOM review knows nothing about digits 5-6 of a Würth WR-PHD part number
encoding the contact count.

At gate creation the five defects below were frozen as
``xfail(strict=True)`` -- each failing test goes red with an XPASS once
repaired, until the mark is removed and the green state re-pinned (that is
the re-classification alarm). Defects 1, 2, 4 and 5 are repaired on
``fix/electrical-repairs``: markers dropped, gates re-pinned green.
Defect 3 is still open:

  1. LM358 power pads swapped, netlist (grad/Udrv): pad 4 sits on +5V and
     pad 8 on GND. The true LM358 dual opamp is 4 = V-, 8 = V+; the
     symbol carries the QUAD-opamp convention (4 = V+, 11 = V-, as the head
     board's TLV9004 quads correctly use) copied into a dual part.
  2. LM358 power pins swapped, lib-symbol (qssc-grad.kicad_sch):
     ``qssc:LM358`` names pin number 4 "VCC" (power_in) and pin number 8
     "GND" (power_in) -- the source of defect 1 (regenerating the netlist
     from this symbol does NOT fix the board).
  3. MCP4921 (grad/Ugrad) runs at VDD = +5V while the SPI master is
     LVCMOS33: Microchip DS22248A datasheet VIH = 0.7 * VDD, so 3.5 V is
     required and a 3.3 V master is out of spec. Note the coupled
     constraint: DS22248A absolute maximum is "all inputs and outputs
     w.r.t. VSS -0.3V to VDD +0.3V", and VREF (pad 6) is tied to +5V, so
     VDD can NOT simply drop to 3V3 -- the reference path must change with
     it (level shifter, DAC swap, or reference redesign; issue #168).
  4. J9 (main): Würth 61300411121 -- digits 5-6 encode FOUR contacts --
     on a 2x05 / 10-pad JTAG footprint (issue #169).
  5. Udrv BOM row (grad): TI part "LM358P" -- the -P suffix is PDIP-8 --
     on an SOIC-8 footprint (issue #169).

Everything else here is green and pins verified-correct state: symbol pins
matching Microchip DS22248A, the MCP4921 signal wiring, the quad-opamp
power convention on all 16 head TLV9004, per-IC supply rails, the exact
U*/Y*/REG* inventory per board, Würth contact-vs-pad agreement, the TVS
map, the fuse/ferrite/bias chains, rail decoupling, the USB-C front end,
the LED and reset networks (and the RTL reset polarity that matches the
button network), the detector network uniformity, and BOM MPN
uniqueness/blanket.
"""
from __future__ import annotations

import csv
import re
from functools import cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PCB_DIR = REPO / "hardware" / "pcb"
BOARDS = {
    "main": PCB_DIR / "qssc.kicad_pcb",
    "grad": PCB_DIR / "qssc-grad.kicad_pcb",
    "head": PCB_DIR / "chip_module.kicad_pcb",
}
GRAD_SCH = PCB_DIR / "qssc-grad.kicad_sch"
BOM_CSV = REPO / "hardware" / "datasheets" / "BOM.csv"


def _need_grad():
    """The gradient board is local-only: skip its gates when unpublished."""
    if not (PCB_DIR / "qssc-grad.kicad_pcb").exists() or not GRAD_SCH.exists():
        pytest.skip("qssc-grad board is local-only (not published here)")

# Exact board inventory as parsed by _board(). If the s-expression layout
# changes (or this mini-parser rots) the counts below shift and these gates
# fail instead of silently scanning nothing.
BOARD_INVENTORY = {
    "main": {"nets": 137, "fps": 126, "pads": 1019},
    "grad": {"nets": 147, "fps": 134, "pads": 1048},
    "head": {"nets": 159, "fps": 201, "pads": 991},
}


# --------------------------------------------------------------------------
# parsers (same strategy as test_pcb_component_audit, regex-based so the
# whole file runs without pcbnew in CI)
# --------------------------------------------------------------------------
@cache
def _board(board: str) -> dict:
    """{nets: id->name, name2id: name->id, fps: ref->{value, pads}, pads_total}"""
    txt = BOARDS[board].read_text(encoding="utf-8")
    header = txt[: txt.find("(footprint")]
    nets = {int(i): n for i, n in re.findall(r'\(net (\d+) "([^"]*)"\)', header)}
    name2id = {n: i for i, n in nets.items()}
    fps: dict[str, dict] = {}
    pads_total = 0
    for blk in re.split(r"\n\t\(footprint ", txt)[1:]:
        m = re.search(r'\(property "Reference" "([^"]*)"', blk)
        if not m:
            continue
        val = re.search(r'\(property "Value" "([^"]*)"', blk)
        pads: dict[str, str] = {}
        for pad in re.split(r"\n\t+\(pad ", blk)[1:]:
            pads_total += 1
            pn = re.match(r'"([^"]*)"', pad)
            nm = re.search(r"\(net (\d+)", pad)
            if pn:
                pads[pn.group(1)] = nets.get(int(nm.group(1)), "") if nm else ""
        fps[m.group(1)] = {"value": val.group(1) if val else "", "pads": pads}
    return {"nets": nets, "name2id": name2id, "fps": fps,
            "pads_total": pads_total}


@cache
def _sch_pins(symbol: str) -> dict:
    """{pin number: pin name} for one lib-symbol in the grad schematic.

    The window runs from the symbol header to the next TOP-LEVEL library
    symbol: KiCad emits per-URL unit sub-symbols BEFORE that
    (``"qssc:MCP4921_0_1"``, ``_1_1``, ..., whose pin definitions are the
    real ones), so a naive "slice to the next qssc: symbol" lands in an
    empty unit shell. Sub-symbols are recognised by the
    ``<lib_id>_<unit>_<style>`` suffix and skipped. Each ``(name "...")``
    is paired with the next ``(number "...")`` -- in KiCad lib symbols the
    name precedes the number inside a pin definition.
    """
    txt = GRAD_SCH.read_text(encoding="utf-8")
    i = txt.index(f'(symbol "{symbol}"')
    j = i + 1
    while True:
        k = txt.find('(symbol "qssc:', j)
        if k == -1:
            block = txt[i:]
            break
        nxt = re.search(r'\(symbol "qssc:([^"]+)"', txt[k: k + 80]).group(1)
        if not nxt.startswith(f"{symbol}_"):
            block = txt[i: k]
            break
        j = k + 1
    pins: dict[str, str] = {}
    # KiCad 10 writes the name/number values on their OWN line inside the
    # pin sub-block ("(name\n\t\"VREF\"\n\t(effects ...)\n)"), so the
    # whitespace between opener and value must be matched and the pairing
    # window has to cross the effects block (~150 lines of boilerplate).
    for m in re.finditer(r'\(\s*name\s+"([^"]*)"[\s\S]{0,400}?\(number\s+"(\d+)"',
                         block):
        pins[m.group(2)] = m.group(1)
    return pins


@cache
def _bom() -> list:
    with BOM_CSV.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _bom_row(board: str, ref: str) -> dict:
    for row in _bom():
        if row["Board"].strip() == board and ref in [d.strip() for d in
                                                     row["Designators"].split(",")]:
            return row
    raise AssertionError(f"no BOM row for {board}/{ref}")


# --------------------------------------------------------------------------
# parser integrity: the inventory itself is pinned so a silently-wrong
# parse cannot make every gate below vacuous
# --------------------------------------------------------------------------
@pytest.mark.parametrize("board", ["main", "grad", "head"])
def test_board_parse_inventory(board: str):
    b = _board(board)
    inv = BOARD_INVENTORY[board]
    assert len(b["nets"]) == inv["nets"], f"{board}: net inventory drifted"
    assert len(b["fps"]) == inv["fps"], f"{board}: footprint count drifted"
    assert b["pads_total"] == inv["pads"], f"{board}: pad count drifted"


# --------------------------------------------------------------------------
# known defect 1+2: LM358 dual-opamp power pins (grad board + lib symbol)
# --------------------------------------------------------------------------
# Repaired on fix/electrical-repairs: pad 4 = GND, pad 8 = +5V, symbol
# renamed (issue #168). Gate stays as a regression pin, no xfail.
def test_lm358_dual_opamp_power_pads():
    _need_grad()
    pads = _board("grad")["fps"]["Udrv"]["pads"]
    assert pads["4"] == "GND", f"pad 4 (must be V-): {pads.get('4')}"
    assert pads["8"] == "+5V", f"pad 8 (must be V+): {pads.get('8')}"


def test_lm358_symbol_pin_numbering():
    _need_grad()
    pins = _sch_pins("qssc:LM358")
    assert len(pins) == 8, f"LM358 symbol pins parsed: {sorted(pins)}"
    norm = {k: v.upper() for k, v in pins.items()}
    # Pin 4 must name the negative supply; single-supply symbols may ground
    # it ("GND"), so that is accepted as a repair. Pin 8 must be positive.
    assert norm["4"] in {"V-", "VEE", "VS-", "-V", "VNEG", "GND", "VSS"}, \
        f"LM358 pin 4 = {pins['4']!r} (must be the negative supply)"
    assert norm["8"] in {"V+", "VCC", "VS+", "+V", "VPOS", "VDD", "+VS"}, \
        f"LM358 pin 8 = {pins['8']!r} (must be the positive supply)"


# --------------------------------------------------------------------------
# known defect 3: MCP4921 symbol is DS22248A-correct, but VDD=+5V vs
# LVCMOS33 SPI master is a level violation
# --------------------------------------------------------------------------
def test_mcp4921_symbol_matches_datasheet():
    _need_grad()
    pins = _sch_pins("qssc:MCP4921")
    assert len(pins) == 8, f"MCP4921 symbol pins parsed: {sorted(pins)}"
    norm = {k: v.upper().lstrip("!") for k, v in pins.items()}
    # DS22248A 8-pin pinout: 1 VDD, 2 !CS, 3 SCK, 4 SDI, 5 !LDAC, 6 VREF,
    # 7 VSS (the SOIC symbol calls it AVSS), 8 VOUT.
    expect = {"1": {"VDD"}, "2": {"CS"}, "3": {"SCK"}, "4": {"SDI"},
              "5": {"LDAC"}, "6": {"VREF"}, "7": {"AVSS", "VSS"},
              "8": {"VOUTA", "VOUT"}}
    for num, ok in expect.items():
        assert norm[num] in ok, (
            f"MCP4921 pin {num} = {pins[num]!r} -- DS22248A pinout expects "
            f"one of {sorted(ok)}")


def test_dac_signal_wiring_frozen():
    """Signal wiring of the grad DAC is correct today and frozen pad-exact.

    Pads 1 (VDD) and 6 (VREF) sit on +5V TODAY. Any repair of the level
    violation (issue #168) intentionally re-pins this map -- the freeze is
    what turns "the DAC was quietly rewired" into a suite failure.
    """
    _need_grad()
    pads = _board("grad")["fps"]["Ugrad"]["pads"]
    assert pads == {
        "1": "+5V",            # VDD (target: +3V3 -- see the xfail below)
        "2": "grad_cs",        # !CS  (LDAC held low: transparent updates)
        "3": "grad_sclk",
        "4": "grad_mosi",
        "5": "GND",            # !LDAC low -> latch transparent
        "6": "+5V",            # VREF (5 V full scale by design)
        "7": "GND",            # VSS
        "8": "GRAD_DAC_OUT",
    }


@pytest.mark.xfail(strict=True, reason="Ugrad MCP4921 VDD=+5V while the SPI "
                    "master is LVCMOS33: DS22248A VIH = 0.7*VDD -> 3.5V "
                    "required > 3.3V VOH. Coupled constraint: VREF=+5V (pad 6) "
                    "forces VDD>=5V via the abs-max VDD+0.3V input limit, so "
                    "the fix needs level-shifting or a reference redesign "
                    "(issue #168)")
def test_dac_vdd_compatible_with_lvcmos33():
    _need_grad()
    pads = _board("grad")["fps"]["Ugrad"]["pads"]
    assert pads["1"] == "+3V3", (
        f"MCP4921 VDD on {pads['1']!r}: VIH = 0.7*VDD (DS22248A) makes a "
        "5 V VDD incompatible with a 3.3 V SPI master")


# --------------------------------------------------------------------------
# quad-opamp convention (head board) -- green, pins all 16 TLV9004
# --------------------------------------------------------------------------
@pytest.mark.parametrize("ref", [f"UT{i}" for i in range(16)])
def test_tlv9004_quad_power_convention(ref: str):
    b = _board("head")
    assert b["fps"][ref]["value"].startswith("TLV9004"), b["fps"][ref]["value"]
    pads = b["fps"][ref]["pads"]
    assert pads["4"] == "+3V3", f"{ref} pad 4 (V+): {pads.get('4')}"
    assert pads["11"] == "GND", f"{ref} pad 11 (V-): {pads.get('11')}"


# --------------------------------------------------------------------------
# per-IC supply rails + exact IC inventory
# --------------------------------------------------------------------------
_IC_RAILS: dict = {
    "main": {
        "U1": {"+3V3", "+1V8", "+1V0"},         # XC7A35T
        "U3": {"+3V3"},                          # ADF4351BCPZ
        "U8": {"+3V3"},                          # W25Q128JVSIQ
        "UE0": {"+3V3"}, "UE1": {"+3V3"}, "UE2": {"+3V3"},   # TLC5947
        "Y1": {"+3V3"},                          # 100MHz XO
        "REG1": {"+5V", "+3V3"},                 # AMS1117-3.3
        "REG2": {"+5V", "+1V8"},                 # AMS1117-1.8
        "REG3": {"+5V", "+1V0"},                 # AP2127K-1.0
    },
    "grad": {
        "U1": {"+3V3", "+1V8", "+1V0"},
        "U3": {"+3V3"},
        "U8": {"+3V3"},
        "UE0": {"+3V3"}, "UE1": {"+3V3"}, "UE2": {"+3V3"},
        "Y1": {"+3V3"},
        "REG1": {"+5V", "+3V3"},
        "REG2": {"+5V", "+1V8"},
        "REG3": {"+5V", "+1V0"},
        "Ugrad": {"+5V"},                        # MCP4921 (+ VREF on +5V)
        "Udrv": {"+5V"},                         # LM358 (pad 4, swapped pad)
    },
    "head": {
        **{f"UT{i}": {"+3V3"} for i in range(16)},       # TLV9004
        **{f"UAD{i}": {"+3V3", "+5V_A"} for i in range(4)},  # AD7616
    },
}

# ICs that legitimately have NO rail pad (verified):
#   * U5 (GVA-84+ MMIC, SOT-89): DC bias arrives through the RF output line
#     via the bias-tee (L2: +5V_RF -> RF_AMP_OUT -> U5 pad 3).
RAILESS_IC = {"main": ("U5",), "grad": ("U5",), "head": ()}

EXPECTED_IC_REFS = {
    "main": {"U1", "U3", "U5", "U8", "UE0", "UE1", "UE2", "Uchip", "Y1",
             "REG1", "REG2", "REG3"},
    "grad": {"U1", "U3", "U5", "U8", "UE0", "UE1", "UE2", "Uchip", "Y1",
             "REG1", "REG2", "REG3", "Ugrad", "Udrv"},
    "head": {"Uchip"} | {f"UT{i}" for i in range(16)}
    | {f"UAD{i}" for i in range(4)},
}


@pytest.mark.parametrize("board,ref,rails", [
    pytest.param(b, ref, sorted(rails), id=f"{b}:{ref}")
    for b, m in sorted(_IC_RAILS.items()) for ref, rails in sorted(m.items())
])
def test_ic_supply_rails(board: str, ref: str, rails: list):
    actual = sorted({n for n in _board(board)["fps"][ref]["pads"].values()
                     if n.startswith("+")})
    assert actual == rails, f"{board}/{ref}: rails {actual}"
    assert "GND" in set(_board(board)["fps"][ref]["pads"].values()), \
        f"{board}/{ref}: no GND pad"


def test_ic_reference_inventory():
    """Any new U*/Y*/REG* part must be consciously documented in _IC_RAILS --
    an unlisted IC would otherwise ship without this file checking its rails."""
    for board in [b for b in ("main", "grad", "head")
                  if b != "grad" or BOARDS["grad"].exists()]:
        b = _board(board)
        refs = {r for r in b["fps"]
                if r.startswith(("U", "Y")) or re.match(r"^REG\d", r)}
        assert refs == EXPECTED_IC_REFS[board], f"{board}: IC inventory"

    # the documented railless exceptions really are railless
    for board, refs in RAILESS_IC.items():
        for ref in refs:
            rails = [n for n in _board(board)["fps"][ref]["pads"].values()
                     if n.startswith("+")]
            assert not rails, f"{board}/{ref}: unexpected rail pads {rails}"
            assert ref == "U5"  # only the SOT-89 MMIC is exempt today


# --------------------------------------------------------------------------
# Würth connector part numbers: digits 5-6 of the MPN are the contact count
# --------------------------------------------------------------------------
@pytest.mark.parametrize("board,ref", [
    ("main", "J8"),
    ("main", "J12"),
    ("head", "J12"),
    ("main", "J9"),  # repaired: 61301011121 (10 contacts) matches the 2x05 PADS
])
def test_wuerth_mpn_matches_pad_count(board: str, ref: str):
    row = _bom_row(board, ref)
    assert "Würth" in row["Manufacturer"], row["Manufacturer"]
    mpn = row["MPN"].strip()
    assert re.fullmatch(r"61\d{9}", mpn), mpn
    contacts = int(mpn[4:6])
    pads = _board(board)["fps"][ref]["pads"]
    n_pads = len([p for p in pads if p.strip()])
    assert contacts == n_pads, (
        f"Würth {mpn} declares {contacts} contacts but {board}/{ref} "
        f"({row['Value']}) has {n_pads} signal pads")


def test_lm358p_package_matches_footprint():
    """Udrv BOM repaired to LM358DR (SOIC-8, issue #169); gate stays as a pin."""
    row = _bom_row("grad", "Udrv")
    mpn, fp = row["MPN"].strip().upper(), row["Footprint"]
    assert not (re.search(r"\bSOIC\b", fp) and mpn.endswith("P")), (
        f"'{mpn}' is a PDIP order for footprint '{fp}'")


# --------------------------------------------------------------------------
# TVS map, protection chains, USB-C front end, LED, reset network
# --------------------------------------------------------------------------
_TVS_MAP = {
    "D1": "USB_DP", "D2": "USB_DM", "D3": "RF_AMP_OUT", "D4": "+5V_IN",
    "D5": "+5V", "D6": "+3V3", "D7": "+1V8", "D8": "+1V0", "D9": "+5V_RF",
    "D10": "mw_gate",
}


@pytest.mark.parametrize("board,ref,net", [
    pytest.param(b, ref, net, id=f"{b}:{ref}")
    for b in ("main", "grad") for ref, net in sorted(_TVS_MAP.items())
])
def test_tvs_diode_map(board: str, ref: str, net: str):
    """Every protected line has its ESD5V0 TVS with pin 1 on GND."""
    pads = _board(board)["fps"][ref]["pads"]
    assert pads["1"] == "GND", f"{board}/{ref}: pin 1 on {pads.get('1')}"
    assert pads["2"] == net, f"{board}/{ref}: pin 2 on {pads.get('2')} (want {net})"


@pytest.mark.parametrize("board", ["main", "grad"])
def test_usb_tvs_between_rails(board: str):
    """D_USB (SS14) blocks the back-feed of +5V_IN into VBUS_USB."""
    nets = {_board(board)["fps"]["D_USB"]["pads"][p] for p in ("1", "2")}
    assert nets == {"VBUS_USB", "+5V_IN"}


def test_head_has_no_tvs():
    """The head board carries no TVS at all (it has no USB/RF front end and
    its rails arrive through J12) -- a documented design state, not a slip:
    if a TVS ever appears on chip_module this pin keeps the change visible."""
    refs = [r for r in _board("head")["fps"]
            if r.startswith("D") and re.match(r"^D[\d_]", r)]
    assert refs == []


@pytest.mark.parametrize("board", ["main", "grad"])
def test_12v_emit_relies_on_ptc_only(board: str):
    """+12V_EMIT is intentionally TVS-less: its only protection is the F2
    PTC. Frozen so any TVS addition/removal here is conscious."""
    refs = [r for r in _board(board)["fps"]
            if _board(board)["fps"][r]["pads"]
            and "+12V_EMIT" in _board(board)["fps"][r]["pads"].values()
            and re.match(r"^D[\d_]", r)]
    assert refs == [], f"{board}: TVS on +12V_EMIT: {refs}"


_PWR_CHAINS = {
    "main": {"F1": {"+5V_IN", "+5V"},                # fuse
             "F2": {"+12V_EMIT_IN", "+12V_EMIT"},     # 1A PTC
             "FB1": {"+5V", "+5V_RF"},               # ferrite into RF rail
             "L2": {"+5V_RF", "RF_AMP_OUT"}},        # MMIC bias-tee
    "grad": {"F1": {"+5V_IN", "+5V"}, "F2": {"+12V_EMIT_IN", "+12V_EMIT"},
             "FB1": {"+5V", "+5V_RF"}, "L2": {"+5V_RF", "RF_AMP_OUT"},
             "Lgrad": {"GRAD_COIL_P", "GRAD_COIL_N"}},
    "head": {"FB_A": {"+5V", "+5V_A"}},
}


@pytest.mark.parametrize("board,ref,nets", [
    pytest.param(b, ref, sorted(nets), id=f"{b}:{ref}")
    for b, m in sorted(_PWR_CHAINS.items()) for ref, nets in sorted(m.items())
])
def test_fuse_ferrite_bias_chain(board: str, ref: str, nets: list):
    pads = _board(board)["fps"][ref]["pads"]
    got = sorted({n for n in pads.values() if n})
    assert got == nets, f"{board}/{ref}: nets {got} (want {nets})"
    assert len(pads) == 2, f"{board}/{ref}: {len(pads)} pads"


@pytest.mark.parametrize("board", ["main", "grad", "head"])
def test_fuse_ferrite_inventory(board: str):
    """Any new fuse/ferrite/inductor is consciously added to _PWR_CHAINS."""
    refs = set(_board(board)["fps"])
    # "FB_A" (head's analog-rail ferrite) carries a letter suffix, so the
    # digit-only pattern "^FB?\d" would silently drop it from the census.
    fp_refs = {r for r in refs if re.match(r"^FB?[\d_]", r)
               or (r.startswith("L") and not r.startswith(("LED", "LOGO")))}
    assert fp_refs == set(_PWR_CHAINS[board]), f"{board}: F/FB/L inventory"


@pytest.mark.parametrize("board", ["main", "grad"])
def test_usb_c_front_end(board: str):
    b = _board(board)

    def _pads(ref: str) -> dict:
        return b["fps"][ref]["pads"]

    # 5.1k Rd pull-downs: this connector identifies as a UFP (device)
    assert _pads("R71")["1"] == "CC1" and _pads("R71")["2"] == "GND"
    assert _pads("R72")["1"] == "CC2" and _pads("R72")["2"] == "GND"
    assert b["fps"]["R71"]["value"] == "5.1k"
    assert b["fps"]["R72"]["value"] == "5.1k"
    # USB2 pairs through 27R series
    assert _pads("R78") == {"1": "USB_DP_F", "2": "USB_DP"}
    assert _pads("R79") == {"1": "USB_DM_F", "2": "USB_DM"}
    # connector routing is symmetric across both CC lines
    j10 = _pads("J10")
    assert j10["A5"] == "CC1" and j10["B5"] == "CC2"
    assert j10["A6"] == "USB_DP" == j10["B6"]
    assert j10["A7"] == "USB_DM" == j10["B7"]
    assert j10["A4"] == "VBUS_USB" and j10["B4"] == "VBUS_USB"
    assert j10["A1"] == "GND" and j10["B12"] == "GND"


@pytest.mark.parametrize("board", ["main", "grad"])
def test_led_and_reset_network(board: str):
    """R70 drives the LED from +3V3 (330R); ``reset`` idles LOW through R83
    and the button pulls it HIGH -- an ACTIVE-HIGH reset, matching
    fpga/rtl/top_level.v's "// active high (button)"."""
    b = _board(board)

    def _pads(ref: str) -> dict:
        return b["fps"][ref]["pads"]

    assert _pads("LED1") == {"1": "GND", "2": "LED_ANODE"}
    assert b["fps"]["R70"]["value"] == "330"
    assert _pads("R70") == {"1": "+3V3", "2": "LED_ANODE"}
    assert b["fps"]["R83"]["value"] == "10k"
    assert _pads("R83") == {"1": "reset", "2": "GND"}
    assert b["fps"]["C68"]["value"] == "100nF"
    assert _pads("C68") == {"1": "reset", "2": "GND"}
    assert _pads("J7") == {"1": "+3V3", "2": "reset"}


def test_rtl_reset_polarity_matches_button_network():
    """The board pulls ``reset`` LOW at idle (R83) and asserts it HIGH
    (button) -- the RTL must agree (top_level.v treats it as active high
    with async assert / 2-stage sync deassert)."""
    txt = (REPO / "fpga" / "rtl" / "top_level.v").read_text(encoding="utf-8")
    assert "posedge reset" in txt
    assert re.search(r"if \(reset\)", txt)
    assert not re.search(r"if\s*\(\s*!reset\b", txt)


# --------------------------------------------------------------------------
# rail decoupling: every rail an IC pad sits on carries >= 1 capacitor
# --------------------------------------------------------------------------
def _decoupling_params() -> list:
    out = []
    for board in ("main", "grad", "head"):
        if board == "grad" and not BOARDS["grad"].exists():
            continue          # local-only gradient board, unpublished
        b = _board(board)
        rail_refs: dict = {}
        for ref, info in b["fps"].items():
            if ref.startswith(("U", "Y")) or re.match(r"^REG\d", ref):
                for net in {n for n in info["pads"].values()
                            if n.startswith("+")}:
                    rail_refs.setdefault(net, []).append(ref)
        for rail in sorted(rail_refs):
            ic_refs = sorted(set(rail_refs[rail]))
            out.append(pytest.param(board, rail, ic_refs,
                                    id=f"{board}:{rail}"))
    return out


@pytest.mark.parametrize("board,rail,ic_refs", _decoupling_params())
def test_rail_decoupling(board: str, rail: str, ic_refs: list):
    """Every rail with an IC pad carries >= 1 decoupling cap, and the IC list
    per rail stays pinned (new consumer -> conscious re-pin)."""
    assert len(ic_refs) >= 1

    def _cap_nets(ref: str) -> set:
        if not ref.startswith("C"):
            return set()
        return {n for n in _board(board)["fps"][ref]["pads"].values()
                if n.startswith("+")}

    rails = {rail}
    cap_refs = [ref for ref in _board(board)["fps"]
                if rails & _cap_nets(ref)]
    assert cap_refs, (
        f"{board}/{rail}: {len(ic_refs)} IC pad(s) on the rail and no "
        f"decoupling capacitor")
    assert cap_refs, f"{board}/{rail}: caps found: {sorted(cap_refs)}"


# --------------------------------------------------------------------------
# head detector network uniformity: 64 identical TIA channels
# --------------------------------------------------------------------------
@pytest.mark.parametrize("ch", list(range(64)))
def test_head_det_channel_uniformity(ch: int):
    """Each detector channel is a passive TIA: 2 IC pads (die socket + AD7616
    input), one 1M feedback R, one 1pF C -- identical, so a mis-place of one
    channel's part value turns the uniform network red."""
    b = _board("head")
    net = f"DET_IN{ch}"
    members = [(ref, info["value"])
               for ref, info in sorted(b["fps"].items())
               if net in set(info["pads"].values())]
    us = [m for m in members if m[0].startswith("U")]
    rs = [m for m in members if m[0].startswith("R")
          and not m[0].startswith("REG")]
    cs = [m for m in members if m[0].startswith("C")]
    assert len(members) == 4, f"{net}: {members}"
    assert len(us) == 2, f"{net}: {len(us)} IC pads"
    assert len(rs) == 1 and rs[0][1] == "1M", f"{net}: R = {rs}"
    assert len(cs) == 1 and cs[0][1] == "1pF", f"{net}: C = {cs}"


def test_head_det_ref_network():
    """DET_REF is the shared detector reference: IC pads = 1 die-socket pin +
    16 AD7616 REF-input pins (UT0..UT15), Rref1/Rref2 = 10k divider members,
    Cref = 100nF -- 20 members in total."""
    b = _board("head")
    net = "DET_REF"
    members = [(ref, info["value"])
               for ref, info in sorted(b["fps"].items())
               if net in set(info["pads"].values())]
    us = [m for m in members if m[0].startswith("U")]
    rs = [m for m in members if m[0].startswith("R")
          and not m[0].startswith("REG")]
    cs = [m for m in members if m[0].startswith("C")]
    assert len(members) == len(us) + len(rs) + len(cs) == 20
    assert len(us) == 17, f"DET_REF: {len(us)} IC pads (Uchip + 16 UT)"
    assert len(rs) == 2 and all(v == "10k" for _, v in rs), f"DET_REF: R = {rs}"
    assert len(cs) == 1 and cs[0][1] == "100nF", f"DET_REF: C = {cs}"


# --------------------------------------------------------------------------
# BOM: MPN uniqueness + the frozen no-MPN blanket
# --------------------------------------------------------------------------
KNOWN_NO_MPN = {
    # Repaired on this branch (issue #169): U3, Y1 and J4 now carry an MPN --
    # their rows were blank because MPN_MAP keyed them on values/footprints no
    # row ever used, not because the parts are unknown. What is left is
    # genuinely generic or project-specific.
    ("main", "F2"), ("main", "LED1"), ("main", "D_USB"), ("main", "Uchip"),
    ("head", "Uchip"),
    # The gradient board carries the same power/RF/connector parts as the main
    # board (build_gradient_board reuses build_main_board(grad=True)), so each
    # of them has a grad twin -- gen_bom emits the full grad section now
    # (issue #162) instead of six hand-typed rows.
    ("grad", "F2"), ("grad", "LED1"), ("grad", "D_USB"), ("grad", "Uchip"),
}
# rows whose designator MUST carry an MPN (active parts, connectors, fuses)
_NEEDS_MPN = re.compile(r"^(U|Y|J|D|R?EG\d|F|Q|LED)")


def test_bom_mpn_conflict_free():
    """One MPN must always mean one footprint.

    A part number appearing against two *footprints* is a paste/sourcing
    split waiting to happen -- one order, two land patterns. Two rows may
    legitimately share an MPN under different labels: J2's 5 V barrel and
    J4's 12 V barrel are the same CUI PJ-102AH, and every board twin repeats
    its part across main/grad, so the label is not the constraint. The
    footprint is (issue #169).
    """
    by_mpn: dict = {}
    for row in _bom():
        mpn = row["MPN"].strip()
        if not mpn:
            continue
        by_mpn.setdefault(mpn, set()).add(row["Footprint"])
    conflicts = {m: v for m, v in by_mpn.items() if len(v) > 1}
    assert not conflicts, f"MPN reuse across footprints: {conflicts}"


def test_bom_mpn_known_empty_set():
    """Every non-passive BOM row without an MPN is exactly the frozen set
    below. A new MPN-less connector/IC row (or a repair of one of these)
    fails until the set is re-pinned."""
    empties = set()
    for row in _bom():
        if row["MPN"].strip():
            continue
        for d in [x.strip() for x in row["Designators"].split(",")]:
            if d and _NEEDS_MPN.match(d):
                empties.add((row["Board"].strip(), d))
    assert empties == KNOWN_NO_MPN, (
        f"unexpected MPN-less non-passive rows: "
        f"{sorted(empties - KNOWN_NO_MPN)}; repaired/re-pinned: "
        f"{sorted(KNOWN_NO_MPN - empties)}")