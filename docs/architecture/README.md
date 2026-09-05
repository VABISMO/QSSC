# QSSC Architecture Diagrams

Mermaid views of the QSSC system. This directory **complements** — not
duplicates — the three diagrams that already live in the repo:

| Existing diagram | Location |
|---|---|
| Chip architecture stack (ENC/DET/FILT/HOST/WG/EM/SUB/TEC) | `README.md` (chip architecture section) |
| Board-level block diagram (main board + head, full nets) | `hardware/HARDWARE_DESIGN.md` §1 |
| Physical stack cross-section (sandwich, classDefs) | `hardware/HARDWARE_DESIGN.md` §1B |

> ⚠ The `README.md` chip stack still shows the pre-pivot readout
> (analog MUX + XADC, 12-bit). The current architecture is the 4× AD7616
> chain below — tracked upstream as issue #19.

## Views in this directory

| File | Diagram | Question it answers |
|---|---|---|
| `system-overview.md` | flowchart | How do host, FPGA, boards, die and optics connect end-to-end? |
| `readout-chain.md` | sequenceDiagram | What happens, step by step, during one `READ_DETECTORS` round trip? |
| `fpga-rtl.md` | flowchart | Which RTL modules exist and who talks to whom? |
| `power-clock.md` | flowchart | Where does every rail and every clock come from? |

Decisions behind this architecture are recorded in
[`docs/adr/`](../adr/) — e.g. the AD7616 pivot (ADR-0002), the sandwich
(ADR-0003), the emitter drive (ADR-0004), the FPGA package (ADR-0005).