# QSSC — Component datasheets & verified pinouts

Local reference folder for every active component on the two QSSC boards
(main control board + swappable chip module).  Each entry has:

- the **verified pinout** (extracted from the installed KiCad 8 symbol library
  at `/usr/share/kicad/symbols/` and cross-checked against the datasheet —
  these are the pin numbers that map 1:1 to the footprint pad numbers used in
  `tools/gen_qssc_kicad.py` / `tools/gen_chip_module.py`), and
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

### U1 — XC7A35T-1CPG236C  (AMD/Xilinx Artix-7 FPGA)
- **Function:** digital core — SPI masters (ADF4351 + W25Q128 config flash),
  64:1 MUX select generator, emitter row/col one-hot driver, 25 MHz refclk
  output to the PLL, XADC photodetector read (VauxP0 = TIA_OUT).
- **Package:** BGA-236, CPG236, 0.5 mm pitch (17x17, 238 balls).
- **Datasheet:** https://docs.amd.com/r/en-US/ds181-Artix-7-Data-Sheet
  (package: UG475 v1.16 — CPG236 ball map).
- **Local PDF:** not fetched (AMD blocks headless download; 30 MB+).
- **Pin map:** 238 balls, built from `.tmpwork/fpga_cpg236_map.json` (power
  GND/+3V3/+1V0/+1V8, config, JTAG, XADC VP=TIA_OUT/VN=GND, 32 user IO).  70
  balls are spare IO left NC (correct for unused BGA IO — pad soldered, no
  net).  Verified on the PCB: A14=refclk_25m, P17=UART_RX, P18=UART_TX,
  A12=TIA_OUT, B12=VREFP.

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

### Passives / misc (main board)
- C1-C20 100nF, C21-C30 10uF, C40-C48 — rail decoupling.
- C31 220p, C32 10n, C33 220p, R67 1k, R68 2.2k — ADF4351 loop filter.
- C34/C35 100p — RF AC-coupling.  L2 100nH — RF choke.  R73 4.7k — RSET.  C36 100n — VREF decoupling.
- R70 330 + LED1 green — power indicator.  R71/R72 5.1k — USB-C CC pulldowns.
- R75/R76/R77 4.7k — config pull-ups.  D1-D10 ESD5V0 — USB/IO ESD protection.
- F1 2A PTC — barrel input polyfuse.  J2 barrel jack, J3 SMA edge-mount,
  J7 reset button, J8 mw_gate header, J9 JTAG header.
- J11 — Samtec LSHM-130 2x30 0.5mm mezzanine to the chip module.

---

## Chip module  (swappable, mates via J1 = main-board J11)

### U1 / U2 — ADG732BCPZ  (Analog Devices 32:1 analog multiplexer) x2
- **Function:** 64:1 cascade selecting one of 64 detector electrodes into the
  TIA summing node.  U2 enabled by MUX_EN2 to extend U1's 32 channels to 64.
- **Package:** QFN-48-1EP, 7x7 mm, 0.5 mm pitch (pad 49 = EP = GND).
- **Datasheet:** https://www.analog.com/media/en/technical-documentation/data-sheets/ADG732.pdf
- **Local PDF:** not fetched (ADI blocks headless).
- **Verified pinout (key pads, pad# from PCB probe):**
  - 1-12, 25-40 — DET_IN0..DET_IN31 (U1) / DET_IN32..63 (U2): the 32 channel inputs.
  - 13, 14 — +3V3 (VDD).  20, 21, 23, 24 — GND.  49 — GND (EP).
  - 15-19 — det_mux_sel0..4.  22 — det_mux_sel5 (U1) / MUX_EN2 (U2).
  - 43 — TIA_IN (the mux drain output -> ADA4530 -IN).
  - 41, 42, 44 — NC (no internal connection; verified unnetted is correct).
  - "" (unnumbered sub-pad) — NC.

### U3 — ADA4530-1  (Analog Devices electrometer op-amp / TIA)
- **Function:** transimpedance amplifier for the selected photodetector
  electrode.  Rf = 1M, Cf = 1.5p.  Output -> XADC VauxP0.
- **Package:** SOIC-8 3.9x4.9 mm.
- **Datasheet:** https://www.analog.com/media/en/technical-documentation/data-sheets/ADA4530-1.pdf
- **Local PDF:** not fetched (ADI blocks headless).
- **Verified pinout (SOIC-8):**

| pad | name      | net on QSSC | note |
|----:|-----------|-------------|------|
| 1   | +IN       | TIA_REF     | non-inverting = 2.5V mid-supply reference |
| 2   | IN_GUARD  | TIA_REF     | input guard (driven at virtual-ground ref) |
| 3   | IC        | NC          | internal connection — leave open |
| 4   | V-        | GND         | |
| 5   | V+        | +5V         | **min supply 5V — was +3V3 (out of spec), fixed 2026-09** |
| 6   | OUT       | TIA_OUT     | -> XADC |
| 7   | IN_GUARD  | TIA_REF     | input guard |
| 8   | -IN       | TIA_IN      | inverting = summing node <- mux drain (pad 43) |
- **TIA_REF reference generator:** R10 10k (+5V -> TIA_REF) + R11 10k
  (TIA_REF -> GND) = 2.5V mid-supply + C11 100nF bypass.  The prior net was
  floating on both boards (J1 pin 49 crossed it but nothing drove it); now
  generated locally on the module (electrometer reference must stay local).

### U4 — 74LVC1G04  (single inverter, Nexperia SOT-23-5)
- **Function:** logic inversion in the MUX select path.
- **Package:** SOT-23-5.  **BOM must specify the Nexperia pinout variant.**
- **Datasheet:** https://www.ti.com/lit/ds/symlink/sn74lvc1g04.pdf
- **Local PDF:** `pdfs/74LVC1G04.pdf`
- **Verified pinout:** 2 = A (input), 3 = GND, 4 = Y (output), 5 = VCC.

### U6 / U7 — 74HCT240  (octal inverting 3-state buffer) x2
- **Function:** level-shift + invert the FPGA's 3.3V one-hot emitter enables
  to 0/5V MOSFET gate drives.  U6 drives P-MOS row gates (low=on), U7 drives
  N-MOS col gates (high=on).  /OE tied low (always enabled).
- **Package:** TSSOP-20.
- **Datasheet:** https://www.ti.com/lit/ds/symlink/sn74hct240.pdf
- **Local PDF:** `pdfs/74HCT240.pdf`
- **Verified pinout (Nexperia 74HC/HCT240):** 1=1OE, 2=1A0, 4=1A1, 6=1A2,
  8=1A3, 11=2A3, 13=2A2, 15=2A1, 17=2A0, 19=2OE (inputs);
  3=2Y0, 5=2Y1, 7=2Y2, 9=2Y3, 12=1Y3, 14=1Y2, 16=1Y1, 18=1Y0 (outputs);
  20=VCC (+5V), 10=GND.

### Q1-Q8 — AO3401A  (P-channel MOSFET, SOT-23-3) x8
- **Function:** high-side row switches — anode enable to +5V (Vgs=-5V = on).
- **Datasheet:** https://aosmd.com/res/data_sheets/AO3401A.pdf
- **Local PDF:** not fetched (AOS blocks headless).
- **Verified pinout:** 1 = G (gate), 2 = S (source), 3 = D (drain).

### Q9-Q16 — AO3400A  (N-channel MOSFET, SOT-23-3) x8
- **Function:** low-side column sinks — cathode pull-down (Vgs=+5V = on).
- **Datasheet:** https://aosmd.com/res/data_sheets/AO3400A.pdf
- **Local PDF:** not fetched.
- **Verified pinout:** 1 = G (gate), 2 = S (source), 3 = D (drain).

### U5 — QSSC_Chip  (custom photodetector die)
- **Function:** the 64-electrode solid-state chip itself (no commercial
  datasheet — it is the project's own fabricated die).  80-pad custom
  footprint; electrode pads route to the ADG732 channels via the module.

### Passives / misc (chip module)
- R1-R8 220R — MOSFET gate resistors.  R10/R11 10k — TIA_REF divider.
  Rf 1M, Cf 1.5p — TIA feedback.  R_THM 10k + TH1 10k NTC — thermistor divider.
- C1-C5, C7-C11 — decoupling.  J1 — Samtec LSHM-130 mezzanine (mates main J11).

---

## KiCad footprints & 3D models

All 2D footprints used by both boards are present in the installed KiCad 8
libraries at `/usr/share/kicad/footprints/` (verified — no custom .pretty
needed except the FPGA BGA `Xilinx_CPG236` and the `QSSC_Chip` die, both
generated into the project lib).  **No 3D `.step`/`.wrl` models are
installed**; they are optional (visualisation only, not needed for DRC or
fabrication).  If wanted, fetch from
https://gitlab.com/kicad/libraries/kicad-3d-models (-3d-models) and point
`KICAD6_3DMODEL_DIR`/the 3D path at them.  See `HARDWARE_DESIGN.md` §15.