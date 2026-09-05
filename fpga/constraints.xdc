# =============================================================================
# constraints.xdc  --  QSSC single-board parallel control
# Target: Xilinx Artix-7 XC7A35T-1CSG324C (CSG324, 0.8 mm BGA, 324 balls)
# ----------------------------------------------------------------------------
# Pin assignments are derived from the datasheet-correct CSG324 ball map
# (.tmpwork/fpga_csg324_map.json, parsed from the real KiCad Artix-7 symbol
# and verified against UG475 / DS181). All six VCCO banks are powered at
# 3.3 V (LVCMOS33). clk_100mhz is on a clock-capable (MRCC) pin.
#
# Parallel architecture: 64 emitters driven by 3x TLC5947 constant-current
# LED drivers over a 4-wire SPI (em_sclk/sin/xlat/blank), and 64 photo-
# luminescence-intensity channels read in parallel via 4x AD7616 16-bit
# ADCs over a 9-wire SPI (adc_convst/sclk/miso/busy/cs[3:0]/resetn).
# No analog MUX, no row/col matrix, no XADC, no photon counting.
# =============================================================================

# ---- Global options ---------------------------------------------------------
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]

# ---- 100 MHz system clock (XO, Abracon ASE) --------------------------------
# clk_100mhz -> B8, the P-side of an MRCC pair (IO_L..P.._MRCC_):
# per UG472 the single-ended clock INPUT takes the dedicated, delay-
# compensated route to the MMCM (no CLOCK_DEDICATED_ROUTE FALSE needed).
set_property PACKAGE_PIN B8   [get_ports clk_100mhz]
set_property IOSTANDARD LVCMOS33 [get_ports clk_100mhz]
create_clock -name sys_clk -period 10.000 [get_ports clk_100mhz]

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

# ---- 4x AD7616 parallel ADC readout (FPGA <- 64 TIA outputs) ---------------
# 9-wire SPI bus: adc_convst (shared conversion start), adc_sclk (shared
# clock), adc_miso (shared SDOA, per-chip CS selects the driver), adc_busy
# (chip0 only -- push-pull, all 4 finish a simultaneous CONVST together),
# adc_cs[3:0] (per-chip select), adc_resetn (shared reset, active-low). The
# +/-5V range is HW-pin-strapped on each AD7616 (no MOSI / config writes).
set_property PACKAGE_PIN A18  [get_ports adc_convst]
set_property PACKAGE_PIN B18  [get_ports adc_sclk]
set_property PACKAGE_PIN D18  [get_ports adc_miso]
set_property PACKAGE_PIN E18  [get_ports adc_busy]
set_property PACKAGE_PIN M18  [get_ports adc_resetn]
set_property PACKAGE_PIN F18  [get_ports {adc_cs[0]}]
set_property PACKAGE_PIN G18  [get_ports {adc_cs[1]}]
set_property PACKAGE_PIN J18  [get_ports {adc_cs[2]}]
set_property PACKAGE_PIN L18  [get_ports {adc_cs[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports adc_convst]
set_property IOSTANDARD LVCMOS33 [get_ports adc_sclk]
set_property IOSTANDARD LVCMOS33 [get_ports adc_miso]
set_property IOSTANDARD LVCMOS33 [get_ports adc_busy]
set_property IOSTANDARD LVCMOS33 [get_ports adc_resetn]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_cs[*]}]
set_property SLEW FAST [get_ports adc_sclk]
set_property DRIVE 8   [get_ports adc_sclk]

# ---- TLC5947 constant-current LED driver (FPGA -> 3x TLC5947, 64 emitters) --
# 4-wire interface: em_sclk (shift clock), em_sin (data in), em_xlat (latch),
# em_blank (global blank/PWM gate). 3x TLC5947 daisy-chain SIN->SOUT for 72
# channels (64 used); each emitter cathode is a TLC5947 OUTn, anode = +5V.
# 12-bit PWM grayscale per channel -> parallel multi-emitter drive.
set_property PACKAGE_PIN V1   [get_ports em_sclk]
set_property PACKAGE_PIN V2   [get_ports em_sin]
set_property PACKAGE_PIN V4   [get_ports em_xlat]
set_property PACKAGE_PIN V5   [get_ports em_blank]
set_property IOSTANDARD LVCMOS33 [get_ports em_sclk]
set_property IOSTANDARD LVCMOS33 [get_ports em_sin]
set_property IOSTANDARD LVCMOS33 [get_ports em_xlat]
set_property IOSTANDARD LVCMOS33 [get_ports em_blank]
set_property SLEW FAST [get_ports em_sclk]
set_property DRIVE 8   [get_ports em_sclk]

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

# ---- 25 MHz reference clock to ADF4351 REF_IN -------------------------------
set_property PACKAGE_PIN A8   [get_ports refclk_out]
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
# The 1 kHz mod_sync is derived from sys_clk (no CDC). The AD7616 SPI bus
# (adc_*) is a synchronous master interface clocked by adc_sclk from sys_clk.
# Asynchronous reset is not timed.
set_false_path -through [get_ports reset]
