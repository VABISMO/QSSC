# Contributing to QSSC

Thanks for contributing. This repo is evidence-first: claims about the
code, the physics or the hardware should be checkable against the tree —
not asserted from memory. The rules below keep issues, PRs and docs
mutually consistent.

## Where knowledge lives

| What | Where |
|---|---|
| Usage, architecture, counts | `README.md` (Documentation index at the bottom) |
| Full hardware design | `hardware/HARDWARE_DESIGN.md` |
| Component datasheets (authoritative) | `hardware/datasheets/` (`DATASHEETS.md`, `BOM.csv`, `pdfs/`) |
| Architecture decisions | `docs/adr/` (start with [ADR-0001](docs/adr/0001-record-architecture-decisions.md)) |
| Architecture diagrams | `docs/architecture/` |
| Facts for agents (build, CI, conventions) | `docs/agents/project-specs.md`, routed by `AGENTS.md` |
| Fabrication protocols (canonical) | `qssc/protocol.py`, served via `/api/protocol` |

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[api,dev]"

python -m pytest -rs          # baseline: 129 passed, 4 skipped
ruff check --select=E9,F63,F7,F82,F401,F841 .   # the CI lint set (ci.yml)
```

## Reporting defects

Use the **Defect report** issue template (`.github/ISSUE_TEMPLATE/`). One
defect — or one tightly coupled defect cluster — per issue. Evidence
norms:

- Every claim carries a `file:line` reference or a commit hash.
- Measured output beats assertion: paste the actual log, count, or
  measurement, and state the tree/commit it was taken on.
- Hypotheses are marked as hypotheses, not stated as facts.
- State the impact (what breaks or drifts if this is not fixed) and a
  minimal suggested fix.

## Submitting changes

1. Branch from `main`: `fix/…`, `docs/…`, or `feat/…`.
2. Use the PR template: **What / Why (refs issue) / Test**.
3. Keep the test suite green and state the measured baseline in the
   **Test** section — "should work" is not a test result.
4. One logical change per commit.

### Commit message style

`<area>(<scope>)?: <imperative summary>`, matching the existing history:

```
pcb: two-board sandwich routed + DRC/ERC 0/0, HDI via rules
docs(pcb): two-board AD7616 sandwich — rewrite HARDWARE_DESIGN, BOM, tests
fix(ci): add tlc5947_ctrl.v to yosys synth
test(pcb): per-datasheet pin-audit gate (Ironwood socket, FT232 24/23)
```

Common areas: `pcb`, `fpga`, `docs`, `qssc`, `api`, `fix`, `test`, `ci`.

## Keep docs in sync with commits

Any commit that changes **behavior, numbers, pinouts, module lists or
counts** updates the affected docs **in the same PR**:

- `README.md` — usage, architecture description, module/test counts
- `hardware/HARDWARE_DESIGN.md` — hardware behavior and rationale
- `docs/agents/project-specs.md` — facts agents rely on
- `docs/adr/` — if a change touches an accepted architecture decision,
  open a new ADR that supersedes it (ADR-0001); never edit an accepted
  ADR in place

This is what keeps audit findings and issue reports verifiable against a
single current tree — stale docs are how doc/RTL divergences start.

## For agent contributors

`AGENTS.md` routes agent work to the guidance bundle
(`docs/agents/project-specs.md` and companions). Agents follow the same
evidence rules as above and add `<!-- UNVERIFIED — source: … -->` markers
for anything they could not execute on the tree.