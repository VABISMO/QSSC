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
| **Tamaño** | 4.497 líneas físicas de código fuente (SLOC) |
| **Valoración de esfuerzo (COCOMO básico, SLOCCount)** | 0,97 persona-años (11,6 persona-meses); coste estimado de desarrollo 130.982 USD (salario medio 56.286 USD/año, factor 2,40) |

> Métricas generadas con SLOCCount 2.26 (David A. Wheeler, GPL). SLOCCount no
> incluye contador de Verilog; las líneas Verilog/XDC se han contado con la
> misma metodología que SLOCCount (líneas físicas menos líneas en blanco y
> líneas solo-comentario) y se ha aplicado la misma fórmula COCOMO básica
> (`PM = 2,4 · KSLOC^1,05`). La valoración es una estimación de coste de
> re-desarrollo y no un precio de mercado.

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

- **`tb_top_level.v`** — banco de pruebas exhaustivo (919 SLOC) que inyecta los
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
| Verilog HDL | 4.231 | 94,1 % |
| Python (automatización síntesis) | 149 | 3,3 % |
| XDC (constraints) | 75 | 1,7 % |
| Shell | 29 | 0,6 % |
| Tcl | 13 | 0,3 % |
| **Total** | **4.497** | **100 %** |

Herramientas: Yosys / Vivado (síntesis), iverilog (simulación), pyverilog
(verificación estructural en CI), formato de bitstream Artix-7.

---

### 5. Funcionalidades destacadas

1. **Barrido ODMR paralelo de 64 píxeles** (`RUN_ALL_ODMR`): un único barrido
   serial de frecuencia muestrea simultáneamente los 64 detectores y devuelve
   una matriz (n_puntos × 64) de códigos ADC; el host descompone cada columna
   en las líneas ODMR resueltas = número de qubits por píxel.
2. **Secuencia de pulsos con detección síncrona** (`RUN_SEQUENCE` +
   `READ_LOCKIN`): motor de secuencias con puerta de microondas temporizada,
   acumulador lock-in simétrico (media-ventana, tope 10 000) y readout de Ramsey
   con fase aplicada — habilita medidas de T2* / Hahn a 300 K.
3. **Programación de bobina de gradiente** (`SET_GRADIENT <código>`): escribe el
   DAC de gradiente antes del barrido para separar espectralmente los NV dentro
   de un píxel (registro direccionable por gradiente).
4. **Lectura cuántica prima** (`READ_LOCKINP`): lectura de lock-in integrada con
   denoiser por primas-coprimas para mejorar la relación señal/ruido del
   readout.
5. **Generador de entropía cuántica** (`qrng_entropy`): RNG derivado del ruido
   de detección cuántica con tests de salud y re-intento de 64× sobre la
   entropía del SO.
6. **Seguridad determinista**: watchdogs anti-bloqueo en todas las FSMs,
   sincronizadores CDC de 2 FF para señales entre dominios de reloj, y
   read-one-with-wait-for-fresh en el lector ADC para evitar datos rezagados.

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

- 18/18 escenarios de simulación iverilog pasan (`sim/tb_top_level.v`).
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