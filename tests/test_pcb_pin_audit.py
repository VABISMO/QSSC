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
part (PLL loop, SPI, reference decoupling). It also locks the Ironwood SBT
socket footprint facts (4 NPTH mount holes, F.Paste on the 65 solder pads) so a
future regen cannot silently revert the socket.

Skips where ``pcbnew`` (system KiCad) is not importable (CI / .venv).

The expected nets come from the datasheets in ``hardware/datasheets/`` cross-
checked against the generator's pin dicts; where the two disagreed (FT232 pins
23/24, ADF4351 VCOM) the datasheet + generator-comment rationale wins and this
test encodes that. See ``memory/netlist-verified-per-datasheet``.
"""
from __future__ import annotations

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


def test_main_chip_is_ironwood_sbt_socket():
    """Uchip must be the Ironwood SBT socket (not the ACF QSSC_Chip_EM), with
    4 NPTH mount holes and F.Paste on the solder pads (the socket solders down,
    unlike the ACF pressure-contact die footprint)."""
    if not MAIN.exists():
        pytest.skip(f"{MAIN} not present")
    fp = _footprint(MAIN, "Uchip")
    assert fp is not None, "Uchip not found on main board"
    assert fp.GetFPID().GetLibItemName() == "Ironwood_SBT", (
        f"Uchip is {fp.GetFPID().GetLibItemName()}, expected Ironwood_SBT")
    pads = list(fp.Pads())
    npth = [p for p in pads if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH]
    assert len(npth) >= 4, f"Ironwood SBT needs >=4 NPTH mount holes, has {len(npth)}"
    fpaste = [p for p in pads if p.IsOnLayer(pcbnew.F_Paste)]
    assert len(fpaste) >= 65, (
        f"Ironwood SBT solder pads need F.Paste (>=65), has {len(fpaste)}")


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