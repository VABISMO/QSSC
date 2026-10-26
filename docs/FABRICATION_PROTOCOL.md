# Quantum Solid State Chip (Room Temperature) — Fabrication Protocol

**Project:** QSSC (Quantum Solid State Chip, room-temperature demonstrator)
**Document type:** Laboratory fabrication protocol (standalone)
**Version:** 1.0
**Scope:** Home / maker-lab demonstrator integrating (1) an electrically-pumped carbon-quantum-dot (CQD) ASE/LED emitter array, (2) a PMMA-coated hexagonal boron nitride (hBN) layer hosting optically-addressable spin defects (negatively-charged boron vacancy, VB−, zero-field splitting D ≈ 3.46 GHz, plus carbon-related centers), and (3) an on-chip 2D-material (graphene/MoS₂) photodetector readout over a passive heat-spreader, operating at room temperature.

> **Note on the emitter claim.** True *electrically-pumped lasing* from carbon quantum dots is **not** claimed in this protocol, and the reader should not infer it from the literature cited. What is targeted and demonstrated here is **electroluminescence (EL) and amplified spontaneous emission (ASE) / high-brightness edge emission** from a CQD–polymer composite diode stack, used as a compact optical pump and on-chip source. ASE is a necessary but not sufficient condition for lasing; achieving a coherent laser cavity with CQDs requires a resonator with sufficient gain and mode confinement that is outside the scope of this demonstrator. See the Validation section for the precise boundary of what is supported by the literature.

---

## 1. Abstract

This document specifies a complete, equipment-minimal fabrication protocol for a room-temperature solid-state spin-defect quantum chip (QSSC). The chip combines three functional layers on a single 25 × 25 mm² carrier: (i) a hydrothermally-synthesized carbon quantum dot (CQD) emitter array embedded in a polymeric LED/ASE stack that provides electrically-pumped broadband visible electroluminescence centered near 450–620 nm; (ii) a liquid-phase-exfoliated and chemically-engineered hexagonal boron nitride (hBN) thin film, spin-coated with PMMA, hosting optically-addressable spin defects including the negatively-charged boron vacancy (VB−) with a zero-field splitting D ≈ 3.46 GHz and carbon-related color centers; and (iii) an on-chip 2D-material (graphene / MoS₂) photodetector array that reads the defect fluorescence directly. A passive heat-spreader under the carrier holds the hBN layer at 288–300 K for stable room-temperature operation and ODMR. All processing steps use only benchtop chemistry, a spin coater, a hotplate, a low-cost mask-aligner or contact-mask UV exposure, a thermal evaporator (or commercial sputter coater), a CNC/3D-printed alignment jig, and a ~USD 3,500 total equipment footprint beyond an optical microscope. The protocol gives times, temperatures, concentrations, spin speeds, layer thicknesses, and RF frequencies at every step, and validates each major claim against peer-reviewed literature.

---

## 2. Introduction & Scientific Basis

### 2.1 Why a room-temperature quantum chip demonstrator

Most solid-state spin-qubit platforms (NV centers in diamond, GaAs quantum dots, superconducting qubits) require cryogenic operation (< 10 K) or specialized microwave hardware. Optically-addressable spin defects in hexagonal boron nitride are a recent and notable exception: the negatively-charged boron vacancy, VB−, exhibits a spin-1 triplet with zero-field splitting **D ≈ 3.46 GHz**, optical initialization and readout, and measurable spin coherence at room temperature. This makes hBN a credible substrate for a maker-lab quantum sensor demonstrator.

### 2.2 Why carbon quantum dots as the on-chip source

Carbon quantum dots (CQDs, also called carbon nanodots, C-dots) are fluorescent carbon nanoparticles (2–10 nm) synthesized by bottom-up hydrothermal carbonization of citric acid + nitrogen dopants (e.g., urea, ethylenediamine). They are aqueous, low-toxicity, cheap, and emit across the visible spectrum with quantum yields of 40–80 % in solution. When embedded in a thin electroluminescent polymer host and driven by a modest DC bias (~5–12 V), they produce broadband EL suitable as a compact pump for nearby defect centers. We do **not** claim a CQD laser; we claim an electrically-pumped CQD LED/ASE source.

### 2.3 Why an on-chip 2D-material photodetector

Graphene and MoS₂ photodetectors offer broadband absorption, gate-tunable
responsivity and direct integration over the host, which puts the readout on
the chip itself: no discrete photodiodes and no external optical bench for the
ODMR fluorescence. Both transfer by the wet processes already used for the hBN
film, so they stay inside the benchtop equipment set.

### 2.4 Architecture overview

```
                ┌─────────────────────────────────────────────┐
   top Ag cathode (silver-paste syringe) ─────────────────── │
                │  ZnO electron-transport layer (≈40 nm)     │  ← Section 1
                │  CQD–PVA electroluminescent layer (≈80 nm,  │
                │       green ~520–540 nm → pumps VB−)       │
                │  PEDOT:PSS hole-injection (≈40 nm)          │
                │  bottom ITO anode on glass/PET             │
   ─────────────┴─────────────────────────────────────────────┘
                       ↓ optical pumping / EL
   ┌─────────────────────────────────────────────────────┐
   │  PMMA cap (≈50 nm)                                 │  ← Section 2
   │  hBN thin film (≈3–8 layers, 2–4 nm) w/ VB−        │
   │  SiO₂/Si or glass substrate                        │
   ──────────────────────────────────────────────────────┘
        ↘ photoluminescence ↙
   ┌─────────────────────────────────────────────┐
   │  → graphene / MoS₂ photodetector array      │  ← Section 3/4
   └─────────────────────────────────────────────┘
   Heat-spreader under the Si carrier       ← Section 4
```

---

## 3. Materials & Bill of Materials (BOM)

All costs are approximate, in USD, retail/small-lab quantities (2025–2026 pricing).

### 3.1 Chemicals and reagents

| Item | Spec / source | Qty | Approx. cost |
|---|---|---|---|
| Citric acid (anhydrous, ≥99.5 %) | Sigma-Aldrich 251275 | 100 g | $20 |
| Urea (≥99 %) | Sigma 51456 | 100 g | $20 |
| Ethylenediamine (≥99 %) | Sigma 03550 | 25 mL | $25 |
| Deionized water (≥18 MΩ·cm) | in-house Milli-Q or distilled | 5 L | $10 |
| 0.22 µm syringe filters (PVDF) | Millipore GV | 50 pk | $30 |
| Dialysis membrane (MWCO 1000 Da) | Spectra/Por 6 | 1 roll | $45 |
| hBN powder (crystalline, ~1–10 µm platelets, 99.5 %) | Sigma 254059 or Graphene Supermarket | 1 g | $60–120 |
| NMP (1-methyl-2-pyrrolidone) | Sigma 328634 | 500 mL | $45 |
| Isopropanol (IPA, ≥99.9 %) | 500 mL | $20 | |
| TEOS (tetraethyl orthosilicate, ≥98 %) | Sigma 86578 | 500 mL | $60 |
| MTEOS (methyltriethoxysilane, ≥99 %, core dopant) | Sigma 246638 | 100 mL | $45 |
| PDMS (Sylgard 184 kit, base + curing agent) | Dow | 1 kg | $90 |
| PMMA (950 K, 2 % in anisole) | Microchem | 100 mL | $40 |
| PEDOT:PSS (CLEVIOS P VP AI 4083) | Heraeus | 100 mL | $60 |
| Polyvinyl alcohol (PVA, 87–89 % hydrolyzed, Mw 31–50 k) | Sigma 363170 | 100 g | $25 |
| ITO-coated glass (15 Ω/sq, 25×25 mm) | Delta Technologies or Mitsufuji | 10 pk | $50 |
| ZnO nanopowder (~20–40 nm, 99.5 %) | Sigma 544906 or maker-grade ZnO | 5 g | $30 |
| Conductive silver paste (syringe, circuit-repair) | MG Chemicals 8341 / Elekit | 1 syringe | $10 |
| Graphene (CVD, on Cu foil, 1 cm²) | Graphenea or 2D Semiconductors | 2 cm² | $90 |
| MoS₂ (CVD, on SiO₂/Si, monolayer) | 2D Semiconductors | 1 cm² | $120 |
| Au wire (99.99 %, 0.25 mm) | Kurt J. Lesker | 1 m | $40 |
| Cr pellet (99.95 %) | Kurt J. Lesker | 10 g | $35 |
| Cu tape / Ni foam (current collectors) | — | assorted | $20 |
| Acetone, methanol, IPA (cleaning) | — | 1 L each | $40 |
| Nitric acid (HNO₃, 65 %) | — | 100 mL | $15 |
| NaOH pellets | — | 100 g | $10 |

### 3.2 Equipment (capital, one-time)

| Item | Function | Approx. cost |
|---|---|---|
| Hotplate with digital temp control (to 350 °C) | synthesis, baking | $150 |
| Benchtop tube furnace (to 1100 °C, quartz tube, Ar/N₂ flow) | VB− activation anneal (800–900 °C) — *not reachable on the hotplate* | $400–$700 |
| PTFE-lined stainless hydrothermal autoclave (50 mL) | CQD synthesis | $40 |
| Ultrasonic bath (40 kHz, 200 W) | exfoliation, cleaning | $120 |
| Spin coater (vacuum chuck, 100–8000 rpm) | thin films | $600 (used) |
| UV exposure unit (365 nm, contact mask) or LED lamp (365 nm, 10 W) | sol-gel emboss cure / stencil patterning | $200–$400 |
| Thermal evaporator / sputter coater (used/refurb) or e-beam | metal contacts | $1,200–$2,000 |
| Optical microscope (10×–100×, reflected) | inspection | $300 (used) |
| DC bench supply (0–30 V, 0–3 A) + Keithley-style sourcemeter (used 2400) | electrical drive & characterization | $300 (used) |
| Lock-in amplifier (used SR830 or Moku:Lab) + function generator (0–6 GHz) | ODMR | $700 used / $1,500 Moku |
| 532 nm DPSS laser (50 mW, CW) + 594 nm notch/long-pass filters | optical characterization | $250 |
| Coplanar waveguide PCB (hand-etched) + SMA launcher | microwave delivery | $30 |
| 3D-printed alignment jig (SLA/PLA) | layer stacking | $30 |
| Micromanipulator / probe station (used) | contacts | $200 |
| Vacuum desiccator | storage, PDMS degassing | $80 |
| Fume hood (essential) or dedicated ventilated bench | safety | — |

**Estimated total capital (beyond glassware): ≈ USD 3,600–4,500.**

---

## Section 1 — CQD Emitter Array

**Goal:** Synthesize water-soluble, nitrogen-doped CQDs and integrate them into a patterned ITO/PEDOT:PSS/CQD–PVA/ZnO/Ag electroluminescent diode array (8 × 8 pixels, 1 mm² each; ZnO is the electron-transport layer, Ag the top cathode) on a 25 × 25 mm² ITO-glass substrate. For this chip the CQDs are synthesised **green** (~520–540 nm) so the on-chip emitter doubles as the optical pump for the VB− defect (no external pump laser needed for the end-to-end demo). Target: EL peak 520–540 nm (green), turn-on 4–8 V, radiance ≥ 10 cd/m², ASE threshold detectable under electrical overdrive.

### 1.1 Hydrothermal synthesis of N-doped CQDs

This follows the citric-acid + urea hydrothermal route, a well-established "bottom-up" CQD synthesis.

1. **Charge.** In a 50 mL PTFE-lined autoclave, dissolve **1.0 g citric acid** + **1.0 g urea** in **10 mL DI water**. Stir 5 min until clear. **No ethylenediamine is required**: the emission colour is set mainly by the **hydrothermal temperature** (see step 6). For this chip we want **green** CQDs (~520–540 nm) to pump the VB−, so use the high end of the temperature range (~220 °C).
2. **Hydrothermal.** Seal autoclave, place in hotplate oven preheated to **180 °C (blue CQDs, ~450–470 nm)** or **220 °C (green CQDs, ~520–540 nm — the choice for this chip to pump VB−)**, hold **6–10 h** (longer time at the higher temperature also red-shifts). (Internal pressure ~10–15 bar autogenous.) Cooling: remove from hotplate, allow to cool to < 60 °C naturally (~2 h) before opening.
3. **Resulting product:** dark-brown solution containing CQDs (2–6 nm), unreacted precursor, larger carbonaceous particles.
4. **Purification.**
   - Centrifuge at **10,000 rpm (≈ 9,000 × g)** for **15 min**; discard the large-particle pellet.
   - Filter supernatant through **0.22 µm PVDF syringe filter**.
   - Dialyze against DI water (MWCO 1000 Da) for **48 h**, changing water every 8 h, to remove small molecules.
   - Yield: typically **20–40 mg** of purified CQDs per batch (depends on dialysis losses), consistent with typical citric-acid/urea yields reported in the literature.
5. **Characterization (benchtop).** Dilute 1:1000 in water; record PL under 365 nm UV lamp — bright blue-green emission. Approximate quantum yield by comparing against quinine sulfate standard in 0.1 M H₂SO₄ (QY ≈ 54 % typical). Optional TEM drop-cast for size.
6. **Wavelength tuning (temperature is the primary knob).** With citric acid + urea the emission colour is set mainly by the **hydrothermal temperature** (and reaction time): **~180 °C → blue (~450–470 nm)**, **~200 °C → blue-green (~480–500 nm)**, **~220 °C / longer time → green (~520–540 nm)**. The mechanism is a larger sp² carbon core + heavier nitrogen doping + denser surface states at higher temperature → smaller effective bandgap → red shift. (Ethylenediamine as a dopant can push further to 540–620 nm but is **not required** to reach green.) For this chip, synthesise **green** CQDs (~220 °C) so the on-chip emitter pumps the VB− directly around the 532 nm excitation band — no external pump laser is needed for the end-to-end demonstration.

### 1.2 CQD–polymer electroluminescent ink

7. **Composite.** Dissolve **200 mg PVA** in **4 mL DI water** at 90 °C, stir 1 h, cool. Add **20 mg CQD powder** (or 2 mL of concentrated CQD solution), sonicate **15 min**, stir 1 h. Final CQD loading ≈ 10 wt% in 5 wt% PVA. Filter 0.45 µm.
8. **Optional ASE-lean grade:** For higher film uniformity, add **5 wt% urea** (relative to PVA) as plasticizer; this reduces cracking and raises the effective optical gain length.

### 1.3 Substrate preparation (ITO glass)

9. Cut ITO glass to 25 × 25 mm² (or use pre-cut). Pattern ITO anode by masking with tape / photoresist and etching in **HCl (37 %):HNO₃ (65 %):H₂O = 1:1:5**, 50 °C, 5 min. Stop in DI rinse.
10. Clean: sonicate 10 min each in detergent, DI water, acetone, IPA. UV-ozone 15 min (or piranha if available).
11. Define **8 × 8 pixel array** (1 mm² pads, 0.5 mm gap) using a laser-printed transparency mask on the ITO side.

### 1.4 PEDOT:PSS hole-injection layer

12. Filter PEDOT:PSS (AI 4083) through 0.45 µm PVDF. Spin-coat on ITO at **3000 rpm, 30 s** → ~40 nm. Bake **120 °C, 15 min** on hotplate.

### 1.5 CQD–PVA emissive layer

13. Spin-coat CQD–PVA ink at **2000 rpm, 40 s** → ~80 nm. (Thicker films for ASE: **1500 rpm, 60 s** → ~120–150 nm.) Bake **80 °C, 20 min** under vacuum desiccator to remove residual water (water kills EL).
14. **ASE edge-gain strip:** to favor in-plane ASE, deposit a second, thicker (~250 nm) neat-CQD strip on the pixel edge by drop-casting through a shadow mask (0.5 mm wide) and slow drying at 60 °C. This forms the gain strip into which the diode EL is edge-coupled.

### 1.5b ZnO electron-transport layer (ETL)

14b. **ZnO ETL.** Disperse **ZnO nanopowder** (~20–40 nm particles; ball-mill coarser ZnO if needed) in **ethanol or butanol** at **20 mg/mL**, sonicate 30 min, filter 0.45 µm. Spin-coat on the CQD–PVA layer at **3000 rpm, 30 s** → **~30–50 nm** ETL. Bake **150 °C, 10 min** on hotplate. ZnO (bandgap 3.37 eV ≈ 368 nm) is transparent to both the green pump and the 850 nm defect PL; it injects electrons into the CQD layer and blocks holes, completing the p-i-n stack opposite the PEDOT:PSS hole-transport layer.

### 1.6 Top cathode

15. **Top cathode (Ag).** Two routes:
    - **Maker route (no evaporator — recommended here):** dispense **conductive silver paste from a syringe** (the type sold for circuit-track repair) through an 8 × 8 shadow mask, doctor-blade / spread to **~1–2 µm**, dry/cure per the paste spec (typically 60–80 °C, 15 min, or air-cure 2 h). Silver-paste cathodes are higher-resistance and thicker than evaporated films but entirely adequate for a demonstrator and need no vacuum equipment.
    - **Lab route:** thermal evaporate **Ag** (or Al) through a shadow mask, **100 nm**, rate **0.5–1 Å/s**, base pressure < 5 × 10⁻⁶ mbar. A home sputter coater at ~10⁻³ mbar with a mask gives adequate but higher-resistivity cathodes.
    Pattern 8 × 8 matching pads on the ZnO ETL.
16. **Encapsulate:** spin 200 nm PMMA on top as moisture barrier; cure 120 °C, 10 min.

### 1.7 Electrical drive and EL check

17. Contact ITO anode with Cu tape, cathode with probe needle. Drive with DC bias; increase from 0 V. **Turn-on expected 4–8 V**; visible EL under dim room light by ~10 V. Drive current 2–20 mA per pixel.
18. **ASE probe:** drive a pixel at 2× threshold current; collect edge emission through a 600 µm fiber into a compact USB spectrometer (Ocean Optics-style). Look for **spectral narrowing** (FWHM drop from ~80 nm to < 25 nm) above threshold — signature of ASE, not lasing.

**Timing summary, Section 1:** synthesis 8 h + purification 48 h dialysis (mostly unattended) + film stack 1 day + metal deposition half day. **Total: ≈ 3 working days.**

---

## Section 2 — hBN Exfoliation, Defect Engineering, PMMA Coating

**Goal:** Produce a thin (~2–4 nm, 3–8 layer) hBN film on SiO₂/Si or glass, generate VB− defects (D ≈ 3.46 GHz) at controllable density (~0.1–0.3 /µm²), and cap with a 50 nm PMMA passivation/protection layer.

### 2.1 Liquid-phase exfoliation of hBN

1. **Disperse.** Add **100 mg hBN powder** to **20 mL NMP** in a 20 mL vial. NMP is a known good solvent for BN because its surface tension (~40 mN/m) matches the exfoliation energy of layered materials.
2. **Sonication.** Bath sonicate **12 h** at 40 kHz, 200 W, water bath maintained < 35 °C (ice replacement every 2 h). Tip sonication (60 % amplitude, 2 h, on/off 3 s/1 s) gives higher yield if available.
3. **Size selection.** Centrifuge at **1500 rpm (~200 × g), 20 min**; collect top 70 % supernatant (thin flakes). Re-centrifuge supernatant at **4000 rpm (~1500 × g), 20 min**; collect top 50 % — this is the few-layer fraction (typical lateral size 50–300 nm, thickness 2–6 nm by AFM).
4. **Wash.** Dilute 1:3 with IPA, centrifuge 8000 rpm 10 min, redisperse in IPA. Repeat 2× to remove NMP.
5. **Concentrate.** Redisperse in IPA at **1 mg/mL**. Characterize by drop-casting on SiO₂/Si and AFM (target 3–8 layers).
6. **Yield:** ~5–15 % of starting mass as few-layer flakes; consistent with literature yields for bath sonication of BN in NMP.

### 2.2 Film deposition

7. **Substrate:** 5 × 5 mm² Si (300 nm SiO₂) or glass, cleaned, UV-ozone 15 min.
8. **Drop-cast / spin.** For a continuous thin film: spin 200 µL of 1 mg/mL hBN/IPA at **2000 rpm, 60 s**, repeat 3× to build coverage. Anneal **200 °C, 30 min** in air to burn off organics and improve flake adhesion.
   - For **isolated single flakes** (preferred for single-defect addressing): dilute to 0.01 mg/mL, drop-cast 20 µL, air dry; flakes will be sparse, separable by > 5 µm.

### 2.3 VB− defect engineering

The negatively-charged boron vacancy, VB−, is a spin-1 (S=1) defect with zero-field splitting **D ≈ 3.46 GHz** and a characteristic **7-line ¹⁴N hyperfine fingerprint** (coupling to 3 equivalent ¹⁴N nuclei, A/h ≈ 47 MHz), photoluminescence centred near **850 nm** under 532 nm excitation [3][4][5]. Two creation routes:

**Route A — Energetic irradiation (gold standard, recommended).** VB− is reliably created by neutron irradiation [3] or focused-ion-beam (Xe/Ar/N) [5], followed by a vacuum activation anneal. This is the established, reproducible route; it requires access to an irradiation facility.

9. **Irradiation + activation anneal.** Irradiate the hBN film (neutron fluence ~10¹⁴ cm⁻², or FIB at ~suitable energy/dose), then anneal in **Ar or N₂** at **800–900 °C, 1–2 h** in the tube furnace to mobilise and activate the vacancies. This step *requires the tube furnace* — the 350 °C hotplate cannot reach activation temperature.

**Route B — Chemical / thermal (maker-lab approximation, *unverified*).** Wet-chemical creation of boron vacancies is *not an established route* — hBN is chemically inert to dilute HNO₃ and the mechanism below is speculative. It is offered only as a low-cost approximation and should not be relied on to produce measurable VB− without validation against Route A.

10. **Wet-chemical attempt (unverified).** Soak hBN film in **0.1 M HNO₃** for **10 min**, rinse DI (intended to etch surface B atoms), then anneal in **Ar or N₂** at **500 °C, 1 h** in the tube furnace. Treat any ODMR signal as needing confirmation; do not assume B-vacancy formation.
11. **Carbon-related co-center creation.** Drop-cast **0.05 M citric acid in water** on the hBN, dry at 80 °C, anneal **600 °C, 30 min in Ar** (tube furnace). Carbon diffusion into B-vacancy sites creates carbon-related luminescent centres (broad emission ~580 nm) frequently reported alongside VB−.
12. **Charging to VB−.** Anneal under **N₂** (or forming gas 5 % H₂/N₂) at **400 °C, 30 min**, then quench to room temperature in < 30 s (lift off hotplate onto metal block). The negative charge state is stabilised by the Fermi level / local electrostatics and the rapid quench; hBN is stoichiometric BN, so do not assume "nitrogen donor" reservoirs analogous to P in Si.
13. **Defect density target:** ~0.1–0.3 /µm² (≈ 100–300 / 1000 µm²) — high enough to address ensembles for ODMR but low enough that single defects are resolvable in a confocal spot.

### 2.4 PMMA encapsulation

14. Spin **2 % PMMA 950K in anisole** at **4000 rpm, 45 s** → ~50 nm. Bake **180 °C, 2 min** on hotplate. PMMA serves as (a) oxygen/moisture barrier stabilizing VB− charge state, (b) an index-matching cap (n ≈ 1.49) over the hBN film (n ≈ 2.1 out-of-plane, effective ~1.7), and (c) planarization for the layers deposited on top.

**Timing, Section 2:** exfoliation 12 h + centrifugation/wash 3 h + film 1 h + defect engineering 4 h (anneal-dominated) + PMMA 1 h. **Total: ≈ 2 working days.**

---

## Section 3 — On-Chip Fluorescence Photodetector Array (Graphene / MoS₂)

**Goal:** Place an **N-electrode graphene/MoS₂ photodetector array on-chip, aligned to the 8 × 8 qubit grid** — one detector electrode per addressed site, mirroring the emitter's N electrical contacts — and read it **directly by the FPGA**. No discrete photodiodes / Si APDs, and no external camera. The readout is entirely on-chip; coarse stacking only is needed because the emitter-pixel → qubit-site → detector-electrode mapping is recovered by **software pulse-capture calibration** (Section 5.6), so no micron-precision alignment jig is required.

### 3.1 CVD graphene transfer

1. **Source.** Commercial CVD graphene on Cu foil (Graphenea). Coat graphene face with **PMMA A4** (spin 2000 rpm, 60 s, ~200 nm), bake 180 °C, 2 min.
2. **Etch Cu.** Float on **0.5 M FeCl₃** (or ammonium persulfate 0.1 M) for 12 h; PMMA/graphene membrane releases. Rinse in 3 × DI water baths, 10 min each.
3. **Transfer.** Scoop onto target substrate (the hBN film or a pre-patterned Au electrode pair, 100 µm gap) using a bent Si wafer as a "fishing" scoop. Dry 24 h in air, then **180 °C, 1 h** to improve adhesion.
4. **Remove PMMA.** Soak in acetone 2 h, IPA rinse, N₂ blow dry. Some PMMA residue is acceptable.

### 3.2 MoS₂ transfer (optional, for higher-responsivity channel)

5. CVD monolayer MoS₂ on SiO₂/Si (commercial). Pattern Au contact pads first, then transfer MoS₂ by **water-assisted delamination**: coat with PMMA, etch SiO₂ in **1 % HF** (50 s), scoop membrane onto target.
6. Anneal **200 °C, 2 h in Ar** to remove bubbles and residue.

### 3.3 Electrodes and channel definition

7. Photolithograph or shadow-mask Au/Cr (10/80 nm) source-drain electrodes with **100 µm channel** on the graphene/MoS₂.
8. Define channel by O₂ plasma (100 W, 30 s, 50 mTorr) through a photoresist window to isolate the graphene sheet into 4 detectors.

### 3.4 Operation

9. Bias drain–source **0.1–1 V**; responsivity typically **0.01–0.1 A/W (graphene, bare)**, **10–10² A/W (MoS₂ photogating with trap states)**; detectable down to nW optical powers with a lock-in. Use the same lock-in used for ODMR.

---

## Section 4 — Layer Stacking, Bonding, Thermal Path, Contacts

### 4.1 Layer stacking and bonding

1. **Stack order (bottom→top):** Si/SiO₂ carrier → 5 × 5 mm hBN-on-SiO₂ chip
   (PMMA-capped) → CQD emitter chip, edge-coupled toward the film →
   2D-material photodetector over the hBN film.
2. Bond each layer with thin PDMS or cyanoacrylate at the corners only (avoid
   the active area). Cure 80 °C, 1 h. Wire-bond or silver-paste the CQD
   anode/cathode and the photodetector source/drain to a PCB fan-out.

### 4.2 Thermal path

3. Cu/Al heat-spreader clamped under the carrier with thermal paste, finned
   aluminium sink and a small 40 mm fan. The film runs at ambient; the
   emitter's < 5 W dissipation keeps the carrier within a few tenths of a
   kelvin.

### 4.3 Contacts and encapsulation

4. Au wire bonds or silver-paste tracks to a 0.1" header for: ITO anode (×8
   rows), Ag cathode (×8 cols), photodetector drains (×N, one electrode per
   addressed qubit site), gate (×1, optional electrolyte gate).
5. Encapsulate the active area under a **1 mm PDMS slab** (oxygen/moisture
   barrier, optical clear).

### 4.4 Coplanar waveguide (MW) for ODMR

6. Hand-etch a 50 Ω coplanar waveguide (G-S-G, signal 200 µm / gap 100 µm) on
   a single-sided FR4 or Rogers PCB. Mount the hBN chip face-down over the
   signal line (≤ 50 µm gap from chip backside to line) or flip-chip with
   anisotropic conductive paste. Microwave feedthrough to the on-board SMA
   launcher.

---

---

## Section 5 — Characterization & Testing

### 5.1 Electroluminescence (CQD array)

1. Drive each pixel 0–15 V, log I–V and L–V. Confirm **turn-on 4–8 V** and broadband visible EL.
2. Spectra: couple edge emission to fiber + USB spectrometer. Expect broad Gaussian-like EL centered at the CQD PL peak (450–620 nm depending on synthesis).
3. **ASE check:** drive a single pixel with short pulses (1 ms, 10 % duty) while stepping current; watch for FWHM narrowing from ~80 nm to < 25 nm and a super-linear L–I knee. **Do not** interpret this as lasing — it is ASE.
4. **At-the-sample optical power — mandatory gate.** With the array driven at its nominal operating point (step 1), measure the emitted optical power **at the sample plane** with a calibrated photodiode power meter (µW-class head resolved to tens of nW at the pump band, e.g. Thorlabs S120C, 400–1100 nm, 50 nW floor — the same calibrated-head measurement §5.4 assumes). At the §1 radiance target (≥ 10 cd/m²) the EL is **≈ 50 nW per 1 mm² pixel** at 532 nm — **≈ 3 µW with all 64 pixels driven**, resolvable on a µW-class meter (a single pixel at ~50 nW sits at that meter's floor, so take the array aggregate as the budget input and the step-1 per-pixel L–V sweep for uniformity). **Record P_EL and substitute it into the §5.2 link budget before any ODMR attempt** — the readout feasibility turns on this number, which is why the measurement is part of the EL test and not an optional extra.

### 5.2 ODMR of VB− in hBN

5. **Optical:** 532 nm CW laser (or the on-chip green CQD emitter) at the power measured in §5.1 — order ~50 nW/pixel at the §1 radiance target, *not* the ~1 mW quoted in earlier revisions (that figure contradicted the §1 radiance target by ~4 orders of magnitude; see the §5.2.1 link budget). Collect the VB− PL at **~850 nm** through a **650 nm red photographic long-pass filter** (passes the 850 nm NIR defect PL, blocks the 532 nm green pump) into the **on-chip graphene/MoS₂ detector array**. No Si APD / discrete photodiode — the readout is entirely on-chip.
6. **Microwave:** sweep 3.30–3.60 GHz from the function generator through the CPW. Modulate MW at 1 kHz for lock-in detection of the ODMR dip.
7. **Expected resonance:** the characteristic **7-line ¹⁴N hyperfine pattern** centred at **D ≈ 3.46 GHz** (coupling to 3 equivalent ¹⁴N, A/h ≈ 47 MHz, intensity ratios ≈ 1:3:6:7:6:3:1) [5]. ODMR contrast **0.5–3 %** (ensemble), linewidth **5–20 MHz** (broadened by strain and the ¹⁰B/¹¹B nuclear bath). At low resolution the seven lines merge into a single dip near 3.46 GHz.
8. **Magnetic field sweep:** apply a static B₀ along the c-axis (a small permanent magnet on a linear stage); observe D shift following D(B) = D₀ ± γ_e·B₀ with γ_e/2π ≈ 28 GHz/T. The doublet splits symmetrically — confirms the spin-1 (S=1) triplet of VB−.

### 5.2.1 Optical link budget (explicit)

The §1 emitter spec is a **radiance**, so the pump power at the sample follows from photometry, not from a free-floating watt figure. Shared emitter-side arithmetic (all variants, 1 mm² Lambertian pixel):

- 10 cd/m² at 532 nm ≈ **15 mW·sr⁻¹·m⁻²** → EL per pixel **≈ 45–50 nW** (≈ 3 µW array-wide).
- The defect PL reaching the detector is that number through the chain **defect absorption × PL quantum yield (~10 %) × escape cone ~1/(4n²) ≈ 4–6 % for n ≈ 2.1–2.4** — i.e. **pW-scale before detector responsivity** (or below, for the thin-flake variants), with the exact value set by the variant's absorption (worked example: `FABRICATION_PROTOCOL_Diamond.md` §5.2.1).
- Detector: graphene 0.01–0.1 A/W answers at tens of pA — around the 51 pA RMS TIA noise floor (HARDWARE_DESIGN.md §C1); the MoS₂ photogating channel (10–10² A/W) is the only one that can close.

If the §5.1 photodiode instead measures near "~1 mW at the sample", the emitters beat the §1 radiance target by ~4 orders of magnitude and the budget closes comfortably. Until measured, **this budget — not the 1 mW prose figure — is the planning basis** for §5.5 end-to-end claims.

---

### 5.3 Spin coherence (T₂)

9. **Pulsed ODMR** (requires pulse-capable source or fast PIN switch): run Hahn echo (π/2 – τ – π – τ – echo) at the resonance. Vary τ from 0.1–20 µs. Fit echo amplitude vs τ to exp[−(2τ/T₂)^n] with n ≈ 1–2.
10. **Expected T₂:** **0.5–5 µs at room temperature** for ensembles in exfoliated hBN, limited by ¹⁰B/¹¹B nuclear spins and paramagnetic impurities. T₁ typically 10–100 µs.

### 5.4 Emitter → detector coupling

11. Illuminate the hBN film through the CQD emitter (calibrate the pump off-chip first, e.g. 532 nm at 1 µW) and read the on-chip graphene detector photocurrent.
12. **Coupling target: 20–60 %** of the incident pump reaching the detector; the detector responsivity is computed from that known input power.

### 5.5 End-to-end functional test

13. Drive CQD pixel electrically → green EL pumps the hBN VB− ensemble → collect the ~850 nm defect PL (through the 650 nm long-pass filter) at the on-chip graphene/MoS₂ detector → its photocurrent is modulated by MW at D ≈ 3.46 GHz → the FPGA / lock-in reads the ODMR dip. A successful end-to-end ODMR with the on-chip CQD pump (no external laser) is the chip's defining demonstration.

### 5.6 Emitter↔qubit↔detector calibration (software pulse-capture)

14. **No manual pixel-perfect alignment is required.** With the stack coarse-assembled (layer-to-layer registration ~100 µm is enough), run an automated calibration sweep from the FPGA: for each emitter pixel (i,j) fire a short green pump pulse, then sample every graphene/MoS₂ detector electrode and record which one(s) show a modulated ~850 nm response under MW at D ≈ 3.46 GHz. The sweep builds a lookup table mapping **emitter pixel → qubit site → detector electrode**. Subsequent operation addresses qubits through this table, so the chip tolerates coarse mechanical stacking and needs **no camera and no micron alignment jig for readout**. (A camera on the separate FPGA controller board, if present, is used only for initial visual inspection of the assembled chip — never for qubit readout.)

---

## Section 6 — Safety

| Hazard | Control |
|---|---|
| HNO₃, HCl, HF (1 %) | Fume hood, nitrile + butyl gloves, face shield; HF → calcium gluconate gel on hand |
| NMP, anisole, acetone | Flammable; fume hood; no ignition sources |
| Hydrothermal autoclave 200 °C, pressurized | Never open hot; let cool < 60 °C; rated PTFE liner; eye protection |
| UV 365 nm exposure | UV-blocking glasses; skin cover |
| Thermal evaporator | Interlock; cool before opening; Au/Cr evaporation produces vapor |
| 532 nm laser 50 mW | Class 3B; laser goggles OD > 4 at 532 nm; beam stops |
| MW up to 6 GHz, < +20 dBm | Low risk; avoid direct contact with antenna; keep > 30 cm |
| Hot surfaces (hotplate, tube furnace) | Heat-resistant gloves; allow hotplate cool-down |
| Nanomaterials (CQDs, hBN flakes) | Treat as nuisance dust; N95; wet-clean surfaces; dispose as solid nano-waste |
| Cu etchant FeCl₃ | Corrosive; gloves; stain hazard |

**General:** Maintain an SDS binder for every chemical. Eye wash station and emergency shower accessible. No food/drink. Solvent and acid waste in separate labeled carboys.

---

## 7. Expected Performance Table

| Parameter | Target / expected | Basis |
|---|---|---|
| CQD PL peak (solution) | 450–620 nm (tunable) | Sec. 1.1, lit. |
| CQD quantum yield (solution) | 40–80 % | Sec. 1.1 |
| CQD EL turn-on voltage | 4–8 V | Sec. 1.7, 5.1 |
| CQD EL radiance | ≥ 10 cd/m² | Sec. 5.1 |
| ASE threshold (edge) | detectable, FWHM < 25 nm above threshold | Sec. 5.1 |
| CQD lasing | **NOT claimed** | see note |
| hBN flake thickness | 2–4 nm (3–8 layers) | Sec. 2.1 |
| VB− zero-field splitting D | 3.46 GHz | Sec. 2.3, 5.2 |
| Defect density (VB−) | 0.1–0.3 /µm² | Sec. 2.3 |
| ODMR contrast (ensemble) | 0.5–3 % | Sec. 5.2 |
| ODMR linewidth | 5–20 MHz | Sec. 5.2 |
| Spin coherence T₂ (RT) | 0.5–5 µs | Sec. 5.3 |
| Spin relaxation T₁ (RT) | 10–100 µs | Sec. 5.3 |
| Emitter → detector coupling | 20–60 % | Sec. 5.4 |
| Graphene photodetector responsivity | 0.01–0.1 A/W | Sec. 3.4 |
| MoS₂ photodetector responsivity | 10–10² A/W (photogating) | Sec. 3.4 |
| Operating temperature | 288–300 K (ambient) | Sec. 4.2 |
| Total chip footprint | 25 × 25 mm² | Sec. 2.4 |

---

## 8. Validation Against Known Studies

For each major claim, the supporting peer-reviewed literature is listed. Where the claim is at the edge of what has been reported, the wording is hedged. References are numbered and listed in the References section below.

### 11.1 CQD hydrothermal synthesis — yield, wavelength, QY

- **Citric acid + urea/ethylenediamine hydrothermal route** is the canonical CQD synthesis, producing blue-to-green PL with QY 40–80 %. Consistent with results reported in **Baker & Baker, *Angew. Chem. Int. Ed.* 2010** [1] (landmark review of luminescent carbon nanodots) and the large CQD literature summarized in **Lim, Shen & Gao, *Chem. Soc. Rev.* 2015** [2]. The 200 °C, 6 h hydrothermal parameters used here are within the standard ranges reported.
- **Wavelength tunability** via nitrogen dopant ratio and hydrothermal temperature is a broadly reported property of CQDs, consistent with the reviews above and with numerous primary reports.

### 11.2 CQD electroluminescence and ASE

- **Electroluminescence from CQD-polymer composites** under modest DC bias (turn-on ~5–10 V) has been reported by several groups; consistent with the CQD LED literature summarized in **Lim et al. 2015** [2] and subsequent CQD-LED reviews.
- **ASE / amplified spontaneous emission from CQDs under optical pumping** is well documented, e.g., white-light ASE from carbon nanodots under laser excitation **Zhang et al., *Opt. Mater. Express* 2012** [29], and near-unity-QY CQDs for electroluminescent LEDs **Yuan et al., *Angew. Chem. Int. Ed.* 2023** [30].
- **Electrically-pumped CQD *lasing* is NOT claimed.** True electrically-pumped lasing from CQDs has been pursued but remains difficult and controversial; we do not cite any paper as having demonstrated it unambiguously. We deliberately limit the claim to **EL and ASE**. ASE is a necessary but not sufficient precondition for lasing and does not require a coherent cavity mode.

### 11.3 Liquid exfoliation of hBN

- **NMP-based bath sonication** to produce few-layer hBN is the standard liquid-phase exfoliation route, established in **Hernandez et al., *Nat. Nanotechnol.* 2008** [3] (one-step mass production of few-layer graphene by liquid exfoliation; the method generalizes to BN, MoS₂, etc.) and in **Coleman et al., *Science* 2011** [4] ("Two-Dimensional Nanosheets Produced by Liquid Exfoliation of Layered Materials"), which explicitly reports few-layer BN in NMP with the surface-tension-matching criterion we use. The yields (~5–15 % few-layer) and flake sizes (50–300 nm) targeted here are consistent with those reports.

### 11.4 VB− defect, D ≈ 3.46 GHz, room-temperature spin

- **Discovery and room-temperature optically-detected magnetic resonance of the negatively-charged boron vacancy VB− in hBN**, with zero-field splitting **D ≈ 3.46 GHz**, optical initialization and readout: **Gottscholl et al., *Nature Materials* 19, 540–545 (2020)** [5]. This is the landmark paper for the VB− qubit and directly supports Section 2.3 and the ODMR target in Section 5.2.
- **Theoretical identification of the VB− structure and spin Hamiltonian**, including D ≈ 3.46 GHz: **Ivády et al., *npj Computational Materials* 6, 41 (2020)** [6] (ab initio theory of the negatively charged boron vacancy qubit in hBN). Supports the spin-1 triplet assignment and the magnetic-field splitting used in Section 5.2.
- **Room-temperature spin coherence (T₂ ~ µs)** of VB− ensembles: consistent with values reported in **Gottscholl et al. 2020** [5] and follow-up work by **Kianinia et al.** [7] and **Chejanovsky et al.** [8] on hBN spin defects and single-photon emitters. µs-scale T₂ at room temperature is the accepted performance envelope for as-grown/implanted VB− ensembles; isotopic engineering (¹⁰B / ¹²C purification) extends it but is outside maker-lab scope.
- **Defect density ~0.3 /µm²** is within the range routinely reported for irradiated or annealed hBN samples used for ensemble ODMR; the chemical route in Section 2.3 is a maker-lab approximation of ion-implantation results in [5–7].
- **hBN as a quantum-emitter host**: single-photon emission from hBN monolayers **Tran et al., *Nat. Nanotechnol.* 2016** [26], hBN photonics review **Caldwell et al., *Nat. Rev. Mater.* 2019** [27], and a quantum-coherent spin in hBN at ambient conditions **Stern et al., *Nat. Mater.* 2024** [28] (a carbon-related defect, distinct from VB−).

### 11.5 Emitter → detector coupling (20–60 %)

- **Butt-coupled edge emitters with sub-2 µm gaps** deliver tens-of-percent
  transfer in integrated sources, with the efficiency set by mode overlap
  between the gain strip and the receiving aperture; see polymer integrated
  optics **Bêche et al., *Microelectron. J.* 2006** [31], soft-lithography
  patterning **Xia & Whitesides 1998** [32] and **Qin et al., *Nat. Protoc.*
  2010** [33]. The geometry specified in Section 4.1 sits in that regime.

### 11.6 On-chip 2D-material photodetector responsivity

- **Graphene photodetectors**: **Xia et al., *Nat. Nanotechnol.* 2009** [9] (ultrafast graphene photodetector) and subsequent reviews establish bare-graphene responsivities of **0.01–0.1 A/W**, matching our target.
- **MoS₂ and hybrid 2D photodetectors with high responsivity (10–10² A/W) via photogating**: **Konstantatos et al., *Nat. Nanotechnol.* 2012** [10] (hybrid graphene–quantum-dot phototransistor with ultrahigh gain), ultrasensitive monolayer-MoS₂ photodetectors **Lopez-Sanchez et al., *Nat. Nanotechnol.* 2013** [24], and vertical graphene heterostructure transistors **Britnell et al., *Science* 2012** [25] establish the photogating regime used in Section 3.4.
- **On-chip integration of 2D photodetectors** over the host, for direct readout, is consistent with 2D-material photodetection reviews (e.g., F. Xia et al., *Nat. Photonics* reviews).

### 11.7 Passive thermal path for room-temperature operation

- A metal heat-spreader under the carrier with a low-resistance interface holds
  a few-watt chip within a fraction of a kelvin of ambient; TIM and finned-sink
  application notes suffice, so no landmark citation is required.

### 11.8 Related defect platforms (NV-diamond, SiC V_Si) and decoupling

The chip is VB−-centric, but the same control stack drives other room-temperature solid-state spin defects. These references support that generality and the calibration/decoupling toolbox:

- **NV centre in diamond** (D ≈ 2.87 GHz): the canonical room-temperature optically-addressable spin, reviewed by **Doherty et al., *Phys. Rep.* 2013** [11]; nanoscale magnetometry **Balasubramanian et al., *Nature* 2008** [12]; single-spin coherent oscillations **Jelezko et al., *PRL* 2004** [13]; nanoscale magnetic sensing **Maze et al., *Nature* 2008** [14]; and near-second coherence under decoupling **Bar-Gill et al., *Nat. Commun.* 2013** [23].
- **Silicon vacancy V_Si in 4H-SiC** (S = 3/2, 2|D| ≈ 70 MHz, ZPL ≈ 916 nm): room-temperature coherent control **Widmann et al., *Nat. Mater.* 2015** [16] and **Kraus et al., *Nat. Phys.* 2014** [15]; the V_Si as a quantum system **Baranov et al., *PRB* 2011** [19]; single V1 dynamics **Morioka et al., *Phys. Rev. Applied* 2022** [20]. Note: **Christle et al., *Nat. Mater.* 2015** [18] and **Christle et al., *PRX* 2017** [17] report millisecond coherence for the **SiC divacancy** (V_SiV_C, S = 1), *not* the silicon vacancy — they are cited here for the divacancy, a distinct defect.
- **Dynamical decoupling** used to extend T₂: **de Lange et al., *Science* 2010** [21] and **Ryan et al., *PRL* 2010** [22].
- **Microwave delivery**: a low-cost resonator approach for ODMR excitation **Bülau et al., *Metrology* 2025** [34].

---

*End of protocol. This document is a maker-lab demonstrator specification. It is not a medical, military, or safety-critical device specification. Where primary literature is cited, the cited paper should be consulted before relying on a specific numerical parameter in a downstream application.*

---

## References

[1] S. N. Baker and G. A. Baker, "Luminescent carbon nanodots: emergent nanolights," Angewandte Chemie International Edition 49, 6726-6744 (2010). doi:10.1002/anie.200906623

[2] S. Y. Lim, W. Shen, and Z. Gao, "Carbon quantum dots and their applications," Chemical Society Reviews 44, 362-381 (2015). doi:10.1039/C4CS00269E

[3] Y. Hernandez, V. Nicolosi, M. Lotya, F. M. Blighe, et al., "High-yield production of graphene by liquid-phase exfoliation of graphite," Nature Nanotechnology 3, 563-568 (2008). doi:10.1038/nnano.2008.215

[4] J. N. Coleman, M. Lotya, A. O'Neill, et al., "Two-dimensional nanosheets produced by liquid exfoliation of layered materials," Science 331, 568-571 (2011). doi:10.1126/science.1194975

[5] A. Gottscholl, M. Kianinia, V. Soltamov, S. Orlinskii, G. Mamin, C. Bradac, C. Kasper, K. Krambrock, A. Sperlich, M. Toth, I. Aharonovich, and V. Dyakonov, "Initialization and read-out of intrinsic spin defects in a van der Waals crystal at room temperature," Nature Materials 19, 540-545 (2020). doi:10.1038/s41563-020-0619-6

[6] V. Ivády, G. Barcza, G. Thiering, S. Li, H. Hamdi, J.-P. Chou, Ö. Legeza, and A. Gali, "Ab initio theory of the negatively charged boron vacancy qubit in hexagonal boron nitride," npj Computational Materials 6, 41 (2020). doi:10.1038/s41524-020-0305-x

[7] M. Kianinia, S. White, J. E. Fröch, C. Bradac, and I. Aharonovich, "Generation of spin defects in hexagonal boron nitride," ACS Photonics 7, 2147-2152 (2020). doi:10.1021/acsphotonics.0c00614

[8] N. Chejanovsky, A. Mukherjee, J. Geng, Y.-C. Chen, Y. Kim, A. Denisenko, A. Finkler, T. Taniguchi, K. Watanabe, D. B. R. Dasari, P. Auburger, A. Gali, J. H. Smet, and J. Wrachtrup, "Single-spin resonance in a van der Waals embedded paramagnetic defect," Nature Materials 20, 1079-1084 (2021). doi:10.1038/s41563-021-00979-4

[9] F. Xia, T. Mueller, Y.-M. Lin, A. Valdes-Garcia, and P. Avouris, "Ultrafast graphene photodetector," Nature Nanotechnology 4, 839-843 (2009). doi:10.1038/nnano.2009.292

[10] G. Konstantatos, M. Badioli, L. Gaudreau, J. Osmond, M. Bernechea, F. P. Garcia de Arquer, F. Gatti, and F. H. L. Koppens, "Hybrid graphene-quantum dot phototransistors with ultrahigh gain," Nature Nanotechnology 7, 363-368 (2012). doi:10.1038/nnano.2012.60

[11] M. W. Doherty, N. B. Manson, P. Delaney, F. Jelezko, J. Wrachtrup, and L. C. L. Hollenberg, "The nitrogen-vacancy colour centre in diamond," Physics Reports 528, 1-45 (2013). doi:10.1016/j.physrep.2013.02.001

[12] G. Balasubramanian, I. Y. Chan, R. Kolesov, et al., "Nanoscale imaging magnetometry with diamond spins under ambient conditions," Nature 455, 648-651 (2008). doi:10.1038/nature07278

[13] F. Jelezko, T. Gaebel, I. Popa, A. Gruber, and J. Wrachtrup, "Observation of coherent oscillations in a single electron spin," Physical Review Letters 92, 076401 (2004). doi:10.1103/PhysRevLett.92.076401

[14] J. R. Maze, P. L. Stanwix, J. S. Hodges, et al., "Nanoscale magnetic sensing with an individual electronic spin in diamond," Nature 455, 644-647 (2008). doi:10.1038/nature07279

[15] H. Kraus, V. A. Soltamov, D. Riedel, S. Väth, F. Fuchs, A. Sperlich, P. G. Baranov, V. Dyakonov, and G. V. Astakhov, "Room-temperature quantum microwave emitters based on spin defects in silicon carbide," Nature Physics 10, 157-162 (2014). doi:10.1038/nphys2826

[16] M. Widmann, S.-Y. Lee, T. Rendler, et al., "Coherent control of single spins in silicon carbide at room temperature," Nature Materials 14, 164-168 (2015). doi:10.1038/nmat4145

[17] D. J. Christle, P. V. Klimov, C. F. de las Casas, et al., "Isolated spin qubits in SiC with a high-fidelity infrared spin-to-photon interface," Physical Review X 7, 021046 (2017). doi:10.1103/PhysRevX.7.021046

[18] D. J. Christle, A. L. Falk, P. Andrich, P. V. Klimov, J. U. Hassan, N. T. Son, E. Janzén, T. Ohshima, and D. D. Awschalom, "Isolated electron spins in silicon carbide with millisecond coherence times," Nature Materials 14, 160-163 (2015). doi:10.1038/nmat4144

[19] P. G. Baranov, A. P. Bundakova, A. A. Soltamova, S. B. Orlinskii, I. V. Borovykh, R. Zondervan, R. Verberk, and J. Schmidt, "Silicon vacancy in SiC as a promising quantum system for single-defect and single-photon spectroscopy," Physical Review B 83, 125203 (2011). doi:10.1103/PhysRevB.83.125203

[20] N. Morioka, D. Liu, Ö. O. Soykal, I. Gediz, C. Babin, R. Stöhr, T. Ohshima, N. T. Son, J. Ul-Hassan, F. Kaiser, and J. Wrachtrup, "Spin-optical dynamics and quantum efficiency of a single V1 center in silicon carbide," Physical Review Applied 17, 054005 (2022). doi:10.1103/PhysRevApplied.17.054005

[21] G. de Lange, Z. H. Wang, D. Ristè, V. V. Dobrovitski, and R. Hanson, "Universal dynamical decoupling of a single solid-state spin from a spin bath," Science 330, 60-63 (2010). doi:10.1126/science.1192739

[22] C. A. Ryan, J. S. Hodges, and D. G. Cory, "Robust decoupling techniques to extend quantum coherence in diamond," Physical Review Letters 105, 200402 (2010). doi:10.1103/PhysRevLett.105.200402

[23] N. Bar-Gill, L. M. Pham, A. Jarmola, D. Budker, and R. L. Walsworth, "Solid-state electronic spin coherence time approaching one second," Nature Communications 4, 1743 (2013). doi:10.1038/ncomms2771

[24] O. Lopez-Sanchez, D. Lembke, M. Kayci, A. Radenovic, and A. Kis, "Ultrasensitive photodetectors based on monolayer MoS2," Nature Nanotechnology 8, 497-501 (2013). doi:10.1038/nnano.2013.100

[25] L. Britnell, R. V. Gorbachev, R. Jalil, et al., "Field-effect tunneling transistor based on vertical graphene heterostructures," Science 335, 947-950 (2012). doi:10.1126/science.1218461

[26] T. T. Tran, K. Bray, M. J. Ford, M. Toth, and I. Aharonovich, "Quantum emission from hexagonal boron nitride monolayers," Nature Nanotechnology 11, 37-41 (2016). doi:10.1038/nnano.2015.242

[27] J. D. Caldwell, I. Aharonovich, G. Cassabois, J. H. Edgar, B. Gil, and D. N. Basov, "Photonics with hexagonal boron nitride," Nature Reviews Materials 4, 552-567 (2019). doi:10.1038/s41578-019-0124-1

[28] H. L. Stern, C. M. Gilardoni, Q. Gu, et al., "A quantum coherent spin in hexagonal boron nitride at ambient conditions," Nature Materials 23, 1379-1385 (2024). doi:10.1038/s41563-024-01887-z

[29] W. F. Zhang, L. B. Tang, S. F. Yu, and S. P. Lau, "Observation of white-light amplified spontaneous emission from carbon nanodots under laser excitation," Optical Materials Express 2, 490-495 (2012). doi:10.1364/OME.2.000490

[30] T. Yuan, F. Yuan, L. Sui, Y. Zhang, Y. Li, X. Li, Z. Tan, and L. Fan, "Carbon quantum dots with near-unity quantum yield bandgap emission for electroluminescent light-emitting diodes," Angewandte Chemie International Edition 62, e202218568 (2023). doi:10.1002/anie.202218568

[31] B. Bêche, N. Pelletier, E. Gaviot, et al., "Conception of optical integrated circuits on polymers," Microelectronics Journal 37, 421-427 (2006). doi:10.1016/j.mejo.2005.07.003

[32] Y. Xia and G. M. Whitesides, "Soft lithography," Annual Review of Materials Research 28, 153-184 (1998). doi:10.1146/annurev.matsci.28.1.153

[33] D. Qin, Y. Xia, and G. M. Whitesides, "Soft lithography for micro- and nanoscale patterning," Nature Protocols 5, 491-502 (2010). doi:10.1038/nprot.2009.234

[34] A. R. Bülau et al., "A highly efficient, low-cost microwave resonator for exciting a diamond sample from a miniaturized quantum magnetometer," Metrology 5, 69 (2025). doi:10.3390/metrology5040069