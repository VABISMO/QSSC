#!/usr/bin/env python3
"""Emit fpga/constraints.xdc for the XC7A35T-1CSG324C from the authoritative
ball map (.tmpwork/fpga_csg324_map.json, built by gen_fpga_ballmap_csg324.py).

Parallel architecture (single merged board): the FPGA drives 64 individual
emitters (3x TLC5947 over 4-wire SPI) and reads 64 photoluminescence-intensity
channels in parallel via 4x AD7616 16-bit ADCs over a 9-wire SPI bus -- no
analog MUX, no 8x8 row/col matrix, no XADC, no photon counting.  The RTL
top-level ports (fpga/rtl/top_level.v) are:

    clk_100mhz, reset, rx, tx,
    adc_convst, adc_sclk, adc_miso, adc_busy, adc_resetn, adc_cs[3:0]
                        # 4x AD7616 parallel ADC (64 TIA outputs digitised)
    em_sclk, em_sin, em_xlat, em_blank   # 4-wire TLC5947 LED driver (72-ch chain)
    mod_sync, mw_gate, spi_pins[3:0], refclk_out

This maps the JSON signal names (SPI_LE/SPI_CLK/SPI_DATA/ADF_MUXOUT,
adc_*/em_*) to the RTL port convention and assigns every port to the
datasheet-correct CSG324 ball.  All VCCO banks run at 3.3 V -> LVCMOS33
everywhere.  clk_100mhz lands on an MRCC (clock-capable) pin of the CSG324
(see the ballmap).  The MCP4725 I2C threshold DAC is GONE (the comparator
chain it served was removed by the ADC pivot; the AD7616 range is HW-pin-
strapped to +/-5 V, so no config-register writes either).
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / ".tmpwork" / "fpga_csg324_map.json"
OUT = ROOT / "fpga" / "constraints.xdc"

# JSON control-signal name -> RTL port expression (the adc_cs bus is emitted
# separately below from the adc_cs* signal entries).
PORT = {
    "clk_100mhz": "clk_100mhz",
    "reset": "reset",
    "rx": "rx",
    "tx": "tx",
    "refclk_out": "refclk_out",
    "mod_sync": "mod_sync",
    "mw_gate": "mw_gate",
    "SPI_LE": "{spi_pins[0]}",
    "SPI_CLK": "{spi_pins[1]}",
    "SPI_DATA": "{spi_pins[2]}",
    "ADF_MUXOUT": "{spi_pins[3]}",
}


def emit() -> str:
    m = json.loads(MAP.read_text())
    sig = m["signals"]
    jtag = m["jtag"]
    config = m["config"]

    def ball(name: str) -> str:
        return sig[name]

    L = []
    L.append("# =============================================================================")
    L.append("# constraints.xdc  --  QSSC single-board parallel control")
    L.append("# Target: Xilinx Artix-7 XC7A35T-1CSG324C (CSG324, 0.8 mm BGA, 324 balls)")
    L.append("# ----------------------------------------------------------------------------")
    L.append("# Pin assignments are derived from the datasheet-correct CSG324 ball map")
    L.append("# (.tmpwork/fpga_csg324_map.json, parsed from the real KiCad Artix-7 symbol")
    L.append("# and verified against UG475 / DS181). All six VCCO banks are powered at")
    L.append("# 3.3 V (LVCMOS33). clk_100mhz is on a clock-capable (MRCC) pin.")
    L.append("#")
    L.append("# Parallel architecture: 64 emitters driven by 3x TLC5947 constant-current")
    L.append("# LED drivers over a 4-wire SPI (em_sclk/sin/xlat/blank), and 64 photo-")
    L.append("# luminescence-intensity channels read in parallel via 4x AD7616 16-bit")
    L.append("# ADCs over a 9-wire SPI (adc_convst/sclk/miso/busy/cs[3:0]/resetn).")
    L.append("# No analog MUX, no row/col matrix, no XADC, no photon counting.")
    L.append("# =============================================================================")
    L.append("")

    # ---- global options ----
    L.append("# ---- Global options ---------------------------------------------------------")
    L.append("set_property CFGBVS VCCO [current_design]")
    L.append("set_property CONFIG_VOLTAGE 3.3 [current_design]")
    L.append("set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]")
    L.append("")

    # ---- clock ----
    L.append("# ---- 100 MHz system clock (TCXO) --------------------------------------------")
    L.append(f"# clk_100mhz -> {ball('clk_100mhz')}, a multi-region clock-capable (MRCC) pin.")
    L.append(f"set_property PACKAGE_PIN {ball('clk_100mhz')}   [get_ports clk_100mhz]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports clk_100mhz]")
    L.append("create_clock -name sys_clk -period 10.000 [get_ports clk_100mhz]")
    L.append("set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets clk_100mhz_IBUF]")
    L.append("")

    # ---- reset ----
    L.append("# ---- Reset (active-high push button) ----------------------------------------")
    L.append(f"set_property PACKAGE_PIN {ball('reset')}   [get_ports reset]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports reset]")
    L.append("set_property PULLDOWN TRUE [get_ports reset]")
    L.append("")

    # ---- UART ----
    L.append("# ---- UART (FT232 RX/TX) -----------------------------------------------------")
    L.append(f"set_property PACKAGE_PIN {ball('rx')}   [get_ports rx]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports rx]")
    L.append(f"set_property PACKAGE_PIN {ball('tx')}   [get_ports tx]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports tx]")
    L.append("set_property SLEW SLOW  [get_ports tx]")
    L.append("set_property DRIVE 8    [get_ports tx]")
    L.append("")

    # ---- detector readout: 4x AD7616 parallel ADC over a 9-wire SPI bus ----
    L.append("# ---- 4x AD7616 parallel ADC readout (FPGA <- 64 TIA outputs) ---------------")
    L.append("# 9-wire SPI bus: adc_convst (shared conversion start), adc_sclk (shared")
    L.append("# clock), adc_miso (shared SDOA, per-chip CS selects the driver), adc_busy")
    L.append("# (chip0 only -- push-pull, all 4 finish a simultaneous CONVST together),")
    L.append("# adc_cs[3:0] (per-chip select), adc_resetn (shared reset, active-low). The")
    L.append("# +/-5V range is HW-pin-strapped on each AD7616 (no MOSI / config writes).")
    L.append(f"set_property PACKAGE_PIN {ball('adc_convst')}  [get_ports adc_convst]")
    L.append(f"set_property PACKAGE_PIN {ball('adc_sclk')}  [get_ports adc_sclk]")
    L.append(f"set_property PACKAGE_PIN {ball('adc_miso')}  [get_ports adc_miso]")
    L.append(f"set_property PACKAGE_PIN {ball('adc_busy')}  [get_ports adc_busy]")
    L.append(f"set_property PACKAGE_PIN {ball('adc_resetn')}  [get_ports adc_resetn]")
    for j in range(4):
        L.append(f"set_property PACKAGE_PIN {sig[f'adc_cs{j}']}  [get_ports {{adc_cs[{j}]}}]")
    for p in ("adc_convst", "adc_sclk", "adc_miso", "adc_busy", "adc_resetn"):
        L.append(f"set_property IOSTANDARD LVCMOS33 [get_ports {p}]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports {adc_cs[*]}]")
    L.append("set_property SLEW FAST [get_ports adc_sclk]")
    L.append("set_property DRIVE 8   [get_ports adc_sclk]")
    L.append("")

    # ---- TLC5947 emitter driver (4-wire SPI, 72-ch chain, 64 used) ----
    L.append("# ---- TLC5947 constant-current LED driver (FPGA -> 3x TLC5947, 64 emitters) --")
    L.append("# 4-wire interface: em_sclk (shift clock), em_sin (data in), em_xlat (latch),")
    L.append("# em_blank (global blank/PWM gate). 3x TLC5947 daisy-chain SIN->SOUT for 72")
    L.append("# channels (64 used); each emitter cathode is a TLC5947 OUTn, anode = +5V.")
    L.append("# 12-bit PWM grayscale per channel -> parallel multi-emitter drive.")
    L.append(f"set_property PACKAGE_PIN {ball('em_sclk')}   [get_ports em_sclk]")
    L.append(f"set_property PACKAGE_PIN {ball('em_sin')}   [get_ports em_sin]")
    L.append(f"set_property PACKAGE_PIN {ball('em_xlat')}   [get_ports em_xlat]")
    L.append(f"set_property PACKAGE_PIN {ball('em_blank')}   [get_ports em_blank]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports em_sclk]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports em_sin]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports em_xlat]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports em_blank]")
    L.append("set_property SLEW FAST [get_ports em_sclk]")
    L.append("set_property DRIVE 8   [get_ports em_sclk]")
    L.append("")

    # ---- mod_sync ----
    L.append("# ---- Lock-in modulation reference (1 kHz square wave out) ------------------")
    L.append("# Reference for the host/MW-chain lock-in demodulation of the detector")
    L.append("# photocurrent; gates the 64 parallel counters' I/Q accumulation windows.")
    L.append(f"set_property PACKAGE_PIN {ball('mod_sync')}   [get_ports mod_sync]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports mod_sync]")
    L.append("set_property SLEW SLOW [get_ports mod_sync]")
    L.append("set_property DRIVE 8   [get_ports mod_sync]")
    L.append("")

    # ---- mw_gate ----
    L.append("# ---- MW RF-switch gate pulse (APPLY_PULSE duration) ------------------------")
    L.append("# Gates the external RF switch between the ADF4351 output and the antenna.")
    L.append(f"set_property PACKAGE_PIN {ball('mw_gate')}   [get_ports mw_gate]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports mw_gate]")
    L.append("set_property SLEW FAST [get_ports mw_gate]")
    L.append("set_property DRIVE 8   [get_ports mw_gate]")
    L.append("")

    # ---- ADF4351 SPI ----
    L.append("# ---- ADF4351 SPI: {LE, CLK, DATA, MUXOUT} -----------------------------------")
    L.append("# spi_pins[0]=LE, [1]=CLK, [2]=DATA (FPGA outputs), [3]=MUXOUT (FPGA input)")
    L.append(f"set_property PACKAGE_PIN {ball('SPI_LE')}  [get_ports {{spi_pins[0]}}]")
    L.append(f"set_property PACKAGE_PIN {ball('SPI_CLK')}  [get_ports {{spi_pins[1]}}]")
    L.append(f"set_property PACKAGE_PIN {ball('SPI_DATA')}  [get_ports {{spi_pins[2]}}]")
    L.append(f"set_property PACKAGE_PIN {ball('ADF_MUXOUT')}  [get_ports {{spi_pins[3]}}]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[0]}]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[1]}]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[2]}]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[3]}]")
    L.append("set_property SLEW FAST [get_ports {spi_pins[1]}]")
    L.append("set_property DRIVE 8   [get_ports {spi_pins[1]}]")
    L.append("")

    # ---- refclk_out ----
    L.append("# ---- 25 MHz reference clock to ADF4351 REF_IN -------------------------------")
    L.append(f"set_property PACKAGE_PIN {ball('refclk_out')}   [get_ports refclk_out]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports refclk_out]")
    L.append("set_property SLEW FAST [get_ports refclk_out]")
    L.append("set_property DRIVE 8   [get_ports refclk_out]")
    L.append("")

    # ---- config / JTAG (dedicated, not get_ports) ----
    L.append("# ---- Configuration (QSPI boot from W25Q128) + JTAG --------------------------")
    L.append("# Dedicated / fixed-function pins (not in top_level's port list):")
    L.append(f"#   CCLK={config['CCLK']}  D00={config['D00']}  D01={config['D01']}  "
             f"D02={config['D02']}  D03={config['D03']}  FCS_B={config['FCS_B']}")
    L.append(f"#   DONE={config['DONE']}  INIT_B={config['INIT_B']}  "
             f"PROGRAM_B={config['PROGRAM_B']}  M0={config['M0']}  M1={config['M1']}  M2={config['M2']}")
    L.append(f"#   JTAG: TCK={jtag['TCK']} TMS={jtag['TMS']} TDI={jtag['TDI']} TDO={jtag['TDO']}")
    L.append("# BITSTREAM.CONFIG.SPI_BUSWIDTH 4 is set above; the Artix-7 hard SPI controller")
    L.append("# drives these pins automatically at config time -- no per-port pin assignments.")
    L.append("")

    # ---- timing ----
    L.append("# ---- Timing: clock-domain crossings (constrained for analysis) --------------")
    L.append("# The 1 kHz mod_sync is derived from sys_clk (no CDC). The AD7616 SPI bus")
    L.append("# (adc_*) is a synchronous master interface clocked by adc_sclk from sys_clk.")
    L.append("# Asynchronous reset is not timed.")
    L.append("set_false_path -through [get_ports reset]")
    L.append("")

    return "\n".join(L)


def main() -> None:
    OUT.write_text(emit())
    m = json.loads(MAP.read_text())
    n_adc = sum(1 for k in m["signals"] if k.startswith("adc_"))
    n_em = sum(1 for k in m["signals"] if k.startswith("em_"))
    print(f"wrote {OUT}")
    print(f"  CSG324: {len(m['signals'])} user-IO signals "
          f"(adc_SPI={n_adc} em_SPI={n_em} control={len(m['signals']) - n_adc - n_em})")
    print(f"  clk_100mhz -> {m['signals']['clk_100mhz']} (MRCC)")
    print(f"  refclk_out -> {m['signals']['refclk_out']}")
    print(f"  adc_convst -> {m['signals']['adc_convst']}  adc_sclk -> {m['signals']['adc_sclk']}")


if __name__ == "__main__":
    main()