#!/usr/bin/env python3
"""Regenerate ``hardware/datasheets/BOM.csv`` from the ACTUAL components on the
single merged QSSC board (the components emitted by ``gen_qssc_kicad.py``).

The committed BOM was a stale two-board artefact (Samtec J11/J1, ADG732 MUX,
ADA4530 TIA, 74LVC1G04, 74HCT240, AO3401A P-MOS, a "chip/main" Board column).
After the single-board parallel pivot + TLC5947 emitter redesign it no longer
matched the board at all. This builder imports the board generator, populates
``COMPONENTS`` exactly as the board build does, groups parts by
(value, footprint), and writes a correct single-board BOM with no leftovers.

Run:  python3 tools/gen_bom.py
(Pure-Python: imports gen_qssc_kicad, no kicad-cli / no resource cap needed.)

Columns: Designators, Qty, Value, Footprint, Manufacturer, MPN, DatasheetURL
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

# Curated manufacturer/MPN/URL for the ICs + connectors (verified pinouts in
# DATASHEETS.md). Keyed by (value, footprint-basename) so it is robust to the
# exact KiCad footprint-library path prefix.
MPN_MAP: dict[tuple[str, str], tuple[str, str, str]] = {
    # ("value", "footprint contains"): (Manufacturer, MPN, DatasheetURL)
    ("XC7A35T-1CSG324C", "Xilinx_CSG324"):
        ("AMD/Xilinx", "XC7A35T-1CSG324C",
         "https://docs.amd.com/r/en-US/ds181-Artix-7-Data-Sheet"),
    ("ADF4351BCPZ", "LFCSP"):
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
    ("TLV9004", "SOIC-14"):
        ("Texas Instruments", "TLV9004",
         "https://www.ti.com/lit/ds/symlink/tlv9004.pdf"),
    ("LMV339", "TSSOP-14"):
        ("Texas Instruments", "LMV339",
         "https://www.ti.com/lit/ds/symlink/lmv339.pdf"),
    ("MCP4725", "SOT-23-6"):
        ("Microchip", "MCP4725",
         "https://ww1.microchip.com/downloads/en/devicedoc/22060d.pdf"),
    ("AMS1117-3.3", "SOT-223"):
        ("AMS", "AMS1117-3.3",
         "https://www.mouser.com/datasheet/2/260/AMS1117-Datasheet-1273816.pdf"),
    ("AMS1117-1.8", "SOT-223"):
        ("AMS", "AMS1117-1.8",
         "https://www.mouser.com/datasheet/2/260/AMS1117-Datasheet-1273816.pdf"),
    ("AP2127K-1.0", "SOT-23-5"):
        ("Diodes Inc", "AP2127K-1.0",
         "https://www.diodes.com/assets/Datasheets/AP2127K.pdf"),
    ("ASE-100MHz", "Oscillator"):
        ("Abracon", "ASE-100.000MHZ-LR",
         "https://abracon.com/Oscillators/ASE.pdf"),
    ("USB-C", "USB_C"):
        ("GCT", "USB4105-GF-610-50-B",
         "https://www.gct.cc/connector/usb4105/"),
    ("5V Barrel", "BarrelJack"):
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
        ("Würth", "61300411121", ""),
    ("2A PTC", "Fuse_1206"):
        ("Littelfuse", "nanoSMDC020F",
         "https://www.littelfuse.com/media?resourcetype=datasheets&value=0810070"),
    ("600Ω@100MHz", "L_0603"):
        ("Murata", "BLM18AG601SN1D",
         "https://www.murata.com/products/productdata/8796738650146/ENFA0003.pdf"),
    ("ESD5V0", "D_SOD-923"):
        ("ON Semi", "ESD5V0S3B-U",
         "https://www.onsemi.com/pub/Collateral/ESD5V0S3B-U-D.PDF"),
    ("QSSC_Chip", "QSSC_Chip"):
        ("project die", "QSSC-128pad-CQD", ""),
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
    # Populate COMPONENTS exactly as the board build does.
    g.build_pincoords()
    g.assign_pcb_positions()
    g.assign_sch_positions()

    comps = g.COMPONENTS
    # Group by (value, footprint) -> list of refs.
    groups: dict[tuple[str, str], list[str]] = {}
    for c in comps:
        key = (c["value"], c["fp"])
        groups.setdefault(key, []).append(c["ref"])

    # Stable, readable row order: ICs/connectors first by value, then passives
    # by (value, footprint). Within a row, designators sorted naturally.
    def row_sort_key(item):
        (value, fp), refs = item
        # IC/connector-ish footprints (non-R/C/L/D passives) sort first.
        passive = re.match(r"^(R|C|L|D)\d", refs[0])
        return (0 if passive else 1, value, fp)

    rows = []
    for (value, fp), refs in sorted(groups.items(), key=row_sort_key):
        refs = sorted(refs, key=_ref_sort_key)
        mfr, mpn, url = _mpn_for(value, fp)
        rows.append({
            "Designators": ", ".join(refs),
            "Qty": len(refs),
            "Value": value,
            "Footprint": fp,
            "Manufacturer": mfr,
            "MPN": mpn,
            "DatasheetURL": url,
        })

    fieldnames = ["Designators", "Qty", "Value", "Footprint",
                  "Manufacturer", "MPN", "DatasheetURL"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    # Summary to stdout.
    n_comp = len(comps)
    n_rows = len(rows)
    print(f"wrote {OUT}")
    print(f"  {n_comp} components -> {n_rows} distinct BOM line items")
    # sanity: list any leftover two-board parts that must NOT appear.
    forbidden = {"ADG732BCPZ", "ADA4530-1", "74LVC1G04", "74HCT240",
                 "AO3401A", "AO3400A", "Samtec LSHM-130"}
    leftovers = [r for r in rows if r["Value"] in forbidden
                 or "Samtec" in r["Manufacturer"]]
    if leftovers:
        print("  !! LEFTOVER two-board parts found:")
        for r in leftovers:
            print(f"     {r['Value']} ({r['Designators']})")
    else:
        print("  no two-board leftovers (no ADG732/ADA4530/74HCT240/"
              "AO3401A/Samtec) -- clean single-board BOM")


if __name__ == "__main__":
    main()