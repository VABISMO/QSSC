# Project specs, tools, build & conventions

> **Purpose.** This file holds every project-specific detail an AI coding
> agent (or human contributor) needs to work in this repository. It is
> **loaded on demand by the metaprompt in [`AGENTS.md`](../../AGENTS.md)** —
> together with [`research.md`](research.md) (methodology) and
> [`devmind.md`](devmind.md) (engineering mindset).
>
> **Evidence rules.** Every factual claim here is backed by evidence: a
> file in this repo (cite as `file:line`), git history, CI configuration,
> or a command that was actually run. Documented commands carry a
> verification marker — `<!-- verified YYYY-MM-DD: <observation> -->` —
> or are marked `<!-- UNVERIFIED — source: <where it was read> -->`.
> Sections without evidence are deleted, never left as empty stubs.

<!-- Verified on branch docs/agents-bundle (tree f5b887a = fork/main, 2026-09-05). -->

## Build Commands

```bash
# create an environment (requires Python >= 3.9; CI matrix: 3.10–3.12, Docker: 3.12)
python -m venv .venv && . .venv/bin/activate
pip install -e ".[api,dev]"          # core + FastAPI extras + pytest/httpx + matplotlib
```
<!-- verified 2026-09-05: `pip show qssc` reports 2.0.0 installed editable (qssc.egg-info at repo root); package set from pyproject.toml [project] + [project.optional-dependencies] -->

```bash
python -m pytest -rs                                     # full suite: 129 passed, 4 skipped (~36 s)
python -m pytest tests/test_core.py -k test_backend_shor_15 -q   # single test case
python -m pytest --collect-only -q                       # per-file counts (see Testing)
ruff check --select=E9,F63,F7,F82,F401,F841 .            # exact CI lint job
qssc info                                                # CLI smoke test (also run in CI)
qssc odmr --profile hBN-VB                               # CLI ODMR for hBN (also run in CI)
python tools/check_xdc_ports.py                          # XDC ↔ top_level.v port audit
```
<!-- verified 2026-09-05: suite = 129 passed, 4 skipped, 173 warnings in 36.47 s; single test exit 0; ruff "All checks passed!"; `qssc info` renders the NV defect table; check_xdc_ports exit 0 (spi_pins width=4, tx width=1) -->

```bash
QSSC_SLOW=1 python -m pytest -m slow    # full routing-pipeline test (deselected by default)
uvicorn api.main:app --port 8000        # REST API dev server
cd frontend && npm ci && npm run build  # React dashboard production build (Node 20 in CI)
```
<!-- UNVERIFIED — source: pyproject.toml `slow` marker def; README.md:56 uvicorn line; .github/workflows/ci.yml frontend job (node 20, npm ci, npm run build). None of the three was executed in this environment (QSSC_SLOW not set; no node_modules/dist present; uvicorn not started). -->

## Specifications & references

Normative / reference material this project's behaviour is checked against:

- **`hardware/datasheets/`** — `DATASHEETS.md` (index), `BOM.csv`, `pdfs/` —
  the authoritative component datasheets for the PCB (pin audits in
  `tests/test_pcb_pin_audit.py` are gated on per-datasheet pin facts).
- **`hardware/HARDWARE_DESIGN.md`** — the full hardware design document.
  Sections referenced by code and tests: §5 (UART command protocol), §14
  (CNC G-code), §15 (pcbnew limitations), §17 (synthesis flow) — per
  README.md:211, 324, 331, 197.
- **`fpga/constraints.xdc`** — Artix-7 XC7A35T-1**CSG324**C pin assignments
  (constraints.xdc:3); mechanically cross-checked against `fpga/rtl/top_level.v`
  by `tools/check_xdc_ports.py` (CI fpga job).
- **RFC 5280** (X.509) — referenced in `qssc/crypto.py:68` for the
  certificate serial-number encoding; the module issues Ed25519 key pairs +
  self-signed certificate PEMs consumed by `api/runner.py:25`.
- **Defect-physics literature** — `qssc/defects.py` carries citations in
  comments (e.g. Balasubramanian 2009 for NV; Carter/Embley 2017, Carter
  2015 for SiC/hBN). Caveat: the `depol_1q`/`depol_2q` rates and gate
  durations in `qssc/defects.py` are **not** cited (tracked upstream as
  VABISMO/QSSC issue #9).
- No vendored third-party code and no pinned reference implementations
  exist in the repo (only the datasheet PDFs).

## Linting / Formatting

- **ruff** with the exact CI selection set
  `--select=E9,F63,F7,F82,F401,F841` (syntax errors, ambiguous/undefined
  names, unused imports/variables) — `.github/workflows/ci.yml` `lint` job.
  No formatter is configured (no black/ruff-format/isort section in
  `pyproject.toml`, no `.editorconfig`).
- The `slow` marker and `-q` defaults live in `pyproject.toml`
  (`[tool.pytest.ini_options]`).
- Observable failure if skipped: red **Lint (ruff)** job on the PR — any
  F401/F841 introduced by new code fails CI.

<!-- verified 2026-09-05: full CI select set runs clean on this tree ("All checks passed!") -->

## Coding Conventions

Observed, not prescribed (sampled across `qssc/`, `api/`, `tests/`,
`frontend/src/`):

- **Python**: 4-space indent, PEP 8 naming; module header is
  `# path/file.py` followed by a NumPy-style docstring with reST markup
  (e.g. `qssc/defects.py:1-9`); type hints on public functions (e.g.
  `qssc/backend.py` `_signal_survival(self, circuit: QuantumCircuit) -> tuple`).
- **Tests**: plain pytest, no test classes; platform matrices via
  `@pytest.mark.parametrize` (e.g. `tests/test_algorithms_platforms.py`);
  optional dependencies gated with module-level
  `pytest.importorskip("pcbnew")` (see Testing).
- **JavaScript**: React function components; ES modules; a thin fetch
  client reading `import.meta.env.VITE_API_BASE`
  (`frontend/src/api.js:1-2`).
- **Verilog**: one file per module under `fpga/rtl/`, `` `timescale 1ns/1ps``
  header + `// module.v` banner comment (e.g. `fpga/rtl/adc_reader.v:1-3`).

## Commit Message Style

Nothing is enforced. Dominant forms in `git log` (50 subjects sampled):
`type(scope): summary` (fix/test/docs/ci/ui/feat) and bare topical prefixes
(`pcb:`, `fpga:`, `readout:`, `phase 2:`). Real examples, verbatim:

- `fix(backend): model 2q relaxation duration and cumulative gate depolarization`
- `test(pcb): per-datasheet pin-audit gate (Ironwood socket, FT232 24/23, ADF4351 TEMP, no_connect)`
- `pcb: commit regenerated schematics + project netclasses (ERC 0/0)`
- `fpga: refactor RTL serial->parallel (64 photon counters, 64 emitter enables, I2C threshold DAC)`
- `fix(ci): add tlc5947_ctrl.v to yosys synth, clear ruff F401/F841`

Summaries are concrete and often carry measured results in parentheses.
Merge style on the upstream repo: merge commits of the form
`Merge pull request #N from VABISMO/<branch>`.

## Common Workflows

1. **Run the suite** — `python -m pytest -rs` (see Build Commands).
2. **Run one case** — `python -m pytest tests/test_core.py -k test_backend_shor_15 -q`.
3. **Lint before committing** — `ruff check --select=E9,F63,F7,F82,F401,F841 .`
   (exact CI set; unused imports/variables are the usual failures).
4. **CLI smoke** — `qssc info && qssc odmr --profile hBN-VB` (mirrors CI).
5. **Check FPGA pins** — after editing `fpga/constraints.xdc` or
   `fpga/rtl/top_level.v`: `python tools/check_xdc_ports.py`; full RTL gate
   needs iverilog + yosys (see CI — toolchain absent from a typical dev box).
6. **Regenerate the PCB** — `python tools/gen_qssc_kicad.py` rewrites
   `hardware/pcb/qssc.kicad_*` from the pin/net tables in the repo, then the
   `test_pcb_*` files validate the result. Routing pipeline:
   `tools/route_signals.py` (signals + BGA fanout) **then**
   `tools/route_qssc_pcb.py` (inner planes + stitching; the zone fill runs
   in a `--fill` subprocess automatically). Both run on KiCad 8–10
   bindings via `tools/_kicad_compat.py` (PR #24) and are idempotent —
   point them at a COPY with `QSSC_PCB_PATH=<copy>`: pcbnew's `SaveBoard`
   rewrites `.kicad_pro`/`.kicad_prl` with the running KiCad's schema.

   <!-- verified 2026-09-07: pipeline run on KiCad 10.0.6 on a /tmp copy = 2 zones (In1.Cu GND 26030 mm^2, In2.Cu +3V3 27468 mm^2) + 396 vias, exit 0, idempotent re-run; .kicad_pro/.prl schema churn observed on in-repo runs (git restore needed) -->
   <!-- UNVERIFIED — gen_qssc_kicad regeneration itself not executed this session (in-place artefact overwrite) -->
7. **Serve the API locally** — `uvicorn api.main:app --port 8000`;
   endpoints include `/api/protocol` (`api/main.py:261`).

   <!-- UNVERIFIED — server start not executed; endpoint list read from api/main.py -->

## Architecture Overview

Data flow (REST/CLI run path):

1. Entry points: the `qssc` CLI (`qssc/cli.py`, click + rich) and the
   FastAPI app (`api/main.py`) → circuit runner in `api/runner.py`.
2. The runner builds the algorithm circuit via `qssc/circuits.py`
   (Shor QPE, QFT-NOS period-finding, Grover, arithmetic, Bell) and selects
   the defect platform via `qssc/defects.py` (`auto_align` maps a requested
   platform to the best site profile).
3. `QSSCBackend` (`qssc/backend.py`, Qiskit `BackendV2`) transpiles/executes
   through qiskit-aer with one of three noise modes: `real` (density-matrix
   ≤ 10 qubits, analytic blend above), `phenomenological`, `none`.
4. Results are post-processed (`postprocess_period` etc. in
   `qssc/circuits.py`) into period/factors/assessment payloads.
5. `api/jobs.py` queues async runs — Redis/RQ when reachable, else an
   in-process executor (same module, feature-detected).
6. The React frontend (`frontend/src/`, Vite) calls `/api/*` through the
   thin client `frontend/src/api.js` (base URL from `VITE_API_BASE`).

Key files:

| File | Purpose |
|---|---|
| `qssc/backend.py` | `QSSCBackend` (BackendV2), noise model, assessment |
| `qssc/circuits.py` | algorithm builders + post-processing |
| `qssc/defects.py` | NV/hBN/SiC defect profiles + `auto_align` |
| `qssc/simulator.py` | ODMR / photophysics simulator, `SimulatedFPGA` |
| `qssc/protocol.py` | structured fabrication-protocol data (served by REST) |
| `qssc/qkd.py` | QKD / quantum-random-number demos |
| `qssc/crypto.py` | Ed25519 keygen + X.509 certificate PEM (RFC 5280) |
| `qssc/cli.py` | click CLI (`calibrate`, `info`, `math`, `odmr`, `period`, `shor`) |
| `api/main.py` | FastAPI routes incl. `/api/protocol` |
| `api/runner.py` | circuit execution + `qssc.crypto` certificates |
| `api/jobs.py` | job queue (Redis/RQ or in-process fallback) |
| `fpga/rtl/*.v` | 9 Artix-7 Verilog modules |
| `tools/*.py` | generators/audits (KiCad, G-code, XDC, BOM, netlist) |

## Testing

- **Layout**: 11 files under `tests/`. Collected cases per file
  (pytest `--collect-only -q`): `test_algorithms_platforms` 35,
  `test_api` 33, `test_core` 36, `test_gcode` 18, `test_pcb_drc` 1,
  `test_pcb_head` 5, `test_pcb_stackup` 1 → **129 collected**; plus 4
  module-skipped pcbnew-gated files → **133 items total**.
- **Expected result**: `129 passed, 4 skipped` (~36 s locally) on a box
  **without** KiCad. The 4 skips are module-level
  `pytest.importorskip("pcbnew")` gates at `tests/test_pcb_pin_audit.py:28`,
  `test_pcb_route.py:32`, `test_pcb_route_full.py:31`,
  `test_pcb_route_signals.py:28` — KiCad's `pcbnew` python module is not
  pip-installable; these skip in CI too.
- **With KiCad installed** (local dev box, KiCad 10.0.6 + venv python 3.14,
  `PYTHONPATH=/usr/lib/python3.14/site-packages`): the pcbnew-gated files
  RUN — the 4 skips become 3 gcode scribe/stencil skips
  (`test_gcode.py:153`, no through-cut outline) + 1 `QSSC_SLOW` gate
  (`test_pcb_route_full.py:89`). Suite on a KiCad-10 box with the
  `tools/_kicad_compat.py` layer (PR #24): **4 failed, 164 passed,
  4 skipped** — the 4 failures are pre-existing board connectivity defects
  (`mod_sync` dangling net, 11 split nets on `qssc.kicad_pcb`, 80 split
  `DET_IN*` on `chip_module.kicad_pcb`), documented in the PR #23 review.

  <!-- verified 2026-09-07: suite on fix/kicad-version-compat (739d732) with
  KiCad 10.0.6 pcbnew = "4 failed, 164 passed, 4 skipped in 53.41s"; skip
  reasons from `pytest -rs`; without pcbnew the same tree = "129 passed,
  4 skipped" (docs/agents-bundle tree) -->
- **Test data**: no fixtures/golden dirs — tests parse the repo artefacts
  directly (`hardware/pcb/*.kicad_pcb` sexpressions, `hardware/gcode/*`).
- **Config**: `pyproject.toml [tool.pytest.ini_options]` —
  `testpaths=["tests"]`, `addopts="-q"`, `pythonpath=["."]`, marker `slow`
  deselected unless `QSSC_SLOW=1`.
- **Local quirk (pytest 9.1.1)**: with `-q`, the trailing `N passed`
  summary line only appears when a short-summary flag (`-rs`/`-ra`) is
  passed. Local dev venv runs Python 3.14 — outside the CI matrix
  (3.10–3.12); a failing import there does not imply a CI failure.
- Updating expected output: change the assertion in the test in the same
  commit as the behaviour change (no golden files to regenerate).

## CI

`.github/workflows/` (the only content of `.github/`; no issue templates,
no CONTRIBUTING.md):

- **`ci.yml`** — four jobs:
  - *test*: matrix Python 3.10/3.11/3.12, `pip install -e ".[api,dev]"`,
    `pytest --cov=qssc --cov=api`, CLI smoke (`qssc info`,
    `qssc odmr --profile hBN-VB`, `qssc odmr --profile NV`).
  - *lint*: the ruff select set above.
  - *fpga*: pyverilog parses all `fpga/rtl/*.v`; `tools/check_xdc_ports.py`;
    `iverilog -g2012 -DFAST_SIM` testbench (`grep "ALL CHECKS PASSED"`);
    yosys `synth_xilinx` via `fpga/synth/run_synth.sh` (OSS CAD Suite pinned
    to tag `2026-08-26`), gated on "synth_xilinx COMPLETE", zero latches and
    "Fits XC7A35T … yes" in `fpga/synth/SYNTH_REPORT.md`.
  - *frontend*: Node 20, `npm ci`, `npm run build`.
- **`deploy.yml`** — workflow_run gate: deploys (GitHub Pages + Render hook)
  only after green CI on `main`; `RENDER_DEPLOY_HOOK_URL` secret.
- **`release.yml`** — build artifacts.

Local repro: the Python jobs are exactly the Build Commands above; the fpga
job needs `iverilog`, `pyverilog` and oss-cad-suite, which a dev box usually
lacks (absent here) — `tools/check_xdc_ports.py` alone runs fine.

## Runtime Configuration / Usage

- **CLI** (`qssc --help`): `calibrate`, `info`, `math`, `odmr`, `period`,
  `shor`. Examples: `qssc info` (backend/defect summary, NV default),
  `qssc shor 15` → factors 3 × 5, `qssc odmr --profile hBN-VB`.
- **API**: `uvicorn api.main:app --port 8000`; `/api/protocol` serves the
  structured fabrication protocol (`api/main.py:261-262`); a
  custom-circuit endpoint accepts user QASM/Qiskit payloads — treat input as
  untrusted (sandboxing caveat in README Deployment section).
- **Environment**: `QSSC_SLOW=1` (enables the `slow` test), `VITE_API_BASE`
  (frontend API base, `frontend/src/api.js:2`), `RENDER_DEPLOY_HOOK_URL`
  (deploy secret). No config files; all knobs are CLI flags/env.
- **Deployment**: `render.yaml` (Blueprint; `autoDeploy: false`, gated on
  CI), `Dockerfile` (python:3.12-slim; job queue falls back in-process when
  Redis/Mongo are unreachable — `api/jobs.py`).

## Dependencies

- **Python** (`pyproject.toml`): qiskit>=1.2, qiskit-aer>=0.14, numpy>=1.24,
  click>=8.1, rich>=13.0, cryptography>=42.0; extras `api`
  (fastapi, uvicorn, pydantic, redis, rq, pymongo), `dev` (pytest>=8.0,
  pytest-cov, matplotlib, httpx), plus `plot`, `cv`, `hw` (pyserial).
  `requires-python >=3.9`; local venv runs 3.14, CI 3.10–3.12, Docker 3.12.
- **Frontend**: react 18.3.1, vite 5.4.10 (`frontend/package.json`); Node 20
  in CI; `npm ci` (no lockfile drift concerns — `package-lock.json` present).
- **CI toolchain**: iverilog (`-g2012`), pyverilog, OSS CAD Suite tag
  `2026-08-26` (yosys synth_xilinx), ruff (latest).
- **System**: none beyond the above for the Python/API path; FPGA
  sim/synthesis needs the iverilog/oss-cad-suite binaries; PCB regeneration
  needs KiCad (`kicad-cli`/`pcbnew` — the `pcbnew` python module is not
  pip-installable). When driving `pcbnew` from a venv python, add KiCad's
  python dir to `PYTHONPATH` (Arch: `PYTHONPATH=/usr/lib/python3.14/site-packages`).
  The routing tools run on KiCad 8–10 bindings via `tools/_kicad_compat.py`
  (swig iterator shim, layer-aware via widths, fill-in-subprocess, symbolic
  copper layer IDs) — PR #24.

  <!-- verified 2026-09-07: KiCad 10.0.6, pcbnew via PYTHONPATH=/usr/lib/python3.14/site-packages with a venv python 3.14; route_qssc_pcb.py + route_signals.py run exit 0 and build 2 planes + 396 vias (PR #24 head 739d732); without the compat layer the same tools segfault (exit 139) or build only 1 zone -->

## Source Layout

```
qssc/            Python core: backend, circuits, defects, simulator,
                 protocol, qkd, crypto, cli
api/             FastAPI app: main.py (routes), runner.py (execution),
                 jobs.py (queue + in-process fallback)
frontend/        React 18 + Vite dashboard (src/api.js thin client)
fpga/            rtl/ (9 Verilog modules), sim/ (testbench),
                 constraints.xdc, synth/ (run_synth.sh + SYNTH_REPORT.md)
hardware/        pcb/ (KiCad project), gcode/ (CNC toolpaths),
                 datasheets/ (DATASHEETS.md, BOM.csv, pdfs/),
                 HARDWARE_DESIGN.md
tools/           generator + audit scripts (KiCad, G-code, XDC, BOM,
                 netlist, pin/port checks)
tests/           11 pytest files (unit, API, PCB, G-code)
presentation/    index.html deck + PDF export
docs/agents/     this guidance bundle
```

Build output locations: `qssc.egg-info/` (editable install, repo root),
`frontend/dist/` (Vite build), `fpga/synth/synth.log` +
`fpga/synth/SYNTH_REPORT.md` (synthesis). All are git-ignored or
regenerable (see `.gitignore`: `__pycache__/`, `*.egg-info/`, `dist/`,
`node_modules/`, `frontend/dist/`, KiCad scratch/DRC reports).

## Generated artefacts (never hand-edit)

| Artefact | Source | Regenerate with |
|---|---|---|
| `qssc.egg-info/` | `pyproject.toml` | `pip install -e .` |
| `frontend/dist/` | `frontend/src/` | `cd frontend && npm run build` |
| `hardware/pcb/qssc.kicad_*` | pin/net tables in `tools/` | `python tools/gen_qssc_kicad.py` |
| `hardware/gcode/*` | geometry in `tools/gen_qssc_gcode.py` | `python tools/gen_qssc_gcode.py` |
| `fpga/synth/synth.log`, `SYNTH_REPORT.md` | `fpga/rtl`, `constraints.xdc` | `bash fpga/synth/run_synth.sh` (needs oss-cad-suite) |
| `tools/build_protocol_html.py` output | reads `docs/FABRICATION_PROTOCOL*.md` as input — restored upstream in `d2159f6` after the `a3ab46b` deletion, so the tool should run again; UNVERIFIED on this branch (branch predates the restore) | re-verify on current `main` |

<!-- verified 2026-09-05: pip editable install + pytest + ruff + xdc-check executed. Regeneration commands for PCB/G-code/synthesis UNVERIFIED — read from tool docstrings, scripts and ci.yml; not executed (in-place artefact overwrite / missing toolchain) -->

## Writing pull requests / issues

No `CONTRIBUTING.md`, no `.github/ISSUE_TEMPLATE/` (`.github/` contains
only `workflows/`). Observed norms from repo history and the upstream
queue:

- PRs from topic branches into `main`; merged as merge commits
  (`Merge pull request #N from VABISMO/<branch>`).
- Commit subjects follow the style section above; one logical change per
  commit (see `devmind.md`).
- Issues: no template — the issues filed against this repo so far
  (VABISMO/QSSC #7, #8, #9) follow the shape *defect → measured evidence →
  file:line cites → suggested fix*.
- Upstream contribution path: fork → topic branch → PR
  (github.com/bartman081523/QSSC → VABISMO/QSSC).

## Verification log

| Command | Status | Evidence | Date |
|---------|--------|----------|------|
| `python -m pytest -rs` | verified | 129 passed, 4 skipped, 173 warnings, 36.47 s | 2026-09-05 |
| `python -m pytest --collect-only -q` | verified | per-file counts 35/33/36/18/1/5/1 (+4 pcbnew module-skips) | 2026-09-05 |
| `python -m pytest tests/test_core.py -k test_backend_shor_15 -q` | verified | exit 0 | 2026-09-05 |
| `ruff check --select=E9,F63,F7,F82,F401,F841 .` | verified | "All checks passed!" | 2026-09-05 |
| `qssc --help` / `qssc info` | verified | 6 commands; NV defect table rendered | 2026-09-05 |
| `python tools/check_xdc_ports.py` | verified | exit 0 (spi_pins=4, tx=1) | 2026-09-05 |
| `pip show qssc` | verified | 2.0.0, editable install | 2026-09-05 |
| `uvicorn api.main:app --port 8000` | UNVERIFIED | README.md:56 + api/main.py docstring, not executed | 2026-09-05 |
| `QSSC_SLOW=1 python -m pytest -m slow` | UNVERIFIED | pyproject marker def, not executed | 2026-09-05 |
| `cd frontend && npm ci && npm run build` | UNVERIFIED | ci.yml frontend job; no node_modules locally | 2026-09-05 |
| `iverilog` / `yosys` runs | UNVERIFIED | ci.yml fpga job; binaries absent locally | 2026-09-05 |
| `python tools/gen_qssc_kicad.py` / `gen_qssc_gcode.py` | UNVERIFIED | tool docstrings; not executed (in-place overwrite) | 2026-09-05 |
| `python tools/build_protocol_html.py` | UNVERIFIED | input `docs/FABRICATION_PROTOCOL*.md` restored upstream in `d2159f6`; not re-run on this branch | 2026-09-05 |
| `python -m venv .venv && pip install -e ".[api,dev]"` | UNVERIFIED as literal sequence | equivalent editable install already present; dep set read from pyproject.toml | 2026-09-05 |