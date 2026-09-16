# 0008. `docs/` layout: knowledge hub (adr/, architecture/, fabrication/); datasheets stay in `hardware/`

## Status

Accepted (2026-09-05) · Tags: documentation, process

## Context

The contributor-facing knowledge of this repo grew across three roots:
`hardware/HARDWARE_DESIGN.md` (design prose), the guidance bundle
`docs/agents/*` (PR #12), and the audit reports (`docs/audits/`, branch
`docs/audit-qssc-004`). ADRs (ADR-0001) needed a home, architecture
diagrams had no index, and the diamond fabrication protocol PDF
(`QSSC_fabrication_protocol_diamond.pdf`) existed only outside the repo.
The obvious consolidation — moving `hardware/datasheets/` into `docs/` as
well — was evaluated and deliberately **not** taken.

## Decision

`docs/` is the knowledge hub, with three sub-directories:

- **`docs/adr/`** — this decision series (ADR-0001).
- **`docs/architecture/`** — Mermaid diagram hub; complements the three
  existing diagrams (`README.md` chip stack, `HARDWARE_DESIGN.md` board
  block diagram and physical stack) with the views they lack: full
  system incl. host software, readout signal chain, FPGA RTL module
  graph, power/clock trees.
- **`docs/fabrication/`** — non-canonical reference PDFs, starting with
  `QSSC_fabrication_protocol_diamond.pdf` (committed 2026-09-05; the
  canonical protocol remains `qssc/protocol.py`, ADR-0007).

**Negative decision — recorded to prevent re-litigation:** the datasheet
set (`DATASHEETS.md`, `BOM.csv`, `pdfs/`) **stays in
`hardware/datasheets/`**. Rationale: it is hardware-adjacent reference
material next to the design doc that cites it (`HARDWARE_DESIGN.md` §2,
§16), and relocating it would touch `tools/gen_bom.py` (hardcoded OUT
path), several HARDWARE_DESIGN links, and the guidance bundle — churn with
no documentation gain.

## Consequences

- + One discoverable root for ADRs, diagrams and reference PDFs.
- + Zero reference breakage: `gen_bom.py`, `HARDWARE_DESIGN.md` and the
  bundle keep their datasheet paths.
- − `docs/` now mixes process (adr/, agents/) and reference (fabrication/)
  material; the per-directory READMEs carry the distinction.
- The fabrication PDF is authored by the upstream maintainer and enters
  the repo through this branch's PR — the PR itself is the author's
  review gate.

## Evidence

- `docs/agents/project-specs.md` → "Specifications & references"
  (datasheet set as authoritative component reference) and
  "Generated artefacts" (`gen_bom.py` OUT path `hardware/datasheets/BOM.csv`).
- `hardware/HARDWARE_DESIGN.md` §2 ("Authoritative BOM:
  `hardware/datasheets/BOM.csv`") and §16 (References).
- Reference map measured 2026-09-05: `grep -rn "pdfs/|datasheets"` →
  references confined to `DATASHEETS.md` (relative, moves with the folder),
  `tools/gen_bom.py:36`, `HARDWARE_DESIGN.md` (~6 links),
  `docs/agents/project-specs.md` (2 lines).

## Alternatives considered

- **Move datasheets to `docs/datasheets/`**: one-stop hub, but 9+ reference
  edits and PR-#12 sequencing risk for cosmetic gain; rejected.
- **Flat `docs/` without sub-directories**: fine at three file classes,
  unmanageable at six; rejected.