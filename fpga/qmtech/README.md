# QSSC firmware — QMTECH XC7A15T port

Bring-up port of the QSSC control firmware to a **stock QMTECH XC7A15T core
board**, used to exercise the firmware while the custom control board
(XC7A35T-1CSG324C) is unavailable.

The custom board's build is untouched: it stays in
[`../synth/`](../synth/) with [`../constraints.xdc`](../constraints.xdc).

## Why a separate port exists

| | Custom board | QMTECH |
|---|---|---|
| Part | XC7A35T-**1CSG324C** | XC7A15T-**1CSG325C** |
| Package | CSG324 | **CSG325 — different ball map** |
| Clock | 100 MHz TCXO on B8 | **50 MHz** oscillator on **R2** |
| Reset | active-high button on A1 | **active-low** SW1 on H18 |
| On-board I/O | none | 2 LEDs, 2 buttons (active-low) |
| UART | FT232RL onboard | none (JP2-18/19) |
| Flash | W25Q128 128 Mbit | N25Q64 64 Mbit |

Several balls used by `../constraints.xdc` are **ground** on CSG325
(`B8, A18, B18, G1, A8`) or are not brought out (`A1, B1, C1`), so every pin
had to be re-assigned. Details: [`PINMAP.md`](PINMAP.md) and
[`../INTERCONNECT.md`](../INTERCONNECT.md).

## Files

| File | Purpose |
|---|---|
| `top_qmtech.v` | Wrapper: MMCME2_BASE 50 -> 100 MHz, reset conditioning, LED heartbeat / button mirror, instantiates `top_level` |
| `constraints_qmtech.xdc` | All pin assignments for CSG325 (JP2/JP3 + on-board) |
| `build_bitstream_qmtech.tcl` | Vivado batch flow: elaborate, P&R, `write_bitstream` |
| `PINMAP.md` | Board pin map: JTAG, on-board signals, JP2/JP3 tables, traps |
| `yosys_check/check_xdc.py` | Validates the XDC against `top_qmtech.v` (balls, duplicates, IOSTANDARD) |
| `yosys_check/check_lite.ys` | Structural check: hierarchy resolves, no latches (~800 MB, ~7 s) |
| `yosys_check/check.ys` | Full resource count via `synth_xilinx`+`abc9` (~6 GB, minutes) |
| `yosys_check/run_checks.sh` | Runs the checks **under a memory cap with idle CPU/IO priority** |

## Building

Requires **Vivado** (not installed on the development box):

```bash
vivado -mode batch -source fpga/qmtech/build_bitstream_qmtech.tcl
# -> fpga/qmtech/build/qssc_qmtech.bit
```

Then flash over JTAG:

```bash
openFPGALoader -c ft232 fpga/qmtech/build/qssc_qmtech.bit
```

Without Vivado, the checks still run locally — **always through the wrapper**
so they stay inside a memory cap and never run in parallel (an earlier
unguarded run of `check.ys` used ~6 GB and two of them in parallel pushed the
box into swap):

```bash
python3 fpga/qmtech/yosys_check/check_xdc.py     # pin validation (instant)
bash fpga/qmtech/yosys_check/run_checks.sh       # lite structural check (~7 s)
FULL=1 MEM_MB=6144 bash fpga/qmtech/yosys_check/run_checks.sh   # + resource count
```

## Bring-up test (hardware currently uncabled)

Nothing is connected off-board yet (no ADF4351, TLC5947, AD7616, MCP4921 or
UART), so only the on-board resources can be exercised:

| Indicator | Expected | Proves |
|---|---|---|
| **D2** | always on | 3.3 V rail present |
| **D1** (`led0_n`) | blinking at **1 Hz** | 50 MHz in, MMCM locked, 100 MHz running |
| **D4** (`led1_n`) | lights while **SW2** held | input path + button wiring |
| **D3** (red) | on after programming | `DONE` asserted, bitstream accepted |
| **D1/D4** frozen while **SW1** held | — | reset path (`key0_n` inverted into `top_level`) |

Both user LEDs are **active-low**: `3V3 -> 1k -> LED -> pin`, so `0` = on.

## Known blockers (as of the current tree)

1. **No bitstream can be produced on the development box** — Vivado is not
   installed. yosys cannot generate a 7-series bitstream (no
   `write_bitstream`, and neither `MMCME2_BASE` nor `ODDR2` are recognised
   for `family xc7`).
2. **`rtl/top_level.v:443` instantiates `ODDR2`**, a Spartan-6 primitive that
   does not exist on Artix-7. This blocks *both* the QMTECH and the
   custom-board build. `build_bitstream_qmtech.tcl` patches it into a copy
   under `build/` without touching the original; the permanent fix is to use
   the 7-series `ODDR` (see the .tcl header).
3. **Resource fit is tight.** Measured on the current RTL (yosys, `family
   xc7`, `-abc9`):

   | Resource | Used | XC7A15T | XC7A35T |
   |---|---:|---:|---:|
   | LUT | 16 299 | **156.7 %** | 78.4 % |
   | CARRY4 | 8 737 | **336.0 %** | **168.0 %** |
   | FF | 5 661 | 27.2 % | 13.6 % |
   | DSP | 9 | 22.5 % | 10.0 % |

   `command_parser.v` alone accounts for **8 222 of 8 737 CARRY4** and
   **13 288 of 16 299 LUT**. The bulk comes from the combinational
   number-to-decimal formatters (`dec32_pad` x6 call sites, each 10 chained
   32-bit `/10`; `signed_dec48` x2, each 15 chained 48-bit `/10`).

   Note `fpga/synth/SYNTH_REPORT.md` is **stale** (26 Sep: CARRY4 = 4 856);
   `command_parser.v` and `adf4351_ctrl.v` were modified on 27 Sep.

## Current status

| Step | State |
|---|---|
| JTAG chain to the board | **works** — `openFPGALoader -c ft232 --detect` -> `idcode 0x362e093 / artix a7 15t / irlength 6` |
| Bitstream load path | **works** — a known `.bit` loads and `done 1` is reported |
| QSSC bitstream for QMTECH | **does not exist** — see blockers 1-3 |
| Firmware flashed | **not yet** |
