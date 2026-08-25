# Quantum Solid State Chip (Room Temperature) — Fabrication Protocol · NV-Diamond variant

**Project:** QSSC (Quantum Solid State Chip, room-temperature demonstrator)
**Document type:** Laboratory fabrication protocol (standalone variant)
**Version:** 1.0
**Variant:** NV-diamond (negatively-charged nitrogen-vacancy, NV⁻)
**Scope:** Same chip architecture as the hBN base protocol — (1) an electrically-pumped carbon-quantum-dot (CQD) green ASE/LED emitter array, (2) SU-8/PDMS polymer waveguides, and (3) an on-chip 2D-material (graphene/MoS₂) photodetector array with FPGA readout and software pulse-capture calibration — but the **middle spin-host block is a CVD diamond plate hosting NV⁻ centres** (zero-field splitting D ≈ 2.87 GHz, PL 637–800 nm, T₂ ≈ 1.8 ms at 300 K). The optical/electronic chain (green CQD pump, 650 nm red long-pass filter, graphene detector, ADF4351 MW) is **unchanged** from the hBN chip; only the host, the defect-creation step, the geometry (bulk vs 2D) and the MW frequency change.

> **Honesty note.** The NV⁻ variant is the **easiest platform swap** in the QSSC family because the NV⁻ is pumped at **532 nm green** — exactly the colour the on-chip CQD emitter already makes — and its red/NIR PL (637–800 nm) passes the **same 650 nm long-pass filter** and is read by the **same graphene/MoS₂ array** as the hBN chip. The emitter, filter, detector and FPGA are reused verbatim. What you give up relative to hBN is the 2D / van-der-Waals integrability and the atomically-flat surface proximity: diamond is a **bulk 3D plate**, so the emitter and detector sit *on top* of the diamond rather than in a stacked 2D heterostructure. What you gain is **~900× better room-temperature coherence** (T₂ 1.8 ms vs 2 µs), enough to run small quantum algorithms at 300 K with dynamical decoupling.

> **On-chip emitter (no big diodes).** This variant keeps the **on-chip CQD emitter** as the pump — no external laser diode is required. (The SiC variant is the one that needs a different on-chip emitter; diamond reuses the green CQD unchanged.)

---

## 1. Abstract

This document specifies a complete, equipment-minimal fabrication protocol for a room-temperature solid-state quantum photonic chip (QSSC) built on a **CVD diamond plate hosting NV⁻ centres**. The chip combines three functional blocks on a single 25 × 25 mm² carrier: (i) a hydrothermally-synthesized **green (~520–540 nm)** carbon quantum dot (CQD) emitter array in a polymeric LED/ASE stack (ITO/PEDOT:PSS/CQD–PVA/ZnO/Ag) that doubles as the optical pump for the NV⁻ (532 nm excitation band) — no external pump laser; (ii) a **commercial CVD diamond plate with NV⁻ centres** (D ≈ 2.87 GHz, ZPL 637 nm, phonon sideband to ~800 nm, T₂ ≈ 1.8 ms at 300 K), cleaned and (if needed) annealed to activate implanted vacancies; and (iii) SU-8/PDMS polymer waveguides routing the red/NIR NV PL to an **on-chip graphene/MoS₂ photodetector array** read directly by the FPGA, with software pulse-capture calibration (no camera, no alignment jig). A Peltier module holds the diamond at 288–300 K. All processing uses benchtop chemistry, a spin coater, a hotplate, contact-mask UV, a tube furnace (for the optional NV-activation anneal) and a ~USD 3,000–3,500 equipment footprint beyond an optical microscope.

---

## 2. Introduction & Scientific Basis

### 2.1 Why NV-diamond for the same chip

The negatively-charged nitrogen-vacancy centre (NV⁻) in diamond is the **canonical room-temperature optically-addressable spin**: spin-1 (S=1), zero-field splitting **D ≈ 2.87 GHz**, optical initialization and single-shot readout at 300 K, and Hahn-echo coherence **T₂ ≈ 1.8 ms** in ¹²C-enriched diamond (≈0.5–0.7 ms in natural-abundance diamond) — extended toward 1 s with dynamical decoupling. For the QSSC architecture this means the **same control/readout stack** as the hBN chip drives a qubit that can actually run small quantum algorithms at room temperature, not only sense.

### 2.2 Why the green CQD emitter is reused unchanged

NV⁻ is optically excited at **532 nm** — the same green band the citric-acid+urea CQDs already produce at ~220 °C hydrothermal temperature. So the entire Section-1 emitter (ITO/PEDOT:PSS/CQD–PVA/ZnO/Ag, green, silver-paste cathode) is reused **without modification**. The on-chip emitter pumps the NV⁻ directly; no external 532 nm laser is needed for the end-to-end demo.

### 2.3 Why the detector and filter are reused unchanged

NV⁻ photoluminescence is the zero-phonon line at **637 nm** plus a broad phonon sideband extending to **~800 nm** (red/NIR). The **650 nm red photographic long-pass** passes the 637–800 nm PL and blocks the 532 nm green pump — the **same filter** used for the hBN chip. The graphene/MoS₂ photodetector reads 637–800 nm; MoS₂ (monolayer bandgap ~690 nm) actually absorbs a larger share of the NV PL than it does of the hBN 850 nm emission, so the detector works **at least as well** here.

### 2.4 Architecture overview

```
                ┌─────────────────────────────────────────────┐
   top Ag cathode (silver-paste syringe) ─────────────────── │
                │  ZnO electron-transport layer (≈40 nm)     │  ← Section 1 (UNCHANGED)
                │  CQD–PVA electroluminescent layer (≈80 nm,  │
                │       green ~520–540 nm → pumps NV⁻)       │
                │  PEDOT:PSS hole-injection (≈40 nm)          │
                │  bottom ITO anode on glass/PET             │
   ─────────────┴─────────────────────────────────────────────┘
                       ↓ optical pumping / EL (532 nm)
   ┌─────────────────────────────────────────────┐
   │  CVD diamond plate (bulk, ~0.5 mm)          │  ← Section 2 (REPLACES hBN)
   │  with NV⁻ centres (D ≈ 2.87 GHz, ZPL 637)   │
   │  emitter + detector sit ON TOP (not a 2D    │
   │  stack — bulk 3D geometry)                  │
   ──────────────────────────────────────────────┘
        ↘ waveguide coupling ↙
   ┌─────────── SU-8/PDMS waveguides ───────────┐       ← Section 3/4 (UNCHANGED)
   │  → graphene / MoS₂ photodetector array     │
   └─────────────────────────────────────────────┘
   Peltier (TEC) clamped under Si carrier      ← Section 4
```

---

## 3. Materials & Bill of Materials (BOM) — deltas from the hBN base

Only the **host** and one optional anneal change. Everything else (CQD chemicals, emitter stack, graphene/MoS₂, SU-8/PDMS, Peltier, FPGA/ADF4351, 650 nm filter) is the same BOM as the hBN protocol.

### 3.1 Added / substituted reagents

| Item | Spec / source | Qty | Approx. cost |
|---|---|---|---|
| **CVD diamond plate with NV⁻ centres** | "CVD diamond with NV centres" / Type Ib diamond, ~2×2×0.5 mm or ~3×3×0.5 mm, NV density ~10¹⁰–10¹² cm⁻³ (Alibaba / Element Six / 2DBS) | 1 plate | $50–$250 |
| Alternative: HPHT Type Ib diamond (natural substitutional N, form NV⁻ on anneal) | — | 1 plate | $40–$150 |
| Boiling acid clean mix: H₂SO₄:HNO₃:H₂O₂ (3:1:1, "piranha-triacid") | — | 50 mL | $20 |
| 0.1 M HNO₃ (surface rinse) | — | 100 mL | $15 |
| (Optional, if NV not pre-activated) N₂ / forming-gas anneal consumables | — | — | $10 |

### 3.2 Removed from hBN BOM (not needed)

- hBN powder, NMP, PMMA (as hBN passivation — a thin PMMA cap is still used on the *emitter*, but not on the diamond), the wet-chemical/hBN defect-engineering reagents. **Carbon CQD synthesis reagents stay** (the emitter is identical).

### 3.3 Equipment deltas

- The tube furnace (to 1100 °C) is **only needed if the purchased plate requires an NV-activation/vacancy-anneal step** (~800 °C). Many CVD-NV plates arrive NV-active and need only a clean. If buying "NV-ready" plate, the furnace is optional.
- Everything else (hotplate, autoclave, sonicator, spin coater, UV, Peltier, sourcemeter, lock-in, 650 nm filter, ADF4351) is unchanged from the hBN BOM.

---

## Section 1 — CQD Emitter Array (UNCHANGED from hBN base)

**Goal:** Synthesize **green** (~520–540 nm) N-doped CQDs and integrate them into a patterned ITO/PEDOT:PSS/CQD–PVA/ZnO/Ag electroluminescent diode array (8 × 8 pixels, 1 mm² each) on a 25 × 25 mm² ITO-glass substrate. The green emitter doubles as the pump for the NV⁻ (532 nm band). **This section is identical to the hBN protocol Section 1** — the same citric-acid+urea hydrothermal route at ~220 °C, the same p-i-n stack, the same ZnO ETL and silver-paste cathode. No ethylenediamine is required; emission colour is set by hydrothermal temperature (~220 °C → green).

> **Key parameters (unchanged):** CQD PL peak 520–540 nm (green), EL turn-on 4–8 V, ASE detectable under overdrive (FWHM narrowing < 25 nm), target radiance ≥ 10 cd/m². The full step-by-step procedure (1.1 hydrothermal synthesis, 1.2 CQD–PVA ink, 1.3 ITO substrate, 1.4 PEDOT:PSS, 1.5 CQD emissive layer, 1.5b ZnO ETL, 1.6 silver-paste cathode, 1.7 EL/ASE check) is reproduced verbatim from the hBN base protocol — see `FABRICATION_PROTOCOL.md` Section 1. **Only the destination of the pump light changes** (NV⁻ in diamond instead of V_B⁻ in hBN).

**Timing:** ≈ 3 working days (synthesis 8 h + dialysis 48 h unattended + film stack 1 day).

---

## Section 2 — Diamond Plate Preparation + NV⁻ (REPLACES the hBN section)

**Goal:** Obtain a clean CVD diamond plate hosting optically-addressable NV⁻ centres (D ≈ 2.87 GHz) at ensemble density suitable for 8 × 8 site addressing, with the surface ready to receive the emitter/waveguide/detector stack on top.

### 2.1 Procurement

1. **Buy the plate.** Order a **CVD diamond plate with NV⁻ centres** (~2×2×0.5 mm to ~3×3×0.5 mm, optical/CVD grade, NV density ~10¹⁰–10¹² cm⁻³). Search terms: "CVD diamond NV centre", "nitrogen vacancy diamond plate", "Type Ib diamond". For a budget demonstration, a small Type Ib HPHT plate (natural substitutional nitrogen, which forms NV⁻ on anneal) is adequate. Expect ~$50–$250 retail.
2. **Spec check.** Confirm with the vendor that NV⁻ are present and optically active (vendor ODMR plot if available). If buying bare CVD N-doped diamond *without* NV⁻ formed, you must implant/anneal (Route B below) — prefer buying NV-ready to avoid an ion implanter.

### 2.2 Surface cleaning

3. **Solvent clean.** Sonicate 5 min each in acetone, IPA, DI water.
4. **Acid clean.** Boil in **H₂SO₄:HNO₃:H₂O₂ = 3:1:1** at ~90 °C for 30 min (removes graphitic/surface contamination, leaves an oxygen-terminated surface). **Cool**, then rinse copiously in DI water. (Fume hood, full PPE — strong oxidizer.)
   - Alternative: **O₂ plasma** (100 W, 10 min) for a dry, mask-friendly clean if you want to avoid hot acid.
5. **Final rinse.** 0.1 M HNO₃ dip 1 min, DI rinse, N₂ blow dry. Surface is now O-terminated, hydrophilic, ready for layer deposition.

### 2.3 NV⁻ activation (ONLY if the plate needs it)

**Route A — Pre-activated (preferred, no furnace needed).** Many CVD-NV plates arrive with NV⁻ already formed. If the vendor confirms optically-active NV⁻, **skip to 2.4**. No high-temperature step is required.

**Route B — Anneal to form NV⁻ (if vacancies are present but not yet bound to N, e.g. as-implanted or irradiated plate).**

6. **Vacuum / Ar anneal.** Place the cleaned plate in the tube furnace, pump to < 10⁻⁵ mbar (or flowing Ar / forming gas 5 % H₂). Ramp to **800 °C, 2 h**. This mobilizes vacancies, which diffuse to substitutional N and form NV⁻. Cool to < 100 °C in Ar before removing. *This step needs the tube furnace — the 350 °C hotplate cannot reach it.*
7. **Optional surface graphitization removal.** Re-do step 4 (boiling acid) for 10 min to remove any surface graphitization from the anneal.

> **Note on creating NV⁻ from scratch (not recommended for the maker route):** Growing NV⁻ requires either CVD growth with in-situ N doping (needs a CVD diamond reactor) or N⁺ ion implantation + 800–1200 °C anneal (needs an ion implanter). Both are outside maker scope. **Buy the plate with NV⁻ already in it** — this is the whole point of choosing diamond for this variant.

### 2.4 Site density and surface proximity

8. **Density.** Ensemble NV⁻ density ~10¹⁰–10¹² cm⁻³ is typical for CVD-NV plates; at the plate surface this gives resolvable ensemble spots for 8 × 8 ODMR. If single-site addressing is desired, buy a low-density (~10⁹ cm⁻³) plate.
9. **Depth caveat (honest).** Unlike 2D hBN where defects sit at the surface, NV⁻ in a bulk plate are **distributed through the ~0.5 mm thickness**. For sensing you may want shallow NV⁻ (within ~10 nm of the surface); specify "shallow NV" or "delta-doped" with the vendor. For the QSSC compute/sensor demonstrator, ensemble bulk NV⁻ is fine; the trade-off vs hBN is **worse surface proximity, far better coherence**.

### 2.5 No PMMA-on-host step

10. Unlike hBN, **no PMMA passivation coat is applied to the diamond**. Diamond is chemically inert and the NV⁻ charge state is intrinsically stable at the surface (unlike V_B⁻). Proceed directly to waveguide/emitter/detector deposition on the cleaned diamond surface (Section 4). A thin SU-8 or PDMS planarization layer may be spun on the diamond first if the surface is too rough for waveguide adhesion.

**Timing, Section 2:** clean 1 h + (optional) anneal 4 h. **Total: ~0.5–1 working day** (far faster than hBN exfoliation + defect engineering).

---

## Section 3 — On-Chip Fluorescence Photodetector Array (Graphene / MoS₂) (UNCHANGED)

**Goal:** N-electrode graphene/MoS₂ photodetector array on-chip, read directly by the FPGA, software pulse-capture calibration (Section 5.6). **Identical to the hBN protocol Section 3.** The only platform note: NV⁻ PL (637–800 nm) is **better matched to MoS₂** (monolayer bandgap ~690 nm) than hBN's 850 nm emission, so the MoS₂ photogating channel contributes more gain here; bare graphene still carries the broadband absorption up to ~800 nm.

- CVD graphene transfer, MoS₂ transfer, Au/Cr source-drain electrodes, O₂-plasma channel isolation, bias 0.1–1 V, lock-in readout — all as in the hBN base protocol. No discrete photodiodes, no external camera.

---

## Section 4 — Waveguides, Stacking (bulk geometry), Thermal, Contacts

### 4.1 SU-8 waveguides (on the diamond)

1. If the diamond surface is rough (> few nm Ra), spin a thin SU-8 2002 planarization layer (1000 rpm → ~5 µm), soft-bake, flood-expose, hard-bake 150 °C, 30 min. Then spin the **SU-8 2002 core** (2000 rpm → ~2 µm) and pattern waveguides 5–10 µm wide, 5–20 mm long as in the hBN protocol. PDMS cladding (n ≈ 1.41) over SU-8 core (n ≈ 1.58).
   - Diamond's refractive index (n ≈ 2.4) is high; keep the SU-8 planarization layer thick enough (~5 µm) that waveguide modes are not pulled into the diamond. This is the main optical difference from the hBN chip (where the SiO₂/Si carrier is a cleaner substrate).

### 4.2 Coupling the CQD emitter to the waveguide (unchanged)

2. Cleave the CQD stack so the green gain-strip edge faces the SU-8 input facet; butt-couple in the alignment jig; UV-cure NOA 61. Coupling 20–60 % as in the hBN protocol.

### 4.3 Stacking (bulk, not 2D)

3. **Stack order (bottom→top):** Si/SiO₂ carrier → Peltier TEC top plate → **diamond plate** (face up) → SU-8 waveguides → PDMS cladding → CQD emitter chip (edge-coupled) → graphene/MoS₂ detector at waveguide output. The diamond replaces the hBN-on-SiO₂ chip; the rest of the stack is the same. Bond with thin PDMS/cyanoacrylate at corners; cure 80 °C, 1 h. Wire-bond or silver-paste the CQD anode/cathode and detector source/drain to a PCB fan-out.

### 4.4 Peltier thermal management (unchanged)

4. 2-stage TEC (or TEC1-12706), NTC thermistor, PID set to **295 K ± 0.1 K**, < 5 W. Diamond's thermal conductivity (~2000 W/m·K, 4× better than Si) makes temperature stability **easier** than the hBN chip — the diamond itself is an excellent heat spreader.

### 4.5 Contacts and encapsulation (unchanged)

5. Au wire / silver-paste to a 0.1" header: ITO anode (×8 rows), Ag cathode (×8 cols), detector drains (×N), gate (×1), thermistor (×2), TEC ± (×2). Encapsulate under a 1 mm PDMS slab, leaving the waveguide output facet exposed.

### 4.6 Coplanar waveguide (MW) for ODMR (unchanged, frequency changes)

6. Hand-etch a 50 Ω CPW on FR4/Rogers; mount the diamond plate face-down over the signal line (≤ 50 µm gap) or flip-chip with anisotropic conductive paste. **Microwave frequency changes from 3.46 GHz (hBN) to 2.87 GHz (NV⁻)** — well within the ADF4351 range (35 MHz–4.4 GHz), no hardware change.

---

## Section 5 — Characterization & Testing

### 5.1 Electroluminescence (CQD array) — unchanged
Drive each pixel 0–15 V, confirm turn-on 4–8 V and broadband green EL; ASE check (FWHM narrowing < 25 nm). As in the hBN protocol.

### 5.2 ODMR of NV⁻ in diamond
4. **Optical:** on-chip green CQD emitter (~532 nm, ~1 mW at the sample) — no external laser. Collect NV⁻ PL at **637–800 nm** through the **650 nm red photographic long-pass filter** (passes the red/NIR NV PL, blocks the 532 nm green pump) into the **on-chip graphene/MoS₂ detector array**. No Si APD / discrete photodiode.
5. **Microwave:** sweep **2.80–2.94 GHz** from the function generator / ADF4351 through the CPW. Modulate MW at 1 kHz for lock-in detection of the ODMR dip.
6. **Expected resonance:** a single dip at **D ≈ 2.87 GHz** (the ¹⁴N 3-line hyperfine, A ≈ 2.16 MHz, is sub-linewidth and usually merges into one dip at B=0; it splits into a doublet under a static B₀). ODMR contrast **2–30 %** (ensemble/single), linewidth **1–10 MHz**. NV⁻ ODMR contrast is **higher** than V_B⁻ (0.5–3 %), so readout is easier.
7. **Magnetic field sweep:** apply B₀ along the NV axis; observe D shift D(B) = D₀ ± γ_e·B₀, γ_e/2π ≈ 28 GHz/T; the resonance splits into a doublet — confirms the S=1 triplet.

### 5.3 Spin coherence (T₂)
8. **Pulsed ODMR** (Hahn echo, π/2–τ–π–τ–echo). Vary τ from 1 µs to **2 ms**. Fit echo vs τ to exp[−(2τ/T₂)^n].
9. **Expected T₂:** **0.5–1.8 ms at room temperature** (≈0.5–0.7 ms natural-abundance, ≈1.8 ms in ¹²C-enriched CVD). T₁ typically **3–7 ms**. This is **~900× longer than V_B⁻** and is why this variant can run small quantum algorithms at 300 K. With dynamical decoupling (XY8/CPMG) T₂ extends further.

### 5.4 Coupling efficiency (waveguide → detector) — unchanged
Inject calibrated 532 nm light into the SU-8 facet; measure output + on-chip detector photocurrent. Target 20–60 % coupling, < 1 dB/cm loss.

### 5.5 End-to-end functional test
12. Drive the CQD pixel electrically → green EL pumps the NV⁻ ensemble → collect 637–800 nm NV PL (through the 650 nm long-pass filter) at the on-chip graphene/MoS₂ detector → photocurrent modulated by MW at **2.87 GHz** → FPGA/lock-in reads the ODMR dip. Successful end-to-end ODMR with the **on-chip CQD pump** (no external laser) is the chip's defining demonstration — identical concept to the hBN chip, at a different MW frequency and with ~900× more coherence headroom.

### 5.6 Emitter↔qubit↔detector calibration (software pulse-capture) — unchanged
13. Same automated FPGA sweep as the hBN chip: for each emitter pixel fire a green pump pulse, sample every detector electrode, record which sees a modulated NV-PL response under MW at **2.87 GHz** → build the emitter-pixel→qubit-site→detector-electrode lookup table. No camera, no alignment jig. (The only change is the MW frequency in the sweep.)

---

## Section 6 — Safety

| Hazard | Control |
|---|---|
| Boiling H₂SO₄:HNO₃:H₂O₂ (triacid) | Fume hood, nitrile + butyl gloves, face shield; strong oxidizer; add peroxide last; never seal hot |
| HNO₃, HCl | Fume hood, PPE |
| Tube furnace 800 °C | Heat-resistant gloves; cool in Ar before opening; fire risk |
| Hydrothermal autoclave 220 °C (CQD synthesis) | Never open hot; cool < 60 °C; rated PTFE liner |
| 532 nm laser / high-brightness CQD EL | Laser goggles if using an external 532 for characterization; treat bright green LED as eye hazard |
| MW up to 4 GHz, < +20 dBm | Low risk; keep > 30 cm from antenna |
| Peltier / hot surfaces | Heat-resistant gloves |
| Nanomaterials (CQDs, graphene/MoS₂) | N95; wet-clean; dispose as solid nano-waste |

---

## 7. Expected Performance Table (NV-diamond variant)

| Parameter | Target / expected | Basis |
|---|---|---|
| CQD pump (on-chip) | green 520–540 nm (unchanged) | Sec. 1 |
| CQD EL turn-on | 4–8 V | Sec. 1 |
| CQD lasing | **NOT claimed** (ASE only) | honesty note |
| **Host** | CVD diamond plate (bulk 3D) | Sec. 2 |
| **Defect** | NV⁻, S=1, D ≈ 2.87 GHz | Sec. 2, 5.2 |
| NV⁻ PL | 637 nm ZPL + sideband to ~800 nm | Sec. 5.2 |
| Optical filter | **650 nm red long-pass (unchanged)** | Sec. 5.2 |
| Defect density | ~10¹⁰–10¹² cm⁻³ (ensemble) | Sec. 2.4 |
| ODMR contrast | 2–30 % | Sec. 5.2 |
| ODMR linewidth | 1–10 MHz | Sec. 5.2 |
| **Spin coherence T₂ (RT)** | **0.5–1.8 ms** | Sec. 5.3 |
| Spin relaxation T₁ (RT) | 3–7 ms | Sec. 5.3 |
| **Compute at 300 K?** | **YES** (with DD; small Shor N=15 fits T₂) | Sec. 5.3 |
| Waveguide coupling | 20–60 % | Sec. 4.2 |
| Graphene detector responsivity | 0.01–0.1 A/W (MoS₂ 10–10² A/W) | Sec. 3 |
| Operating temperature | 288–300 K (set 295 K) | Sec. 4.4 |
| TEC power | < 5 W | Sec. 4.4 |
| Chip footprint | 25 × 25 mm² (host = diamond plate ~3×3×0.5 mm) | Sec. 2 |

---

## 8. Validation Against Known Studies

### 8.1 NV⁻ centre — D, ODMR, coherence
- **NV⁻ centre review (D ≈ 2.87 GHz, room-temperature spin):** Doherty et al., *Phys. Rep.* 528, 1–45 (2013) — the canonical reference for the NV centre Hamiltonian and optical cycle.
- **Nanoscale magnetometry with NV⁻ at ambient:** Balasubramanian et al., *Nature* 455, 648–651 (2008).
- **Single-spin coherent oscillations:** Jelezko et al., *PRL* 92, 076401 (2004).
- **Nanoscale magnetic sensing:** Maze et al., *Nature* 455, 644–647 (2008).
- **Near-second coherence under decoupling (T₂ → ~1 s):** Bar-Gill et al., *Nat. Commun.* 4, 1743 (2013).
- **1.8 ms Hahn-echo T₂ at 300 K in ¹²C-enriched diamond:** Balasubramanian et al., *Nature Materials* 8, 383 (2009) — the basis for the T₂ = 1.8 ms figure used by the QSSC simulator `NV` profile.
- **NV T₁ at room temperature:** Jarmola et al., *PRL* 108, 197601 (2012).

### 8.2 CQD green emitter / graphene detector / waveguides
- Identical to the hBN base protocol validation (citric-acid+urea CQD synthesis, CQD-LED/ASE, SU-8/PDMS waveguides, graphene/MoS₂ photodetectors) — see `FABRICATION_PROTOCOL.md` Section 8. The 532 nm pump / 650–800 nm PL / 650 nm long-pass filter combination is the standard NV-ODMR optical chain, used in every NV reference above.

### 8.3 Diamond surface preparation
- Boiling triacid (H₂SO₄:HNO₃:H₂O₂) cleaning and O₂-plasma termination of diamond surfaces are standard NV-surface preparation, consistent with the NV-magnetometry literature above; no single landmark citation is required for the clean itself.

---

*End of NV-diamond variant. This document is a maker-lab demonstrator specification. It reuses the hBN chip's emitter, filter, detector and FPGA verbatim; only the host (diamond plate), the defect-creation/anneal step, the bulk geometry and the MW frequency (2.87 GHz) change. Where primary literature is cited, consult the cited paper before relying on a specific numerical parameter.*

---

## References (platform-specific; CQD/detector/waveguide refs are in the hBN base)

- [11] M. W. Doherty et al., "The nitrogen-vacancy colour centre in diamond," Phys. Rep. 528, 1–45 (2013). doi:10.1016/j.physrep.2013.02.001
- [12] G. Balasubramanian et al., "Nanoscale imaging magnetometry with diamond spins under ambient conditions," Nature 455, 648–651 (2008). doi:10.1038/nature07278
- [13] F. Jelezko et al., "Observation of coherent oscillations in a single electron spin," PRL 92, 076401 (2004). doi:10.1103/PhysRevLett.92.076401
- [14] J. R. Maze et al., "Nanoscale magnetic sensing with an individual electronic spin in diamond," Nature 455, 644–647 (2008). doi:10.1038/nature07279
- [23] N. Bar-Gill et al., "Solid-state electronic spin coherence time approaching one second," Nat. Commun. 4, 1743 (2013). doi:10.1038/ncomms2771
- G. Balasubramanian et al., "Ultralong spin coherence time in isotopically engineered diamond," Nature Materials 8, 383–387 (2009). doi:10.1038/nmat2420
- K. Jarmola et al., "Temperature- and magnetic-field-dependent luminescence of NV⁻ in diamond," PRL 108, 197601 (2012). doi:10.1103/PhysRevLett.108.197601