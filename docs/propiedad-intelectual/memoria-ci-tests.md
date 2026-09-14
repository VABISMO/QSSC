# Memoria descriptiva — Programa de ordenador

## Unidad: Sistema de integración continua (CI) y suite de tests — Verificación automatizada QSSC

---

### 1. Datos de la obra

| Campo | Valor |
|---|---|
| **Título** | QSSC — Pipelines de integración continua (GitHub Actions) y suite de verificación automatizada |
| **Tipo de obra** | Programa de ordenador (Art. 96.1 del Texto Refundido de la Ley de Propiedad Intelectual, RDLeg 1/1996) |
| **Titular / Autor** | _[A completar por el titular: nombre/apellidos o razón social, NIF/CIF, domicilio]_ |
| **Fecha de creación (inicio)** | 2026-08-07 |
| **Fecha de finalización (versión depositada)** | 2026-09-14 |
| **Lugar de elaboración** | España |
| **Lenguaje de programación** | Python 3.12 (tests, `pytest`) + YAML (workflows de GitHub Actions) |
| **Plataforma objetivo** | GitHub Actions (`ubuntu-latest`); `pytest` + `iverilog` + `kicad-cli` + `ruff` |
| **Tamaño** | 5.209 líneas físicas de código fuente (SLOC) — contaje nativo SLOCCount |
| **Valoración de esfuerzo (COCOMO básico, SLOCCount)** | 1,13 persona-años (13,6 persona-meses); coste estimado de desarrollo 152.839 USD (salario medio 56.286 USD/año, factor 2,40) |

> Métricas generadas con SLOCCount 2.26 (David A. Wheeler, GPL), contaje nativo
> sobre los 33 ficheros Python de tests (4.843 SLOC) + 4 workflows YAML (366
> SLOC, renombrados `.yml`→`.py` para el contador de `#`, misma técnica ya usada
> para `.xdc`). Verificación cruzada con cloc 2.11: 5.181 líneas de código
> (tests 4.815 + workflows 366), diferencia −28 (−0,5 %). Fórmula COCOMO básica
> `PM = 2,4 · KSLOC^1,05`. La valoración es una estimación de coste de
> re-desarrollo, no un precio de mercado. El script reproducible
> `docs/propiedad-intelectual/sloc_count.py` contiene el conteo por fichero y la
> comparación SLOCCount/cloc de las cinco áreas.

---

### 2. Descripción general

Esta unidad es el sistema de calidad que verifica automáticamente las otras
cuatro en cada push y pull request: una suite de tests `pytest` que ejercita la
física, la API, el modo hardware real, la readout avanzada, la sensórica y la
PCB, y cuatro pipelines de GitHub Actions que orquestan tests, lint, simulación
RTL, DRC de PCB, build y despliegue.

El sistema garantiza que ningún cambio llega a `main` sin pasar los tests de
software/física, el lint `ruff`, la simulación funcional `iverilog` del RTL, el
DRC `kicad-cli` de ambas placas y el gate verde-de-CI antes del despliegue. Es
la capa que hace reproducible y auditable el depósito completo.

---

### 3. Arquitectura y módulos

#### 3.1 Pipelines de GitHub Actions (4 workflows, 9 jobs)

- **`.github/workflows/ci.yml`** (125 SLOC) — pipeline principal, 3 jobs en
  matriz de versiones de Python:
  - *Tests* — `pytest --cov=qssc --cov=api` con cobertura, smoke test del CLI
    (`qssc info`, `qssc shor 15`) y subida del informe de cobertura como
    artefacto.
  - *Lint* — `ruff check --select=E9,F63,F7,F82,F401,F841`.
  - *FPGA RTL* — parse pyverilog de todos los módulos, consistencia
    XDC↔top_level (`tools/check_xdc_ports.py`) y simulación funcional
    `iverilog -g2012 -DFAST_SIM` del testbench auto-verificable.
- **`.github/workflows/pcb.yml`** (59 SLOC) — verificación de PCB, 1 job:
  instala KiCad 8.0 del PPA oficial (`kicad-cli` + binding pcbnew),
  sanity-check del binding, y ejecuta `pytest tests/test_pcb_*.py` (DRC real +
  conectividad + pin-audit + stackup de ambas placas) + `ruff` sobre los tools
  de PCB.
- **`.github/workflows/deploy.yml`** (109 SLOC) — despliegue, 3 jobs:
  - *Require green CI on main* — verifica que la última ejecución de CI en main
    esté verde antes de desplegar.
  - *Frontend → GitHub Pages* — build del dashboard con Vite y despliegue a
    Pages.
  - *API → Render* — dispara el deploy hook (test-gated).
- **`.github/workflows/release.yml`** (73 SLOC) — release, 2 jobs:
  - *Build artifacts* — sdist+wheel Python + build del dashboard frontend.
  - *Publish GitHub Release* — crea el release con los artefactos.

#### 3.2 Suite de tests pytest (33 ficheros, 4.843 SLOC, 354 funciones de test)

- **Núcleo y algoritmos** (4 ficheros) — `test_core.py` (592),
  `test_algorithms_platforms.py`, `test_circuit_histogram.py`,
  `test_circuit_physics_audit.py` — backend Qiskit, Bell/Shor/Grover/QFT,
  auditoría física (dipolo-dipolo, canales de error, corrección Qiskit).
- **API y vía hardware real** (2 ficheros) — `test_api.py` (294),
  `test_hardware_real.py` (294) — endpoints REST con TestClient sobre la app
  real; detección de FPGA y degradación veraz `no-fpga-detected`.
- **Calibración y readout avanzado** (7 ficheros) —
  `test_array_calibration.py`, `test_superres_bayesian_tensor.py` (266),
  `test_noise_filters.py` (222), `test_pixel_mode.py`,
  `test_prime_readout.py`, `test_detector_centroid.py`,
  `test_noise_summary.py` — ancho de registro medido, super-res OMP, lectura
  Bayesiana, centroiding, denoise por primas, pila anti-ruido, modos
  registro/ensamble.
- **Sensórica y computación paralela** (2 ficheros) —
  `test_parallel_computing.py` (287), `test_magnetometer.py` —
  magnetómetro vectorial, gradiometría tensorial, cómputo 64-píxel.
- **Memoria nuclear + QRNG + FPGA sim** (4 ficheros) —
  `test_nuclear_memory.py`, `test_field_gradient_register.py`,
  `test_qrng.py` (135), `test_fpga_sim.py` — memoria ¹³C, gradiente, entropía
  cuántica, emulación de la FPGA.
- **PCB** (10 ficheros) — `test_pcb_connectivity.py` (235),
  `test_pcb_component_audit.py`, `test_pcb_die_fpga_audit.py`,
  `test_pcb_pin_audit.py`, `test_pcb_head.py`, `test_pcb_route.py`,
  `test_pcb_route_signals.py`, `test_pcb_route_full.py`, `test_pcb_drc.py`,
  `test_pcb_stackup.py` — DRC real, conectividad, pin-audit por datasheet,
  stackup de ambas placas.
- **Fabricación gcode/SVG** (4 ficheros) — `test_gcode.py` (267),
  `test_render_p0_svg.py`, `test_render_p1_svg.py`,
  `test_spincoater_gcode.py` — protocolos de fabricación P0/P1 (CNC 3018),
  render SVG verificado con `xmllint`.

---

### 4. Tecnologías y lenguajes

| Lenguaje | SLOC | Porcentaje |
|---|---|---|
| Python 3.12 (tests) | 4.843 | 93,0 % |
| YAML (workflows) | 366 | 7,0 % |
| **Total** | **5.209** | **100 %** |

Herramientas: `pytest` + `pytest-cov` (tests/cobertura), `ruff` (lint),
`iverilog` (simulación RTL), `pyverilog` (parse RTL), `kicad-cli` + binding
pcbnew (DRC PCB), GitHub Actions (orquestación).

---

### 5. Funcionalidades destacadas — gates de calidad automatizados

1. **Gate de tests con cobertura** (`ci.yml · Tests`): `pytest --cov=qssc
   --cov=api` en matriz de versiones de Python; smoke test del CLI; subida del
   informe de cobertura como artefacto.
2. **Lint estricto anti-errores en tiempo de ejecución** (`ci.yml · Lint`):
   `ruff check --select=E9,F63,F7,F82,F401,F841` — captura errores de sintaxis,
   imports no usados y variables no usadas antes de llegar a main.
3. **Verificación RTL: parse + consistencia + simulación** (`ci.yml · FPGA
   RTL`): pyverilog parsea todos los módulos, `check_xdc_ports.py` verifica la
   consistencia entre el XDC y `top_level.v`, e `iverilog -g2012 -DFAST_SIM`
   ejecuta el testbench auto-verificable (18 escenarios de verbos).
4. **DRC de PCB real en CI** (`pcb.yml`): instala KiCad 8.0 del PPA oficial y
   ejecuta `pytest tests/test_pcb_*.py` — DRC 0/0/0, conectividad, pin-audit
   por datasheet y stackup de ambas placas, sobre `kicad-cli` real, no un mock.
5. **Gate verde-de-CI antes de desplegar** (`deploy.yml`): el despliegue
   (frontend → GitHub Pages, API → Render) sólo se dispara si la última
   ejecución de CI en main está verde.
6. **Build + release de artefactos** (`release.yml`): construye el sdist+wheel
   Python y el dashboard frontend, y publica un GitHub Release con los
   artefactos.
7. **Suite de tests de física auditoriable** (`tests/`): 354 funciones de test
   en 33 ficheros que ejercitan el modelo físico (dipolo 1/r³, canales de error,
   corrección Qiskit), la readout avanzada (super-res, Bayesiano, primas,
   anti-ruido), la sensórica tensorial y la PCB — la base empírica de las
   afirmaciones de física del depósito.

---

### 6. Originalidad y características distintivas

- CI que verifica **las cuatro capas** del instrumento (backend, RTL, PCB,
  frontend) en cada push, no sólo los tests de software: simulación RTL + DRC
  de PCB real + lint + despliegue gateado.
- **DRC de PCB real en CI** con `kicad-cli` + binding pcbnew instalado del PPA
  oficial — el mismo DRC que un ingeniero abriría en la GUI, ejecutado headless
  en cada cambio.
- **Suite de tests de física** que audita las afirmaciones del simulador
  (dipolo, canales de error, corrección Qiskit) — los claims de física están
  respaldados por tests ejecutables, no por afirmaciones.
- Despliegue **gateado por CI verde**: ningún commit llega a producción sin
  pasar todos los gates.

---

### 7. Estado de verificación

- Suite `pytest` en verde (software/física): 354 funciones de test en 33
  ficheros, salida 0 fallos.
- Simulación RTL `iverilog`: 18/18 escenarios del testbench pasan.
- DRC PCB `kicad-cli`: ambas placas 0/0/0.
- Lint `ruff` limpio (reglas CI E9/F63/F7/F82/F401/F841) antes de cada push.
- Cobertura de código recogida en cada ejecución de CI (`--cov-report=xml`).

---

### 8. Entorno de ejecución

- **Tests locales**: `python -m pytest` (suite software/física); los tests PCB
  requieren `kicad-cli`.
- **CI**: GitHub Actions sobre `ubuntu-latest`; KiCad 8.0 del PPA
  `kicad/kicad-8.0-releases`.
- **Instrucciones de instalación y uso**: el fichero `README.md` del
  repositorio (sección _Quick start_) contiene la guía canónica de instalación
  del entorno (`python -m venv .venv && pip install -e ".[api,dev]"`),
  ejecución de la suite (`python -m pytest`), arranque de la API
  (`uvicorn api.main:app --port 8000`) y del dashboard (`npm install && npm run
  dev`).

---

### 9. Lista de ficheros fuente depositados

```
.github/workflows/ci.yml
.github/workflows/deploy.yml
.github/workflows/pcb.yml
.github/workflows/release.yml
tests/test_algorithms_platforms.py
tests/test_api.py
tests/test_array_calibration.py
tests/test_circuit_histogram.py
tests/test_circuit_physics_audit.py
tests/test_core.py
tests/test_detector_centroid.py
tests/test_field_gradient_register.py
tests/test_fpga_sim.py
tests/test_gcode.py
tests/test_hardware_real.py
tests/test_magnetometer.py
tests/test_noise_filters.py
tests/test_noise_summary.py
tests/test_nuclear_memory.py
tests/test_parallel_computing.py
tests/test_pcb_component_audit.py
tests/test_pcb_connectivity.py
tests/test_pcb_die_fpga_audit.py
tests/test_pcb_drc.py
tests/test_pcb_head.py
tests/test_pcb_pin_audit.py
tests/test_pcb_route.py
tests/test_pcb_route_full.py
tests/test_pcb_route_signals.py
tests/test_pcb_stackup.py
tests/test_pixel_mode.py
tests/test_prime_readout.py
tests/test_qrng.py
tests/test_render_p0_svg.py
tests/test_render_p1_svg.py
tests/test_spincoater_gcode.py
tests/test_superres_bayesian_tensor.py
```

---

_Fecha de la memoria: 2026-09-14. Esta memoria descriptiva acompaña al depósito
del código fuente en el Repositorio Oficial del Proyecto QSSC y se destina a su
inscripción en el Registro de la Propiedad Intelectual._