# Memoria descriptiva — Programa de ordenador

## Unidad: Backend — API, simulador cuántico y librería de física QSSC

---

### 1. Datos de la obra

| Campo | Valor |
|---|---|
| **Título** | QSSC — API REST, simulador cuántico realista y librería de física para plataforma de 64 píxeles NV |
| **Tipo de obra** | Programa de ordenador (Art. 96.1 del Texto Refundido de la Ley de Propiedad Intelectual, RDLeg 1/1996) |
| **Titular / Autor** | _Vicent Nos Ripolles y Pablo Lopez_ |
| **Fecha de creación (inicio)** | 2026-08-07 |
| **Fecha de finalización (versión depositada)** | 2026-09-14 |
| **Lugar de elaboración** | España |
| **Lenguaje de programación** | Python 3.12 |
| **Plataforma objetivo** | Servidor Linux; API FastAPI (uvicorn); worker RQ para tareas asíncronas |
| **Tamaño** | 7.825 líneas físicas de código fuente (SLOC) — contaje nativo SLOCCount |
| **Valoración de esfuerzo (COCOMO básico, SLOCCount)** | 1,73 persona-años (20,8 persona-meses); coste estimado de desarrollo 234.315 USD (salario medio 56.286 USD/año, factor 2,40) |

> Métricas generadas con SLOCCount 2.26 (David A. Wheeler, GPL), contaje nativo
> sobre ficheros Python (SLOCCount sí reconoce `.py`). Fórmula COCOMO básica
> `PM = 2,4 · KSLOC^1,05`. La valoración es una estimación de coste de
> desarrollo, no un precio de mercado. El script reproducible
> `docs/propiedad-intelectual/sloc_count.py` contiene el conteo por fichero
> y la valoración de las tres áreas.

---

### 2. Descripción general

El backend es la capa de software que (i) implementa un **simulador cuántico
realista** de la plataforma QSSC, (ii) expone una **API REST** para que el
frontend y clientes externos ejecuten circuitos, calibren la matriz y operen la
sensórica, y (iii) contiene la **librería de física** (modelos de defecto,
descomposición ODMR, magnetometría, gradiometría tensorial, readout super-res
y Bayesiano, filtros anti-ruido) que comparten el simulador y la vía de
hardware real.

Se estructura en dos paquetes: `qssc/` (librería de física + simulador +
backend cuántico Qiskit) y `api/` (servicio FastAPI + runner + cola de tareas).
La vía de hardware real conecta con la FPGA por el mismo firmware depositado en
la unidad FPGA, de modo que el backend opera sobre medidas reales cuando la
placa está presente y sobre el simulador cuando no lo está, sin enmascarar una
vía por otra.

---

### 3. Arquitectura y módulos

#### 3.1 `qssc/` — librería de física + simulador (5.100 SLOC)

- **`backend.py`** (1.753 SLOC) — `QSSCBackend`: backend de Qiskit para la
  plataforma. Modela los 64 píxeles como procesadores independientes a 300 K,
  acoplamiento dipolo-dipolo dentro del píxel (1/r³, `dipole.py`), detuning por
  qubit, readout asimétrico, y modos `pixel_mode` (registro/ensamble). Soporta
  `run_array` (un circuito por píxel, agregación 64× los shots) con saltado de
  píxeles cuyo ancho calibrado es menor que el circuito.
- **`simulator.py`** (843 SLOC) — `SimulatedFPGA`: emulación fiel de la FPGA
  (barrido ODMR con ruido de lectura, detectores, lock-in, secuencias de
  pulsos). `auto_align` coloca los NV en la rejilla del chip o en el registro
  dentro de un píxel, opcionalmente sobre una rejilla de frecuencias ODMR
  **medidas** (no una rejilla sintética fija).
- **`circuits.py`** (531 SLOC) — construcción de circuitos: Bell, Shor (con
  factorización de potencias primas vía `perfect_power`, QFT, aritmética
  eficiente), Grover, QFT, caminos personalizados, y postproceso con corte por
  anchura de registro.
- **`array_calibration.py`** (529 SLOC) — calibración de la matriz de 64
  píxeles: `calibrate_array` (vía sim) y `calibrate_array_from_codes` (vía real,
  desde la matriz de códigos ADC medidos). Cuenta las líneas ODMR resueltas por
  píxel = anchura de registro; integra sub-píxel (centroiding), integración
  N-shot, super-resolución y Bayesiano. `gradient_dac_code_from_mhz` es el
  inverso del driver del DAC de gradiente (MCP4921, 4095 → 2,23 MHz/µm).
  `cap_to_resolvable` separa el tope del registro-por-gradiente (Inv. 5) del
  doblete Zeeman del campo ambiente que lee la gradiometría tensorial.
- **`protocol.py`** (439 SLOC) — protocolo experimental (etapas de calibración:
  ODMR, linewidth, T2*/Hahn, tabla de frecuencias por pin).
- **`magnetometry.py`** (364 SLOC) — magnetómetro vectorial de 4 ejes ⟨111⟩:
  `b_vector_from_odmr_lines` reconstruye el vector B a partir de los dobletes
  ODMR (≥3 splits distintos), `b_vector_map_from_array_calibration` construye el
  mapa (8×8×3) desde la calibración medida, `measure_pixel` con ruido de shot
  linewidth-limitado.
- **`tensor_gradiometry.py`** (344 SLOC) — gradiometría tensorial: Jacobiano
  por píxel, localización de dipolo (modo diferencial por splits signo-ambigüo
  o por vectores), `tensor_gradient_sweep`.
- **`defects.py`** (298 SLOC) — perfiles de defectos (NV, SiC, hBN) con
  constantes físicas: splitting a campo cero (D), ratio giromagnético, linewidth
  ODMR, T1/T2 (cuando aplica), siempre anclados a datos del proveedor.
- **`sensors.py`** (269 SLOC) — ejes NV tetraédricos ⟨111⟩, sensórica
  auxiliar (compás, giro, ferro, nav, thermal, current).
- **`prime_readout.py`** (231 SLOC) — denoiser de readout por primas-coprimas.
- **`odmr_decompose.py`** (219 SLOC) — descomposición espectral ODMR (CLEAN /
  matching pursuit): resuelve líneas individuales, SNR mínimo, separación mínima
  > linewidth. Es el contador de qubits por píxel.
- **`noise_filters.py`** (212 SLOC) — pila de filtros anti-ruido clásicos a
  300 K (SNR-protectiva, canales lentos no 1/√N).
- **`cli.py`** (206 SLOC) — interfaz de línea de comandos.
- **`detector_centroid.py`** (197 SLOC) — centroiding sub-píxel por
  charge-sharing (imagen detector 64→64·S, metadatos, no incrementa qubits).
- **`crypto.py`** (154 SLOC) — primitivas criptográficas (QKD).
- **`superres_odmr.py`** (140 SLOC) — super-resolución ODMR por sparse OMP
  (deconvolución de líneas solapadas).
- **`bayesian_readout.py`** (104 SLOC) — lectura Bayesiana cuántico-limitada
  (intervalo creíble unilateral con corrección Bonferroni).
- **`qkd.py`** (103 SLOC) — distribución cuántica de claves.
- **`dipole.py`** (68 SLOC) — acoplamiento dipolo-dipole 1/r³ (fundamento del
  gate de 2 qubits spin a 300 K).
- **`nuclear.py`** (64 SLOC) — memoria nuclear (NV + ¹³C) — vía simulador.
- **`__init__.py`** (104 SLOC) — exports públicos del paquete.

#### 3.2 `api/` — servicio REST + runner (2.725 SLOC)

- **`main.py`** (1.725 SLOC) — aplicación FastAPI: endpoints de circuito
  (`/api/circuit/run`, `/run-custom`, `/run-parallel`), calibración
  (`/api/calibration/run`, `/api/circuit/calibrate-array`), sensórica
  (`/api/sensor/magneto`, `/gyro`, `/compass`, `/tensor`, etc.), modos
  (`/api/modes`), perfiles (`/api/profiles`), y control FPGA (`/api/fpga/*`).
  Cada endpoint conmuta fuente `sim`/`real`; la vía real detecta la placa y
  devuelve `no-fpga-detected` cuando no está presente (sin relabel silencioso).
  El endpoint tensorial ejecuta la cadena real completa: `RUN_ALL_ODMR` →
  descomposición por píxel → B-vector 4 ejes → gradiente tensorial + ajuste de
  dipolo sobre el mapa medido.
- **`runner.py`** (1.062 SLOC) — lógica de ejecución: `run_circuit`,
  `run_parallel` (Shor coprimo por píxeles, muestreo paralelo), `run_calibrate`
  y `run_calibrate_array` (vía real: envía `SET_GRADIENT` antes del barrido,
  acumula N shots, descompone, cachea la calibración). Cierra el lazo
  calibración → circuito alimentando las frecuencias ODMR medidas como rejilla
  del registro.
- **`jobs.py`** (300 SLOC) — cola RQ para tareas asíncronas (calibración y
  ejecución paralela no bloqueante), con modo `?sync=true` en proceso.

---

### 4. Tecnologías y lenguajes

| Lenguaje | SLOC | Porcentaje |
|---|---|---|
| Python | 7.825 | 100 % |
| **Total** | **7.825** | **100 %** |

Stack: Python 3.12, FastAPI + uvicorn, Qiskit (backend cuántico con Aer),
NumPy/SciPy (física y descomposición espectral), RQ + Redis (tareas
asíncronas), Pydantic (modelos de petición), pytest (tests).

---

### 5. Funcionalidades destacadas — metodologías implementadas

1. **Descomposición espectral ODMR (contador de qubits)** (`odmr_decompose.py`):
   CLEAN / matching pursuit que resuelve líneas individuales con SNR mínimo y
   separación mínima > linewidth. El número de líneas resueltas por píxel =
   anchura de registro = qubits. Es el contador de qubits por píxel.
2. **Super-resolución ODMR (sparse OMP)** (`superres_odmr.py`): deconvolución
   de líneas solapadas por orthogonal matching pursuit; deshace la respuesta
   espectral del píxel para resolver líneas bajo la linewidth.
3. **Lectura Bayesiana cuántico-limitada** (`bayesian_readout.py`): intervalo
   creíble unilateral con corrección Bonferroni; lectura óptima
   cuántico-limitada por píxel.
4. **Centroiding sub-píxel** (`detector_centroid.py`): reconstrucción del
   centroide por charge-sharing (imagen detector 64 → 64·S); mejora la
   resolución de imagen, no incrementa qubits.
5. **Denoise por primas-coprimas** (`prime_readout.py`): selección de primas
   por canal (16 primas = 16 canales de direccionamiento), ortogonalidad
   coprima exacta (cero cross-talk), denoiser coprimo y rejection de
   fuera-de-banda.
6. **Pila anti-ruido clásica 300 K** (`noise_filters.py`): filtros
   SNR-protectivos a temperatura ambiente; los canales lentos no promedian
   como 1/√N; `effective_noise_sigma` / `effective_t2` (DD tope 2·T1).
7. **Magnetometría vectorial 4 ejes ⟨111⟩** (`magnetometry.py`): reconstruye
   el vector B por píxel desde los dobletes ODMR (gap = 2γ|B·nᵢ|), búsqueda
   LS sobre las 2ᵏ combinaciones de signo con referencia gradiométrica para
   romper la ambigüedad tetraédrica.
8. **Gradiometría tensorial + localización de dipolo**
   (`tensor_gradiometry.py`): Jacobiano por píxel del mapa B-vector 8×8×3;
   localización de dipolo por modo diferencial (splits signo-ambiguo) o por
   vectores; `tensor_gradient_sweep`.
9. **Computación paralela de 64 píxeles** (`run_array`): un circuito por
   píxel con semillas independientes y agregación 64× shots; saltado de
   píxeles de ancho insuficiente; Shor coprimo distribuido (el primer píxel
   que halla el período gana).
10. **Acoplamiento dipolo 1/r³ (gate spin 2q)** (`dipole.py`): gate de 2
    qubits spin por dipolo dentro del píxel a 300 K (K_N all-to-all en modo
    registro); fundamenta el gate sin penalizar el canal óptico (gate de
    espín, no HOM).
11. **Memoria nuclear NV + ¹³C (simulador)** (`nuclear.py`): qubit nuclear
    ¹³C con T2 ~1 s (literatura, no facilitada por el proveedor) apareado al
    electrón; STORE/RECALL por SWAP; vía simulador (necesita canal RF ~2 MHz
    en hardware).
12. **Calibración medida del ancho de registro** (`array_calibration.py`): el
    número de qubits por píxel es el conteo de líneas ODMR resueltas, no una
    constante dura; integra sub-píxel, N-shot, super-resolución y Bayes; el
    lazo calibración → circuito alimenta las frecuencias medidas como rejilla
    del registro. `cap_to_resolvable` separa el tope del
    registro-por-gradiente del doblete Zeeman del campo ambiente.
13. **Modo píxel registro / ensamble** (`backend.py`): registro de N qubits
    dentro del píxel (acoplamiento dipolo K_N, gate 2q nativo) frente a
    ensamble de 1 qubit por píxel a 300 K (sin 2q cross-píxel); rechaza
    gates 2q en ensamble; N medido.
14. **Vía de hardware real completa**: detección de FPGA, verbos de control,
    barrido ODMR paralelo, programación de gradiente, integración N-shot y
    reconstrucción del mapa B-vector 4 ejes para gradiometría tensorial —
    todo sobre medidas reales, con degradación veraz a `no-fpga-detected`.
15. **QRNG + criptografía** (`crypto.py` / `qkd.py`): primitivas
    criptográficas (QKD) y consumo de la entropía cuántica del firmware
    (`READ_RNG`) para el backend cuántico.
16. **Tareas asíncronas** (`jobs.py`): calibración y ejecución paralela no
    bloquean la API (cola RQ), con modo síncrono en proceso para tests.

### 6. Originalidad y características distintivas

- Backend cuántico donde **el número de qubits por píxel es una cantidad
  medida** (líneas ODMR resueltas), no un parámetro fijo, cerrando el lazo
  calibración → circuito sobre la rejilla de frecuencias medida.
- **Modelo físico de 64 procesadores independientes a 300 K** (acoplamiento
  cross-píxel nulo a 2,5 mm) frente a una máquina de 640 qubits, con gate de 2
  qubits spin por dipolo 1/r³ fundamentado (no penaliza el canal óptico HOM).
- **Cadena de hardware real de-extremo-a-extremo** para gradiometría tensorial:
  FPGA → códigos ADC → descomposición → B-vector 4 ejes → gradiente tensorial,
  sin inyectar campo sintético, con degradación veraz cuando no hay placa.
- **Métricas de física ancladas a datos del proveedor** (splitting D, linewidth,
  ratio giromagnético), evitando afirmar constantes (T2/T1) no facilitadas por
  el proveedor.

---

### 7. Estado de verificación

- Suite pytest completa en verde (salida 0, sin fallos): **416 tests superados,
  12 omitidos**, en 33 ficheros de test.
- Tests de física (dipolo-dipole, canales de error, corrección Qiskit) + tests
  de integración API (TestClient sobre la app real) + test de la vía real con
  FPGA simulada (mock) para gradiometría tensorial y programación de gradiente.
- `ruff` limpio (reglas CI E9/F63/F7/F82/F401/F841) antes de cada entrega.
- Verificación local API + frontend antes de cada push.

---

### 8. Entorno de ejecución

- **Python**: 3.12 con entorno virtual.
- **Servicio**: `uvicorn api.main:app --port 8011`; worker RQ `rq worker qssc`
  desde el mismo árbol que la API.
- **Dependencias runtime**: FastAPI, uvicorn, Qiskit + Aer, NumPy/SciPy,
  Pydantic, RQ + Redis (tareas asíncronas).
- **Hardware opcional**: placa FPGA QSSC (firmware de la unidad FPGA) vía UART;
  en su ausencia opera en modo simulador.

---

### 9. Lista de ficheros fuente depositados

```
api/jobs.py
api/main.py
api/runner.py
qssc/__init__.py
qssc/array_calibration.py
qssc/backend.py
qssc/bayesian_readout.py
qssc/circuits.py
qssc/cli.py
qssc/crypto.py
qssc/defects.py
qssc/detector_centroid.py
qssc/dipole.py
qssc/magnetometry.py
qssc/noise_filters.py
qssc/nuclear.py
qssc/odmr_decompose.py
qssc/prime_readout.py
qssc/protocol.py
qssc/qkd.py
qssc/sensors.py
qssc/simulator.py
qssc/superres_odmr.py
qssc/tensor_gradiometry.py
```

---

## Algoritmos implementados en esta unidad

### Algoritmos publicados (referencia al artículo original)

- **Shor — factorización** [1] — `qssc/circuits.py`. Factoriza potencias primas vía `perfect_power` en el paso clásico.
- **QFT** [3] — `qssc/circuits.py`. Núcleo de la estimación de fase y de Shor.
- **Grover — búsqueda** [2] — `qssc/circuits.py`. Amplificación de amplitud.
- **Aritmética cuántica Draper / RGQFT** [4,5] — `qssc/circuits.py`. Sumador y multiplicador modulares.
- **Shor compilado (Amico)** [6] — `qssc/circuits.py`. QFT compilada para reducir profundidad.
- **BB84 — QKD** [29] — `qssc/qkd.py`. Bases rectilínea/diagonal, descarte de bits discordantes.
- **Extractor de von Neumann** [30] — `qssc/crypto.py`. Consumo de la entropía cuántica del firmware.
- **Centro NV — modelo físico** [7] — `qssc/defects.py`. Splitting D, γ, linewidth.
- **NV T1 a 300 K** [9] — `qssc/defects.py`. Tope del dynamical decoupling.
- **Magnetometría NV (base)** [8,10,11] — `qssc/magnetometry.py`. Doblete Zeeman 2γ|B·n|.
- **Gate spin 2q por dipolo 1/r³** [12,13,14,15] — `qssc/dipole.py`. Entrelazamiento y CNOT a 300 K.
- **Memoria nuclear ¹³C (base)** [16,17,18] — `qssc/nuclear.py`. Control universal y QEC nuclear.
- **Ramsey** [19] — `qssc/sensors.py`. Precesión separada π/2 – τ – π/2.
- **Hahn echo** [20] — `qssc/noise_filters.py`. Refocalización del dephasing estático.
- **CPMG** [21,22] — `qssc/noise_filters.py`. Dynamical decoupling.
- **CLEAN / matching pursuit** [23,24] — `qssc/odmr_decompose.py`. Descomposición espectral iterativa.
- **OMP** [25] — `qssc/superres_odmr.py`. Matching pursuit ortogonalizado.
- **Centroiding por charge-sharing (base)** [26] — `qssc/detector_centroid.py`. Reparto de carga.
- **Lectura Bayesiana (Helstrom)** [27,28] — `qssc/bayesian_readout.py`. Detección cuántica + Bonferroni.
- **Defectos hBN / SiC** [31,32,33,34,35] — `qssc/defects.py`. Perfiles físicos alternativos al NV.

### Algoritmos originales del QSSC

- **Magnetómetro vectorial de matriz 64-píxel + resolución de ambigüedad tetraédrica** (propio; basado en [8,10,11]) — `qssc/magnetometry.py`.
- **Readout por primas-coprimas + denoiser coprimo** (propio) — `qssc/prime_readout.py`.
- **Memoria nuclear NV + ¹³C emparejada (STORE/RECALL por SWAP)** (propio; basado en [16,17,18]) — `qssc/nuclear.py`.
- **Computación paralela de 64 píxeles (`run_array`, agregación 64×, saltado por ancho)** (propio) — `qssc/backend.py` · `api/runner.py`.
- **Ancho de registro mediado por gradiente (bobina + DAC, tope resoluble)** (propio) — `qssc/simulator.py` · `qssc/array_calibration.py`.
- **Centroiding sub-píxel sobre electrodo ITO continuo** (propio; basado en [26]) — `qssc/detector_centroid.py`.
- **Integración N-shot del lock-in (+~30 %/píxel, sin cambio HW)** (propio) — `qssc/array_calibration.py`.
- **Pila anti-ruido clásica 300 K (canales lentos no 1/√N)** (propio) — `qssc/noise_filters.py`.
- **Super-resolución ODMR sparse (diccionario ODMR + OMP)** (propio; basado en [25]) — `qssc/superres_odmr.py`.
- **Lectura Bayesiana cuántico-limitada por píxel + Bonferroni** (propio; basado en [27,28]) — `qssc/bayesian_readout.py`.
- **Gradiometría tensorial + localización de dipolo (Jacobiano 8×8×3)** (propio) — `qssc/tensor_gradiometry.py`.
- **Selector modo píxel registro/ensamble con N medido** (propio) — `qssc/backend.py`.

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
