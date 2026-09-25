"""Placement: the per-board component builders + PCB (mm) assigners.

Each ``build_*`` function appends one board's parts to the shared
``COMPONENTS`` buffer (via :func:`add`); each ``assign_pcb_positions*``
function sets the ``pcb_x``/``pcb_y`` footprint coordinates on those parts.
``build_gradient_board`` calls ``build_main_board(grad=True)`` first so the
extended board is the base main board plus the gradient coil + DAC driver.
"""
from __future__ import annotations

from .component_lib import COMPONENTS, add, add_btb_connector
from .symbols import (
    GVA84_NETS, chip_net_map_det, chip_net_map_em, fpga_net_map,
)

__all__ = ['assign_pcb_positions', 'assign_pcb_positions_gradient', 'assign_pcb_positions_head', 'build_detector_analog', 'build_detector_head', 'build_emitter_driver', 'build_gradient_board', 'build_main_board']


def build_main_board(grad=False):
    # ---- place components on the schematic grid -------------------------------
    # Power chain (left column)
    add("J2", "qssc:Barrel_Jack", "5V Barrel", "Connector_BarrelJack:BarrelJack_CUI_PJ-102AH_Horizontal",
        30, 30, pins={"1": "+5V_IN", "2": "GND"})
    # +5V_IN input bulk decoupling: the barrel-jack input rail (J2.1 -> F1.1) had
    # NO decoupling cap -- 4 loads (J2, F1, USB diode-OR D4, PWR_FLAG) and 0 caps.
    # Per standard practice a 10 uF bulk cap at the barrel input absorbs source
    # ripple / hot-plug transient before the polyfuse + regulators.
    add("C_in", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
        43, 38, pins={"1": "+5V_IN", "2": "GND"})
    add("F1", "qssc:Polyfuse", "2A PTC", "Fuse:Fuse_1206_3216Metric",
        55, 30, pins={"1": "+5V_IN", "2": "+5V"})
    add("REG1", "qssc:AMS1117-3.3", "AMS1117-3.3", "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
        80, 30, pins={"1": "GND", "2": "+3V3", "3": "+5V"})
    add("REG2", "qssc:AMS1117-1.8", "AMS1117-1.8", "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
        80, 50, pins={"1": "GND", "2": "+1V8", "3": "+5V"})
    add("REG3", "qssc:AP2127K-1.0", "AP2127K-1.0", "Package_TO_SOT_SMD:SOT-23-5",
        80, 70, pins={"1": "+5V", "2": "GND", "3": "+5V", "5": "+1V0"},
        nc_pins=["4"])  # pin 4 = NC on the fixed AP2127K-1.0 (no bypass pin)
    # REG3 (AP2127K-1.0) decoupling: 10uF in + 10uF/100nF out (per datasheet; no
    # bypass cap -- pin 4 is NC, the prior C41 10nF on a floating pad is removed).
    add("C42", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
        68, 70, pins={"1": "+5V", "2": "GND"})
    add("C43", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
        92, 78, pins={"1": "+1V0", "2": "GND"})
    add("C44", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        80, 78, pins={"1": "+1V0", "2": "GND"})
    # REG4 was an AMS1117-5.0 LDO from +5V -> +5V_RF, but the AMS1117 needs ~6.5 V
    # in for a 5.0 V out -- fed from the 5 V barrel rail it sat permanently in
    # dropout and delivered ~3.7-4 V to the RF amp (needs 4.8-5.2 V). The +5V
    # rail is already regulated 5 V, so +5V_RF needs NO regulation -- only a ferrite
    # bead + pi filter to keep the RF section's noise off the main +5V rail (and vice
    # versa). FB1 is a 600-ohm@100MHz bead; C45/C46 buffer the +5V side, C47/C48 the
    # +5V_RF side (10uF bulk + 100nF HF each).
    add("FB1", "qssc:L", "600Ω@100MHz", "Inductor_SMD:L_0603_1608Metric",
        80, 90, pins={"1": "+5V", "2": "+5V_RF"})
    add("C45", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
        68, 90, pins={"1": "+5V", "2": "GND"})
    add("C46", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        68, 98, pins={"1": "+5V", "2": "GND"})
    add("C47", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
        92, 90, pins={"1": "+5V_RF", "2": "GND"})
    add("C48", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        92, 98, pins={"1": "+5V_RF", "2": "GND"})
    # ---- +12V_EMIT emitter anode supply (separate 12 V LED input) -------------
    # The 64 CQD emitters share a common anode (EM_ANODE -> +12V_EMIT). Per
    # docs/FABRICATION_PROTOCOL.md the CQD EL turn-on is 4-8 V (visible by ~10 V)
    # at 2-20 mA/pixel, so a +5V rail is insufficient headroom for the TLC5947
    # constant-current sink. The TLC5947 OUT pins tolerate 33 V independent of VCC,
    # so a standard separate 12 V supply gives guaranteed compliance for any CQD
    # V_f up to ~11 V. J4 is a 2.1 mm barrel (12 V wall wart); F2 a 1 A PTC; C85
    # 22 uF bulk + C86 100 nF HF decouple the rail at the die anode pad fanout.
    add("J4", "qssc:Barrel_Jack", "12V LED", "Connector_BarrelJack:BarrelJack_CUI_PJ-102AH_Horizontal",
        30, 60, pins={"1": "+12V_EMIT_IN", "2": "GND"})
    add("F2", "qssc:Polyfuse", "1A PTC", "Fuse:Fuse_1206_3216Metric",
        55, 60, pins={"1": "+12V_EMIT_IN", "2": "+12V_EMIT"})
    add("C85", "qssc:C", "22uF", "Capacitor_SMD:C_0805_2012Metric",
        68, 60, pins={"1": "+12V_EMIT", "2": "GND"})
    add("C86", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        68, 68, pins={"1": "+12V_EMIT", "2": "GND"})
    add("#FLG7", "power:PWR_FLAG", "PWR_FLAG", "", 45, 60, pins={"1": "+12V_EMIT_IN"})
    add("#FLG8", "power:PWR_FLAG", "PWR_FLAG", "", 70, 60, pins={"1": "+12V_EMIT"})
    add("R70", "qssc:R", "330", "Resistor_SMD:R_0603_1608Metric",
        110, 30, pins={"1": "+3V3", "2": "LED_ANODE"})
    add("LED1", "qssc:LED", "Green", "LED_SMD:LED_0603_1608Metric",
        110, 45, pins={"1": "GND", "2": "LED_ANODE"})

    # FPGA (center) -- XC7A35T-1CSG324C (324-ball 0.8 mm BGA).  PCB placement is
    # overridden to (128,55) in assign_pcb_positions() to clear the die courtyard.
    add("U1",
        "qssc:XC7A35T_CSG324_GRAD" if grad else "qssc:XC7A35T_CSG324",
        "XC7A35T-1CSG324C", "Package_BGA:Xilinx_CSG324",
        200, 70, pins=fpga_net_map(grad=grad))

    # Config flash W25Q128 (QSPI x4 boot).  FPGA dedicated config balls:
    #   D00->SPI_D0, D01->SPI_D1, D02->SPI_D2, D03->SPI_D3, FCS_B->SPI_CS,
    #   CCLK->SPI_CCLK, DONE->CONF_DONE, INIT_B->CONF_INIT, PROGRAM_B->CONF_PROG.
    add("U8", "qssc:W25Q128JVS", "W25Q128JVSIQ", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
        140, 95, pins={"1": "SPI_CS", "2": "SPI_D1", "3": "SPI_D2", "4": "GND",
                       "5": "SPI_D0", "6": "SPI_CCLK", "7": "SPI_D3", "8": "+3V3"})
    # Config pull-ups (open-drain config pins need a pull-up to +3V3 to boot).
    add("R75", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric",
        120, 90, pins={"1": "+3V3", "2": "CONF_DONE"})
    add("R76", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric",
        120, 95, pins={"1": "+3V3", "2": "CONF_INIT"})
    add("R77", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric",
        120, 100, pins={"1": "+3V3", "2": "CONF_PROG"})
    # XADC reference decoupling: VREFP decoupled to GND (internal reference mode).
    add("C40", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        215, 90, pins={"1": "VREFP", "2": "GND"})

    # Clock
    add("Y1", "qssc:ASE_osc", "100MHz XO", "Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm",
        200, 30, pins={"1": "+3V3", "2": "GND", "3": "clk_100mhz", "4": "+3V3"})

    # USB-UART
    # FT232 USBDP/USBDM (pins 15/16) -> USB_DP_F/USB_DM_F -> R78/R79 27R series
    # damping -> USB_DP/USB_DM (ESD diodes D1/D2 + USB-C).  Series R dampens the
    # USB edge reflections; ESD stays on the connector side (correct clamp point).
    # FT232RL pin-tie rationale (verified vs FTDI FT232R datasheet + KiCad
    # Interface_USB:FT232RL symbol, SSOP-28 -- the symbol omits the 2 NC pads 8 & 24):
    #   pin  1 TXD   -> UART_RX   (FT232 *output* -> FPGA RX ball P17; net named from
    #                             FPGA side, so the TXD/RXD crossover is correct)
    #   pin  5 RXD   -> UART_TX   (FPGA TX ball P18 -> FT232 *input*)
    #   pin 15 USBDP -> USB_DP_F  pin 16 USBDM -> USB_DM_F (series-damped via R78/R79)
    #   pin  4 VCCIO -> +3V3 (I/O supply)    pin 17 3V3OUT -> 3V3OUT (reg o/p, dec C55)
    #   pin 20 VCC   -> +3V3 (the ONLY core supply pad on the SSOP-28)
    #   pin 19 RESET#-> +3V3 (internal pull-up ok)
    #   pin 25 AGND  -> GND (real analog ground; FT232R analog/regulator return)
    #   pins 7/18/21/26 GND -> GND (digital + TEST bond-pad grounds tied)
    #   pin 23 CBUS0 -> NC (configurable CBUS, default TXLED#; unused -> float)
    #   pin 24 NC    -> NC (no internal bond on the SSOP-28; NOT a 2nd VCC pad)
    #   pins 2/3/6/9-14/22/27/28 -> NC (modem control, crystal, LED drive, TEST):
    #       unused per datasheet; emitted as (no_connect) below.
    add("J1", "qssc:FT232RL", "FT232RL", "Package_SO:SSOP-28_5.3x10.2mm_P0.65mm",
        300, 70, pins={"1": "UART_RX", "5": "UART_TX", "15": "USB_DP_F", "16": "USB_DM_F",
                       "4": "+3V3", "17": "3V3OUT", "20": "+3V3",
                       "7": "GND", "18": "GND", "21": "GND", "25": "GND",
                       "19": "+3V3", "26": "GND"},
        nc_pins=["2", "3", "6", "9", "10", "11", "12", "13", "14", "22", "23", "24", "27", "28"])
    # USB-C VBUS is on its own VBUS_USB net; D_USB (Schottky, below) passes
    # USB -> +5V_IN only, blocking the barrel wall adapter from back-feeding +5V
    # into the USB host.  Barrel stays direct so the +5V rail keeps the full 5.0 V
    # the GVA-84+ RF amp needs (4.8-5.2 V abs max); only USB-only power (config,
    # no RF) takes the Schottky drop.
    add("J10", "qssc:USB_C_Receptacle", "USB-C", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
        360, 70, pins={"A4": "VBUS_USB", "A9": "VBUS_USB", "B4": "VBUS_USB", "B9": "VBUS_USB",
                       "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND",
                       "A6": "USB_DP", "B6": "USB_DP", "A7": "USB_DM", "B7": "USB_DM",
                       "A5": "CC1", "B5": "CC2", "A8": "GND", "B8": "GND", "S1": "GND"})
    add("R71", "qssc:R", "5.1k", "Resistor_SMD:R_0603_1608Metric", 360, 100, pins={"1": "CC1", "2": "GND"})
    add("R72", "qssc:R", "5.1k", "Resistor_SMD:R_0603_1608Metric", 360, 110, pins={"1": "CC2", "2": "GND"})

    # ADF4351 PLL + loop filter + GVA-84+ RF amp + SMA
    add("U3", "qssc:ADF4351", "ADF4351BCPZ", "Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm",
        300, 150, pins={"1": "SPI_CLK", "2": "SPI_DATA", "3": "SPI_LE", "4": "+3V3",
                        "6": "+3V3", "7": "LF_CP", "9": "GND", "10": "+3V3", "11": "GND",
                        "12": "RF_OUT", "16": "+3V3", "17": "+3V3", "18": "GND", "20": "LF_OUT",
                        "21": "GND", "22": "RSET", "23": "ADF_VCOM", "24": "VREF", "26": "+3V3",
                        "27": "GND", "28": "+3V3", "29": "refclk_25m", "30": "ADF_MUXOUT",
                        "31": "GND", "32": "+3V3", "33": "GND", "8": "GND",
                        "19": "ADF_TEMP",
                        # The QFN-32-1EP footprint models the 3.45x3.45 mm exposed
                        # pad as ONE named pad "33" (netted GND above) PLUS a 3x3
                        # grid of 9 numberless "" sub-pads (paste/copper apertures
                        # under the EP). Per ADF4351 datasheet the EP MUST be tied to
                        # GND (thermal + electrical return); net the sub-pads GND too
                        # so none of the EP copper floats. fp_net_pads matches "".
                        "": "GND"},
        nc_pins=["5", "13", "14", "15", "25"])
    # ADF4351 pin-tie rationale (verified vs ADI ADF4351 datasheet, 32-lead LFCSP):
    #   pin 20 VTUNE  -> LF_OUT  (loop-filter output drives the VCO tuning input;
    #                             WITHOUT this the PLL loop is open and VCO freq drifts)
    #   pin 26 PDB_RF -> +3V3    (RF power-down is active-LOW: LOW mutes the RF outs,
    #                             so tie HIGH to keep the 2.87/3.46 GHz RF enabled)
    #   pin 24 VREF   -> VREF    (internal reference node -- needs a DECOUPLING cap to
    #                             GND, NOT a direct tie; a direct GND tie shorts the
    #                             internal reference. Decoupled by C36 below.)
    #   pin 23 VCOM   -> ADF_VCOM (internal compensation node, biased at HALF the
    #                             tuning range -- needs a DECOUPLING cap to GND, NOT
    #                             a direct tie; a direct GND tie shorts the half-range
    #                             bias. Decoupled by C41 below. PDF p8 confirms.)
    #   pin 19 TEMP   -> ADF_TEMP (temperature-compensation OUTPUT; the ADF4351
    #                             has NO REFIN- pin -- the reference is single-ended
    #                             on REFIN pin 29.  Per datasheet Table 5, TEMP
    #                             requires decoupling caps to GND as close as
    #                             possible -> C37 100nF, NOT a no_connect and NOT
    #                             a direct tie.  Net is a 2-pin decoupling net.)
    #   pin  5 SW     -> NC      (fast-lock switch I/O; fast-lock unused, leave open)
    #   pin 13 RFOUTA- -> NC     (complement of used single-ended RFOUTA+ pin 12;
    #                             datasheet: unused RF output left floating)
    #   pin 14 RFOUTB+ -> NC     (unused RF output B; datasheet: leave floating)
    #   pin 15 RFOUTB- -> NC     (unused RF output B; datasheet: leave floating)
    #   pin 25 LD     -> NC      (lock-detect output, unused)
    #   pin 29 REFIN  -> refclk_25m (FPGA 25 MHz refclk_out on an N-side MRCC ball, aliased in fpga_net_map)
    add("R67", "qssc:R", "1k", "Resistor_SMD:R_0603_1608Metric", 250, 175, pins={"1": "LF_CP", "2": "LF_MID"})
    add("C31", "qssc:C", "220p", "Capacitor_SMD:C_0603_1608Metric", 240, 175, pins={"1": "LF_CP", "2": "GND"})
    add("C32", "qssc:C", "10n", "Capacitor_SMD:C_0603_1608Metric", 260, 190, pins={"1": "LF_MID", "2": "GND"})
    add("R68", "qssc:R", "2.2k", "Resistor_SMD:R_0603_1608Metric", 270, 175, pins={"1": "LF_MID", "2": "LF_OUT"})
    add("C33", "qssc:C", "220p", "Capacitor_SMD:C_0603_1608Metric", 285, 190, pins={"1": "LF_OUT", "2": "GND"})
    add("R73", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric", 300, 175, pins={"1": "RSET", "2": "GND"})
    add("C36", "qssc:C", "100n", "Capacitor_SMD:C_0603_1608Metric", 315, 175,
        pins={"1": "VREF", "2": "GND"})  # ADF4351 pin 24 VREF decoupling (internal ref node)
    add("C37", "qssc:C", "100n", "Capacitor_SMD:C_0603_1608Metric", 315, 185,
        pins={"1": "ADF_TEMP", "2": "GND"})  # ADF4351 pin 19 TEMP decoupling (temp-comp output, datasheet Table 5)
    add("C41", "qssc:C", "100n", "Capacitor_SMD:C_0603_1608Metric", 315, 195,
        pins={"1": "ADF_VCOM", "2": "GND"})  # ADF4351 pin 23 VCOM decoupling (internal compensation node, biased at half tuning range -- decouple, NOT a direct GND tie which would short the half-range bias). Designator C41 reused: the old C41 was the AP2127K BP cap (deleted -- pin 4 is NC).
    add("C34", "qssc:C", "100p", "Capacitor_SMD:C_0603_1608Metric", 330, 150, pins={"1": "RF_OUT", "2": "RF_IN"})
    add("U5", "qssc:GVA-84", "GVA-84+", "Package_TO_SOT_SMD:SOT-89-3",
        360, 150, pins=GVA84_NETS)
    # GVA-84+ is self-biased: +5V_RF feeds pin 3 (RFOUT/DC-IN) through the L2 RF
    # choke -- no VGG gate-bias resistor (the old HMC441 needed R69; removed).
    add("L2", "qssc:L", "100nH", "Inductor_SMD:L_0603_1608Metric", 360, 175, pins={"1": "+5V_RF", "2": "RF_AMP_OUT"})
    add("C35", "qssc:C", "100p", "Capacitor_SMD:C_0603_1608Metric", 390, 150, pins={"1": "RF_AMP_OUT", "2": "J3_SIG"})
    add("J3", "qssc:Conn_Coaxial", "SMA", "Connector_Coaxial:SMA_Amphenol_132289_EdgeMount",
        420, 150, pins={"1": "J3_SIG", "2": "GND"})

    # ==========================================================================
    # QSSC_Chip -- the two-faced quantum die, clamped between TWO PCBs (the
    # 2026-09 two-board sandwich; supersedes the earlier single merged board).
    # The old design mated a swappable chip MODULE here via a 60-pin Samtec
    # LSHM-130 (J11) carrying a 64:1 analog MUX, one TIA, and an 8x8 emitter
    # crossbar.  That is GONE: every channel is now parallel.  The 130-pad bare
    # die (65 pads/face: 64 electrodes + 1 common; 8x8 grid at 2.54 mm pitch, 1.7 mm
    # THT pads) is clamped between this main board (die BOTTOM / emitter face on
    # F.Cu, via a standard 2.54 mm female-header socket ChipSocket_2.54_EM) and the
    # head board (die TOP / detector face on B.Cu, via the matching
    # ChipSocket_2.54_DET).  On THIS (main) board the 64
    # emitter_cat{i} cathode pads fan out to the 3x TLC5947 constant-current LED
    # driver, and the EM_ANODE pad is the common +12V_EMIT anode rail.  The 64
    # EM signal nets escape outward on F.Cu; the dog-bone vias (0.20 mm via 1.0 mm
    # out) drop to B.Cu for the router.
    # ==========================================================================
    # The die sits EMITTER-DOWN plugged into a standard 2.54 mm female-header
    # SOCKET (qssc:ChipSocket_2.54_EM) on F.Cu -- an off-the-shelf through-hole
    # part (8x8 grid at 2.54 mm pitch, 17.78 mm span on the 20 mm die body).  The
    # P1-style pin-scaffold chip module plugs its male emitter pins DOWN into this
    # socket.  The 65 contact pads net identically (chip_net_map_em) so the
    # dog-bone fanout is unchanged; the head PCB carries the matching
    # ChipSocket_2.54_DET on B.Cu for the detector/TOP face.  Die removable.
    add("Uchip", "qssc:ChipSocket_2.54_EM", "ChipSocket_2.54_EM", "qssc:ChipSocket_2.54_EM",
        80, 55, pins=chip_net_map_em())

    # mw_gate drives the RF switch on THIS board (ADF4351 -> GVA-84+ -> SMA), so it
    # stays local (does not cross to the module): J8 header to the off-board switch.
    add("J8", "qssc:Conn_01x02", "MW_GATE", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
        260, 110, pins={"1": "mw_gate", "2": "GND"})

    # Reset button + JTAG (mw_gate J8 header is defined above with J11)
    # Reset button: drives the FPGA's active-high fabric "reset" net (top_level.v
    # input `reset`, async-asserted, 2-flop synchronized to clk_100mhz).  Pressing
    # ties `reset` to +3V3 (assert).  BUGFIX 2026-09: the original button had NO
    # pull-down, so `reset` FLOATED when released -> the fabric sat in an
    # indeterminate reset state.  R83 (10k pull-down to GND, added below) defines
    # the released state (reset=0); C68 (100nF to GND) debounces.  Pressed ->
    # reset=3V3 (strong drive overrides the 10k); released -> pulled low + RC-filtered.
    add("J7", "qssc:SW_Push", "RESET", "Button_Switch_THT:SW_PUSH_6mm_H5mm",
        60, 70, pins={"1": "+3V3", "2": "reset"})
    # JTAG header J9 -- 2x5 2.54mm, the standard Xilinx 10-pin JTAG pinout
    # (1=TCK 3=TDO 5=TMS 7=TDI, even=GND, 9=VREF 10=GND) per the Digilent
    # JTAG-HS3 / Xilinx Platform Cable II cable pinout.  Pin 9 = VREF supplies
    # the programmer's IO buffer and senses the target VCCO; the JTAG balls
    # (TCK/TDI/TDO/TMS = E10/E11/E13/E12) sit in bank 0 whose VCCO_0 = +3V3,
    # so VREF = +3V3.  (Was GND -- only FTDI-generic JTAG worked; HS3/Platform
    # Cable II need VREF.)
    add("J9", "qssc:Conn_02x05", "JTAG", "Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical",
        260, 30, pins={"1": "TCK", "2": "GND", "3": "TDO", "4": "GND", "5": "TMS",
                       "6": "GND", "7": "TDI", "8": "GND", "9": "+3V3", "10": "GND"})

    # Decoupling caps C1-C20 (100nF) round-robin on rails
    _rails = ["+3V3", "+1V8", "+1V0", "+5V_RF", "+5V", "+3V3"]
    _x0, _y0 = 30, 215
    for i in range(20):
        add(f"C{i+1}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
            _x0 + (i % 10) * 12, _y0 + (i // 10) * 10,
            pins={"1": _rails[i % len(_rails)], "2": "GND"})
    # Bulk caps C21-C30 (10uF)
    for i in range(10):
        add(f"C{i+21}", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
            _x0 + 130 + i * 12, _y0,
            pins={"1": _rails[i % len(_rails)], "2": "GND"})
    # ESD diodes D1-D10
    _esd = [("USB_DP", "D1"), ("USB_DM", "D2"), ("RF_AMP_OUT", "D3"), ("+5V_IN", "D4"),
            ("+5V", "D5"), ("+3V3", "D6"), ("+1V8", "D7"), ("+1V0", "D8"), ("+5V_RF", "D9"), ("mw_gate", "D10")]
    for i, (net, ref) in enumerate(_esd):
        add(ref, "qssc:D", "ESD5V0", "Diode_SMD:D_SOD-923",
            _x0 + 130 + i * 12, _y0 + 10, pins={"1": "GND", "2": net})

    # PWR_FLAG on nets without a power_out driver
    add("#FLG1", "power:PWR_FLAG", "PWR_FLAG", "", 45, 30, pins={"1": "+5V_IN"})
    add("#FLG2", "power:PWR_FLAG", "PWR_FLAG", "", 70, 30, pins={"1": "+5V"})
    add("#FLG3", "power:PWR_FLAG", "PWR_FLAG", "", 45, 45, pins={"1": "GND"})
    # +5V_RF is now driven through FB1 (a passive ferrite bead) from +5V, not by a
    # regulator power_out pin, so ERC cannot see a driver -> flag it.
    add("#FLG5", "power:PWR_FLAG", "PWR_FLAG", "", 95, 90, pins={"1": "+5V_RF"})
    # (+5V_A is NOT on the main board -- the AD7616 analog rail lives on the detector
    # head now.  Its PWR_FLAG is added in build_detector_head().)
    # VREFP/VREFN: the Artix-7 XADC reference. When the internal 1.25 V reference
    # is used, VREFP is bypassed to GND (C40) and VREFN tied to GND; the pin is a
    # power input driven by the on-chip reference, which ERC cannot see -> flag it.
    # (XADC is unused on this board -- the detector readout is 4x AD7616 on the head
    # -- but VREFP stays decoupled + flagged so the unused ref pad is not an ERC
    # error.  Moved off (45,60) which overlapped #FLG7 +12V_EMIT_IN.)
    add("#FLG4", "power:PWR_FLAG", "PWR_FLAG", "", 95, 110, pins={"1": "VREFP"})

    # ==========================================================================
    # Production-hardening pass 2026-09: missing decoupling, pull-ups, protection.
    # Every active IC now has LOCAL decoupling; the reset button is fixed (above);
    # the USB host is protected from barrel backfeed; AMS1117s get stability bulk;
    # the FPGA gets per-rail 4.7uF bulk (UG393); the ADF4351 gets per-supply 100nF;
    # JTAG gets pull resistors.  PCB positions are in assign_pcb_positions() below.
    # Full rationale + BOM/supplier parts: HARDWARE_DESIGN.md §16 + BOM.csv.
    # ==========================================================================
    # USB-C VBUS backfeed protection + VBUS bulk.
    add("D_USB", "qssc:D", "SS14 Schottky", "Diode_SMD:D_SMA",
        300, 220, pins={"1": "VBUS_USB", "2": "+5V_IN"})   # anode=VBUS_USB cathode=+5V_IN
    add("#FLG6", "power:PWR_FLAG", "PWR_FLAG", "", 300, 232, pins={"1": "VBUS_USB"})
    add("C84", "qssc:C", "4.7uF", "Capacitor_SMD:C_0805_2012Metric",
        312, 220, pins={"1": "VBUS_USB", "2": "GND"})
    # USB_DP/DM series damping (27R) between FT232 and the ESD/connector side.
    add("R78", "qssc:R", "27R", "Resistor_SMD:R_0603_1608Metric",
        330, 220, pins={"1": "USB_DP_F", "2": "USB_DP"})
    add("R79", "qssc:R", "27R", "Resistor_SMD:R_0603_1608Metric",
        342, 220, pins={"1": "USB_DM_F", "2": "USB_DM"})
    # AMS1117 output stability bulk (22uF MLCC) + shared input bulk.  AMS1117 can
    # oscillate with only a low-ESR ceramic; 22uF guarantees stability.  REG3
    # (AP2127K) is ceramic-stable and already decoupled (C41-C44).
    add("C49", "qssc:C", "22uF", "Capacitor_SMD:C_0805_2012Metric",
        330, 240, pins={"1": "+3V3", "2": "GND"})   # REG1 +3V3 output bulk
    add("C50", "qssc:C", "22uF", "Capacitor_SMD:C_0805_2012Metric",
        342, 240, pins={"1": "+1V8", "2": "GND"})   # REG2 +1V8 output bulk
    add("C51", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric",
        354, 240, pins={"1": "+5V", "2": "GND"})    # REG1/REG2 input bulk
    # FPGA per-rail 4.7uF bulk (UG393 Artix-7 decoupling): the round-robin C1-C20
    # 100nF is the HF local decoupling; these are the missing per-rail LF bulk.
    add("C52", "qssc:C", "4.7uF", "Capacitor_SMD:C_0805_2012Metric",
        330, 256, pins={"1": "+1V0", "2": "GND"})
    add("C53", "qssc:C", "4.7uF", "Capacitor_SMD:C_0805_2012Metric",
        342, 256, pins={"1": "+1V8", "2": "GND"})
    add("C54", "qssc:C", "4.7uF", "Capacitor_SMD:C_0805_2012Metric",
        354, 256, pins={"1": "+3V3", "2": "GND"})
    # FT232 decoupling: 3V3OUT (pin17) needs 100nF per datasheet (was missing); VCC
    # (pin20) local 100nF; 4.7uF rail bulk.
    add("C55", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        330, 272, pins={"1": "3V3OUT", "2": "GND"})
    add("C56", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        342, 272, pins={"1": "+3V3", "2": "GND"})
    add("C57", "qssc:C", "4.7uF", "Capacitor_SMD:C_0805_2012Metric",
        354, 272, pins={"1": "+3V3", "2": "GND"})
    # XO (Abracon ASE) + config flash Vdd/VCC local decoupling.
    add("C58", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        330, 288, pins={"1": "+3V3", "2": "GND"})   # Y1 pin4 Vdd
    add("C59", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        342, 288, pins={"1": "+3V3", "2": "GND"})   # U8 pin8 VCC
    # ADF4351 per-supply decoupling: AVDD(10)/DVDD(28)/SDVDD(32)/Vp(6)/VVCO(16,17)
    # each get a local 100nF to GND -- critical for low phase noise / ref spurs.
    add("C60", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        330, 304, pins={"1": "+3V3", "2": "GND"})   # AVDD
    add("C61", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        342, 304, pins={"1": "+3V3", "2": "GND"})   # DVDD
    add("C62", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        354, 304, pins={"1": "+3V3", "2": "GND"})   # SDVDD
    add("C63", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        330, 316, pins={"1": "+3V3", "2": "GND"})   # Vp
    add("C64", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        342, 316, pins={"1": "+3V3", "2": "GND"})   # VVCO
    add("C65", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        354, 316, pins={"1": "+3V3", "2": "GND"})   # VVCO
    # JTAG pull resistors (TCK pull-down, TMS/TDI pull-up) -- defines the JTAG
    # state when the header is open and dampens floating-clock edges.
    add("R80", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric",
        330, 332, pins={"1": "TCK", "2": "GND"})
    add("R81", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric",
        342, 332, pins={"1": "TMS", "2": "+3V3"})
    add("R82", "qssc:R", "4.7k", "Resistor_SMD:R_0603_1608Metric",
        354, 332, pins={"1": "TDI", "2": "+3V3"})
    # Reset-button pull-down + debounce on the active-high fabric "reset" net:
    # R83 holds reset=0 when J7 is released (fixes the original floating-reset
    # bug); C68 + R83 form an RC debounce (10k x 100nF = 1 ms) on the release edge.
    add("R83", "qssc:R", "10k", "Resistor_SMD:R_0603_1608Metric",
        330, 348, pins={"1": "reset", "2": "GND"})
    add("C68", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        342, 348, pins={"1": "reset", "2": "GND"})
    # (The TIA_OUT -> XADC level-shift divider R84/R85/C66 is GONE: the single-board
    # pivot removed the XADC; the 64 TIA outputs now feed 4x AD7616 parallel ADCs
    # (true analog intensity readout, not photon counting).  The XADC VP/VN balls
    # are left NC; see fpga_net_map.)
    build_emitter_driver()
    add_btb_connector("host")


# --------------------------------------------------------------------------
# Phase 1b -- 64-ch parallel detector front-end + 64-ch parallel emitter driver
# --------------------------------------------------------------------------
# Detector path (per channel i=0..63):  chip DET_IN{i} -> TLV9004 TIA (-IN,
# +IN=DET_REF=1.65 V bias) -> DET_TIA{i} (0..3.3 V analog intensity waveform)
# -> AD7616 analog input.  4x AD7616 (16-ch 16-bit dual simultaneous-sampling
# ADC, UAD0..3) digitize all 64 TIA outputs; the FPGA reads the 64 16-bit
# samples over a 9-pin SPI bus (adc_convst/sclk/miso/busy/cs0..3/resetn)
# and does the 1 kHz lock-in demodulation numerically in signal_processor.v.
# This is a TRUE analog readout of the photoluminescence intensity -- the user
# explicitly rejected photon counting (user: "I want the signal, not a counter"): the
# 16x LMV339 comparator + 64x pull-up + MCP4725 threshold DAC + 64 FPGA
# photon-counter chain is GONE, replaced by 4 ADC chips (66 parts removed, 34
# added; FPGA detector IO drops 64+2 -> 9 pins).
# Emitter path:  FPGA 4-wire SPI (em_sclk/sin/xlat/blank) -> 3x TLC5947
# daisy-chained constant-current LED driver (SIN->SOUT, 72 ch, 64 used).  Each
# emitter cathode emitter_cat{i} sinks into a TLC5947 OUTn; the 64 emitters
# share a common +12V_EMIT anode rail (chip EM_ANODE pad).  12-bit PWM grayscale per
# channel -> any combination of emitters fires in parallel at independent
# intensity (the old 64-GPIO + 74HCT240 + P-MOS one-at-a-time design is gone).
# The TIAs run on +3V3; the TLC5947 logic (VCC) on +3V3, OUT sinks from +12V_EMIT.
# The AD7616 analog VCC runs on +5V_A (a ferrite-isolated +5V rail) and VDRIVE on +3V3.
def build_detector_analog():
    # TLV9004 channel k -> (out, in-, in+) pin numbers (TSSOP-14 datasheet)
    TIA_CH = {0: ("1", "2", "3"), 1: ("7", "6", "5"),
              2: ("8", "9", "10"), 3: ("14", "13", "12")}
    # ---- 16x TLV9004 (TIA): detector current -> voltage waveform -----------
    for j in range(16):
        tia = {"4": "+3V3", "11": "GND"}
        for k in range(4):
            i = 4 * j + k
            to, tm, tp = TIA_CH[k]
            tia[tm] = f"DET_IN{i}"      # detector current -> inverting input
            tia[tp] = "DET_REF"         # 1.65 V virtual-ground bias -> non-inv input
            tia[to] = f"DET_TIA{i}"     # TIA output -> AD7616 analog input
        add(f"UT{j}", "qssc:TLV9004", "TLV9004",
            "Package_SO:TSSOP-14_4.4x5mm_P0.65mm", 0, 0, pins=tia)
    # ---- per-channel TIA feedback (Rf 1M || Cf 1p) -------------------------
    for i in range(64):
        add(f"Rf{i}", "qssc:R", "1M", "Resistor_SMD:R_0603_1608Metric", 0, 0,
            pins={"1": f"DET_IN{i}", "2": f"DET_TIA{i}"})
        add(f"Cf{i}", "qssc:C", "1pF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": f"DET_IN{i}", "2": f"DET_TIA{i}"})
    # ---- DET_REF bias divider: +3V3 split 1:1 -> 1.65 V virtual ground -----
    # (the TIA non-inverting inputs share this reference; the AD7616 reads the
    # TIA output as a single-ended 0..3.3 V signal within its +/-5 V range.)
    add("Rref1", "qssc:R", "10k", "Resistor_SMD:R_0603_1608Metric", 0, 0,
        pins={"1": "+3V3", "2": "DET_REF"})
    add("Rref2", "qssc:R", "10k", "Resistor_SMD:R_0603_1608Metric", 0, 0,
        pins={"1": "DET_REF", "2": "GND"})
    add("Cref", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
        pins={"1": "DET_REF", "2": "GND"})
    # ---- per-IC decoupling (one 100nF each on the 16 TIA ICs) --------------
    for j in range(16):
        add(f"Cut{j}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": "+3V3", "2": "GND"})      # TLV9004 decoupling
    # ---- +5V_A rail: ferrite-isolated +5V for the AD7616 analog VCC --------
    # The AD7616 VCC is 5 V analog; isolating it from the main +5V rail with a
    # 600-ohm ferrite + bulk/HF decoupling keeps the ADC's sampling transients
    # off the digital +5V (and vice-versa).  VDRIVE is +3V3 (logic levels).
    # Head +5V (from BTB J12 pins 17/18) feeds FB_A; it had NO decoupling (2
    # loads, 0 caps). Add a 10 uF bulk cap on the +5V side of the ferrite as the
    # LDO/feed decoupling per standard practice.
    add("C_5V", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric", 0, 0,
        pins={"1": "+5V", "2": "GND"})
    add("FB_A", "qssc:L", "600Ω@100MHz", "Inductor_SMD:L_0603_1608Metric", 0, 0,
        pins={"1": "+5V", "2": "+5V_A"})
    add("C_Abulk", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric", 0, 0,
        pins={"1": "+5V_A", "2": "GND"})
    add("C_ahf", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
        pins={"1": "+5V_A", "2": "GND"})
    # ---- 4x AD7616 (16-ch 16-bit ADC): digitize the 64 TIA outputs ---------
    # Pin numbers verified against KiCad Analog_ADC:AD7616 (datasheet Rev. 0).
    # Bank A inputs V0A..V7A (pins 28,26,24,22,19,17,14,12) take channels
    # 16j+0..16j+7; bank B inputs V0B..V7B (pins 73,75,77,79,2,4,7,9) take
    # channels 16j+8..16j+15.  Per-channel VGND pins all tie to GND.
    VA_PINS = ["28", "26", "24", "22", "19", "17", "14", "12"]   # V0A..V7A
    VB_PINS = ["73", "75", "77", "79", "2", "4", "7", "9"]       # V0B..V7B
    VAG_GND = ["27", "25", "23", "21", "20", "18", "13", "11"]   # V0AGND..V7AGND
    VBG_GND = ["74", "76", "78", "80", "1", "3", "8", "10"]      # V0BGND..V7BGND
    AD7616_FP = "Package_QFP:LQFP-80_14x14mm_P0.65mm"
    for j in range(4):
        p = {}
        # 16 analog inputs <- DET_TIA{16j+k}
        for k in range(8):
            p[VA_PINS[k]] = f"DET_TIA{16*j + k}"        # bank A
            p[VB_PINS[k]] = f"DET_TIA{16*j + 8 + k}"    # bank B
        # per-channel grounds + all analog/digital/reg/ref grounds -> GND
        for g in VAG_GND + VBG_GND:
            p[g] = "GND"
        for g in ["5", "16", "29", "72", "50", "69", "51", "32", "34"]:
            p[g] = "GND"
        # power: VCC (4 pins) -> +5V_A, VDRIVE -> +3V3
        for vcc in ["6", "15", "30", "71"]:
            p[vcc] = "+5V_A"
        p["49"] = "+3V3"                      # VDRIVE
        # serial-mode config ties (datasheet serial-interface section):
        p["40"] = "+3V3"   # SER/PAR = HIGH -> serial/SPI mode
        p["45"] = "GND"    # SER1W = LOW -> 1-wire (all data on SDOA, SDOB unused)
        p["37"] = "+3V3"   # SEQEN = HIGH -> sequencer auto-cycles all 8 VA/VB pairs
        p["35"] = "GND"    # REFSEL = LOW -> internal reference (REFCAP/REFINOUT decoupled)
        # software range/channel config via SDI -> HW pins tied low (don't care):
        # range: HW_RNGSEL1=1, HW_RNGSEL0=0 -> hardware mode, +/-5 V bipolar
        # range on all 16 channels (AD7616 datasheet Table 8; the 0..3.3 V TIA
        # output fits with full resolution: 3.3/5*32768 ~= 21.6k codes).  Hardware
        # mode (HW_RNGSEL != 00) PROHIBITS register access, so SDI is unused and
        # no init config-register write is needed -- the range is pin-strapped.
        p["39"] = "GND"; p["38"] = "+3V3"     # HW_RNGSEL0=0, HW_RNGSEL1=1 -> +/-5V HW mode
        p["64"] = "GND"; p["65"] = "GND"; p["66"] = "GND"   # CHSEL0/1/2 (don't-care in sequencer mode)
        p["61"] = "GND"   # BURST/WR = LOW (serial read, no burst)
        p["58"] = "GND"; p["59"] = "GND"; p["60"] = "GND"   # OS0/1/2 = no oversampling
        p["46"] = "GND"   # CRCEN = LOW (no CRC)
        # reference/reg decoupling pins -> dedicated 2-pin nets to decoupling caps
        p["31"] = f"adc_refc{j}"     # REFCAP
        p["33"] = f"adc_refio{j}"    # REFINOUT
        p["70"] = f"adc_rgc{j}"      # REGCAP  (1.0 V analog regulator out)
        p["52"] = f"adc_rgd{j}"      # REGCAPD (1.0 V digital regulator out)
        # FPGA SPI bus: shared CONVST/SCLK/MISO/RESETn, per-chip CS, chip0 BUSY.
        # (BUSY is push-pull -> only chip0's BUSY is read; all 4 finish a
        # simultaneous CONVST together.  SDOB + SDI = NC in 1-wire hardware mode.)
        p["68"] = "adc_convst"        # CONVST (shared)
        p["62"] = "adc_sclk"          # SCLK (shared)
        p["57"] = "adc_miso"          # SDOA (shared; 4 chips share, per-chip CS selects)
        p["36"] = "adc_resetn"        # RESET (shared, active-low)
        p["63"] = f"adc_cs{j}"        # CS (per chip)
        if j == 0:
            p["67"] = "adc_busy"      # BUSY (only chip0 -> FPGA; push-pull, no wire-OR)
        # Intentionally-unused pins (AD7616 1-wire HW serial mode, datasheet Table 8
        # + serial-interface section): the parallel DB bus (DB0-3,DB6-9 = pins
        # 41-44,47,48,53,54) is inert in serial mode; SDI (55) carries no register
        # data because HW mode (HW_RNGSEL != 00) PROHIBITS register access; SDOB
        # (56) is the second serial output, unused in 1-wire mode (all data on
        # SDOA = pin 57).  All declared (no_connect) per datasheet.
        add(f"UAD{j}", "qssc:AD7616", "AD7616", AD7616_FP, 0, 0, pins=p,
            nc_pins=["41", "42", "43", "44", "47", "48", "53", "54", "55", "56"]
                 + ([] if j == 0 else ["67"]))
        # j>0 BUSY (pin 67) is unused: BUSY is push-pull (no wire-OR), only chip0's
        # BUSY is read by the FPGA, so chip1-3 BUSY is left NC per datasheet.
        # per-chip decoupling (datasheet ref/reg/supply caps):
        add(f"Cua{j}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": "+5V_A", "2": "GND"})              # VCC HF
        add(f"CuaB{j}", "qssc:C", "10uF", "Capacitor_SMD:C_0805_2012Metric", 0, 0,
            pins={"1": "+5V_A", "2": "GND"})              # VCC bulk
        add(f"Cud{j}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": "+3V3", "2": "GND"})               # VDRIVE
        add(f"Cur{j}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": f"adc_refc{j}", "2": "GND"})       # REFCAP
        add(f"Crio{j}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": f"adc_refio{j}", "2": "GND"})      # REFINOUT
        add(f"Crgc{j}", "qssc:C", "1uF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": f"adc_rgc{j}", "2": "GND"})        # REGCAP (1.0 V reg)
        add(f"Crgd{j}", "qssc:C", "1uF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": f"adc_rgd{j}", "2": "GND"})        # REGCAPD (1.0 V reg)


def build_emitter_driver():
    # ---- 3x TLC5947 constant-current LED driver (daisy-chained, 64 emitters) --
    # Replaces the old 8x 74HCT240 + 64x AO3401A P-MOS + 128 passives (208 parts)
    # with 3 chips + 3 Riref + 3 decoupling caps (9 parts).  Each TLC5947 has 24
    # constant-current sink outputs (OUT0..OUT23 = pins 5..28); 3 chips chain to
    # 72 channels, of which 64 are used (chip2 uses only OUT0..OUT15).  The 64
    # emitter cathodes (emitter_cat0..63, chip bottom-edge pads) sink into the
    # TLC5947 OUTn; the emitters share a common +12V_EMIT anode rail (EM_ANODE pad).
    # FPGA interface is 4 wires: em_sclk/em_sin/em_xlat/em_blank; SIN->SOUT
    # cascades chip-to-chip (chip2 SOUT left NC).  VCC=+3V3; IREF set by
    # Riref=4.87k -> I_OUT = 41*1.20/4.87k ~= 10.1 mA per channel.
    TLC_FP = ("Package_SO:HTSSOP-32-1EP_6.1x11mm_P0.65mm_EP5.2x11mm_"
              "Mask4.11x4.36mm_ThermalVias")
    for j in range(3):
        pins = {"1": "GND", "2": "em_blank", "3": "em_sclk",
                "30": "em_xlat", "31": f"em_iref{j}", "32": "+3V3", "33": "GND",
                # The HTSSOP-32-1EP_ThermalVias footprint models the EP as 38
                # plated-thermal-via pads named "33" (netted GND above) PLUS 13
                # numberless "" EP copper sub-pads. Per TLC5947 datasheet the EP
                # (DAP) MUST be tied to GND (thermal + electrical); net the
                # sub-pads GND so no EP copper floats. fp_net_pads matches "".
                "": "GND"}
        # SIN: chip0 takes em_sin from the FPGA; chip1/2 take the prior chip SOUT.
        pins["4"] = "em_sin" if j == 0 else f"em_sout{j-1}"
        # SOUT: chip0/1 cascade to the next chip's SIN; chip2 SOUT left NC.
        if j < 2:
            pins["29"] = f"em_sout{j}"
        # OUT0..OUT23 (pins 5..28) -> emitter_cat{24*j+k}; only the 64 used
        # channels are netted (chip2 OUT16..OUT23 stay NC).
        for k in range(24):
            i = 24 * j + k
            if i < 64:
                pins[str(5 + k)] = f"emitter_cat{i}"
        # chip2 (j=2) uses only OUT0..OUT15 (pins 5..20); OUT16..OUT23 (pins
        # 21..28) are unused and SOUT (pin 29) is the dangling end of the daisy
        # chain -> all declared (no_connect) per TLC5947 datasheet (unused outputs
        # left open; SOUT unconnected at the chain tail).
        nc = (["21", "22", "23", "24", "25", "26", "27", "28", "29"]
              if j == 2 else [])
        add(f"UE{j}", "qssc:TLC5947", "TLC5947", TLC_FP, 0, 0, pins=pins, nc_pins=nc)
        # Riref: IREF -> GND, sets the per-channel constant current.
        add(f"Riref{j}", "qssc:R", "4.87k", "Resistor_SMD:R_0603_1608Metric", 0, 0,
            pins={"1": f"em_iref{j}", "2": "GND"})
        # per-chip VCC decoupling (+3V3).
        add(f"Cem{j}", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric", 0, 0,
            pins={"1": "+3V3", "2": "GND"})


def build_gradient_board():
    """EXTENDED MAIN board (qssc-grad): the base main board PLUS the field-gradient coil + DAC current driver.  Calls build_main_board() first so
    every base component (FPGA, emitter driver, RF, USB, power, die emitter face,
    BTB host) is present unchanged, then adds the 7 gradient parts in a free
    schematic block.  PCB placement is set in assign_pcb_positions_gradient().

    Chain:  FPGA 3-wire SPI (grad_cs/sclk/mosi) -> MCP4921 12-bit DAC (Ugrad)
    -> LM358 op-amp servo (Udrv) -> IRLML2502 N-MOSFET (Qgrad) -> coil (Lgrad).
    Vdac = I_coil * Rsense (1 ohm), so a 12-bit code sets the gradient current
    directly: 0..~5 V -> 0..~5 A (sized for the ~2.2 A clean-addressing
    current; the ~0.56 A resolvable-measurement current is mid-range).  The
    8-pin MCP4921 has no !SHDN pin (shutdown = SPI bit 12, kept active by
    dac_ctrl's {4'b0011, code} framing), so the coil is cut by writing code 0
    -> VOUTA=0 -> op-amp -> MOSFET off (clean zero-current cut).  The MOSFET
    gate is driven by the op-amp whose loop holds V_sense = V_dac.  Rsense
    (1 ohm, 2512 power) sits in the MOSFET source to GND; the coil +5V feed ->
    trace -> drain -> source -> Rsense -> GND.  The LM358's unused amp B is
    parked as a unity follower (INB+=GND, INB-=OUTB) so it cannot oscillate.
    grad_en is an FPGA STATUS output only (the ball stays NC on the PCB -- a
    spare for future high-side enable); it is NOT wired to the DAC."""
    build_main_board(grad=True)
    # ---- gradient coil + DAC current driver (schematic block, x 360-410) -----
    # Ugrad: MCP4921 12-bit SPI DAC (8-pin SOIC; pinout per Microchip DS22248A,
    # verified against the downloaded datasheet PDF). The 8-pin single MCP4921
    # has NO !SHDN pin -- shutdown is SPI write-word bit 12, which dac_ctrl.v
    # keeps =1 (active) in its {4'b0011, code} framing, so the coil is cut by
    # writing code 0 -> VOUTA=0 -> op-amp -> MOSFET off (clean zero-current cut).
    # VREF(pin6) tied to +5V (VDD) for rail-to-rail Vout = 5V*code/4096 (1x gain);
    # !LDAC(pin5) tied to GND (transparent: output updates on CS rising edge);
    # AVSS(pin7)=GND; VOUTA(pin8)->GRAD_DAC_OUT -> op-amp servo.
    # grad_en (FPGA ball) is kept as a spare STATUS output only -- it no longer
    # wires to any DAC pin (there is no !SHDN to drive). The FPGA ball stays NC
    # on the PCB (a free spare for future high-side enable / status readback).
    add("Ugrad", "qssc:MCP4921", "MCP4921", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
        360, 150, pins={"1": "+5V", "2": "grad_cs", "3": "grad_sclk",
                        "4": "grad_mosi", "5": "GND", "6": "+5V",
                        "7": "GND", "8": "GRAD_DAC_OUT"})
    # Udrv: LM358 dual op-amp (SOIC-8). Amp A servo: INA+=DAC, INA-=sense,
    # OUTA=gate. Amp B parked: INB+=GND, INB-=OUTB (GRAD_NFB feedback wire).
    add("Udrv", "qssc:LM358", "LM358", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
        385, 150, pins={"1": "GRAD_GATE", "2": "GRAD_SENSE", "3": "GRAD_DAC_OUT",
                        "4": "+5V", "5": "GND", "6": "GRAD_NFB", "7": "GRAD_NFB",
                        "8": "GND"})
    # Qgrad: IRLML2502 N-MOSFET (SOT-23-3). G=gate, S=sense, D=coil return.
    add("Qgrad", "qssc:IRLML2502", "IRLML2502", "Package_TO_SOT_SMD:SOT-23-3",
        405, 150, pins={"1": "GRAD_GATE", "2": "GRAD_SENSE", "3": "GRAD_COIL_N"})
    # Lgrad: the gradient coil -- a 1.5 mm PCB copper trace on F.Cu (qssc:GradCoil
    # footprint, a KiCad net-tie), drawn along the die socket bottom edge.  The
    # coil bridges two nets: pin 1 = GRAD_COIL_P (the +5V side, fed through
    # Rgrad_feed), pin 2 = GRAD_COIL_N (coil return -> MOSFET drain).  The
    # net-tie joins GRAD_COIL_P and GRAD_COIL_N ONLY through the coil trace, so
    # +5V (a poured plane) and the MOSFET drain stay distinct nets -- the coil
    # cannot be bypassed.  Rgrad_feed (0 ohm) is the single component tie from
    # the +5V plane to GRAD_COIL_P.
    add("Rgrad_feed", "qssc:R", "0R", "Resistor_SMD:R_0805_2012Metric",
        352, 175, pins={"1": "+5V", "2": "GRAD_COIL_P"})
    add("Lgrad", "qssc:L", "GradCoil", "qssc:GradCoil",
        360, 175, pins={"1": "GRAD_COIL_P", "2": "GRAD_COIL_N"})
    # Rsense: 1 ohm current-sense in the MOSFET source -> GND (2512 power: at the
    # 2.2 A clean-addressing current I^2*R = 4.84 W -> use a 5 W thick-film part).
    add("Rsense", "qssc:R", "1", "Resistor_SMD:R_2512_6332Metric",
        405, 175, pins={"1": "GRAD_SENSE", "2": "GND"})
    # DAC Vdd decoupling: 2x 100 nF (HF + bulk-by) on the +5V rail at Ugrad.
    add("C_grad1", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        348, 138, pins={"1": "+5V", "2": "GND"})
    add("C_grad2", "qssc:C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
        360, 138, pins={"1": "+5V", "2": "GND"})


def build_detector_head():
    """Build the DETECTOR HEAD board components: the die TOP (detector) face
    (ChipSocket_2.54_DET on B.Cu -- the head's bottom face contacts the die top
    face via a standard 2.54 mm female-header socket; the P1-style pin-scaffold
    chip module plugs its male detector pins UP into it), the 64-ch TLV9004 TIA +
    4x AD7616 analog front-end (build_detector_analog), and the BTB DAUGHTER
    connector (J12) that brings in +3V3 / +5V / GND and carries the 9-wire AD7616
    SPI back to the FPGA on the main board.

    Power: +3V3 and +5V arrive through J12 (passive connector pins -- ERC sees
    no power_out driver), so they get PWR_FLAGs.  +5V_A (the AD7616 analog rail)
    is derived locally from +5V through FB_A (a passive ferrite, inside
    build_detector_analog) -- also flagged.  The head has no FPGA, no RF, no USB,
    no emitter driver -- it is purely the detector analog front-end + the die
    detector face."""
    add("Uchip", "qssc:ChipSocket_2.54_DET", "ChipSocket_2.54_DET", "qssc:ChipSocket_2.54_DET",
        80, 55, pins=chip_net_map_det())
    build_detector_analog()
    add_btb_connector("daughter")
    # +3V3 / +5V arrive through J12 (passive pins) -> flag so ERC sees a driver.
    add("#FLG10", "power:PWR_FLAG", "PWR_FLAG", "", 30, 12, pins={"1": "+3V3"})
    add("#FLG11", "power:PWR_FLAG", "PWR_FLAG", "", 34, 12, pins={"1": "+5V"})
    # GND also arrives through J12 (passive pins) -> flag so the AD7616 / TLV9004
    # power-input GND pins (V*GND, V-) see an external driver (else ERC
    # power_pin_not_driven on every VGND pin). The main board sources GND.
    add("#FLG12", "power:PWR_FLAG", "PWR_FLAG", "", 38, 12, pins={"1": "GND"})
    # +5V_A driven through FB_A (passive ferrite) from +5V -> flag (like +5V_RF).
    add("#FLG9", "power:PWR_FLAG", "PWR_FLAG", "", 80, 132, pins={"1": "+5V_A"})




def assign_pcb_positions():
    # Fixed IC/connector placements (mm) on the MAIN control board (150x118,
    # 6-layer: right edge at J3,
    # bottom edge at J10). The detector front-end is on the HEAD board (see
    # assign_pcb_positions_head); this board carries FPGA + emitter + RF + USB +
    # power + the die EMITTER face (QSSC_Chip_EM).
    # Layout zones:
    #   centre        ChipSocket_2.54_EM die (64 EM cathodes + EM_ANODE on F.Cu, 20x20 mm)
    #   right         FPGA CSG324 (324-ball BGA, moved off-centre to clear the die) + config flash
    #   top           power chain (left) + RF chain (ADF4351 -> GVA-84 -> SMA right edge)
    #   bottom-left   USB-C (J10) + FT232 USB cluster -- all user-facing connectors
    #                 live in the LEFT x<60 strip (the only main-board region NOT
    #                 covered by the stacked detector head board, which overhangs
    #                 the main's right + bottom edges); reset/JTAG/MW-gate/5V+12V
    #                 barrels also on the left
    #   top-left      BTB HOST J12 (9-wire SPI + power to the detector head)
    #   below die     3x TLC5947 constant-current LED driver (UE0/1/2 + Riref/Cem)
    # Edge-mount connectors sit at the board edge by design (the barrel/USB-C/SMA
    # opening MUST overhang an edge or the plug cannot insert):
    #   J2 5V barrel   -> top edge (10,10) rot 180, barrel overhangs -y (top)
    #   J4 12V barrel  -> left edge (10,72) rot 270, barrel overhangs -x (left)
    #   J10 USB-C      -> bottom edge (32,114) rot 180, mouth overhangs +y (bottom)
    #   J3 SMA         -> right edge (150,83) rot 0, jack overhangs +x (right)
    #   J8 MW_GATE     -> left edge (x10, y92) -- was x150 under the head board
    #                     (through-hole pins collided with the stacked head board)
    # Rotations are chosen so the footprint's native pad geometry is preserved
    # (USB-C and SMA pads collapse/short when rotated 90 deg -- see DRC notes);
    # barrel jacks are round/rect THT pads and DO tolerate 90 deg (J4: rot 270
    # = 180 + 90, verified empirically -- bbox x[-4.22,11.82] overhangs x=0).
    fixed = {
        # power (top-left)
        "J2": (10, 10, 180), "F1": (24, 10, 0), "R70": (20, 16, 0), "LED1": (26, 16, 0),
        # C_in = +5V_IN input bulk decoupling (10uF 0805) right at the barrel-jack
        # input rail (J2.1 -> C_in -> F1).  Top-left, clear of the J2/F1/R70/LED1
        # row (y=10/16) and of the default C1-30 cap grid (x>=50).  Without this it
        # fell through to the (50,50) default and collided with C37.
        "C_in": (46, 8, 0),
        "REG1": (34, 14, 0), "REG2": (34, 26, 0), "REG3": (34, 38, 0), "FB1": (34, 50, 0),
        # REG3 (AP2127K-1.0) decoupling, around (34,38)
        "C42": (28, 36, 0), "C43": (40, 42, 0), "C44": (28, 42, 0),
        # FB1 (ferrite bead) +5V -> +5V_RF pi filter, around (34,50)
        "C45": (28, 50, 0), "C46": (28, 56, 0), "C47": (40, 50, 0), "C48": (40, 56, 0),
        # ---- +12V_EMIT emitter anode supply (left edge, below the 5V chain) ----
        # J4 12V barrel -> F2 1A PTC. J4 sits on the LEFT edge, rot 270 so the
        # barrel opening faces -x (off the left edge, overhanging x=0 ~4.2 mm --
        # verified empirically: bbox x[-4.22,11.82]). rot 180 faces the barrel
        # -y/up INTO the board at y=72 (blocked); rot 90 also points
        # inward (+x) -- only rot 270 launches -x. The +12V rail runs on B.Cu
        # up to y~55 and
        # across to the die EM_ANODE pad (70,55); C85/C86 decouple it at the
        # anode fanout (just left of the ChipSocket_2.54_EM courtyard x66.5).
        "J4": (10, 72, 270), "F2": (24, 72, 0), "C85": (64, 52, 0), "C86": (64, 58, 0),
        # ---- quantum die (centre) + FPGA (moved right off the die) ----
        "Uchip": (80, 55, 0),
        # BTB HOST connector J12 (top edge, between the RF chain and the config
        # flash).  At (110,14) the x[105,114] y[1,27] courtyard window is empty
        # on the main board (clear of the decoupling-cap grid x<=86, the RF
        # chain y>=30, and the flash/RTC cluster y>=52).  The stacked detector
        # head's J12 sits at head-local (50,14) -- the head origin is the main
        # origin + (60,0) mm, so head (50,14) == main (110,14), and head
        # x[45,54] y[1,27] is clear of the head die (10..30 x 45..65) and the
        # head front-end (x>=80).  y=14 keeps the 22.86 mm-tall connector body
        # (pads +/-11.43) fully on-board.  The 9-wire AD7616 SPI + 3V3/5V/GND
        # cross the sandwich here.
        "J12": (110, 14, 0),
        # FPGA CSG324 (~17 mm BGA, courtyard x[119.5,136.5] y[46.5,63.5]) right of
        # the die; ~29 mm gap for the 64 DET_DIG routes (Phase 4). XO above the die.
        "U1": (128, 55, 0), "Y1": (70, 25, 0),
        # config flash W25Q128 + pull-ups in the die<->FPGA gap (x106-118); C40
        # VREFP decoupling just below-left of the FPGA.
        "U8": (106, 58, 0), "R75": (114, 54, 0), "R76": (114, 58, 0),
        "R77": (114, 62, 0), "C40": (118, 70, 0),
        # USB (bottom): FT232 + USB-C + CC pulls.  The USB-C + damping cluster
        # sits ~20 mm up with FT232 on the y~84 connector row; these explicit
        # coordinates preserve the manual placement across regen.
        "J1": (115.9, 83.725, 0), "J10": (32.175, 114, 180), "R71": (24.175, 106, 0), "R72": (28.175, 106, 0),
        "J8": (10, 92, 0),
        # RF chain (top-right): U3 ADF4351 + U5 GVA-84 -> J3 SMA.  J3 is an
        # SMA_Amphenol_132289_EdgeMount edge-launch footprint: its origin (pad 1
        # + GND pads) sits ON the board edge so the jack body overhangs off-board
        # by ~14.5 mm and the coax launches cleanly off the edge.  The 150 mm
        # board width puts J3 at the right edge x=150 so the edge-mount has an
        # edge to launch from.  U3/U5 stay at the top-right; the 50R microstrip
        # from U5 to J3 runs down the right side.
        "U3": (92, 30, 0), "U5": (106, 40, 0), "J3": (150, 83, 0),
        "R67": (84, 26, 0), "C31": (84, 30, 0), "R68": (84, 34, 0),
        "C32": (100, 26, 0), "C33": (100, 30, 0), "R73": (100, 34, 0),
        "C34": (100, 40, 0), "C35": (110, 34, 0), "C36": (110, 40, 0), "L2": (110, 46, 0),
        # C37 = ADF4351 pin-19 TEMP decoupling (ADF_TEMP node); sits just below-right
        # of U3 (clear of the QFN-32 keepout x[88.5,95.5] y[26.5,33.5] and of
        # C33@(100,30)/C34@(100,40)/C35@(110,34)).  Without an explicit placement it
        # fell through assign_pcb_positions()'s else-branch to the (50,50) default
        # cell and collided with C_in -> DRC shorting_items +5V_IN/ADF_TEMP.
        "C37": (104, 36, 0),
        # C41 = ADF4351 pin-23 VCOM decoupling (ADF_VCOM node); sits next to the
        # C36/C37 ADF decoupling cluster below U3.  Designator C41 reused: the old
        # C41 was the AP2127K BP cap (deleted -- AP2127K-1.0 pin 4 is NC).
        "C41": (104, 46, 0),
        "J7": (10, 40, 0), "J9": (10, 26, 0),
        # ---- production-hardening pass (see add() block + §16) ----
        # USB VBUS Schottky + bulk near the USB-C connector (left of the die).
        "D_USB": (22.175, 100, 0), "C84": (30.175, 98, 0),
        # USB DP/DM series damping next to the USB-C connector so the USB-C +
        # its VBUS/CC/damping cluster is visible and reachable in a case.
        # FT232 (J1) sits on the same y~84 row (see above).  The DP/DM_F diff
        # pair runs from FT232 to here.
        "R78": (38.175, 102, 0), "R79": (42.175, 102, 0),
        # AMS1117 stability bulk + input bulk (clear of the SOT-223 courtyards
        # x[29.5,38.5]: 22uF output caps at x42, input bulk at x28)
        "C49": (40, 20, 0), "C50": (42, 28, 0), "C51": (28, 20, 0),
        # FPGA per-rail 4.7uF bulk (left of the BGA keepout x119.5, y in [40,52])
        "C52": (58, 40, 0), "C53": (58, 46, 0), "C54": (58, 52, 0),
        # FT232 decoupling (column x62: clear of the die courtyard left x69.5)
        "C55": (62, 60, 0), "C56": (62, 64, 0), "C57": (62, 56, 0),
        # XO + W25Q128 Vdd/VCC decoupling (C59 at x116: clear of TLC5947 UE2 @ x100)
        "C58": (76, 22, 0), "C59": (116, 68, 0),
        # ADF4351 per-supply 100nF (around U3 keepout x[88,96] y[26,34]: above & below)
        "C60": (84, 22, 0), "C61": (88, 22, 0), "C62": (96, 22, 0), "C63": (100, 22, 0),
        "C64": (88, 38, 0), "C65": (96, 38, 0),
        # JTAG pulls (right of J9 header x[5,15])
        "R80": (16, 22, 0), "R81": (16, 26, 0), "R82": (16, 30, 0),
        # Reset pull-down + debounce cap (J7 SW_PUSH pads reach x16.5, courtyard x18 -> x24)
        "R83": (24, 40, 0), "C68": (24, 44, 0),
        # ---- emitter driver cluster (3x TLC5947 + 3x Riref + 3x Cem) ----
        # Cluster sits at y~106/114 (shifted from the procedural y=78/86 below),
        # x ~56/79/103.  These explicit entries OVERRIDE the procedural
        # UE/Riref/Cem branches below (the `if c["ref"] in fixed` check is first)
        # so regen preserves the placement.
        "UE0": (56.25, 105.8, 0), "UE1": (78.635, 106.35, 0), "UE2": (103.35, 105.575, 0),
        "Riref0": (56.25, 113.8, 0), "Riref1": (78.635, 114.35, 0), "Riref2": (103.35, 113.575, 0),
        "Cem0": (61.25, 113.8, 0), "Cem1": (83.635, 114.35, 0), "Cem2": (108.35, 113.575, 0),
    }
    for c in COMPONENTS:
        if c["ref"] in fixed:
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = fixed[c["ref"]]
        elif c["ref"].startswith("C") and c["ref"][1:].isdigit() and 1 <= int(c["ref"][1:]) <= 20:
            i = int(c["ref"][1:]) - 1
            c["pcb_x"] = 50 + (i % 10) * 4
            c["pcb_y"] = 4 + (i // 10) * 4
            c["pcb_rot"] = 0
        elif c["ref"].startswith("C") and c["ref"][1:].isdigit() and 21 <= int(c["ref"][1:]) <= 30:
            i = int(c["ref"][1:]) - 21
            c["pcb_x"] = 50 + i * 4
            c["pcb_y"] = 12
            c["pcb_rot"] = 0
        elif c["ref"].startswith("D") and c["ref"][1:].isdigit():
            i = int(c["ref"][1:]) - 1
            c["pcb_x"] = 50 + i * 4
            c["pcb_y"] = 16
            c["pcb_rot"] = 0
        # ---- Emitter driver procedural grid (3x TLC5947 + 3x Riref + 3x Cem,
        # 9 parts) lives BELOW the die at y=78 (EM bottom-edge pads fan down to
        # the TLC5947 OUTn sinks).  The detector front-end (TIA + AD7616 +
        # Rf/Cf + DET_REF + +5V_A) is on the HEAD board -- see
        # assign_pcb_positions_head().  All spacing is sized to the largest
        # courtyard half-extent in each block so nothing overlaps: HTSSOP-32
        # x+/-4/y+/-6.5, 0603 ~+/-0.9.
        elif c["ref"].startswith("UE"):          # 3x TLC5947 LED driver (row below the die)
            j = int(c["ref"][2:])                 # UE0..2 -> x 40/60/100, y=78
            xs = [40, 60, 100]
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = xs[j], 78, 0
        elif c["ref"].startswith("Riref"):       # 3x IREF set resistor (below each TLC5947)
            j = int(c["ref"][5:])
            xs = [40, 60, 100]
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = xs[j], 86, 0
        elif c["ref"].startswith("Cem"):         # 3x TLC5947 VCC decoupling (right of each Riref)
            j = int(c["ref"][3:])
            xs = [45, 65, 105]
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = xs[j], 86, 0
        else:
            c.setdefault("pcb_x", 50)
            c.setdefault("pcb_y", 50)
            c.setdefault("pcb_rot", 0)


def assign_pcb_positions_gradient():
    """PCB placement for the EXTENDED main board (qssc-grad): run the base main-
    board placement first (so FPGA/die/emitter/RF/USB/power/BTB all land exactly
    as on the base board), then place the 7 gradient parts in free areas.

    The gradient coil Lgrad runs HORIZONTALLY (rot 0) along the die socket
    BOTTOM edge at y=70, x 70->90 (the qssc:GradCoil trace is 20 mm; its pads
    sit at (70,70)+(90,70), 5 mm below the die courtyard y65.5, spanning the
    full die width -- this trace's current produces the per-NV addressing field
    gradient across the 200 um pixels; a horizontal trace gives a vertical
    gradient = 8 distinct row frequencies).  The driver cluster (Ugrad/Udrv/
    Qgrad/Rsense + 2 decoupling caps) sits in the free gap between the die and
    the FPGA (x 92-115, y 87-101), clear of U8/R75-77 (y<70), UE2 (y106) and
    C40 (x118) -- near the FPGA for the 3-wire SPI, near the coil for the
    high-current return loop."""
    assign_pcb_positions()
    grad_fixed = {
        "Ugrad":   (126, 98, 0),    # MCP4921 DAC (SOIC-8)
        "Udrv":    (138, 98, 0),    # LM358 op-amp (SOIC-8)
        "Qgrad":   (126, 107, 0),   # IRLML2502 N-MOSFET (SOT-23-3)
        "Rsense":  (138, 107, 0),   # 1 ohm current-sense (2512 power)
        "Lgrad":   (80, 74, 0),     # gradient coil trace, horizontal along die bottom edge
        "Rgrad_feed": (70, 82, 0),  # 0R feed: +5V rail -> GRAD_COIL_P -> Lgrad pad1 (below coil)
        "C_grad1": (126, 90, 0),    # DAC Vdd decoupling (0603)
        "C_grad2": (132, 90, 0),    # DAC Vdd decoupling (0603)
    }
    for c in COMPONENTS:
        if c["ref"] in grad_fixed:
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = grad_fixed[c["ref"]]


def assign_pcb_positions_head():
    """PCB placement (mm) for the DETECTOR HEAD board (110x102 at offset 37,12, 6-layer).

    The board outline is drawn at x[37,147] y[12,114] (board_x0/board_y0 in
    HEAD_CFG) so the component coordinate system is stable -- only the
    Edge.Cuts rectangle moves to trim the board to the furthest component
    courtyard + ~3.5 mm margin on every side.  This lets the manual KiCad
    placements be reproduced verbatim (see `fixed` below) without shifting any
    footprint coordinates.

    The compressed head placement: Uchip + J12 are relocated and the +5V_A
    ferrite rail (FB_A/C_Abulk/C_ahf) + DET_REF divider (Rref1/Rref2/Cref)
    moved up off the bottom edge.  Those 8 footprints are pinned in `fixed` so
    regen reproduces the placement exactly; the elif branches below keep the
    computed positions for the rest (16x TLV9004 TIA grid, 64x Rf/Cf clustered
    at each TIA, 4x AD7616 + decoupling columns).

    ALIGNMENT CAVEAT: head Uchip(56.23,83.27)/J12(47.54,30.81) do not match
    main Uchip(80,55)/J12(110,14) -- the J12-vs-Uchip relative geometry differs,
    so the two boards will NOT mate as-is.  The main board's Uchip/J12 must be
    reconciled to the head's geometry before the sandwich assembles.  The head
    board is generated standalone-correct regardless.

    Zones (post-move): die Uchip(56,83) -> 4x4 TLV9004 TIA grid (x84-132,y20-50)
    with each TIA's 4 Rf/Cf feedback pairs clustered BESIDE it (~3mm summing-node
    traces, channels A,B left / C,D right) -> 4x AD7616 2x2 grid (y68-98 + 7-cap
    decoupling columns) -> +5V_A ferrite rail (FB_A/C_Abulk/C_ahf at y59-63)
    moved up off the bottom edge; DET_REF divider (Rref1/Rref2/Cref at y20)
    left of the TIA grid; J12(47.54,30.81).  Netlist is unchanged; only
    placement + outline moves."""
    fixed = {
        # Compressed head placement: Uchip + J12 relocated, the +5V_A ferrite
        # rail (FB_A/C_Abulk/C_ahf) and the DET_REF divider (Rref1/Rref2/Cref)
        # moved up off the bottom edge.  Pinned verbatim so regen preserves the
        # placement.
        # NOTE: Uchip + J12 here do not match main Uchip(80,55)/J12(110,14) --
        # the J12-vs-Uchip relative geometry differs, so the main board's
        # Uchip/J12 must be moved to match before the two boards will mate
        # (J12 BTB + the die-between-sockets alignment).  Flagged for
        # reconciliation; the head is generated standalone-correct regardless.
        "Uchip": (56.23, 83.27, 0),      # die TOP/detector face (B.Cu pads face the die)
        "J12": (47.54, 30.81, 0),        # BTB DAUGHTER -- see alignment note above
        "FB_A": (61.2125, 59.5, 0),      # +5V_A ferrite
        "C_5V": (57.2125, 59.5, 0),      # +5V INPUT-side bulk for FB_A (left of ferrite)
        "C_Abulk": (67.2125, 59.5, 0),   # +5V_A bulk
        "C_ahf": (67.2125, 63.5, 0),     # +5V_A HF decoupling
        "Rref1": (56.175, 20.16, 0),     # DET_REF 1.65V divider (upper leg)
        "Rref2": (60.175, 20.16, 0),     # DET_REF divider (lower leg)
        "Cref": (64.175, 20.16, 0),      # DET_REF divider filter cap
    }
    for c in COMPONENTS:
        if c["ref"] in fixed:
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = fixed[c["ref"]]
        elif c["ref"].startswith("UT"):          # 16x TLV9004 TIA (4x4 grid, 16mm col x 10mm row)
            j = int(c["ref"][2:]); col, row = j % 4, j // 4
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 84 + col * 16, 20 + row * 10, 0
        elif c["ref"].startswith("Cut"):         # 16x TLV9004 decoupling (below each TIA, near V+/V- pins)
            j = int(c["ref"][3:]); col, row = j % 4, j // 4
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 84 + col * 16, 20 + row * 10 + 5.0, 0
        elif c["ref"].startswith("UAD"):         # 4x AD7616 ADC (2x2 grid, 14mm LQFP-80), shifted +16mm y
            j = int(c["ref"][3:])
            grid = [(90, 68), (126, 68), (90, 98), (126, 98)]
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = grid[j][0], grid[j][1], 0
        elif c["ref"].startswith("CuaB"):        # AD7616 VCC bulk 10uF (per chip, decap column, +16mm y)
            j = int(c["ref"][4:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 3, 0
        elif c["ref"].startswith("Cua"):         # AD7616 VCC HF 100nF (per chip)
            j = int(c["ref"][3:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 0, 0
        elif c["ref"].startswith("Cud"):         # AD7616 VDRIVE 100nF (per chip)
            j = int(c["ref"][3:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 6, 0
        elif c["ref"].startswith("Cur"):         # AD7616 REFCAP 100nF (per chip)
            j = int(c["ref"][3:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 9, 0
        elif c["ref"].startswith("Crio"):        # AD7616 REFINOUT 100nF (per chip)
            j = int(c["ref"][4:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 12, 0
        elif c["ref"].startswith("Crgc"):        # AD7616 REGCAP 1uF (per chip)
            j = int(c["ref"][4:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 15, 0
        elif c["ref"].startswith("Crgd"):        # AD7616 REGCAPD 1uF (per chip)
            j = int(c["ref"][4:]); x = 104 if j in (0, 2) else 140
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = x, (60 if j in (0, 1) else 90) + 18, 0
        elif c["ref"].startswith("Rf"):           # 64x TIA feedback R -- clustered BESIDE its TIA (vertical)
            i = int(c["ref"][2:]); j, k = i // 4, i % 4   # TIA j, channel k (A,B,C,D)
            cx, cy = 84 + (j % 4) * 16, 20 + (j // 4) * 10
            sx = -1 if k < 2 else 1            # channels A,B left of TIA; C,D right
            sy = -1 if k in (0, 3) else 1      # A,D top (pins 1/14); B,C bottom (pins 7/8)
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = cx + sx * 5.2, cy + sy * 2.2, 90
        elif c["ref"].startswith("Cf"):           # 64x TIA feedback C -- beside its Rf (outer column, vertical)
            i = int(c["ref"][2:]); j, k = i // 4, i % 4
            cx, cy = 84 + (j % 4) * 16, 20 + (j // 4) * 10
            sx = -1 if k < 2 else 1
            sy = -1 if k in (0, 3) else 1
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = cx + sx * 7.0, cy + sy * 2.2, 90
        elif c["ref"] == "FB_A":                 # +5V_A ferrite (zone bottom-left)
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 80, 128, 0
        elif c["ref"] == "C_Abulk":              # +5V_A bulk/HF decoupling
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 86, 128, 0
        elif c["ref"] == "C_ahf":
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 86, 132, 0
        elif c["ref"] == "Rref1":               # DET_REF 1.65V divider (left of TIA grid, clear of die/J12)
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 40, 30, 0
        elif c["ref"] == "Rref2":
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 44, 30, 0
        elif c["ref"] == "Cref":
            c["pcb_x"], c["pcb_y"], c["pcb_rot"] = 48, 30, 0
        else:
            c.setdefault("pcb_x", 20)
            c.setdefault("pcb_y", 70)
            c.setdefault("pcb_rot", 0)


