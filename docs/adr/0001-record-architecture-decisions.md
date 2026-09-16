# 0001. Record architecture decisions as ADRs in `docs/adr/`

## Status

Accepted (2026-09-05) · Tags: process, documentation

## Context

QSSC evolved through several hardware pivots — photon counters → 4× AD7616
readout, single-board → two-board sandwich, CPG236 → CSG324, discrete
emitter driver → 3× TLC5947 — but their rationale lived only in commit
subjects and prose inside `hardware/HARDWARE_DESIGN.md`. Why a decision was
taken, and what alternatives were rejected, was nowhere recoverable. Two
audits against this repo (QSSC-002, QSSC-004) traced real findings back to
exactly that gap: docs and RTL told different stories because the *decision*
layer was missing, not because anyone wrote something wrong on purpose.

## Decision

Record architecture decisions as short ADRs in `docs/adr/`, one file per
decision:

- Numbering `NNNN-title.md`; format is MADR-inspired: *Status / Context /
  Decision / Consequences / Evidence / Alternatives considered*.
- Lifecycle: `proposed → accepted → (superseded | rejected)`. An accepted
  ADR is **not edited** — a change of mind produces a new ADR that links
  and supersedes the old one.
- Evidence rules follow the repo guidance bundle
  (`docs/agents/project-specs.md` → "Evidence rules"): every claim carries
  a commit hash or `file:line` citation; nothing is asserted from memory.
- Numbering note: ADR-0002…0007 follow the backfill sequence approved on
  2026-09-05 (thematic order, not strict chronology); each ADR's Status
  line carries the true decision date, and its Evidence section the true
  commits.

## Consequences

- The decision chain becomes readable without mining `git log`.
- Every architectural change now has two update sites (code + ADR); a PR
  that changes an accepted decision must reference or create an ADR.
- Overhead is bounded: ADRs stay short (target ≤ 60 lines); deep rationale
  remains in `HARDWARE_DESIGN.md`, linked by `file:line`.

## Evidence

- Pivot history mined from `git log` (hashes per ADR-0002…0007).
- Audit series: QSSC-002 (full-repo, grade F → issues #7–#9),
  QSSC-004 (PCB physics, grade B → `docs/audits/` once merged).

## Alternatives considered

- **Prose-only in `HARDWARE_DESIGN.md`** (status quo): rationale stays
  buried in a 1500-line document; rejected — this is the state that made
  the audits necessary.
- **External design wiki**: not versioned with the code, invisible to
  offline contributors and AI agents; rejected.