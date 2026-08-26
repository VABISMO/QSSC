# qssc/protocol.py
"""Structured fabrication-protocol data served by the REST API.

A machine-readable companion to ``docs/FABRICATION_PROTOCOL.md``.  The frontend
renders this as an interactive step list; each step carries the key parameters
(timetables, temperatures, concentrations) so the protocol is actionable
without leaving the dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any


@dataclass
class Step:
    title: str
    duration: str
    detail: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Section:
    id: str
    title: str
    goal: str
    steps: List[Step] = field(default_factory=list)
    expected: str = ""


CHIP_PROTOCOL: List[Section] = [
    Section(
        id="cqD", title="1 · CQD electrically-pumped emitter array",
        goal="Biomass-derived carbon quantum dots (CQDs) self-assembled into a "
             "patterned electroluminescent array. For this chip the CQDs are "
             "synthesised green (~520–540 nm, set by hydrothermal temperature "
             "~220 °C) so the on-chip emitter doubles as the pump for the VB− "
             "(532 nm excitation band) — no external pump laser needed.",
        steps=[
            Step("Precursor solution", "15–30 min",
                 "Grind citric acid / orange peel, dissolve 3–5 g in 30–50 mL water "
                 "with 1–2 g urea, stir at 50–60 °C.",
                 {"precursor_g": "3–5", "water_mL": "30–50", "urea_g": "1–2",
                  "temp_C": "50–60"}),
            Step("Hydrothermal synthesis", "4–6 h",
                 "Seal in Teflon autoclave (≤70% fill), heat 180–220 °C for 4 h, "
                 "cool naturally. Lower T→blue, higher T→longer wavelength.",
                 {"temp_C": "180–220", "time_h": 4, "pressure_bar": "<20"}),
            Step("Purification", "30–60 min",
                 "Filter, centrifuge 3000–5000 rpm, optional 24 h dialysis.",
                 {"rpm": "3000–5000"}),
            Step("CQD array film", "1–2 h",
                 "Disperse ~10 mg/mL, spin-coat/drop-cast 100–200 nm on cleaned "
                 "ITO glass, dry 60–80 °C.",
                 {"film_nm": "100–200", "density_per_cm2": "1e9–1e11"}),
            Step("p-i-n diode stack", "≈1 h",
                 "ITO anode → PEDOT:PSS HTL (~50 nm) → CQD-PVA active layer "
                 "(green ~520–540 nm) → ZnO ETL (~40 nm) → Ag cathode "
                 "(silver-paste syringe, no evaporator).",
                 {"layers": ["ITO", "PEDOT:PSS", "CQD-PVA", "ZnO", "Ag"]}),
            Step("Anneal", "30 min", "150–250 °C, 15–30 min, air or N₂.",
                 {"temp_C": "150–250"}),
            Step("Electrical/optical test", "≈30 min",
                 "Drive 5–20 V, observe EL at 450–620 nm; pulsed 10²–10³ A/cm² "
                 "for ASE narrowing.",
                 {"voltage_V": "5–20"}),
        ],
        expected="EQY 10–20%, detectable ASE narrowing, area 1–5 cm².",
    ),
    Section(
        id="hbn", title="2 · hBN exfoliation + defect engineering + PMMA",
        goal="Few-layer hBN flakes with optically addressable VB- spin defects "
             "(D ≈ 3.46 GHz) embedded in a PMMA matrix.",
        steps=[
            Step("Liquid exfoliation", "1–2 h",
                 "100–200 mg hBN powder in 20–50 mL IPA, sonicate 1–2 h, "
                 "centrifuge 1500–3000 rpm, collect supernatant.",
                 {"hBN_mg": "100–200", "solvent": "IPA", "layers": "2–10"}),
            Step("Defect engineering", "1–3 h",
                 "Gold standard: create VB- by irradiation (neutron, or focused "
                 "ion beam Xe/Ar/N), then vacuum anneal 800–900 °C (Ar/N₂) 1–2 h to "
                 "activate. UV-ozone 10–30 min is a milder alternative. (Tube "
                 "furnace required — see BOM; the benchtop hotplate cannot reach "
                 "these temperatures.)",
                 {"D_GHz": 3.46, "density_per_um2": 0.30, "anneal_C": "800–900"}),
            Step("PMMA passivation + transfer", "1–2 h",
                 "Spin PMMA 950K A7 (500→5000 rpm), bake 160 °C; a thin PMMA coat "
                 "(few hundred nm) passivates the hBN surface and cuts surface "
                 "dephasing noise. Optional wet transfer to target substrate.",
                 {"pmma_nm": "200–500", "passivation": "PMMA"}),
        ],
        expected="hBN ~2–4 nm (3–8 layers) in PMMA, ~0.3 emitters/µm², room-T T2 ~µs.",
    ),
    Section(
        id="sensor", title="3 · On-chip 2D photodetector array",
        goal="Camera-free fluorescence readout: graphene/MoS₂ photoconductive "
             "pixels in a multiplexed N-electrode grid, read directly by the "
             "FPGA. Software pulse-capture calibration maps emitter pixels → "
             "qubit sites → detector electrodes, so no manual alignment is needed. "
             "A 650 nm red long-pass filter blocks the green pump and passes the "
             "~850 nm defect PL.",
        steps=[
            Step("Dispersion", "30 min",
                 "100–200 mg graphene/MoS₂ in 20 mL ethanol, sonicate 30 min."),
            Step("Patterning + deposit", "1–2 h",
                 "Drop-cast into resin-moulded sites (~20–30 µm), add optical "
                 "isolation trenches, dry 80 °C."),
            Step("Grid electrodes + anneal", "30 min",
                 "Cu/Ag ink row/column grid, cure 100 °C, anneal 150–200 °C.",
                 {"responsivity_AW": "0.05–0.3", "frame_Hz": "10–100"}),
        ],
        expected="On-chip scanning readout, responsivity 0.05–0.3 A/W.",
    ),
    Section(
        id="assembly", title="4 · Waveguides, stacking & thermal management",
        goal="Couple CQD emission into hBN defects and read fluorescence on-chip, "
             "with active Peltier cooling.",
        steps=[
            Step("Waveguides", "1–2 h",
                 "SU-8 (~2–5 µm) UV-patterned or PDMS cast; 10–50 µm channels, "
                 "n≈1.5–1.6, loss 0.5–2 dB/cm.",
                 {"coupling_pct": "20–60"}),
            Step("Layer stacking + bond", "≈1 h",
                 "Align PMMA/hBN over waveguides, dry-transfer, bond 100–150 °C "
                 "15–30 min."),
            Step("Thermal management", "≈1 h",
                 "Cu/Al heat-spreader + Peltier (TEC1-12706) + liquid loop.",
                 {"peltier_V": "5–12"}),
            Step("Contacts + encapsulation", "30 min",
                 "Extend Cu contacts, add microwave strips for ODMR, encapsulate "
                 "PMMA/epoxy, cure 80 °C."),
            Step("Final test", "30 min",
                 "Anneal 150 °C 15 min, verify EL→waveguide→hBN→sensor chain; "
                 "ODMR with microwave source.",
                 {"T2_us": "~µs", "coherence": "room temperature"}),
        ],
        expected="Integrated 1–5 cm² demonstrator, 20–60% photon transfer, room-T spin readout.",
    ),
    Section(
        id="safety", title="5 · Safety",
        goal="Keep the maker-lab build safe.",
        steps=[
            Step("Pressure", "—", "Autoclave at 220 °C is a pressure hazard; use a ≥3 MPa rated vessel."),
            Step("Solvents", "—", "NMP is toxic — substitute IPA. Ventilate for UV resins."),
            Step("Electrical/thermal", "—", "Insulate Peltier & CQD emitter-driver wiring; avoid shorts."),
            Step("PPE", "—", "Gloves, eye protection, ventilation for all chemicals."),
        ],
        expected="Safe reproducible fabrication.",
    ),
]


# ---------------------------------------------------------------------------
# NV-diamond variant — same chip architecture, middle block = CVD diamond NV⁻
# plate. The green CQD emitter, 650 nm long-pass filter, graphene/MoS₂ detector
# and ADF4351 MW are REUSED unchanged; only the host, defect-creation step,
# bulk geometry and MW frequency (2.87 GHz) change. Easiest platform swap.
# ---------------------------------------------------------------------------
NV_PROTOCOL: List[Section] = [
    Section(
        id="cqd", title="1 · CQD electrically-pumped emitter array (green, unchanged)",
        goal="Identical to the hBN base: green (~520–540 nm) citric-acid+urea "
             "CQDs in an ITO/PEDOT:PSS/CQD-PVA/ZnO/Ag p-i-n stack with a "
             "silver-paste cathode. The green emitter doubles as the pump for "
             "the NV⁻ (532 nm excitation band) — no external pump laser, no "
             "big diode. Emission colour is set by hydrothermal temperature "
             "(~220 °C → green); no ethylenediamine required.",
        steps=[
            Step("Precursor + hydrothermal", "8 h + 48 h dialysis",
                 "1 g citric acid + 1 g urea in 10 mL DI water; autoclave "
                 "~220 °C, 6–10 h → green CQDs (~520–540 nm). Purify (centrifuge, "
                 "0.22 µm filter, dialysis MWCO 1 kDa).",
                 {"precursor_g": "1 + 1", "temp_C": 220, "time_h": "6–10"}),
            Step("p-i-n diode stack (unchanged)", "≈1 h",
                 "ITO anode → PEDOT:PSS HTL (~50 nm) → CQD-PVA active "
                 "(green ~520–540 nm) → ZnO ETL (~40 nm) → Ag cathode "
                 "(silver-paste syringe, no evaporator).",
                 {"layers": ["ITO", "PEDOT:PSS", "CQD-PVA", "ZnO", "Ag"]}),
            Step("EL / ASE check", "≈30 min",
                 "Drive 5–15 V; turn-on 4–8 V; green EL. ASE = FWHM narrowing "
                 "< 25 nm under overdrive (ASE, not lasing).",
                 {"voltage_V": "4–8", "el_nm": "520–540"}),
        ],
        expected="Green EL 520–540 nm, turn-on 4–8 V, ASE detectable; pumps NV⁻.",
    ),
    Section(
        id="diamond", title="2 · CVD diamond plate + NV⁻ (replaces hBN)",
        goal="A clean CVD diamond plate hosting NV⁻ centres (D ≈ 2.87 GHz, "
             "ZPL 637 nm, PL 637–800 nm, T₂ ≈ 1.8 ms at 300 K). Bulk 3D "
             "geometry — emitter and detector sit ON TOP, not a 2D stack. "
             "No PMMA passivation on the host (NV⁻ is intrinsically stable).",
        steps=[
            Step("Procurement", "—",
                 "Buy a CVD diamond plate with NV⁻ centres (~2×2×0.5 mm to "
                 "3×3×0.5 mm, NV ~1e10–1e12 cm⁻³). Search 'CVD diamond NV', "
                 "'Type Ib diamond'. ~$50–$250. Prefer NV-ready (no furnace).",
                 {"host": "CVD diamond plate", "defect": "NV⁻"}),
            Step("Surface clean", "≈1 h",
                 "Solvent sonicate (acetone/IPA/DI); boil in H₂SO₄:HNO₃:H₂O₂ "
                 "3:1:1 at 90 °C, 30 min (or O₂ plasma 100 W, 10 min). 0.1 M "
                 "HNO₃ dip, DI rinse, N₂ dry. O-terminated, hydrophilic.",
                 {"clean": "triacid or O₂ plasma", "anneal_C": "n/a"}),
            Step("NV⁻ activation (only if needed)", "≈4 h",
                 "If vacancies not yet bound to N: vacuum/Ar anneal 800 °C, "
                 "2 h in the tube furnace to mobilize vacancies → NV⁻. "
                 "Skip if buying NV-ready plate (no furnace needed).",
                 {"anneal_C": 800, "atmosphere": "Ar/vacuum", "optional": True}),
            Step("Depth / density note", "—",
                 "Bulk NV⁻ distributed through ~0.5 mm; for shallow sensing "
                 "buy 'shallow NV'/'delta-doped'. Ensemble NV is fine for the "
                 "compute/sensor demo; trade-off vs hBN = worse surface "
                 "proximity, ~900× better coherence.",
                 {"depth": "bulk 0.5 mm (shallow optional)"}),
        ],
        expected="Clean diamond plate, NV⁻ ODMR-ready, D ≈ 2.87 GHz.",
    ),
    Section(
        id="sensor", title="3 · On-chip graphene/MoS₂ detector array (unchanged)",
        goal="Identical to the hBN base: N-electrode graphene/MoS₂ "
             "photoconductive pixels, FPGA-read, software pulse-capture "
             "calibration. NV⁻ PL (637–800 nm) is BETTER matched to MoS₂ "
             "(monolayer bandgap ~690 nm) than hBN's 850 nm, so the MoS₂ "
             "photogating channel contributes MORE gain here. No discrete "
             "photodiodes, no external camera.",
        steps=[
            Step("Graphene/MoS₂ transfer + electrodes", "2–3 h",
                 "CVD graphene + MoS₂ transfer onto patterned Au/Cr "
                 "source-drain (100 µm channel); O₂-plasma isolation into N "
                 "detectors; bias 0.1–1 V; lock-in readout.",
                 {"responsivity_AW": "0.01–0.1 (graphene), 10–10² (MoS₂)",
                  "pl_nm": "637–800"}),
        ],
        expected="On-chip readout of NV PL 637–800 nm; MoS₂ gain ≥ hBN case.",
    ),
    Section(
        id="assembly", title="4 · Waveguides, bulk stacking, filter, thermal",
        goal="SU-8/PDMS waveguides on the diamond (planarize first; diamond "
             "n≈2.4 is high), 650 nm red long-pass filter (UNCHANGED from hBN), "
             "Peltier at 295 K, CPW for MW at 2.87 GHz.",
        steps=[
            Step("Waveguides on diamond", "1–2 h",
                 "SU-8 planarization ~5 µm (diamond n≈2.4 high-index); SU-8 "
                 "2002 core ~2 µm, 5–10 µm wide; PDMS cladding. Coupling 20–60%.",
                 {"coupling_pct": "20–60", "index_n": 2.4}),
            Step("Stacking (bulk)", "≈1 h",
                 "Carrier → Peltier → diamond plate (face up) → waveguides → "
                 "PDMS → CQD emitter (edge-coupled) → detector. Bond corners; "
                 "cure 80 °C 1 h."),
            Step("Filter (UNCHANGED)", "—",
                 "650 nm red photographic long-pass: passes NV PL 637–800 nm, "
                 "blocks 532 nm green pump. Same filter as the hBN chip.",
                 {"filter": "650 nm long-pass", "passes_nm": "637–800"}),
            Step("Thermal management", "≈1 h",
                 "2-stage TEC + NTC + PID 295 K ±0.1 K, <5 W. Diamond thermal "
                 "conductivity ~2000 W/m·K makes stability EASIER than hBN.",
                 {"peltier_V": "5–12", "temp_K": 295}),
            Step("CPW for MW", "≈30 min",
                 "50 Ω CPW on FR4/Rogers; diamond face-down over signal line. "
                 "MW frequency = 2.87 GHz (ADF4351 covers it, no HW change).",
                 {"mw_GHz": 2.87}),
        ],
        expected="Integrated demonstrator, 20–60% coupling, room-T NV readout.",
    ),
    Section(
        id="safety", title="5 · Safety",
        goal="Keep the maker-lab build safe (diamond variant).",
        steps=[
            Step("Triacid boil", "—", "H₂SO₄:HNO₃:H₂O₂ 90 °C: fume hood, butyl gloves, face shield; strong oxidizer; add peroxide last."),
            Step("Tube furnace", "—", "800 °C anneal (if used): heat-resistant gloves; cool in Ar before opening."),
            Step("Autoclave", "—", "CQD synthesis 220 °C pressurized: cool < 60 °C before opening; rated liner."),
            Step("PPE", "—", "Gloves, eye protection, ventilation for all chemicals; nanomaterial waste stream."),
        ],
        expected="Safe reproducible fabrication.",
    ),
]


# ---------------------------------------------------------------------------
# SiC variant — same chip architecture, middle block = 4H-SiC wafer with V_Si
# (V2, S=3/2, 2|D|=70 MHz, ZPL 916 nm, T₂≈40 µs@300K). Three real changes:
# (i) on-chip emitter shifts green(532)→red/NIR(730–760) via CuInS₂/ZnS (CIS)
# colloidal QDs — on-chip, NO big diode (carbon CQDs cannot reach 730–760 nm);
# (ii) filter shifts 650→850 nm long-pass; (iii) detector is graphene-led for IR.
# ---------------------------------------------------------------------------
SIC_PROTOCOL: List[Section] = [
    Section(
        id="cqd", title="1 · CIS/ZnS QD emitter array (red/NIR, CHANGED)",
        goal="On-chip CuInS₂/ZnS (CIS) colloidal quantum-dot LED emitting "
             "~730–760 nm to pump V_Si (optical excitation below its 916 nm "
             "ZPL). ITO/PEDOT:PSS/poly-TPD/CIS-ZnS/ZnO/Ag QLED stack, "
             "silver-paste cathode — NO big diode. Carbon CQDs cannot reach "
             "730–760 nm (ceiling green ~540 nm), so semiconductor CIS QDs "
             "are required. CIS synthesis is hot-injection (Schlenk/Ar), a "
             "step up from the carbon autoclave, but keeps on-chip.",
        steps=[
            Step("CIS hot-injection synthesis", "≈1 day",
                 "Under Ar: 0.5 mmol CuI + 1 mmol In(OAc)₃ in oleylamine/ODE/"
                 "oleic acid; degas 110 °C; heat 230 °C; inject 1-dodecanethiol; "
                 "grow 20 min at 230–240 °C → larger cores → 730–760 nm. "
                 "ZnS shell: zinc stearate 200 °C 20 min (QY 30–60%). Purify "
                 "(ethanol precipitate, redisperse in toluene/hexane).",
                 {"emission_nm": "730–760", "core_nm": "3.5–4.5",
                  "atmosphere": "Ar/Schlenk", "qy_pct": "30–60"}),
            Step("CIS-QD ink (non-polar host)", "≈30 min",
                 "CIS-ZnS QDs ~10–15 mg/mL in poly-TPD/chlorobenzene (non-polar; "
                 "CIS QDs are hydrophobic — NOT water/PVA). Filter 0.45 µm PTFE.",
                 {"host": "poly-TPD", "solvent": "chlorobenzene/toluene"}),
            Step("p-i-n QLED stack", "≈1 h",
                 "ITO anode → PEDOT:PSS (~40 nm) → poly-TPD HTL (~40 nm, bridges "
                 "polar PEDOT:PSS to non-polar QDs) → CIS-ZnS active (~70 nm) "
                 "→ ZnO ETL (~40 nm, transparent to 730–916 nm) → Ag cathode "
                 "(silver-paste syringe).",
                 {"layers": ["ITO", "PEDOT:PSS", "poly-TPD", "CIS-ZnS", "ZnO", "Ag"]}),
            Step("EL / ASE check", "≈30 min",
                 "Drive 3–8 V; red/NIR EL ~730–760 nm (check with night-vision "
                 "viewer/InGaAs spectrometer; 916 nm near Si detector cutoff). "
                 "ASE = FWHM narrowing < 30 nm. ASE, not lasing.",
                 {"voltage_V": "3–8", "el_nm": "730–760"}),
        ],
        expected="Red/NIR EL 730–760 nm, turn-on 3–8 V, ASE detectable; pumps V_Si.",
    ),
    Section(
        id="sic", title="2 · 4H-SiC wafer + V_Si (replaces hBN)",
        goal="A commercial 4H-SiC wafer hosting V_Si (V2) centres (S=3/2, "
             "2|D|≈70 MHz, ZPL 916 nm, T₂≈40 µs@300K, ~20× better than hBN). "
             "Easiest host in the QSSC family: commercial wafer + irradiation "
             "+ anneal (no exfoliation, no 2D transfer). Bulk 3D geometry; "
             "emitter/detector on top. No PMMA on host.",
        steps=[
            Step("Procurement", "—",
                 "Buy 4H-SiC (semi-insulating or N-doped, double-side polished, "
                 "5×5 mm to 2-inch). Mass-produced power wafer: ~$30–$120. "
                 "V_Si often present as-grown at low density.",
                 {"host": "4H-SiC wafer", "defect": "V_Si (V2)"}),
            Step("Defect engineering", "1–3 h + irradiation turnaround",
                 "Route A: electron (2 MeV, ~1e18 cm⁻²) or proton irradiation "
                 "creates V_Si; anneal Ar/N₂ 800–1200 °C 1–2 h (tube furnace; "
                 "SiC robust to 1600 °C, wide/forgiving window). Route B: if "
                 "as-grown wafer shows ODMR, skip irradiation/anneal.",
                 {"irradiation": "e⁻/p⁺ 1e18 cm⁻²", "anneal_C": "800–1200"}),
            Step("Surface clean", "≈1 h",
                 "Solvent sonicate; dilute HF (49%:H₂O 1:10, 60 s — calcium "
                 "gluconate gel on hand) removes native SiO₂; or O₂ plasma. "
                 "SiC surfaces are robust (no boiling triacid needed).",
                 {"clean": "dilute HF or O₂ plasma", "anneal_C": "n/a"}),
            Step("Depth / density note", "—",
                 "Bulk V_Si through ~0.5 mm — WORST surface proximity of the "
                 "three platforms (shallow V_Si via low-energy implant is out "
                 "of maker scope). Trade-off vs hBN = worst proximity, ~20× "
                 "better coherence, easiest host.",
                 {"depth": "bulk 0.5 mm"}),
        ],
        expected="Clean 4H-SiC wafer, V_Si ODMR-ready, 2|D| ≈ 70 MHz.",
    ),
    Section(
        id="sensor", title="3 · On-chip graphene/MoS₂ detector array (graphene-led for IR)",
        goal="Same N-electrode array concept as the hBN base, but the 916 nm "
             "V_Si PL is in the IR where MONOLAYER MoS₂ (bandgap ~690 nm) does "
             "NOT absorb → MoS₂ photogating gain is lost. Use GRAPHENE-led "
             "(broadband, absorbs 916 nm) or FEW-LAYER MoS₂ (bandgap ~1.2 eV "
             "≈ 1030 nm, absorbs 916) to restore gain. FPGA-read, software "
             "pulse-capture calibration. No discrete photodiodes, no camera.",
        steps=[
            Step("Graphene + few-layer MoS₂ + electrodes", "2–3 h",
                 "CVD graphene + FEW-LAYER MoS₂ (not monolayer) on Au/Cr "
                 "source-drain; O₂-plasma isolation; bias 0.1–1 V; lock-in. "
                 "Graphene carries 916 nm absorption; few-layer MoS₂ adds "
                 "photogating gain.",
                 {"responsivity_AW": "0.01–0.1 (graphene), few-layer MoS₂ restores gain",
                  "pl_nm": 916, "mos2": "few-layer"}),
        ],
        expected="On-chip readout of V_Si PL 916 nm via graphene.",
    ),
    Section(
        id="assembly", title="4 · Waveguides, bulk stacking, 850 nm filter, thermal",
        goal="SU-8/PDMS waveguides on SiC (planarize; n≈2.6 high), 850 nm "
             "long-pass filter (CHANGED — 650 would pass the 730–760 pump), "
             "Peltier at 295 K, CPW for MW at 70 MHz.",
        steps=[
            Step("Waveguides on SiC", "1–2 h",
                 "SU-8 planarization ~5 µm (SiC n≈2.6 very high); SU-8 2002 "
                 "core ~2 µm, 5–10 µm wide; PDMS cladding. Coupling 20–60%.",
                 {"coupling_pct": "20–60", "index_n": 2.6}),
            Step("Stacking (bulk)", "≈1 h",
                 "Carrier → Peltier → SiC wafer (face up) → waveguides → PDMS → "
                 "CIS emitter (edge-coupled) → 850 nm LP filter → detector. "
                 "Bond corners; cure 80 °C 1 h."),
            Step("Filter (CHANGED)", "—",
                 "850 nm long-pass: passes V_Si PL 916 nm, blocks CIS pump "
                 "730–760 nm. The hBN chip's 650 nm filter is NOT used (it "
                 "would pass the 730–760 pump). Pump at ~730 nm for cleanest "
                 "separation.",
                 {"filter": "850 nm long-pass", "passes_nm": 916}),
            Step("Thermal management", "≈1 h",
                 "TEC + NTC + PID 295 K ±0.1 K, <5 W. SiC thermal conductivity "
                 "~370 W/m·K; easy stability.",
                 {"peltier_V": "5–12", "temp_K": 295}),
            Step("CPW for MW", "≈30 min",
                 "50 Ω CPW; SiC face-down over signal line. MW frequency = "
                 "70 MHz (V_Si V2). ADF4351 covers it (35 MHz–4.4 GHz); at "
                 "70 MHz a low-freq generator also works. No HW change.",
                 {"mw_GHz": 0.070}),
        ],
        expected="Integrated demonstrator, 20–60% coupling, room-T V_Si readout.",
    ),
    Section(
        id="safety", title="5 · Safety",
        goal="Keep the maker-lab build safe (SiC variant).",
        steps=[
            Step("Schlenk/hot flask", "—", "230–240 °C Ar hot-injection: heat-resistant gloves, confirm Ar flow, fire risk with organics."),
            Step("Oleylamine/thiol/ODE", "—", "High-bp odorous organics: fume hood, no ignition, capture waste."),
            Step("HF (dilute)", "—", "Calcium gluconate gel on hand, face shield, butyl gloves, fume hood."),
            Step("Tube furnace", "—", "800–1200 °C anneal: heat-resistant gloves; cool before opening."),
            Step("In/Cu salts + CIS QDs", "—", "Moderately toxic: N95, gloves, fume hood for synthesis; dispose as heavy-metal solid waste."),
            Step("PPE", "—", "Eye protection, ventilation; nanomaterial waste stream."),
        ],
        expected="Safe reproducible fabrication.",
    ),
]


# Registry keyed by the SAME profile keys the simulator uses (qssc.defects.PROFILES).
CHIP_PROTOCOLS: Dict[str, List[Section]] = {
    "hBN-VB": CHIP_PROTOCOL,    # hBN base (default)
    "NV": NV_PROTOCOL,           # NV-diamond variant
    "SiC-VSi": SIC_PROTOCOL,     # SiC V_Si variant
}


def protocol_as_dict(platform: str = "hBN-VB") -> List[dict]:
    return [asdict(s) for s in CHIP_PROTOCOLS.get(platform, CHIP_PROTOCOL)]


def protocol_summary(platform: str = "hBN-VB") -> dict:
    proto = CHIP_PROTOCOLS.get(platform, CHIP_PROTOCOL)
    summaries = {
        "hBN-VB": dict(name="Quantum Solid State Chip (room temperature)",
                        defect="hBN boron-vacancy (VB-)", d_GHz=3.46,
                        operating_temp="room temperature (~300 K)",
                        coherence="T2 ~ 2 µs", chip_area="1–5 cm²",
                        host="hBN 2D film", pl_nm="~850", filter="650 nm long-pass"),
        "NV": dict(name="Quantum Solid State Chip (room temperature) · NV-diamond",
                    defect="diamond nitrogen-vacancy (NV-)", d_GHz=2.87,
                    operating_temp="room temperature (~300 K)",
                    coherence="T2 ~ 1.8 ms (computes at 300 K)", chip_area="1–5 cm²",
                    host="CVD diamond plate (bulk)", pl_nm="637–800",
                    filter="650 nm long-pass (unchanged)"),
        "SiC-VSi": dict(name="Quantum Solid State Chip (room temperature) · SiC",
                         defect="silicon-carbide silicon-vacancy (V_Si)", d_GHz=0.070,
                         operating_temp="room temperature (~300 K)",
                         coherence="T2 ~ 40 µs", chip_area="1–5 cm²",
                         host="4H-SiC wafer (bulk)", pl_nm="~916",
                         filter="850 nm long-pass (changed)"),
    }
    s = summaries.get(platform, summaries["hBN-VB"])
    s["platform"] = platform
    s["sections"] = [
        {"id": x.id, "title": x.title, "goal": x.goal, "expected": x.expected,
         "step_count": len(x.steps)}
        for x in proto
    ]
    return s


def protocol_platforms() -> List[dict]:
    """List the available fabrication-protocol variants."""
    out = []
    for key, proto in CHIP_PROTOCOLS.items():
        s = protocol_summary(key)
        out.append({"key": key, "defect": s["defect"], "d_GHz": s["d_GHz"],
                     "host": s["host"], "coherence": s["coherence"],
                     "step_count": sum(len(x.steps) for x in proto)})
    return out