# =============================================================================
# constraints_qmtech.xdc  --  QSSC firmware ported to the QMTECH board
# -----------------------------------------------------------------------------
# Target: QMTECH XC7A15T core board, FPGA = XC7A15T-1CSG325C
#   (NOT the custom board: that one is XC7A35T-1CSG324C and uses
#    ../constraints.xdc.  This file exists only for tests on the QMTECH.)
#
# Differences from the custom board, and why this file exists:
#   * Clock: 50 MHz on R2 (SG-310SCN oscillator, always enabled), NOT 100 MHz
#     on B8.  top_qmtech.v multiplies up to 100 MHz with an MMCME2_BASE.
#   * Reset: there is no active-high reset button.  SW1 (USER_KEY0, H18) is
#     used instead; on this board it is ACTIVE-LOW with a 4.7k pull-up.
#   * Package is CSG325, not CSG324 -> different ball map.  Several balls
#     used in ../constraints.xdc (B8, A18, B18, G1, A8, A1, B1, C1) are
#     ground or are not user I/O here, which is why they must be re-assigned.
#   * Available I/O: two headers, JP2 (bank 34) and JP3 (bank 15), 87 pins
#     in total, all banks at 3.3 V by default.
#   * On-board LEDs and buttons (see below).
#
# HARDWARE STATE: nothing is cabled off-board yet (no ADF4351, no TLC5947,
# no AD7616, no MCP4921, no external UART).  The peripheral signals are
# assigned to the headers anyway so later wiring does not require rewriting
# this file.
# =============================================================================

# ---- Global options (same as the custom board) -----------------------------
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]

# ---- System clock: 50 MHz on R2 (CLK_50M) ----------------------------------
# R2 is IO_L12N_T1_MRCC_34 -> a clock-capable MRCC pin with a dedicated route
# into the MMCM.  The SG-310SCN oscillator is always enabled (OE pulled to
# 3V3).  Period 20.000 ns.  The MMCM in top_qmtech.v produces 100 MHz and
# Vivado derives the output clock automatically -> no create_generated_clock.
set_property PACKAGE_PIN R2  [get_ports clk_50mhz]
set_property IOSTANDARD LVCMOS33 [get_ports clk_50mhz]
create_clock -name sys_clk_50m -period 20.000 [get_ports clk_50mhz]

# ---- Reset: SW1 / USER_KEY0 (H18), ACTIVE-LOW ------------------------------
# External 4.7k pull-up to 3V3 -> idle high, low while pressed.
# top_qmtech.v inverts it to deliver an active-high reset to top_level.
set_property PACKAGE_PIN H18 [get_ports key0_n]
set_property IOSTANDARD LVCMOS33 [get_ports key0_n]

# SW2 / USER_KEY1 (H17): reserved, active-low, external 4.7k pull-up.
set_property PACKAGE_PIN H17 [get_ports key1_n]
set_property IOSTANDARD LVCMOS33 [get_ports key1_n]

# ---- On-board LEDs: D1 and D4, ACTIVE-LOW -----------------------------------
# Circuit: 3V3 -> 1k -> LED -> FPGA pin.  To light it, drive the pin to 0 V.
#   led0_n -> D1 (USER_LED0, ball D8)
#   led1_n -> D4 (USER_LED1, ball C8)
set_property PACKAGE_PIN D8 [get_ports led0_n]
set_property IOSTANDARD LVCMOS33 [get_ports led0_n]
set_property PACKAGE_PIN C8 [get_ports led1_n]
set_property IOSTANDARD LVCMOS33 [get_ports led1_n]
set_property SLEW SLOW [get_ports {led0_n led1_n}]
set_property DRIVE 8   [get_ports {led0_n led1_n}]

# ---- UART (FT232H in UART mode, once cabled) -------------------------------
# Not wired for now.  JP2 pins 18/19.
set_property PACKAGE_PIN V2 [get_ports rx]
set_property IOSTANDARD LVCMOS33 [get_ports rx]
set_property PACKAGE_PIN U2 [get_ports tx]
set_property IOSTANDARD LVCMOS33 [get_ports tx]
set_property SLEW SLOW [get_ports tx]
set_property DRIVE 8   [get_ports tx]

# ---- ADC bus: 4x AD7616, 9 wires -------------------------------------------
# All on JP3 (bank 15) so the bus stays in one bank and one header.
# Not wired for now.
set_property PACKAGE_PIN F18 [get_ports adc_convst]
set_property PACKAGE_PIN E18 [get_ports adc_sclk]
set_property PACKAGE_PIN D18 [get_ports adc_miso]
set_property PACKAGE_PIN C17 [get_ports adc_busy]
set_property PACKAGE_PIN E17 [get_ports {adc_cs[0]}]
set_property PACKAGE_PIN C18 [get_ports {adc_cs[1]}]
set_property PACKAGE_PIN G15 [get_ports {adc_cs[2]}]
set_property PACKAGE_PIN H16 [get_ports {adc_cs[3]}]
set_property PACKAGE_PIN F15 [get_ports adc_resetn]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_convst adc_sclk adc_miso adc_busy adc_resetn}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_cs[*]}]
set_property SLEW FAST [get_ports adc_sclk]
set_property DRIVE 8   [get_ports adc_sclk]

# ---- Emitters: 3x TLC5947, 4 wires -----------------------------------------
# JP2 pins 3..6 (adjacent, for a 4-wire ribbon).  Not wired for now.
set_property PACKAGE_PIN V8 [get_ports em_sclk]
set_property PACKAGE_PIN V7 [get_ports em_sin]
set_property PACKAGE_PIN U7 [get_ports em_xlat]
set_property PACKAGE_PIN V6 [get_ports em_blank]
set_property IOSTANDARD LVCMOS33 [get_ports {em_sclk em_sin em_xlat em_blank}]
set_property SLEW FAST [get_ports em_sclk]
set_property DRIVE 8   [get_ports em_sclk]

# ---- 1 kHz lock-in modulation reference ------------------------------------
set_property PACKAGE_PIN V3 [get_ports mod_sync]
set_property IOSTANDARD LVCMOS33 [get_ports mod_sync]
set_property SLEW SLOW [get_ports mod_sync]
set_property DRIVE 8   [get_ports mod_sync]

# ---- RF switch gate pulse ---------------------------------------------------
set_property PACKAGE_PIN U4 [get_ports mw_gate]
set_property IOSTANDARD LVCMOS33 [get_ports mw_gate]
set_property SLEW FAST [get_ports mw_gate]
set_property DRIVE 8   [get_ports mw_gate]

# ---- Gradient DAC MCP4921 ---------------------------------------------------
# JP2 pins 7..10.
set_property PACKAGE_PIN T7 [get_ports grad_cs]
set_property PACKAGE_PIN R7 [get_ports grad_sclk]
set_property PACKAGE_PIN R6 [get_ports grad_mosi]
set_property PACKAGE_PIN T5 [get_ports grad_en]
set_property IOSTANDARD LVCMOS33 [get_ports {grad_cs grad_sclk grad_mosi grad_en}]
set_property SLEW FAST [get_ports grad_sclk]
set_property DRIVE 8   [get_ports grad_sclk]

# ---- ADF4351 SPI ------------------------------------------------------------
# {LE, CLK, DATA, MUXOUT}; MUXOUT is an input.  JP2 pins 11..14.
set_property PACKAGE_PIN R5 [get_ports {spi_pins[0]}]
set_property PACKAGE_PIN T4 [get_ports {spi_pins[1]}]
set_property PACKAGE_PIN U6 [get_ports {spi_pins[2]}]
set_property PACKAGE_PIN U5 [get_ports {spi_pins[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_pins[*]}]
set_property SLEW FAST [get_ports {spi_pins[1]}]
set_property DRIVE 8   [get_ports {spi_pins[1]}]

# ---- 25 MHz reference to the ADF4351 REF_IN --------------------------------
# Not wired for now; assigned so this file does not have to be rewritten later.
set_property PACKAGE_PIN V4 [get_ports refclk_out]
set_property IOSTANDARD LVCMOS33 [get_ports refclk_out]
set_property SLEW FAST [get_ports refclk_out]
set_property DRIVE 8   [get_ports refclk_out]

# ---- Configuration: QSPI boot from N25Q64 ----------------------------------
# M0:M1:M2 = 1:0:0 -> Master SPI.  CCLK/FCS_B/D00..D03 are dedicated pins and
# are NOT assigned here.  The N25Q64 is wired for quad mode (D02->WP#,
# D03->HOLD#), hence SPI_BUSWIDTH 4 above.

# ---- Timing ----------------------------------------------------------------
set_false_path -through [get_ports key0_n]
set_false_path -from [get_ports rx]
set_false_path -from [get_ports adc_busy]
set_false_path -from [get_ports {spi_pins[3]}]

# ---- Timing: reset synchroniser output --------------------------------------
# rst_sync_reg[1] fans out to the synchronous reset of thousands of flops.
# Its de-assertion is already synchronised to clk_100m by the two-stage
# synchroniser in top_level.v, so there is no setup relation to enforce -- the
# net is constant except at the single instant reset is released.  Left
# unconstrained this one net produced 25 of the 60 worst paths (22.6 ns, 61 %
# of it pure routing to a huge fanout).
set_false_path -from [get_cells -hier -regexp {.*rst_sync_reg.*}]

# NOTE on the other long paths: adf4351_ctrl (tgt_reg -> r0_r) and
# command_parser (arg_vals -> pll_phase) latch a combinational divide ONE
# cycle after its inputs change.  Those are fixed in the RTL by
# patch_pll_wait.py / patch_div_seq.py rather than hidden with a false path --
# a false path there would let garbage through.

# ---- Multi-cycle: the RTL now waits, but Vivado cannot see that -----------
# Timing is derived from the clock relationship only: for same-clock endpoints
# it ALWAYS assumes 1 cycle, no matter how many states the FSM spends in
# between.  Adding ST_SETTLE / S_FMT_WAIT makes the hardware correct but does
# not move WNS by itself -- these constraints are what tell Vivado the real
# window.
#
# The numbers below are deliberately CONSERVATIVE (fewer cycles than the RTL
# actually uses), so if the count is ever wrong the error direction is "Vivado
# was stricter than reality" and the hardware still works:
#
#   adf4351_ctrl:  ST_IDLE latches tgt_reg at edge T, ST_SETTLE holds 8
#                  cycles, ST_CALC captures r0..r5 at edge T+10 -> window 10
#                  cycles (path measures 52.3 ns).  Claim 8 (=80 ns).
#   command_parser: last-arg write at T, S_FMT_WAIT (56), dispatch captures
#                  pll_phase at edge T+58 -> window 58 cycles (path 39.7 ns).
#                  Claim 20 (=200 ns).
set_multicycle_path -setup 8 \
    -from [get_cells -hier -regexp {.*tgt_reg_reg.*}] \
    -to   [get_cells -hier -regexp {.*r[0-5]_r_reg.*}]
set_multicycle_path -hold 7 \
    -from [get_cells -hier -regexp {.*tgt_reg_reg.*}] \
    -to   [get_cells -hier -regexp {.*r[0-5]_r_reg.*}]

set_multicycle_path -setup 20 \
    -from [get_cells -hier -regexp {.*arg_vals_reg.*}] \
    -to   [get_cells -hier -regexp {.*pll_phase_reg.*}]
set_multicycle_path -hold 19 \
    -from [get_cells -hier -regexp {.*arg_vals_reg.*}] \
    -to   [get_cells -hier -regexp {.*pll_phase_reg.*}]

# Same window applies to the pre-divided RF gate duration: patch_gate_cmd.py
# turns `mw_gate_ns <= arg_vals[3]` into `<= arg_vals[3]/10`, and that write
# happens in S_DISPATCH behind the same 56-cycle S_FMT_WAIT.
set_multicycle_path -setup 20 \
    -from [get_cells -hier -regexp {.*arg_vals_reg.*}] \
    -to   [get_cells -hier -regexp {.*mw_gate_ns_reg.*}]
set_multicycle_path -hold 19 \
    -from [get_cells -hier -regexp {.*arg_vals_reg.*}] \
    -to   [get_cells -hier -regexp {.*mw_gate_ns_reg.*}]

# Same again for seq_dly_inc <= arg_vals[3] / 10 (RUN_SEQUENCE delay step).
# This is a constant divide -- cheap, but still 12.84 ns of path -- and it is
# written in the CMD_SEQ branch of S_DISPATCH, i.e. behind the same wait.
# All 36 reads of arg_vals in command_parser.v live between lines 1420-1747,
# i.e. inside S_DISPATCH (which starts at line 1412), so the 56-cycle window
# applies to every one of them.
set_multicycle_path -setup 20 \
    -from [get_cells -hier -regexp {.*arg_vals_reg.*}] \
    -to   [get_cells -hier -regexp {.*seq_dly_inc_reg.*}]
set_multicycle_path -hold 19 \
    -from [get_cells -hier -regexp {.*arg_vals_reg.*}] \
    -to   [get_cells -hier -regexp {.*seq_dly_inc_reg.*}]
