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
             "patterned electroluminescent array (450–620 nm, pump band centred on "
             "532 nm) on ITO glass.",
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
                 "ITO anode → PEDOT:PSS HTL (~50 nm) → CQD-PVA active layer → "
                 "Ag cathode (1–2 mm zones).",
                 {"layers": ["ITO", "PEDOT:PSS", "CQD-PVA", "Ag"]}),
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
             "pixels in a multiplexed grid.",
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
            Step("Electrical/thermal", "—", "Insulate Peltier & laser-diode wiring; avoid shorts."),
            Step("PPE", "—", "Gloves, eye protection, ventilation for all chemicals."),
        ],
        expected="Safe reproducible fabrication.",
    ),
]


def protocol_as_dict() -> List[dict]:
    return [asdict(s) for s in CHIP_PROTOCOL]


def protocol_summary() -> dict:
    return {
        "name": "Quantum Solid State Chip (room temperature)",
        "defect": "hBN boron-vacancy (VB-)",
        "d_GHz": 3.46,
        "operating_temp": "room temperature (~300 K)",
        "coherence": "T2 ~ µs",
        "chip_area": "1–5 cm²",
        "sections": [
            {"id": s.id, "title": s.title, "goal": s.goal, "expected": s.expected,
             "step_count": len(s.steps)}
            for s in CHIP_PROTOCOL
        ],
    }