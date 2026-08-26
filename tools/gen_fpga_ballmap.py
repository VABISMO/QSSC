#!/usr/bin/env python3
"""Authoritative XC7A35T-1CPG236C ball->net map, parsed from the real KiCad
symbol ``FPGA_Xilinx_Artix7:XC7A35T-CPG236`` (238 pads, DS181/UG475).

Emits ``.tmpwork/fpga_cpg236_map.json`` consumed by ``gen_qssc_kicad.py``::

  { signals: {net: ball}     # 32 user-IO assignments (clk on a MRCC pin)
    power:   {rail: [balls]} # VCCINT/VCCBRAM/VCCAUX/VCCADC/GNDADC/VCCBATT/
                             # CFGBVS/VREFP/VREFN/GND/MGT_GND/VCCO_<bank>
    config:  {name: ball}    # D00-D03/FCS_B/CCLK/DONE/INIT_B/PROGRAM_B/M0-M2
    jtag:    {name: ball}    # TCK/TMS/TDI/TDO
    xadc:    {name: ball} }  # VP/VN (VauxP0/VauxN0); DXP/DXN left NC

The power dict is derived directly from the symbol pin names so it can never
silently drop a ground/supply ball (the prior hand-built JSON grounded only 10
of the 22 MGT_GND balls, leaving 12 floating -- DRC-clean but electrically
wrong). The signals/config/jtag/xadc sections are design choices preserved from
the existing JSON (all verified to land on real IO / dedicated pins).
"""
from __future__ import annotations
import re, json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYM = Path("/usr/share/kicad/symbols/FPGA_Xilinx_Artix7.kicad_sym")
OUT = ROOT / ".tmpwork" / "fpga_cpg236_map.json"


def parse_pins() -> dict[str, str]:
    sym = SYM.read_text()
    i = sym.find('(symbol "XC7A35T-CPG236"')
    assert i != -1, "XC7A35T-CPG236 symbol not found"
    depth, j = 0, i
    while j < len(sym):
        c = sym[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    blk = sym[i:j + 1]
    pins: dict[str, str] = {}
    last = None
    for tok in re.finditer(r'\(name\s+"([^"]+)"|\(number\s+"([^"]+)"', blk):
        if tok.group(1):
            last = tok.group(1)
        else:
            pins[tok.group(2)] = last
    assert len(pins) == 238, f"expected 238 pads, parsed {len(pins)}"
    return pins


def classify_power(pins: dict[str, str]) -> dict[str, list[str]]:
    power = defaultdict(list)
    for b, n in pins.items():
        if n.startswith("VCCO_"):
            power[n].append(b)
        elif n.startswith("VCCINT"):
            power["VCCINT"].append(b)
        elif n.startswith("VCCBRAM"):
            power["VCCBRAM"].append(b)
        elif n.startswith("VCCAUX"):
            power["VCCAUX"].append(b)
        elif n.startswith("VCCADC"):
            power["VCCADC"].append(b)
        elif n.startswith("GNDADC"):
            power["GNDADC"].append(b)
        elif n == "VCCBATT_0":
            power["VCCBATT"].append(b)
        elif n == "CFGBVS_0":
            power["CFGBVS"].append(b)
        elif n.startswith("MGT"):   # MGTGND_* (no MGT supplies on XC7A35T CPG236)
            power["MGT_GND"].append(b)
        elif n.startswith("GND"):
            power["GND"].append(b)
        elif n.startswith("VREFP"):
            power["VREFP"].append(b)
        elif n.startswith("VREFN"):
            power["VREFN"].append(b)
    return {k: sorted(v) for k, v in sorted(power.items())}


def main() -> None:
    pins = parse_pins()
    power = classify_power(pins)
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    new = {
        "signals": old.get("signals", {}),
        "power":   power,
        "config":  old.get("config", {}),
        "jtag":    old.get("jtag", {}),
        "xadc":    old.get("xadc", {}),
        "source":  "tools/gen_fpga_ballmap.py from "
                   "FPGA_Xilinx_Artix7:XC7A35T-CPG236 (238 pads)",
    }
    OUT.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n")
    n_pow = sum(len(v) for v in power.values())
    n_sig = len(new["signals"])
    print(f"wrote {OUT}")
    print(f"  power balls: {n_pow} (GND={len(power['GND'])} "
          f"MGT_GND={len(power['MGT_GND'])} "
          f"VCCINT={len(power['VCCINT'])} "
          f"VCCO banks={sum(1 for k in power if k.startswith('VCCO_'))})")
    print(f"  signals: {n_sig}  config: {len(new['config'])} "
          f"jtag: {len(new['jtag'])}  xadc: {len(new['xadc'])}")
    print(f"  total netted: {n_sig + n_pow + len(new['config']) + len(new['jtag']) + len(new['xadc'])} / 238")


if __name__ == "__main__":
    main()