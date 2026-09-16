# Quantum Solid State Chip (Room Temperature) — Fabrication Protocol · SiC variant

**Project:** QSSC (Quantum Solid State Chip, room-temperature demonstrator)
**Document type:** Laboratory fabrication protocol (standalone variant)
**Version:** 1.0
**Variant:** Silicon-carbide silicon-vacancy (V_Si, V2 line in 4H-SiC)
**Scope:** Same chip architecture as the hBN base protocol — (1) an electrically-pumped quantum-dot emitter array, (2) SU-8/PDMS polymer waveguides, (3) an on-chip 2D-material (graphene/MoS₂) photodetector array with FPGA readout and software pulse-capture calibration — but the **middle spin-host block is a commercial 4H-SiC wafer hosting V_Si centres** (S=3/2, 2|D| ≈ 70 MHz, ZPL ≈ 916 nm, T₂ ≈ 40 µs at 300 K). Unlike the diamond variant (which reuses the green CQD emitter unchanged), the SiC variant requires **three real changes** to the optical chain: (i) the on-chip emitter must shift from green (532 nm) to **red/NIR (~730–760 nm)** to pump V_Si — done with an **on-chip CuInS₂/ZnS (CIS) colloidal quantum-dot LED, not a big diode**; (ii) the optical filter must shift from a 650 nm long-pass to an **850 nm long-pass** (the 650 would pass the 730–760 pump); (iii) the detector leans on **graphene** (the 916 nm PL is in the IR where MoS₂'s monolayer gain drops).

> **Note.** The SiC variant is the **most reworked** of the three: the host is easier (commercial wafer, defects as-grown + irradiation, ~20× better coherence than hBN), but the on-chip emitter is harder. **Carbon CQDs (citrato+urea) cannot reach 730–760 nm** — their ceiling is green ~540 nm. Pumping V_Si on-chip requires **semiconductor colloidal QDs (CuInS₂/ZnS, "CIS")**, a hot-injection colloidal synthesis with indium/copper/sulfur precursors under inert atmosphere. This is a real step up in chemistry from boiling citric acid, but it keeps the **on-chip, no-big-diode** constraint the user requires. The divacancy (V_SiV_C, D ≈ 1.3 GHz, T₂ ~ ms with DD, ZPL ~1.1 µm) is noted as an optional upgrade but is **not** the primary protocol here (the QSSC simulator profiles V_Si, 70 MHz).

> **On-chip emitter (no big diodes).** This variant uses an **on-chip CIS/ZnS quantum-dot LED** as the ~730–760 nm pump. No external laser diode. A commercial 730/808 nm diode would be simpler but is explicitly excluded by the design constraint.

---

## 1. Abstract

This document specifies a complete, equipment-minimal fabrication protocol for a room-temperature solid-state quantum photonic chip (QSSC) built on a **4H-SiC wafer hosting V_Si centres**. The chip combines three functional blocks on a single 25 × 25 mm² carrier: (i) a **red/NIR (~730–760 nm)** CuInS₂/ZnS colloidal quantum-dot (CIS-QD) emitter array in a polymeric QLED stack (ITO/PEDOT:PSS/poly-TPD/CIS-ZnS/ZnO/Ag) that pumps the V_Si (optical excitation band below its 916 nm ZPL) — no external pump laser; (ii) a **commercial 4H-SiC wafer** with V_Si (V2) centres (2|D| ≈ 70 MHz, ZPL 916 nm, T₂ ≈ 40 µs at 300 K), defects formed by as-grown + electron/proton irradiation + anneal; and (iii) SU-8/PDMS waveguides routing the 916 nm V_Si PL through an **850 nm long-pass filter** to an **on-chip graphene/MoS₂ photodetector array** read directly by the FPGA, with software pulse-capture calibration (no camera, no alignment jig). A Peltier holds the wafer at 288–300 K. Equipment footprint ~USD 3,500–4,500 (the CIS hot-injection setup adds a Schlenk line / Ar glovebag over the carbon-CQD autoclave).

---

## 2. Introduction & Scientific Basis

### 2.1 Why SiC (V_Si) for the same chip

The silicon vacancy V_Si (V2 line) in 4H-SiC is a **spin-3/2** (S=3/2) defect with zero-field splitting **2|D| ≈ 70 MHz**, ZPL ≈ 916 nm, optically addressable and coherently controlled at room temperature. Its ensemble Hahn-echo coherence is **T₂ ≈ 40 µs at 300 K** (natural abundance), extending to ~100 µs in isotopically purified 4H-SiC and further with dynamical decoupling — roughly **20× better than hBN V_B⁻** (2 µs). Critically, the host is a **commercial semiconductor wafer** (4H-SiC power-electronics wafers are mass-produced and cheap), and the defects are created by **standard irradiation + anneal** — no liquid exfoliation, no 2D transfer. This is the **easiest host to fabricate** in the QSSC family.

### 2.2 Why the emitter must change (and why not carbon CQDs)

V_Si is optically excited **below its 916 nm ZPL** — the standard pump is a **730 nm** red laser (or 808 nm, or white light). The hBN chip's green (532 nm) CQD emitter is **sub-optimal** for V_Si (532 nm is sub-bandgap for SiC and only weakly excites the defect). **Carbon quantum dots (citric-acid+urea route) cannot reach 730–760 nm** — their realistic ceiling is green ~540 nm; "red carbon dots" are an active research area with low, unreproducible quantum yield. To keep the **on-chip, no-big-diode** constraint, the emitter is switched to **CuInS₂/ZnS (CIS) colloidal QDs**, a Cd/Pb-free semiconductor-dot system that emits tunably across **550–800+ nm** and is the established "green" (heavy-metal-free) NIR-QLED material. CIS QDs at ~730–760 nm pump V_Si directly.

### 2.3 Why the filter and detector must change

V_Si PL is at **916 nm** (NIR). The hBN chip's **650 nm red long-pass would pass the 730–760 nm pump** (730–760 > 650) and fail to separate pump from signal. So the filter shifts to an **850 nm long-pass** (passes 916 nm PL, blocks 730–760 nm pump). On the detector side, the 916 nm PL is in the IR: monolayer MoS₂ (bandgap ~690 nm) does **not** absorb 916 nm, so the MoS₂ photogating gain is lost and the readout relies on **broadband graphene** (or multilayer MoS₂, bandgap ~1.2 eV ≈ 1030 nm, which does absorb 916). Graphene absorbs 916 nm comfortably — the detector works, but the channel is graphene-led here, unlike the hBN/diamond cases where MoS₂ contributes gain.

### 2.4 Architecture overview

```
                ┌─────────────────────────────────────────────┐
   top Ag cathode (silver-paste syringe) ─────────────────── │
                │  ZnO electron-transport layer (≈40 nm)     │  ← Section 1 (CHANGED)
                │  CIS-ZnS QD emissive layer (≈80 nm,         │
                │       red/NIR ~730–760 nm → pumps V_Si)     │
                │  poly-TPD hole-transport (≈40 nm)           │
                │  PEDOT:PSS hole-injection (≈40 nm)          │
                │  bottom ITO anode on glass/PET             │
   ─────────────┴─────────────────────────────────────────────┘
                       ↓ optical pumping / EL (730–760 nm)
   ┌─────────────────────────────────────────────┐
   │  4H-SiC wafer (bulk, ~0.5 mm)               │  ← Section 2 (REPLACES hBN)
   │  with V_Si (V2) centres (2|D|≈70 MHz, ZPL 916)│
   │  emitter + detector sit ON TOP (bulk 3D)    │
   ──────────────────────────────────────────────┘
        ↘ waveguide coupling ↙  (850 nm long-pass filter in path)
   ┌─────────── SU-8/PDMS waveguides ───────────┐       ← Section 3/4 (CHANGED filter)
   │  → graphene-led / MoS₂ photodetector array │
   └─────────────────────────────────────────────┘
   Peltier (TEC) clamped under Si carrier      ← Section 4
```

---

## 3. Materials & Bill of Materials (BOM) — deltas from the hBN base

### 3.1 Added / substituted reagents

| Item | Spec / source | Qty | Approx. cost |
|---|---|---|---|
| **4H-SiC wafer** (semi-insulating or N-doped, 2" or 5×5 mm sample, double-side polished) | WolSpeed / II-VI / Alibaba 4H-SiC | 1 sample | $30–$120 |
| (Optional, if wafer has no V_Si) electron/proton irradiation service | — | 1 run | $0 (university) – $200 |
| **Copper(I) iodide (CuI, 99.999 %)** | Sigma 205568 | 5 g | $40 |
| **Indium(III) acetate (In(OAc)₃, 99.99 %)** | Sigma 510270 | 5 g | $60 |
| **1-Dodecanethiol (≥98 %)** — sulfur source / ligand | Sigma 471364 | 100 mL | $30 |
| **Oleylamine (70 %, technical)** — high-boiling ligand/solvent | Sigma O7805 | 100 mL | $30 |
| **Oleic acid (90 %)** — co-ligand | Sigma O1008 | 100 mL | $20 |
| **1-Octadecene (90 %)** — non-coordinating solvent | Sigma O806 | 100 mL | $25 |
| **Zinc stearate (or zinc acetate + sulfur)** — ZnS shell precursor | Sigma | 10 g | $25 |
| **poly-TPD** (poly[N,N'-bis(4-butylphenyl)-N,N'-bis(phenyl)benzidine]) — non-polar HTL | Sigma 902388 / Ossila | 1 g | $60 |
| (Optional) **TFB** or **PVK** alternative non-polar HTL/host | Ossila | 1 g | $50 |
| **850 nm long-pass optical filter** (photographic / glass) | midopt / Edmund / Amazon | 1 | $15–$40 |
| Anisole / toluene / hexane (CIS-QD solvents) | — | 1 L each | $40 |

### 3.2 Removed from hBN BOM (not needed)

- hBN powder, NMP, PMMA-as-hBN-passivation. **Carbon CQD reagents (citric acid, urea) are NOT used** for the emitter (replaced by CIS QD reagents above) — though you may still keep a small carbon-CQD batch as a green reference if desired.

### 3.3 Equipment deltas

| Item | Function | Approx. cost |
|---|---|---|
| **Schlenk line / vacuum-Ar manifold** (or Ar glovebag + needle-valve flask) | inert-atmosphere hot-injection for CIS QDs | $150–$300 (used Schlenk line) / $40 glovebag |
| **3-neck round-bottom flask + heating mantle + thermocouple** (to 300 °C) | hot-injection reactor | $60 |
| Tube furnace (to 1200 °C) | V_Si activation anneal (800–1200 °C) — *needed unless you buy V_Si-ready wafer* | $400–$700 |
| Glovebox (optional) | oxygen/moisture-free CIS handling | (optional, $0 if using glovebag) |

Everything else (hotplate, autoclave, sonicator, spin coater, UV, Peltier, sourcemeter, lock-in, ADF4351) is unchanged. The 650 nm filter is **replaced** by the 850 nm long-pass.

---

## Section 1 — CIS/ZnS Quantum-Dot Emitter Array (CHANGED: red/NIR, not green)

**Goal:** Synthesize **CuInS₂/ZnS (CIS) colloidal quantum dots** emitting ~730–760 nm and integrate them into a patterned ITO/PEDOT:PSS/poly-TPD/CIS-ZnS/ZnO/Ag electroluminescent QLED array (8 × 8 pixels, 1 mm² each) on a 25 × 25 mm² ITO-glass substrate. The red/NIR emitter doubles as the on-chip pump for V_Si (no external 730 nm laser). Target: EL peak 730–760 nm, turn-on 3–8 V, detectable ASE/edge narrowing under overdrive.

> **Note.** CIS QLEDs are an established NIR-QLED family (Cd/Pb-free). True electrically-pumped *lasing* from CIS QDs is **not claimed** — as with the carbon CQD chip, the target is **electroluminescence and ASE / high-brightness edge emission**, used as a compact pump. This synthesis is **more demanding than the carbon-CQD autoclave route** (it needs an inert atmosphere and hot injection), but it is the only way to reach 730–760 nm on-chip without a discrete diode laser.

### 1.1 Hot-injection synthesis of CuInS₂/ZnS (CIS) QDs

This is the standard **CuInS₂ core + ZnS shell** colloidal route, size-tuned to ~730–760 nm (larger cores → redder emission).

1. **Charge (under Ar).** In a 3-neck flask on the Schlenk line (or Ar glovebag), load **0.5 mmol CuI (95 mg) + 1.0 mmol In(OAc)₃ (292 mg)** in **5 mL oleylamine + 5 mL 1-octadecene** + **1 mL oleic acid**. Degas at **110 °C, 30 min** under vacuum, then back-fill with **Ar**.
2. **Heat to growth temp.** Ramp to **230 °C** under Ar with stirring.
3. **Inject sulfur.** Swiftly inject **1 mL 1-dodecanethiol** (or 2 mL of 0.1 M S in oleylamine). The solution darkens (nucleation). Hold at **230 °C, 10–30 min** — **longer time / higher T → larger cores → redder emission**. For ~730–760 nm, target ~20 min at 230–240 °C.
4. **ZnS shelling (passivation).** Cool to ~200 °C. Inject **2 mL of 0.2 M zinc stearate in ODE** (or zinc acetate + sulfur in oleylamine). Hold **200 °C, 20 min** to grow a ~1–2 monolayer ZnS shell. The shell raises PL quantum yield to **30–60 %** and shifts emission slightly red.
5. **Cool + purify.** Cool to < 60 °C. Precipitate with **ethanol/acetone** (3× volume), centrifuge **5000 rpm, 5 min**, discard supernatant. Redisperse pellet in **hexane or toluene** (~10 mg/mL). Repeat precipitation/redispersion 2× to remove free ligand.
6. **Size/colour check.** Dilute 1:100 in hexane; under 365 nm UV expect **deep-red/NIR emission** (~700–780 nm). Approximate QY vs a standard. For 730–760 nm, cores are ~3.5–4.5 nm; adjust growth time in step 3 to tune.
7. **Wavelength tuning.** CIS emission is tuned by **core size** (growth T/time) and the Cu:In ratio: lower Cu:In (e.g. 1:4) blue-shifts toward 600 nm; stoichiometric 1:2 with larger cores + ZnS reaches ~730–800 nm. **Target ~730–760 nm** so the 850 nm long-pass filter cleanly separates pump from the 916 nm PL.

### 1.2 CIS-QD electroluminescent ink (non-polar host)

8. **Composite.** Dissolve **poly-TPD** (the non-polar hole-transport layer, also acts as a soft host) in **chlorobenzene or toluene** at **8 mg/mL**. Add **CIS-ZnS QDs** to ~**10–15 mg/mL** final QD concentration, sonicate 5 min, filter 0.45 µm PTFE. (Unlike the carbon-CQD/PVA aqueous ink, CIS QDs are hydrophobic/oleylamine-capped and **do not disperse in water/PVA** — use a non-polar aromatic solvent + poly-TPD host. A ligand-exchange to a short thiol can make CIS QDs polar-dispersible for a PVA host, but it usually quenches PL; the non-polar QLED stack is the higher-yield route.)

### 1.3 Substrate preparation (ITO glass)

9. Cut ITO glass to 25 × 25 mm², pattern the ITO anode by tape/photoresist + HCl:HNO₃:H₂O (1:1:5) etch, 50 °C, 5 min. Clean: sonicate 10 min each in detergent, DI, acetone, IPA. UV-ozone 15 min.

### 1.4 PEDOT:PSS + poly-TPD hole-injection/transport layers

10. Filter PEDOT:PSS (AI 4083) 0.45 µm, spin on ITO **3000 rpm, 30 s** → ~40 nm. Bake **120 °C, 15 min**.
11. **poly-TPD layer (non-polar bridge).** Spin poly-TPD in chlorobenzene (8 mg/mL) at **2000 rpm, 40 s** → ~30–40 nm. Bake **150 °C, 30 min**. This bridges the polar PEDOT:PSS to the non-polar CIS-QD layer and is **standard in red/NIR QLEDs**; it replaces the direct-CQD-on-PEDOT layout of the carbon chip.

### 1.5 CIS-ZnS emissive layer

12. Spin the CIS-QD/poly-TPD ink at **2000 rpm, 40 s** → ~60–80 nm. Bake **80 °C, 20 min** under N₂ / vacuum desiccator (oxygen and moisture quench CIS PL).
13. **ASE edge strip (optional):** drop-cast a thicker (~250 nm) neat-CIS strip at the pixel edge through a 0.5 mm shadow mask, dry 60 °C, to form the in-plane gain strip for edge ASE.

### 1.5b ZnO electron-transport layer (ETL) — unchanged chemistry

14b. ZnO nanopowder in ethanol/butanol 20 mg/mL, sonicate 30 min, 0.45 µm. Spin on the CIS layer **3000 rpm, 30 s** → ~30–50 nm. Bake **150 °C, 10 min**. ZnO (bandgap 3.37 eV ≈ 368 nm) is **transparent to the 730–760 nm pump and the 916 nm PL**; it injects electrons and blocks holes, completing the p-i-n stack.

### 1.6 Top cathode — unchanged

15. **Top cathode (Ag).** Maker route: dispense **conductive silver paste from a syringe** through the 8 × 8 shadow mask, doctor-blade ~1–2 µm, cure 60–80 °C, 15 min. Lab route: thermal-evaporate Ag 100 nm through mask. Pattern 8 × 8 pads.
16. Encapsulate: spin 200 nm PMMA, cure 120 °C, 10 min.

### 1.7 Electrical drive and EL check

17. Contact ITO anode + Ag cathode; drive DC, increase 0 V. Turn-on **3–8 V**; red/NIR EL visible under dim light / with a night-vision viewer or phone camera (730–760 nm is at the edge of Si-camera sensitivity). Drive 2–20 mA/pixel.
18. **ASE probe:** drive a pixel at 2× threshold; collect edge emission via fiber into a USB spectrometer (NIR-capable, or a Si spectrometer with reduced sensitivity — note 916 nm is near the Si detector cutoff; an InGaAs spectrometer is ideal for the PL). Look for FWHM narrowing (from ~100 nm to < 30 nm) above threshold = ASE.

**Timing, Section 1:** CIS synthesis ~1 day (hot-injection + shell + purify) + film stack 1 day + cathode half day. **Total: ≈ 2.5–3 working days** (comparable to the carbon-CQD route but with the Schlenk step).

---

## Section 2 — 4H-SiC Wafer + V_Si Defect Engineering (REPLACES the hBN section)

**Goal:** Obtain a 4H-SiC wafer sample hosting V_Si (V2) centres (2|D| ≈ 70 MHz, ZPL 916 nm) at ensemble density for 8 × 8 site addressing, with the surface ready for the emitter/waveguide/detector stack.

### 2.1 Procurement

1. **Buy the wafer.** Order a **4H-SiC** sample (semi-insulating or N-doped, 5×5 mm to 2-inch, double-side polished). 4H-SiC power-electronic wafers are mass-produced and inexpensive. Expect ~$30–$120.
2. **Native V_Si.** V_Si (V2) are **often present as-grown** in commercial 4H-SiC at low density, and are reliably created/enriched by irradiation. Check vendor data or plan an irradiation step.

### 2.2 Defect engineering (V_Si creation)

**Route A — Irradiation + anneal (recommended, the established route).**

3. **Irradiate.** Electron (2 MeV, ~10¹⁸ cm⁻²) or proton irradiation creates silicon vacancies. University irradiation services are often free/cheap; commercial services ~$200.
4. **Activation anneal.** Anneal in **Ar or N₂** at **800–1200 °C, 1–2 h** in the tube furnace to mobilize/activate the vacancies and remove damage. *This needs the tube furnace — the hotplate cannot reach it.* SiC is robust up to ~1600 °C so the anneal window is wide and forgiving (much easier than the 800–900 °C narrow hBN window).

**Route B — As-grown (no furnace, if the wafer already shows V_Si).** Some 4H-SiC wafers show V_Si ODMR as-delivered; if you confirm a PL/ODMR signal, skip irradiation/anneal and proceed to clean.

### 2.3 Surface cleaning

5. **Solvent clean.** Sonicate 5 min each in acetone, IPA, DI water.
6. **Acid/oxide clean.** Dip in **49 % HF : DI = 1:10** for 60 s (removes native SiO₂; **HF — calcium gluconate gel on hand, full PPE**), or RCA-1 (NH₃:H₂O₂:H₂O) if you want a hydrophilic oxide. Alternatively **O₂ plasma** 100 W, 10 min (dry). Rinse DI, N₂ dry. SiC surfaces are robust and easy to prepare (no boiling triacid needed, unlike diamond).

### 2.4 Site density and surface proximity

7. **Density.** Irradiated/enriched V_Si ensemble density ~10¹⁰–10¹² cm⁻³ is typical; at the wafer surface this gives resolvable ensemble spots for 8 × 8 ODMR.
8. **Depth caveat.** V_Si in bulk SiC are **distributed through the ~0.5 mm wafer** (even deeper/denser than diamond, since SiC irradiation defects form throughout). Surface proximity for sensing is the **worst** of the three platforms — bulk V_Si are far from the surface. (Shallow V_Si via low-energy implant is possible but outside maker scope.) For the QSSC compute/sensor demonstrator this is acceptable; the trade-off vs hBN is **worst surface proximity, ~20× better coherence, easiest host fabrication**.

### 2.5 No PMMA-on-host step

9. No PMMA passivation on the SiC. Proceed directly to waveguide/emitter/detector deposition (Section 4). Planarize with SU-8 if the polished surface is too rough for waveguide adhesion.

**Timing, Section 2:** clean 1 h + (optional) irradiation (outsourced) + anneal 4 h. **Total: ~0.5–1 working day + irradiation turnaround** (the easiest host in the QSSC family).

---

## Section 3 — On-Chip Fluorescence Photodetector Array (Graphene / MoS₂) (CHANGED: graphene-led for IR)

**Goal:** N-electrode graphene/MoS₂ photodetector array on-chip, FPGA-read, software pulse-capture calibration (Section 5.6). **Identical process to the hBN protocol Section 3**, with one platform note: the 916 nm V_Si PL is in the IR where **monolayer MoS₂ (bandgap ~690 nm) does not absorb**, so the MoS₂ photogating gain is lost. Options:
- **Graphene-led channel** (broadband, absorbs 916 nm) — the simplest; bare-graphene responsivity ~0.01–0.1 A/W, detectable with the lock-in.
- **Multilayer MoS₂** (few-layer, bandgap ~1.2 eV ≈ 1030 nm) — absorbs 916 nm, restores some photogating gain; use few-layer MoS₂ instead of monolayer for this variant.
- **Graphene/MoS₂-photogating heterostructure with few-layer MoS₂** — best of both: graphene collects, few-layer MoS₂ gates.

- CVD graphene transfer, Au/Cr source-drain electrodes, O₂-plasma isolation, bias 0.1–1 V, lock-in readout — as in the hBN base protocol. No discrete photodiodes, no external camera. **Use few-layer MoS₂ (not monolayer) for this variant.**

---

## Section 4 — Waveguides, Stacking (bulk geometry), Filter, Thermal, Contacts

### 4.1 SU-8 waveguides (on SiC)

1. SiC index n ≈ 2.6 (very high); spin a ~5 µm SU-8 planarization layer (1000 rpm, flood-expose, hard-bake) so waveguide modes are not pulled into the wafer. Then spin SU-8 2002 core (2000 rpm → ~2 µm), pattern 5–10 µm wide guides, PDMS cladding (n 1.41) over SU-8 (n 1.58). Same as the hBN protocol modulo the thicker planarization.

### 4.2 Coupling the CIS emitter to the waveguide + **850 nm long-pass filter**

2. Cleave the CIS stack so the red/NIR gain-strip edge faces the SU-8 input facet; butt-couple in the jig; UV-cure NOA 61. Coupling 20–60 %.
3. **850 nm long-pass filter.** Place an **850 nm long-pass** in the collection path (between the SiC PL output and the detector array, or as a coating on the detector facet). It **passes the 916 nm V_Si PL and blocks the 730–760 nm CIS pump**. (The hBN chip's 650 nm filter is **not** used here — it would pass the 730–760 pump.) If pumping at the red edge (~760 nm), verify the 850 nm filter edge is steep enough; pumping at ~730 nm gives the cleanest separation.

### 4.3 Stacking (bulk, not 2D)

4. **Stack order (bottom→top):** Si/SiO₂ carrier → Peltier TEC → **4H-SiC wafer** (face up) → SU-8 waveguides → PDMS → CIS emitter chip (edge-coupled) → 850 nm LP filter → graphene/few-layer-MoS₂ detector at waveguide output. The SiC replaces the hBN chip; the rest mirrors the hBN stack. Bond corners with PDMS/cyanoacrylate; cure 80 °C, 1 h. Wire-bond/silver-paste to PCB fan-out.

### 4.4 Peltier thermal management (unchanged)

5. 2-stage TEC (or TEC1-12706), NTC, PID **295 K ± 0.1 K**, < 5 W. SiC thermal conductivity (~370 W/m·K) is excellent; temperature stability is easy.

### 4.5 Contacts and encapsulation (unchanged)

6. Au wire / silver-paste to header: ITO anode (×8 rows), Ag cathode (×8 cols), detector drains (×N), gate (×1), thermistor (×2), TEC ± (×2). PDMS encapsulation, output facet exposed.

### 4.6 Coplanar waveguide (MW) for ODMR (unchanged, frequency changes)

7. Hand-etch 50 Ω CPW on FR4/Rogers; mount the SiC wafer face-down over the signal line (≤ 50 µm gap) or flip-chip with anisotropic paste. **Microwave frequency changes to 70 MHz** (V_Si V2, 2|D| = 70 MHz) — well within the ADF4351 range (35 MHz–4.4 GHz); at 70 MHz you may alternatively use a low-frequency function generator directly. **No hardware change** to the PLL chain.

> **Optional divacancy upgrade (not the primary protocol):** the SiC **divacancy** V_SiV_C (S=1, D ≈ 1.3 GHz, ZPL ~1.1 µm, T₂ ~ ms with DD) would compute small Shor at room temperature. It needs a **~808/980 nm pump** (CIS QDs reach ~800; for 980 use PbS — Pb, toxic — or a 980 diode) and a **~1000 nm long-pass filter**. The QSSC simulator profiles V_Si (70 MHz), so this variant stays V_Si-primary; the divacancy is documented as a future upgrade path.

---

## Section 5 — Characterization & Testing

### 5.1 Electroluminescence (CIS-QD array)
1. Drive each pixel 0–12 V, confirm turn-on 3–8 V and red/NIR EL (~730–760 nm; check with a night-vision viewer, phone camera, or InGaAs/Si spectrometer). ASE check: FWHM narrowing < 30 nm above threshold. As in the hBN protocol.

**One addition over the hBN base:** the hBN §5.1 at-the-sample photodiode power measurement is **mandatory here too** (same logic, at the 730–760 nm pump band); it pins the pump power of §5.2.

### 5.2 ODMR of V_Si in 4H-SiC
5. **Optical:** on-chip CIS-QD emitter (~730–760 nm, ~1 mW at the sample) — no external laser. (**Verify the ~1 mW with the §5.1 photodiode measurement — mandatory.** At ~745 nm the cd→W conversion differs strongly from the green-pixel arithmetic in `FABRICATION_PROTOCOL.md` §5.2.1, so no internal contradiction is claimed here — the measured value pins it.) Collect V_Si PL at **~916 nm** through the **850 nm long-pass filter** (passes 916, blocks the 730–760 pump) into the **on-chip graphene/few-layer-MoS₂ detector array**. No Si APD / discrete photodiode. (A Si spectrometer has reduced sensitivity at 916 nm near its cutoff; an InGaAs detector/spectrometer is preferred for characterization, but the on-chip graphene detector reads 916 nm fine.)
6. **Microwave:** sweep **~50–90 MHz** from the function generator / ADF4351 through the CPW. Modulate MW at 1 kHz for lock-in.
7. **Expected resonance:** a resonance at **2|D| ≈ 70 MHz** (V_Si V2, S=3/2; the ²⁹Si hyperfine, A ~ 1.5 MHz, is weak/sub-linewidth and usually merges into one line). ODMR contrast **1–10 %** (ensemble), linewidth **5–30 MHz**. The S=3/2 quartet structure splits under a static B₀.
8. **Magnetic field sweep:** apply B₀; observe the S=3/2 sublevel splitting, D shift D(B) = D₀ ± γ_e·B₀, γ_e/2π ≈ 28 GHz/T — confirms the spin-3/2 assignment.

### 5.3 Spin coherence (T₂)
9. **Pulsed ODMR** (Hahn echo at 70 MHz). Vary τ from 0.1 µs to **100 µs**. Fit echo vs τ to exp[−(2τ/T₂)^n].
10. **Expected T₂:** **20–60 µs at room temperature** (natural abundance, ensemble), up to **~100 µs isotopically purified** (⁴H-²⁸SiC), extended with dynamical decoupling. T₁ typically **~340 µs**. This is **~20× longer than V_B⁻** — enough for shallow algorithms (Bell pairs, small QFT, Ramsey sensing) at 300 K, but **deep algorithms (Shor N=15) do not fit T₂ at 300 K without DD + isotopic purification**; cool to raise T₂ further.

### 5.4 Coupling efficiency (waveguide → detector) — unchanged
Inject calibrated 730 nm light into the SU-8 facet; measure output + on-chip detector photocurrent through the 850 nm LP. Target 20–60 % coupling, < 1 dB/cm loss.

### 5.5 End-to-end functional test
13. Drive the CIS pixel electrically → red/NIR EL pumps the V_Si ensemble → collect 916 nm PL (through the 850 nm long-pass filter) at the on-chip graphene/few-layer-MoS₂ detector → photocurrent modulated by MW at **70 MHz** → FPGA/lock-in reads the ODMR dip. Successful end-to-end ODMR with the **on-chip CIS-QD pump** (no external laser, no big diode) is the chip's defining demonstration.

### 5.6 Emitter↔qubit↔detector calibration (software pulse-capture) — unchanged
14. Same automated FPGA sweep: for each emitter pixel fire a red/NIR pump pulse, sample every detector electrode, record which sees a modulated 916 nm response under MW at **70 MHz** → build the emitter-pixel→qubit-site→detector-electrode lookup table. No camera, no alignment jig. (The MW frequency in the sweep is 70 MHz, the filter is 850 nm LP, the pump is red/NIR — the only calibration deltas from the hBN chip.)

---

## Section 6 — Safety

| Hazard | Control |
|---|---|
| Schlenk line / hot flask 230–240 °C, Ar | Heat-resistant gloves; confirm Ar flow; vent overpressure; fire risk with organics |
| Oleylamine / 1-dodecanethiol / ODE (high-bp organics, odorous) | Fume hood; no ignition; capture waste |
| HF (49 %, dilute) for SiC clean | **Calcium gluconate gel on hand**, face shield, butyl gloves, fume hood |
| HNO₃, HCl | Fume hood, PPE |
| Tube furnace 800–1200 °C | Heat-resistant gloves; cool before opening; fire risk |
| Electron/proton irradiation (outsourced) | Facility controls; treat irradiated wafer per facility guidance |
| 730–760 nm high-brightness EL | Not class 3B (it's an LED), but avoid staring into the bright NIR source; NIR is invisible — use a viewer |
| MW up to 4 GHz, < +20 dBm | Low risk; keep > 30 cm |
| Peltier / hot surfaces | Heat-resistant gloves |
| Nanomaterials (CIS QDs, In/Cu salts, graphene/MoS₂) | Indium/copper salts are moderately toxic — N95, gloves, fume hood for synthesis; dispose CIS waste as heavy-metal solid waste |

---

## 7. Expected Performance Table (SiC V_Si variant)

| Parameter | Target / expected | Basis |
|---|---|---|
| CIS-QD pump (on-chip) | **730–760 nm** (red/NIR) | Sec. 1 |
| CIS-QD EL turn-on | 3–8 V | Sec. 1 |
| CIS-QD lasing | **NOT claimed** (ASE only) | see note |
| **Host** | 4H-SiC wafer (bulk 3D) | Sec. 2 |
| **Defect** | V_Si (V2), S=3/2, 2|D| ≈ 70 MHz | Sec. 2, 5.2 |
| V_Si PL | ~916 nm (NIR) | Sec. 5.2 |
| **Optical filter** | **850 nm long-pass (CHANGED)** | Sec. 5.2 |
| Defect density | ~10¹⁰–10¹² cm⁻³ (ensemble) | Sec. 2.4 |
| ODMR contrast | 1–10 % | Sec. 5.2 |
| ODMR linewidth | 5–30 MHz | Sec. 5.2 |
| **Spin coherence T₂ (RT)** | **20–60 µs (≤100 µs isotopic)** | Sec. 5.3 |
| Spin relaxation T₁ (RT) | ~340 µs | Sec. 5.3 |
| **Compute at 300 K?** | Shallow only (Bell, small QFT, Ramsey); deep (Shor) needs DD + cool | Sec. 5.3 |
| Waveguide coupling | 20–60 % | Sec. 4.2 |
| Graphene detector responsivity | 0.01–0.1 A/W (few-layer MoS₂ restores some gain) | Sec. 3 |
| Operating temperature | 288–300 K (set 295 K) | Sec. 4.4 |
| TEC power | < 5 W | Sec. 4.4 |
| Chip footprint | 25 × 25 mm² (host = 4H-SiC wafer ~5×5×0.5 mm) | Sec. 2 |

---

## 8. Validation Against Known Studies

### 8.1 V_Si (V2) in 4H-SiC — D, ODMR, coherence
- **V_Si (V2), 2|D| = 70 MHz, S=3/2, room-temperature spin control:** Kraus et al., *Nature Physics* 10, 157–162 (2014) — the basis for the 70 MHz figure and the S=3/2 assignment used by the QSSC simulator `SiC-VSi` profile.
- **Coherent control of single spins in SiC at room temperature:** Widmann et al., *Nature Materials* 14, 164–168 (2015).
- **V_Si as a quantum system / single-defect spectroscopy:** Baranov et al., *PRB* 83, 125203 (2011).
- **Single V1 spin-optical dynamics:** Morioka et al., *Phys. Rev. Applied* 17, 054005 (2022).
- **Room-T ensemble spin echo of V_Si, field-dependent T₂ (tens of µs):** Carter et al., *PRB* 92, 161202 (2015).
- **V_Si T₁ ~ 340 µs, T₂ ~ 104 µs at 295 K (proton-irradiated 4H-SiC):** Embley et al., *PRB* 95, 045206 (2017) — the basis for T₂ ≈ 40 µs (natural) / ~100 µs (purified).
- **Isotopic purification extends V_Si T₂ to ~105 µs:** Lekavicius et al., *PRX Quantum* 3, 010343 (2022).
- **Divacancy (V_SiV_C, S=1, D ≈ 1.3 GHz, ms coherence) — distinct defect, optional upgrade:** Christle et al., *Nat. Mater.* 14, 160–163 (2015) and *PRX* 7, 021046 (2017).
- **Dynamical decoupling to extend T₂:** de Lange et al., *Science* 330, 60–63 (2010); Ryan et al., *PRL* 105, 200402 (2010).

### 8.2 CIS/ZnS QDs and NIR QLEDs
- **CuInS₂/ZnS core/shell colloidal QDs, size-tunable 550–800+ nm, Cd/Pb-free:** the CIS/ZnS QD literature (e.g. Zhong et al. and subsequent CIS-QD reviews) establishes the synthesis and the 550–800 nm tunability used in Section 1.
- **CIS-QD electroluminescent NIR QLEDs (ITO/PEDOT:PSS/poly-TPD/CIS/ZnO/metal):** the poly-TPD interlayer + ZnO ETL stack is the standard red/NIR QLED architecture, supporting Section 1.4–1.5b.
- CIS QDs are chosen specifically because **carbon quantum dots (citric-acid+urea) do not reach 730–760 nm** (their ceiling is green ~540 nm).

### 8.3 Detector / waveguides / Peltier
- Graphene (broadband, reads 916 nm IR) and few-layer MoS₂ (bandgap ~1.2 eV, absorbs ~1030 nm and thus 916 nm) detector performance is consistent with the hBN base protocol validation (graphene: Xia et al. 2009; MoS₂: Lopez-Sanchez et al. 2013; photogating: Konstantatos et al. 2012). SU-8/PDMS waveguide coupling and Peltier stability are as in the hBN base.

---

*End of SiC V_Si variant. This document is a maker-lab demonstrator specification. It reuses the hBN chip's detector concept, waveguides, FPGA and software calibration; it changes the on-chip emitter (green carbon CQD → red/NIR CIS/ZnS QDs, on-chip, no big diode), the optical filter (650 → 850 nm long-pass), the host (hBN film → 4H-SiC wafer) and the MW frequency (3.46 GHz → 70 MHz). Where primary literature is cited, consult the cited paper before relying on a specific numerical parameter.*

---

## References (platform-specific; CQD/detector/waveguide refs are in the hBN base)

- [15] H. Kraus et al., "Room-temperature quantum microwave emitters based on spin defects in silicon carbide," Nature Physics 10, 157–162 (2014). doi:10.1038/nphys2826
- [16] M. Widmann et al., "Coherent control of single spins in silicon carbide at room temperature," Nature Materials 14, 164–168 (2015). doi:10.1038/nmat4145
- [17] D. J. Christle et al., "Isolated spin qubits in SiC with a high-fidelity infrared spin-to-photon interface," PRX 7, 021046 (2017). doi:10.1103/PhysRevX.7.021046
- [18] D. J. Christle et al., "Isolated electron spins in silicon carbide with millisecond coherence times," Nature Materials 14, 160–163 (2015). doi:10.1038/nmat4144
- [19] P. G. Baranov et al., "Silicon vacancy in SiC as a promising quantum system…," PRB 83, 125203 (2011). doi:10.1103/PhysRevB.83.125203
- [20] N. Morioka et al., "Spin-optical dynamics and quantum efficiency of a single V1 center in silicon carbide," Phys. Rev. Applied 17, 054005 (2022). doi:10.1103/PhysRevApplied.17.054005
- [21] G. de Lange et al., "Universal dynamical decoupling…," Science 330, 60–63 (2010). doi:10.1126/science.1192739
- [22] C. A. Ryan et al., "Robust decoupling techniques to extend quantum coherence in diamond," PRL 105, 200402 (2010). doi:10.1103/PhysRevLett.105.200402
- Embley et al., PRB 95, 045206 (2017). doi:10.1103/PhysRevB.95.045206
- Lekavicius et al., PRX Quantum 3, 010343 (2022). doi:10.1103/PRXQuantum.3.010343
- H. Zhong et al., and the CuInS₂/ZnS colloidal-QD / NIR-QLED literature (size-tunable 550–800 nm, Cd/Pb-free).