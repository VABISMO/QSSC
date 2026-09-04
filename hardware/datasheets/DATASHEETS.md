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

### U1 — XC7A35T-1CSG324C  (AMD/Xilinx Artix-7 FPGA)
- **Function:** digital core — UART command parser, 64 parallel photon counters
  (one per detector, `det_in[63:0]` LVCMOS), 4-wire TLC5947 emitter driver
  (`em_sclk/sin/xlat/blank`), ADF4351 PLL SPI master, W25Q128 config-flash SPI,
  I²C MCP4725 threshold DAC, 25 MHz refclk output to the PLL, 1 kHz `mod_sync`
  lock-in reference, `mw_gate` RF-switch envelope.  **No analog MUX, no XADC**
  — the single-board parallel pivot removed both; readout is all-digital.
- **Package:** BGA-324, CSG324, 0.8 mm pitch (15x15, 324 balls).
- **Datasheet:** https://docs.amd.com/r/en-US/ds181-Artix-7-Data-Sheet
  (package: UG475 — CSG324 ball map).
- **Local PDF:** not fetched (AMD blocks headless download; 30 MB+).
- **Pin map:** 324 balls, built from `.tmpwork/fpga_csg324_map.json` (power
  GND/+3V3/+1V0/+1V8, config, JTAG, 81 user IO = 64 `DET_DIG` detector inputs
  + `em_sclk/sin/xlat/blank` + UART rx/tx + ADF4351 SPI/LE/MUXOUT + refclk_25m
  + mw_gate + mod_sync + I²C sda/scl).  Verified by `tools/check_xdc_ports.py`
  against `fpga/constraints.xdc`.

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
| 19  | TEMP    | NC                 | temperature monitor, unused |
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
| 4   | VCCIO| power     | +3V3        | |
| 5   | RXD  | input     | UART_TX     | <- FPGA tx (P18). |
| 6,9,10,11 | RI/DCR/DCD/CTS | input | NC | unused modem control |
| 7,18,21,25,26 | GND | power | GND | |
| 15  | USBD+ | bidir    | USB_DP      | -> USB-C |
| 16  | USBD- | bidir    | USB_DM      | -> USB-C |
| 17  | 3V3OUT| output   | NC (3V3OUT) | internal regulator out, unused |
| 19,20 | VCC | power    | +3V3        | |
| 2,3,12,13,14,22,23,27,28 | DTR/RTS/CBUS/OSC | — | NC | unused |
- **Connection note:** the FPGA ballmap ports are `tx`/`rx`; `fpga_net_map()`
  aliases them to `UART_TX`/`UART_RX` so the FT232 and FPGA share one net
  (the prior name mismatch silently disconnected the UART).

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

### UT0..UT15 — TLV9004  (Texas Instruments quad CMOS op-amp) x16
- **Function:** 64-channel transimpedance + signal-conditioning front-end for
  the graphene/MoS₂ photodetectors.  One TLV9004 = 4 channels; 16 ICs = 64.
  Each detector `DET_IN{i}` -> TIA (Rf 1 MΩ ‖ Cf 1 pF, virtual-ground ref
  DET_REF = 1.65 V) -> `DET_CMP_IN{i}` to the comparator.  Photovoltaic /
  zero-bias mode (the detectors output current).
- **Package:** SOIC-14, 3.9x8.7 mm, 1.27 mm pitch.
- **Datasheet:** https://www.ti.com/lit/ds/symlink/tlv9004.pdf
- **Local PDF:** `pdfs/TLV9004.pdf`
- **Verified pinout:** 4=V+ (+3V3), 11=V- (GND); per-channel: 1=OUT_A, 2=-IN_A,
  3=+IN_A, 5=+IN_B, 6=-IN_B, 7=OUT_B, 8=OUT_C, 9=-IN_C, 10=+IN_C, 12=+IN_D,
  13=-IN_D, 14=OUT_D.

### UC0..UC15 — LMV339  (Texas Instruments quad comparator) x16
- **Function:** 64-channel Schmitt-style photon-count comparator.  Each TIA
  output `DET_CMP_IN{i}` is compared against the shared threshold `DET_THR`
  (from UDAC) -> open-collector output pulled up to +3V3 -> `DET_DIG{i}` FPGA
  LVCMOS33 photon-counter input.  16 ICs = 64 channels (one per detector).
- **Package:** TSSOP-14, 4.4x5 mm, 0.65 mm pitch.
- **Datasheet:** https://www.ti.com/lit/ds/symlink/lmv339.pdf
- **Local PDF:** `pdfs/LMV339.pdf`
- **Verified pinout:** 14=VCC (+3V3), 7=GND, 3=/OE (tie high); per-channel:
  1=OUT_A, 2=-IN_A (DET_THR), 4=+IN_A (DET_CMP_IN{i}), 5=+IN_B, 6=-IN_B,
  8=OUT_C, 9=-IN_C, 10=+IN_C, 11=+IN_D, 12=-IN_D, 13=OUT_D.  Rpu0-63 10k
  pull each output to +3V3.

### UDAC — MCP4725  (Microchip 12-bit I²C DAC)
- **Function:** generates the shared `DET_THR` comparator threshold voltage
  (one DAC sets all 64 comparators' trip point).  FPGA I²C writes the code;
  Rref1/Rref2 + Cref buffer/scale the output; Cdac decouples.
- **Package:** SOT-23-6.
- **Datasheet:** https://ww1.microchip.com/downloads/en/devicedoc/22060d.pdf
- **Local PDF:** not fetched.
- **Verified pinout:** 1=VOUT (DET_THR), 2=VSS (GND), 3=VDD (+3V3), 4=SDA
  (i2c_sda), 5=SCL (i2c_scl), 6=A0 (GND for address 0x60).  R_I2C_SDA/SCL
  4.7k pull-ups to +3V3.

### Passives / misc (main board)
- C1-C20 100nF, C21-C30 10uF, C40-C48 — rail decoupling.
- C31 220p, C32 10n, C33 220p, R67 1k, R68 2.2k — ADF4351 loop filter.
- C34/C35 100p — RF AC-coupling.  L2 100nH — RF choke.  R73 4.7k — RSET.  C36 100n — VREF decoupling.
- R70 330 + LED1 green — power indicator.  R71/R72 5.1k — USB-C CC pulldowns.
- R75/R76/R77 4.7k — config pull-ups.  D1-D10 ESD5V0 — USB/IO ESD protection.
- F1 2A PTC — +5V barrel input polyfuse.  F2 1A PTC — +12V_EMIT input polyfuse.
  J2 5V barrel jack, J4 12V LED barrel jack, J3 SMA edge-mount, J7 reset
  button, J8 mw_gate header, J9 JTAG header, J10 USB-C.
- UE0-2 TLC5947 — Riref0-2 4.87k (I_SET), Cem0-2 100nF (VCC decoupling).
  UT0-15 TLV9004 — Cut0-15 100nF (VCC decoupling).  UC0-15 LMV339 — Cuc0-15
  100nF (VCC decoupling).  Rf0-63 1M + Cf0-63 1p — TIA feedback.  Rpu0-63 10k
  — comparator pull-ups.  Rref1/Rref2 5.1k + Cref/Cdac — MCP4725 threshold DAC.

---

## Quantum die (on-board — no second board, no Samtec)

### Uchip — QSSC_Chip  (custom CQD photodetector + emitter die)
- **Function:** the project's own fabricated quantum die — 64 top-edge
  detector electrodes + 64 bottom-edge emitter electrodes, driven and read
  in PARALLEL (no ADG732 MUX, no serial sweep, no XADC).  No commercial
  datasheet.  See `docs/FABRICATION_PROTOCOL.md` for the CQD stack
  (ITO shared anode / Ag per-pixel cathode) and EL specs (turn-on 4-8 V,
  drive 2-20 mA/pixel, ~520-540 nm green).
- **Die:** 20 x 20 mm, 128 pads @ 0.30 mm pitch (0.15 x 0.30 mm F.Cu SMD):
  64 DET pads on the top edge + 64 EM pads on the bottom edge + 1 EM_ANODE
  pad on the LEFT edge (the shared common anode, clear of both dog-bone
  via fields).
- **Topology (common-anode, validated against the fab protocol):** the 64 EM
  pads are per-pixel CATHODES (net `emitter_cat{i}`), each sunk by one
  TLC5947 OUTn constant-current channel (3-chip daisy chain, 72 ch, 64 used).
  The single EM_ANODE pad is the shared common anode on the **+12V_EMIT**
  rail — a separate 12 V LED supply (J4 barrel + F2 1A PTC + C85/C86), because
  the CQD EL turn-on is 4-8 V and the old +5V anode left no headroom; the
  TLC5947 OUT pins tolerate 33 V, so 12 V is textbook headroom.
- **Detector path:** the 64 DET pads (`DET_IN{i}`) -> 16x TLV9004 TIA
  (per-channel Rf/Cf) -> 16x LMV339 Schmitt comparator (shared MCP4725
  threshold `DET_THR`) -> 64 FPGA LVCMOS inputs -> 64 parallel photon
  counters in the fabric.
- **Netclass:** "Chip" 0.10 mm clearance (the 0.30 mm-pitch dog-bone fanout
  requires it).  Fanout: 0.15 mm F.Cu stub -> 0.20 mm through-via per pad,
  staggered even/odd (0.5 / 1.0 mm out), escaped on B.Cu / inner signal
  layers.  The dense 0.30 mm field is beyond the 0.2 mm line-router grid, so
  the via<->target escapes are routed by FreeRouting (Task 17); the dog-bone
  fanout itself (pad -> stub -> via) is DRC-clean and in place.

---

## KiCad footprints & 3D models

All 2D footprints used by the single merged board are present in the installed KiCad 8
libraries at `/usr/share/kicad/footprints/` (verified — no custom .pretty
needed except the FPGA BGA `Xilinx_CSG324` and the `QSSC_Chip` die, both
generated into the project lib).  **No 3D `.step`/`.wrl` models are
installed**; they are optional (visualisation only, not needed for DRC or
fabrication).  If wanted, fetch from
https://gitlab.com/kicad/libraries/kicad-3d-models (-3d-models) and point
`KICAD6_3DMODEL_DIR`/the 3D path at them.  See `HARDWARE_DESIGN.md` §15.