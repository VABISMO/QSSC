<!--
Evidence-first PR: what changed, why (issue ref), and measured results.
See CONTRIBUTING.md for the full norms.
-->

## What

<!-- One or two sentences; bullet list for multi-part PRs. One logical change per commit. -->

## Why

<!-- Fixes #NN — link the issue(s). Why this approach, and which alternatives were rejected. -->

## Test

<!-- Measured on this PR tree — "should work" is not a test result. -->

- [ ] `python -m pytest -rs` green (baseline: **129 passed, 4 skipped**)
- [ ] `ruff check --select=E9,F63,F7,F82,F401,F841 .` clean (CI lint set)
- [ ] Docs updated in the same PR where behavior/counts changed (README, `hardware/HARDWARE_DESIGN.md`, `docs/agents/project-specs.md`)
- [ ] Architecture decision changed or introduced → ADR in `docs/adr/` (supersedes, never edits — see ADR-0001)

## Status

<!-- Open questions, follow-ups, deliberately-untouched scope. -->