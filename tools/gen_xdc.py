#!/usr/bin/env python3
"""Emit fpga/constraints.xdc for the XC7A35T-1CSG324C from the authoritative
ball map (.tmpwork/fpga_csg324_map.json, built by gen_fpga_ballmap_csg324.py).

Parallel architecture (single merged board): the FPGA drives 64 individual
emitters and reads 64 individual photon-counting detector channels in
parallel -- no analog MUX, no 8x8 row/col matrix, no XADC.  The RTL top-level
ports (fpga/rtl/top_level.v, after the Phase 3 refactor) are:

    clk_100mhz, reset, rx, tx,
    det_in[63:0]        # 64 comparator outputs -> FPGA LVCMOS33 inputs (DET_DIG)
    emitter_en[63:0]    # 64 P-MOS emitter enables (EM_DRV)
    mod_sync, mw_gate, spi_pins[3:0], refclk_out,
    i2c_sda, i2c_scl    # MCP4725 threshold DAC

This maps the JSON signal names (SPI_LE/SPI_CLK/SPI_DATA/ADF_MUXOUT,
DET_DIG*/EM_DRV*, I2C_SDA/I2C_SCL) to the RTL port convention and assigns
every port to the datasheet-correct CSG324 ball.  All VCCO banks run at
3.3 V -> LVCMOS33 everywhere.  clk_100mhz lands on an MRCC (clock-capable)
pin of the CSG324 (see the ballmap).
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / ".tmpwork" / "fpga_csg324_map.json"
OUT = ROOT / "fpga" / "constraints.xdc"

# JSON control-signal name -> RTL port expression (the 64-bit buses are
# emitted separately below from the DET_DIG*/EM_DRV* signal entries).
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
    "I2C_SDA": "i2c_sda",
    "I2C_SCL": "i2c_scl",
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
    L.append("# Parallel architecture: 64 emitters driven individually (emitter_en[63:0])")
    L.append("# and 64 photon-counting detector channels read in parallel (det_in[63:0],")
    L.append("# comparator -> LVCMOS -> 64 fabric counters). No analog MUX, no row/col")
    L.append("# matrix, no XADC. A MCP4725 DAC sets the comparator threshold over I2C.")
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

    # ---- detector inputs (64 parallel photon-count channels) ----
    L.append("# ---- 64 detector photon-count inputs (comparator -> FPGA LVCMOS33) ----------")
    L.append("# det_in[i] = digitised photocurrent of detector i (DET_DIG{i}); each channel")
    L.append("# has its own TIA + Schmitt comparator, counted by a 64-wide counter fabric.")
    for i in range(64):
        L.append(f"set_property PACKAGE_PIN {sig[f'DET_DIG{i}']}  [get_ports {{det_in[{i}]}}]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports {det_in[*]}]")
    L.append("")

    # ---- emitter enables (64 individual emitters) ----
    L.append("# ---- 64 emitter enables (FPGA -> 74HCT240 -> P-MOS high-side driver) --------")
    L.append("# emitter_en[i] drives emitter i's P-MOS gate through a 74HCT240 buffer;")
    L.append("# 64 independent channels (no 8x8 matrix).")
    for i in range(64):
        L.append(f"set_property PACKAGE_PIN {sig[f'EM_DRV{i}']}  [get_ports {{emitter_en[{i}]}}]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports {emitter_en[*]}]")
    L.append("set_property SLEW FAST [get_ports {emitter_en[*]}]")
    L.append("set_property DRIVE 8   [get_ports {emitter_en[*]}]")
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

    # ---- I2C (MCP4725 threshold DAC) ----
    L.append("# ---- I2C to MCP4725 comparator-threshold DAC --------------------------------")
    L.append("# Open-drain SDA/SCL with 4.7k pull-ups to +3V3 on the board.")
    L.append(f"set_property PACKAGE_PIN {ball('I2C_SDA')}   [get_ports i2c_sda]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports i2c_sda]")
    L.append(f"set_property PACKAGE_PIN {ball('I2C_SCL')}   [get_ports i2c_scl]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports i2c_scl]")
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
    L.append("# The 1 kHz mod_sync is derived from sys_clk (no CDC). The 64 det_in channels")
    L.append("# are synchronous to sys_clk (comparator outputs registered in the fabric).")
    L.append("# Asynchronous reset is not timed.")
    L.append("set_false_path -through [get_ports reset]")
    L.append("")

    return "\n".join(L)


def main() -> None:
    OUT.write_text(emit())
    m = json.loads(MAP.read_text())
    n_det = sum(1 for k in m["signals"] if k.startswith("DET_DIG"))
    n_em = sum(1 for k in m["signals"] if k.startswith("EM_DRV"))
    print(f"wrote {OUT}")
    print(f"  CSG324: {len(m['signals'])} user-IO signals "
          f"(det_in={n_det} emitter_en={n_em} control={len(m['signals']) - n_det - n_em})")
    print(f"  clk_100mhz -> {m['signals']['clk_100mhz']} (MRCC)")
    print(f"  refclk_out -> {m['signals']['refclk_out']}")
    print(f"  i2c_sda -> {m['signals']['I2C_SDA']}  i2c_scl -> {m['signals']['I2C_SCL']}")


if __name__ == "__main__":
    main()