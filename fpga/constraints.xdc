# =============================================================================
# constraints.xdc  --  QSSC single-board parallel control
# Target: Xilinx Artix-7 XC7A35T-1CSG324C (CSG324, 0.8 mm BGA, 324 balls)
# ----------------------------------------------------------------------------
# Pin assignments are derived from the datasheet-correct CSG324 ball map
# (.tmpwork/fpga_csg324_map.json, parsed from the real KiCad Artix-7 symbol
# and verified against UG475 / DS181). All six VCCO banks are powered at
# 3.3 V (LVCMOS33). clk_100mhz is on a clock-capable (MRCC) pin.
#
# Parallel architecture: 64 emitters driven individually (emitter_en[63:0])
# and 64 photon-counting detector channels read in parallel (det_in[63:0],
# comparator -> LVCMOS -> 64 fabric counters). No analog MUX, no row/col
# matrix, no XADC. A MCP4725 DAC sets the comparator threshold over I2C.
# =============================================================================

# ---- Global options ---------------------------------------------------------
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]

# ---- 100 MHz system clock (TCXO) --------------------------------------------
# clk_100mhz -> A8, a multi-region clock-capable (MRCC) pin.
set_property PACKAGE_PIN A8   [get_ports clk_100mhz]
set_property IOSTANDARD LVCMOS33 [get_ports clk_100mhz]
create_clock -name sys_clk -period 10.000 [get_ports clk_100mhz]
set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets clk_100mhz_IBUF]

# ---- Reset (active-high push button) ----------------------------------------
set_property PACKAGE_PIN A1   [get_ports reset]
set_property IOSTANDARD LVCMOS33 [get_ports reset]
set_property PULLDOWN TRUE [get_ports reset]

# ---- UART (FT232 RX/TX) -----------------------------------------------------
set_property PACKAGE_PIN C1   [get_ports rx]
set_property IOSTANDARD LVCMOS33 [get_ports rx]
set_property PACKAGE_PIN B1   [get_ports tx]
set_property IOSTANDARD LVCMOS33 [get_ports tx]
set_property SLEW SLOW  [get_ports tx]
set_property DRIVE 8    [get_ports tx]

# ---- 64 detector photon-count inputs (comparator -> FPGA LVCMOS33) ----------
# det_in[i] = digitised photocurrent of detector i (DET_DIG{i}); each channel
# has its own TIA + Schmitt comparator, counted by a 64-wide counter fabric.
set_property PACKAGE_PIN A18  [get_ports {det_in[0]}]
set_property PACKAGE_PIN B18  [get_ports {det_in[1]}]
set_property PACKAGE_PIN D18  [get_ports {det_in[2]}]
set_property PACKAGE_PIN E18  [get_ports {det_in[3]}]
set_property PACKAGE_PIN F18  [get_ports {det_in[4]}]
set_property PACKAGE_PIN G18  [get_ports {det_in[5]}]
set_property PACKAGE_PIN J18  [get_ports {det_in[6]}]
set_property PACKAGE_PIN L18  [get_ports {det_in[7]}]
set_property PACKAGE_PIN M18  [get_ports {det_in[8]}]
set_property PACKAGE_PIN P18  [get_ports {det_in[9]}]
set_property PACKAGE_PIN B17  [get_ports {det_in[10]}]
set_property PACKAGE_PIN C17  [get_ports {det_in[11]}]
set_property PACKAGE_PIN D17  [get_ports {det_in[12]}]
set_property PACKAGE_PIN E17  [get_ports {det_in[13]}]
set_property PACKAGE_PIN G17  [get_ports {det_in[14]}]
set_property PACKAGE_PIN H17  [get_ports {det_in[15]}]
set_property PACKAGE_PIN J17  [get_ports {det_in[16]}]
set_property PACKAGE_PIN M17  [get_ports {det_in[17]}]
set_property PACKAGE_PIN N17  [get_ports {det_in[18]}]
set_property PACKAGE_PIN P17  [get_ports {det_in[19]}]
set_property PACKAGE_PIN A16  [get_ports {det_in[20]}]
set_property PACKAGE_PIN B16  [get_ports {det_in[21]}]
set_property PACKAGE_PIN C16  [get_ports {det_in[22]}]
set_property PACKAGE_PIN E16  [get_ports {det_in[23]}]
set_property PACKAGE_PIN F16  [get_ports {det_in[24]}]
set_property PACKAGE_PIN G16  [get_ports {det_in[25]}]
set_property PACKAGE_PIN H16  [get_ports {det_in[26]}]
set_property PACKAGE_PIN K16  [get_ports {det_in[27]}]
set_property PACKAGE_PIN L16  [get_ports {det_in[28]}]
set_property PACKAGE_PIN M16  [get_ports {det_in[29]}]
set_property PACKAGE_PIN N16  [get_ports {det_in[30]}]
set_property PACKAGE_PIN A15  [get_ports {det_in[31]}]
set_property PACKAGE_PIN C15  [get_ports {det_in[32]}]
set_property PACKAGE_PIN D15  [get_ports {det_in[33]}]
set_property PACKAGE_PIN E15  [get_ports {det_in[34]}]
set_property PACKAGE_PIN F15  [get_ports {det_in[35]}]
set_property PACKAGE_PIN H15  [get_ports {det_in[36]}]
set_property PACKAGE_PIN J15  [get_ports {det_in[37]}]
set_property PACKAGE_PIN K15  [get_ports {det_in[38]}]
set_property PACKAGE_PIN L15  [get_ports {det_in[39]}]
set_property PACKAGE_PIN N15  [get_ports {det_in[40]}]
set_property PACKAGE_PIN A14  [get_ports {det_in[41]}]
set_property PACKAGE_PIN B14  [get_ports {det_in[42]}]
set_property PACKAGE_PIN C14  [get_ports {det_in[43]}]
set_property PACKAGE_PIN D14  [get_ports {det_in[44]}]
set_property PACKAGE_PIN F14  [get_ports {det_in[45]}]
set_property PACKAGE_PIN G14  [get_ports {det_in[46]}]
set_property PACKAGE_PIN H14  [get_ports {det_in[47]}]
set_property PACKAGE_PIN J14  [get_ports {det_in[48]}]
set_property PACKAGE_PIN N14  [get_ports {det_in[49]}]
set_property PACKAGE_PIN A13  [get_ports {det_in[50]}]
set_property PACKAGE_PIN B13  [get_ports {det_in[51]}]
set_property PACKAGE_PIN D13  [get_ports {det_in[52]}]
set_property PACKAGE_PIN F13  [get_ports {det_in[53]}]
set_property PACKAGE_PIN G13  [get_ports {det_in[54]}]
set_property PACKAGE_PIN J13  [get_ports {det_in[55]}]
set_property PACKAGE_PIN K13  [get_ports {det_in[56]}]
set_property PACKAGE_PIN M13  [get_ports {det_in[57]}]
set_property PACKAGE_PIN B12  [get_ports {det_in[58]}]
set_property PACKAGE_PIN C12  [get_ports {det_in[59]}]
set_property PACKAGE_PIN D12  [get_ports {det_in[60]}]
set_property PACKAGE_PIN A11  [get_ports {det_in[61]}]
set_property PACKAGE_PIN B11  [get_ports {det_in[62]}]
set_property PACKAGE_PIN C11  [get_ports {det_in[63]}]
set_property IOSTANDARD LVCMOS33 [get_ports {det_in[*]}]

# ---- 64 emitter enables (FPGA -> 74HCT240 -> P-MOS high-side driver) --------
# emitter_en[i] drives emitter i's P-MOS gate through a 74HCT240 buffer;
# 64 independent channels (no 8x8 matrix).
set_property PACKAGE_PIN V1  [get_ports {emitter_en[0]}]
set_property PACKAGE_PIN V2  [get_ports {emitter_en[1]}]
set_property PACKAGE_PIN V4  [get_ports {emitter_en[2]}]
set_property PACKAGE_PIN V5  [get_ports {emitter_en[3]}]
set_property PACKAGE_PIN V6  [get_ports {emitter_en[4]}]
set_property PACKAGE_PIN V7  [get_ports {emitter_en[5]}]
set_property PACKAGE_PIN V9  [get_ports {emitter_en[6]}]
set_property PACKAGE_PIN V10  [get_ports {emitter_en[7]}]
set_property PACKAGE_PIN V11  [get_ports {emitter_en[8]}]
set_property PACKAGE_PIN V12  [get_ports {emitter_en[9]}]
set_property PACKAGE_PIN V14  [get_ports {emitter_en[10]}]
set_property PACKAGE_PIN V15  [get_ports {emitter_en[11]}]
set_property PACKAGE_PIN V16  [get_ports {emitter_en[12]}]
set_property PACKAGE_PIN V17  [get_ports {emitter_en[13]}]
set_property PACKAGE_PIN U1  [get_ports {emitter_en[14]}]
set_property PACKAGE_PIN U2  [get_ports {emitter_en[15]}]
set_property PACKAGE_PIN U3  [get_ports {emitter_en[16]}]
set_property PACKAGE_PIN U4  [get_ports {emitter_en[17]}]
set_property PACKAGE_PIN U6  [get_ports {emitter_en[18]}]
set_property PACKAGE_PIN U7  [get_ports {emitter_en[19]}]
set_property PACKAGE_PIN U8  [get_ports {emitter_en[20]}]
set_property PACKAGE_PIN U9  [get_ports {emitter_en[21]}]
set_property PACKAGE_PIN U11  [get_ports {emitter_en[22]}]
set_property PACKAGE_PIN U12  [get_ports {emitter_en[23]}]
set_property PACKAGE_PIN U13  [get_ports {emitter_en[24]}]
set_property PACKAGE_PIN U14  [get_ports {emitter_en[25]}]
set_property PACKAGE_PIN U16  [get_ports {emitter_en[26]}]
set_property PACKAGE_PIN U17  [get_ports {emitter_en[27]}]
set_property PACKAGE_PIN U18  [get_ports {emitter_en[28]}]
set_property PACKAGE_PIN T1  [get_ports {emitter_en[29]}]
set_property PACKAGE_PIN T3  [get_ports {emitter_en[30]}]
set_property PACKAGE_PIN T4  [get_ports {emitter_en[31]}]
set_property PACKAGE_PIN T5  [get_ports {emitter_en[32]}]
set_property PACKAGE_PIN T6  [get_ports {emitter_en[33]}]
set_property PACKAGE_PIN T8  [get_ports {emitter_en[34]}]
set_property PACKAGE_PIN T9  [get_ports {emitter_en[35]}]
set_property PACKAGE_PIN T10  [get_ports {emitter_en[36]}]
set_property PACKAGE_PIN T11  [get_ports {emitter_en[37]}]
set_property PACKAGE_PIN T13  [get_ports {emitter_en[38]}]
set_property PACKAGE_PIN T14  [get_ports {emitter_en[39]}]
set_property PACKAGE_PIN T15  [get_ports {emitter_en[40]}]
set_property PACKAGE_PIN T16  [get_ports {emitter_en[41]}]
set_property PACKAGE_PIN T18  [get_ports {emitter_en[42]}]
set_property PACKAGE_PIN R1  [get_ports {emitter_en[43]}]
set_property PACKAGE_PIN R2  [get_ports {emitter_en[44]}]
set_property PACKAGE_PIN R3  [get_ports {emitter_en[45]}]
set_property PACKAGE_PIN R5  [get_ports {emitter_en[46]}]
set_property PACKAGE_PIN R6  [get_ports {emitter_en[47]}]
set_property PACKAGE_PIN R7  [get_ports {emitter_en[48]}]
set_property PACKAGE_PIN R8  [get_ports {emitter_en[49]}]
set_property PACKAGE_PIN R10  [get_ports {emitter_en[50]}]
set_property PACKAGE_PIN R11  [get_ports {emitter_en[51]}]
set_property PACKAGE_PIN R12  [get_ports {emitter_en[52]}]
set_property PACKAGE_PIN R13  [get_ports {emitter_en[53]}]
set_property PACKAGE_PIN R15  [get_ports {emitter_en[54]}]
set_property PACKAGE_PIN R16  [get_ports {emitter_en[55]}]
set_property PACKAGE_PIN R17  [get_ports {emitter_en[56]}]
set_property PACKAGE_PIN R18  [get_ports {emitter_en[57]}]
set_property PACKAGE_PIN P2  [get_ports {emitter_en[58]}]
set_property PACKAGE_PIN P3  [get_ports {emitter_en[59]}]
set_property PACKAGE_PIN P4  [get_ports {emitter_en[60]}]
set_property PACKAGE_PIN P5  [get_ports {emitter_en[61]}]
set_property PACKAGE_PIN P14  [get_ports {emitter_en[62]}]
set_property PACKAGE_PIN P15  [get_ports {emitter_en[63]}]
set_property IOSTANDARD LVCMOS33 [get_ports {emitter_en[*]}]
set_property SLEW FAST [get_ports {emitter_en[*]}]
set_property DRIVE 8   [get_ports {emitter_en[*]}]

# ---- Lock-in modulation reference (1 kHz square wave out) ------------------
# Reference for the host/MW-chain lock-in demodulation of the detector
# photocurrent; gates the 64 parallel counters' I/Q accumulation windows.
set_property PACKAGE_PIN K1   [get_ports mod_sync]
set_property IOSTANDARD LVCMOS33 [get_ports mod_sync]
set_property SLEW SLOW [get_ports mod_sync]
set_property DRIVE 8   [get_ports mod_sync]

# ---- MW RF-switch gate pulse (APPLY_PULSE duration) ------------------------
# Gates the external RF switch between the ADF4351 output and the antenna.
set_property PACKAGE_PIN L1   [get_ports mw_gate]
set_property IOSTANDARD LVCMOS33 [get_ports mw_gate]
set_property SLEW FAST [get_ports mw_gate]
set_property DRIVE 8   [get_ports mw_gate]

# ---- ADF4351 SPI: {LE, CLK, DATA, MUXOUT} -----------------------------------
# spi_pins[0]=LE, [1]=CLK, [2]=DATA (FPGA outputs), [3]=MUXOUT (FPGA input)
set_property PACKAGE_PIN G1  [get_ports {spi_pins[0]}]
set_property PACKAGE_PIN E1  [get_ports {spi_pins[1]}]
set_property PACKAGE_PIN F1  [get_ports {spi_pins[2]}]
set_property PACKAGE_PIN H1  [get_ports {spi_pins[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[3]}]
set_property SLEW FAST [get_ports {spi_pins[1]}]
set_property DRIVE 8   [get_ports {spi_pins[1]}]

# ---- I2C to MCP4725 comparator-threshold DAC --------------------------------
# Open-drain SDA/SCL with 4.7k pull-ups to +3V3 on the board.
set_property PACKAGE_PIN M1   [get_ports i2c_sda]
set_property IOSTANDARD LVCMOS33 [get_ports i2c_sda]
set_property PACKAGE_PIN N1   [get_ports i2c_scl]
set_property IOSTANDARD LVCMOS33 [get_ports i2c_scl]

# ---- 25 MHz reference clock to ADF4351 REF_IN -------------------------------
set_property PACKAGE_PIN B8   [get_ports refclk_out]
set_property IOSTANDARD LVCMOS33 [get_ports refclk_out]
set_property SLEW FAST [get_ports refclk_out]
set_property DRIVE 8   [get_ports refclk_out]

# ---- Configuration (QSPI boot from W25Q128) + JTAG --------------------------
# Dedicated / fixed-function pins (not in top_level's port list):
#   CCLK=E9  D00=K17  D01=K18  D02=L14  D03=M14  FCS_B=L13
#   DONE=P10  INIT_B=P7  PROGRAM_B=P9  M0=P12  M1=P13  M2=P11
#   JTAG: TCK=E10 TMS=E12 TDI=E11 TDO=E13
# BITSTREAM.CONFIG.SPI_BUSWIDTH 4 is set above; the Artix-7 hard SPI controller
# drives these pins automatically at config time -- no per-port pin assignments.

# ---- Timing: clock-domain crossings (constrained for analysis) --------------
# The 1 kHz mod_sync is derived from sys_clk (no CDC). The 64 det_in channels
# are synchronous to sys_clk (comparator outputs registered in the fabric).
# Asynchronous reset is not timed.
set_false_path -through [get_ports reset]
