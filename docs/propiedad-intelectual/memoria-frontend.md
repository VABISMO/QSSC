# Memoria descriptiva — Programa de ordenador

## Unidad: Frontend web — Interfaz de operación del instrumento cuántico QSSC

---

### 1. Datos de la obra

| Campo | Valor |
|---|---|
| **Título** | QSSC — Interfaz web de operación, calibración y sensórica para plataforma cuántica de 64 píxeles NV |
| **Tipo de obra** | Programa de ordenador (Art. 96.1 del Texto Refundido de la Ley de Propiedad Intelectual, RDLeg 1/1996) |
| **Titular / Autor** | _[A completar por el titular: nombre/apellidos o razón social, NIF/CIF, domicilio]_ |
| **Fecha de creación (inicio)** | 2026-08-07 |
| **Fecha de finalización (versión depositada)** | 2026-09-14 |
| **Lugar de elaboración** | España |
| **Lenguaje de programación** | JavaScript (JSX / React 18), CSS |
| **Plataforma objetivo** | Navegador web moderno (SPA); construcción con Vite |
| **Tamaño** | 4.344 líneas físicas de código fuente (SLOC) |
| **Valoración de esfuerzo (COCOMO básico, SLOCCount)** | 0,94 persona-años (11,2 persona-meses); coste estimado de desarrollo 126.307 USD (salario medio 56.286 USD/año, factor 2,40) |

> Métricas generadas con SLOCCount 2.26 (David A. Wheeler, GPL). SLOCCount no
> reconoce la extensión `.jsx` ni `.css`; los ficheros JSX/CSS se han contado
> con SLOCCount nativo tras renombrar la extensión a `.c` (contador ANSI-C,
> misma sintaxis de comentarios `//` y `/* */`). Anclaje de validación: los
> ficheros `.js` contados nativamente por SLOCCount dan el mismo resultado al
> renombrarlos a `.c` (336 = 336 SLOC), lo que prueba que el renombrado
> preserva la semántica de conteo. Fórmula COCOMO básica
> `PM = 2,4 · KSLOC^1,05`. La valoración es una estimación de coste de
> re-desarrollo, no un precio de mercado. El script reproducible
> `docs/propiedad-intelectual/sloc_count.py` contiene el conteo por fichero
> y la valoración.

---

### 2. Descripción general

El frontend es una aplicación web de página única (SPA) que constituye la
interfaz de usuario del instrumento cuántico QSSC. Permite a un operador:

- configurar y ejecutar circuitos cuánticos (Bell, Shor, Grover, QFT, caminos
  personalizados) sobre la matriz de 64 píxeles, en modo simulador o modo
  hardware real;
- calibrar la matriz píxel a píxel (barrido ODMR, conteo de qubits resueltos por
  píxel, anchura de registro, frecuencias de los qubits medidos);
- visualizar los resultados (histogramas de conteo con marcado del resultado,
  tablas por píxel, derivaciones modales) y los parámetros físicos;
- operar la sensórica (magnetómetro vectorial de 64 píxeles, gradiometría
  tensorial, compás cuántico, giro, sensores ferro/nav/thermal/current);
- gestionar el estado del hardware real (detección de FPGA, verbos de control,
  panel de estado, parámetros de ejecución).

La aplicación consume una API REST (backend) y se sirve como ficheros estáticos
construidos por Vite, comunicándose con el backend por una pasarela proxy en
desarrollo y por el mismo origen en producción.

---

### 3. Arquitectura y módulos

El código fuente reside en `frontend/src/` y consta de seis ficheros:

- **`App.jsx`** (1.833 SLOC) — componente raíz y orquestador de estado. Mantiene
  el estado global de la aplicación: plataforma de defecto (NV/SiC/hBN),
  temperatura, modo de ejecución (simulador/hardware), modo de píxel
  (registro/ensamble), número de qubits por píxel, modos de lectura
  (super-resolución, Bayesiano, denoise por primas, pila anti-ruido), y los
  parámetros de los algoritmos. Construye los cuerpos de petición (`buildBody`)
  hacia los endpoints de circuito, circuito personalizado y ejecución paralela,
  y renderiza el panel de controles globales y la distribución de pestañas.
- **`components.jsx`** (1.628 SLOC) — biblioteca de componentes de la interfaz:
  panel de calibración (con flujo de etapas, marcado de qubits escaneados y
  modal de frecuencias por píxel), tarjeta de calibración paralela de 64 píxeles
  (con selector de modo de lectura, preset anti-ruido, estadísticas de
  canales espaciales, ganancia de filtro y límites físicos), panel de
  sensores, panel FPGA (con botón de flasheo, auto-curación de estado y
  referencia de verbos), histograma de medición con marcado del resultado y
  modal de derivación, y selectores de modo/denoise.
- **`derive.js`** (281 SLOC) — utilidades de derivación: explica cómo se obtiene
  cada resultado (factorización, período, suma, amplitud) a partir de los
  conteos, con renderizado modal paso a paso.
- **`api.js`** (55 SLOC) — capa de acceso a la API REST: funciones `get`/`post`
  con manejo de errores y los puntos finales (`run`, `runCustom`,
  `runParallel`, `calibrate`, `calibrateArray`, `modes`, `profiles`, sensores,
  FPGA).
- **`styles.css`** (538 SLOC) — estilos de la aplicación, con soporte de tema
  claro/oscuro y diseño responsivo.
- **`main.jsx`** (9 SLOC) — punto de entrada que monta el componente raíz en el
  DOM.

---

### 4. Tecnologías y lenguajes

| Lenguaje | SLOC | Porcentaje |
|---|---|---|
| JavaScript / JSX | 3.806 | 87,6 % |
| CSS | 538 | 12,4 % |
| **Total** | **4.344** | **100 %** |

Stack: React 18 (hooks), Vite 5 (bundler y dev-server), JSX, CSS nativo con
variables de tema. Sin dependencias de UI pesadas: la interfaz se construye con
componentes propios.

---

### 5. Funcionalidades destacadas — metodologías expuestas al operador

1. **Conmutador simulador / hardware real**: el operador selecciona la fuente
   de ejecución; en modo real la interfaz detecta la FPGA y ofrece los verbos
   de control (ID/STATUS/RESET/ODMR_SWEEP), sin enmascarar un resultado
   sintético como real — la UI distingue y comunica explícitamente la fuente
   de cada resultado.
2. **Calibración paralela de 64 píxeles (barrido ODMR)**: lanza el barrido
   ODMR del array, muestra la anchura de registro medida por píxel, el número
   de qubits escaneados, las frecuencias ODMR y un modal de
   pines/frecuencias. Refleja el lazo calibración → circuito: el N medido
   alimenta la rejilla del registro.
3. **Super-resolución ODMR**: factor de super-resolución ajustable
   (deconvolución sparse OMP de líneas solapadas), conmutable desde el panel
   de modos de lectura.
4. **Lectura Bayesiana cuántico-limitada**: intervalo creíble unilateral
   ajustable 0,01–0,99 con corrección Bonferroni; lectura óptima por píxel.
5. **Denoise por primas + pila anti-ruido**: conmutador de denoise por
   primas-coprimas (lock-in de período primo) y pila anti-ruido clásica 300 K
   en modo pasivo / activo / completo, con ganancia de filtro y límites
   físicos visibles.
6. **Centroiding sub-píxel e integración N-shot**: selector de régimen de
   readout con centroiding por charge-sharing (imagen detector 64→64·S) e
   integración N-shot del lock-in (eleva el ancho medido ~+30 %/píxel sin
   cambio de hardware).
7. **Panel de sensórica cuántica**: magnetómetro vectorial 4 ejes ⟨111⟩,
   gradiometría tensorial con ajuste de dipolo, compás cuántico, giro y
   sensores auxiliares (ferro/nav/thermal/current).
8. **Modo de píxel registro / ensamble**: selector que refleja el modelo
   físico (registro de N qubits dentro del píxel con gate dipolo K_N vs.
   ensamble de 1 qubit por píxel a 300 K), con el N medido por la
   calibración.
9. **Regulador de gradiente**: control del DAC de la bobina de gradiente con
   tope al máximo de la bobina, valor por defecto de ~70 líneas/píxel, y
   visualización del tope resoluble + código DAC.
10. **Marcado de resultados y derivación**: el histograma marca el resultado
    (factor, período, estado suma) y un modal explica paso a paso su
    derivación desde los conteos cuánticos — destinado a la reproducibilidad
    experimental.
11. **Ejecución paralela 64-píxel**: lanzamiento de la computación paralela
    (Shor coprimo distribuido, muestreo 64×) con resultado marcado y
    derivación al estilo del píxel único.

---

### 6. Originalidad y características distintivas

- Interfaz diseñada específicamente para **operar un instrumento cuántico de
  matriz de 64 píxeles a temperatura ambiente**, no un dashboard genérico.
- **Integración de primitivas cuánticas de alto nivel** (calibración ODMR,
  modos de lectura super-resolución/Bayesiana, sensórica tensorial) en la
  interfaz de operación, exponiendo la física del dispositivo al operador.
- **Transparencia simulador/hardware**: la UI distingue y comunica
  explícitamente la fuente de cada resultado, evitando relabel silencioso.
- **Derivación explicada**: cada resultado cuántico muestra su cadena de
  derivación, destinado a la reproducibilidad experimental.

---

### 7. Estado de verificación

- La construcción de producción (`vite build`) compila sin errores (bundle
  ~311 KB / 92 KB gzip).
- Verificación local API + frontend antes de cada entrega.
- La lógica de derivación y los constructores de petición se ejercitan vía la
  suite de tests del backend (TestClient sobre la API real).

---

### 8. Entorno de ejecución

- **Navegador**: navegador web moderno con soporte ES2020.
- **Construcción**: `npm install` + `npm run build` (Vite) → `dist/` estático.
- **Desarrollo**: `npm run dev` (Vite, puerto 5173) con proxy a la API
  (puerto 8011).
- **Backend**: requiere el backend QSSC (API REST) sirviendo los endpoints
  consumidos por `api.js`.

---

### 9. Lista de ficheros fuente depositados

```
frontend/src/App.jsx
frontend/src/api.js
frontend/src/components.jsx
frontend/src/derive.js
frontend/src/main.jsx
frontend/src/styles.css
```

---

_Fecha de la memoria: 2026-09-14. Esta memoria descriptiva acompaña al depósito
del código fuente en el Repositorio Oficial del Proyecto QSSC y se destina a su
inscripción en el Registro de la Propiedad Intelectual._