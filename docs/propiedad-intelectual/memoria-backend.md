# Memoria descriptiva — Programa de ordenador

## Unidad: Backend — API, simulador cuántico y librería de física QSSC

---

### 1. Datos de la obra

| Campo | Valor |
|---|---|
| **Título** | QSSC — API REST, simulador cuántico realista y librería de física para plataforma de 64 píxeles NV |
| **Tipo de obra** | Programa de ordenador (Art. 96.1 del Texto Refundido de la Ley de Propiedad Intelectual, RDLeg 1/1996) |
| **Titular / Autor** | _[A completar por el titular: nombre/apellidos o razón social, NIF/CIF, domicilio]_ |
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
> re-desarrollo, no un precio de mercado. El script reproducible
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

_Fecha de la memoria: 2026-09-14. Esta memoria descriptiva acompaña al depósito
del código fuente en el Repositorio Oficial del Proyecto QSSC y se destina a su
inscripción en el Registro de la Propiedad Intelectual._