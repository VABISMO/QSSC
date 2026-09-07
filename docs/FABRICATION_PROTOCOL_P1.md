# QSSC-P1 — Pin-scaffold fabrication: pin-molded fluorescence sensor + pin-molded CQD array (CNC 3018, 1 mm bit)

**Project:** QSSC (Quantum Solid State Chip, room-temperature demonstrator)
**Document:** Protocol for the **second prototype** — a **commercial pin-header scaffold** replaces the P0 hand-bonded magnet-wire fan-out, and **both** the detector and the emitter are built by **molding paste around the pins** (symmetric process).
**Version:** P1 · 0
**Scope:** Use **standard commercial PCB pin headers** (2.54 mm pitch, 0.64 mm square, tin-plated) as the per-pixel **electrode AND the plug-in fan-out** in one part. CNC a 20×20×3 mm support with an **8×8 = 64 hole matrix** sized for the pins. Build **two devices on the same scaffold**, by the SAME pin-molding process:

- **Fluorescence sensor** (the graphene/MoS₂ photodetector) — **pin-molded paste**: fill each hole with a **graphene paste**, insert the pin, let it dry hardening into the shape of pin + hole; then repeat with a **MoS₂ + PEDOT:PSS paste**. Each pin becomes a coaxial **pin / graphene / MoS₂** pixel; the **ITO glass under the support is the common back-electrode** (DET_COMMON).
- **CQD array** (the green emitter) — **pin-molded paste, symmetric to the detector**: mold the CQD-LED stack around each pin in the hole, layer by layer — **ZnO paste (inner, ETL) → CQD paste (EML) → PEDOT:PSS paste (outer, HIL)**. Pin = cathode, ITO glass under the support = EM_ANODE anode. The paste layers interdiffuse (not as sharp as a spin-coated 10–80 nm stack), so it is a **weaker/rougher LED than a spin-coated stack**, but it emits and the process is symmetric to the detector (see §2c).

> **Why this P1.** P0 (see `FABRICATION_PROTOCOL_P0.md`) is the full vertical 3-layer stack, but its 64+64 = 128 pixel contacts are brought out by **hand-bonded magnet wire** to 130 carrier edge holes — slow, fragile, and the bare two-face die has no off-the-shelf socket (custom pogo clamshell only). P1 replaces that with a **pin-header scaffold**: each pixel's electrode is a commercial pin that **solders straight into a standard 2.54 mm female socket**. No wire-bonding, no pogo clamshell, no fan-out wires, no spin-coater. The pin is the electrode AND the fan-out. The same CNC'd 64-hole support serves both devices.

---

## 1. What P1 validates

| Phase | Device | Demonstration |
|---|---|---|
| **F1-D** | Pin-scaffold fluorescence sensor (graphene/MoS₂, pin-molded paste) | 64 individually-addressable photodetector pixels, each a pin → graphene → MoS₂ → ITO vertical photogating junction; photocurrent under 532 nm / NV PL |
| **F1-E** | Pin-molded CQD array (pin-molded ZnO/CQD/PEDOT paste stack + 64-pin cathode scaffold) | 64 individually-addressable green CQD-LED pixels, EL surface-normal up; pin-molded symmetric to the detector, no silver stencil, no spin-coater, no fan-out wires |
| **F2** | Stack + the P0 L2 entanglement bus | Reuse the P0 L2 TEOS+NV serpentine bus unchanged (the pin-scaffold only changes how the detector and emitter are fabricated/contacted, not the bus) |

**Pin count: 8×8 = 64** (user-confirmed), matching the P0 64-pixel grid. An earlier 48-pin draft is superseded.

---

## 2. Architecture — the pin-header scaffold

The pin-header scaffold is one idea applied two ways, both by the SAME pin-molding process. A **standard 2.54 mm pitch male pin header** (the kind soldered into PCBs) is cut to length. Its pins are **molded into the plastic strip at 2.54 mm pitch** — the pitch is fixed, so the CNC hole grid MUST be 2.54 mm (not the P0 2.5 mm). The 1 mm flat end-mill **circle-bores 1.5 mm holes** as **linear-segment polygon circles** (G1 XY moves, NOT G02 arcs — some gcode viewers misdraw the G02 I/J arc-centre offset as a diagonal chord, which makes the square grid *look* diagonal; linear segments render as a clean circle in every viewer); a 0.64 mm square pin leaves **~0.43 mm radial clearance** — the paste annulus, the same for both devices.

```
   PIN-HEADER SCAFFOLD (20x20x3 mm support, 64 holes @ 2.54 mm pitch, 1.5 mm circle-bored)

      ┌──────────────────────────────────────┐   ◀── pin heads / socket side
      │  ●  ●  ●  ●  ●  ●  ●  ●   row 1        │       (plugs into a 2.54 mm
      │  ●  ●  ●  ●  ●  ●  ●  ●   row 2        │        female socket on the
      │  ●  ●  ●  ●  ●  ●  ●  ●   ...   8x8    │        breakout motherboard)
      │  ...                                  │
      │  ●  ●  ●  ●  ●  ●  ●  ●   row 8        │
      └──────────────────────────────────────┘
            │ │ │                        │ │     ◀── pin shafts through the
            v v v                        v v         1.5 mm circle-bored holes
            ┌──────────────────────────────�         (0.43 mm clearance = paste
            │  ITO glass 20x20 (DET_COMMON │         annulus, both devices)
            │  for the sensor / EM_ANODE    │  ◀── ITO film = common back-electrode
            │  for the CQD array)           │      (detector) / common anode (emitter)
            └──────────────────────────────┘

   DETECTOR pixel (pin-molded paste, vertical junction):
        pin shaft  ── graphene skin (dried) ── MoS2+PEDOT:PSS skin (dried)
                 └──► paste column ──► ITO (DET_COMMON)
        light (NV PL 637-800 nm) -> MoS2 photogates -> photocurrent pin-to-ITO

   EMITTER pixel (pin-molded paste, symmetric to the detector):
        pin shaft (cathode) ── ZnO skin (inner, ETL) ── CQD paste (EML)
                 ── PEDOT:PSS skin (outer, HIL) ──► paste column ──► ITO (EM_ANODE)
        the paste layers interdiffuse -> weaker/rougher LED, but it emits
```

**The 64 pins span 17.78×17.78 mm** (7×2.54), centred on the 20×20 support (first pin at 1.11 mm). The array fits within the 20×20 ITO glass with a 1.11 mm margin. Holes are drilled in **boustrophedon (snake) row-major order** — row 1 left→right, row 2 right→left, … — so every between-hole travel is a short horizontal step (or a short vertical step between rows); no long diagonal rapids cross the matrix, and the toolpath reads as straight aligned rows matching the pin matrix.

### 2c. Why the emitter is pin-molded (symmetric), and the trade-off

The user chose to make the emitter **symmetric to the detector** — the same pin-molding process, molding the CQD-LED stack around each pin layer by layer (ZnO → CQD → PEDOT:PSS), rather than spin-coating a thin-film stack and using the pins as bare cathode contacts.

A CQD-LED is normally a **stack of distinct 10–80 nm layers with sharp interfaces** — ITO / PEDOT:PSS (HIL) / CQD-PVA (EML) / ZnO (ETL) / cathode — built by spin-coating with orthogonal solvents so each layer does not dissolve the one below. Pin-molding replaces those with **paste layers dried in a ~0.43 mm annulus**: the layers **interdiffuse** as they dry, the interfaces are not sharp, and the resulting diode is **weaker and rougher** than a spin-coated stack — lower rectification, dimmer EL. That is the accepted trade-off: it is a worse LED, but it emits, and the process is the same as the detector (one process for both devices, no spin-coater, no silver stencil, no fan-out wires).

The detector, by contrast, is a **bulk photogating composite** — a paste of graphene flakes / MoS₂ nanosheets in a binder, dried in a hole, is a legitimate photoconductor — so the pin-molded paste is the right route there with no such trade-off.

---

## 3. CNC — the 64-hole pin-array support (Job P1-A)

One gcode file: **`p1_pin_array_support.gcode`** (generated by `tools/gen_qssc_gcode.py`).

| | |
|---|---|
| **Stock** | 20×20×3 mm FR4 (or clear acrylic — transparent so you can see the paste fill; FR4 if you want the "PCB" feel) |
| **Tool** | 1.0 mm flat end-mill |
| **Holes** | **8×8 = 64**, Ø1.5 mm **circle-bored** (linear-segment polygon circles, no G02 arcs, visible in any viewer), **2.54 mm pitch**, centred (first pin 1.11 mm), through Z-3.0, drilled in **boustrophedon row-major** order |
| **Fiducials** | 4 corner dimples (inset 1 mm) |
| **Outline** | 20×20 through-cut with 4 bridges (tabs) |
| **Coords** | support-local, work zero = support bottom-left (0,0) |

> **Why 1.5 mm circle-bored with linear segments, not 1.0 mm peck-drilled and not G02 arcs.** A 1.0 mm peck drill at 1.0 mm = bit diameter shows as an invisible point in a top-view gcode viewer (the earlier bug: the gcode "showed only a border"). G02 arcs would be visible but some viewers misdraw the I/J arc-centre offset as a diagonal chord, making the square grid *look* diagonal (the "pins don't fit" report). The 1 mm bit circle-bores a 1.5 mm hole as a 16-segment G1-XY polygon circle, path radius 0.25 mm — visible in every viewer, no arc to misrender. The 0.43 mm radial clearance (1.5 mm hole − 0.64 mm pin)/2 still holds the paste annulus.

Run it once; you get one support. Make **two** supports — one for the fluorescence sensor, one for the CQD array — so you can build and test each device independently before stacking. (Or make three: a spare.)

> **Pin choice.** Standard **2.54 mm pitch male pin header, 0.64 mm square, tin-plated** (the cheapest "standard PCB pin"). Upgrade to **machined round gold-plated pins** (Mill-Max 1129 / Harwin M50 series, ~0.46 mm round) for a **uniform paste coating** — round cross-section has no corners, gold is an inert electrode. The 1.5 mm hole fits both. Cut the header strip to 8-pin rows, or use individual machined pins placed one per hole.

---

## 4. Fabrication — F1-D: pin-molded fluorescence sensor (graphene/MoS₂)

The graphene/MoS₂ photodetector, built by **molding paste around the pins in the CNC'd holes**, not spin-coating a film + stenciling pads (the P0 route). The pin is the per-pixel top electrode; the ITO glass under the support is the common back-electrode (DET_COMMON).

### 4.1 Materials (the user's)

- **Graphene paste** — the user's **graphene-in-alcohol** flake ink, let the alcohol mostly evaporate to a paste consistency (thick enough to hold shape in a hole, thin enough to wet the pin).
- **MoS₂ + PEDOT:PSS paste** — the user's **MoS₂ powder**, LPE to nanosheet ink (`FABRICATION_PROTOCOL_P0.md` F3.0: ~50–100 mg in 10 mL IPA, bath-sonicate 2–6 h, settle/centrifuge, keep the supernatant), then blend with **PEDOT:PSS** (aqueous dispersion, 1:1 to 1:3 MoS₂-ink:PEDOT:PSS by volume) and thicken — PEDOT:PSS is the p-type conductive binder that improves hole collection from the MoS₂.
- **ITO glass** 20×20×1.1 mm — the **ITO film = DET_COMMON** (edge-contacted by a silver-epoxy dot on a bare-ITO corner, as in P0).
- **Pin-header support** (Job P1-A, 1.5 mm holes) + **64 pins**.

### 4.2 Process

1. **Clean the ITO glass** — ultrasonicate IPA → DI → acetone → IPA, UV-ozone 15 min. Mask a bare-ITO corner for the DET_COMMON silver-epoxy dot. Place the ITO glass ITO-face-up under the support so the 64 holes sit over the ITO film. (The support rests on the ITO glass; the 17.78 mm array fits within the 20×20 ITO with 1.11 mm margin.)

2. **Graphene layer (inner coat on the pin).** Fill each of the 64 holes **half-way** with graphene paste. Insert a pin into each hole, push it down so its tip sits **~0.5 mm above the ITO** (do NOT let the pin tip touch the ITO — the paste column, not the metal, must bridge pin to ITO). The paste wets the pin shaft and fills the annular gap (~0.43 mm radial). Let it **dry** (room temperature, ~1–2 h, or 60 °C hotplate 20 min). The dried graphene takes the shape of pin + hole — a graphene skin on the pin and a graphene column down to near the ITO.

   *Alternative (dip-coat):* dip the pin in graphene paste, let a film dry on the pin, reinsert. The hole-mold route (above) is primary — it gives a well-defined paste column for the vertical junction.

3. **MoS₂ + PEDOT:PSS layer (outer coat).** Fill the remaining hole volume **on top of the dried graphene** with the MoS₂+PEDOT:PSS paste, around the pin. Let it **dry**. The pin is now coaxial: **pin → graphene skin → MoS₂+PEDOT skin**, and the paste column reaches the ITO. The vertical junction is **pin → graphene → MoS₂+PEDOT → ITO (DET_COMMON)**.

4. **DET_COMMON contact** — silver-epoxy dot on the masked bare-ITO corner, edge-contacted (mirror of the P0 EM_ANODE). Wire it to the breakout.

5. **Seat the pin heads** into a 2.54 mm female socket on the breakout motherboard (or solder a second header to the pin tails below the ITO glass). Each pin = one detector pixel, addressed by its socket position.

### 4.3 Readout

Address pin (i,j) vs DET_COMMON. The MoS₂ photogates under illumination (NV red PL 637–800 nm, or a 532 nm test pump); the photocurrent is read pin-to-ITO. 64 pixels, one pin each, no multiplexing needed (direct socket access).

> **Caveat (datasheet-grounded).** The dried paste is a **bulk flake composite** (~0.27 mm thick annulus + column), not a monolayer/few-layer CVD film. LPE MoS₂ is a mono/few-layer mix → photogating responsivity is **lower and more variable** than the ~10–100 A/W CVD-monolayer best case; bare graphene ~0.01 A/W. Usable for the ODMR lock-in demo (longer integration / stronger pump), not the best-case detector. This is the same caveat as the P0 LPE detector — the pin-mold route changes the **geometry and contacting**, not the fundamental flake-composite responsivity. Responsivity is **not quoted by the supplier** for the user's flake inks; state measured values when characterized.

---

## 5. Fabrication — F1-E: pin-molded CQD array (pin-molded ZnO/CQD/PEDOT paste stack)

The green CQD emitter, built by the **same pin-molding process as the detector** — the CQD-LED stack molded around each pin in the hole, layer by layer. Pin = cathode, ITO glass = EM_ANODE. No spin-coater, no silver stencil, no fan-out wires.

### 5.1 Materials

- **ITO glass** 20×20×1.1 mm — the **continuous ITO common anode (EM_ANODE)**, edge-contacted (silver-epoxy dot on a bare-ITO corner, as P0).
- **ZnO paste** — ZnO nanoparticle ink (the ETL material from the P0 spin-coated stack), thickened to a paste consistency (thick enough to hold shape in the 0.43 mm annulus, thin enough to wet the pin). Inner coat, against the cathode pin.
- **CQD paste** — the green CQD-PVA ink (the EML material from P0), thickened. Middle coat, the emissive layer.
- **PEDOT:PSS paste** — aqueous PEDOT:PSS dispersion, thickened. Outer coat (HIL), against the ITO anode.
- **Pin-header support** (Job P1-A, 1.5 mm holes) + **64 pins**.

### 5.2 Process

1. **Clean + mask** the ITO glass (UV-ozone, mask a bare-ITO corner for EM_ANODE) as P0. Place the ITO glass ITO-face-up under the support so the 64 holes sit over the ITO film.

2. **ZnO layer (inner coat, ETL — against the pin).** Fill each of the 64 holes **part-way** with ZnO paste. Insert a pin into each hole, push it down so its tip sits **~0.5 mm above the ITO** (do NOT let the pin tip touch the ITO — the paste column, not the metal, must bridge pin to ITO, same as the detector). The ZnO paste wets the pin shaft (the cathode) and fills the annular gap (~0.43 mm radial). Let it **dry** (room temperature, or 60 °C hotplate). The dried ZnO is the ETL skin on the pin.

3. **CQD layer (EML).** Fill the next volume of the hole **on top of the dried ZnO** with the CQD paste, around the pin. Let it **dry**. This is the emissive layer.

4. **PEDOT:PSS layer (outer coat, HIL — against the ITO anode).** Fill the remaining hole volume **on top of the dried CQD** with the PEDOT:PSS paste, around the pin, down to the ITO. Let it **dry**. The pin is now coaxial: **pin (cathode) → ZnO → CQD → PEDOT:PSS**, and the paste column reaches the ITO anode. The vertical diode is **pin → ZnO → CQD → PEDOT:PSS → ITO (EM_ANODE)**.

5. **EM_ANODE contact** — silver-epoxy on the masked corner, edge-contacted.

6. **Seat the pin heads** into a 2.54 mm female socket. Each pin = one cathode pixel, addressed from the back. No silver paste, no stencil, no spin-coater, no fan-out wires.

### 5.3 Readout

Drive pin (i,j) cathode vs EM_ANODE anode. The paste diode (pin → ZnO → CQD → PEDOT:PSS → ITO) emits green (520–540 nm) surface-normal up through the transparent ITO/glass. 64 pixels, one pin each, direct socket access.

> **Trade-off (datasheet-grounded).** The dried paste stack is a **~0.43 mm interdiffused composite**, not the sharp 10–80 nm spin-coated stack with orthogonal-solver interfaces. The layers interdiffuse as they dry → lower rectification, dimmer EL, rougher emission — a **weaker/rougher LED than the P0 spin-coated stack**. The EL band (520–540 nm, NV⁻ excitation) is preserved; efficiency and contrast are not quoted by the supplier for a paste-molded stack — state measured values when characterized. The benefit is symmetry with the detector (one process, no spin-coater).

---

## 6. The L2 entanglement bus (carried over from P0, unchanged)

P1 reuses the **P0 L2 TEOS sol-gel serpentine entanglement bus** verbatim — the 20×20×1 mm glass wafer with the 1 mm serpentine channel threading all 64 NV microdiamond sites (8×8) in series, 532 nm pump in the LEFT facet, perimeter MW loop at 2.87 GHz. See `FABRICATION_PROTOCOL_P0.md` §F2. The pin-scaffold only changes **how the detector (L3) and emitter (L1) are fabricated and contacted**; the bus is identical. Stack the sensor support / L2 wafer / CQD-array support as in P0 (the 20×20 footprints align).

---

## 7. BOM (P1 additions over P0)

| # | Part | Spec | Note |
|---|---|---|---|
| 1 | Male pin headers | 2.54 mm pitch, 0.64 mm square, tin-plated, 8-pin strips ×8 (or 64 individual pins) | the "standard PCB pin"; ~$0.01/pin |
| 1b | (upgrade) Machined round pins | Mill-Max 1129 / Harwin M50, 0.46 mm round gold, 2.54 mm pitch | uniform paste coating, inert electrode |
| 2 | Pin-array support | 20×20×3 mm FR4 (or clear acrylic) ×2–3 | Job P1-A; one per device + a spare |
| 3 | Graphene paste | graphene-in-alcohol flake ink, thickened | detector inner coat (the user's) |
| 4 | MoS₂ + PEDOT:PSS paste | LPE MoS₂-nanosheet ink + PEDOT:PSS aqueous (1:1–1:3), thickened | detector outer coat (the user's MoS₂ powder) |
| 5 | ZnO paste | ZnO nanoparticle ink, thickened | emitter inner coat (ETL) |
| 6 | CQD paste | green CQD-PVA ink, thickened | emitter middle coat (EML) |
| 7 | PEDOT:PSS paste | aqueous PEDOT:PSS dispersion, thickened | emitter outer coat (HIL) — also used in the detector MoS₂ blend |
| 8 | 2.54 mm female socket | 8×8 = 64-pos, or two 8-pin rows ×8 | breakout motherboard interface |
| 9 | ITO glass | 20×20×1.1 mm ×2 (sensor + emitter) | DET_COMMON (sensor) / EM_ANODE (emitter) |

(P0 L2 bus BOM — TEOS, NV microdiamonds Adamas FMDNVLN, glass wafer — unchanged; see P0 §3.)

---

## 8. Physics (datasheet-grounded, no external numbers)

- **NV⁻ microdiamonds (Adamas FMDNVLN datasheet, 150–200 µm HPHT, low-N, RT):** T₁ ≈ 5.9–10.2 ms (FMDNVLN1 = 10.16 ms, FMDNVLN2 = 5.91 ms, FMDNVLN3 = 5.57 ms); CW-ODMR contrast 4.4–4.9 %; 2E linewidth 2.86–4.15 MHz; NV < 3 ppm, N sub-50 ppm. The datasheet quotes T₁ (relaxation). The long coherence **T₂ ≈ 1.8 ms** (Hahn echo, RT) is the ¹²C-enriched-diamond value the in-repo simulator `qssc/defects.py` `NV_DIAMOND` uses and the P2 plate target — **not a P0/P1 microdiamond datasheet claim**. Do not write a µs-scale T₂ for the microdiamonds from external papers.
- **Graphene / MoS₂ detector:** responsivity is **not quoted by the supplier** for the user's flake inks; the bulk-flake-composite caveat in §4.3 applies. State measured values when characterized; do not invent A/W figures.
- **CQD emitter (pin-molded paste):** EL, not lasing (see the master P0 note). Emission band 520–540 nm (NV⁻ excitation). The pin-molded paste stack interdiffuses → weaker/rougher diode than the P0 spin-coated stack; efficiency and contrast are **not quoted by the supplier** for a paste-molded stack. State measured values when characterized.

---

## 9. Test plan

| Test | Device | Pass criterion |
|---|---|---|
| P1-A gcode | support | `pytest tests/test_gcode.py::test_p1_pin_array_64_holes_2p54_pitch` — 64 holes @ 2.54 mm, 8×8, 1.5 mm circle-bored (linear-segment polygon circles, no G02), through Z-3, boustrophedon order, outline bridges |
| P1-A render | support | `pytest tests/test_render_p1_svg.py` — well-formed, deterministic, 64 holes parsed from the real gcode, provenance, forbidden-word sweep clean |
| F1-D sensor | fluorescence sensor | photocurrent on ≥1 pin under 532 nm, pin-to-DET_COMMON; 64/64 pins addressable (open/short check) |
| F1-E array | CQD array | green EL on ≥1 pin, pin-to-EM_ANODE; 64/64 cathodes addressable; rectifying I-V (diode) — dimmer than a spin-coated stack is expected |
| F2 stack | sensor + L2 + array | ODMR dip at ~2.87 GHz modulates the sensor photocurrent (lock-in) |

---

**Files added in P1:**
- `tools/gen_qssc_gcode.py` — `gen_p1_pin_array_support()` + `circle_bore()` (8×8 hole grid, 2.54 mm pitch, 1.5 mm circle-bored, boustrophedon, 20×20×3 support).
- `hardware/gcode/p1_pin_array_support.gcode` — the 64-hole matrix (regenerated).
- `docs/FABRICATION_PROTOCOL_P1.md` — this protocol.
- `tools/render_p1_svg.py` → `docs/p1_pin_matrix.svg` — top view of the 64-hole matrix (parsed from the real gcode) + the pin/paste/ITO cross-section of both devices.
- `tests/test_gcode.py` — `test_p1_pin_array_64_holes_2p54_pitch`. `tests/test_render_p1_svg.py` — render checks.