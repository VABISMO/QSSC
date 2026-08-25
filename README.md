# QSSC — Quantum Solid State Chip (room temperature)

[![CI](https://github.com/VABISMO/QSSC/actions/workflows/ci.yml/badge.svg)](https://github.com/VABISMO/QSSC/actions/workflows/ci.yml)
[![License: Apache-2.0 NC](https://img.shields.io/badge/license-Apache--2.0%20NC-blue.svg)](LICENSE)
[![Python 3.10–3.12](https://img.shields.io/badge/python-3.10–3.12-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-59%20passed-brightgreen.svg)](#testing)

A room-temperature **solid-state spin-defect** quantum platform: a Qiskit
backend, an FPGA control stack with PCB design, a validated chip-fabrication
protocol, and a web dashboard. This project remasters and merges two earlier
projects by the same author — an hBN spin-defect *chip design* and a Qiskit
NV-diamond *simulator* — into one coherent, renamed deliverable.

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
| **Tests** | `tests/` | 59 tests (defects, simulator, circuits, backend, protocol, crypto) |

## Quick start

```bash
cd qssc
python -m venv .venv && . .venv/bin/activate
pip install -e ".[api,dev]"

# run the test suite
python -m pytest                       # 59 passed

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
[testbench](fpga/sim/tb_top_level.v)) is parse-verified with pyverilog. Pin
assignments live in [`fpga/constraints.xdc`](fpga/constraints.xdc).

The host protocol is a small ASCII UART command set (115200 baud, 8-N-1,
`\n`-terminated; all numbers decimal integers — see
[`hardware/HARDWARE_DESIGN.md`](hardware/HARDWARE_DESIGN.md) §5 for the full
specification):

| Command | Action | Reply |
|---|---|---|
| `DRIVE_EMITTER <row> <col> <duration_ms>` | drive CQD emitter pixel (row,col) | `OK` |
| `APPLY_PULSE <qubit> <freq_khz> <amp> <duration_ns> <phase>` | program ADF4351 to `freq_khz` | `OK` |
| `READ_DETECTOR <site>` | digitise one detector electrode (XADC) | `<code>` |
| `READ_DETECTORS` | sweep all 64 electrodes, stream codes | `<code> <code> …` (64) |
| `CALIBRATE` | drive each emitter + read detectors, build emitter→detector LUT | `OK` |

`freq_khz` is the RF output in kHz (e.g. `2870000` for 2.87 GHz NV⁻, `3460000`
for 3.46 GHz hBN V_B⁻); `amp` 0–3 maps to the ADF4351 output power index;
`duration_ns`/`phase` (milliradians) are parsed and reserved for a future
RF-gate/phase-register extension. Detector codes are 12-bit ADC values
(0–4095). The Python [`EmitterController`](qssc/backend.py) /
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
- [`hardware/gcode/`](hardware/gcode) — CNC G-code for the microfluidic
  cylinder array, fluorescence-sensor routing, microlens array, and square
  cavity chip (described in `HARDWARE_DESIGN.md` §14).
- [`hardware/pcb/`](hardware/pcb) — KiCad PCB project (starting layout;
  described in `HARDWARE_DESIGN.md` §15).

### Fabrication protocol

- [`docs/FABRICATION_PROTOCOL_Diamond.md`](docs/FABRICATION_PROTOCOL_Diamond.md)
  — 5-section lab fabrication protocol for the **canonical NV-diamond** compute
  chip (CQD emitter array, CVD diamond + NV⁻ engineering, on-chip graphene/MoS₂
  photodetectors, waveguides/stacking/thermal management, safety), validated
  against landmark publications.
- [`docs/FABRICATION_PROTOCOL.md`](docs/FABRICATION_PROTOCOL.md) — hBN V_B⁻
  sensor-variant protocol (CQD emitter array, hBN exfoliation + defect
  engineering + PMMA, on-chip 2D photodetectors, …).
- [`docs/FABRICATION_PROTOCOL_SiC.md`](docs/FABRICATION_PROTOCOL_SiC.md) —
  SiC V_Si variant.
- [`docs/protocol.html`](docs/protocol.html) /
  [`protocol_diamond.html`](docs/protocol_diamond.html) /
  [`protocol_sic.html`](docs/protocol_sic.html) — branded, print-ready HTML
  renders (regenerate with `python3 tools/build_protocol_html.py`).
- [`docs/QSSC_fabrication_protocol.pdf`](docs/QSSC_fabrication_protocol.pdf) /
  [`…_diamond.pdf`](docs/QSSC_fabrication_protocol_diamond.pdf) /
  [`…_sic.pdf`](docs/QSSC_fabrication_protocol_sic.pdf) — PDF exports.

### Presentation

- [`presentation/index.html`](presentation/index.html) — self-contained HTML
  deck with software mocks and diagrams.
- [`presentation/QSSC_presentation.pdf`](presentation/QSSC_presentation.pdf) —
  PDF export of the deck.

## License

Apache License 2.0 **with a non-commercial use restriction** — see
[`LICENSE`](LICENSE). Academic research, teaching and personal use are fine;
commercial use requires written permission from the author.