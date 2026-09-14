# Memoria descriptiva — Programa de ordenador

## Unidad: Sistema de integración continua (CI) y suite de tests — Verificación automatizada QSSC

---

### 1. Datos de la obra

| Campo | Valor |
|---|---|
| **Título** | QSSC — Pipelines de integración continua (GitHub Actions) y suite de verificación automatizada |
| **Tipo de obra** | Programa de ordenador (Art. 96.1 del Texto Refundido de la Ley de Propiedad Intelectual, RDLeg 1/1996) |
| **Titular / Autor** | _Vicent Nos Ripolles y Pablo Lopez_ |
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
> desarrollo, no un precio de mercado. El script reproducible
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

## Algoritmos implementados en esta unidad

La suite de tests verifica los algoritmos de las otras cuatro unidades; no
implementa algoritmos propios, pero ejercita los siguientes de forma ejecutable y
auditable (referencias a los artículos originales en la bibliografía):

- **Física del dipolo 1/r³ y canales de error** [12,13] — `tests/test_circuit_physics_audit.py`.
- **Shor / QFT / Grover** [1,3,2] — `tests/test_core.py`, `test_algorithms_platforms.py`.
- **Magnetometría vectorial + gradiometría tensorial** [8,10,11] — `tests/test_magnetometer.py`, `test_superres_bayesian_tensor.py`.
- **Descomposición ODMR (CLEAN/MP)** [23,24] y **super-res OMP** [25] — `tests/test_array_calibration.py`, `test_superres_bayesian_tensor.py`.
- **Lectura Bayesiana + Bonferroni** [27,28] — `tests/test_superres_bayesian_tensor.py`.
- **Centroiding por charge-sharing** [26] — `tests/test_detector_centroid.py`.
- **Denoise por primas-coprimas** (propio) — `tests/test_prime_readout.py`.
- **Pila anti-ruido 300 K** (propio) — `tests/test_noise_filters.py`, `test_noise_summary.py`.
- **Memoria nuclear ¹³C** [16,17,18] — `tests/test_nuclear_memory.py`.
- **Ancho de registro por gradiente** (propio) — `tests/test_field_gradient_register.py`.
- **QRNG von-Neumann** [30] — `tests/test_qrng.py`.
- **Modo píxel registro/ensamble** (propio) — `tests/test_pixel_mode.py`.
- **Computación paralela 64-píxel** (propio) — `tests/test_parallel_computing.py`.

---

## Bibliografía

Referencias a los artículos originales de los algoritmos publicados implementados
en el QSSC, en formato académico con DOI. Los algoritmos propios del QSSC se
describen en la sección *Algoritmos implementados* de cada unidad; aquí se cita
sólo el método publicado en que se basan. La guía canónica de instalación, uso y
despliegue es el fichero `README.md` del repositorio (sección *Quick start*).

### Algoritmos cuánticos

[1] Shor, P. W. «Algorithms for quantum computation: discrete logarithms and
factoring». *Proc. 35th FOCS*, 124–134 (1994). arXiv:quant-ph/9501002.
[2] Grover, L. K. «A fast quantum mechanical algorithm for database search».
*Proc. 28th STOC*, 212–219 (1996); *Phys. Rev. Lett.* **79**, 325–328 (1997).
doi:10.1103/PhysRevLett.79.325.
[3] Coppersmith, D. «An approximate Fourier transform useful in quantum
factoring». IBM Research Report RC19642 (1994); arXiv:quant-ph/0201067.
[4] Draper, T. G. «Addition on a quantum computer». arXiv:quant-ph/0008033 (2000).
[5] Qiskit Development Team. «Qiskit: an open-source framework for working with
quantum computers» (multiplicador RGQFT). Zenodo (2024).
doi:10.5281/zenodo.2562110.
[6] Amico, M.; Saleem, Z. H.; Kumph, M. «Experimental study of Shor's quantum
factoring algorithm using the IBM backend». *Phys. Rev. A* **100**, 012305 (2019).
doi:10.1103/PhysRevA.100.012305.

### Centro NV y sensórica cuántica

[7] Doherty, M. W. *et al.* «The nitrogen-vacancy colour centre in diamond».
*Phys. Rep.* **528**, 1–45 (2013). doi:10.1016/j.physrep.2013.02.001.
[8] Balasubramanian, G. *et al.* «Nanoscale imaging magnetometry with
nitrogen-vacancy centers in diamond». *Nature* **455**, 648–651 (2008).
doi:10.1038/nature07277.
[9] Jarmola, A. *et al.* «Temperature- and magnetic-field-dependent longitudinal
spin relaxation in nitrogen-vacancy ensembles in diamond». *Phys. Rev. Lett.*
**108**, 197601 (2012). doi:10.1103/PhysRevLett.108.197601.
[10] Degen, C. L.; Reinhard, F.; Cappellaro, P. «Quantum sensing».
*Rev. Mod. Phys.* **89**, 035002 (2017). doi:10.1103/RevModPhys.89.035002.
[11] Rondin, L. *et al.* «Magnetometry with nitrogen-vacancy defects in diamond».
*Rep. Prog. Phys.* **77**, 056503 (2014). doi:10.1088/0034-4885/77/5/056503.

### Gate spin de 2 qubits por dipolo (300 K)

[12] Neumann, P. *et al.* «Multipartite entanglement among single spins in
diamond». *Science* **320**, 1326–1329 (2008). doi:10.1126/science.1157233.
[13] Dolde, F. *et al.* «Room-temperature entanglement between single spins in
diamond». *Nat. Phys.* **9**, 139–143 (2013). doi:10.1038/nphys2545.
[14] Joas, J. *et al.* «Room-temperature entanglement between nitrogen-vacancy
centers in diamond». *Phys. Rev. X* **15**, 021069 (2025).
[15] Xie, T. *et al.* «High-fidelity CNOT gate between nitrogen-vacancy centers
in diamond». *Phys. Rev. Lett.* **130**, 030601 (2023).
doi:10.1103/PhysRevLett.130.030601.

### Memoria nuclear ¹³C

[16] Taminiau, T. H. *et al.* «Universal control and error correction in a
solid-state spin register». *Nature* **486**, 91–94 (2014).
doi:10.1038/nature11185.
[17] Cramer, J. *et al.* «Repeated quantum error correction on a continuously
encoded qubit by real-time feedback». *Nat. Commun.* **7**, 11526 (2016).
doi:10.1038/ncomms11526.
[18] Reimer, M. E. *et al.* «Single ¹³C nuclear spin control and readout in
diamond». *Phys. Rev. B* **93**, 024104 (2016).

### Secuencias de pulsos

[19] Ramsey, N. F. «A molecular beam resonance method with separated oscillating
fields». *Phys. Rev.* **78**, 695–699 (1950). doi:10.1103/PhysRev.78.695.
[20] Hahn, E. L. «Spin echoes». *Phys. Rev.* **80**, 580–594 (1950).
doi:10.1103/PhysRev.80.580.
[21] Carr, H. Y.; Purcell, E. M. «Effects of diffusion on free precession in
nuclear magnetic resonance experiments». *Phys. Rev.* **94**, 630–638 (1954).
doi:10.1103/PhysRev.94.630.
[22] Meiboom, S.; Gill, D. «Modified spin-echo method for measuring nuclear
relaxation times». *Rev. Sci. Instrum.* **29**, 688–691 (1958).
doi:10.1063/1.1716296.

### Descomposición espectral y super-resolución

[23] Högbom, J. A. «Aperture synthesis with a non-regular distribution of
interferometer baselines». *Astron. Astrophys. Suppl.* **15**, 417–426 (1974).
[algoritmo CLEAN.]
[24] Mallat, S. G.; Zhang, Z. «Matching pursuits with time-frequency
dictionaries». *IEEE Trans. Signal Process.* **41**, 3397–3415 (1993).
doi:10.1109/78.258082.
[25] Tropp, J. A.; Gilbert, A. C. «Signal recovery from random measurements via
orthogonal matching pursuit». *IEEE Trans. Inf. Theory* **53**, 4655–4666 (2007).
doi:10.1109/TIT.2007.909108.
[26] Lampton, M.; Carlson, R. F. «Inexpensive, reliable position encoding for
microchannel-plate detectors» (sensado de posición por división de carga).
*Rev. Sci. Instrum.* **50**, 1093 (1979). doi:10.1063/1.1135729.

### Lectura Bayesiana y detección cuántica

[27] Helstrom, C. W. *Quantum Detection and Estimation Theory*. Academic Press,
Nueva York (1976). ISBN 978-0-12-340050-5.
[28] Bonferroni, C. E. «Teoria statistica delle classi e calcolo delle
probabilità». *Pubblicazioni del R. Istituto Superiore di Scienze Economiche e
Commerciali di Firenze* **8**, 3–62 (1936). [corrección de comparaciones
múltiples.]

### QKD y generador cuántico de aleatorios

[29] Bennett, C. H.; Brassard, G. «Quantum cryptography: public key distribution
and coin tossing». *Proc. IEEE Int. Conf. on Computers, Systems and Signal
Processing*, 175–179 (Bangalore, 1984). [protocolo BB84.]
[30] von Neumann, J. «Various techniques used in connection with random digits».
*Appl. Math. Ser.* **12**, 36–38 (1951). [extractor de sesgo.]

### Defectos spin en hBN y SiC

[31] Gottscholl, V. A. *et al.* «Spin defects in hBN as promising sensors for
magnetic fields». *Nat. Mater.* **19**, 540–545 (2020).
doi:10.1038/s41563-020-0619-6.
[32] Kianinia, M. *et al.* «Generation of spin defects in hexagonal boron
nitride». *ACS Photonics* **7**, 2147–2152 (2020).
doi:10.1021/acsphotonics.0c00661.
[33] Carter, S. D. *et al.* «Spin coherence in SiC». *Phys. Rev. B* **92**,
161202 (2015). doi:10.1103/PhysRevB.92.161202.
[34] Kraus, H. *et al.* «Quantum clock in SiC». *Nat. Phys.* **10**, 157–162
(2014). doi:10.1038/nphys2826.
[35] Lekavicius, I. *et al.* «Spin coherence in SiC». *PRX Quantum* **3**,
010343 (2022).

---

_Fecha de la memoria: 2026-09-14. Esta memoria descriptiva acompaña al depósito
del código fuente en el Repositorio Oficial del Proyecto QSSC y se destina a su
inscripción en el Registro de la Propiedad Intelectual._
