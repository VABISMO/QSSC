#!/usr/bin/env python3
"""Authoritative XC7A35T-1CSG324C ball->net map, parsed from the real KiCad
symbol ``FPGA_Xilinx_Artix7:XC7A35T-CSG324`` (324 pads, DS181/UG475).

Supersedes ``gen_fpga_ballmap.py`` (CPG236, 238 pads) — the parallel 64-ch
readout + 64-ch emitter drive needs 128 user IO (64 DET_DIG inputs + 64
EM_DRV outputs) plus ~13 control nets ≈ 141 user IO, which exceeds the
CPG236's ~125.  CSG324 has 215 user IO.

Emits ``.tmpwork/fpga_csg324_map.json`` consumed by ``gen_qssc_kicad.py``::

  { signals: {net: ball}     # control + 64 DET_DIG + 4 em_SPI (side-assigned)
    power:   {rail: [balls]} # derived from symbol pin names (never drops a GND)
    config:  {name: ball}    # D00-D03/FCS_B/CCLK/DONE/INIT_B/PROGRAM_B/M0-M2
    jtag:    {name: ball}    # TCK/TMS/TDI/TDO
    xadc:    {name: ball} }  # VP/VN (kept NC-netted; XADC removed by the pivot)

Assignment (deterministic, routing-friendly):
  * clk_100mhz (in)  + refclk_25m (out, =refclk_out) -> MRCC clock-capable balls
  * 64 DET_DIG0-63   -> right-edge balls (high column) toward the comparator
                        front-end (above the die, board y<44)
  * 4 em_SPI         -> bottom-edge balls toward the 3x TLC5947 constant-current
                        LED driver below the die (em_sclk/sin/xlat/blank)
  * control nets     -> remaining general IO
The old det_mux_sel*/emitter_rows*/emitter_cols* signals AND the 64x EM_DRV
GPIO are gone (the parallel architecture has no MUX, no row/col matrix, and the
TLC5947 drives the 64 emitters over 4 SPI wires, not 64 GPIO).
"""
from __future__ import annotations
import re, json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYM = Path("/usr/share/kicad/symbols/FPGA_Xilinx_Artix7.kicad_sym")
FP = Path("/usr/share/kicad/footprints/Package_BGA.pretty/Xilinx_CSG324.kicad_mod")
OUT = ROOT / ".tmpwork" / "fpga_csg324_map.json"


def parse_pins() -> dict[str, str]:
    """{ball: pin_name} for the XC7A35T-CSG324 symbol (324 pads)."""
    sym = SYM.read_text()
    i = sym.find('(symbol "XC7A35T-CSG324"')
    assert i != -1, "XC7A35T-CSG324 symbol not found"
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
    assert len(pins) == 324, f"expected 324 pads, parsed {len(pins)}"
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
        elif n.startswith("MGT"):
            power["MGT_GND"].append(b)
        elif n.startswith("GND"):
            power["GND"].append(b)
        elif n.startswith("VREFP"):
            power["VREFP"].append(b)
        elif n.startswith("VREFN"):
            power["VREFN"].append(b)
        elif n.startswith("VREF_"):
            power["VREF"].append(b)
    return {k: sorted(v) for k, v in sorted(power.items())}


def ball_xy() -> dict[str, tuple[float, float]]:
    """{ball: (x,y)} from the CSG324 footprint (mm, body-centred)."""
    fp = FP.read_text()
    pads = {}
    for m in re.finditer(
        r'\(pad\s+"([^"]+)"\s+smd\s+\w+\s+\(at\s+([-\d.]+)\s+([-\d.]+)\)', fp):
        pads[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    assert len(pads) == 324, f"footprint pads {len(pads)} != 324"
    return pads


# ---- dedicated / dual-purpose pin name -> logical config/jtag name ----
# Match the CSG324 symbol's actual pin names (DS181).  These balls are
# reserved for configuration / JTAG / XADC and may NOT be user IO.
DEDICATED = {
    # config data (QSPI flash) — dual-purpose IO used for config, reserved
    "IO_L1P_T0_D00_MOSI_14": "D00",
    "IO_L1N_T0_D01_DIN_14": "D01",
    "IO_L2P_T0_D02_14": "D02",
    "IO_L2N_T0_D03_14": "D03",
    "IO_L6P_T0_FCS_B_14": "FCS_B",
    "CCLK_0": "CCLK",
    "DONE_0": "DONE",
    "INIT_B_0": "INIT_B",
    "PROGRAM_B_0": "PROGRAM_B",
    "M0_0": "M0",
    "M1_0": "M1",
    "M2_0": "M2",
    # JTAG
    "TCK_0": "TCK",
    "TDI_0": "TDI",
    "TMS_0": "TMS",
    "TDO_0": "TDO",
    # XADC (kept NC; the pivot removed the analog chain)
    "VP_0": "VP",
    "VN_0": "VN",
}


def is_power_name(n: str) -> bool:
    return (n.startswith("VCC") or n.startswith("GND") or n.startswith("MGT")
            or n.startswith("VREF") or n == "CFGBVS_0" or n == "VCCBATT_0")


def main() -> None:
    pins = parse_pins()
    power = classify_power(pins)
    xy = ball_xy()

    # 1. config / jtag / xadc — from the dedicated map above.
    config, jtag, xadc = {}, {}, {}
    for ball, name in pins.items():
        if name in DEDICATED:
            log = DEDICATED[name]
            if log.startswith("D0") or log in (
                    "FCS_B", "CCLK", "DONE", "INIT_B", "PROGRAM_B",
                    "M0", "M1", "M2"):
                config[log] = ball
            elif log in ("TCK", "TMS", "TDI", "TDO"):
                jtag[log] = ball
            elif log in ("VP", "VN"):
                xadc[log] = ball
    assert len(config) == 12, f"config {len(config)}"
    assert len(jtag) == 4, f"jtag {len(jtag)}"
    assert len(xadc) == 2, f"xadc {len(xadc)}"

    # 2. available IO balls = not power, not dedicated.
    used = set(config.values()) | set(jtag.values()) | set(xadc.values())
    avail = [b for b, n in pins.items()
             if not is_power_name(n) and b not in used]
    # clock-capable (MRCC preferred for clocks; SRCC ok as general IO).
    # The token sits mid-name, e.g. IO_L12P_T1_MRCC_16.
    mrcc = sorted(b for b, n in pins.items()
                  if "_MRCC_" in n and b in avail)

    def col(b: int) -> int:
        return int(re.match(r'[A-Z]+(\d+)', b).group(1))

    def rowidx(b: str) -> int:
        return "ABCDEFGHJKLMNPRTUV".index(re.match(r'([A-Z])\d+', b).group(1))

    # 3. signals — control + 64 DET_DIG + 64 EM_DRV.
    signals: dict[str, str] = {}
    taken: set[str] = set()

    # 3a. clocks on MRCC (clk_100mhz input, refclk_25m output).
    assert len(mrcc) >= 2, f"need >=2 MRCC, have {len(mrcc)}"
    signals["clk_100mhz"] = mrcc[0]
    signals["refclk_out"] = mrcc[1]        # -> refclk_25m alias in net_map
    taken |= {mrcc[0], mrcc[1]}

    # 3b. 4 TLC5947 SPI signals -> bottom-edge balls toward the 3x TLC5947
    # constant-current LED driver that lives below the die. The old 64x EM_DRV
    # GPIO is GONE: the TLC5947 daisy-chains 72 sink channels (64 used) over just
    # 4 wires (SCLK/SIN/XLAT/BLANK, with SIN->SOUT cascading chip-to-chip), so
    # the FPGA emitter interface collapses from 64 pins to 4. 60 user-IO balls
    # are freed (spare).
    em_pool = sorted(
        (b for b in avail if b not in taken),
        key=lambda b: (-rowidx(b), col(b)))          # bottom rows first
    em_balls = em_pool[:4]
    for name, b in zip(("em_sclk", "em_sin", "em_xlat", "em_blank"), em_balls):
        signals[name] = b
    taken |= set(em_balls)

    # 3c. 64 DET_DIG inputs -> right-edge balls (high col), excluding taken.
    det_pool = sorted(
        (b for b in avail if b not in taken),
        key=lambda b: (-col(b), rowidx(b)))          # right cols first
    det_balls = det_pool[:64]
    for i, b in enumerate(det_balls):
        signals[f"DET_DIG{i}"] = b
    taken |= set(det_balls)

    # 3d. control nets -> remaining general IO (prefer SRCC/centre, then rest).
    control = ["reset", "tx", "rx", "SPI_CLK", "SPI_DATA", "SPI_LE",
               "ADF_MUXOUT", "mod_sync", "mw_gate", "I2C_SDA", "I2C_SCL"]
    ctrl_pool = sorted(
        (b for b in avail if b not in taken),
        key=lambda b: (col(b), rowidx(b)))            # left/centre first
    for name, b in zip(control, ctrl_pool):
        signals[name] = b
        taken.add(b)

    new = {
        "signals": dict(sorted(signals.items())),
        "power": power,
        "config": config,
        "jtag": jtag,
        "xadc": xadc,
        "source": "tools/gen_fpga_ballmap_csg324.py from "
                  "FPGA_Xilinx_Artix7:XC7A35T-CSG324 (324 pads)",
    }
    OUT.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n")

    n_pow = sum(len(v) for v in power.values())
    n_sig = len(signals)
    n_det = sum(1 for k in signals if k.startswith("DET_DIG"))
    n_em = sum(1 for k in signals if k.startswith("em_"))
    print(f"wrote {OUT}")
    print(f"  power balls: {n_pow} (GND={len(power['GND'])} "
          f"MGT_GND={len(power.get('MGT_GND', []))} "
          f"VCCINT={len(power['VCCINT'])} "
          f"VCCO banks={sum(1 for k in power if k.startswith('VCCO_'))})")
    print(f"  signals: {n_sig}  (DET_DIG={n_det} em_SPI={n_em} "
          f"control={n_sig - n_det - n_em})")
    print(f"  config: {len(config)}  jtag: {len(jtag)}  xadc: {len(xadc)}")
    print(f"  spare IO: {len(avail) - len(taken)}")
    total = n_sig + n_pow + len(config) + len(jtag) + len(xadc)
    print(f"  total netted: {total} / 324")


if __name__ == "__main__":
    main()