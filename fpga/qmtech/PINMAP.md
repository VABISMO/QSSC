# QMTECH XC7A15T — board pin map

Quick-reference for consultation. Everything is extracted from and verified
against the vendor PDFs, which are in this repository:

- [`hardware/datasheets/pdfs/QMTECH_XC7A15T_schematic.pdf`](../../hardware/datasheets/pdfs/QMTECH_XC7A15T_schematic.pdf)
  (4-sheet schematic — the authoritative ball/pin map)
- [`hardware/datasheets/pdfs/QMTECH_XC7A15T_user_manual.pdf`](../../hardware/datasheets/pdfs/QMTECH_XC7A15T_user_manual.pdf)
  (user manual — JP2/JP3 pin tables, power, JTAG/Vivado flow, boot mode)

If something is not here, look it up on the matching sheet of the schematic.

---

## 1. Board

| | |
|---|---|
| FPGA | **XC7A15T-1CSG325C** (package **CSG325**, 325 balls) |
| Resources | 16 640 logic cells · 10 400 LUT · 20 800 FF · 2 600 CARRY4 · 50 BRAM · 40 DSP |
| Clock | **50 MHz** on **R2** (SG-310SCN oscillator, OE pulled up -> always enabled) |
| Flash | N25Q64A 64 Mbit, wired for **quad** mode (D02->WP#, D03->HOLD#) |
| Boot | Master SPI, `M[2:0] = 1:0:0` |
| I/O banks | all at **3.3 V** by default (JP2 = bank 34, JP3 = bank 15) |
| JTAG | J1, 6-pin 2.54 mm header |

> **Watch the package.** The custom QSSC board is **XC7A35T-1CSG324C**
> (CSG324). The ball map is **not the same**: several balls used by
> `fpga/constraints.xdc` (B8, A18, B18, G1, A8, A1, B1, C1) are **ground**
> or are not user I/O on CSG325. See §5.

---

## 2. JTAG — header J1 (6 pins, 2.54 mm)

| J1 pin | Signal | -> FPGA ball |
|---|---|---|
| 1 | 3V3 (VREF) | — (output of the board itself) |
| 2 | GND | — |
| 3 | TCK | **F8** (`TCK_0`) |
| 4 | TDO | **T8** (`TDO_0`) |
| 5 | TDI | **T9** (`TDI_0`) |
| 6 | TMS | **R8** (`TMS_0`) |

- Pin 1 is the board's own **3.3 V output**, used only as a reference by the
  cable. **Do not connect it to GND** and do not back-power it.
- FT232H hookup (MPSSE): `ADBUS0->pin3 (TCK)`, `ADBUS1->pin5 (TDI)`,
  `ADBUS2<-pin4 (TDO)`, `ADBUS3->pin6 (TMS)`, `GND->pin2`, pin 1 unconnected.

---

## 3. On-board signals

| Function | Signal | Ball | Bank | Notes |
|---|---|---|---|---|
| 50 MHz clock | `CLK_50M` | **R2** | 34 | `IO_L12N_T1_MRCC_34` -> MRCC pin, dedicated route to the MMCM |
| User LED 0 | `USER_LED0` (D1) | **D8** | 15 | **ACTIVE-LOW** |
| User LED 1 | `USER_LED1` (D4) | **C8** | 15 | **ACTIVE-LOW** |
| Button 0 | `USER_KEY0` (SW1) | **H18** | 15 | external 4.7k pull-up -> **pressed = 0** |
| Button 1 | `USER_KEY1` (SW2) | **H17** | 15 | external 4.7k pull-up -> **pressed = 0** |
| PROGRAM_B | `PROG_B` (SW3) | **P10** | 0 | clears configuration when pressed |
| INIT_B | `INIT_B_0` | **T10** | 0 | 4.7k pull-up (R30) |
| DONE | `FPGA_DONE` (red LED D3) | **F12** | — | on = configuration loaded OK |
| CCLK | `FPGA_CCLK` | **E8** | — | configuration clock (dedicated, not assigned in the XDC) |
| Power | LED D2 | — | — | on = 3.3 V present |

LED circuit: `3V3 -> 1 kΩ -> LED -> FPGA pin`, so they light with the pin at
**0 V**. On = `0`.

Button circuit: `3V3 -> 4.7 kΩ -> node -> button -> GND`, node tied to the
FPGA pin. Idle = **1**, pressed = **0**.

---

## 4. Expansion headers

### JP2 — 50 pins, bank 34

| Pin | Ball | Pin | Ball | Pin | Ball | Pin | Ball |
|---|---|---|---|---|---|---|---|
| 1 | USB_5V | 2 | 3V3 | 3 | V8 | 4 | V7 |
| 5 | U7 | 6 | V6 | 7 | T7 | 8 | R7 |
| 9 | R6 | 10 | T5 | 11 | R5 | 12 | T4 |
| 13 | U6 | 14 | U5 | 15 | V4 | 16 | U4 |
| 17 | V3 | 18 | V2 | 19 | U2 | 20 | U1 |
| 21 | P4 | 22 | T3 | 23 | R3 | 24 | T2 |
| 25 | N4 | 26 | P3 | 27 | M6 | 28 | M5 |
| 29 | L5 | 30 | L4 | 31 | K3 | 32 | K5 |
| 33 | P1 | 34 | R1 | 35 | N1 | 36 | N2 |
| 37 | M2 | 38 | M1 | 39 | J5 | 40 | J4 |
| 41 | J6 | 42 | K6 | 43 | L2 | 44 | L3 |
| 45 | K2 | 46 | K1 | 47 | — | 48 | — |
| 49 | — | 50 | — | | | | |

### JP3 — 50 pins, bank 15

| Pin | Ball | Pin | Ball | Pin | Ball | Pin | Ball |
|---|---|---|---|---|---|---|---|
| 1 | USB_5V | 2 | 3V3 | 3 | F18 | 4 | G17 |
| 5 | E18 | 6 | F17 | 7 | D18 | 8 | E17 |
| 9 | C17 | 10 | C18 | 11 | G15 | 12 | H16 |
| 13 | F15 | 14 | G16 | 15 | E16 | 16 | E15 |
| 17 | D16 | 18 | D15 | 19 | A17 | 20 | B17 |
| 21 | C16 | 22 | B16 | 23 | A15 | 24 | B15 |
| 25 | A14 | 26 | B14 | 27 | C14 | 28 | D14 |
| 29 | C13 | 30 | D13 | 31 | A13 | 32 | C12 |
| 33 | F14 | 34 | E13 | 35 | B12 | 36 | A12 |
| 37 | C11 | 38 | B11 | 39 | D11 | 40 | D10 |
| 41 | C9 | 42 | D9 | 43 | A10 | 44 | B10 |
| 45 | A9 | 46 | B9 | 47 | — | 48 | — |
| 49 | — | 50 | — | | | | |

**87 usable I/Os** in total (44 on JP2 + 43 on JP3, excluding USB_5V/3V3).

---

## 5. Pins you cannot use (traps when porting the custom XDC)

Balls that `fpga/constraints.xdc` (written for CSG324) assigns as I/O but
which are ground or are not user I/O on CSG325, plus pins that are simply
**not brought out on JP2/JP3**:

| Custom-board XDC | Used as | Available on QMTECH? |
|---|---|---|
| B8 | `clk_100mhz` | **No** — ground ball on CSG325 (the clock is on **R2**) |
| A18 | `adc_convst` | **No** — ground ball |
| B18 | `adc_sclk` | **No** — ground ball |
| G1 | `spi_pins[0]` | **No** — ground ball |
| A8 | `refclk_out` | **No** — ground ball |
| A1 | `reset` | **No** — not on JP2/JP3 |
| B1 | `tx` | **No** — not on JP2/JP3 |
| C1 | `rx` | **No** — not on JP2/JP3 |
| D18, E18, F18, K1, V1–V12 | various | **Yes** (JP3 / JP2) |

Full re-assignment used in `constraints_qmtech.xdc`: see that file, and
[`../INTERCONNECT.md`](../INTERCONNECT.md) for the side-by-side mapping.

---

## 6. Cabling status

**Nothing is connected off-board yet** — no ADF4351, no TLC5947, no AD7616,
no MCP4921, no external UART. Only the oscillator, the LEDs, the buttons,
the flash and JTAG are live. A bring-up test can therefore only verify:

1. clock -> LED0 beating at 1 Hz,
2. button SW2 -> LED1,
3. JTAG bitstream load -> D3 (DONE) lit.
