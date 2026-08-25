# =============================================================================
# constraints.xdc  --  QSSC control board
# Target: Xilinx Artix-7 XC7A35T-1CPG236C (CSG236, 0.8 mm BGA, 236 balls)
# ----------------------------------------------------------------------------
# Pin assignments are PLAUSIBLE defaults for the CPG236 package and MUST be
# verified against the official Xilinx XC7A35T-1CPG236C pinout (UG475 /
# Vivado Part-Pinout view) and the actual board schematic before routing.
# All banks below are assumed to be powered at 3.3 V (VCCO = 3.3 V, LVCMOS33).
#
# This board has NO camera. The chip is read on-chip: an 8x8 CQD emitter array
# (driven via an external higher-voltage driver enabled by the FPGA crossbar)
# pumps each site and an on-chip graphene/MoS2 photodetector array is read
# through an external TIA + analog MUX into the Artix-7 XADC, lock-in
# demodulated at 1 kHz. The former OV7670 SCCB/pixel pins are reused for the
# detector MUX select and the lock-in modulation reference.
# =============================================================================

# ---- Global options ---------------------------------------------------------
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]

# ---- 100 MHz system clock (TCXO) --------------------------------------------
# Single-ended 100 MHz TCXO into a CCIO pin of bank 34.
set_property PACKAGE_PIN G21   [get_ports clk_100mhz]
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
# Slew / drive strength tuned for short board-level UART traces
set_property SLEW SLOW  [get_ports tx]
set_property DRIVE 8    [get_ports tx]

# ---- 8x8 on-chip CQD emitter array: row anode-enables (active high) ----------
# The FPGA enables an external higher-voltage emitter driver (4-8 V / 2-20 mA
# per pixel); these 8 row enables select the anode of one CQD pixel column-pair.
set_property PACKAGE_PIN K16  [get_ports {emitter_rows[0]}]
set_property PACKAGE_PIN K17  [get_ports {emitter_rows[1]}]
set_property PACKAGE_PIN L18  [get_ports {emitter_rows[2]}]
set_property PACKAGE_PIN M16  [get_ports {emitter_rows[3]}]
set_property PACKAGE_PIN M17  [get_ports {emitter_rows[4]}]
set_property PACKAGE_PIN M18  [get_ports {emitter_rows[5]}]
set_property PACKAGE_PIN N16  [get_ports {emitter_rows[6]}]
set_property PACKAGE_PIN N18  [get_ports {emitter_rows[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {emitter_rows[*]}]
set_property SLEW FAST [get_ports {emitter_rows[*]}]
set_property DRIVE 8   [get_ports {emitter_rows[*]}]

# ---- 8x8 on-chip CQD emitter array: column cathode-enables (active low) -------
set_property PACKAGE_PIN P16  [get_ports {emitter_cols[0]}]
set_property PACKAGE_PIN K18  [get_ports {emitter_cols[1]}]
set_property PACKAGE_PIN R16  [get_ports {emitter_cols[2]}]
set_property PACKAGE_PIN R17  [get_ports {emitter_cols[3]}]
set_property PACKAGE_PIN R18  [get_ports {emitter_cols[4]}]
set_property PACKAGE_PIN T16  [get_ports {emitter_cols[5]}]
set_property PACKAGE_PIN T17  [get_ports {emitter_cols[6]}]
set_property PACKAGE_PIN T18  [get_ports {emitter_cols[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {emitter_cols[*]}]
set_property SLEW FAST [get_ports {emitter_cols[*]}]
set_property DRIVE 8   [get_ports {emitter_cols[*]}]

# ---- On-chip graphene/MoS2 photodetector: analog MUX select -----------------
# 6 select lines to the external analog MUX that routes one detector
# electrode's TIA voltage to the XADC aux input. Reuses the freed OV7670 SCCB
# / XCLK / pixel pins (same package pins, repurposed — verify on the schematic).
set_property PACKAGE_PIN J17  [get_ports {det_mux_sel[0]}]
set_property PACKAGE_PIN J16  [get_ports {det_mux_sel[1]}]
set_property PACKAGE_PIN H17  [get_ports {det_mux_sel[2]}]
set_property PACKAGE_PIN G18  [get_ports {det_mux_sel[3]}]
set_property PACKAGE_PIN G17  [get_ports {det_mux_sel[4]}]
set_property PACKAGE_PIN F18  [get_ports {det_mux_sel[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {det_mux_sel[*]}]
set_property SLEW SLOW [get_ports {det_mux_sel[*]}]
set_property DRIVE 8   [get_ports {det_mux_sel[*]}]

# ---- Lock-in modulation reference (1 kHz square wave out) ------------------
# Reference for the host/MW-chain lock-in demodulation of the detector
# photocurrent. Reuses a freed OV7670 pixel-data pin.
set_property PACKAGE_PIN F16  [get_ports mod_sync]
set_property IOSTANDARD LVCMOS33 [get_ports mod_sync]
set_property SLEW SLOW [get_ports mod_sync]
set_property DRIVE 8   [get_ports mod_sync]

# ---- MW RF-switch gate pulse (APPLY_PULSE duration) ------------------------
# Gates the external RF switch between the ADF4351 output and the antenna for
# the requested pulse duration (mw_gate_ns). The ADF4351 sets carrier phase
# (R1) + output power (R4); this pin times the pulse. Reuses a freed OV7670
# pixel-data pin. Verify the pin on the schematic before routing.
set_property PACKAGE_PIN E16  [get_ports mw_gate]
set_property IOSTANDARD LVCMOS33 [get_ports mw_gate]
set_property SLEW FAST [get_ports mw_gate]
set_property DRIVE 8   [get_ports mw_gate]

# ---- Artix-7 XADC (12-bit, up to 1 MSPS) ------------------------------------
# The XADC is a hard block of the XC7A35T-1CPG236C. Its analog aux input pair
# (VauxP0 / VauxN0 on the CPG236 package) and the analog supply pins
# (VCCADC, GNDADC, VREFP, VREFN) are FIXED-PACKAGE pins: they are NOT assigned
# here via get_ports (top_level models the XADC behaviourally and has no
# analog port). In the real build, instantiate the XADC wizard and let it
# generate the aux-pin constraints; route the TIA output into VauxP0/VauxN0.
# Verify the aux-pair assignment against UG475 before routing.

# ---- ADF4351 SPI: {LE, CLK, DATA, MUXOUT} -----------------------------------
# spi_pins[0]=LE, [1]=CLK, [2]=DATA (FPGA outputs), [3]=MUXOUT (FPGA input)
set_property PACKAGE_PIN D17  [get_ports {spi_pins[0]}]
set_property PACKAGE_PIN D18  [get_ports {spi_pins[1]}]
set_property PACKAGE_PIN D19  [get_ports {spi_pins[2]}]
set_property PACKAGE_PIN E19  [get_ports {spi_pins[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[3]}]
set_property SLEW FAST [get_ports {spi_pins[1]}]
set_property DRIVE 8   [get_ports {spi_pins[1]}]

# ---- 25 MHz reference clock to ADF4351 REF_IN -------------------------------
set_property PACKAGE_PIN C18  [get_ports refclk_out]
set_property IOSTANDARD LVCMOS33 [get_ports refclk_out]
set_property SLEW FAST [get_ports refclk_out]
set_property DRIVE 8   [get_ports refclk_out]

# ---- Timing: clock-domain crossings (constrained for analysis) -------------
# The on-chip readout has no external pixel clock, so the old cam_pclk false
# paths are removed. The 1 kHz mod_sync is derived from sys_clk (no CDC).
# Asynchronous reset is not timed.
set_false_path -through [get_ports reset]