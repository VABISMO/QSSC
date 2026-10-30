#!/usr/bin/env python3
"""Regenerate ``hardware/datasheets/BOM.csv`` from the ACTUAL components on the
two QSSC sandwich boards (the components emitted by ``gen_qssc_kicad.py``).

The QSSC die is a two-faced (no-TSV) die clamped between TWO PCBs:
  - **main** board: FPGA + 3x TLC5947 emitter + RF + USB + power, die BOTTOM /
    emitter face held in a plain **through-hole 2.54 mm die socket**
    (no paste, die removable), BTB HOST connector J12.
  - **head** board: 16x TLV9004 TIA + 4x AD7616 ADC, die TOP / detector face in
    the matching through-hole `ChipSocket_2.54_DET` (no paste), BTB DAUGHTER
    connector J12. The 9-wire AD7616
    SPI + GND/+3V3/+5V cross the BTB connector; RTL and Qiskit software unchanged.

This builder imports the board generator, populates ``COMPONENTS`` for each
board exactly as the board builds do, groups parts by (Board, value,
footprint), and writes a correct two-board BOM with a Board column.

Run:  python3 tools/gen_bom.py
(Pure-Python: imports gen_qssc_kicad, no kicad-cli / no resource cap needed.)

Columns: Board, Designators, Qty, Value, Footprint, Manufacturer, MPN, DatasheetURL
IC/connector MPNs are filled from a curated map (verified against
DATASHEETS.md); passives (R/C/L/D) are left generic (no MPN) since the value
+ footprint fully specify them for procurement.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import gen_qssc_kicad as g  # noqa: E402

OUT = ROOT / "hardware" / "datasheets" / "BOM.csv"

# NOTE: the BOM-only escape hatch (components in the BOM but deliberately not
# placed) was removed once C87 -- its only user, from audit #99 / issue #169
# item 3 -- graduated into build_main_board with a real PCB site. A BOM row
# without a footprint is now always a defect the BOM<->board deficit gates
# catch, never a pinned state.

# Curated manufacturer/MPN/URL for the ICs + connectors (verified pinouts in
# DATASHEETS.md). Keyed by (value, footprint-basename) so it is robust to the
# exact KiCad footprint-library path prefix.
MPN_MAP: dict[tuple[str, str], tuple[str, str, str]] = {
    # ("value", "footprint contains"): (Manufacturer, MPN, DatasheetURL)
    ("XC7A35T-1CSG324C", "Xilinx_CSG324"):
        ("AMD/Xilinx", "XC7A35T-1CSG324C",
         "https://docs.amd.com/r/en-US/ds181-Artix-7-Data-Sheet"),
    ("ADF4351BCPZ", "QFN-32"):
        # Keyed on the footprint's real name: KiCad calls this land pattern
        # QFN-32-1EP, ADI calls the same package LFCSP -- matching on "LFCSP"
        # matched nothing, so the ADF4351 rows shipped without an MPN even
        # though the part number sits in their own Value column (issue #169).
        ("Analog Devices", "ADF4351BCPZ",
         "https://www.analog.com/media/en/technical-documentation/data-sheets/ADF4351.pdf"),
    ("GVA-84+", "SOT-89"):
        ("Mini-Circuits", "GVA-84+",
         "https://www.minicircuits.com/WebStore/dashboard.html?model=GVA-84%2B"),
    ("W25Q128JVSIQ", "SOIC-8"):
        ("Winbond", "W25Q128JVSIQ",
         "https://www.winbond.com/resource-files/w25q128jv%20revh%2004082019%20plus.pdf"),
    ("FT232RL", "SSOP-28"):
        ("FTDI", "FT232RL",
         "https://www.ftdichip.com/Support/Documents/DataSheets/ICs/DS_FT232R.pdf"),
    ("TLC5947", "HTSSOP-32"):
        ("Texas Instruments", "TLC5947",
         "https://www.ti.com/lit/ds/symlink/tlc5947.pdf"),
    ("TLV9004", "TSSOP-14"):
        ("Texas Instruments", "TLV9004",
         "https://www.ti.com/lit/ds/symlink/tlv9004.pdf"),
    ("AD7616", "LQFP-80"):
        ("Analog Devices", "AD7616BSTZ",
         "https://www.analog.com/media/en/technical-documentation/data-sheets/ad7616.pdf"),
    ("BTB_HOST", "Conn_02x10_BTB"):
        ("Würth Elektronik", "61302011121",
         "https://www.we-online.com/en/components/products/WR-PHD_2_54_MM_SMT_PIN_HEADER_61302011121"),
    ("BTB_DAUGHTER", "Conn_02x10_BTB"):
        ("Würth Elektronik", "61002021121",
         "https://www.we-online.com/en/components/products/WR-PHD_2_54_MM_THT_FEMALE_HEADER_VERTICAL_61002021121"),
    ("AMS1117-3.3", "SOT-223"):
        ("AMS", "AMS1117-3.3",
         "https://www.mouser.com/datasheet/2/260/AMS1117-Datasheet-1273816.pdf"),
    ("AMS1117-1.8", "SOT-223"):
        ("AMS", "AMS1117-1.8",
         "https://www.mouser.com/datasheet/2/260/AMS1117-Datasheet-1273816.pdf"),
    ("AP2127K-1.0", "SOT-23-5"):
        ("Diodes Inc", "AP2127K-1.0",
         "https://www.diodes.com/assets/Datasheets/AP2127K.pdf"),
    ("100MHz XO", "ASE-4Pin"):
        # Was keyed on the value "ASE-100MHz", which no row ever used -- the
        # rows say "100MHz XO" and carry the Abracon ASE land pattern in the
        # footprint, so the oscillator rows stayed MPN-less (issue #169).
        ("Abracon", "ASE-100.000MHZ-LR",
         "https://abracon.com/Oscillators/ASE.pdf"),
    ("USB-C", "USB_C"):
        ("GCT", "USB4105-GF-610-50-B",
         "https://www.gct.cc/connector/usb4105/"),
    ("5V Barrel", "BarrelJack"):
        ("CUI", "PJ-102AH",
         "https://www.cui.com/product/resource/pj-102a.pdf"),
    ("12V LED", "BarrelJack"):
        # J4 carries the identical CUI PJ-102AH land pattern as J2's 5 V
        # barrel: one connector part feeding two rails, so the row must not
        # stay MPN-less (issue #169). Same MPN across two *labels* is fine;
        # the gate that guards this only cares that one MPN never spans two
        # different footprints.
        ("CUI", "PJ-102AH",
         "https://www.cui.com/product/resource/pj-102a.pdf"),
    ("SMA", "SMA_Amphenol"):
        ("Amphenol", "132289",
         "https://www.amphenolrf.com/132289.html"),
    ("RESET", "SW_PUSH"):
        ("Würth", "430182050816", ""),
    ("MW_GATE", "PinHeader"):
        ("Würth", "61300211121", ""),
    ("JTAG", "PinHeader"):
        # 2x05 = 10 contacts: digits 5-6 of the WR-PHD series are the contact
        # count, so a 4-contact MPN on a 10-pad footprint was wrong (issue #169
        # item 1, repaired in BOM.csv by PR #171 -- kept in sync here or a
        # regeneration reverts it).
        ("Würth", "61301011121", ""),
    ("2A PTC", "Fuse_1206"):
        ("Littelfuse", "nanoSMDC020F",
         "https://www.littelfuse.com/media?resourcetype=datasheets&value=0810070"),
    ("600Ω@100MHz", "L_0603"):
        ("Murata", "BLM18AG601SN1D",
         "https://www.murata.com/products/productdata/8796738650146/ENFA0003.pdf"),
    ("ESD5V0", "D_SOD-923"):
        ("ON Semi", "ESD5V0S3B-U",
         "https://www.onsemi.com/pub/Collateral/ESD5V0S3B-U-D.PDF"),
    ("QSSC_Chip_DET", "QSSC_Chip_DET"):
        ("project die", "QSSC-128pad-CQD (detector face)", ""),
    # Gradient-board parts.  These rows used to exist only as hand-typed lines
    # in BOM.csv, which is why regenerating used to drop their manufacturer /
    # MPN / datasheet URL (issue #162).
    #
    # NOTE: repairing an MPN in BOM.csv alone is not enough -- a regeneration
    # re-derives the value from this map, so the fix must land HERE too
    # (issue #169: J9 and Udrv are repaired in BOM.csv but still carry their
    # pre-repair values in MPN_MAP).
    ("IRLML2502", "SOT-23-3"):
        ("Infineon", "IRLML2502TRPBF",
         "https://www.infineon.com/dgdl/irlml2502.pdf"),
    ("LM358", "SOIC-8"):
        # -P is a PDIP-8 suffix on a SOIC-8 footprint; an SOIC build orders
        # -DR (issue #169 item 2, repaired in BOM.csv by PR #171 -- kept in
        # sync here or a regeneration reverts it).
        ("Texas Instruments", "LM358DR",
         "https://www.ti.com/lit/ds/symlink/lm358.pdf"),
    ("MCP4921", "SOIC-8"):
        ("Microchip", "MCP4921-I/SN",
         "https://ww1.microchip.com/downloads/en/DeviceDoc/22248A.pdf"),
}


def _mpn_for(value: str, fp: str) -> tuple[str, str, str]:
    for (v, fpat), info in MPN_MAP.items():
        if value == v and fpat in fp:
            return info
    return ("", "", "")


def _ref_sort_key(ref: str):
    """Natural sort: prefix then numeric index (C2 before C10)."""
    m = re.match(r"([A-Za-z]+)(\d*)", ref)
    pre, num = (m.group(1), m.group(2)) if m else (ref, "")
    return (pre, int(num) if num.isdigit() else -1)


def main() -> None:
    # Populate COMPONENTS for each board exactly as the board build does.
    # The board is identified by its Board label for the BOM column.
    g.build_pincoords()

    board_comps: list[tuple[str, list[dict]]] = []
    # Exactly the two published boards (matching the two tracked .kicad_pcb
    # files). The main board IS the extended build: build_gradient_board()
    # (base main + the field-gradient block) is what gen_qssc_kicad emits as
    # qssc.kicad_pcb (__main__.py emits it via build_gradient_board), so the
    # main board ALWAYS carries the gradient. Emitting the base
    # build_main_board() as a separate 'main' produced a phantom gradient-less
    # board, and labelling the real build a separate 'grad' duplicated every
    # main row under two labels (round-12 fix: main = the complete published
    # board, no third/grad column).
    for label, build_fn in (("main", g.build_gradient_board),
                            ("head", g.build_detector_head)):
        g.COMPONENTS.clear()
        build_fn()
        board_comps.append((label, list(g.COMPONENTS)))
    g.COMPONENTS.clear()

    # Group by (Board, value, footprint) -> list of refs.
    groups: dict[tuple[str, str, str], list[str]] = {}
    for label, comps in board_comps:
        for c in comps:
            # Skip KiCad schematic-only markers (#FLG*/PWR_FLAG): they are
            # power flags, not purchasable parts, and never become a footprint
            # (issue #167).
            if c["ref"].startswith("#"):
                continue
            key = (label, c["value"], c["fp"])
            groups.setdefault(key, []).append(c["ref"])

    # Stable, readable row order: ICs/connectors first by value, then passives
    # by (value, footprint). Within a row, designators sorted naturally.
    def row_sort_key(item):
        (_board, value, fp), refs = item
        # IC/connector-ish footprints (non-R/C/L/D passives) sort first.
        passive = re.match(r"^(R|C|L|D)\d", refs[0])
        return (0 if passive else 1, value, fp)

    rows = []
    for (board, value, fp), refs in sorted(groups.items(), key=row_sort_key):
        refs = sorted(refs, key=_ref_sort_key)
        mfr, mpn, url = _mpn_for(value, fp)
        rows.append({
            "Board": board,
            "Designators": ", ".join(refs),
            "Qty": len(refs),
            "Value": value,
            "Footprint": fp,
            "Manufacturer": mfr,
            "MPN": mpn,
            "DatasheetURL": url,
        })

    fieldnames = ["Board", "Designators", "Qty", "Value", "Footprint",
                  "Manufacturer", "MPN", "DatasheetURL"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    # Summary to stdout.
    n_comp = sum(len(c) for _, c in board_comps)
    n_rows = len(rows)
    print(f"wrote {OUT}")
    print("  " + " | ".join(f"{label}: {len(comps)} comps"
                            for label, comps in board_comps))
    print(f"  {n_comp} components -> {n_rows} distinct BOM line items")
    # sanity: list any leftover two-board parts that must NOT appear.
    forbidden = {"ADG732BCPZ", "ADA4530-1", "74LVC1G04", "74HCT240",
                 "AO3401A", "AO3400A", "Samtec LSHM-130",
                 "LMV339", "MCP4725"}
    leftovers = [r for r in rows if r["Value"] in forbidden
                or "Samtec" in r["Manufacturer"]]
    if leftovers:
        print("  !! LEFTOVER stale parts found:")
        for r in leftovers:
            print(f"     [{r['Board']}] {r['Value']} ({r['Designators']})")
    else:
        print("  no stale leftovers (no ADG732/ADA4530/74HCT240/AO3401A/"
              "LMV339/MCP4725/Samtec) -- clean two-board BOM")


if __name__ == "__main__":
    main()