# 0004. Emitter drive: 3× TLC5947 constant-current sinks on a +12 V_EMIT rail

## Status

Accepted (upstream commits `9e1e92b`, `0bb52d9`, 2026-09) · Tags: pcb, emitter, power

## Context

The first board pass drove the 64 CQD emitter pixels with a discrete
**64-channel P-MOS high-side driver** (`446a30a`, "64-ch P-MOS emitter
driver"). A P-MOS switch only gates current — it does not regulate it —
so emitter brightness depends on pixel yield spread, and the driver chain
had to live at the anode rail.

## Decision

Drive the array with **3× TLC5947** (24-channel, 12-bit PWM,
constant-current **sink**) in a daisy chain — 72 channels for 64 pixels —
on the emitter **cathode** side, with the common anode raised to a
dedicated **+12 V_EMIT** rail (J4 barrel + F2 PTC 1A + C85/C86). FPGA side:
`fpga/rtl/tlc5947_ctrl.v` shifts the 4-wire daisy
(`em_sclk/sin/xlat/blank`).

## Consequences

- + Per-channel 12-bit PWM current regulation instead of raw switching;
  one R_IREF sets the global scale.
- + TLC5947 headroom needs the raised anode: lifting to +12 V_EMIT was
  required to get DRC from 43 violations to 2 (`9e1e92b`).
- − A separate 12 V input (J4) joins the 5 V input (J2/J10) — two barrels
  on one board, documented in the power-sequencing section.
- − Daisy-chain shift register adds serial latency per frame (accepted:
  emitter patterns change far slower than the readout).

## Evidence

- `9e1e92b` — "pcb: raise emitter anode to +12V_EMIT, fix DRC 43->2
  (TLC5947 headroom)" (20 files).
- `0bb52d9` — "docs(hardware): rewrite emitter driver to 3x TLC5947 +
  +12V_EMIT" (HARDWARE_DESIGN §7).
- `hardware/HARDWARE_DESIGN.md` §7 ("On-chip CQD Emitter Array Driving
  (64 parallel)"), block diagram lines "3x TLC5947 24-ch 12-bit PWM sink
  (daisy, 72ch/64used)" and "+12V_EMIT".
- `ea727e4` — added `tlc5947_ctrl.v` to the yosys synthesis gate.
- Superseded driver: `446a30a` (64-ch P-MOS emitter driver, Phase 1b).

## Alternatives considered

- **64-ch P-MOS high-side switch** (`446a30a`): no current regulation, no
  PWM; superseded by the constant-current sink design.
- **On-die emitter drive**: couples the optical stack to a driver process;
  rejected (the die carries emitters + detectors only, see ADR-0003).