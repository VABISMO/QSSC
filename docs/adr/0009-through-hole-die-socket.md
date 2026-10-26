# 0009. Emitter-face contact: plain through-hole socket (supersedes ADR-0006)

## Status

Accepted (2026-10-26) · Supersedes [ADR-0006](0006-ironwood-sbt-socket.md) · Tags: pcb, packaging, assembly

## Context

ADR-0006 chose an Ironwood SBT spring-pin socket for the emitter face: a
made-to-order part quoted against the die drawing and soldered down with paste.
That part never reached the committed design — the board generator, the
footprint and the sourcing row were all moved to a catalogue part, so the
paste-down variant and its quote-only MPN no longer describe anything the
board can be built from.

The committed main board carries `qssc:ChipSocket_2.54_EM` instead: a 2.54 mm
female-header footprint with 69 pads (65 through-hole signal pads + 4 NPTH
mount holes) and **no F.Paste** on any of them.

## Decision

The die module's **emitter (bottom) face** plugs into the main board's
**`ChipSocket_2.54_EM`** and its **detector (top) face** into the head board's
matching **`ChipSocket_2.54_DET`** — both plain through-hole 2.54 mm sockets
(65 PTH pads + 4 NPTH, no paste), both faces removable together. The Ironwood
SBT part, its paste-down footprint and its BOM line are out of the design, and
so is the ACF bond previously planned for the detector face.

## Consequences

- + No procurement dependency: the socket is a catalogue part, and the
  footprint is exactly what the pin-audit gate pins (`ChipSocket_2.54_EM`,
  4 NPTH holes, `fpaste == []`).
- + ADR-0006's main argument survives — the die is still reworkable from the
  emitter side without scrapping the main board.
- − No socket lid clamping: retention relies on the socket body plus the 4 M2
  mount holes, and the ADR-0006 stack-height figure is void. §F10's
  registration gate must be re-derived from the J12 and mount-hole geometry,
  and the head↔main registration mismatch flagged in `tools/kicad/placement.py`
  has to be resolved before the sandwich can be assembled.

## Evidence

- `hardware/pcb/qssc.kicad_pcb` — Uchip = `qssc:ChipSocket_2.54_EM`, 69 pads,
  zero F.Paste (verified through pcbnew).
- `hardware/pcb/chip_module.kicad_pcb` — Uchip = `qssc:ChipSocket_2.54_DET`,
  69 pads, zero F.Paste: the detector face is a socket too, not ACF.
- `tools/kicad/placement.py:240-246` — "an off-the-shelf through-hole ...
  Die removable."
- `tests/test_pcb_pin_audit.py::test_main_chip_is_female_header_socket` —
  pins the through-hole footprint and the absent paste layer.
