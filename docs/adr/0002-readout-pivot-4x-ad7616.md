# 0002. Readout pivot: 4× AD7616 parallel 16-bit ADCs replace photon counters / XADC thresholding

## Status

Accepted (upstream commit `e25e04b`, 2026-09) · Tags: readout, rtl, fpga, pcb

## Context

The earlier readout architecture digitised the 64 on-chip detector
electrodes with on-die **photon counters** (RTL refactor `780fae5`, "64
photon counters") and an FPGA-internal thresholding path built on the
Artix-7 **XADC** hard block plus an analog MUX. That tied the readout to
one vendor's hard ADC, made the RTL unportable, and put digitisation
behind a multiplexer instead of per-channel conversion.

## Decision

Pivot the readout to **4× AD7616** (16-channel, 16-bit SAR) mounted on the
detector head, fed by a **9-wire SPI bus** (`adc_convst/sclk/miso/busy/
resetn/cs0..3`) that crosses the J12 board-to-board connector. RTL side:
`fpga/rtl/adc_reader.v` implements the sequencer master (SEQEN=HIGH → 8
CONVST pulses auto-scan all 16 channels per chip; each CONVST returns one
VA/VB pair = 2 channels). Qiskit-side software was updated in the same
commit. The `xadc_behavioral` stub and all XADC wiring were deleted from
`top_level.v`.

## Consequences

- + Vendor-neutral RTL: no Xilinx-hard-ADC dependency — the same firmware
  ports to any 7-series part (and to Lattice/Intel/Gowin with a new `.xdc`).
- + 16-bit per-channel conversion replaces thresholding; the 1 kHz
  `mod_sync` lock-in reference is still generated for host-side demodulation.
- − 9 GPIO wires + a board-to-board connector are now on the critical
  readout path (J12, see ADR-0003).
- − Older docs still describe the XADC/MUX/12-bit path — tracked upstream
  as issue #19 (README drift).

## Evidence

- `e25e04b` — "readout: pivot photon counters -> 4x AD7616 parallel 16-bit
  ADC (RTL + Qiskit sw)" (17 files, +1296/−825).
- `hardware/HARDWARE_DESIGN.md` §8 ("64 parallel TIA → 4× AD7616 ADC →
  9-wire SPI") and §1A ("no Xilinx-hard-ADC dependency … the
  `xadc_behavioral` stub and all XADC wiring were deleted").
- `fpga/rtl/adc_reader.v:17-23` (module header, sequencer semantics).
- Superseded path: `780fae5` ("64 photon counters"), pre-pivot README
  XADC/MUX text (now issue #19).

## Alternatives considered

- **XADC + analog MUX + 1 kHz lock-in** (pre-pivot): vendor-locked, 12-bit,
  MUX-shared digitisation; rejected in `e25e04b`.
- **On-die photon counters** (`780fae5`): requires counter structures on the
  die itself, coupling the quantum stack to a silicon process step; superseded.