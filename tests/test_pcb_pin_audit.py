# tests/test_pcb_pin_audit.py
"""Per-datasheet pin-audit gate for the main board (the user's
"practically nothing is connected per its datasheet" complaint).

Verifies, against the committed artefacts, the concrete per-datasheet fixes:

  * The die sits in the Ironwood SBT socket (footprint value ``Ironwood_SBT``,
    NOT the bare-die ``QSSC_Chip_EM``): 65 solder pads (64 EM + EM_ANODE) on
    F.Cu WITH F.Paste (the socket solders down) + 4 NPTH M2 mount holes.
  * FT232RL pin 24 -> +3V3 (VCC) and pin 23 -> GND (AGND) -- both required
    supply/ground pads per the SSOP-28 datasheet that were previously unnetted.
  * ADF4351 REFIN- (pin 19) is netted to a decoupling net (single-ended CMOS ref,
    datasheet Fig.51); the unused RF outputs RFOUTB+/- (13,14), RFOUTA- (15) and
    CE (25) are deliberately unnetted (left floating per datasheet).
  * The schematics carry ``(no_connect)`` markers for the documented-unused
    pins (FT232 modem/crystal/LED, ADF4351 unused RF, AD7616 parallel-DB +
    SDI/SDOB, TLC5947 chip2 OUT16-23 + SOUT).

Skips where ``pcbnew`` (system KiCad) is not importable.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pcbnew = pytest.importorskip("pcbnew")

REPO = Path(__file__).resolve().parent.parent
MAIN_PCB = REPO / "hardware" / "pcb" / "qssc.kicad_pcb"
MAIN_SCH = REPO / "hardware" / "pcb" / "qssc.kicad_sch"
HEAD_SCH = REPO / "hardware" / "pcb" / "chip_module.kicad_sch"


def _fp_by_ref(board, ref):
    return next((f for f in board.GetFootprints() if f.GetReference() == ref), None)


def test_ironwood_socket_footprint():
    """Uchip is the Ironwood SBT socket, not the bare die; 65 solder pads on
    F.Cu + F.Paste, plus 4 NPTH mount holes."""
    b = pcbnew.LoadBoard(str(MAIN_PCB))
    fp = _fp_by_ref(b, "Uchip")
    assert fp is not None, "Uchip not found on main board"
    assert fp.GetValue() == "Ironwood_SBT", (
        f"Uchip value is {fp.GetValue()!r}, expected 'Ironwood_SBT' "
        f"(the Ironwood SBT socket, not the bare QSSC_Chip_EM die)")

    pads = list(fp.Pads())
    # 65 solder pads (64 EM cathodes + EM_ANODE) + 4 NPTH mount holes = 69 total
    solder = [p for p in pads if p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH]
    npth = [p for p in pads if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH]
    assert len(solder) == 65, f"expected 65 socket solder pads, got {len(solder)}"
    assert len(npth) == 4, f"expected 4 NPTH mount holes, got {len(npth)}"

    f_cu = pcbnew.F_Cu
    f_mask = pcbnew.F_Mask
    f_paste = pcbnew.F_Paste
    for p in solder:
        layers = p.GetLayerSet()
        assert layers.Contains(f_cu), f"socket pad {p.GetPadName()} not on F.Cu"
        assert layers.Contains(f_paste), (
            f"socket pad {p.GetPadName()} missing F.Paste (socket solders down, "
            f"unlike the ACF pressure-contact die face)")
        assert layers.Contains(f_mask), f"socket pad {p.GetPadName()} missing F.Mask"


def test_ft232_supply_pins_netted():
    """FT232RL pin 24 (VCC) -> +3V3 and pin 23 (AGND) -> GND per SSOP-28."""
    b = pcbnew.LoadBoard(str(MAIN_PCB))
    j1 = _fp_by_ref(b, "J1")
    assert j1 is not None, "J1 (FT232RL) not found"
    by_name = {p.GetPadName(): p.GetNetname() for p in j1.Pads()}
    assert by_name.get("24") == "+3V3", (
        f"FT232 pin 24 (VCC) net is {by_name.get('24')!r}, expected '+3V3'")
    assert by_name.get("23") == "GND", (
        f"FT232 pin 23 (AGND) net is {by_name.get('23')!r}, expected 'GND'")


def test_adf4351_temp_decoupled_and_unused_rfon():
    """ADF4351 TEMP (pin 19, temperature-comp output) is decoupled to GND
    (netted, per datasheet Table 5 -- the ADF4351 has NO REFIN- pin); unused RF
    outs 13/14/15 and LD 25 are deliberately left floating (unnetted)."""
    b = pcbnew.LoadBoard(str(MAIN_PCB))
    u3 = _fp_by_ref(b, "U3")
    assert u3 is not None, "U3 (ADF4351) not found"
    by_name = {p.GetPadName(): p.GetNetname() for p in u3.Pads()}
    assert by_name.get("19", "") == "ADF_TEMP", (
        "ADF4351 pin 19 (TEMP) must be netted to ADF_TEMP and decoupled to GND "
        f"via C37 per datasheet Table 5 (NOT floating, NOT REFIN- -- the ADF4351 "
        f"reference is single-ended on REFIN pin 29); net is {by_name.get('19')!r}")
    for pin in ("13", "14", "15", "25"):
        assert by_name.get(pin, "") == "", (
            f"ADF4351 pin {pin} should be left floating (no_connect) per "
            f"datasheet, net is {by_name.get(pin)!r}")


def test_no_connect_markers_present():
    """The schematics carry (no_connect) sexps for documented-unused pins."""
    main_nc = len(re.findall(r"\(no_connect\b", MAIN_SCH.read_text()))
    head_nc = len(re.findall(r"\(no_connect\b", HEAD_SCH.read_text()))
    # main: FT232(12) + ADF4351(5) + TLC5947 chip2(9) = 26
    assert main_nc >= 26, (
        f"main schematic has {main_nc} no_connect markers, expected >= 26")
    # head: 4x AD7616 x10 (parallel-DB + SDI/SDOB) = 40
    assert head_nc >= 40, (
        f"head schematic has {head_nc} no_connect markers, expected >= 40")