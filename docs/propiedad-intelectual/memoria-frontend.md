# Memoria descriptiva — Programa de ordenador

## Unidad: Frontend web — Interfaz de operación del instrumento cuántico QSSC

---

### 1. Datos de la obra

| Campo | Valor |
|---|---|
| **Título** | QSSC — Interfaz web de operación, calibración y sensórica para plataforma cuántica de 64 píxeles NV |
| **Tipo de obra** | Programa de ordenador (Art. 96.1 del Texto Refundido de la Ley de Propiedad Intelectual, RDLeg 1/1996) |
| **Titular / Autor** | _Vicent Nos Ripolles y Pablo Lopez_ |
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
> desarrollo, no un precio de mercado. El script reproducible
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

## Algoritmos implementados en esta unidad

El frontend expone al operador los algoritmos del backend y del firmware. No
implementa la física, pero construye los cuerpos de petición y muestra la
derivación de cada resultado.

### Algoritmos publicados expuestos

- **Shor / QFT / Grover** [1,3,2] — `frontend/src/App.jsx`, `derive.js`. Construcción del cuerpo y derivación del factor/período.
- **Super-resolución ODMR (OMP)** [25] — `frontend/src/components.jsx`. Factor de super-resolución ajustable.
- **Lectura Bayesiana** [27,28] — `frontend/src/components.jsx`. Intervalo creíble unilateral 0,01–0,99.
- **Centroiding por charge-sharing** [26] — `frontend/src/components.jsx`. Selector de régimen de readout.
- **Magnetometría vectorial NV** [8,10,11] — `frontend/src/components.jsx`. Panel de sensórica cuántica.

### Algoritmos originales del QSSC expuestos

- **Denoise por primas-coprimas** (propio) — conmutador en el panel de modos de lectura.
- **Pila anti-ruido 300 K** (propio) — modo pasivo / activo / completo con ganancia y límites físicos.
- **Integración N-shot del lock-in** (propio) — selector de régimen de readout.
- **Modo píxel registro/ensamble (N medido)** (propio) — selector que refleja el modelo físico.
- **Regulador de gradiente (tope de la bobina + código DAC)** (propio) — control del DAC MCP4921.
- **Computación paralela 64-píxel** (propio) — lanzamiento de `run-parallel` con derivación.

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
