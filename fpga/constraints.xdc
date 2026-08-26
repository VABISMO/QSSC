# =============================================================================
# constraints.xdc  --  QSSC control board
# Target: Xilinx Artix-7 XC7A35T-1CPG236C (CPG236, 0.5 mm BGA, 236 balls)
# ----------------------------------------------------------------------------
# Pin assignments are derived from the datasheet-correct CPG236 ball map
# (.tmpwork/fpga_cpg236_map.json, parsed from the real KiCad Artix-7 symbol
# and verified against UG475 / DS181). All six VCCO banks are powered at
# 3.3 V (LVCMOS33). clk_100mhz is on A16 = IO_L12P_T1_MRCC_16, a clock-
# capable (MRCC) pin of bank 16.
#
# This board has NO camera. The chip is read on-chip: an 8x8 CQD emitter
# array (driven via an external higher-voltage driver enabled by the FPGA
# crossbar) pumps each site and an on-chip graphene/MoS2 photodetector array
# is read through an external TIA + analog MUX into the Artix-7 XADC,
# lock-in demodulated at 1 kHz. The detector MUX select and the lock-in
# modulation reference are the freed OV7670 SCCB/pixel pin functions.
# =============================================================================

# ---- Global options ---------------------------------------------------------
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]

# ---- 100 MHz system clock (TCXO) --------------------------------------------
# A16 = IO_L12P_T1_MRCC_16, a multi-region clock-capable (MRCC) pin of bank 16.
set_property PACKAGE_PIN A16   [get_ports clk_100mhz]
set_property IOSTANDARD LVCMOS33 [get_ports clk_100mhz]
create_clock -name sys_clk -period 10.000 [get_ports clk_100mhz]
set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets clk_100mhz_IBUF]

# ---- Reset (active-high push button) ----------------------------------------
set_property PACKAGE_PIN N17   [get_ports reset]
set_property IOSTANDARD LVCMOS33 [get_ports reset]
set_property PULLDOWN TRUE [get_ports reset]

# ---- UART (FT232 RX/TX) -----------------------------------------------------
set_property PACKAGE_PIN P17   [get_ports rx]
set_property IOSTANDARD LVCMOS33 [get_ports rx]
set_property PACKAGE_PIN P18   [get_ports tx]
set_property IOSTANDARD LVCMOS33 [get_ports tx]
set_property SLEW SLOW  [get_ports tx]
set_property DRIVE 8    [get_ports tx]

# ---- 8x8 on-chip CQD emitter array: row anode-enables (active high) ----------
# The FPGA enables an external higher-voltage emitter driver (4-8 V / 2-20 mA
# per pixel); these 8 row enables select the anode of one CQD pixel column.
set_property PACKAGE_PIN B17  [get_ports {emitter_rows[0]}]
set_property PACKAGE_PIN K17  [get_ports {emitter_rows[1]}]
set_property PACKAGE_PIN L18  [get_ports {emitter_rows[2]}]
set_property PACKAGE_PIN B18  [get_ports {emitter_rows[3]}]
set_property PACKAGE_PIN C15  [get_ports {emitter_rows[4]}]
set_property PACKAGE_PIN M18  [get_ports {emitter_rows[5]}]
set_property PACKAGE_PIN C16  [get_ports {emitter_rows[6]}]
set_property PACKAGE_PIN N18  [get_ports {emitter_rows[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {emitter_rows[*]}]
set_property SLEW FAST [get_ports {emitter_rows[*]}]
set_property DRIVE 8   [get_ports {emitter_rows[*]}]

# ---- 8x8 on-chip CQD emitter array: column cathode-enables (active low) -------
set_property PACKAGE_PIN C17  [get_ports {emitter_cols[0]}]
set_property PACKAGE_PIN K18  [get_ports {emitter_cols[1]}]
set_property PACKAGE_PIN G19  [get_ports {emitter_cols[2]}]
set_property PACKAGE_PIN H19  [get_ports {emitter_cols[3]}]
set_property PACKAGE_PIN R18  [get_ports {emitter_cols[4]}]
set_property PACKAGE_PIN J18  [get_ports {emitter_cols[5]}]
set_property PACKAGE_PIN T17  [get_ports {emitter_cols[6]}]
set_property PACKAGE_PIN T18  [get_ports {emitter_cols[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {emitter_cols[*]}]
set_property SLEW FAST [get_ports {emitter_cols[*]}]
set_property DRIVE 8   [get_ports {emitter_cols[*]}]

# ---- On-chip graphene/MoS2 photodetector: analog MUX select -----------------
# 6 select lines to the external analog MUX that routes one detector electrode's
# TIA voltage to the XADC aux input (VauxP0/VauxN0 = A12/B13).
set_property PACKAGE_PIN J17  [get_ports {det_mux_sel[0]}]
set_property PACKAGE_PIN J19  [get_ports {det_mux_sel[1]}]
set_property PACKAGE_PIN H17  [get_ports {det_mux_sel[2]}]
set_property PACKAGE_PIN L17  [get_ports {det_mux_sel[3]}]
set_property PACKAGE_PIN G17  [get_ports {det_mux_sel[4]}]
set_property PACKAGE_PIN M19  [get_ports {det_mux_sel[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {det_mux_sel[*]}]
set_property SLEW SLOW [get_ports {det_mux_sel[*]}]
set_property DRIVE 8   [get_ports {det_mux_sel[*]}]

# ---- Lock-in modulation reference (1 kHz square wave out) ------------------
# Reference for the host/MW-chain lock-in demodulation of the detector
# photocurrent.
set_property PACKAGE_PIN A15   [get_ports mod_sync]
set_property IOSTANDARD LVCMOS33 [get_ports mod_sync]
set_property SLEW SLOW [get_ports mod_sync]
set_property DRIVE 8   [get_ports mod_sync]

# ---- MW RF-switch gate pulse (APPLY_PULSE duration) ------------------------
# Gates the external RF switch between the ADF4351 output and the antenna for
# the requested pulse duration (mw_gate_ns). The ADF4351 sets carrier phase
# (R1) + output power (R4); this pin times the pulse.
set_property PACKAGE_PIN A17   [get_ports mw_gate]
set_property IOSTANDARD LVCMOS33 [get_ports mw_gate]
set_property SLEW FAST [get_ports mw_gate]
set_property DRIVE 8   [get_ports mw_gate]

# ---- Artix-7 XADC (12-bit, up to 1 MSPS) ------------------------------------
# The XADC is a hard block. Its analog aux input pair VauxP0/VauxN0 = A12/B13
# and the analog supply pins VCCADC/GNDADC/VREFP/VREFN are FIXED-PACKAGE pins
# (not assigned via get_ports -- the top_level models the XADC behaviourally
# and has no analog port). In the real build, instantiate the XADC wizard;
# route the TIA output into VauxP0 (A12) / VauxN0 (B13). DXP/DXN (A11/B11,
# on-die temperature diode) are left unconnected (temp sensor not used).

# ---- ADF4351 SPI: {LE, CLK, DATA, MUXOUT} -----------------------------------
# spi_pins[0]=LE, [1]=CLK, [2]=DATA (FPGA outputs), [3]=MUXOUT (FPGA input)
set_property PACKAGE_PIN D17  [get_ports {spi_pins[0]}]
set_property PACKAGE_PIN A18  [get_ports {spi_pins[1]}]
set_property PACKAGE_PIN B15  [get_ports {spi_pins[2]}]
set_property PACKAGE_PIN B16  [get_ports {spi_pins[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[3]}]
set_property SLEW FAST [get_ports {spi_pins[1]}]
set_property DRIVE 8   [get_ports {spi_pins[1]}]

# ---- 25 MHz reference clock to ADF4351 REF_IN -------------------------------
set_property PACKAGE_PIN A14   [get_ports refclk_out]
set_property IOSTANDARD LVCMOS33 [get_ports refclk_out]
set_property SLEW FAST [get_ports refclk_out]
set_property DRIVE 8   [get_ports refclk_out]

# ---- Configuration (QSPI boot from W25Q128) + JTAG --------------------------
# These are dedicated / fixed-function pins (not in top_level's port list):
#   CCLK=C11  D00=D18  D01=D19  D02=G18  D03=F18  FCS_B=K19  (bank 14 + dedicated)
#   DONE=U12  INIT_B=U11  PROGRAM_B=V10  M0=V12  M1=W11  M2=U10
#   JTAG: TCK=C8 TMS=W9 TDI=W10 TDO=W8
# BITSTREAM.CONFIG.SPI_BUSWIDTH 4 is set above; the Artix-7 hard SPI controller
# drives these pins automatically at config time -- no per-port pin assignments.

# ---- Timing: clock-domain crossings (constrained for analysis) --------------
# The on-chip readout has no external pixel clock, so the old cam_pclk false
# paths are removed. The 1 kHz mod_sync is derived from sys_clk (no CDC).
# Asynchronous reset is not timed.
set_false_path -through [get_ports reset]
