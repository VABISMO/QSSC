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

Para cada algoritmo propio se incluye la función matemática que lo define (notación de
función), verificada contra el código depositado.

- **Magnetómetro vectorial de matriz 64-píxel + resolución de ambigüedad tetraédrica**
  (propio; basado en [8,10,11]) — `qssc/magnetometry.py`.

  ```
  Δᵢ(B) = (2γ/10⁶)·|B·nᵢ|        [MHz]   (nᵢ = eje ⟨111⟩, γ = 28,024 GHz/T)
  B̂ = argmin_{B∈ℝ³, s∈{±1}ᵏ} ‖A·B − s⊙b̂‖₂   (A=[nᵢ], b̂ᵢ=Δᵢ·10⁶/(2γ) [T], k≤4)
  ```

  Variables:
  - `Δᵢ` — desdoblamiento Zeeman medido en el píxel i [MHz] = separación del doblete ODMR
  - `γ` — relación giromagnética del electrón = 28,024 GHz/T (constante del centro NV)
  - `B` — vector de campo magnético incógnita a reconstruir [T]
  - `nᵢ` — versor del i-ésimo eje cuántico NV ⟨111⟩ (1 de los 4 del tetraedro)
  - `A` — matriz 3×k cuyas columnas son los versores nᵢ (modelo directo B→doubletes)
  - `b̂ᵢ` — proyección del campo sobre nᵢ deducida del doblete [T] = Δᵢ·10⁶/(2γ)
  - `s` — vector de signos ∈ {±1}ᵏ que resuelve la ambigüedad de signo del doblete
  - `k` — número de ejes NV resueltos en el píxel (≤ 4)

- **Readout por primas-coprimas + denoiser coprimo** (propio) — `qssc/prime_readout.py`
  · `fpga/rtl/command_parser.v`. La definición matemática del algoritmo usa una
  referencia **cosenoidal**: dos primas coprimas distintas dan ortogonalidad exacta
  **por pares** (cross-talk nulo entre dos canales coprimos) sobre una ventana
  igual al lcm del par. El firmware (`READ_LOCKINP`) aproxima la referencia
  cosenoidal con una onda cuadrada ±1 (boxcar de un ADC sample por
  medio-periodo); las ondas cuadradas de primas impares no son de media cero, por
  lo que esa aproximación arrastra un cross-talk residual ±1/(p·q) entre primas
  impares (sólo p=2 es de media cero).

  ```
  A_p = (2/N)·Σ_{n=0}^{N−1} s[n]·cos(2πn/p)        (N = ventana; ortogonalidad exacta por pares si N es múltiplo del lcm del par)
  Σ_{n=0}^{lcm(p,q)−1} cos(2πn/p)·cos(2πn/q) = 0   (p,q primas distintas — ortogonalidad exacta por pares, suma-a-producto)
  Σ_{n=0}^{pq−1} sgn_p[n]·sgn_q[n] = ±1            (firmware: onda cuadrada; cross-talk residual ±1/(p·q) para primas impares)
  ```

  Variables:
  - `A_p` — amplitud del canal de modulación de periodo p·τ recuperada por el lock-in
  - `N` — número de muestras de la ventana de demodulación (entero)
  - `s[n]` — señal muestreada en el instante n (ADC del detector integrado por el lock-in)
  - `p, q` — primas coprimas distintas; el periodo de cada canal es p·τ, q·τ
  - `τ` — reloj base del lock-in (1 µs en el QSSC); el medio-periodo host = p·τ/2
  - `lcm(p,q)` — mínimo común múltiplo del par = p·q; ventana de ortogonalidad exacta por pares
  - `sgn_p[n]` — onda cuadrada ±1 de periodo p usada por el firmware (aproximación al coseno)
  - `cos(2πn/p)` — referencia cosenoidal del demodulador software (definición exacta del algoritmo)

- **Memoria nuclear NV + ¹³C emparejada (STORE/RECALL por rotación controlada
  hiperfina)** (propio; basado en [16,17,18]) — `qssc/nuclear.py`.

  ```
  t_SWAP = 1/(2·A∥)                              (A∥ = hiperfino secular [Hz])
  F_SWAP = [1 − (p_T1 + 2·p_T2)/6]·(1 − δ)       (F_avg=(3+2e^{−t/T2}+e^{−t/T1})/6; p_T1=1−e^{−t/T1}, p_T2=1−e^{−t/T2_n})
  S(t) = e^{−t/T2_n}   ;   G = T2_n/T2_e
  ```

  Variables:
  - `t_SWAP` — tiempo de la rotación controlada hiperfina (un entangler CNOT-equivalente); un SWAP completo = 3 rotaciones
  - `A∥` — componente secular del acoplamiento hiperfino electrón–¹³C [Hz] (~2 MHz)
  - `F_SWAP` — fidelidad media del gate (canal térmico compuesto con el error de drive)
  - `p_T1` — probabilidad de relajación longitudinal = 1 − e^{−t/T1}
  - `p_T2` — probabilidad de dephasing = 1 − e^{−t/T2_n}
  - `T1, T2_n` — relajación longitudinal y coherencia del qubit nuclear ¹³C [s]; T1 ≈ 2·T2_n
  - `δ` — error de depolarización del drive de RF (canal de control, independiente de la decoherencia)
  - `t` — duración del gate [s] = t_SWAP
  - `S(t)` — fracción del estado cuántico almacenado que sobrevive un tiempo t sobre el ¹³C
  - `T2_e` — coherencia del electrón NV [s] (referencia; mucho más corta que T2_n)
  - `G` — ventaja de almacenamiento = T2_n/T2_e (cuántas veces más dura la memoria nuclear)

- **Computación paralela de 64 píxeles (`run_array`, agregación 64×, saltado por
  ancho)** (propio) — `qssc/backend.py` · `api/runner.py`.

  ```
  C[b] = Σ_{i∈P} cᵢ[b]        (P = {píxeles con ancho ≥ num_qubits}; semillaᵢ = base_seed + i)
  # un píxel se SALTA (nunca se trunca) si el circuito excede su ancho medido
  ```

  Variables:
  - `C[b]` — conteo agregado del bitstring b sobre los 64 píxeles (histograma 64×)
  - `cᵢ[b]` — conteo del bitstring b devuelto por el píxel i (con su semilla independiente)
  - `P` — conjunto de píxeles no saltados = {píxeles cuyo ancho medido ≥ num_qubits del circuito}
  - `semillaᵢ` — semilla PRNG del píxel i = base_seed + i (independiente por píxel)
  - `ancho medido` — número de qubits resueltos del píxel i (de la calibración ODMR)
  - `num_qubits` — qubits que requiere el circuito (Bell=2, Shor-n=n, …)

- **Ancho de registro mediado por gradiente (bobina + DAC, tope resoluble)** (propio)
  — `qssc/simulator.py` · `qssc/array_calibration.py`.

  ```
  N_res = max(1, ⌊(g·L)/Δf⌋)   (g [MHz/µm], L [µm], Δf = linewidth [MHz]; separación ≥ FWHM o colapsa)
  ```

  Variables:
  - `N_res` — número de líneas ODMR resueltas = anchura de registro = qubits del píxel (entero ≥ 1)
  - `g` — gradiente de campo de la bobina [MHz/µm] (programado por el DAC MCP4921)
  - `L` — tamaño del píxel [µm] (extensión espacial del conjunto NV)
  - `Δf` — anchura de línea ODMR (FWHM) [MHz] del defecto (~5 MHz para NV)
  - `g·L` — dispersión espectral total inducida por el gradiente [MHz] = ventana de direccionalidad

- **Centroiding sub-píxel sobre electrodo ITO continuo** (propio; basado en [26]) —
  `qssc/detector_centroid.py`.

  ```
  w_p = exp(−d_p²/(2σ²)) / Σ_{p'} exp(−d_{p'}²/(2σ²))
  x_c = Σ_p q_p·x_p / Σ_p q_p   ;   y_c = Σ_p q_p·y_p / Σ_p q_p   (q_p = carga en electrodo p)
  S = max(1, ⌊SNR/umbral⌋)  →  canales = 64·S   (resolución de imagen, no más qubits)
  ```

  Variables:
  - `w_p` — peso (fracción de carga) asignado al electrodo p para un spot dado
  - `d_p` — distancia del spot al centro del electrodo p [µm]
  - `σ` — dispersión de la nube de carga sobre el ITO continuo [µm]
  - `x_c, y_c` — coordenadas del centroide reconstruido [µm]
  - `q_p` — carga total recogida en el electrodo p (suma de los w_p del spot)
  - `x_p, y_p` — coordenadas del centro del electrodo p [µm]
  - `S` — factor sub-píxel = canales por píxel (resolución de imagen 64 → 64·S)
  - `SNR` — relación señal-ruido del spot; umbral = SNR mínimo para subdividir

- **Integración N-shot del lock-in (+~30 %/píxel, sin cambio HW)** (propio) —
  `qssc/array_calibration.py`.

  ```
  σ_eff(N) = σ₁/√N          (canal rápido incorrelado)
  SNR(N) = (A_k/σ₁)·√N      (A_k = 1−(1−c_s)ᵏ = contraste de k NV en el bin)
  ```

  Variables:
  - `σ_eff(N)` — ruido de readout efectivo tras N ciclos de lock-in integrados
  - `σ₁` — ruido de un solo ciclo (canal rápido incorrelado)
  - `N` — número de ciclos de lock-in acumulados
  - `SNR(N)` — relación señal-ruido de la línea k tras N ciclos
  - `A_k` — contraste ODMR de k NV en el bin = 1 − (1 − c_s)ᵏ
  - `c_s` — contraste de un solo NV (profundidad ODMR por defecto)
  - `k` — número de NV que contribuyen al bin (línea espectral)

- **Pila anti-ruido clásica 300 K (canales lentos no 1/√N)** (propio) —
  `qssc/noise_filters.py`.

  ```
  σ_eff = √(σ_floor² + Σ_canales σ_c²)   (σ_floor = base/√N)
  # lentos (temp,presión): N⁰ — no promedian como 1/√N; rápidos (vib,EMI): 1/√N
  T2_eff = min(T2_dd, 2·T1)              (tope de relajación 1/T2 = 1/(2·T1) + 1/Tφ)
  ```

  Variables:
  - `σ_eff` — sigma de ruido efectivo del píxel (composición en cuadratura de canales)
  - `σ_floor` — ruido de piso (base/√N, el canal cuántico rápido incorrelado)
  - `σ_c` — sigma de cada canal de ruido clásico (temp, presión, vib, EMI…)
  - `N` — número de shots promediados (los canales lentos escalan como N⁰, no 1/√N)
  - `T2_eff` — coherencia efectiva tras el dynamical decoupling, topada por la relajación
  - `T2_dd` — coherencia extendida por la secuencia de DD (CPMG/XY8…)
  - `T1, Tφ` — relajación longitudinal y dephasing puro [s]; 1/T2 = 1/(2·T1) + 1/Tφ

- **Super-resolución ODMR sparse (diccionario ODMR + OMP)** (propio; basado en [25])
  — `qssc/superres_odmr.py`.

  ```
  Φ[i,j] = exp(−(f_i−c_j)²/(2σ²)) / ‖Φ_j‖
  j* = argmax_j |⟨Φ_j, r⟩| ;  x = argmin ‖Φ_S x − y‖₂ ;  r ← y − Φ_S x   (parada: ‖r‖ ≤ σ·√N)
  ```

  Variables:
  - `Φ` — diccionario espectral; columna j = Gaussiano unitario centrado en c_j
  - `Φ[i,j]` — respuesta del píxel a la frecuencia f_i de una línea en c_j (normalizada)
  - `f_i, c_j` — frecuencias de muestreo y centros de las líneas candidatas [GHz]
  - `σ` — anchura espectral del píxel (FWHM/2,35) [GHz]
  - `‖Φ_j‖` — norma de la columna j (normaliza el Gaussiano a energía unitaria)
  - `j*` — índice de la línea candidata de mayor correlación con el residuo
  - `r` — residuo = y − Φ_S·x (se actualiza cada iteración OMP)
  - `x` — amplitudes sparse estimadas (LS sobre el soporte S seleccionado)
  - `y` — espectro ODMR medido (vector de contrastes por frecuencia)
  - `S` — soporte = conjunto de líneas seleccionadas hasta la parada

- **Lectura Bayesiana cuántico-limitada por píxel + Bonferroni** (propio; basado en
  [27,28]) — `qssc/bayesian_readout.py`.

  ```
  â_j = (Φ_j·y)/‖Φ_j‖²   ;   σ_j = σ/‖Φ_j‖            (filtro adaptado, varianza Cramér–Rao)
  línea j ⇔ â_j/σ_j > z(1 − (1−C)/n_bins) ∧ â_j > 0   (Bonferroni α_per = (1−C)/n_bins)
  ```

  Variables:
  - `â_j` — estimador de máxima verosimilitud (filtro adaptado) de la amplitud de la línea j
  - `σ_j` — incertidumbre (Cramér–Rao) de â_j = σ/‖Φ_j‖
  - `Φ_j` — filtro adaptado (columna del diccionario espectral) para la línea j
  - `y` — espectro ODMR medido (vector de contrastes por frecuencia)
  - `σ` — ruido de readout por bin [u.a.]
  - `z(·)` — cuantil de la normal estándar (umbral estadístico unilateral)
  - `C` — credibilidad global deseada (0,01–0,99, p.e. 0,95)
  - `n_bins` — número de bins del espectro (corrección de comparaciones múltiples)
  - `α_per` — nivel de significancia por bin tras Bonferroni = (1−C)/n_bins

- **Gradiometría tensorial + localización de dipolo (Jacobiano 8×8×3)** (propio) —
  `qssc/tensor_gradiometry.py`.

  ```
  B(r) = (μ₀/4π)·[3(m·r̂)r̂ − m]/r³                  (μ₀/4π = 10⁻⁷ T·m/A)
  J[i] = [[∂B_x/∂x, ∂B_x/∂y],[∂B_y/∂x, ∂B_y/∂y],[∂B_z/∂x, ∂B_z/∂y]]   [µT/mm]
  Δsᵢ·sgn(B_bg·nᵢ) ≈ (2γ/10⁶)(B_dip·nᵢ)  →  argmin_{x₀,y₀,z₀,m} ‖…‖₂   (sin ambigüedad de signo)
  ```

  Variables:
  - `B(r)` — campo del dipolo en el punto de observación r [T]
  - `m` — momento magnético del dipolo fuente [A·m²]
  - `r, r̂` — vector del dipolo al píxel (= píxel − fuente; norma r y versor r̂) [m]; fuente bajo el plano z=0 ⇒ r_z = +z₀
  - `μ₀/4π` — constante magnética = 10⁻⁷ T·m/A
  - `J[i]` — Jacobiano 3×2 del campo en el píxel i (gradiente in-plano) [µT/mm]
  - `B_x,B_y,B_z` — componentes del campo medido en el píxel [µT]
  - `Δsᵢ` — variación del doblete Zeeman en el píxel i causada por el dipolo [MHz]
  - `B_bg` — campo de fondo (sin dipolo) que fija el signo del doblete [T]
  - `B_dip` — campo del dipolo candidato, función de x₀,y₀,z₀,m [T]
  - `nᵢ` — versor del eje NV del píxel i (proyecta el campo sobre el doblete)
  - `x₀,y₀,z₀,m` — posición y momento del dipolo incógnita (ajuste LS)

- **Selector modo píxel registro/ensamble con N medido** (propio) — `qssc/backend.py`.

  ```
  d = (μ₀/4π)·(g_e μ_B)²/(h·r³) [Hz]   ;   t_CZ = 1/(2d)        (acoplamiento dipolo 1/r³)
  # registro: K_n all-to-all sobre N qubits del píxel; ensamble: 64 píxeles, sin 2q cross-píxel
  N = len(líneas resueltas por decompose_odmr)               (N medido reemplaza constante dura)
  ```

  Variables:
  - `d` — acoplamiento dipolo-dipolo entre dos NV del píxel [Hz]
  - `g_e` — factor g del electrón (~2,003)
  - `μ_B` — magnetón de Bohr [J/T]
  - `h` — constante de Planck [J·s]
  - `r` — separación entre dos NV del píxel [m] (el acoplamiento escala como 1/r³)
  - `t_CZ` — tiempo de un gate CZ por acoplamiento dipolo = 1/(2d) [s]
  - `K_n` — grafo completo all-to-all de N qubits (modo registro); gate 2q nativo en cada par
  - `N` — anchura de registro = nº de líneas ODMR resueltas (medido, no constante)
  - `decompose_odmr` — descomposición espectral que cuenta las líneas resueltas por píxel

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
