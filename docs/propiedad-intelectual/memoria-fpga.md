# Memoria descriptiva — Programa de ordenador

## Unidad: Firmware FPGA — Controlador de placa cuántica QSSC

---

### 1. Datos de la obra

| Campo | Valor |
|---|---|
| **Título** | QSSC — Firmware FPGA de control y readout para plataforma cuántica de 64 píxeles NV |
| **Tipo de obra** | Programa de ordenador (Art. 96.1 del Texto Refundido de la Ley de Propiedad Intelectual, RDLeg 1/1996) |
| **Titular / Autor** | _[A completar por el titular: nombre/apellidos o razón social, NIF/CIF, domicilio]_ |
| **Fecha de creación (inicio)** | 2026-08-07 |
| **Fecha de finalización (versión depositada)** | 2026-09-14 |
| **Lugar de elaboración** | España |
| **Lenguaje de programación** | Verilog HDL (RTL), Tcl (flujo de síntesis), Python y Shell (automatización del flujo) |
| **Plataforma objetivo** | FPGA Xilinx Artix-7 (XC7A15T-1CSG325C), placa de desarrollo QMTECH |
| **Tamaño** | 4.483 líneas físicas de código fuente (SLOC) |
| **Valoración de esfuerzo (COCOMO básico, SLOCCount)** | 0,97 persona-años (11,6 persona-meses); coste estimado de desarrollo 130.554 USD (salario medio 56.286 USD/año, factor 2,40) |

> Métricas generadas con SLOCCount 2.26 (David A. Wheeler, GPL). SLOCCount no
> incluye contador de Verilog ni reconoce `.xdc`; los ficheros Verilog y XDC
> se han contado con SLOCCount nativo tras renombrar la extensión a `.c`
> (contador ANSI-C, misma sintaxis de comentarios `//` y `/* */`) para `.v` y
> a `.py` (contador de `#`) para `.xdc`. El anclaje de validación: los
> ficheros `.js` contados nativamente por SLOCCount dan el mismo resultado al
> renombrarlos a `.c` (336 = 336 SLOC), lo que prueba que el renombrado
> preserva la semántica de conteo. Fórmula COCOMO básica
> `PM = 2,4 · KSLOC^1,05`. La valoración es una estimación de coste de
> re-desarrollo y no un precio de mercado. El script reproducible
> `docs/propiedad-intelectual/sloc_count.py` contiene el conteo por fichero
> y la valoración.

---

### 2. Descripción general

El firmware FPGA constituye la capa de control en tiempo real de la plataforma
QSSC, un instrumento de computación y sensórica cuántica basado en una matriz de
64 píxeles, cada uno conteniendo microdiamantes con centros de nitrógeno-vacante
(NV) de diamante, operando a temperatura ambiente (300 K).

El firmware reside en una FPGA Xilinx Artix-7 y gobierna de forma determinista:

- la generación de microondas para el direccionamiento espectral de los qubits
  spin (oscilador ADF4351 controlado por SPI);
- la conmutación del banco de emisores ópticos (64 LEDs CQD excitados a través
  de drivers TLC5947 de corriente constante);
- la adquisición paralela de la fotocorriente de los 64 detectores mediante
  cuatro ADCs AD7616 muestreados simultáneamente;
- la ejecución de secuencias de pulsos con detección síncrona (lock-in digital)
  para la lectura integrada de ODMR;
- la comunicación con el host por UART con un protocolo de verbos ASCII.

El programa transforma la FPGA en un periférico de medida cuántica: el host
envía verbos de alto nivel (barridos ODMR, secuencias de Ramsey, lectura de
detectores, programación de la bobina de gradiente) y la FPGA devuelve matrices
de códigos ADC, acumuladores de lock-in o estados, con sincronización a nivel de
ciclo de reloj que un procesador de propósito general no puede garantizar.

---

### 3. Arquitectura y módulos

El código se organiza en tres subdirectorios: `rtl/` (lógica síncrona
sintetizable), `sim/` (banco de pruebas) y `synth/` (flujo de implementación).

#### 3.1 `rtl/` — lógica sintetizable (12 módulos Verilog)

- **`top_level.v`** — entidad de integración. Instancia todos los módulos,
  gestiona el reset asíncrono y el reloj único (50 MHz del oscilador de placa,
  escalado internamente a 100 MHz vía MMCM), y enruta el bus de comandos
  analizado hacia los controladores de periféricos. Es el punto único de
  entrada/salida de la FPGA hacia el host y hacia los dispositivos analógicos.
- **`command_parser.v`** — analizador del protocolo de verbos ASCII recibido por
  UART. Decodifica comandos textuales (`ID`, `STATUS`, `RESET`, `ODMR_SWEEP`,
  `READ_DETECTORS`, `READ_LOCKIN`, `READ_LOCKINP`, `RUN_SEQUENCE`,
  `SET_GRADIENT`, `RUN_ALL_ODMR`, `APPLY_PULSE`) y despacha señales de control a
  los módulos correspondientes. Contiene la máquina de estados principal de
  comandos y los temporizadores de vigilancia (watchdogs) anti-bloqueo.
- **`uart_rx.v`** / **`uart_tx.v`** — transceptor UART 115200-8N1 con
  sobremuestreo 8× en recepción y generador de baudios por división de reloj.
- **`spi_master.v`** — maestro SPI para programar el sintetizador de microondas
  ADF4351 y los DACs de gradiente/emisor. Reloj SPI 12,5 MHz, modo 0, escritura
  de registros de 32 bits con bit de habilitación.
- **`adf4351_ctrl.v`** — controlador específico del ADF4351. Programa los
  registros R0/R1/R2/R4 para fijar la frecuencia de microondas en el intervalo
  2,80–2,94 GHz (cubriendo la transición ms=0 del NV a D≈2,87 GHz), con
  escritura de fase R1-only que evita el re-lock del lazo durante los cambios
  rápidos de frecuencia de un barrido ODMR.
- **`dac_ctrl.v`** — controlador del DAC MCP4921 (12 bits) que programa la
  bobina de gradiente magnético; código 0–4095 lineal con el campo.
- **`emitter_array.v`** — generador de las señales PWM/serie para los drivers
  TLC5947 de corriente constante que excitán los 64 emisores CQD, con
  habilitación por píxel y apagado global de seguridad.
- **`tlc5947_ctrl.v`** — controlador del bus serie (GSCLK + BLANK + XLAT) del
  driver LED TLC5947, con corrección de corriente por canal.
- **`adc_reader.v`** — lector de los cuatro ADCs AD7616 en modo bus de datos
  paralelo. Muestreo simultáneo de los 64 canales (16 por AD7616) con espera de
  dato fresco (`wait-for-fresh`) y sincronizador de 2 flip-flops (CDC) para la
  señal `adc_busy` que cruza dominios de reloj.
- **`qrng_entropy.v`** — generador de entropía cuántica derivado del ruido de
  lectura de los detectores, con condicionamiento y tests de salud por lotes,
  fuente de aleatoriedad para el backend cuántico.

#### 3.2 `sim/` — verificación

- **`tb_top_level.v`** — banco de pruebas exhaustivo (926 SLOC) que inyecta los
  verbos por UART modelo, modela la respuesta analógica de los ADCs y verifica
  18 escenarios: identificación, barrido ODMR, lectura de detectores, acumulador
  lock-in, secuencia de Ramsey con fase de readout, programación de gradiente,
  watchdogs anti-bloqueo y reinicios. Los escenarios pasan en iverilog 18/18.

#### 3.3 `synth/` — flujo de implementación

- **`build_bitstream.tcl`** — script Tcl para Vivado que fija la lista explícita
  de ficheros Verilog (no glob), los constraints, y ejecuta síntesis →
  implementación → bitstream → reporte de utilización/timing.
- **`run_synth.sh`** — envoltura de shell que lanza el flujo headless.
- **`make_report.py`** — parsea los reportes de Vivado y emite un resumen
  de utilización de LUT/FF/BRAM/DSP y el estado de timing (WNS/TNS).

#### 3.4 `constraints.xdc` — restricciones físicas

Asignación de pines para la placa QMTECH XC7A15T-1CSG325C: reloj 50 MHz (pin
R2), reset activo-bajo (H18, invertido), LEDs de estado (C8/D8), JTAG (TCK=F8,
TDI=T9, TDO=T8, TMS=R8), UART host, y los buses SPI/paralelo hacia ADF4351,
MCP4921, TLC5947 y AD7616.

---

### 4. Tecnologías y lenguajes

| Lenguaje | SLOC | Porcentaje |
|---|---|---|
| Verilog HDL | 4.230 | 94,4 % |
| Python (automatización síntesis) | 136 | 3,0 % |
| XDC (constraints) | 75 | 1,7 % |
| Shell | 29 | 0,6 % |
| Tcl | 13 | 0,3 % |
| **Total** | **4.483** | **100 %** |

Herramientas: Yosys / Vivado (síntesis), iverilog (simulación), pyverilog
(verificación estructural en CI), formato de bitstream Artix-7.

---

### 5. Funcionalidades destacadas — metodologías implementadas

Cada metodología se realiza con un verbo del protocolo UART y un módulo RTL
dedicado. Todas las cifras (frecuencias, duraciones de puerta, topes de
acumulador) son constantes del firmware depositado.

#### Adquisición y barrido ODMR

1. **Barrido ODMR con puerta prolongada** (`ODMR_SWEEP`): barrido del ADF4351
   sobre 2,80–2,94 GHz (transición ms=0 del NV, D≈2,87 GHz). Cada punto
   mantiene la puerta RF 200 µs (`ODMR_GATE_NS`), mayor que los ~86 µs del
   scan del ADC, y la lectura ADC se dispara al flanco de subida de la puerta
   (*gate-rise read*) para que la muestra refleje la fluorescencia con MW
   encendido. La escritura de frecuencia usa sólo el registro R1 del ADF4351
   entre puntos para no re-lockear el lazo durante el barrido rápido.
2. **Barrido ODMR paralelo de 64 píxeles** (`RUN_ALL_ODMR`): un único barrido
   serial de frecuencia muestrea simultáneamente los 64 detectores en cada
   punto y devuelve una matriz (n_puntos × 64) de códigos ADC; el host
   descompone cada columna en las líneas ODMR resueltas, cuyo conteo es el
   número de qubits por píxel. Un solo barrido produce los 64 espectros a la
   vez, preservando la sincronía óptica entre píxeles.

#### Secuencias de pulsos

3. **Motor de pulsos Ramsey / Hahn / CPMG** (`RUN_SEQUENCE <site> <kind>
   <n> <t_step>`): motor de secuencias sobre `n` ventanas (1..1024) con
   tiempo de evolución libre T = i·t_step. Por ventana: puerta π/2 (fase 0) →
   precesión libre T → [puerta π → retardo T] (Hahn kind=1 / CPMG kind=2) →
   puerta π/2 de readout (fase 90° para Ramsey kind=0) → lectura ADC del
   sitio → emisión del código. El RTL sólo secuencia puertas MW y snapshot
   del ADC; el host ajusta T2* (Ramsey) / T2 (Hahn/CPMG). CPMG implementa un
   eco π único en el RTL; el modelo de decaimiento multi-eco es host-side. El
   motor vigila `mw_gate` subir y bajar antes de iniciar el retardo de
   precesión — sin solape en T=0.
4. **Readout de Ramsey con fase aplicada**: la puerta de readout π/2 se escribe
   a fase 90° para el modo Ramsey. La fase se aplica como escritura R1-only
   del ADF4351 (`pll_phase_only`) que cambia el registro PHASE sin retocar
   R0/R2/R4, evitando un re-lock del lazo en medio de la secuencia. La
   conversión de milirradianes a pasos de fase del chip es
   `mrad_to_phase = m·4096/6283` (mrad → 0..4095 pasos mod 2π).
5. **Pulso MW con fase y potencia** (`APPLY_PULSE <qubit> <freq_khz> <amp>
   <duration_ns> <phase>`): programa la portadora del ADF4351 — fase (R1
   PHASE, 12 bit), potencia (R4 RF_PWR, 4 niveles) — y temporiza la puerta
   del conmutador RF durante `duration_ns` (`mw_gate_ns → mw_gate_load`). El
   firmware **no** conforma una envolvente analógica de amplitud/fase (eso
   requiere un AWG/modulador real); fija fase + potencia y gateda el RF — el
   camino MW mínimo y verificable.

#### Detección síncrona (lock-in)

6. **Lock-in síncrono on/off** (`READ_LOCKIN <site> <n_cycles>`): acumulador
   auto-temporizado independiente del `mod_sync` externo (sim-friendly). Por
   ciclo: MW on medio-periodo (200 µs, `LI_HALF_NS`) + lectura ADC a
   `sum_on`; MW off medio-periodo + lectura a `sum_off`. Respuesta
   `<sum_on> <sum_off> <n_on> <n_off>` — acumulaciones raw; el host deriva
   diff/σ/SNR. Media-ventana simétrica y tope de 10 000 ciclos. `mod_sync`
   permanece como referencia externa del lock-in; el FPGA entrega las
   muestras raw.
7. **Lock-in de período primo** (`READ_LOCKINP <site> <n_cycles> <half_ns>`):
   demodula a un medio-período `half_ns = p·τ/2` suministrado por el host,
   donde `p` es el primo asignado al canal del NV. Como primas distintas son
   pairwise coprimas, las referencias de lock-in son exactamente ortogonales
   sobre el LCM (= pₐ·p_b) — cero cross-talk entre canales primos, no
   meramente pequeño. La selección de primas y la ortogonalidad coprima se
   mantienen en Python (`qssc.prime_readout`); el RTL sólo demodula al
   medio-período pedido. Compatible hacia atrás: `READ_LOCKIN` conserva el
   medio-período de 200 µs por defecto. Verificación de cotas: `n_cycles`
   fuera de rango responde `ERR`.

#### Técnicas de resolución / qubits

8. **Bobina de gradiente magnético** (`SET_GRADIENT <código 0..4095>`):
   escribe el DAC MCP4921 (12 bit) que programa la corriente de la bobina de
   gradiente (I = V_dac/R_sense) antes del barrido ODMR, separando
   espectralmente los NV dentro de un píxel → registro direccionable por
   gradiente. La escritura SPI está serializada y espera a que `dac_ctrl`
   termine el desplazamiento antes de responder OK. `RESET` anula el
   gradiente (código 0, `grad_en=0`). El inverso
   `gradient_dac_code_from_mhz` vive en el backend.

#### Emisores y detectores

9. **Emisores CQD paralelos** (`SET_EMITTER / DRIVE_EMITTER / DRIVE_PATTERN /
   CLEAR_EMITTERS`): 3× TLC5947 de corriente constante, grayscale 12-bit por
   canal, para los 64 emisores CQD. `SET_EMITTER` arma el nivel de un canal;
   `DRIVE_PATTERN <duration_ms>` dispara **todos** los canales armados
   simultáneamente (multi-emisor paralelo); `DRIVE_EMITTER` es el atajo de un
   solo emisor a plena potencia; `CLEAR_EMITTERS` desarma los 64.
10. **Calibración emisor→detector** (`CALIBRATE`): por cada emisor, lo bombea,
    lee los 64 detectores y almacena el detector más brillante en
    `cal_lut[em]`. Aísla qué emisor se acopla a qué electrodo detector — la
    LUT de correspondencia óptica que el host usa para enrutar la
    calibración ODMR.
11. **Readout paralelo de 64 detectores** (`READ_DETECTORS / READ_DETECTOR`):
    4× AD7616 dual simultaneous-sampling, 64 canales 16-bit sobre un bus SPI
    de 9 hilos. Una FSM en background refresca continuamente una RAM 64×16
    (8 CONVST por scan completo); la FSM de lectura snapshot el sample
    solicitado. El código entregado es una **muestra de intensidad 16-bit**
    (salida TIA digitalizada sobre el rango bipolar ±5 V), no un conteo de
    fotones. `read_one` espera dato fresco (*wait-for-fresh*).

#### Entropía cuántica

12. **Generador de entropía cuántica** (`READ_RNG <n_bytes 1..256>` +
    `qrng_entropy.v`): extractor de von-Neumann sobre el ruido LSB de un
    canal oscuro del AD7616 — pares (b0,b1): 01 guarda 1, 10 guarda 0,
    00/11 descarta. Empaqueta bits depolarizados en palabras W=32-bit.
    `harvest_en` gatea el extractor; `flush` descarta el par/palabra parcial
    para que la próxima palabra nazca sólo de muestras post-flush (mantiene
    kept ≤ raw/2). `samples_seen` es un contador 24-bit. El verbo entrega
    hex + un footer ` 00064 0 0\n` con el conteo de muestras raw. `mod_sync`
    no se usa: la entropía viene del ruido eléctrico, no de la señal óptica.

#### Seguridad determinista y control

13. **Seguridad determinista**: watchdogs anti-bloqueo
    (`GATE_WDOG_CYC = READ_WDOG_CYC = 200 000` ciclos, cubre la puerta máxima
    de 200 µs + latencia de lectura con margen) en todas las FSM: **sólo**
    `RESET` escapa de un `mw_gate` atascado o un ADC muerto. Sincronizador
    CDC de 2 flip-flops para `adc_busy` que cruza dominios de reloj.
    `read_one` espera dato fresco. La escritura R1-only de fase evita la
    carrera de re-lock. `RESET` re-estroboscopia `adc_resetn`, anula emisores
    y gradiente, pero **no** resetea el parser — puede recuperarse a mitad
    de flujo.
14. **Verbos de control y prueba** (`ID / STATUS / RESET`): `ID` responde
    `QSSC v1`; `STATUS` devuelve una línea con `PLL_LOCKED`, `ADC_BUSY` y el
    estado de la FSM; `RESET` reinicia los periféricos. Verbos desconocidos o
    mal formados responden `ERR`. Los números son enteros decimales
    (opcionalmente con signo); las muestras 16-bit se envían como ASCII
    decimal separado por espacios.

---

### 6. Originalidad y características distintivas

- Firmware diseñado específicamente para **computación y sensórica cuántica a
  temperatura ambiente** sobre centros NV de diamante, no un controlador de
  instrumento genérico.
- **Readout paralelo de 64 canales** con ADCs simultáneos (AD7616) en lugar del
  muxeo serial habitual, para no perder la sincronía óptica entre píxeles.
- **Verbos de alto nivel** que exponen primitivas cuánticas (barrido ODMR,
  secuencia de Ramsey, lock-in) directamente en la FPGA, de modo que el host
  opera sobre medidas cuánticas y no sobre registros crudos.
- **Alineación espectral por gradiente** integrada en el flujo de firmware
  (SET_GRADIENT), posibilitando un registro de qubits de ancho medible dentro
  de cada microdiamante.

---

### 7. Estado de verificación

- Banco de pruebas `sim/tb_top_level.v` (926 SLOC) que inyecta los verbos por
  UART modelo y modela la respuesta analógica de los ADCs; todos los
  escenarios pasan en iverilog: **ID** (`QSSC v1`), **STATUS**
  (PLL_LOCKED=1, ADC_BUSY=0), **RESET** (re-estroboscopia + anula gradiente),
  **ODMR_SWEEP** (4 líneas 2870000–2879999 + OK), **RUN_SEQUENCE** (00004 +
  4×01234, puertas MW y escrituras PLL contadas), **Ramsey con fase**
  (puertas + escrituras PLL R1-only), **READ_LOCKIN**
  (sum_on/sum_off/n_on/n_off), **READ_LOCKINP** (válido + fuera-de-cotas →
  ERR), **RUN_ALL_ODMR** (2 filas × 64 muestras + OK), **SET_GRADIENT**
  (palabra MCP4921 + grad_en), **CALIBRATE** (LUT em→det) y **READ_RNG**
  (hex + footer 00064 0 0).
- Verificación estructural pyverilog y validación de constraints XDC en CI.
- El bitstream sintetiza para XC7A15T-1CSG325C (informe de utilización y timing
  generado por `synth/make_report.py`).

---

### 8. Entorno de ejecución

- **Hardware**: placa QMTECH XC7A15T-1CSG325C; oscilador 50 MHz; programador
  JTAG/FT2232H.
- **Dependencias externas**: sintetizador RF ADF4351, DAC MCP4921, driver LED
  TLC5947, ADC AD7616 ×4 — todos en la placa cabezal del QSSC.
- **Interfaz host**: UART 115200-8N1, protocolo de verbos ASCII documentado en
  `docs/`.

---

### 9. Lista de ficheros fuente depositados

```
fpga/constraints.xdc
fpga/rtl/adc_reader.v
fpga/rtl/adf4351_ctrl.v
fpga/rtl/command_parser.v
fpga/rtl/dac_ctrl.v
fpga/rtl/emitter_array.v
fpga/rtl/qrng_entropy.v
fpga/rtl/spi_master.v
fpga/rtl/tlc5947_ctrl.v
fpga/rtl/top_level.v
fpga/rtl/uart_rx.v
fpga/rtl/uart_tx.v
fpga/sim/tb_top_level.v
fpga/synth/build_bitstream.tcl
fpga/synth/make_report.py
fpga/synth/run_synth.sh
```

---

_Fecha de la memoria: 2026-09-14. Esta memoria descriptiva acompaña al depósito
del código fuente en el Repositorio Oficial del Proyecto QSSC y se destina a su
inscripción en el Registro de la Propiedad Intelectual._