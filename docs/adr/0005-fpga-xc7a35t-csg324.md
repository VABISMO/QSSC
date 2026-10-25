# 0005. Target FPGA: XC7A35T-1CSG324C (bump from CPG236)

## Status

Accepted (upstream commit `9d2e3e8`, 2026-09) · Tags: fpga, packaging

## Context

Phase 1 targeted the Artix-7 in the **CPG236** package (Digilent Cmod
A7-35T). The parallel architecture of this project needs a large GPIO
budget — at the time of the bump, 128 parallel nets into the chip grid
alone, on top of UART, the ADF4351 SPI + 25 MHz refclk, and the
TLC5947/ADC buses. CPG236's 236-ball package could not carry the pin count.

## Decision

Bump the target to **XC7A35T-1CSG324C** and rewire the design for the
324-ball package (`fpga/constraints.xdc` written for CSG324). The custom
QSSC PCB takes the CSG324 part directly; the Digilent Arty A7-35T carries
the same package, so the same bitstream and pin map drop onto it
unchanged. Cmod A7 (CPG236) remains documented as a remap-required fallback.

## Consequences

- + Pin budget for the parallel nets and all peripheral buses.
- + Arty A7-35T becomes a drop-in development carrier (on-board USB-JTAG,
  same package).
- − CPG236 carriers need a pin remap and lose headroom.
- The RTL itself stays vendor-neutral (see ADR-0002): only
  `constraints.xdc` is package-specific.

## Evidence

- `9d2e3e8` — "phase 2: bump FPGA CPG236 -> CSG324, wire 128 parallel nets"
  (7 files, +17106/−11588).
- `fpga/constraints.xdc:3` (CSG324 part reference).
- `hardware/HARDWARE_DESIGN.md` §1A (supported boards: custom CSG324 PCB,
  Arty A7-35T, Cmod A7-35T with remap caveat).
- Synthesis gate confirms fit: `fpga/synth/SYNTH_REPORT.md`
  ("Fits XC7A35T … yes", via `fpga/synth/run_synth.sh` in CI).

## Alternatives considered

- **Stay on CPG236 / Cmod A7**: pin-budget failure at 128 parallel nets;
  rejected.
- **Larger 7-series part (A50T/A100T)**: more headroom than the current
  design uses (≈10 % LUTs on the A35T); rejected for cost/availability —
  the `.xdc` stays the only file to change if that ever flips.