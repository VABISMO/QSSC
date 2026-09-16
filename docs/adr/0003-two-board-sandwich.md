# 0003. Two-board sandwich: 6-layer HDI main board + 4-layer detector head around the die

## Status

Accepted (upstream commits `f2d62e4`, `72ce3be`, 2026-09) · Tags: pcb, packaging, readout

## Context

The QSSC die has **two active faces**: the bottom (emitter) face carries
the 64 CQD emitter pixels, the top (detector) face the 64 graphene/MoS₂
detector electrodes. A single rigid PCB can contact only one face of the
same die. The project passed through a single-board era (`e561936`, merged
board DRC 0/0/0) — before the AD7616 pivot (ADR-0002) placed a full
64-channel TIA + ADC front-end on the detector side.

## Decision

Sandwich the die between **two boards**:

- **Main board** (6-layer HDI, 210×140 mm, below the die): Artix-7 CSG324,
  ADF4351 + GVA-84+ RF chain, 3× TLC5947 emitter driver, power, USB. The
  die bottom (emitter face) contacts the main-board F.Cu — through the
  Ironwood SBT socket (ADR-0006).
- **Detector head** (4-layer, 150×140 mm, above the die): 16× TLV9004 TIA +
  4× AD7616. The die top (detector face) contacts the head's B.Cu via ACF.
- The two boards mate at the edge through **J12**, a 2×10 board-to-board
  connector carrying the 9-wire AD7616 SPI + power; it also sets the
  stacking height.

## Consequences

- + Both die faces are electrically reachable without wirebonds.
- + The sensitive analog front-end sits on its own board, separated from
  the RF and FPGA domain by the BTB edge only.
- − Two DRC/ERC domains and a mechanical interface: J12 is a single
  mechanical point (open question in the QSSC-004 audit, finding F10).
- − Assembly requires the boards to mate in the right stack order.

## Evidence

- `f2d62e4` — "pcb: two-board sandwich routed + DRC/ERC 0/0, HDI via rules"
  (9 files, +128322/−123107).
- `72ce3be` — "docs(pcb): two-board AD7616 sandwich — rewrite
  HARDWARE_DESIGN, BOM, tests" (5 files).
- `hardware/HARDWARE_DESIGN.md` §1B ("Physical assembly (montaje) — the die
  is sandwiched between two PCBs") and §12 (layer stackup, two boards).
- Superseded single-board state: `e561936`.

## Alternatives considered

- **Single merged board** (`e561936`): tried upstream, superseded — leaves
  the second die face unreachable once the front-end is board-side.
- **Wirebonds to both faces**: incompatible with the 0.30 mm pad pitch of
  the 128-pad die grid and hand assembly; rejected.