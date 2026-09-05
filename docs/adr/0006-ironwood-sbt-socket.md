# 0006. Emitter-face bonding: Ironwood SBT socket on the main board (detector face stays ACF)

## Status

Accepted (upstream commits `5e10c7a`, `3f87c6f`, 2026-09) · Tags: pcb, packaging, assembly

## Context

ADR-0003 fixed *which* board each die face contacts; the open question was
*how*. The detector face is permanently bonded via ACF on the head board.
The emitter face originally followed the same pattern — a permanent ACF
bond on the main-board F.Cu — which would make every rework of the die
(the most expensive part in the stack) a destructive operation.

## Decision

Bond the die's **emitter (bottom) face through an Ironwood SBT socket**
into the main-board F.Cu, and keep the detector (top) face on **ACF** to
the head board's B.Cu. Per-datasheet pin fixes and `no_connect` markers
accompanied the socket introduction, and the netlist audit was driven
green in the same series.

## Consequences

- + The die is reworkable on the emitter side: socket-first assembly means
  a defective die can be swapped without scrapping the main board.
- + Asymmetric bonding matches the repair economics: the head-side ACF is
  permanent, but the head board is the cheaper half of the sandwich.
- − The socket adds stack height and one more mechanical interface
  (together with J12, the mechanical unknowns flagged in the QSSC-004
  audit, finding F10).
- − SBT socket pad geometry is a procurement dependency (Ironwood part).

## Evidence

- `5e10c7a` — "pcb: Ironwood SBT socket + per-datasheet pin fixes +
  no_connect markers" (8 files).
- `3f87c6f` — "pcb: netlist audit green + Ironwood SBT socket +
  per-datasheet pin fixes" (8 files, +287/−47).
- `f44ffac` — "test(pcb): per-datasheet pin-audit gate (Ironwood socket,
  FT232 24/23, ADF4351 TEMP, no_connect)" — the socket is encoded as a
  test gate in `tests/test_pcb_pin_audit.py`.
- `hardware/HARDWARE_DESIGN.md` §1B/§13; `hardware/datasheets/DATASHEETS.md`
  (emitter-face socket paragraph).

## Alternatives considered

- **ACF on both faces**: symmetric, but every die defect becomes a
  destructive rework; rejected.
- **Wirebond emitter face**: pad pitch (0.30 mm) and hand-assembly
  workflow; rejected — same reasoning as ADR-0003.