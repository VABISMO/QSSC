# 0007. Fabrication protocol as structured Python data (`qssc/protocol.py`), not standalone documents

## Status

Accepted (upstream commit `a3ab46b`, 2026-08-28 — the oldest decision in
this backfill) · Tags: protocol, api, documentation

## Context

The fabrication protocols (NV-diamond compute chip, hBN V_B⁻ and SiC V_Si
sensor variants) originally lived as standalone `docs/FABRICATION_PROTOCOL*.md`
/ `.html` / `.pdf` documents. Two problems: three parallel renderings of the
same content drifted apart, and the REST API + frontend could not serve the
content without re-parsing prose.

## Decision

Delete the standalone document tree (`a3ab46b` removed the whole `docs/`
directory, −3115 lines) and make **`qssc/protocol.py` the canonical,
structured source**: the protocols are Python data, served by the REST API
(`/api/protocol`) and rendered by the frontend protocol viewer.

## Consequences

- + One structured source; API and dashboard consume it directly.
- + No doc drift between .md/.html/.pdf renderings — there are no renderings.
- − `tools/build_protocol_html.py` still reads the deleted `docs/` input and
  is therefore not runnable as-is (documented in the README's fabrication
  section and in `docs/agents/project-specs.md` → "Generated artefacts").
- Non-canonical reference material (e.g. `docs/fabrication/*.pdf`,
  ADR-0008) may exist under `docs/` without reversing this decision — the
  canonical protocol remains the Python data.

## Evidence

- `a3ab46b` — "Delete docs directory" (9 files, −3115).
- README "Fabrication protocol" section (protocols as structured data in
  `qssc/protocol.py`, served by `/api/protocol`).
- `docs/agents/project-specs.md` → "Generated artefacts" (the broken
  `build_protocol_html.py` row).

## Alternatives considered

- **Keep all three renderings in sync manually**: drift-prone; rejected
  (this is what `a3ab46b` removed).
- **Generate the HTML from the Python data**: would fix
  `build_protocol_html.py`, but no consumer needs it today; left as a
  possible follow-up instead of a decision.