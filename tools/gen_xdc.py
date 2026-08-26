#!/usr/bin/env python3
"""Emit fpga/constraints.xdc for the XC7A35T-1CPG236C from the authoritative
ball map (.tmpwork/fpga_cpg236_map.json, built by gen_fpga_ballmap.py).

The RTL top-level ports (fpga/rtl/top_level.v) are:
    clk_100mhz, reset, rx, tx, emitter_rows[7:0], emitter_cols[7:0],
    det_mux_sel[5:0], mod_sync, spi_pins[3:0], refclk_out, mw_gate
This maps the JSON signal names (SPI_LE/SPI_CLK/SPI_DATA/ADF_MUXOUT) to the
RTL's spi_pins[] convention and assigns every port to the datasheet-correct
CPG236 ball. The header is corrected: CPG236, 0.5 mm pitch, 236 balls (the old
"CSG236 / 0.8 mm" header was wrong and had driven an invalid pin assignment).

All six VCCO banks run at 3.3 V -> LVCMOS33 everywhere. clk_100mhz lands on
A16 = IO_L12P_T1_MRCC_16, a clock-capable (MRCC) pin of bank 16.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / ".tmpwork" / "fpga_cpg236_map.json"
OUT = ROOT / "fpga" / "constraints.xdc"

# JSON signal name -> RTL port expression
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
SLOW_PORTS = {"reset", "{spi_pins[2]}", "{spi_pins[3]}", "mod_sync"}
FAST_PORTS = {"tx", "{spi_pins[1]}", "refclk_out", "mw_gate",
              "{emitter_rows[*]}", "{emitter_cols[*]}"}


def emit() -> str:
    m = json.loads(MAP.read_text())
    sig = m["signals"]
    cfg = m["config"]
    jtag = m["jtag"]

    def ball(name: str) -> str:
        return sig[name]

    L = []
    L.append("# =============================================================================")
    L.append("# constraints.xdc  --  QSSC control board")
    L.append("# Target: Xilinx Artix-7 XC7A35T-1CPG236C (CPG236, 0.5 mm BGA, 236 balls)")
    L.append("# ----------------------------------------------------------------------------")
    L.append("# Pin assignments are derived from the datasheet-correct CPG236 ball map")
    L.append("# (.tmpwork/fpga_cpg236_map.json, parsed from the real KiCad Artix-7 symbol")
    L.append("# and verified against UG475 / DS181). All six VCCO banks are powered at")
    L.append("# 3.3 V (LVCMOS33). clk_100mhz is on A16 = IO_L12P_T1_MRCC_16, a clock-")
    L.append("# capable (MRCC) pin of bank 16.")
    L.append("#")
    L.append("# This board has NO camera. The chip is read on-chip: an 8x8 CQD emitter")
    L.append("# array (driven via an external higher-voltage driver enabled by the FPGA")
    L.append("# crossbar) pumps each site and an on-chip graphene/MoS2 photodetector array")
    L.append("# is read through an external TIA + analog MUX into the Artix-7 XADC,")
    L.append("# lock-in demodulated at 1 kHz. The detector MUX select and the lock-in")
    L.append("# modulation reference are the freed OV7670 SCCB/pixel pin functions.")
    L.append("# =============================================================================")
    L.append("")
    L.append("# ---- Global options ---------------------------------------------------------")
    L.append("set_property CFGBVS VCCO [current_design]")
    L.append("set_property CONFIG_VOLTAGE 3.3 [current_design]")
    L.append("set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]")
    L.append("")

    # ---- clock ----
    L.append("# ---- 100 MHz system clock (TCXO) --------------------------------------------")
    L.append("# A16 = IO_L12P_T1_MRCC_16, a multi-region clock-capable (MRCC) pin of bank 16.")
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

    # ---- emitter rows ----
    L.append("# ---- 8x8 on-chip CQD emitter array: row anode-enables (active high) ----------")
    L.append("# The FPGA enables an external higher-voltage emitter driver (4-8 V / 2-20 mA")
    L.append("# per pixel); these 8 row enables select the anode of one CQD pixel column.")
    for i in range(8):
        L.append(f"set_property PACKAGE_PIN {ball(f'emitter_rows{i}')}  [get_ports {{emitter_rows[{i}]}}]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports {emitter_rows[*]}]")
    L.append("set_property SLEW FAST [get_ports {emitter_rows[*]}]")
    L.append("set_property DRIVE 8   [get_ports {emitter_rows[*]}]")
    L.append("")

    # ---- emitter cols ----
    L.append("# ---- 8x8 on-chip CQD emitter array: column cathode-enables (active low) -------")
    for i in range(8):
        L.append(f"set_property PACKAGE_PIN {ball(f'emitter_cols{i}')}  [get_ports {{emitter_cols[{i}]}}]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports {emitter_cols[*]}]")
    L.append("set_property SLEW FAST [get_ports {emitter_cols[*]}]")
    L.append("set_property DRIVE 8   [get_ports {emitter_cols[*]}]")
    L.append("")

    # ---- det mux sel ----
    L.append("# ---- On-chip graphene/MoS2 photodetector: analog MUX select -----------------")
    L.append("# 6 select lines to the external analog MUX that routes one detector electrode's")
    L.append("# TIA voltage to the XADC aux input (VauxP0/VauxN0 = A12/B13).")
    for i in range(6):
        L.append(f"set_property PACKAGE_PIN {ball(f'det_mux_sel{i}')}  [get_ports {{det_mux_sel[{i}]}}]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports {det_mux_sel[*]}]")
    L.append("set_property SLEW SLOW [get_ports {det_mux_sel[*]}]")
    L.append("set_property DRIVE 8   [get_ports {det_mux_sel[*]}]")
    L.append("")

    # ---- mod_sync ----
    L.append("# ---- Lock-in modulation reference (1 kHz square wave out) ------------------")
    L.append("# Reference for the host/MW-chain lock-in demodulation of the detector")
    L.append("# photocurrent.")
    L.append(f"set_property PACKAGE_PIN {ball('mod_sync')}   [get_ports mod_sync]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports mod_sync]")
    L.append("set_property SLEW SLOW [get_ports mod_sync]")
    L.append("set_property DRIVE 8   [get_ports mod_sync]")
    L.append("")

    # ---- mw_gate ----
    L.append("# ---- MW RF-switch gate pulse (APPLY_PULSE duration) ------------------------")
    L.append("# Gates the external RF switch between the ADF4351 output and the antenna for")
    L.append("# the requested pulse duration (mw_gate_ns). The ADF4351 sets carrier phase")
    L.append("# (R1) + output power (R4); this pin times the pulse.")
    L.append(f"set_property PACKAGE_PIN {ball('mw_gate')}   [get_ports mw_gate]")
    L.append("set_property IOSTANDARD LVCMOS33 [get_ports mw_gate]")
    L.append("set_property SLEW FAST [get_ports mw_gate]")
    L.append("set_property DRIVE 8   [get_ports mw_gate]")
    L.append("")

    # ---- XADC ----
    L.append("# ---- Artix-7 XADC (12-bit, up to 1 MSPS) ------------------------------------")
    L.append("# The XADC is a hard block. Its analog aux input pair VauxP0/VauxN0 = A12/B13")
    L.append("# and the analog supply pins VCCADC/GNDADC/VREFP/VREFN are FIXED-PACKAGE pins")
    L.append("# (not assigned via get_ports -- the top_level models the XADC behaviourally")
    L.append("# and has no analog port). In the real build, instantiate the XADC wizard;")
    L.append("# route the TIA output into VauxP0 (A12) / VauxN0 (B13). DXP/DXN (A11/B11,")
    L.append("# on-die temperature diode) are left unconnected (temp sensor not used).")
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
    L.append("# These are dedicated / fixed-function pins (not in top_level's port list):")
    L.append("#   CCLK=C11  D00=D18  D01=D19  D02=G18  D03=F18  FCS_B=K19  (bank 14 + dedicated)")
    L.append("#   DONE=U12  INIT_B=U11  PROGRAM_B=V10  M0=V12  M1=W11  M2=U10")
    L.append(f"#   JTAG: TCK={jtag['TCK']} TMS={jtag['TMS']} TDI={jtag['TDI']} TDO={jtag['TDO']}")
    L.append("# BITSTREAM.CONFIG.SPI_BUSWIDTH 4 is set above; the Artix-7 hard SPI controller")
    L.append("# drives these pins automatically at config time -- no per-port pin assignments.")
    L.append("")

    # ---- timing ----
    L.append("# ---- Timing: clock-domain crossings (constrained for analysis) --------------")
    L.append("# The on-chip readout has no external pixel clock, so the old cam_pclk false")
    L.append("# paths are removed. The 1 kHz mod_sync is derived from sys_clk (no CDC).")
    L.append("# Asynchronous reset is not timed.")
    L.append("set_false_path -through [get_ports reset]")
    L.append("")

    return "\n".join(L)


def main() -> None:
    OUT.write_text(emit())
    m = json.loads(MAP.read_text())
    print(f"wrote {OUT}")
    print(f"  {len(m['signals'])} user-IO signals assigned to CPG236 balls")
    print(f"  clk_100mhz -> {m['signals']['clk_100mhz']} (MRCC bank 16)")
    print(f"  config: {m['config']}")
    print(f"  jtag:   {m['jtag']}")


if __name__ == "__main__":
    main()