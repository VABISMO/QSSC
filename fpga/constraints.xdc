# =============================================================================
# constraints.xdc  --  QSSC control board
# Target: Xilinx Artix-7 XC7A35T-1CPG236C (CSG236, 0.8 mm BGA, 236 balls)
# ----------------------------------------------------------------------------
# Pin assignments are PLAUSIBLE defaults for the CPG236 package and MUST be
# verified against the official Xilinx XC7A35T-1CPG236C pinout (UG475 /
# Vivado Part-Pinout view) and the actual board schematic before routing.
# All banks below are assumed to be powered at 3.3 V (VCCO = 3.3 V, LVCMOS33).
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

# ---- 8x8 VCSEL array: row enables (active high) -----------------------------
set_property PACKAGE_PIN K16  [get_ports {laser_rows[0]}]
set_property PACKAGE_PIN K17  [get_ports {laser_rows[1]}]
set_property PACKAGE_PIN L18  [get_ports {laser_rows[2]}]
set_property PACKAGE_PIN M16  [get_ports {laser_rows[3]}]
set_property PACKAGE_PIN M17  [get_ports {laser_rows[4]}]
set_property PACKAGE_PIN M18  [get_ports {laser_rows[5]}]
set_property PACKAGE_PIN N16  [get_ports {laser_rows[6]}]
set_property PACKAGE_PIN N18  [get_ports {laser_rows[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {laser_rows[*]}]
set_property SLEW FAST [get_ports {laser_rows[*]}]
set_property DRIVE 8   [get_ports {laser_rows[*]}]

# ---- 8x8 VCSEL array: column selects (active low sinks) ---------------------
set_property PACKAGE_PIN P16  [get_ports {laser_cols[0]}]
set_property PACKAGE_PIN K18  [get_ports {laser_cols[1]}]
set_property PACKAGE_PIN R16  [get_ports {laser_cols[2]}]
set_property PACKAGE_PIN R17  [get_ports {laser_cols[3]}]
set_property PACKAGE_PIN R18  [get_ports {laser_cols[4]}]
set_property PACKAGE_PIN T16  [get_ports {laser_cols[5]}]
set_property PACKAGE_PIN T17  [get_ports {laser_cols[6]}]
set_property PACKAGE_PIN T18  [get_ports {laser_cols[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {laser_cols[*]}]
set_property SLEW FAST [get_ports {laser_cols[*]}]
set_property DRIVE 8   [get_ports {laser_cols[*]}]

# ---- OV7670 SCCB (I2C-like, open-drain with 4.7k pull-ups) ------------------
set_property PACKAGE_PIN J17  [get_ports scl]
set_property IOSTANDARD LVCMOS33 [get_ports scl]
set_property PULLUP TRUE [get_ports scl]
set_property PACKAGE_PIN J16  [get_ports sda]
set_property IOSTANDARD LVCMOS33 [get_ports sda]
set_property PULLUP TRUE [get_ports sda]

# ---- OV7670 XCLK (FPGA -> camera, ~25 MHz) ----------------------------------
set_property PACKAGE_PIN H17  [get_ports xclk]
set_property IOSTANDARD LVCMOS33 [get_ports xclk]
set_property SLEW FAST [get_ports xclk]
set_property DRIVE 8   [get_ports xclk]

# ---- OV7670 pixel interface -------------------------------------------------
# PCLK should land on a clock-capable input for safe CDC into the BRAM writer.
set_property PACKAGE_PIN G18  [get_ports pclk]
set_property IOSTANDARD LVCMOS33 [get_ports pclk]
create_clock -name cam_pclk -period 41.667 [get_ports pclk]
set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets pclk_IBUF]

set_property PACKAGE_PIN G17  [get_ports vsync]
set_property IOSTANDARD LVCMOS33 [get_ports vsync]
set_property PACKAGE_PIN F18  [get_ports href]
set_property IOSTANDARD LVCMOS33 [get_ports href]

# Camera pixel data bus
set_property PACKAGE_PIN E16  [get_ports {d[0]}]
set_property PACKAGE_PIN E17  [get_ports {d[1]}]
set_property PACKAGE_PIN E18  [get_ports {d[2]}]
set_property PACKAGE_PIN F16  [get_ports {d[3]}]
set_property PACKAGE_PIN F17  [get_ports {d[4]}]
set_property PACKAGE_PIN F19  [get_ports {d[5]}]
set_property PACKAGE_PIN G16  [get_ports {d[6]}]
set_property PACKAGE_PIN G19  [get_ports {d[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {d[*]}]

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
# PCLK (camera) to sys_clk is asynchronous; mark the CDC paths false.
set_false_path -from [get_clocks cam_pclk] -to [get_clocks sys_clk]
set_false_path -from [get_clocks sys_clk] -to [get_clocks cam_pclk]
# Asynchronous reset is not timed.
set_false_path -through [get_ports reset]