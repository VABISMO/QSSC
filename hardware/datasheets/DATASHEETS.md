# QSSC — Component datasheets & verified pinouts

Local reference folder for every active component on the QSSC merged single
board (FPGA + 130-pad quantum die + 64-ch parallel detector front-end +
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

The main board is a 6-layer HDI 150×118 mm board that sits BELOW the die,
contacting the die BOTTOM (emitter) face through the **`ChipSocket_2.54_EM`
through-hole socket** (65 PTH pads + 4 NPTH mount holes, no paste; the detector
TOP face on the head board uses the matching `ChipSocket_2.54_DET`).  It
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
  the only sockets are the two through-hole die sockets (ADR-0009)).
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
  SPI from FPGA, 25 MHz REFIN from FPGA ball A8, loop filter -> VTUNE,
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
| 29  | REFIN   | refclk_25m         | 25 MHz from FPGA ball A8 |
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
| 1   | TXD  | output    | UART_RX     | -> FPGA rx (C1). Net is FPGA-perspective: the FT232 *transmit* drives the FPGA *receive* line. |
| 4   | VCCIO| power     | +3V3        | I/O supply |
| 5   | RXD  | input     | UART_TX     | <- FPGA tx (B1). |
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

### Y1 — 100 MHz XO  (Abracon ASE-100.000MHZ-LR)
- **Function:** 100 MHz FPGA main clock (clkin).
- **Package:** 4-pin 3.2x2.5 mm SMD oscillator.
- **Datasheet:** https://abracon.com/Oscillators/ASE.pdf
- **Local PDF:** not fetched.
- **Verified pinout:** 1 = EN (tri-state/enable), 2 = GND, 3 = OUT, 4 = Vdd (+3V3).

### FB1 — Ferrite bead 600Ω@100MHz
- **Function:** +5V -> +5V_RF isolation (replaces the old REG4 AMS1117-5.0
  LDO which sat in dropout; the +5V rail is already regulated, so +5V_RF only
  needs a bead + pi filter, not another regulator).
- **Package:** FB1 0603.  Pi-filter caps are mixed on the board: C45/C47 =
  0805, C46/C48 = 0603 (verified against the committed footprints).

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

The detector head is a 6-layer 110×102 mm board that sits ABOVE the die,
contacting the die TOP (detector) face through the **`ChipSocket_2.54_DET`
through-hole socket** (65 PTH pads + 4 NPTH, no paste).  It carries the 64-ch
TIA front-end, 4× AD7616 ADCs, and the BTB DAUGHTER connector that returns the
9-wire SPI + power to the main board.  **Registration (measured, OPEN):** the
head's Uchip (56.23, 83.27) / J12 (47.54, 30.81) do NOT reproduce the main
board's Uchip (80, 55) / J12 (110, 14) — J12 − Uchip is (−8.69, −52.46) mm on
the head vs (+30, −41) mm on the main, so the two PCBs do not mate as-is; the
main placement must be reconciled to the head's (`tools/kicad/placement.py`,
HARDWARE_DESIGN §15D F10).  No FPGA, no RF, no USB, no emitter driver.

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
  of the host header set the stacking height (die-module thickness + the
  two socket stack heights).
- **+5V_A rail:** +5V from J12 -> FB_A (600 Ω ferrite) -> +5V_A -> the 4
  AD7616 analog VCC; C_Abulk 10 µF + C_ahf 100 nF decouple it locally.

### Passives / misc (head board)
- UT0-15 TLV9004 — Cut0-15 100nF (VCC decoupling).  Rf0-63 1 M + Cf0-63 1 p —
  TIA feedback.  Rref1/Rref2 10k + Cref — DET_REF = 1.65 V virtual-ground
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

- **Emitter face (main board, die BOTTOM) — `ChipSocket_2.54_EM`** through-hole
  2.54 mm socket (no paste; see `### Uchip — through-hole die sockets` below).
- **Detector face (head board, die TOP) — `ChipSocket_2.54_DET`**, the matching
  through-hole socket (no paste, mates with the same module).

### Uchip — through-hole die sockets (main `ChipSocket_2.54_EM` + head `ChipSocket_2.54_DET`)
- **Function:** the project's own fabricated quantum die — 64 top-face
  detector electrodes + 64 bottom-face emitter electrodes, driven and read in
  PARALLEL (no ADG732 MUX, no serial sweep, no XADC).  No commercial
  datasheet.  See `docs/FABRICATION_PROTOCOL.md` for the CQD stack
  (ITO shared anode / Ag per-pixel cathode) and EL specs (turn-on 4-8 V,
  drive 2-20 mA/pixel, ~520-540 nm green).
- **Die:** 20 x 20 mm, 130 pads @ 2.5 mm pitch (65 per face): 64 DET pads +
  DET_COMMON on the TOP face, 64 EM cathode pads + EM_ANODE on the BOTTOM
  face (one shared common per face).
- **Two footprints, one module:** `ChipSocket_2.54_EM` places 65 through-hole
  pads + 4 NPTH mount holes on the **main board** (no F.Paste — the header
  pins solder through the board) to receive the module's emitter-side pins;
  `ChipSocket_2.54_DET` is the matching **head board** footprint (65 PTH +
  4 NPTH, no paste) for the detector-side pins.  Both faces stay removable
  together (ADR-0009).  NOTE: the two boards' socket and J12 positions are
  NOT yet concentric — see the registration note in `## Detector head` above.
- **Why a socket here:** the die module (P1-style pin scaffold, 2.5 mm pad
  pitch on the 20 mm body) presents **2.54 mm male pins** — an 8×8 grid, a
  standard 0.1-inch pattern — so both boards take an **off-the-shelf 2.54 mm
  female header** (17.78 mm span, catalogue part, no made-to-order
  dependency).  The die module stays removable from both faces, keeping the
  most expensive part of the stack reworkable.  ADR-0009 records the change
  and supersedes the earlier Ironwood SBT quote-only design (ADR-0006).
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
- **Netclass:** "Chip" 0.10 mm clearance (the HDI dog-bone fanout requires
  it).  Fanout: 0.15 mm stub on the pad's own layer -> 0.20 mm through-via per
  pad at a **uniform 1.0 mm out** (`STUB = 1.0 mm` in `tools/router/fanout.py`;
  0.80 mm to the neighbour), escaped on the opposite layer / inner signal
  layers.  EM pads escape on B.Cu (main), DET pads escape on F.Cu (head).  The
  via<->target escapes are routed by FreeRouting; the dog-bone fanout itself
  (pad -> stub -> via) is DRC-clean and in place.
- **Datasheet:** generic 2.54 mm female header (catalogue part; footprints
  `qssc:ChipSocket_2.54_EM` / `qssc:ChipSocket_2.54_DET`) — no vendor-specific
  document.  Design history in ADR-0009 (supersedes ADR-0006).

---

## Gradient coil + DAC on the main board — Invention 5

> **On the published main board:** the field-gradient block ships as part of
> `qssc.kicad_*` itself (the main IS the extended build — no third board);
> its BOM rows are `main` rows in `BOM.csv` (round-12: `gen_bom` emits exactly
> the two published boards, so the gradient parts sit under the single
> complete `main` section — there is no separate `grad` column).

The gradient block is the **main board + an Invention 5 field-gradient
coil and its current driver** — generated from
the same `tools/gen_qssc_kicad.py` (`build_gradient_board()` calls
`build_main_board(grad=True)` then adds the 7 parts below; it emits the
complete main).  The gradient
is the **gate** that makes per-NV frequency addressing work at 300 K: the
design target is one datasheet linewidth (5 MHz) of separation per addressable
NV across the 200 µm pixel, so an N-line register needs ~N·5 MHz = N·0.18 mT
(at the NV gyromagnetic ratio γ = 28 MHz/mT); at that detuning `qssc/dipole.py`
puts the addressing leakage at 0.06×, and `ODMR_SWEEP` then counts the resolved
lines to *measure* the per-pixel register width (the design-N / addressable-N
upper bound).  The actual field the 1.5 mm PCB trace produces at a given
`I_coil` is NOT QUOTED BY THE SUPPLIER — it is set by the coil geometry and
measured by the ODMR line count.  The coil is a 1.5 mm PCB copper trace on
F.Cu (free — no wound part), driven by a unipolar magnitude-programmable
current sink: a 12-bit SPI DAC sets the current
(`I_coil = Vdac / Rsense`), an op-amp servos the MOSFET gate to hold that
current, and writing code 0 zero-cuts the coil.  Full copper routing is a
follow-up (the base board was routed separately via FreeRouting); the
schematic + netlist + placement are DRC 0/0/0.

### Ugrad — MCP4921  (Microchip, 12-bit SPI DAC, 8-pin SOIC)
- **Function:** sets the gradient-coil current.  12-bit voltage-output DAC,
  VOUTA = VREF · code/4096 (1× gain).  VREF tied to +3V3 (= VDD, issue
  #168-C: VIH = 0.7·VDD must stay under the LVCMOS33 master's 3.3 V VOH) →
  0..3.3 V out → 0..3.3 A through the 1 Ω sense resistor — still past the
  ~2.2 A clean-addressing current (the 5 W Rsense ceiling), so no usable
  drive range is lost; the ~0.56 A resolvable-measurement current stays
  mid-range (code ~693); full-scale code 4095 → 1.47 MHz/µm of line spread.
- **Verified pinout (8-pin SOIC/PDIP/MSOP, DS22248A / DS21897B — confirmed
  against the downloaded `pdfs/MCP4921.pdf`):**

  | pin | name  | type    | net            | note                                    |
  |-----|-------|---------|----------------|-----------------------------------------|
  | 1   | VDD   | power   | +3V3           | #168-C: level-compatible with the SPI master |
  | 2   | !CS   | input   | grad_cs        | FPGA SPI CS (idle high, go-low-then-latch)|
  | 3   | SCK   | input   | grad_sclk      | FPGA SPI clock                          |
  | 4   | SDI   | input   | grad_mosi      | FPGA SPI data                           |
  | 5   | !LDAC | input   | GND            | tied low = transparent (update on CS↑)  |
  | 6   | VREF  | input   | +3V3           | tied to VDD (full scale 3.3 V, #168-C)   |
  | 7   | AVSS  | power   | GND            |                                         |
  | 8   | VOUTA | output  | GRAD_DAC_OUT   | → LM358 non-inverting input             |

  **The 8-pin single MCP4921 has NO !SHDN pin** (shutdown is SPI write-word
  bit 12, kept =1 active by `dac_ctrl.v`'s `{4'b0011, code}` framing — the
  14-pin MCP4922 is the one with a SHDN pin).  The coil is cut by writing
  code 0 → VOUTA = 0 → op-amp → MOSFET off.  `grad_en` is an FPGA STATUS
  output only (the ball stays NC on the PCB — a spare for a future high-side
  enable); it does **not** wire to any DAC pin.
- **SPI write word:** `{4'b0011, code[11:0]}` — bit15=0 (write), bit14=0
  (unbuffered VREF), bit13=1 (1× gain), bit12=1 (active/!SHDN), bits11:0=data.
- **Datasheet:** <https://ww1.microchip.com/downloads/en/DeviceDoc/22248A.pdf> (`pdfs/MCP4921.pdf`)

### Udrv — LM358  (TI, dual op-amp, 8-pin SOIC)
- **Function:** current-servo.  Amp A: INA+ = DAC VOUTA, INA− = sense voltage
  (GRAD_SENSE), OUTA = MOSFET gate → the loop holds V_sense = V_dac, i.e.
  I_coil = Vdac/Rsense.  Amp B parked as a unity follower (INB+ = GND,
  INB− = OUTB) so the unused half cannot oscillate.
- **Verified pinout (8-pin SOIC, standard LM358 dual op-amp):**

  | pin | name  | net            |
  |-----|-------|----------------|
  | 1   | OUTA  | GRAD_GATE      |
  | 2   | INA−  | GRAD_SENSE     |
  | 3   | INA+  | GRAD_DAC_OUT   |
  | 4   | V-    | GND            |
  | 5   | INB+  | GND            |
  | 6   | INB−  | GRAD_NFB       |
  | 7   | OUTB  | GRAD_NFB       |
  | 8   | V+    | +5V            |

- **Datasheet:** <https://www.ti.com/lit/ds/symlink/lm358.pdf> (`pdfs/LM358.pdf`)

### Qgrad — IRLML2502  (Infineon, N-channel MOSFET, SOT-23-3)
- **Function:** low-side current switch.  Gate = GRAD_GATE (op-amp OUTA),
  source = GRAD_SENSE (→ Rsense → GND), drain = GRAD_COIL_N (coil return).
  R_DS(on) ≈ 45 mΩ at V_GS=4.5 V, Id up to 4.2 A — comfortably above the
  ~2.2 A clean-addressing current.
- **Verified pinout (SOT-23-3):** 1=Gate (GRAD_GATE), 2=Source (GRAD_SENSE),
  3=Drain (GRAD_COIL_N).
- **Datasheet:** <https://www.infineon.com/dgdl/irlml2502.pdf> (`pdfs/IRLML2502.pdf`)

### Lgrad — gradient coil (PCB copper trace, qssc:GradCoil footprint)
- **Function:** the field-gradient coil — a 1.5 mm F.Cu trace (2 oz Cu, sized
  for ~2 A) running along the die socket left edge.  Pin 1 = +5V feed,
  pin 2 = GRAD_COIL_N → MOSFET drain.  No wound part — the "inductor" is PCB
  copper (free); the footprint draws the trace on F.Fab (the actual copper is
  laid in the routing step).

### Rsense — 1 Ω current-sense (2512 power thick-film, ≥5 W)
- **Function:** source-degeneration sense resistor.  I_coil = Vdac/1 Ω.  At
  the 2.2 A clean-addressing current, P = I²R = 4.84 W, so the part must be
  rated ≥5 W.  Pin 1 = GRAD_SENSE, pin 2 = GND.
- **MPN:** a single 1 Ω / 5 W / 2512 chip is non-standard — 5 W 2512
  current-sense parts are mΩ-range, and 1 Ω 2512 thick-film parts (e.g. Yageo
  RC2512FK-071RL, TT Electronics LR2512-1R0FW) are 1–1.5 W.  NOT QUOTED BY THE
  SUPPLIER — procurement should use either a larger high-power package (e.g.
  6432/5025) at 1 Ω/5 W, or two 0.5 Ω 3 W 2512 parts in series.  MPN to be
  confirmed at procurement.

### C_grad1 / C_grad2 — 100 nF DAC VDD decoupling (0603)
- **Function:** HF + bulk-bypass on the +3V3 rail at Ugrad (VDD/VREF moved
  to +3V3 with #168-C).  Pin 1 = +3V3,
  pin 2 = GND.

### BOM cost (gradient add-on, single qty)
| ref        | part        | qty | ~unit  | ~ext   |
|------------|-------------|-----|--------|--------|
| Ugrad      | MCP4921     | 1   | $1.50  | $1.50  |
| Udrv       | LM358       | 1   | $0.20  | $0.20  |
| Qgrad      | IRLML2502   | 1   | $0.30  | $0.30  |
| Rsense     | 1Ω 5W 2512  | 1   | $0.10  | $0.10  |
| C_grad1/2  | 100 nF 0603 | 2   | $0.05  | $0.10  |
| Lgrad      | PCB copper  | 1   | $0.00  | $0.00  |
| **total**  |             |     |        | **~$2.50** |

---

## QMTECH XC7A15T bring-up board (firmware test target)

A **stock QMTECH XC7A15T core board** (bought, not designed here) is used to
bring up and exercise the QSSC firmware before the custom control board comes
back from fabrication.  Nothing external is cabled to it yet — it is a bare
board, so only its own oscillator, LEDs, buttons, SPI flash and JTAG are
live.

**Local PDFs (pin schematic + manual):**

| file | content |
|---|---|
| [`pdfs/QMTECH_XC7A15T_schematic.pdf`](pdfs/QMTECH_XC7A15T_schematic.pdf) | full 4-sheet schematic — **the authoritative ball/pin map**: FPGA banks, JP2/JP3 headers, JTAG J1, SPI flash, LEDs, keys |
| [`pdfs/QMTECH_XC7A15T_user_manual.pdf`](pdfs/QMTECH_XC7A15T_user_manual.pdf) | user manual — JP2/JP3 pin tables, power, Vivado/JTAG flow, boot mode |

Source: `https://github.com/ChinaQMTECH/QMTECH_XC7A15T_35T_CSG325_CORE_BOARD`
(`Hardware/QMTECH_XC7A15T_35T_50T_CSG325_SDRAM_V1.pdf` and
`QMTECH_XC7A15T_SDRAM-User_Manual_V01.pdf`), md5-verified against upstream.

**Board facts:**

- FPGA **XC7A15T-1CSG325C** — 16 640 logic cells, 10 400 LUTs, 20 800 FF,
  2 600 CARRY4, 50 BRAM, 40 DSP.  **Not** the XC7A35T-1CSG324C of the custom
  board: different die *and* different package, so the ball map differs.
- 50 MHz oscillator on **R2** (`IO_L12N_T1_MRCC_34`), not 100 MHz.
- Two 50-pin headers: **JP2 = bank 34**, **JP3 = bank 15**, 87 usable IOs,
  all banks at 3.3 V by default.
- On-board: 2 user LEDs (D1/D4, active-**low**), 2 user keys (SW1/SW2,
  active-**low**, 4.7 k pull-ups), PROG_B (SW3), DONE LED (D3), power LED (D2),
  64 Mbit SPI flash **N25Q64A** (quad-wired), JTAG J1.
- Boots Master SPI (`M[2:0] = 1:0:0`).

**Gotchas when porting from the custom board** — several balls used in
`../../fpga/constraints.xdc` are ground or absent on CSG325 (e.g. B8, A18, B18, G1,
A8, A1, B1, C1), so they must be re-assigned.  The QMTECH port lives in
[`fpga/qmtech/`](../../fpga/qmtech/) with its own `constraints_qmtech.xdc`.

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