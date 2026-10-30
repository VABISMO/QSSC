# tests/test_pcb_pin_audit.py
"""Per-datasheet pin-net audit for both QSSC boards -- the test that catches the
class of defect the user reported ("practicamente no hay ningun componente
conectado segun la especificacion de su datasheet"): a board can be
schematic-clean and DRC-clean yet have an IC's VCC/AGND/VTUNE pad left floating
because the generator never netted it. DRC's ``unconnected_items=ignore`` and the
plane-net connectivity skip both hide this; a pad simply left at no-net is not an
"unconnected item" and a plane net is not graph-checked. This test loads each
board and asserts the specific pad -> net assignments the datasheet requires for
every IC's supply, ground, and the few signal pins whose absence breaks the
part (PLL loop, SPI, reference decoupling). It also locks the die-socket
footprint facts (ChipSocket_2.54_EM, 4 NPTH mount holes, no F.Paste --
through-hole, die removable) so a future regen cannot silently revert them.

Skips where ``pcbnew`` (system KiCad) is not importable (CI / .venv).

The expected nets come from the datasheets in ``hardware/datasheets/`` cross-
checked against the generator's pin dicts; where the two disagreed (FT232 pins
23/24, ADF4351 VCOM) the datasheet + generator-comment rationale wins and this
test encodes that. See ``memory/netlist-verified-per-datasheet``.
"""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pcbnew = pytest.importorskip("pcbnew")

REPO = Path(__file__).resolve().parent.parent
MAIN = REPO / "hardware" / "pcb" / "qssc.kicad_pcb"
HEAD = REPO / "hardware" / "pcb" / "chip_module.kicad_pcb"


def _pad_nets(board_path: Path, ref: str) -> dict[str, str]:
    """Return {pad_number: netname} for one footprint (by reference)."""
    b = pcbnew.LoadBoard(str(board_path))
    fp = next((f for f in b.GetFootprints() if f.GetReference() == ref), None)
    if fp is None:
        return {}
    return {p.GetNumber(): p.GetNetname() for p in fp.Pads()}


def _footprint(board_path: Path, ref: str):
    b = pcbnew.LoadBoard(str(board_path))
    return next((f for f in b.GetFootprints() if f.GetReference() == ref), None)


# ---- main board --------------------------------------------------------------
# pad -> required net. Keys not listed are not asserted (modem/CBUS/LED NC pins
# are checked separately for absence-of-net only where the datasheet mandates NC).
FT232 = {
    "1": "UART_RX", "5": "UART_TX", "4": "+3V3",          # VCCIO
    "15": "USB_DP_F", "16": "USB_DM_F", "17": "3V3OUT",
    "19": "+3V3",                                         # RESET# tied high
    "20": "+3V3",                                         # the ONLY VCC pad
    "7": "GND", "18": "GND", "21": "GND", "25": "GND", "26": "GND",
}
FT232_NC = {"23", "24"}   # CBUS/PWREN# and the no-bond NC -- must NOT be a rail

ADF4351 = {
    "4": "+3V3", "6": "+3V3", "10": "+3V3", "16": "+3V3", "17": "+3V3",
    "26": "+3V3", "28": "+3V3", "32": "+3V3",             # 8x AVCC/VP
    "8": "GND", "9": "GND", "11": "GND", "18": "GND", "21": "GND",
    "27": "GND", "31": "GND", "33": "GND",                # EP = pad "33"
    "20": "LF_OUT",       # VTUNE <- loop filter: closes the PLL (open => VCO drift)
    "19": "ADF_TEMP",     # TEMP output (decoupled, NOT no-connect)
    "23": "ADF_VCOM",     # VCOM: decoupled to GND via C41, NOT a direct GND tie
    "24": "VREF",         # internal ref node: decoupled via C36, NOT a direct tie
    "29": "refclk_25m",   # REFIN (single-ended; ADF4351 has NO REFIN-)
    "12": "RF_OUT", "22": "RSET", "7": "LF_CP",
}
ADF4351_NC = {"5", "13", "14", "15", "25"}   # unused diff outputs / power-down

W25Q128 = {"8": "+3V3", "4": "GND", "1": "SPI_CS", "6": "SPI_CCLK",
           "2": "SPI_D1", "3": "SPI_D2", "5": "SPI_D0", "7": "SPI_D3"}


@pytest.mark.parametrize("ref,expected", [
    ("J1", FT232), ("U3", ADF4351), ("U8", W25Q128),
])
def test_main_pin_nets(ref, expected):
    if not MAIN.exists():
        pytest.skip(f"{MAIN} not present")
    nets = _pad_nets(MAIN, ref)
    assert nets, f"{ref} not found on main board"
    bad = {n: nets.get(n, "<missing>") for n, want in expected.items()
           if nets.get(n) != want}
    assert not bad, f"main {ref} per-datasheet pin-net mismatches: {bad}"


def test_ft232_nc_pads_have_no_rail():
    """FT232 pin 23 (CBUS/PWREN#) and 24 (no-bond NC) must NOT be tied to a
    power/ground rail -- a prior doc draft wrongly called 24 a 2nd VCC. The
    generator leaves them no-net (or NC); assert they are not +3V3/GND."""
    if not MAIN.exists():
        pytest.skip(f"{MAIN} not present")
    nets = _pad_nets(MAIN, "J1")
    rails = {"+3V3", "GND", "+5V", "+1V8"}
    for n in FT232_NC:
        assert nets.get(n, "") not in rails, (
            f"FT232 pin {n} is on rail {nets.get(n)} -- should be NC per datasheet")


def test_adf4351_nc_pads_have_no_rail():
    if not MAIN.exists():
        pytest.skip(f"{MAIN} not present")
    nets = _pad_nets(MAIN, "U3")
    rails = {"+3V3", "GND"}
    for n in ADF4351_NC:
        assert nets.get(n, "") not in rails, (
            f"ADF4351 pin {n} is on rail {nets.get(n)} -- unused diff out, should be NC")


# ---- gradient (SET_GRADIENT) hardware, on the PUBLISHED main board ----------
# The main board IS the extended build: MCP4921 DAC (Ugrad) driven by the
# FPGA's 3-wire SPI from the south-row balls, LM358 servo (Udrv), MOSFET
# (Qgrad) + sense (Rsense) into the Lgrad coil trace.  This is the exact gap
# issue #172 covered: every verb the software exposes must have its hardware
# on one of the two published boards.
GRAD_PAD_NETS: dict[str, dict[str, str]] = {
    # FPGA SPI balls (grad_en V7 deliberately absent: spare STATUS ball, NC)
    "U1": {"V6": "grad_cs", "V9": "grad_sclk", "V12": "grad_mosi", "V7": ""},
    # MCP4921: VDD(1,6)=+3V3 (VREF(6) tied to VDD -- #168-C level repair),
    # AVSS(5,7)=GND, SPI(2-4), VOUTA(8)
    "Ugrad": {"1": "+3V3", "2": "grad_cs", "3": "grad_sclk", "4": "grad_mosi",
              "5": "GND", "6": "+3V3", "7": "GND", "8": "GRAD_DAC_OUT"},
    # LM358: amp A servo INA+/INA-/OUTA, amp B parked (INB+=GND, INB-=OUTB),
    # V-(4)=GND V+(8)=+5V
    "Udrv": {"1": "GRAD_GATE", "2": "GRAD_SENSE", "3": "GRAD_DAC_OUT",
             "4": "GND", "5": "GND", "6": "GRAD_NFB", "7": "GRAD_NFB",
             "8": "+5V"},
    "Qgrad": {"1": "GRAD_GATE", "2": "GRAD_SENSE", "3": "GRAD_COIL_N"},
    "Rgrad_feed": {"1": "+5V", "2": "GRAD_COIL_P"},
    "Lgrad": {"1": "GRAD_COIL_P", "2": "GRAD_COIL_N"},
    "Rsense": {"1": "GRAD_SENSE", "2": "GND"},
    "C_grad1": {"1": "+3V3", "2": "GND"},
    "C_grad2": {"1": "+3V3", "2": "GND"},
}


def test_main_gradient_pad_nets():
    """Every gradient pad on the published main must carry its design net
    (pad-level wiring; DRC alone cannot catch a pad left at no-net)."""
    if not MAIN.exists():
        pytest.skip(f"{MAIN} not present")
    for ref, expected in GRAD_PAD_NETS.items():
        nets = _pad_nets(MAIN, ref)
        assert nets, f"{ref} (gradient block) not found on main board"
        bad = {n: nets.get(n, "<missing>") for n, want in expected.items()
               if nets.get(n) != want}
        assert not bad, f"main {ref} gradient pin-net mismatches: {bad}"


# Exact schematic node sets for the gradient chain (netlist export names the
# sheet-local nets with a leading "/").
GRAD_SCH_NETS: dict[str, set[tuple[str, str]]] = {
    "/grad_cs": {("U1", "V6"), ("Ugrad", "2")},
    "/grad_sclk": {("U1", "V9"), ("Ugrad", "3")},
    "/grad_mosi": {("U1", "V12"), ("Ugrad", "4")},
    "/GRAD_DAC_OUT": {("Ugrad", "8"), ("Udrv", "3")},
    "/GRAD_GATE": {("Udrv", "1"), ("Qgrad", "1")},
    "/GRAD_SENSE": {("Udrv", "2"), ("Qgrad", "2"), ("Rsense", "1")},
    "/GRAD_NFB": {("Udrv", "6"), ("Udrv", "7")},
    "/GRAD_COIL_P": {("Rgrad_feed", "2"), ("Lgrad", "1")},
    "/GRAD_COIL_N": {("Lgrad", "2"), ("Qgrad", "3")},
}


def test_main_gradient_sch_netlist(tmp_path):
    """Schematic source of truth: each gradient net joins EXACTLY its design
    nodes (FPGA ball -> DAC -> servo -> coil).  The pad audit above proves
    the shipped PCB; this proves the sheet the next regen regenerates from --
    the original defect was single-node nets (Ugrad pins labelled, FPGA balls
    not), which DRC never flags because a one-pad net has nothing to connect."""
    sch = MAIN.with_suffix(".kicad_sch")
    if not sch.exists():
        pytest.skip(f"{sch} not present")
    out = tmp_path / "grad_netlist.xml"
    res = subprocess.run(
        ["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
         "-o", str(out), str(sch)],
        capture_output=True, text=True, check=False, timeout=600,
    )
    assert res.returncode == 0, f"kicad-cli netlist export failed:\n{res.stderr}"
    root = ET.parse(out).getroot()
    nets: dict[str, set[tuple[str, str]]] = {}
    for n in root.find("nets"):
        nets[n.get("name")] = {(x.get("ref"), x.get("pin"))
                               for x in n.findall("node")}
    for name, want in GRAD_SCH_NETS.items():
        got = nets.get(name)
        assert got == want, (
            f"sch net {name}: {sorted(got or [])} != {sorted(want)}"
        )
    grad_en = sorted(nm for nm in nets if "grad_en" in nm.lower())
    assert not grad_en, f"grad_en must stay NC (no net), found: {grad_en}"


def test_main_chip_is_female_header_socket():
    """Uchip must be the 2.54 mm female-header chip socket (ChipSocket_2.54_EM).

    Through-hole part: 4 NPTH mount holes and 65 solder pads with no F.Paste
    -- the pins solder through the board, and the die stays removable (ADR-0009).
    """
    if not MAIN.exists():
        pytest.skip(f"{MAIN} not present")
    fp = _footprint(MAIN, "Uchip")
    assert fp is not None, "Uchip not found on main board"
    assert fp.GetFPID().GetLibItemName() == "ChipSocket_2.54_EM", (
        f"Uchip is {fp.GetFPID().GetLibItemName()}, expected ChipSocket_2.54_EM")
    pads = list(fp.Pads())
    npth = [p for p in pads if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH]
    assert len(npth) >= 4, f"chip socket needs >=4 NPTH mount holes, has {len(npth)}"
    # Through-hole solder pads (PTH), not paste: the female-header pins solder
    # through the board, so there is no F.Paste on this footprint.
    pth = [p for p in pads
           if p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH
           and not p.IsOnLayer(pcbnew.F_Paste)]
    assert len(pth) >= 65, f"chip socket needs >=65 through-hole pads, has {len(pth)}"
    fpaste = [p for p in pads if p.IsOnLayer(pcbnew.F_Paste)]
    assert fpaste == [], (
        f"female-header socket is through-hole, expected no F.Paste, has {len(fpaste)}")


# ---- head board --------------------------------------------------------------
AD7616_VCC = {"6", "15", "30", "71"}      # VCC (4 pads) on +5V_A
AD7616_GND = {"5", "16", "29", "72", "50", "69", "51", "32", "34"}
AD7616_NC = {"41", "42", "43", "44", "47", "48", "53", "54", "55", "56"}


def test_head_ad7616_supply_ground():
    if not HEAD.exists():
        pytest.skip(f"{HEAD} not present")
    for j in range(4):
        nets = _pad_nets(HEAD, f"UAD{j}")
        assert nets, f"UAD{j} not found on head board"
        for v in AD7616_VCC:
            assert nets.get(v) == "+5V_A", (
                f"UAD{j} VCC pin {v} -> {nets.get(v)} (want +5V_A)")
        assert nets.get("49") == "+3V3", (
            f"UAD{j} VDRIVE pin 49 -> {nets.get('49')} (want +3V3)")
        for g in AD7616_GND:
            assert nets.get(g) == "GND", (
                f"UAD{j} GND pin {g} -> {nets.get(g)} (want GND)")
        # serial-mode config ties that, if wrong, silently break the ADC:
        assert nets.get("40") == "+3V3", "AD7616 SER/PAR must be HIGH (serial mode)"
        assert nets.get("45") == "GND", "AD7616 SER1W must be LOW (1-wire)"
        assert nets.get("35") == "GND", "AD7616 REFSEL must be LOW (internal ref)"
        # SPI bus shared + per-chip CS:
        assert nets.get("68") == "adc_convst"
        assert nets.get("62") == "adc_sclk"
        assert nets.get("57") == "adc_miso"
        assert nets.get("36") == "adc_resetn"
        assert nets.get("63") == f"adc_cs{j}", (
            f"UAD{j} CS pin 63 -> {nets.get('63')} (want adc_cs{j})")
        # the parallel DB bus is inert in serial mode -> NC (no rail)
        rails = {"+3V3", "GND", "+5V_A"}
        for n in AD7616_NC:
            assert nets.get(n, "") not in rails, (
                f"UAD{j} DB/SDI pin {n} on rail {nets.get(n)} -- should be NC")


def test_head_tlv9004_supply_ground():
    if not HEAD.exists():
        pytest.skip(f"{HEAD} not present")
    for j in range(16):
        nets = _pad_nets(HEAD, f"UT{j}")
        assert nets, f"UT{j} not found on head board"
        assert nets.get("4") == "+3V3", f"UT{j} V+ (pin4) -> {nets.get('4')}"
        assert nets.get("11") == "GND", f"UT{j} V- (pin11) -> {nets.get('11')}"


# ---------------------------------------------------------------------------
# FULL schematic <-> board net sync.
# The other gates pin refdes sets and a few hand-chosen nets, but nothing
# asserted that EVERY exported netlist node lands on a board pad carrying the
# SAME net (and vice versa) -- a pad bound to the wrong net, consistently
# enough to dodge DRC's short/unconnected checks, was invisible. Audited
# clean by hand during the round-2 audit (2026-10); this pins it forever.
def test_sch_pcb_net_sync(tmp_path):
    if not MAIN.exists():
        pytest.skip(f"{MAIN} not present")
    sch = MAIN.with_suffix(".kicad_sch")
    out = tmp_path / "sync_netlist.xml"
    res = subprocess.run(
        ["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
         "-o", str(out), str(sch)],
        capture_output=True, text=True, check=False, timeout=600,
    )
    assert res.returncode == 0, f"kicad-cli netlist export failed:\n{res.stderr}"

    # (ref, pin) -> net. KiCad's synthetic `unconnected-(...)` names mean the
    # sheet deliberately leaves the pin floating -> normalise to "" (a netless
    # board pad, which is what the pcb carries for those).
    sch_map: dict[tuple[str, str], str] = {}
    for n in ET.parse(out).getroot().find("nets"):
        name = (n.get("name") or "").lstrip("/")
        if name.startswith("unconnected-("):
            name = ""
        for x in n.findall("node"):
            sch_map[(x.get("ref"), x.get("pin"))] = name

    board = pcbnew.LoadBoard(str(MAIN))
    pcb_map: dict[tuple[str, str], str] = {}
    for fp in board.GetFootprints():
        for p in fp.Pads():
            pcb_map[(fp.GetReference(), p.GetNumber())] = p.GetNetname()

    sch_refs = {k[0] for k in sch_map}
    pcb_refs = {k[0] for k in pcb_map}
    assert sch_refs == pcb_refs, (
        f"refdes drift sch vs pcb: only-sch={sorted(sch_refs - pcb_refs)}, "
        f"only-pcb={sorted(pcb_refs - sch_refs)}")

    # sheet -> board: every REAL net node sits on a same-net pad
    wrong = []
    for (ref, pin), snet in sorted(sch_map.items()):
        if not snet or ref.startswith("#") or ref not in pcb_refs:
            continue                     # floating pins / PWR_FLAG markers
        pnet = pcb_map.get((ref, pin), "<no pad>")
        if pnet != snet:
            wrong.append((ref, pin, snet, pnet))
    assert not wrong, (
        f"sch->pcb net mismatches ({len(wrong)}): {wrong[:12]}")

    # board -> sheet: every netted pad is declared on the sheet, same net
    missing = []
    for (ref, pin), pnet in sorted(pcb_map.items()):
        if not pnet or ref.startswith("#") or ref not in sch_refs:
            continue                     # netless pads / footprint-only items
        snet = sch_map.get((ref, pin))
        if snet != pnet:
            missing.append((ref, pin, pnet, snet))
    assert not missing, (
        f"pcb->sch net mismatches ({len(missing)}): {missing[:12]}")