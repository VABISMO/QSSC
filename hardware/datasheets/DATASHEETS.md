# QSSC — Component datasheets & verified pinouts

Local reference folder for every active component on the QSSC merged single
board (FPGA + 128-pad quantum die + 64-ch parallel detector front-end +
64-ch parallel emitter driver, all on one PCB).  Each entry has:

- the **verified pinout** (extracted from the installed KiCad 8 symbol library
  at `/usr/share/kicad/symbols/` and cross-checked against the datasheet —
  these are the pin numbers that map 1:1 to the footprint pad numbers used in
  `tools/gen_qssc_kicad.py`), and
- the **canonical datasheet URL** (download in a browser when a PDF is not
  already in `pdfs/` — many manufacturer sites block headless `curl`).

PDFs already fetched locally are in `./pdfs/`.  The BOM is in `BOM.csv`.

> **Why this folder exists.**  The headless KiCad generator bypasses the
> schematic->footprint pin-number mapping that `kicad-cli sch erc` checks, so
> a custom symbol with a wrong/non-numeric pin number silently leaves the
> footprint pad *unnetted* on the PCB (ERC green, board broken).  The 2026-09
> connection audit found exactly this class of bug (HMC441 logical pins
> VCC/GND/RF_IN never mapped to the QFN-16 numeric pads; ADF4351 VTUNE pin 20
> untied; FT232 UART on `UART_TX/UART_RX` while the FPGA balls were on
> `tx/rx`).  The pinouts below are the authoritative source the generators
> now match, so this folder is the cross-check reference for future edits.

---

## Main control board

The main board is a 6-layer HDI 210×140 mm board that sits BELOW the die,
contacting the die BOTTOM (emitter) face on its F.Cu via an **Ironwood SBT
spring-pin socket** (soldered down — F.Paste on the 65 pads — NOT ACF pressure
contact; the detector TOP face on the head board is the ACF/pogo one).  It
carries the FPGA, 3× TLC5947 emitter driver, RF chain, USB, power LDOs, the
die EM face, and the BTB HOST connector J12.

### U1 — XC7A35T-1CSG324C  (AMD/Xilinx Artix-7 FPGA)
- **Function:** digital core — UART command parser, AD7616 parallel-ADC SPI
  master (9-wire `adc_*` bus, 4 ADCs / 64 channels, the bus crosses J12 to the
  head board), 4-wire TLC5947 emitter driver (`em_sclk/sin/xlat/blank`),
  ADF4351 PLL SPI master, W25Q128 config-flash SPI, 25 MHz refclk output to
  the PLL, 1 kHz `mod_sync` lock-in reference, `mw_gate` RF-switch envelope.
  **No analog MUX, no XADC, no photon-counter comparators** — the parallel
  ADC pivot digitises every channel at the AD7616 instead of thresholding.
- **Package:** BGA-324, CSG324, 0.8 mm pitch (15x15, 324 balls). **No socket**
  — the FPGA is soldered directly to the main board (correct for production;
  the only socket in the design is the Ironwood SBT for the optical die).
- **Datasheet:** https://docs.amd.com/r/en-US/ds181-Artix-7-Data-Sheet
  (package: UG475 — CSG324 ball map).
- **Local PDF:** not fetched (AMD blocks headless download; 30 MB+).
- **Land pattern (verified vs UG475 Appendix A, 2026-09-05):** footprint =
  official KiCad `Package_BGA:Xilinx_CSG324`.  324 balls, 18x18 grid, 0.8 mm
  pitch, 15x15 mm body (F.Fab 15.10), pad copper L = 0.40 mm = UG475 L.  NSMD:
  footprint-level `solder_mask_margin 0.05` => mask opening M = 0.40 + 2x0.05 =
  0.50 mm = UG475 M (NSMD, Xilinx-recommended).  Mask web between pads =
  0.30 mm > 0.10 mm bridge rule.  Datasheet-correct, no change needed.
- **JTAG header J9 (verified vs Xilinx 10-pin pinout, 2026-09-05):** 2x5 2.54 mm
  `PinHeader_2x05_P2.54mm_Vertical`, standard Xilinx 10-pin JTAG — pin 1=TCK,
  3=TDO, 5=TMS, 7=TDI, even pins=GND, **pin 9 = VREF = +3V3** (bank-0 VCCO),
  pin 10=GND.  Pin 9 was GND (only FTDI-generic JTAG worked); now +3V3 so the
  Digilent JTAG-HS3 / Xilinx Platform Cable II programmers power their buffers.
  FPGA JTAG balls: TCK=E10, TDI=E11, TDO=E13, TMS=E12.
- **Pin map:** 324 balls, built from `.tmpwork/fpga_csg324_map.json` (power
  GND/+3V3/+1V0/+1V8, config, JTAG, user IO = `adc_convst/sclk/miso/busy/
  resetn/cs0..3` + `em_sclk/sin/xlat/blank` + UART rx/tx + ADF4351 SPI/LE/
  MUXOUT + refclk_25m + mw_gate + mod_sync).  Verified by
  `tools/check_xdc_ports.py` against `fpga/constraints.xdc`.

### U3 — ADF4351BCPZ  (Analog Devices wideband fractional-N PLL + VCO)
- **Function:** 2.87 GHz (NV⁻) / 3.46 GHz (hBN V_B⁻) ODMR microwave source.
  SPI from FPGA, 25 MHz REFIN from FPGA ball A14, loop filter -> VTUNE,
  RF_OUT_A -> GVA-84+ amp -> SMA.
- **Package:** QFN-32-1EP, 5x5 mm, 0.5 mm pitch (footprint pad 33 = EP).
- **Datasheet:** https://www.analog.com/media/en/technical-documentation/data-sheets/ADF4351.pdf
- **Local PDF:** `pdfs/ADF4351.pdf`
- **Verified pinout (32-lead LFCSP/QFN, pad# = pin#):**

| pad | name    | net on QSSC        | note |
|----:|---------|--------------------|------|
| 1   | CLK     | SPI_CLK            | SPI clock from FPGA |
| 2   | DATA    | SPI_DATA           | |
| 3   | LE      | SPI_LE             | |
| 4   | CE      | +3V3               | chip enable, active high -> always on |
| 5   | SW      | NC                 | fast-lock switch; fast-lock unused -> leave open |
| 6   | Vp      | +3V3               | charge-pump supply |
| 7   | CPout   | LF_CP              | charge-pump out -> loop filter (R67/C31) |
| 8   | CPGND   | GND                | |
| 9   | AGND    | GND                | |
| 10  | AVDD    | +3V3               | |
| 11  | AGNDVCO | GND                | |
| 12  | RF_OUT_A+ | RF_OUT          | single-ended RF out -> C34 AC-couple -> RF_IN |
| 13  | RF_OUT_A- | NC              | unused complementary out (single-ended mode) |
| 14  | RF_OUT_B+ | NC              | output B unused |
| 15  | RF_OUT_B- | NC              | output B unused |
| 16  | VVCO    | +3V3               | |
| 17  | VVCO    | +3V3               | |
| 18  | AGNDVCO | GND                | |
| 19  | TEMP    | ADF_TEMP           | temperature-compensation OUTPUT — **decoupled by C37 100nF to GND per datasheet Table 5 (NOT no-connect; the ADF4351 has NO REFIN- pin, the reference is single-ended on REFIN pin 29)** |
| 20  | VTUNE   | LF_OUT             | **loop-filter output drives VCO tune — closes the PLL loop** |
| 21  | AGNDVCO | GND                | |
| 22  | RSET    | RSET               | R73 4.7k -> GND |
| 23  | VCOM    | GND                | VCO common |
| 24  | VREF    | VREF               | internal reference node — **decoupled by C36 100nF to GND, NOT tied direct** |
| 25  | LD      | NC                 | lock detect, unused |
| 26  | PDB_RF  | +3V3               | **RF power-down is active-LOW: tie HIGH = RF enabled** |
| 27  | DGND    | GND                | |
| 28  | DVDD    | +3V3               | |
| 29  | REFIN   | refclk_25m         | 25 MHz from FPGA ball A14 |
| 30  | MUXOUT  | ADF_MUXOUT         | -> FPGA status |
| 31  | SDGND   | GND                | |
| 32  | SDVDD   | +3V3               | |
| 33  | EP      | GND                | exposed pad |

### U5 — GVA-84+  (Mini-Circuits RF MMIC amplifier)  *(replaces HMC441LP3E)*
- **Function:** RF power amp between the ADF4351 RF_OUT and the SMA antenna
  port.  DC-7 GHz, +18.4 dB @2 GHz, +20.6 dBm P1dB @2 GHz, self-biased from
  +5V (no VGG).  The old HMC441LP3E was 6.5-13.5 GHz (wrong band) AND its
  custom symbol used logical pin names that never mapped to the QFN-16 pads,
  so it was completely unnetted — replaced 2026-09.
- **Package:** SOT-89-3 (Mini-Circuits case DF782).  KiCad footprint pads
  1/2/3; pad 2 is the large centre pad that doubles as the GND paddle (pin 4).
- **Datasheet:** https://www.minicircuits.com/WebStore/dashboard.html?model=GVA-84%2B
- **Local PDF:** `pdfs/GVA-84_plus.pdf`
- **Verified pinout:**

| pad | name        | net on QSSC   | note |
|----:|-------------|---------------|------|
| 1   | RFIN        | RF_IN         | DC-blocked by C34 from ADF4351 RF_OUT |
| 2   | GND         | GND           | (+ paddle pin 4) |
| 3   | RFOUT/DC-IN | RF_AMP_OUT    | +5V_RF bias via L2 100nH choke; RF out via C35 -> J3 SMA |
- **Application circuit (self-biased):** Cblock on RFIN (C34 100p) and RFOUT
  (C35 100p); RFC choke L2 100nH from +5V_RF to pin 3; Cbypass on the +5V_RF
  side is the rail decoupling (C45-C48 pi filter).  No VGG resistor (the old
  R69 was removed).

### U8 — W25Q128JVSIQ  (Winbond 128 Mbit SPI flash)
- **Function:** FPGA configuration bitstream storage (QSPI x4 boot).
- **Package:** SOIC-8 3.9x4.9 mm.
- **Datasheet:** https://www.winbond.com/resource-files/w25q128jv%20revh%2004082019%20plus.pdf
- **Local PDF:** not fetched (Winbond serves a JS viewer; download in browser).
- **Verified pinout:**

| pad | name    | net      |
|----:|---------|----------|
| 1   | CS      | SPI_CS   |
| 2   | DO/D1   | SPI_D1   |
| 3   | WP/D2   | SPI_D2   |
| 4   | GND     | GND      |
| 5   | DI/D0   | SPI_D0   |
| 6   | CLK     | SPI_CCLK |
| 7   | HOLD/D3 | SPI_D3   |
| 8   | VCC     | +3V3     |

### J1 — FT232RL  (FTDI USB-to-UART bridge)
- **Function:** USB config/debug UART to the FPGA.
- **Package:** SSOP-28.
- **Datasheet:** https://www.ftdichip.com/Support/Documents/DataSheets/ICs/DS_FT232R.pdf
- **Local PDF:** not fetched (FTDI blocks headless; download in browser).
- **Verified pinout (key pins):**

| pad | name | direction | net on QSSC | note |
|----:|------|-----------|-------------|------|
| 1   | TXD  | output    | UART_RX     | -> FPGA rx (P17). Net is FPGA-perspective: the FT232 *transmit* drives the FPGA *receive* line. |
| 4   | VCCIO| power     | +3V3        | I/O supply |
| 5   | RXD  | input     | UART_TX     | <- FPGA tx (P18). |
| 15  | USBD+ | bidir    | USB_DP_F    | -> R78 series-damping -> USB_DP -> USB-C |
| 16  | USBD- | bidir    | USB_DM_F    | -> R79 series-damping -> USB_DM -> USB-C |
| 17  | 3V3OUT| output   | 3V3OUT      | internal regulator out — **decoupled by C57 100nF to GND** (regulator stability; NOT no-connect) |
| 19  | RESET#| input    | +3V3        | internal pull-up; tied HIGH |
| 20  | VCC  | power    | +3V3        | core supply — the ONLY VCC pad on the SSOP-28 (pin 24 is a no-bond NC, not a 2nd VCC) |
| 7,18,21,25,26 | GND/AGND | power | GND | bond-pad grounds (pin 25 = AGND, the analog/regulator return) |
| 2,3,6,9,10,11,12,13,14,22,23,24,27,28 | DTR/RTS/CBUS/PWREN/LED/OSC/TEST | — | NC | unused modem control / CBUS / PWREN# / LED drive / crystal / test — `(no_connect)` per datasheet |
- **Connection note:** the FPGA ballmap ports are `tx`/`rx`; `fpga_net_map()`
  aliases them to `UART_TX`/`UART_RX` so the FT232 and FPGA share one net
  (the prior name mismatch silently disconnected the UART). Pins 23 (CBUS/PWREN#)
  and 24 (no-bond NC) are intentionally `(no_connect)` — the SSOP-28 has ONE VCC
  pad (pin 20) and pin 25 is AGND; an earlier draft of this table wrongly listed
  pin 24 as a second VCC and pin 23 as AGND, which the generator (verified vs the
  KiCad `Interface_USB:FT232RL` symbol that omits pads 8 & 24) does NOT do.

### J10 — USB-C receptacle (GCT USB4105-16P)
- **Function:** USB + 5V power input.
- **Package:** USB-C 16-pin top-mount horizontal.
- **Datasheet:** https://www.gct.cc/connector/usb4105/
- **Local PDF:** not fetched.
- **Verified pinout (used pins):** A4/A9/B4/B9 = +5V_IN (VBUS), A1/A12/B1/B12
  + S1 = GND, A6/B6 = USB_DP, A7/B7 = USB_DM, A5 = CC1, B5 = CC2.  R71/R72
  5.1k pull CC1/CC2 to GND (USB-C device-role pulldowns).  TX/RX pairs and
  SBU unused (NC).

### REG1 / REG2 — AMS1117-3.3 / AMS1117-1.8  (AMS LDO)
- **Function:** +3V3 and +1V8 rails from the +5V barrel input.
- **Package:** SOT-223-3 (tab = pin 2 = VO).
- **Datasheet:** https://www.mouser.com/datasheet/2/260/AMS1117-Datasheet-1273816.pdf
- **Local PDF:** not fetched.
- **Verified pinout:** 1 = GND/ADJ, 2 = VO (output, tab), 3 = VI (input).

### REG3 — AP2127K-1.0  (Diodes Inc 1.0V LDO)
- **Function:** +1V0 FPGA core rail (AMS1117 has no 1.0V option, so this real
  1.0V LDO is used instead).
- **Package:** SOT-23-5.
- **Datasheet:** https://www.diodes.com/assets/Datasheets/AP2127K.pdf
- **Local PDF:** not fetched (Diodes Inc blocks headless).
- **Verified pinout:** 1 = VIN, 2 = GND, 3 = EN, 4 = BP (bypass), 5 = VOUT.

### Y1 — ASE-100MHz  (Abracon TCXO)
- **Function:** 100 MHz FPGA main clock (clkin).
- **Package:** 4-pin 3.2x2.5 mm SMD oscillator.
- **Datasheet:** https://abracon.com/Oscillators/ASE.pdf
- **Local PDF:** not fetched.
- **Verified pinout:** 1 = EN (tri-state/enable), 2 = GND, 3 = OUT, 4 = Vdd (+3V3).

### FB1 — Ferrite bead 600Ω@100MHz
- **Function:** +5V -> +5V_RF isolation (replaces the old REG4 AMS1117-5.0
  LDO which sat in dropout; the +5V rail is already regulated, so +5V_RF only
  needs a bead + pi filter, not another regulator).
- **Package:** 0603.  C45/C46 buffer +5V side, C47/C48 buffer +5V_RF side.

### UE0 / UE1 / UE2 — TLC5947  (Texas Instruments 24-ch 12-bit PWM constant-current LED driver) x3
- **Function:** 64-channel parallel CQD emitter driver.  Each CQD emitter
  cathode (`emitter_cat{i}`) sinks into one TLC5947 OUTn; the 64 anodes share
  one common `EM_ANODE` pad -> the **+12V_EMIT** rail.  12-bit PWM grayscale
  per channel -> any combination of emitters fires in parallel at independent
  intensities.  Replaces the old 8x 74HCT240 + 64x AO3401A P-MOS matrix
  (208 parts -> 3 chips) and frees ~60 FPGA GPIO.  3 chips daisy-chain
  SIN->SOUT for 72 channels (64 used; channels 64-71 = 0).
- **Package:** HTSSOP-32-1EP, 6.1x11 mm, 0.65 mm pitch (EP = GND, thermal vias).
- **Datasheet:** https://www.ti.com/lit/ds/symlink/tlc5947.pdf  (TI SBVS114B)
- **Local PDF:** `pdfs/TLC5947.pdf`
- **Key specs:** VCC = 3.0-5.5 V (run +3V3); OUT pins sink up to **33 V / 38 mA
  independent of VCC** (so a 12 V anode rail is fine with 3.3 V logic); internal
  4 MHz grayscale oscillator -> **no Gsclk pin**; 30 MHz max SCLK.  Output
  current I_OUT = 41 x 1.20 / R_IREF -> **Riref0-2 = 4.87 kΩ for ~10.1 mA**
  (inside the CQD 2-20 mA/pixel spec, `docs/FABRICATION_PROTOCOL.md`).
- **Verified pinout:** 1=GND, 2=BLANK, 3=SCLK, 4=SIN, 5..28=OUT0..OUT23,
  29=SOUT, 30=XLAT, 31=IREF, 32=VCC, 33=EP(GND).  Shift order: 864 bits, ch71
  (chip2 OUT23) MSB first -> ch0 (chip0 OUT0) LSB last.
- **Anode rail note:** the CQD EL turn-on is 4-8 V (visible by ~10 V) per the
  fabrication protocol, so a +5V anode is insufficient headroom for the
  constant-current sink.  The +12V_EMIT rail gives compliance for any CQD V_f
  up to ~11 V.  At low V_f the TLC5947 burns the excess (≤ ~2 W/IC at 64x10 mA
  full-on) — the HTSSOP EP + thermal vias handle typical PWM duty.

### J4 / F2 — +12V_EMIT emitter anode supply input
- **Function:** dedicated 12 V LED supply for the 64 CQD emitters' common
  anode (EM_ANODE -> +12V_EMIT).  Separate from the +5V logic rail because the
  CQD V_f (4-8 V turn-on) exceeds 5 V.  J4 is a 2.1 mm barrel jack (12 V wall
  wart); F2 a 1 A PTC; C85 22 uF bulk + C86 100 nF HF decouple the rail at the
  die anode fanout.  This is the textbook TLC5947 application circuit (separate
  higher-voltage LED supply, OUT pins tolerate 33 V).
- **Package:** J4 = BarrelJack CUI PJ-102AH (same as J2); F2 = Fuse 1206.
- **Net:** J4.1 -> +12V_EMIT_IN -> F2 -> +12V_EMIT -> EM_ANODE pad (die).

### J12 — BTB_HOST connector (board-to-board, 2×10 pin header)
- **Function:** the HOST side of the board-to-board connector that mates the
  main board to the detector head.  Carries the 9-wire AD7616 SPI bus
  (`adc_convst`, `adc_sclk`, `adc_miso`, `adc_busy`, `adc_resetn`,
  `adc_cs0..3`) plus GND / +3V3 / +5V.  The FPGA's `adc_*` pins land on J12
  instead of an on-board ADC; the ADCs themselves live on the head board.
  Every SPI signal is GND-flanked for a clean return path (see pin map below).
- **Package:** 2×10 through-hole pin header, 2.54 mm pitch, 1.0 mm drill,
  pads on `*.Cu *.Mask`.  MPN Würth 61302011121 (male, host).
- **Pin map (identical net names on both boards):** 1=GND 2=+3V3 3=adc_convst
  4=GND 5=adc_sclk 6=GND 7=adc_miso 8=GND 9=adc_busy 10=GND 11=adc_resetn
  12=GND 13=adc_cs0 14=adc_cs1 15=adc_cs2 16=adc_cs3 17=+5V 18=+5V 19=GND
  20=GND.

### Passives / misc (main board)
- C1-C20 100nF, C21-C30 10uF, C40-C48 — rail decoupling.
- C31 220p, C32 10n, C33 220p, R67 1k, R68 2.2k — ADF4351 loop filter.
- C34/C35 100p — RF AC-coupling.  L2 100nH — RF choke.  R73 4.7k — RSET.  C36 100n — VREF decoupling.
- R70 330 + LED1 green — power indicator.  R71/R72 5.1k — USB-C CC pulldowns.
- R75/R76/R77 4.7k — config pull-ups.  D1-D10 ESD5V0 — USB/IO ESD protection.
- F1 2A PTC — +5V barrel input polyfuse.  F2 1A PTC — +12V_EMIT input polyfuse.
  J2 5V barrel jack, J4 12V LED barrel jack, J3 SMA edge-mount, J7 reset
  button, J8 mw_gate header, J9 JTAG header, J10 USB-C, J12 BTB_HOST.
- UE0-2 TLC5947 — Riref0-2 4.87k (I_SET), Cem0-2 100nF (VCC decoupling).

---

## Detector head board

The detector head is a 4-layer 150×140 mm board that sits ABOVE the die,
contacting the die TOP (detector) face on its B.Cu via ACF pressure contact
(no B.Paste — it is a contact interface, not soldered).  It carries the 64-ch
TIA front-end, 4× AD7616 ADCs, and the BTB DAUGHTER connector that returns the
9-wire SPI + power to the main board.  Head board origin = main board origin
+ (60, 0) mm, so the head J12 (50,14) == main J12 (110,14) and the head die
(20,55) == main die (80,55) — the two PCBs sandwich the die with J12 as the
edge spacer + electrical link.  No FPGA, no RF, no USB, no emitter driver.

### UT0..UT15 — TLV9004  (Texas Instruments quad CMOS op-amp) x16
- **Function:** 64-channel transimpedance front-end for the CQD
  photodetectors.  One TLV9004 = 4 channels; 16 ICs = 64.  Each detector
  `DET_IN{i}` (from the die TOP face) -> TIA (Rf 1 MΩ ‖ Cf 1 pF,
  virtual-ground ref DET_REF = 1.65 V) -> `DET_TIA{i}` to one AD7616 analog
  input.  Photovoltaic / zero-bias mode (the detectors output current).
- **Package:** TSSOP-14, 4.4x5 mm, 0.65 mm pitch.
- **Datasheet:** https://www.ti.com/lit/ds/symlink/tlv9004.pdf
- **Local PDF:** not fetched (TI serves a JS viewer; download in browser)
- **Verified pinout:** 4=V+ (+3V3), 11=V- (GND); per-channel: 1=OUT_A, 2=-IN_A,
  3=+IN_A, 5=+IN_B, 6=-IN_B, 7=OUT_B, 8=OUT_C, 9=-IN_C, 10=+IN_C, 12=+IN_D,
  13=-IN_D, 14=OUT_D.

### UAD0..UAD3 — AD7616  (Analog Devices 16-ch simultaneous-sampling ADC) x4
- **Function:** the parallel digital readout.  Each AD7616 samples 16 channels
  simultaneously (16-bit, up to 1 MSPS), so 4 ICs = 64 detector channels.  The
  16 TIA outputs `DET_TIA{i}` feed one AD7616's 16 analog inputs (V0..V15 on
  UAD0..UAD3).  The FPGA drives the 9-wire SPI (`adc_convst`, `adc_sclk`,
  `adc_resetn`, `adc_cs0..3`) and reads `adc_miso` / `adc_busy` — the same bus
  that crosses J12 to the main board.  This replaces the old 16× LMV339
  photon-counter comparator + MCP4725 threshold DAC: the ADC digitises the
  full analog waveform, not just a threshold crossing.
- **Package:** LQFP-80, 14×14 mm, 0.65 mm pitch.
- **Datasheet:** https://www.analog.com/media/en/technical-documentation/data-sheets/ad7616.pdf
- **Local PDF:** not fetched.
- **Verified pinout:** 16 analog inputs V0..V15; SPI: CONVST, SCLK, DOUT/RD
  (MISO), BUSY, RESET, CS; REFIO/REFCAP/REFSEL set the internal 5 V or 2.5 V
  reference; REFC1/REFC2 + RGC1/RGC2 set the ±10 V / ±5 V input range;
  VDRIVE = +3V3 (logic), VCC = +5V_A (analog).  Per-ADC decoupling: Cua (VCC),
  Cud (VDRIVE), Cur (REFCAP), Crio (REFIO), Crgc/Crgd (range), CuaB (analog
  input common-mode bypass).

### J12 — BTB_DAUGHTER connector (board-to-board, 2×10 socket)
- **Function:** the DAUGHTER side of the board-to-board connector (mates the
  BTB_HOST on the main board).  Identical pin map and net names to J12 on the
  main board — the connector physically bridges the two boards' same-named
  nets.  Brings in +3V3 / +5V / GND and carries the 9-wire AD7616 SPI back to
  the FPGA.  The rails arrive via the connector (no local regulator), so
  PWR_FLAGs `#FLG9` (+5V_A) / `#FLG10` (+3V3) / `#FLG11` (+5V) silence ERC on
  the connector-fed rails.
- **Package:** 2×10 through-hole socket, 2.54 mm pitch, 1.0 mm drill, pads
  on `*.Cu *.Mask`.  MPN Würth 61002021121 (female, daughter).  The long pins
  of the host header set the stacking height (die thickness + ACF contact
  pressure).
- **+5V_A rail:** +5V from J12 -> FB_A (600 Ω ferrite) -> +5V_A -> the 4
  AD7616 analog VCC; C_Abulk 10 µF + C_ahf 100 nF decouple it locally.

### Passives / misc (head board)
- UT0-15 TLV9004 — Cut0-15 100nF (VCC decoupling).  Rf0-63 1 M + Cf0-63 1 p —
  TIA feedback.  Rref1/Rref2 5.1k + Cref — DET_REF = 1.65 V virtual-ground
  divider.
- UAD0-3 AD7616 — Cua/CuaB/Cud/Cur/Crio/Crgc/Crgd per-ADC decoupling.
- FB_A 600 Ω ferrite + C_Abulk 10 µF + C_ahf 100 nF — +5V_A analog rail.

---

## Quantum die (two-faced, no TSV — sandwiched between two PCBs)

The QSSC die is a **true two-faced die with no through-silicon vias (TSVs)**:
64 detector electrodes on the TOP face, 64 emitter cathodes + 1 shared anode
on the BOTTOM face.  Because the bottom face cannot be wire-bonded face-up, the
die is **clamped between two PCBs** — a main board (below, contacting the
bottom/emitter face) and a detector head (above, contacting the top/detector
face) — joined by the J12 board-to-board connector.  Each face is contacted by
a different mechanism (the die is **removable**):

- **Emitter face (main board, die BOTTOM) — Ironwood SBT spring-pin socket**
  (soldered, see `### Uchip — Ironwood_SBT socket` below).  NOT ACF.
- **Detector face (head board, die TOP) — ACF (anisotropic conductive film)
  pressure contact**, not solder.

### Uchip — Ironwood_SBT socket (main board) + QSSC_Chip_DET (head board)
- **Function:** the project's own fabricated quantum die — 64 top-face
  detector electrodes + 64 bottom-face emitter electrodes, driven and read in
  PARALLEL (no ADG732 MUX, no serial sweep, no XADC).  No commercial
  datasheet.  See `docs/FABRICATION_PROTOCOL.md` for the CQD stack
  (ITO shared anode / Ag per-pixel cathode) and EL specs (turn-on 4-8 V,
  drive 2-20 mA/pixel, ~520-540 nm green).
- **Die:** 20 x 20 mm, 129 pads @ 0.30 mm pitch (0.15 x 0.30 mm): 64 DET pads
  on the TOP face + 64 EM cathode pads on the BOTTOM face + 1 EM_ANODE pad
  (the shared common anode).
- **Two footprints, one die:** `Ironwood_SBT` places the 64 EM cathodes +
  EM_ANODE on the **main board F.Cu + F.Paste + F.Mask** — these are the
  SOLDER pads of a commercial **Ironwood SBT** open-top / clamshell spring-pin
  socket (the die sits emitter-down in the socket; spring pins contact the die
  bottom pads; lid clamps it; die is removable).  `QSSC_Chip_DET` places the
  64 DET pads on the **head board B.Cu + B.Mask** (no B.Paste — ACF contact
  interface, not soldered).  The two boards' origins are offset by (60, 0) mm
  so the two die footprints align on the physical die.
- **Why a socket on the emitter face:** the user required a real **commercial
  socket that fits the 20×20 mm / 0.30 mm-pitch die**.  No off-the-shelf socket
  exists at that pitch/size (Yamaichi IC609 caps at 0.40 mm / ≤12 mm, Enplas
  at 0.50 mm / 4×4 mm).  **Ironwood Electronics SBT** open-top spring-pin
  sockets are **made-to-order** and explicitly cover 0.30 mm pitch over IC
  bodies 1–50 mm — quoted against the die drawing.  +2.5 mm/side body →
  25×25 mm, open-top/clamshell, hardware/screw mount, >100 k insertion cycles,
  −55…+150 °C, <100 mΩ/pin.  MPN = "SBT custom (quote)" (mechanical, non-Digi-Key
  BOM line).  The detector face stays ACF (lowest parasitic C at the TIA input;
  a soldered socket there would add pin parasitics in the photocurrent path).
- **Emitter topology (common-anode, validated against the fab protocol):**
  the 64 EM pads are per-pixel CATHODES (net `emitter_cat{i}`), each sunk by
  one TLC5947 OUTn constant-current channel (3-chip daisy chain, 72 ch, 64
  used).  The single EM_ANODE pad is the shared common anode on the
  **+12V_EMIT** rail — a separate 12 V LED supply (J4 barrel + F2 1A PTC +
  C85/C86), because the CQD EL turn-on is 4-8 V and the old +5V anode left no
  headroom; the TLC5947 OUT pins tolerate 33 V, so 12 V is textbook headroom.
- **Detector path:** the 64 DET pads (`DET_IN{i}`) on the head board B.Cu ->
  16× TLV9004 TIA (per-channel Rf/Cf) -> `DET_TIA{i}` -> 4× AD7616 ADC (16 ch
  each) -> 9-wire SPI (`adc_*`) across J12 -> the FPGA on the main board.
  This replaces the old 16× LMV339 photon-counter comparator + MCP4725
  threshold DAC: the AD7616 digitises the full analog waveform per channel,
  not just a threshold crossing.
- **Netclass:** "Chip" 0.10 mm clearance (the 0.30 mm-pitch dog-bone fanout
  requires it).  Fanout: 0.15 mm stub on the pad's own layer -> 0.20 mm
  through-via per pad, staggered even/odd (0.5 / 1.0 mm out), escaped on the
  opposite layer / inner signal layers.  EM pads escape on B.Cu (main), DET
  pads escape on F.Cu (head).  The dense 0.30 mm field is beyond the 0.2 mm
  line-router grid, so the via<->target escapes are routed by FreeRouting;
  the dog-bone fanout itself (pad -> stub -> via) is DRC-clean and in place.
- **Datasheet:** Ironwood SBT spring-pin test sockets (made-to-order, quoted
  against the die drawing): <https://www.ironwood.com/pqfp-spring-pin-test-sockets/>

---

## KiCad footprints & 3D models

All 2D footprints used by both sandwich boards are present in the installed
KiCad 8 libraries at `/usr/share/kicad/footprints/` (verified — no custom
.pretty needed except the FPGA BGA `Xilinx_CSG324`, the `QSSC_Chip_EM` /
`QSSC_Chip_DET` die faces, and the `Conn_02x10_BTB` connector, all generated
into the project lib `qssc-lib.kicad_sym`).  **No 3D `.step`/`.wrl` models
are installed**; they are optional (visualisation only, not needed for DRC or
fabrication).  If wanted, fetch from
https://gitlab.com/kicad/libraries/kicad-3d-models (-3d-models) and point
`KICAD6_3DMODEL_DIR`/the 3D path at them.  See `HARDWARE_DESIGN.md` §15.