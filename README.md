# QSSC — Quantum Solid State Chip (room temperature)

[![CI](https://github.com/VABISMO/QSSC/actions/workflows/ci.yml/badge.svg)](https://github.com/VABISMO/QSSC/actions/workflows/ci.yml)
[![License: Apache-2.0 NC](https://img.shields.io/badge/license-Apache--2.0%20NC-blue.svg)](LICENSE)
[![Python 3.10–3.12](https://img.shields.io/badge/python-3.10–3.12-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-87%20passed-brightgreen.svg)](#testing)

A room-temperature **solid-state spin-defect** quantum platform: a Qiskit
backend, an FPGA control stack with PCB design, a validated chip-fabrication
protocol, and a web dashboard. This project remasters and merges two earlier
projects by the same author — an hBN spin-defect *chip design* and a Qiskit
NV-diamond *simulator* — into one coherent, renamed deliverable.

<img width="1820" height="951" alt="image" src="https://github.com/user-attachments/assets/8d7b3ccd-f5f2-4925-97b9-cd6bb6e4724e" />


> Solid-state spin defects (NV-diamond, hBN V_B⁻, SiC V_Si) keep quantum
> coherence at **~300 K**, so the chip does not require a dilution refrigerator.
> The canonical **default** defect is the **nitrogen-vacancy (NV⁻) centre in
> CVD diamond** — the only family here whose room-temperature coherence
> (**T₂ ≈ 1.8 ms** in ¹²C-enriched diamond) is long enough to *compute*
> seriously at 300 K. Zero-field splitting **D ≈ 2.87 GHz**, room-temperature
> ODMR readout. hBN V_B⁻ and SiC V_Si are retained as **sensor variants**.

## What's in the box

| Layer | Where | Description |
|---|---|---|
| **Python core** | `qssc/` | `QSSCBackend` (Qiskit `BackendV2`), simulator, defect profiles, quantum circuits, fabrication-protocol model |
| **REST backend** | `api/` | FastAPI app exposing the simulator, ODMR model, protocol and a circuit runner |
| **Frontend** | `frontend/` | React 18 + Vite dashboard (ODMR chart, qubit grid, circuit runner, protocol viewer) |
| **FPGA RTL** | `fpga/rtl` | 8 Verilog modules (Artix-7) — UART, SPI/ADF4351 PLL, 8×8 on-chip CQD emitter array, on-chip graphene/MoS₂ detector readout (analog MUX + XADC + 1 kHz lock-in), command parser, top-level |
| **Hardware** | `hardware/` | KiCad PCB, XDC constraints, CNC G-code, `HARDWARE_DESIGN.md` |
| **Protocol** | `docs/FABRICATION_PROTOCOL.md` | 5-section lab fabrication protocol, validated against published studies |
| **Protocol (HTML)** | `docs/protocol.html` | Branded, print-ready HTML render of the protocol (regenerate with `python3 tools/build_protocol_html.py`) |
| **Presentation** | `presentation/index.html` | Self-contained HTML deck with software mocks and diagrams |
| **PDFs** | `presentation/QSSC_presentation.pdf`, `docs/QSSC_fabrication_protocol.pdf` | Headless-Chromium exports of the presentation and protocol |
| **Tests** | `tests/` | 87 tests (defects, simulator, circuits, backend, API, protocol, crypto) |

## Quick start

```bash
cd qssc
python -m venv .venv && . .venv/bin/activate
pip install -e ".[api,dev]"

# run the test suite
python -m pytest                       # 87 passed

# try the CLI
qssc info                              # backend / defect summary (defaults to NV)
qssc shor 15                           # → factors 3 × 5
qssc odmr                              # ODMR spectrum (NV default, dip at 2.87 GHz)

# start the REST backend
uvicorn api.main:app --port 8000

# in another shell, the dashboard
cd frontend
npm install && npm run dev             # http://localhost:5173
```

## Defect platforms

| Platform | Defect | D (GHz) | T₂ @ 300 K | Readout err | Excitation | Role |
|---|---|---|---|---|---|---|
| **Diamond** | **NV⁻ (nitrogen-vacancy)** | **2.87** | **~1.8 ms\*** | **8 %** | **532 nm (green)** | **default / compute** |
| hBN | V_B⁻ (boron-vacancy) | 3.46 | ~2 µs | 12 % | 561 nm | sensor variant |
| SiC | V_Si (silicon vacancy) | 0.07 / 0.14 | ~40 µs | ~15 % | 780 nm | sensor variant |

\* NV-diamond T₂ ≈ 1.8 ms at 300 K in ¹²C-enriched CVD diamond
(Balasubramanian 2009) — long enough to **compute** at room temperature
(T₁ ≈ 6 ms, single-qubit gate ~25 ns, two-qubit gate ~200 ns, readout ~3 µs).
hBN V_B⁻ and SiC V_Si are kept as **sensor variants** (their T₂ is too short
for serious compute at 300 K, but they sense well).

## Chip architecture

The chip integrates its own emitter and detector arrays and is read
**electrically** — no camera, no alignment jig. The 8×8 CQD emitter array
pumps the NV⁻ ensemble (green ~520–540 nm); a resonant microwave X-gate
(2.87 GHz) flips the spin to the dark ms=±1 state; the on-chip
graphene/MoS₂ detector array measures the resulting photocurrent dip
(ODMR). A 650 nm long-pass filter blocks the green pump and passes the NV
PL (637–800 nm). The Artix-7 XADC samples each electrode with 1 kHz lock-in
demodulation.

```mermaid
flowchart TB
  classDef layer fill:#16203f,stroke:#263056,color:#cfe
  classDef flow fill:#0c1430,stroke:#1d2748,color:#9fb
  classDef mw fill:#2a1530,stroke:#4a2a55,color:#f9a
  classDef elec fill:#102018,stroke:#1f4a2a,color:#9fe

  ENC["**Encapsulation** (PMMA / epoxy) ~1 µm"]:::layer
  DET["**Graphene / MoS₂ photodetector array** — 64 electrodes, bias 0.1–1 V"]:::layer
  FILT["**650 nm long-pass filter** — blocks green pump, passes NV PL"]:::layer
  HOST["**CVD diamond spin-defect host** — NV⁻, D ≈ 2.87 GHz, T₂ ≈ 1.8 ms"]:::layer
  WG["**SU-8 / PDMS polymer waveguides** — couple pump + site PL"]:::layer
  EM["**8×8 CQD ASE/LED emitter array** — ITO/PEDOT:PSS/CQD-PVA/ZnO/Ag, 520–540 nm"]:::layer
  SUB["**ITO-coated glass substrate + Cu contacts** — 1–5 cm²"]:::layer
  TEC["**Peltier (TEC1-12706) + liquid cooling** — 295 K"]:::layer

  ENC --- DET --- FILT --- HOST --- WG --- EM --- SUB --- TEC

  EM -- "green pump 520–540 nm (↑)" --> WG
  WG -- "coupled pump (↑)" --> HOST
  HOST -- "spin init → ms=0 bright" --> HOST
  HOST -- "NV PL 637–800 nm (↑)" --> FILT
  FILT -- "passes PL, blocks pump" --> DET
  DET -- "photocurrent (↓)" --> TIA["**TIA + analog MUX**"]:::elec
  TIA --> XADC["**Artix-7 XADC · 1 kHz lock-in**"]:::elec
  MW["**ADF4351 MW · 2.87 GHz**"]:::mw
  MW -. "resonant X-gate (phase ≈ π) → ms=±1 dark" .-> HOST
  DET -. "ODMR dip = reduced photocurrent → bit 1" .-> XADC
```

Readout: a pumped site with **no** resonant MW reads **bright** (high ADC
code, bit 0); a resonant X-gate flips the spin to **dark** (low ADC code,
ODMR dip, bit 1). Calibration drives each emitter pixel and reads all
detectors to build the emitter-pixel → detector-electrode LUT — no jig.

## Qiskit backend

`qssc.backend.QSSCBackend` is a real `BackendV2` subclass with a `Target`,
`CouplingMap`, `QubitProperties` and per-site `InstructionProperties`.
Circuits run through Qiskit Aer and return standard `get_counts()`.

Three noise modes (set via the backend `noise_mode` option):

- **`real`** (default, honest) — the full physics path. For circuits with ≤ 10
  active qubits and no opaque compiled blocks it runs a full Aer
  density-matrix simulation with thermal + depolarizing + readout noise built
  from the defect's real T₁/T₂ and gate times (O(4ⁿ)). Larger or compiled-block
  circuits fall back to the **analytic decoherence limit** computed from the
  *same* real T₂ and gate-time parameters, so a 13-qubit Shor run honestly
  decoheres toward a near-uniform distribution instead of recovering factors.
- **`phenomenological`** (fast analytic approximation) — ideal Aer statevector +
  post-hoc per-site readout bit-flip + T₂ coherence damping blended into the
  distribution. O(n) in qubits; runs in milliseconds regardless of size.
- **`none`** (ideal) — noiseless statevector only.

`aer` is accepted as an alias of `real`. The phenomenological and real modes
share the *same* real T₂/gate-time parameters, so their coherence verdicts
agree — only the granularity of the noise differs.

Algorithms included: **Shor** (QPE period finding), **period finding**,
**Grover search**, **Bell/GHZ**, and **quantum arithmetic** (add / sub / mul /
sqrt / log via Draper QFT adder, RGQFT multiplier and phase estimation).

### Custom Qiskit circuits

Beyond the demo algorithms, the dashboard and API accept a **user-supplied
Qiskit circuit** — either an **OpenQASM 2** string or a **Python snippet** that
builds a `QuantumCircuit` named `qc`. The builder (`qssc.build_user_circuit`)
runs the snippet in a restricted namespace (imports limited to
`qiskit` / `numpy` / `math`, no file/network/process access) and caps the
circuit at 64 qubits.

```bash
# API
curl -X POST localhost:8000/api/circuit/custom \
  -H 'Content-Type: application/json' \
  -d '{"code":"from qiskit import QuantumCircuit\nqc=QuantumCircuit(3,3)\nqc.h(0);qc.cx(0,1);qc.cx(1,2);qc.measure(range(3),range(3))","shots":512}'
```

In the dashboard, select algorithm **custom** to get a code editor with
ready-made Bell / GHZ / QFT / QASM examples.

> Security note: this is intended for a **local lab / demo tool**, not a public
> multi-tenant service. The sandbox is a restricted namespace, not a hardened
> isolation boundary.

## FPGA control stack

A single **Artix-7 XC7A35T** orchestrates an **8×8 on-chip CQD ASE/LED emitter
array** (green ~520–540 nm, electrically pumped via an external higher-voltage
driver), the **on-chip graphene/MoS₂ photodetector array** read through a TIA +
analog MUX into the Artix-7's built-in **XADC** with **1 kHz lock-in**
demodulation, an **ADF4351** microwave PLL, and a host UART link. **There is no
camera and no alignment jig** — the chip integrates its own emitter and detector
arrays and is read electrically (ODMR dip = reduced photocurrent at resonance).
The RTL ([`fpga/rtl/`](fpga/rtl), 8 modules + a self-checking
[testbench](fpga/sim/tb_top_level.v)) is **parse-verified with pyverilog,
functionally simulated with iverilog, and synthesised with yosys
`synth_xilinx` (xc7)** — the testbench drives
`DRIVE_EMITTER 3 4 50` (asserts emitter rows/cols + `OK` reply) and
`READ_DETECTORS` (asserts the full 64-electrode stream `1000 1040 … 3520\n`,
320 bytes, 10/10 checks pass). The yosys synthesis maps the design to the
Series-7 cell library and confirms it fits the XC7A35T-1CPG236C with ~90 % of
the part free (≈2063 LUTs / 9.9 %, 1016 FFs / 2.4 %, 8 DSP48E1 / 8.9 %, 0
inferred latches, 0 blackboxes). All three checks run in CI. (A first iverilog
run caught two real handshake bugs pyverilog could not — an XADC busy race and
a missing consumer-ack — now fixed.) Pin assignments live in
[`fpga/constraints.xdc`](fpga/constraints.xdc); the synthesis flow + report
live in [`fpga/synth/`](fpga/synth) (see
[`hardware/HARDWARE_DESIGN.md`](hardware/HARDWARE_DESIGN.md) §17).

> **Which board + cable?** The RTL is vendor-neutral Verilog except the Xilinx
> XADC hard block, so the **same firmware runs on any Xilinx 7-series FPGA with
> an XADC** — only the `.xdc` pin map changes. Three options are documented in
> [`HARDWARE_DESIGN.md`](hardware/HARDWARE_DESIGN.md) §1A: the custom QSSC PCB
> (XC7A35T-1CPG236C, programmed over JTAG with a Xilinx Platform Cable USB II
> / Digilent JTAG-HS3), the **Digilent Arty A7-35T** (on-board USB-JTAG; remap
> pins for the CSG324 package), and the **Digilent Cmod A7-35T** (same CPG236
> package, limited I/O). Toolchain: free Xilinx **Vivado**. Host link is always a
> USB cable to the board's USB-UART bridge (FT232RL / FT2232H) at 115200-8-N-1.

The host protocol is a small ASCII UART command set (115200 baud, 8-N-1,
`\n`-terminated; all numbers decimal integers — see
[`hardware/HARDWARE_DESIGN.md`](hardware/HARDWARE_DESIGN.md) §5 for the full
specification):

| Command | Action | Reply |
|---|---|---|
| `DRIVE_EMITTER <row> <col> <duration_ms>` | drive CQD emitter pixel (row,col) | `OK` |
| `APPLY_PULSE <qubit> <freq_khz> <amp> <duration_ns> <phase>` | program ADF4351 + gate the MW pulse | `OK` |
| `READ_DETECTOR <site>` | digitise one detector electrode (XADC) | `<code>` |
| `READ_DETECTORS` | sweep all 64 electrodes, stream codes | `<code> <code> …` (64) |
| `CALIBRATE` | drive each emitter + read detectors, build emitter→detector LUT | `OK` |

`freq_khz` is the RF carrier in kHz (e.g. `2870000` for 2.87 GHz NV⁻, `3460000`
for 3.46 GHz hBN V_B⁻); the pulse is split across the ADF4351 and the RF switch:
`freq_khz` → PLL carrier, `phase` (milliradians) → R1 PHASE register, `amp` 0–3
→ R4 RF_PWR output-power index, `duration_ns` → the `mw_gate` RF-switch pin (a
gate timer counts `duration_ns/10` cycles at 100 MHz). The FPGA does not shape
the analog envelope — an external AWG/modulator does. Detector codes are 12-bit
ADC values (0–4095). The Python [`EmitterController`](qssc/backend.py) /
[`DetectorInterface`](qssc/backend.py) /
[`MicrowaveController`](qssc/backend.py) emit exactly this integer form, so the
same control code drives the [`SimulatedFPGA`](qssc/simulator.py) and a real
board.

## Fabrication protocol

`docs/FABRICATION_PROTOCOL.md` is a complete, parameterised lab build in five
sections — CQD emitter array, hBN exfoliation + defect engineering + PMMA,
on-chip 2D photodetectors, waveguides/stacking/thermal management, and safety —
each step with target parameters and expected outcomes. It is grounded in
landmark publications (Gottscholl *Nature Physics* 2020; Hernandez *Nature
Nanotechnol.* 2008; Coleman *Science* 2011; Kianinia *ACS Photonics* 2020) and
is explicit about what is established versus aspirational (e.g. the CQD source
is described as amplified spontaneous emission, **not** a laser).

## Honesty note

This is an **engineering design + simulator**, not a claim of a working
fault-tolerant processor. The software stack runs and is tested; the
fabrication protocol is validated against the literature; the FPGA RTL and PCB
are design artefacts. No result is claimed that the underlying science does not
support.

## Deployment (free hosting + CI/CD)

The whole stack — static dashboard **plus** the full Python simulator API — runs on
free hosting, and the deploy is **gated on tests**: a red CI run never reaches the
deploy workflow.

| Layer | Host | Plan | What runs there |
|-------|------|------|-----------------|
| Dashboard (React/Vite) | GitHub Pages | free | static `frontend/dist` at `<owner>.github.io/QSSC/` |
| API (FastAPI + qiskit Aer) | Render | free web service | `Dockerfile` → `uvicorn api.main:app`; no Redis/Mongo needed (`api/jobs.py` falls back to an in-process executor) |

Memory check: import ~90 MB, a cold Shor-15 run ~100 MB, the heaviest density-matrix
path (≤10 qubits) adds ~16 MB — comfortably inside Render's 512 MB free tier, so the
simulator is **100% functional**, not a stub. The `Dockerfile` is host-agnostic, so
Fly.io, Hugging Face Spaces or Koyeb are drop-in alternatives if Render's free tier
changes; `render.yaml` is provided for one-click Render Blueprint deploys.

**Test-gated auto-deploy.** `.github/workflows/deploy.yml` fires only as a
`workflow_run` *after* the CI workflow has completed **successfully** on `main` (a
failed test/parse/sim run blocks both deploys). It then:

1. **Frontend → GitHub Pages** — builds the dashboard with `VITE_API_BASE` baked in,
   uploads `frontend/dist`, deploys to Pages.
2. **API → Render** — POSTs to the Render deploy hook, which re-deploys the Docker
   service from the latest green `main` (skips gracefully if the hook secret is
   unset, so the frontend still ships on its own).

**One-time repo configuration** (Settings):

- **Pages → Source = "GitHub Actions"** (so the workflow, not the `gh-pages` branch,
  publishes the dashboard).
- **Repository variable `VITE_API_BASE`** = the public API URL, e.g.
  `https://qssc-api.onrender.com`. The dashboard calls this for every `/api/*`
  request (see `frontend/src/api.js`). Without it, the dashboard defaults to a
  same-origin base and API calls 404 on Pages.
- **Repository secret `RENDER_DEPLOY_HOOK_URL`** = Render's deploy-hook URL (Render
  dashboard → the `qssc-api` service → Settings → Deploy Hook). Create the service
  first (from `render.yaml`), then paste its hook here.
- **Public URLs after first deploy:** dashboard
  `https://<owner>.github.io/QSSC/`, API `https://qssc-api.onrender.com`.

> **Security caveat (read before pointing the API at the public internet).** The
> API exposes `POST /api/circuit/custom`, the custom-circuit sandbox. As the
> `/api/circuit/custom` docstring states, this is **a restricted namespace, not a
> hardened isolation boundary** — it is intended as a local lab / demo tool, not a
> public multi-tenant service. Deploying the full API (as requested, "100%
> functional") puts that endpoint on the public internet. For a hardened public
> deployment, disable `/api/circuit/custom` at the reverse proxy or gate it behind
> authentication; the rest of the API is read/simulate-only and is safe to expose.

## Documentation index

### Hardware

- [`hardware/HARDWARE_DESIGN.md`](hardware/HARDWARE_DESIGN.md) — the full
  hardware design document: system overview + block diagram, BOM, power
  sequencing, clocking, the FPGA↔PC UART protocol (§5), ADF4351 loop filter &
  RF path, on-chip CQD emitter driver math, on-chip photodetector readout
  (graphene TIA + analog MUX + Artix-7 XADC, 1 kHz lock-in, 650 nm long-pass
  filter), microwave antenna/CPW, controlled-impedance routing, decoupling, the
  4-layer PCB stackup, critical-net trace geometry, CNC G-code files, the KiCad
  project, and a references list (ADF4351 / XC7A35T+XADC / HMC441 / NV readout).
- [`fpga/rtl/`](fpga/rtl) — 8 Verilog modules: `top_level`, `command_parser`,
  `uart_rx`/`uart_tx`, `spi_master` (ADF4351), `adf4351_ctrl`, `emitter_array`
  (8×8 CQD crossbar), `photodetector_readout` (analog MUX + XADC + 1 kHz lock-in).
- [`fpga/constraints.xdc`](fpga/constraints.xdc) — Artix-7 CPG236 pin
  assignments and timing constraints (100 MHz TCXO, emitter crossbar, detector
  MUX, ADF4351 SPI; no camera domain).
- [`fpga/sim/tb_top_level.v`](fpga/sim/tb_top_level.v) — self-checking testbench.
- [`hardware/gcode/`](hardware/gcode) — CNC G-code substrate toolpaths for the
  microfluidic cylinder array, waveguide/sensor-grid routing, and square cavity
  chip carrier (described in `HARDWARE_DESIGN.md` §14).
- [`hardware/pcb/`](hardware/pcb) — KiCad PCB project (`qssc.kicad_*`,
  regenerated by `tools/gen_qssc_kicad.py`) — a complete **ERC-clean
  schematic (0 errors) + DRC-clean placed 4-layer board** (0 violations,
  0 unconnected; 130×85 mm FR4, full netlist on pads). Copper routing
  (BGA fanout, 50 Ω RF microstrip, USB diff pair, analog guard rings,
  plane pours) still needs one GUI `pcbnew` session — `kicad-cli` cannot
  export DSN / import SES headless (described honestly in
  `HARDWARE_DESIGN.md` §15).

### Fabrication protocol

- The canonical protocols live as **structured Python data** in
  [`qssc/protocol.py`](qssc/protocol.py) — served by the REST API
  (`/api/protocol`) and rendered by the dashboard protocol viewer. The
  standalone `docs/FABRICATION_PROTOCOL*.md` / `.html` / `.pdf` renderings
  were removed in `a3ab46b` so the API and dashboard consume one source
  (decision: [ADR-0007](docs/adr/0007-fabrication-protocol-as-python-data.md)).
- [`docs/fabrication/QSSC_fabrication_protocol_diamond.pdf`](docs/fabrication/QSSC_fabrication_protocol_diamond.pdf)
  — non-canonical reference PDF export of the NV-diamond protocol
  (CQD emitter array, CVD diamond + NV⁻ engineering, on-chip
  graphene/MoS₂ photodetectors, waveguides/stacking/thermal management,
  safety).

### Architecture & decision records

- [`docs/architecture/`](docs/architecture) — Mermaid diagram hub
  complementing the diagrams above: end-to-end system overview (incl.
  host software), the `READ_DETECTORS` signal chain, the FPGA RTL module
  graph, and the power/clock trees.
- [`docs/adr/`](docs/adr) — architecture decision records (ADR-0001 …
  ADR-0008): why the readout pivoted to 4× AD7616, why the die sits in a
  two-board sandwich, why 3× TLC5947 on +12 V_EMIT, why CSG324, and the
  docs-layout decisions (incl. datasheets staying in `hardware/`).
- [`hardware/datasheets/DATASHEETS.md`](hardware/datasheets/DATASHEETS.md)
  — per-component datasheet index; the authoritative component reference
  (kept next to the hardware design doc, see ADR-0008).

### Presentation

- [`presentation/index.html`](presentation/index.html) — self-contained HTML
  deck with software mocks and diagrams.
- [`presentation/QSSC_presentation.pdf`](presentation/QSSC_presentation.pdf) —
  PDF export of the deck.

## License

Apache License 2.0 **with a non-commercial use restriction** — see
[`LICENSE`](LICENSE). Academic research, teaching and personal use are fine;
commercial use requires written permission from the author.
