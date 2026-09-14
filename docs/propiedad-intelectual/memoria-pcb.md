# Memoria descriptiva — Programa de ordenador

## Unidad: Scripts de automatización PCB — Toolchain EDA KiCad headless

---

### 1. Datos de la obra

| Campo | Valor |
|---|---|
| **Título** | QSSC — Toolchain EDA headless para la generación, enrutado y verificación de las placas PCB KiCad de la plataforma cuántica |
| **Tipo de obra** | Programa de ordenador (Art. 96.1 del Texto Refundido de la Ley de Propiedad Intelectual, RDLeg 1/1996) |
| **Titular / Autor** | _[A completar por el titular: nombre/apellidos o razón social, NIF/CIF, domicilio]_ |
| **Fecha de creación (inicio)** | 2026-08-07 |
| **Fecha de finalización (versión depositada)** | 2026-09-14 |
| **Lugar de elaboración** | España |
| **Lenguaje de programación** | Python 3.12 (manipulación programática de ficheros KiCad `.kicad_pcb` / `.kicad_sch` / `.kicad_pro` / `.kicad_sym`) |
| **Plataforma objetivo** | Linux; `kicad-cli` (KiCad 8.0) para DRC/ERC; FreeRouting 2.4.1 para el enrutado automático |
| **Tamaño** | 5.918 líneas físicas de código fuente (SLOC) — contaje nativo SLOCCount |
| **Valoración de esfuerzo (COCOMO básico, SLOCCount)** | 1,29 persona-años (15,5 persona-meses); coste estimado de desarrollo 174.753 USD (salario medio 56.286 USD/año, factor 2,40) |

> Métricas generadas con SLOCCount 2.26 (David A. Wheeler, GPL), contaje nativo
> sobre los 23 ficheros Python del toolchain (SLOCCount reconoce `.py`
> nativamente, sin renombrado). Verificación cruzada con cloc 2.11: 5.937 líneas
> de código, diferencia +19 (+0,3 %). Fórmula COCOMO básica
> `PM = 2,4 · KSLOC^1,05`. La valoración es una estimación de coste de
> desarrollo, no un precio de mercado. El script reproducible
> `docs/propiedad-intelectual/sloc_count.py` contiene el conteo por fichero y la
> comparación SLOCCount/cloc de las cinco áreas.

---

### 2. Descripción general

Esta unidad es el toolchain EDA headless que genera, enruta y verifica las dos
placas PCB del QSSC (placa principal 6 capas 150×118 mm y placa cabezal 6 capas)
sin abrir la GUI de KiCad.

El toolchain produce los ficheros `.kicad_pcb`, `.kicad_sch`, `.kicad_pro` y
`.kicad_sym` por código —netlist, footprints, apilado de capas, posicionado,
fanout del bus del dado, planos de masa— y luego ejecuta el enrutado de señales,
el vuelcado/rellenado de zonas, el enrutador automático FreeRouting y el cierre
de split-nets residuales, todo de forma programática y reproducible. La
verificación (DRC/ERC, auditoría de conectividad y netlist) se ejecuta con
`kicad-cli` y scripts de auditoría propios, de modo que las dos placas alcanzan
DRC 0/0/0 de extremo a extremo sin intervención manual en la GUI.

---

### 3. Arquitectura y módulos

23 scripts Python en `tools/`, organizados en cinco grupos funcionales:

**Generación del diseño (netlist + footprints + capas)**

- `gen_qssc_kicad.py` (1.671 SLOC) — generador principal: construye ambas placas
  (principal + cabezal), netlist por datasheet, footprints, apilado de 6 capas,
  posicionado, zócalo SBT Ironwood del dado, bus del dado a 2,5 mm de pitch.
- `gen_bom.py` (149 SLOC) — extrae la BOM desde los componentes reales de la
  placa generada.

**Enrutado de señales y fanout**

- `route_signals.py` (984 SLOC) — enrutado de señales de 1ª pasada: fanout del
  dado derivado de la capa de pads (`_chip_fanout`) con 0,80 mm de clearance,
  bus de escape despejado del campo de 0,30 mm para FreeRouting.
- `route_qssc_pcb.py` (645 SLOC) — orquestador: sync → inject → copy_zones →
  fill → maze3d, pipeline por placa.
- `route_grad_nets.py` (167 SLOC) — enrutado dedicado de las redes de la bobina
  de gradiente (cobre DRC-clean, net-tie).
- `freeroute.py` (196 SLOC) — envoltura de FreeRouting 2.4.1 vía la API DSN/SES
  de pcbnew; `-mt 1` (no `-mt 0`, SES vacío); `unconnected_items:ignore`.
- `maze_bridge_3d.py` (439 SLOC) — puente A* 3D multi-capa (B.Cu + In3) para
  cerrar split-nets difíciles; distancia métrica real.
- `maze_bridge_2d.py` (280 SLOC) — puente A* 2D dentro de una capa.

**Zonas, pistas y residuales**

- `close_residuals.py` (626 SLOC) — cierra split-nets residuales tras
  FreeRouting; el que más SLOC aporta al cierre de conectividad.
- `inject_base_routing.py` (118 SLOC) — inyecta el enrutado base antes del
  autoruter.
- `copy_zones.py` (60 SLOC) — copia zonas entre placas/plantillas.
- `fill_zones.py` (16 SLOC) — vuelca (pour) las zonas de masa.
- `widen_tracks.py` (37 SLOC) — ensancha pistas de potencia.
- `sync_placement.py` (59 SLOC) — sincroniza el posicionado entre esquemático y
  PCB.
- `clear_net.py` (20 SLOC) — limpia una red concreta antes de re-enrutar.
- `add_grad_fanout.py` (46 SLOC) — añade el fanout del gradiente.
- `usb_c_reversible_tie.py` (103 SLOC) — resolución reversible del USB-C.

**Auditoría y verificación**

- `audit_connections.py` (121 SLOC) — auditoría de conectividad: split-nets,
  pines colgantes, por placa.
- `audit_netlist.py` (58 SLOC) — auditoría del netlist contra el datasheet.

**Helpers internos de compatibilidad KiCad**

- `_kicad_compat.py` (36), `_chip_map.py` (58), `_audit_pins.py` (17),
  `_grep_ep.py` (12) — utilidades de parsing/escritura KiCad y mapeo del dado
  compartidas por los scripts anteriores.

---

### 4. Tecnologías y lenguajes

| Lenguaje | SLOC | Porcentaje |
|---|---|---|
| Python 3.12 | 5.918 | 100 % |
| **Total** | **5.918** | **100 %** |

Herramientas externas invocadas: `kicad-cli` (KiCad 8.0, DRC/ERC headless),
FreeRouting 2.4.1 (autoruter, vía DSN/SES), pcbnew Python API (swig). Sin GUI:
todo el flujo es reproducible desde línea de comandos.

---

### 5. Funcionalidades destacadas — metodologías EDA implementadas

1. **Generación headless del diseño completo** (`gen_qssc_kicad.py`): construye
   ambas placas (principal 150×118 mm 6 capas + cabezal 6 capas) por código —
   netlist por datasheet, footprints, apilado, posicionado, zócalo SBT Ironwood
   del dado de 130 pads a 2,5 mm, bus del dado. Sin abrir la GUI.
2. **Fanout del dado derivado de la capa de pads** (`_chip_fanout`): fanout
   automático con 0,80 mm de clearance, despejando el campo de 0,30 mm. HDI via
   floors 0,20/0,05.
3. **Enrutado por etapas** (`route_qssc_pcb.py`): pipeline sync → inject →
   copy_zones → fill → maze3d por placa; cada etapa inspeccionable y reversible.
4. **FreeRouting 2.4.1 vía API DSN/SES de pcbnew** (`freeroute.py`): `-mt 1`
   (no `-mt 0`: SES vacío y cuelga), `unconnected_items:ignore`, ciclos
   incrementales cortos (MP=4-6) que guardan el board cada ciclo.
5. **Puente A* 3D multi-capa** (`maze_bridge_3d.py`): cierra split-nets por
   A* 3D a través de B.Cu + In3 con distancia métrica real; distingue las 3
   violaciones reales de KiCad de los 704 internal-violations de FreeRouting.
6. **Cierre de split-nets residuales** (`close_residuals.py`): cierra los
   split-nets uno a uno hasta 0 split / 0 dangling.
7. **Cobre del gradiente DRC-clean (net-tie)** (`route_grad_nets.py`):
   enrutado dedicado de la bobina de gradiente con aproximación net-tie para
   cobre real que pasa el DRC.
8. **Auditoría de conectividad + netlist por datasheet**
   (`audit_connections.py` / `audit_netlist.py`): split-nets, pines colgantes y
   correspondencia del netlist con el datasheet, por placa.
9. **BOM desde los componentes reales** (`gen_bom.py`): extrae la BOM desde los
   componentes efectivamente colocados, no de una lista manual.

---

### 6. Originalidad y características distintivas

- Toolchain EDA **100 % headless y reproducible**: las dos placas se generan,
  enrutan y verifican a DRC 0/0/0 sin abrir la GUI de KiCad, de extremo a
  extremo por código.
- **Generación por código del netlist + footprints + apilado** de un
  instrumento cuántico de matriz 64-píxel con dado de 130 pads a 2,5 mm y
  zócalo SBT, no captura manual de un esquema.
- **Integración del autoruter FreeRouting vía la API DSN/SES** de pcbnew con
  ciclos incrementales que guardan el board, evitando el cuelgue del SES vacío
  de `-mt 0`.
- **Cierre determinista de split-nets** con puente A* 3D multi-capa,
  distinguiendo las violaciones reales de KiCad de las internas de FreeRouting.

---

### 7. Estado de verificación

- Ambas placas alcanzan **DRC 0/0/0** (0 errores, 0 avisos, 0 split-nets)
  verificadas con `kicad-cli` headless.
- Auditoría de conectividad y pin-audit (scripts propios) en verde; integradas
  en el CI (`pcb.yml`).
- Reproducible: regenerar ambas placas + enrutar + DRC desde línea de comandos
  sin intervención GUI.

---

### 8. Entorno de ejecución

- **Python**: 3.12 con el mismo entorno virtual del proyecto (`.venv`).
- **KiCad**: 8.0 (PPA `kicad/kicad-8.0-releases`); `kicad-cli` para DRC/ERC
  headless.
- **Autoruter**: FreeRouting 2.4.1 (vía API DSN/SES de pcbnew, `-mt 1`).
- **Salida**: ficheros `.kicad_pcb / .kicad_sch / .kicad_pro / .kicad_sym` +
  BOM para fabricación.
- **Instrucciones de instalación y uso**: el fichero `README.md` del
  repositorio (sección _Quick start_) contiene la guía canónica de instalación
  del entorno y ejecución del toolchain; la sección _Fabrication protocol_ cubre
  la fabricación de las placas.

---

### 9. Lista de ficheros fuente depositados

```
tools/_audit_pins.py
tools/_chip_map.py
tools/_grep_ep.py
tools/_kicad_compat.py
tools/add_grad_fanout.py
tools/audit_connections.py
tools/audit_netlist.py
tools/clear_net.py
tools/close_residuals.py
tools/copy_zones.py
tools/fill_zones.py
tools/freeroute.py
tools/gen_bom.py
tools/gen_qssc_kicad.py
tools/inject_base_routing.py
tools/maze_bridge_2d.py
tools/maze_bridge_3d.py
tools/route_grad_nets.py
tools/route_qssc_pcb.py
tools/route_signals.py
tools/sync_placement.py
tools/usb_c_reversible_tie.py
tools/widen_tracks.py
```


---

## Algoritmos implementados en esta unidad

El toolchain EDA implementa algoritmos de diseño electrónico (no algoritmos
cuánticos); produce el hardware sobre el que corren los algoritmos de las demás
unidades.

### Algoritmos originales del toolchain QSSC

- **Generación headless del diseño completo** (propio) — `tools/gen_qssc_kicad.py`. Netlist + footprints + apilado + posicionado por código, sin GUI.
- **Fanout del dado derivado de la capa de pads** (propio) — `tools/route_signals.py` (`_chip_fanout`). 0,80 mm de clearance, campo de 0,30 mm despejado.
- **Enrutado por etapas (sync → inject → copy_zones → fill → maze3d)** (propio) — `tools/route_qssc_pcb.py`. Pipeline orquestado por placa.
- **FreeRouting 2.4.1 vía API DSN/SES de pcbnew** (propio) — `tools/freeroute.py`. `-mt 1`, ciclos incrementales MP=4-6 que guardan el board.
- **Puente A* 3D multi-capa** (propio; A* según Hart, Nilsson & Raphael, 1968) — `tools/maze_bridge_3d.py`. Cierra split-nets por B.Cu + In3 con distancia métrica real.
- **Cierre de split-nets residuales** (propio) — `tools/close_residuals.py`. Cierre uno a uno hasta 0 split / 0 dangling.
- **Cobre del gradiente DRC-clean (net-tie)** (propio) — `tools/route_grad_nets.py`.
- **Auditoría de conectividad + netlist por datasheet** (propio) — `tools/audit_connections.py`, `audit_netlist.py`.
- **BOM desde los componentes reales** (propio) — `tools/gen_bom.py`.

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
