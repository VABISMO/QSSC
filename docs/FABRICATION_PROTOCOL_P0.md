# QSSC-P0 — 3-layer vertical-stack prototype: CQD emitter + NV microdiamonds + graphene detector (CNC 3018, 1 mm bit)

**Project:** QSSC (Quantum Solid State Chip, room-temperature demonstrator)
**Document:** Protocol for the **first prototype** — simple, large, 3 layers, mechanical.
**Version:** P0 · 2.0  (supersedes P0·1.0 which was emitter-only; this version reworks the die to a VERTICAL two-faced stack — the old horizontal edge-coupled chain is withdrawn)
**Scope:** Build a **vertical two-faced stack die** — **20×20 mm**, three layers stacked TOP → BOTTOM — on an **FR4 135×90 mm carrier** (breakout) machined with a **CNC 3018 and a 1 mm bit**. The die is a **master-mirror** of the master architecture ("64 DET top + 64 EM bottom, no-TSV die clamped between two PCBs"):

- **L3 — graphene/MoS₂ photodetector (TOP face)** — 64 detector contacts (8×8). **Sole detector**.
- **L2 — NV⁻ microdiamonds in a TEOS sol-gel waveguide (MIDDLE)** — a 20×20×1 mm glass wafer with a **1 mm serpentine ENTANGLEMENT BUS** milled on its top face, threading **all 64 NV sites (8×8) in series** on one continuous Ti-doped TEOS core. 532 nm pumped from the **LEFT facet**, walks the bus and pumps every site in sequence; ODMR at **D ≈ 2.87 GHz**, PL 637–800 nm. Because all 64 sites share the one serial bus, a photon from any site propagates to any other → the on-chip entanglement path.
- **L1 — green CQD emitter (BOTTOM face)** — 8×8 = 64 emitter-cathode contacts (Ag) + 1 common EM_ANODE (ITO, edge-contacted). Full CQD-LED stack ITO/PEDOT/TFB/CQD-PVA/ZnO/Ag.

**Contacts = 64 detector (top) + 64 emitter-cathode (bottom) + 2 ITO commons (EM_ANODE + DET_COMMON) = 130 bond holes** on the carrier. Each face does ONE job (detector vs emitter) → **no through-silicon vias, no per-pixel via** (the old "64 anode + 64 cathode of one emitter on one face" framing needed a via per pixel and was physically over-constrained).

1. **L1 emitter** — green CQD diode (ITO/PEDOT/TFB/CQD-PVA/ZnO/Ag) in an **8×8 = 64 pixel matrix** on ITO glass 20×20, with a **CONTINUOUS ITO common anode** (EM_ANODE, edge-contacted — no isolation grooves). Emits surface-normal **UP** through its transparent ITO/glass into L2 above (520–540 nm, NV⁻ excitation band).
2. **L2 diamond layer** — **commercial NV⁻ microdiamonds (Adamas FMDNVLN, 150–200 µm low-nitrogen HPHT, T₁ ≈ 5.9–10.2 ms)** embedded in the core of **Ti-doped TEOS sol-gel (SiO₂)** of a **1 mm SERPENTINE BUS** milled into the 20×20×1 mm L2 glass wafer. The bus threads **all 64 NV sites (8×8, one per pixel) in series** as one continuous boustrophedon core (8 horizontal runs + 7 vertical connectors). The 532 nm pump enters the **LEFT facet**, walks the whole bus and pumps every NV site in sequence; the NV red PL leaves **vertically UP** from each site to the L3 detector pad directly above it. All 64 sites share the one serial bus → a photon from any site propagates to any other → the on-chip entanglement path.
3. **L3 detector** — **graphene/MoS₂ on-chip** 8×8 = 64 detector pixels (graphene-in-alcohol + MoS₂-ink photogating spin-coated on **ITO glass**, where the **ITO film is the detector common masa (DET_COMMON, edge-contacted)** + Au/Cr readout pads via CNC stencil + **PMMA passivation spin-coated over the channels between pads**, F3.1b). Reads the NV PL coming up from L2.

> **Why this P0.** The master hBN protocol (`FABRICATION_PROTOCOL.md`) and the diamond-plate variant (`FABRICATION_PROTOCOL_Diamond.md`) require non-basic equipment: tube furnace, evaporator, 5 µm lithography, confocal microscope. This P0 isolates what **can be done at home with a CNC 3018 + chemistry bench** and demonstrates the full quantum chain (pump → NV⁻ spin → readout) with 1 mm features. The three layers are built and tested incrementally, then stacked.

---

## 1. What the P0 validates (the 3 layers, phase by phase)

| Phase | Layer added | Demonstration |
|---|---|---|
| **F1** | L1 — CQD emitter 8×8 (bottom face) | Visible green EL, 64 cathode pixels + common anode addressable, surface-normal emission up |
| **F2** | L2 — NV⁻ microdiamonds in TEOS sol-gel (middle) | NV red PL (637–800 nm) under 532 nm pump; **ODMR dip at 2.87 GHz** with MW |
| **F3** | L3 — graphene/MoS₂ detector (top face) + stack + end-to-end | end-to-end readout: MW modulates the detector photocurrent at 2.87 GHz |

**No laser claim:** the CQD emitter produces **EL/ASE**, not lasing (see the master note on the emitter claim). **NV-diamond long coherence time: T₂ ≈ 1.8 ms** (Hahn echo, room temperature) — the value the in-repo simulator `qssc/defects.py` `NV_DIAMOND` profile uses, from Balasubramanian et al. 2009 measured in 99.7 % ¹²C-enriched diamond. This millisecond T₂ is the long-time coherence that puts the NV-diamond chip in the quantum-compute regime.
- **P0 as-built microdiamonds (Adamas FMDNVLN datasheet, 150–200 µm HPHT, low-N, RT):** **T₁ ≈ 5.9–10.2 ms** spin-lattice relaxation (FMDNVLN1 = 10.16 ms, FMDNVLN2 = 5.91 ms, FMDNVLN3 = 5.57 ms, standard-N = 2.38 ms); **CW-ODMR contrast 4.4–4.9 %** (FMDNVLN1 = 4.89 %, FMDNVLN2 = 4.42 %, FMDNVLN3 = 4.41 %, standard-N = 2.17 %); **2E linewidth 2.86–4.15 MHz**; NV < 3 ppm, N sub-50 ppm. The datasheet quotes T₁ (relaxation); the **T₂ ≈ 1.8 ms** above is the ¹²C-enriched-diamond coherence the simulator and the P2 plate target.
- **Conclusion:** the NV-diamond chip targets **T₂ ≈ 1.8 ms** long coherence (the simulator/Qiskit compute regime). The P0 microdiamond demonstrator validates the **pump → NV spin → graphene lock-in readout chain** at the datasheet ODMR values (T₁ ms, ~4.5 % contrast, ~3–4 MHz linewidth), on the way to the ms-coherence ¹²C-bulk P2 CVD NV plate.

---

## 2. Architecture — a VERTICAL stack: the optical path is laser → diamonds → detector

The die is a **vertical 3-layer stack** (master-mirror, no TSV). The NV⁻ microdiamonds are **embedded in the core of a TEOS sol-gel waveguide** machined as a **1 mm SERPENTINE ENTANGLEMENT BUS** milled into the L2 glass wafer — one continuous boustrophedon core (8 horizontal runs + 7 vertical connectors) that threads **all 64 NV sites (8×8, one per pixel) in series**. The 532 nm pump enters the **LEFT facet** of L2 at the bus start, walks the whole serpentine and pumps every NV site in sequence. The NV red PL (637–800 nm) leaves each site **vertically UP** through the L2 wafer to the L3 graphene detector pad directly above it. Because all 64 sites share the one serial bus, a photon emitted by any site propagates along the same core to any other → two indistinguishable photons from two NV sites interfere at the bus output → that interference **heralds spin-spin entanglement** between the two NV centres (the on-chip entanglement path). The L1 CQD emitter below also pumps **UP** (surface-normal through its transparent ITO/glass) as a secondary electrical pump.

```
                  532 nm laser ──→  │  LEFT facet of L2  (primary pump, bus start)
                                    ▼
   TOP face   64 graphene detector contacts (L3, 8×8)  ◀── NV PL arrives UP here (per site)
   ───────────────────────────────────────────────
                L3   graphene / MoS₂ photodetector  (sole detector)
   ───────────────────────────────────────────────   ◀── MW loop 2.87 GHz around
                L2   TEOS sol-gel SERPENTINE BUS + 64 NV sites (in series)   the whole die
                     (20×20×1 glass wafer, 1 mm serpentine channel on top face)
   ───────────────────────────────────────────────
                L1   CQD emitter (green LED 8×8)   ◀── emits UP through ITO/glass
   ───────────────────────────────────────────────
   BOTTOM face 64 emitter-cathode contacts (Ag) + 1 common EM_ANODE (ITO, edge)

   FR4 carrier 135×90 (CNC 1 mm): ONE 20×20 die pocket centred at (57.5,35)
   + 130 bond holes (64 detector top Y=85 + 64 emitter-cathode bottom Y=5
   + EM_ANODE (3.5,8.0) + DET_COMMON (3.5,82.0) = 2 ITO commons) + 135×90 outline with 4 bridges
```

**Why a 1 mm serpentine bus and not a 5 µm guide:** a 1 mm bit cannot mill 5–10 µm guides. The 1 mm serpentine channel is **multimode and lossy**, but it is a **real guide** (Ti-doped core n≈1.55 > glass 1.52 → total-internal-reflection confinement) that **optically bonds** all 64 NV sites to one shared core and to the pump/detector in a single TEOS sol-gel bus — no free space. The 8 horizontal runs sit on the 8 pixel rows (Y=36.25…53.75, 2.5 mm pitch) → 1.5 mm web between runs (no merge). The **TEOS sol-gel** is also the **transparent, non-fluorescent matrix** for the microdiamonds (TEOS silica does not bury the NV⁻ PL under autofluorescence, unlike SU-8). 5–10 µm sol-gel guides by soft lithography are a P1 upgrade.

> **Physics — pumping + entanglement (read this before relying on either pump).**
> (a) **532 nm laser through the LEFT facet = the PRIMARY pump** for reliable ODMR. It enters the L2 facet at the bus start and walks the whole serpentine, pumping all 64 NV sites in sequence. This is the pump used in F2.7.
> (b) **The on-chip L1 CQD emitter emits surface-normal** (perpendicular to the film plane, through the transparent ITO/glass). In the VERTICAL stack the emitter sits BELOW L2 and emits **UP** through its ITO/glass directly into the L2 wafer — so the emitter is a **legitimate secondary/electrical pump** (green 520–540 nm is in the NV⁻ excitation band), AND the end-to-end **EL demo**. This is a real improvement over the old horizontal chain, where the emitter edge-coupled only weakly.
> (c) **The readout leg (L2 diamonds → L3 graphene, vertically UP) is genuinely guided** — the PL stays in the TEOS core / wafer until it reaches the detector pad directly above each site. This is the leg the quantum effect depends on.
> (d) **Entanglement path:** all 64 NV sites share the ONE serial bus, so a photon from site *i* propagates along the same core to site *j*. Two indistinguishable photons from two NV sites interfere at the bus output → spin-spin entanglement heralded. This is the bus's reason for being serial, not 64 isolated channels. P0 demonstrates the guided bus + ODMR; heralded entanglement needs photon-number-resolving detection at the bus output (a P1/P2 readout upgrade), but the serial coupling is built in now.

---

## 2b. Layer map — what material each layer is made of and which gcode makes it

A layer = one physical function on the die. Each layer is built from **one concrete material** on its **own 20×20 substrate**, machined with **one of the 5 P0 gcode files**, then the three wafers are **stacked and bonded**. All gcode is in **carrier coordinates** at the **same centred die pocket (57.5,35)→(77.5,55)** and shares the 4 corner fiducials, so the files overlay when loaded together — you see the 8×8 grids of L1, L2 and L3 land on top of each other.

| Layer | What it is (function) | Material (what it's made of) | Thin-film stack (bottom → top) | Gcode that makes it | How to use that gcode |
|---|---|---|---|---|---|
| **L1 — CQD emitter** (BOTTOM face) | Diode that emits green UP to pump the NV⁻. **8×8 = 64 pixel matrix**, each pixel = 1 cathode pad (Ag) + the shared **CONTINUOUS ITO common anode** (EM_ANODE, edge-contacted — no isolation grooves). | **ITO glass** 20×20×1.1 mm (substrate + transparent anode) + organic/inorganic stack | glass → ITO (**continuous common anode**, 15–20 Ω/sq) → PEDOT:PSS 40 nm (HIL) → **TFB 30 nm (HTL)** → **green CQD-PVA** 80 nm (Cd-free quantum dot, citric acid+urea 220 °C) → ZnO 40 nm (ETL) → **Ag** (64 cathode pads) | **`p0_cathode_stencil.gcode`** (Kapton stencil of 64 windows to deposit Ag) | The ITO is left **CONTINUOUS** (one EM_ANODE plane, edge-contacted by a silver-epoxy dot on a bare-ITO corner masked during stack deposition — no `p0_ito_isolate` grooves; diode rectification stops pixel cross-talk). `p0_cathode_stencil`: mill 50 µm Kapton with **64 windows 1.0×1.0 mm** (NOT 1.5 — a 1 mm bit at 2.5 mm pitch needs window ≤ 1.0 mm to keep a 0.5 mm Kapton web between windows; at 1.5 mm the kerfs touch and all 64 windows merge → Ag shorts), align to the grid with the shared fiducials, doctor-blade silver paste through the windows → 64 isolated cathode pads on the bottom face. |
| **L2 — NV⁻ diamonds** (MIDDLE, the quantum layer) | Silica **serpentine entanglement bus** with NV⁻ microdiamonds **embedded in its core at all 64 sites (8×8) in series**. Produces the quantum effect (ODMR 2.87 GHz) and the on-chip entanglement path (all sites share one serial bus). 532 nm pump in the LEFT facet walks the bus; PL out the TOP of each site to L3. | **Ti-doped TEOS sol-gel** (SiO₂, n≈1.55) as the guide core + **NV⁻ microdiamonds 150–200 µm** (Adamas FMDNVLN low-nitrogen HPHT, NV <3 ppm, pre-activated, T₁ ≈ 5.9–10.2 ms) + **glass** wafer 20×20×1 mm (clad, n≈1.52) | Glass wafer (clad) → milled 1 mm SERPENTINE bus on the TOP face (8 runs + 7 connectors, one continuous core) → 64 NV-site markers → perimeter MW-loop groove → **place one 150–200 µm microdiamond at each of the 64 NV-site markers** → fill the bus with Ti-doped TEOS sol (no diamonds in the sol; 150–200 µm particles settle in seconds, so they are placed per site, not flow-dispensed) → densify 300 °C hotplate → silica core with the 64 microdiamonds embedded | **`p0_waveguide_channel.gcode`** (mills the serpentine entanglement bus + 64 NV-site markers + the perimeter MW-loop seating groove) | 1 mm bit, serpentine bus X58.0→77.0, 8 rows Y=36.25…53.75, Z-0.5 on the top face of the L2 wafer (NO through-cut; the wafer is clamped, not snapped). After milling, **place one 150–200 µm FMDNVLN microdiamond at each of the 64 NV-site peck markers** (tweezers / vacuum picker; the Z-0.20 markers locate each site), then fill the bus with Ti-doped TEOS sol (no diamonds in the sol) to embed them, soft-bake 80 °C, densify 300 °C. Repeat the sol fill 2-3× until the core is level. |
| **L3 — Photodetector** (TOP face) | Reads the NV⁻ red PL that comes UP through L2. **8×8 = 64 graphene/MoS₂ detector pixels**, each with its own Au/Cr readout pad; the **ITO film is the shared detector common masa (DET_COMMON, edge-contacted)**. Sole detector. | **ITO glass** 20×20×1.1 mm (L3 substrate; ITO = transparent common back-electrode / DET_COMMON, PL enters through the glass face) + **graphene-in-alcohol flake ink** + **MoS₂-nanosheet ink** (LPE from powder, F3.0) + **Au/Cr** 64 readout pads + **PMMA passivation** (spin-coated) | clean ITO glass (UV-ozone, ITO face up) → spin-coat MoS₂ ink (photogating, light-facing) → spin-coat graphene ink (read channel) → 64 Au/Cr readout pads (8×8, 1.0 mm, via CNC stencil) → **PMMA passivation spin-coated over the channels between pads** (pad-masked with the Job C cutout squares). Films adhere by **van der Waals** to the atomically-smooth ITO/glass + PMMA edge-lock (no CVD, no Cu etch, no transfer). | **`p0_detector_stencil.gcode`** (64-window Au/Cr pad stencil, 8×8) | `p0_detector_stencil`: 1 mm bit, Z-0.06 through Kapton, **64 windows 1.0×1.0 mm** in an 8×8 grid (same footprint as the emitter grids). Spin-coat the MoS₂ + graphene inks over the ITO glass first (F3.0/F3.1), then doctor-blade Au/Cr through the 64 windows → 64 detector readout pads (one per pixel). Then **spin-coat PMMA passivation** over the graphene/MoS₂ channels with the 64 pads masked by the cutout squares (F3.1b). The ITO film (DET_COMMON) is edge-contacted like the L1 EM_ANODE. Coarse (a real graphene PD uses ~100 µm gap) but that is the 1 mm bit limit. |
| **CARRIER** (holder, not an optical layer) | FR4 breakout that mounts the stacked die and brings the 130 contacts to the edge. Plugs into a commercial 2 mm socket on a motherboard. | **FR4** 135×90×1.6 mm + **130 male pin headers, 2.0 mm pitch** (soldered into the bond holes) + Kapton + epoxy | Machined FR4: ONE die pocket + 130 Ø1 holes + outline with bridges | **`p0_carrier_pcb.gcode`** (mills the die pocket + 130 bond holes + 135×90 outline) | 1 mm bit. Die pocket 20×20 Z-1.1 @ (57.5,35), **130 holes at 2 mm pitch** (64 detector top edge Y=85 + 64 emitter-cathode bottom edge Y=5 + EM_ANODE @ (3.5,8.0) + DET_COMMON @ (3.5,82.0) = the 2 ITO commons) Z-2.0, outline Z-3.0 with 4 bridges. Solder 2 mm male pin headers into the holes → the carrier plugs into **2 mm female socket receptacles (Samtec TMM-1xx / Harwin M20-782, 64 pos/row)** on a breakout motherboard. |

**Fabrication order:** L1 (emitter wafer) → L2 (guide+diamonds wafer) → L3 (detector wafer) → **stack + bond the 3 wafers** → CARRIER (can be milled first and used as an alignment template). Each layer is tested on its own before stacking (F1/F2/F3 in §4).

> **Where everything goes (the "I can't see where the diamonds / CQDs go" question):** the CQDs go on the TOP face of the L1 20×20 ITO wafer (under the stack, emitting up). The NV⁻ diamonds go **embedded inside the milled SERPENTINE BUS** of the L2 20×20 glass wafer (the middle layer, bus on its top face) — **one NV site per pixel, 64 in series** along the bus, each directly under an L3 detector pad and directly over an L1 cathode. The graphene detector goes on the L3 20×20 wafer (the top layer). All three wafers sit at the **same carrier footprint (57.5,35)→(77.5,55)** — load the 4 gcode files in a viewer with the shared fiducials and you see the three 8×8 grids overlaid on the one die pocket.

---

## 2c. Socket / motherboard interface (how the chip connects to electrodes)

**The bare 20×20 two-faced die is NOT socketable off-the-shelf.** Its contacts are an **8×8 area-array grid on EACH of two faces** (64 detector pads top + 64 emitter-cathode pads bottom, 2.5 mm pitch). Commercial IC sockets (QFP / QFN / BGA / PGA) contact either a **perimeter** or **one face** — none contact a **two-sided area array**. A two-sided 64+64 pogo-pin clamshell socket is **custom-only** (Enplas / Yamaichi / Aries, ~USD 500–2000, weeks of tooling, must match the 2.5 mm pitch / 1.5 mm pad layout). Not a home-lab part.

**That is exactly why the carrier exists** — it is the socket adapter. It translates the impossible-to-socket two-face die into **130 edge pins at 2 mm pitch** (64 + 64 + 2) that plug into a standard socket:

| Item | Part | Pitch | Notes |
|---|---|---|---|
| Carrier male pins | **2 mm-pitch male pin header**, 64 pos × 2 rows + 2, soldered into the 130 Ø1 bond holes | 2.0 mm | Fits the gcode holes exactly (the BOM's old "2.54 mm" was wrong — 2.54 mm pins don't fit 2 mm holes and 64×2.54 = 163 mm > 135 mm carrier). |
| Motherboard socket | **Samtec TMM-1xx** or **Harwin M20-782xx** vertical female receptacle, 64 positions × 2 | 2.0 mm | The carrier plugs in like a big DIP. Two strips on opposite edges of the breakout motherboard. ~USD 8–20. |
| 0.1″ (2.54 mm) alternative | standard 0.1″ female header (Adafruit/Jameco) | 2.54 mm | Only if the carrier is **widened to ~170 mm** (64×2.54 = 163 mm). More ubiquitous/cheaper, but tighter on the 3018's 180 mm bed (10 mm margin). Default stays at 2 mm / 135 mm. |

**If you want to probe the bare die before carrier-bonding** (temporary, non-destructive test of the 64+64 pixels): the closest **commercial** part is the **Aries 23018 clamshell** CSP/µBGA test socket (devices 14–27 mm square, **spring probes on BOTH faces**, pitch 0.2 mm up — so a 20×20 two-face 64+64 die fits and is contacted top+bottom at once; you spec the 8×8 interposer at your 2.5 mm pitch, ~USD 300–600 + tooling). The home-lab equivalent is a **DIY two-sided pogo-pin clamp**: two custom PCBs (or two CNC-milled FR4 plates) each carrying an 8×8 array of pogo pins (**Mill-Max 818/825 series**, 2.54 mm pitch, gold, 100k–1M cycles; or P75-B1 at 2.5 mm), hinged/clamped so the die is sandwiched between the top and bottom pogo beds. **Pitch caveat:** Mill-Max standard is 2.54 mm but the die pads are 2.5 mm — 0.04 mm/pin drift = 0.32 mm over a row, enough to miss pads; either order custom 2.5 mm pogo pins or bump the die pad pitch to 2.54 mm. For the deliverable, use the carrier + 2 mm socket.

---

## 2d. The 4 CNC jobs — what each one cuts and what you do on it

The P0 is four CNC jobs on a 3018 with a 1 mm bit. Each job machines **one stock** and produces **one part** (a wafer or a stencil or the carrier); you then do a specific thing with the materials on that part. All four files are in **carrier coordinates** at the **same centred die pocket (57.5,35)→(77.5,55)** and share the 4 corner fiducials, so you fixture every stock at the same mounting position on the carrier and the toolpaths overlay — the three 8×8 grids land on top of each other. Below: stock → what the bit cuts → what you do on the machined part.

### Job A — `p0_cathode_stencil.gcode` → the 64-window Ag mask (L1 emitter bottom face)
- **Stock:** ~22×22 mm **50 µm Kapton** foil (a throwaway stencil, NOT a chip part).
- **Fixture:** clamp the Kapton on the bed over the die area **(57.5, 35)**; align it to the L1 grid using the shared corner fiducials.
- **What the bit cuts:** 64 **square windows 1.0×1.0 mm** in an 8×8 grid at Z −0.06 mm (cuts through the Kapton only). The window is **1.0 mm, not 1.5**: at 2.5 mm pitch a 1 mm bit + 1.0 mm window leaves a **0.5 mm Kapton web** between windows so the 64 openings stay isolated; at 1.5 mm the kerfs touch, all windows merge, and silver paste would short every cathode.
- **What you do on it:** this Kapton is a **mask**. Lay it on the die's **bottom face** (over the ZnO — the face that points DOWN in the stack, toward the carrier bottom contacts), doctor-blade **silver paste** through the 64 windows → **64 isolated Ag cathode pads** (1.0 mm, on the 2.5 mm pitch grid). Peel the stencil off, cure 60–80 °C. The stencil is discarded after use — it only shapes the silver. **The L1 ITO is a CONTINUOUS common anode (no isolation grooves, no `p0_ito_isolate` job):** the whole ITO film is one EM_ANODE plane, edge-contacted by a **silver-epoxy dot on a bare-ITO corner** masked off during the PEDOT/TFB/CQD/ZnO stack deposition. Diode rectification (ITO anode → ZnO → Ag cathode, one diode per pixel) stops cross-talk between the 64 cathodes — no per-pixel anode isolation needed. The anode face points **UP** in the stack (toward L2), so light emits up through the ITO/glass.

### Job B — `p0_waveguide_channel.gcode` → the TEOS SERPENTINE ENTANGLEMENT BUS + 64 NV sites (L2 diamonds, middle)
- **Stock:** 20×20×1 mm **glass wafer** (cover-slip or cut slide; the glass is the cladding, n≈1.52) — the L2 layer.
- **Fixture:** clamp the wafer with its bottom-left corner at carrier **(57.5, 35)** — the centred die-pocket position.
- **What the bit cuts:** a **1 mm SERPENTINE ENTANGLEMENT BUS** milled into the **TOP face** of the wafer (the face that will point UP toward L3; **NOT a through-cut**) — **one continuous boustrophedon core**: 8 horizontal runs (one per pixel row, carrier Y=36.25, 38.75 … 53.75) joined by 7 vertical connectors at alternating ends, X 58.0→77.0, **Z −0.5 mm** (two Z passes, −0.25 then −0.5). The bus threads **all 64 NV sites (8×8, 2.5 mm pitch, one per pixel)** in series — each site sits at a grid intersection where the bus passes through. **64 NV-site markers** (peck Z −0.20) name every site by carrier coord. A **perimeter MW-loop seating groove around the whole die at Z −0.30 mm** (inset 1 mm) seats the copper MW loop that baths all 64 sites in one microwave field. Facet markers at the bus start (X58.0, Y36.25 = 532 nm pump input, top-left) and the bus end (X58.0, Y53.75 = dump, bottom-left).
- **What you do on it:** the milled serpentine becomes the **entanglement-bus core**. Place one **150–200 µm NV⁻ microdiamond** (Adamas FMDNVLN, F2.2) at each of the **64 NV-site peck markers** (Z-0.20) — one microparticle per pixel, directly on the bus core. 150–200 µm particles settle through sol in seconds, so they are placed per site with tweezers / a vacuum picker, NOT flow-dispensed. Then fill the bus with **Ti-doped TEOS sol** (no diamonds in the sol) to embed the 64 particles; soft-bake 80 °C, densify 300 °C on a hotplate → dense Ti-doped SiO₂ core (n≈1.55 > glass 1.52 → TIR confinement) with the NV⁻ microparticles **embedded in the silica at all 64 sites**. Repeat the sol fill 2–3× until the core is level with the top face. The **LEFT facet (X=57.5, the wafer's left edge)** is the 532 nm pump input (bus start) — leave it exposed when the stack is assembled. The serial bus is what lets a photon from any of the 64 sites reach any other → the entanglement path.

### Job C — `p0_detector_stencil.gcode` → the 64-pad graphene contact mask (L3 detector top face)
- **Stock:** ~22×22 mm **50 µm Kapton** (another throwaway stencil).
- **Fixture:** clamp over the die area, carrier **(57.5, 35)**; align with the shared fiducials.
- **What the bit cuts:** **64 square windows 1.0×1.0 mm in an 8×8 grid at Z −0.06 mm** through the Kapton — same footprint as the emitter grids (the detector pixels sit directly above the emitter pixels). 1.0 mm window + 1 mm bit leaves a 0.5 mm Kapton web (same limit as the cathode stencil).
- **What you do on it:** first **spin-coat the MoS₂ + graphene inks** on the clean L3 **ITO glass** (F3.0/F3.1: MoS₂ ink first as the photogating absorber, then graphene-in-alcohol as the read channel — for higher responsivity than bare graphene). The **ITO film is the detector common masa (DET_COMMON, edge-contacted)** — symmetric to the L1 EM_ANODE. Then lay this stencil on top and doctor-blade **Au/Cr** through the 64 windows → **64 detector readout pads** (one per pixel). The NV red PL comes UP through L2 → ITO glass (transparent at 637–800 nm) → MoS₂ → graphene channel → photocurrent at each pixel. The stencil is discarded after the Au/Cr deposition **but KEEP the 64 little 1×1 mm Kapton squares that fall out of the windows** — they are the **pad masks** for the PMMA passivation step (F3.1b): placed one on each Au/Cr pad, they stop the spin-coated PMMA covering the bond pads so the 64 wires can still be bonded.
- **Passivation (spin-coated PMMA, F3.1b):** after the Au/Cr pads are deposited, pick up the **64 Kapton cutout squares** kept from this stencil and place one on each pad (tweezers / sticky tip). **Spin-coat PMMA** (~150 nm, 950K in anisole, ~2000 rpm, bake 150 °C, 2 min) over the whole L3 area → a transparent, non-fluorescent film that passivates the **graphene/MoS₂ channels** between pads against O₂/moisture doping drift (the same PMMA already in the BOM, and the same encapsulation the L1 emitter uses in F1.3 — material-consistent). PMMA is transparent at the NV PL band (637–800 nm) and non-fluorescent under 532 nm, so it does not bury the PL (it arrives from below and is absorbed in the graphene anyway). Remove the 64 Kapton pad-masks with tweezers → the **64 bond pads stay bare** for the top-edge wire bonds. This is a spin-coater step, NOT a CNC step — no gcode.

### Job D — `p0_carrier_pcb.gcode` → the FR4 carrier / socket adapter (holder + breakout)
- **Stock:** 135×90×1.6 mm **FR4** sheet.
- **Fixture:** clamp with bottom-left at the bed origin **(0, 0)**.
- **What the bit cuts:** **ONE die pocket 20×20 @ (57.5,35) Z −1.1** (the stacked die seats here, centred on the carrier) + a 532 pump facet marker on the pocket's left edge (57.5, 45) + **130 Ø1 mm bond holes at 2 mm pitch** (64 detector **top edge Y=85** + 64 emitter-cathode **bottom edge Y=5** + 1 EM_ANODE **@ (3.5, 8.0)** + 1 DET_COMMON **@ (3.5, 82.0)** = the 2 ITO commons, peck-drilled Z −2.0) + the **135×90 outline at Z −3.0 with 4 bridges** (tabs so the freed carrier stays on the stock). Legibility engraves: bounding box, [DIE POCKET]/[DETECTOR BONDS]/[EMITTER-CATHODE BONDS]/[COMMON EM_ANODE]/[COMMON DET_COMMON]/[OUTLINE] labels, shared fiducials.
- **What you do on it:** the carrier is the **mechanical holder + the socket adapter**. Seat the stacked 20×20 die in the pocket with its LEFT facet (532 pump input) toward the carrier's left edge. Bond the 130 die contacts to the 130 edge holes (bottom cathodes + EM_ANODE FIRST, before seating — see F1.4). Solder **2 mm-pitch male pin headers** into the holes → the carrier plugs into a **2 mm female socket (Samtec TMM-1xx / Harwin M20-782)** on a breakout motherboard. The fan-out from the 20 mm die edges to the 128 mm carrier edges is hand-wired (magnet wire) on the carrier — the CNC only drills the edge pin holes + pockets the die. Nothing is deposited on the carrier itself; it holds and routes.

**One bit, four jobs, no tool change:** jobs A–C engrave/stencil at shallow Z (−0.06) or mill a shallow groove (−0.5); only job D goes to Z −3.0 (the through-cut outline). The same 1 mm carbide bit does all four.

## 3. BOM for the P0 (3 layers)

### 3.1 Layer 1 — emitter (reused from P0·1.0 + master §1, + TFB HTL for optimality)
ITO glass 20×20×1.1 (15–20 Ω/sq), citric acid + urea (green CQD 220 °C), PVA, PEDOT:PSS AI 4083, **TFB (poly(9,9-dioctylfluorene-alt-N-(4-sec-butylphenyl)-diphenylamine), Sigma 776025 — hole-transport layer, spun from chlorobenzene)**, ZnO nanopowder, silver paste, PMMA, PTFE autoclave 50 mL, hotplate, ultrasonicator, IPA/acetone/chlorobenzene/DI water.

### 3.2 Layer 2 — NV⁻ microdiamonds + TEOS sol-gel (NEW)
| Item | Spec / source | Approx. cost |
|---|---|---|
| **NV⁻ microdiamonds** (150–200 µm, low-nitrogen HPHT, NV <3 ppm, pre-activated, T₁ ≈ 5.9–10.2 ms) | **Adamas FMDNVLN1-10mg** (20–30 ppm N, ODMR contrast 4.9 %, T₁ 10.16 ms — best for the lock-in demo) or **FMDNVLN3-10mg** (10–20 ppm N, narrowest ODMR linewidth 2.86 MHz) | $30–60 / 10 mg (64 sites need ~1 mg) |
| **TEOS** (tetraethyl orthosilicate, ≥98 %) | Sigma 86578 | $60 / 500 mL |
| **MTEOS** (methyltriethoxysilane, ≥99 %) — optional, raises core n | Sigma 246638 | $45 / 100 mL |
| Absolute ethanol | — | $20 / 1 L |
| HCl 0.01 M (sol-gel catalyst) | — | $10 |
| Glass wafer 20×20×1 mm (cover slip or cut slide) for the L2 guide | — | $5 |
| **532 nm laser** (auxiliary primary pump, through the LEFT facet) | DPSS 532 nm pointer/module, ≥5 mW, collimated | $15–40 |

> **No tube furnace.** The NV microdiamonds arrive **pre-activated** from the supplier (commercial irradiation+anneal). The sol-gel densification is at **300 °C on a hotplate** — within reach of "the basics".

### 3.3 Layer 3 — photodetector (graphene/MoS₂, sole detector)
| Item | Spec | Approx. cost |
|---|---|---|
| **ITO glass** 20×20×1.1 mm (L3 substrate — the ITO film is the detector common masa / DET_COMMON, edge-contacted; transparent at 637–800 nm so the NV PL arrives from below through the glass face) | on hand (same ITO glass as L1) | on hand |
| **Graphene in alcohol** (flake ink, spin-coatable) | the user's material — exfoliated graphene dispersed in ethanol/IPA | on hand |
| **MoS₂ powder** (photogating layer — liquid-phase-exfoliated into an alcohol ink, see F3.0) | the user's material | on hand |
| **IPA / ethanol** (LPE solvent + rinse) | electronics-grade | ~$5 |
| **Au/Cr for 64 readout pads** (via CNC stencil) | evaporate or stencil | — |
| **PMMA 950K in anisole** (spin-coated passivation over graphene/MoS₂ channels, F3.1b — also the mechanical lock that edge-encapsulates the flake films) | same PMMA as the L1 encapsulation | incl. |

### 3.4 Carrier + contacts + socket + MW (CNC 1 mm)
FR4 135×90×1.6, **130 male pin headers, 2.0 mm pitch** (soldered into the bond holes: 64 + 64 + 2), **2× 64-position 2 mm female socket receptacles (Samtec TMM-1xx / Harwin M20-782)** mounted on a breakout motherboard (the carrier plugs in like a big DIP — no per-pin hand-bonding; wire + conductive epoxy is the no-socket fallback), 1 mm carbide bit, 50 µm Kapton tape (cathode stencil 64 windows + detector stencil 64 windows), conductive epoxy, **silver epoxy** (the 2 ITO edge commons: EM_ANODE on L1, DET_COMMON on L3), magnet wire (30–40 AWG) for the die-to-edge fan-out, double-sided tape. MW CPW: semi-rigid cable or tinned-copper loop + SMA connector + ADF4351 (or any < 4 GHz 2.87 GHz source). Glass wafer 20×20×1 mm (cover slip or slide) for the L2 TEOS waveguide — **plus a second 20×20×1.1 mm ITO glass for the L3 detector substrate** (§3.3; the ITO film is DET_COMMON, transparent so the NV PL from L2 reaches the graphene/MoS₂ from below).

**Estimated P0 cost (consumables, assuming CNC + hotplate): ≈ USD 120–180.**

---

## 4. Step-by-step process

### F1 — LAYER 1: CQD emitter 8×8 (L1 bottom face, continuous ITO common anode)

> Executive summary; exact chemical parameters are in the master §1 and in `FABRICATION_PROTOCOL_P0.md` v1.0 (Section A–C). The CNC toolpaths are in `hardware/gcode/p0_*.gcode`. **All P0 files share the same carrier coordinate system** (origin = bottom-left corner of the 135×90 FR4) and the same 4 corner fiducials, so they overlay in a viewer. The die pocket is centred at carrier (57.5,35)→(77.5,55).

**F1.1 Substrate + continuous ITO common anode (EM_ANODE, edge-contacted — no isolation grooves).** Cut ITO glass 20×20 (score-and-snap). Clean (ultrasonicate detergent→DI→acetone→IPA, UV-ozone 15 min). **The ITO film is left CONTINUOUS** — it is the one common EM_ANODE plane for all 64 pixels, NOT scribed into 64 isolated pads. There is no `p0_ito_isolate` job and no isolation grid: the old per-pixel anode isolation is dropped because diode rectification (ITO anode → ZnO → Ag cathode, one diode per pixel) already stops cross-talk between the 64 cathodes. **Make the EM_ANODE edge contact now:** mask a bare-ITO corner (~2×2 mm) with a Kapton tab before any stack deposition, so that corner stays bare ITO; after the stack is deposited, dot **silver epoxy** on that bare corner → the EM_ANODE edge contact (a magnet wire is later bonded to it, fanned out to the (3.5,8.0) carrier hole — F1.4). Fixture the die on the CNC bed at the centred die-pocket position **(X=57.5, Y=35)** only for the cathode stencil (Job A); the anode needs no CNC work.

**F1.2 Diode stack (with TFB hole-transport layer for optimal efficiency).** Over the active area (contact edges masked with Kapton): PEDOT:PSS 40 nm (3000 rpm, bake 120 °C) → **TFB 30 nm (2000 rpm from chlorobenzene, bake 110 °C — hole-transport layer; chlorobenzene is orthogonal to the water-based PEDOT:PSS so it does not redissolve it)** → green CQD-PVA 80 nm (2000 rpm, bake 80 °C in vacuum) → ZnO ETL 40 nm (3000 rpm, bake 150 °C). The TFB steps hole injection (HOMO ~5.3 eV vs PEDOT ~5.0 eV), balances holes vs electrons, and stops acidic PEDOT→CQD quenching at the interface — the single biggest efficiency lever for a CQD-LED. (Without TFB the stack still emits but at lower efficiency.)

**F1.3 Ag cathode on 64 pads.** Mill a Kapton stencil (`p0_cathode_stencil.gcode`, **64 windows 1.0×1.0 mm** in an 8×8 grid, 2.5 mm pitch). **Window must be 1.0 mm, not 1.5:** a 1 mm bit opens window+bit = 2.0 mm; at 2.5 mm pitch that leaves a 0.5 mm Kapton web between windows so the 64 openings stay isolated. At 1.5 mm the kerfs touch (zero web) and all 64 windows merge → silver paste forms one continuous sheet → all cathodes short. Align to the grid with the shared fiducials. Doctor-blade silver paste through the 64 windows onto the **bottom face** (over the ZnO — the face that points DOWN in the stack) → **64 isolated cathode pads** (1.0 mm, on the 2.5 mm pitch grid; the continuous ITO under the stack is the shared EM_ANODE). Cure 60–80 °C. Encapsulate with 200 nm PMMA (except bond pads and the EM_ANODE corner).

**F1.4 Carrier + 130 contacts + socket.** Mill the FR4 (`p0_carrier_pcb.gcode`, stock **135×90**): **ONE die pocket 20×20×1.1 @ (57.5,35)** + **130 Ø1 holes at 2 mm pitch** (64 detector **top edge Y=85** + 64 emitter-cathode **bottom edge Y=5** + 1 EM_ANODE **@ (3.5, 8.0)** + 1 DET_COMMON **@ (3.5, 82.0)** = the 2 ITO edge commons) + 135×90 outline with 4 bridges. Solder **2 mm-pitch male pin headers** into the holes → the carrier plugs into **2 mm female socket receptacles (Samtec TMM-1xx / Harwin M20-782, 64 positions per row)** on a breakout motherboard.

> **Assembly order — bond the bottom cathodes + EM_ANODE BEFORE seating the die (important).** The die pocket is 1.1 mm deep; once the stacked die is seated, its **bottom face (L1 cathodes + EM_ANODE) is inaccessible**. Therefore: (1) **flip** the finished L1 wafer, **bond 64 fine magnet wires** to the 64 bottom-face cathode pads + 1 wire to the **EM_ANODE** silver-epoxy corner contact, with conductive epoxy, tape the wires down; (2) build L2 and L3, **stack + bond** the three wafers (index-matching epoxy at the L1/L2 and L2/L3 interfaces, LEFT facet of L2 kept exposed for the 532 pump); (3) **seat** the stack in the pocket with the 64+1 cathode/anode wires exiting toward the **bottom edge (Y=5)** holes; (4) solder/crimp those wires to the bottom-edge pins; (5) **bond 64 wires** from the top-face L3 detector pads + 1 wire to the **L3 ITO DET_COMMON** edge contact → **top edge (Y=85)** pins. Verify <10 Ω per bond. (This two-step wiring is the price of a two-faced 64+64 die with a 1 mm bit.)

**F1.5 Emitter test.** Address pixels 0–15 V; turn-on 4–8 V; visible green EL ~10 V; spectrum peak 520–540 nm; confirm emission is surface-normal (up through the ITO/glass). **F1 closes when all 64 pixels light individually.**

### F2 — LAYER 2: NV⁻ microdiamonds embedded in the TEOS sol-gel waveguide (L2 middle)

The diamond layer **is the entanglement bus**: a 1 mm SERPENTINE channel milled into the TOP face of the 20×20×1 mm L2 glass wafer, with one **150–200 µm NV⁻ microdiamond (Adamas FMDNVLN)** placed at each of the 64 sites and embedded by filling the bus with Ti-doped TEOS sol. The bus threads **all 64 NV sites (8×8, one per pixel) in series** on one continuous core. On densification the microdiamonds are **embedded in the silica core at all 64 sites** — the 532 nm pump enters the LEFT facet at the bus start and walks the whole serpentine, the NV red PL leaves the top of each site toward the L3 pad above it. All 64 sites share the one serial bus → the on-chip entanglement path.

**F2.1 TEOS core sol (Ti-doped, n ≈ 1.55).** Mix **TEOS : EtOH : H₂O : HCl = 1 : 4 : 2 : 0.02 (mol)**, stir 2 h at 60 °C (hydrolysis/condensation). Age 24 h in a closed vial. Filter 0.22 µm. To raise the core index above the glass (n≈1.52) and guarantee total-internal-reflection confinement: dope with **Ti** (add **Ti(OBu)₄ at 5–10 mol %** of Si, or ~15 mol % MTEOS as a Ti-free alternative) → core n≈1.55–1.60. The clad is the glass of the wafer itself (n≈1.52) or pure TEOS sol (n≈1.45).

**F2.2 NV microdiamonds + core sol.** The quantum particles are **Adamas FMDNVLN low-nitrogen HPHT microdiamonds, 150–200 µm** (NV <3 ppm, pre-activated, T₁ ≈ 5.9–10.2 ms, ODMR contrast 4.4–4.9 % — datasheet). At 150–200 µm a particle settles through sol in seconds, so the microdiamonds are **NOT flow-dispensed** — they are placed one per site (F2.4). Prepare the **Ti-doped TEOS core sol** separately (F2.1, no diamonds in it); it later embeds the placed particles. The sol-gel silica is the transparent, non-fluorescent matrix that fixes each microdiamond in the guide core on densification (no autofluorescence, unlike SU-8).

**F2.3 Mill the L2 entanglement bus (1 mm serpentine on the top face).** Cut a **20×20×1 mm glass wafer** (cover slip or slide, score-and-snap). Clean (ultrasonicate IPA→DI, UV-ozone 15 min). Fixture the wafer on the bed with its bottom-left corner at carrier **(57.5, 35)** — the centred die-pocket position — and run `p0_waveguide_channel.gcode` (carrier coords): the **SERPENTINE ENTANGLEMENT BUS** — 8 horizontal runs (Y=36.25, 38.75 … 53.75, one per pixel row) + 7 vertical connectors at alternating ends, X 58.0→77.0, 1 mm bit, **Z −0.5 mm** (two Z passes), milled into the TOP face of the wafer (the face that will point UP toward L3) — a single continuous boustrophedon core threading **all 64 NV sites (8×8, 2.5 mm pitch)** in series. **64 NV-site markers** (peck Z −0.20) name each site. A **perimeter MW-loop seating groove at Z −0.30** (inset 1 mm around the whole die) for the copper MW loop. The bus is **NOT through-cut** (the wafer is clamped, not snapped). Facet markers at the bus start (X58.0, Y36.25 = 532 pump input) and end (X58.0, Y53.75 = dump). Inspect under a loupe: continuous serpentine, flat floor, ~1 mm wide, 1.5 mm web between runs. (See §2d Job B for the stock/fixturing/process summary.)

**F2.4 Place the 64 microdiamonds + fill the bus with sol.** (1) Pick one **150–200 µm FMDNVLN microdiamond** with fine tweezers or a vacuum picker and set it on the bus at each of the **64 NV-site peck markers** (Z-0.20) — one per pixel, directly on the serpentine core. (2) Pipette the **Ti-doped TEOS core sol** (F2.1, no diamonds) into the bus at the left facet; capillarity fills the whole serpentine and embeds the 64 placed particles. Soft-bake **80 °C, 15 min** (EtOH evaporates, the gel consolidates). Densify **300 °C, 1 h on hotplate** → dense Ti-doped SiO₂ core with the 64 microdiamonds embedded. Repeat the sol fill 2–3× until the core is level with the top face of the wafer.

**F2.5 Coverage / NV count.** One 150–200 µm FMDNVLN microparticle per site, each carrying ~10¹¹–10¹² NV centres (diamond 3.5 g/cm³, NV <3 ppm → ~5×10¹⁷ NV/cm³ × ~4×10⁻⁶ cm³ ≈ 2×10¹² NV per 200 µm particle — far more NV per site than a 100 nm nanodiamond cluster), so the ensemble ODMR signal per pixel is strong and matches the datasheet contrast. Verify under a fluorescence microscope: 64 red dots under 532 nm, one per pixel.

**F2.6 MW for ODMR.** Route a tinned-copper loop (or the CPW milled in FR4 with a stencil) **around the whole die** (seat it in the perimeter groove milled by Job B, inset 1 mm) so it baths **all 64 NV sites** in one microwave field, at < 2 mm. Connect to the ADF4351 / generator. Working frequency: **2.87 GHz** (sweep range 2.80–2.94 GHz), 1 kHz AM modulation for lock-in.

**F2.7 ODMR test (before stacking — test the L2 wafer alone).** **Primary pump = auxiliary 532 nm laser coupled through the LEFT facet** of the L2 wafer at the bus start (the on-chip CQD emitter is the secondary/electrical pump and the EL demo — see the §2 physics note). The pump walks the serpentine and the NV red PL leaves the top of each site. Collect with a Si PD / microscope / the L3 graphene detector while sweeping MW 2.80–2.94 GHz. **Dip at 2.87 GHz = NV⁻ ODMR confirmed.** Expected contrast **4.4–4.9 %** (Adamas FMDNVLN low-N datasheet: FMDNVLN1 = 4.89 %, FMDNVLN3 = 4.41 %; standard-N = 2.17 %). **F2 closes with the dip visible.**

### F3 — LAYER 3: graphene/MoS₂ detector + stack + end-to-end readout

**F3.0 MoS₂ powder → spin-coatable ink (liquid-phase exfoliation, do this first).** You have **MoS₂ powder**, not an ink. Exfoliate it into a nanosheet dispersion you can spin-coat (same idea as the graphene-in-alcohol ink you already have). Key tool: a **bath sonicator** (a cheap ultrasonic jewelry cleaner, ~$20–30, works).

**Recipe (~30 min active + 4–8 h sonication):**
1. **Weigh ~100 mg MoS₂ powder** (a pinch, ~1/8 tsp; 10 mg of nanosheets coats many disks). Wear a dust mask for the dry powder.
2. **Powder + 10 mL IPA in a 15 mL glass vial, cap it**, shake hard ~1 min until no dry lumps (dark grey slurry at first).
3. **Bath-sonicate 4–8 h.** Stand the capped vial in the water bath. **Keep the bath cool** — it heats up and hot solvent re-aggregates the flakes; change the bath water / add ice every ~2 h. Longer = thinner flakes = better photogating. (Probe sonicator → 1–2 h is enough.)
4. **Separate nanosheets from chunks:** centrifuge ~1500 rpm (~200–500 g) 10–20 min → discard the pellet, **keep the supernatant**; OR no centrifuge → let stand 30–60 min, pipette/decant the **top 2/3**.
5. **Check:** the supernatant is **grey-green, lightly opaque** (pale when thin). Drop one drop on a glass slide, evaporate the IPA → a faint grey film should remain. Clear → sonicate longer; dark + fast-settling → mostly big flakes, centrifuge/decant harder.
6. **Use within a few hours**, or re-sonicate 10 min before each use (it slowly re-settles). Optional stabilizer for shelf life: ~10 mg PVP dissolved in the ink.

This is your photogating ink. **Home-lab note:** LPE gives a **mix of mono/few-layer nanosheets**, not strictly monolayer, so the photogating responsivity is lower and more variable than a CVD monolayer — usable for the P0 ODMR demo, just not the ~100 A/W best-case. The 2.87 GHz dip will likely need longer lock-in integration. **If you have no sonicator:** LPE won't work without one — get a $20 ultrasonic jewelry cleaner, or buy pre-made **MoS₂-in-IPA ink** (2D Semiconductors, ~$50) and skip this recipe. **Safety:** MoS₂ is low-toxicity (a dry lubricant) but don't breathe the powder; IPA is flammable — sonicate capped, ventilated, no sparks.

**F3.1 L3 detector — graphene/MoS₂ 8×8 (top face, spin-coated inks on ITO glass).** The detector is built from **two spin-coated inks** on the **L3 ITO glass wafer** — no CVD, no Cu etch, no transfer. The **ITO film is the detector common masa (DET_COMMON, edge-contacted)**, symmetric to the L1 EM_ANODE; mask a bare-ITO corner for the DET_COMMON edge contact before spin-coating (same technique as F1.1). **(1) Clean the L3 ITO glass** (ITO face UP, glass face DOWN toward L2; transparent at 637–800 nm — the NV PL arrives from below through the glass): ultrasonicate IPA → DI → acetone → IPA, UV-ozone 15 min. *Cleaning is the critical step — the flake films adhere by van der Waals force and only stick to a clean, particle-free surface.* **(2) Spin-coat the MoS₂ ink** (from F3.0) over the 20×20 area — MoS₂ first because it is the light absorber and should sit closest to the incoming PL (glass → ITO → MoS₂ → graphene). ~1000–2000 rpm, evaporate the alcohol on a 60 °C hotplate. **(3) Spin-coat the graphene-in-alcohol ink** on top of the MoS₂ → the graphene is the high-mobility read channel; evaporate. **(4) Deposit the 64 Au/Cr readout pads** by **CNC stencil** (`p0_detector_stencil.gcode`: 64 windows 1.0×1.0 mm in an 8×8 grid, 1 mm bit, Z-0.06 through Kapton — same footprint as the emitter grids). Doctor-blade Au/Cr through the 64 windows → 64 detector readout pads (one per pixel; the ITO film is the shared back-electrode / DET_COMMON). **How it sticks:** the dried MoS₂ and graphene flakes lie flat on the clean ITO/glass and on each other, held by **van der Waals** adhesion (no glue, no chemical bond); the **PMMA passivation spun over the channels in F3.1b is also the mechanical lock** that edge-encapsulates the flake films so they do not lift. The red NV PL comes UP through L2 → L3 ITO glass → MoS₂ (absorbed, photogates) → graphene (channel) → photocurrent at each pixel. Bias each pixel (pad vs the ITO DET_COMMON) and read out with the FPGA/lock-in.

**F3.1b PMMA passivation (spin-coated, pad-masked).** The bare graphene/MoS₂ channel dopes and drifts in air (O₂/moisture) → unstable photocurrent. After the 64 Au/Cr pads are in place, **passivate the channels**: keep the **64 little 1×1 mm Kapton squares** that fell out of the Job C stencil windows, place one on each Au/Cr pad (tweezers — they are the pad masks), then **spin-coat PMMA** (~150 nm, 950K in anisole, ~2000 rpm, soft-bake 150 °C / 2 min on the hotplate) over the whole L3 area. The PMMA covers and seals the graphene/MoS₂ between the pads; the 64 Kapton squares keep the bond pads bare. Remove the 64 squares with tweezers → the 64 pads are re-exposed for the top-edge wire bonds (F1.4 step 5). PMMA is the **appropriate spin-coatable passivation** here: transparent at the NV PL band (637–800 nm), non-fluorescent under 532 nm (it does not bury the PL — which arrives from below and is absorbed in the graphene anyway), already in the BOM, **and it doubles as the mechanical lock that edge-encapsulates the spin-coated MoS₂/graphene flake films** (held to the ITO/glass by van der Waals — see F3.1 — so the PMMA overcoat stops them lifting at the edges), and it is material-consistent with the **200 nm PMMA encapsulation the L1 emitter already uses in F1.3**. This is a spin-coater step, not a CNC step — no gcode. (A 5–10 µm pad-gap graphene PD would use ALD Al₂O₃ instead, but ALD is not a home-lab spin-coater step; PMMA is the home-lab-appropriate choice.)

**F3.2 Stack the 3 wafers.** Bond the three 20×20 wafers into the vertical stack: L1 (bottom, emitter, ITO/glass facing UP) + L2 (middle, waveguide, serpentine bus on top face, LEFT facet exposed) + L3 (top, detector). Use **index-matching epoxy** (or immersion oil n≈1.52) at the L1/L2 and L2/L3 interfaces so the pump and PL cross the interfaces without Fresnel loss. Keep the **LEFT facet of L2 exposed** (the carrier's left edge is clear/notched so the 532 nm pump beam reaches the L2 bus start). The MW loop stays around the whole L2 die (perimeter groove), bathing all 64 sites.

**F3.3 Lock-in readout.** 1 kHz MW modulation → the detector photocurrent modulates at 1 kHz as it passes through the ODMR dip → a lock-in (SR830, or Moku, or an ADC + software lock-in on the FPGA) extracts the amplitude vs. MW frequency. The dip depth at 2.87 GHz is the quantum signal.

**F3.4 End-to-end P0.** 532 nm laser → L2 LEFT facet (bus start) → walks the Ti-doped TEOS serpentine → pumps all 64 NV sites in sequence → red PL UP from each site → L3 graphene/MoS₂ detector pad above it → 2.87 GHz MW modulates the photocurrent → lock-in reads the ODMR dip. (The L1 CQD emitter pumps UP through its ITO/glass as the secondary/electrical pump and the EL demo.) The serial bus also couples all 64 sites → photons from any two sites can interfere at the bus output (the entanglement path). **No confocal, no tube furnace, no fine lithography.** The 1 mm TEOS serpentine bonds all 64 diamonds to one shared core and to the pump/detector. That closes the 3-layer vertical-stack prototype.

---

## 5. Geometry and CNC toolpaths (the 4 jobs, 1 mm bit)

**Shared coordinate system:** the 4 files share the same origin = bottom-left corner of the 135×90 carrier, and the same 4 corner fiducials (1,1)/(134,1)/(134,89)/(1,89). Every layer is drawn at the **same centred die pocket (57.5,35)→(77.5,55)**, so **loading the 4 in a viewer overlays them**: the cathode windows, the serpentine entanglement bus (with its 64 NV sites) and the detector pads all fall on the one 20×20 die pocket — the three 8×8 grids land on top of each other, because the three layers stack vertically on the same die. (The L1 ITO is a continuous common anode — no isolation grid — so there is no ITO-scribe job.)

| # | Job | Stock | What it mills (carrier coords) | Gcode file |
|---|---|---|---|---|
| A | **64-window Ag cathode stencil** (L1 bottom) | ~22×22 Kapton 50 µm | **64 windows 1.0×1.0** in 8×8 grid (1 mm bit needs ≤1.0 mm window to keep a 0.5 mm Kapton web at 2.5 mm pitch), Z-0.06, over (57.5,35) | `p0_cathode_stencil.gcode` |
| B | **TEOS SERPENTINE ENTANGLEMENT BUS + 64 NV sites** (L2 middle) | 20×20×1 glass wafer | serpentine bus: 8 runs Y=36.25…53.75 + 7 connectors, X58.0→77.0 Z-0.5 on top face (one continuous core) + 64 NV-site markers Z-0.20 + perimeter MW-loop groove Z-0.30, over (57.5,35); LEFT facet X=58.0 Y36.25 = 532 pump (bus start) | `p0_waveguide_channel.gcode` |
| C | **64-pad graphene detector stencil** (L3 top, on ITO glass) | ~22×22 Kapton 50 µm | **64 windows 1.0×1.0** in 8×8 grid Z-0.06 → 64 Au/Cr detector readout pads, over (57.5,35) | `p0_detector_stencil.gcode` |
| D | **FR4 carrier breakout 135×90** | **135×90×1.6 FR4** | **ONE die pocket 20×20 @ (57.5,35) Z-1.1** + 532 pump facet marker (57.5,45) + **130 Ø1 holes at 2 mm pitch** (64 detector top edge Y=85 + 64 emitter-cathode bottom edge Y=5 + EM_ANODE @ (3.5,8.0) + DET_COMMON @ (3.5,82.0) = the 2 ITO commons) + 135×90 outline with 4 bridges | `p0_carrier_pcb.gcode` |

> The carrier (135×90) fits on the 3018's 180×100 bed with margin. The 4 jobs cover **all** the mechanical features of the P0·2.0 vertical stack: the **64 cathode pads** (L1 emitter on a continuous ITO common anode), the **serpentine entanglement bus + 64 NV sites** (L2, where the NV⁻ microdiamonds embedded in TEOS sol-gel are deposited, all 64 wired in series on one core), the **64 detector pads** (L3 graphene/MoS₂ on ITO glass), and the breakout carrier with ONE die pocket + 130 contacts. The MW loop is a tinned-copper loop seated in the perimeter groove milled by Job B around the whole L2 die, bathing all 64 NV sites.

Regenerate: `python tools/gen_qssc_gcode.py`. Tests: `pytest tests/test_gcode.py` (37 passed, 3 skipped, ruff clean). The 4 P0 files share coordinate system + fiducials → they overlay (test `test_p0_layers_share_carrier_fiducials`). The L2 serpentine + 64 NV sites are checked by `test_p0_waveguide_channel`. Assembly drawing: `docs/p0_assembly.svg` (a top view of every CNC job A–D + the cross-section + the entanglement-bus explanation).

---

## 6. Timeline

| Phase | Active time | Unattended |
|---|---|---|
| F1 (emitter L1) | 2–3 days | CQD synthesis + dialysis 48 h |
| F2 (microdiamonds sol-gel L2) | half a day | sol aging 24 h |
| F3 (detector L3 + stack + end-to-end) | half a day | — |

**Total P0 3 layers: ≈ 3–4 active days** + CQD synthesis. Reproducible in one week.

---

## 7. Roadmap

| Phase | Adds | Requires |
|---|---|---|
| **P0** (this doc) | 3 coarse layers stacked vertically, NV⁻ microdiamonds embedded in a 1 mm TEOS guide, graphene/MoS₂ 8×8 detector on top | CNC 3018 + chemistry bench (no tube furnace) |
| **P1** | 5–10 µm TEOS sol-gel guides (soft lithography), integrated graphene/MoS₂ detector, finer pad pitch | PDMS master, fine Au stencil |
| **P2** | High-coherence CVD NV plate (T₂ ~ms), pulsed MW, T₂/echo | CVD-NV plate ($), pulsed generator |
| **P3** | Full integration = master/diamond-variant protocol | advanced equipment |

---

## 8. Safety (summary)

| Risk | Control |
|---|---|
| ITO/glass dust while milling | N95, wet milling, goggles, wet cleanup |
| TEOS / EtOH / HCl sol-gel | fume hood or ventilation; TEOS is flammable and irritant; HCl corrosive |
| Hydrothermal 220 °C (CQD) | never open hot (<60 °C), PTFE liner |
| IPA/acetone/chlorobenzene | ventilation, no ignition |
| 532 nm laser (≥5 mW, class 3R) | laser safety goggles, no specular reflection, beam stop |
| MW 2.87 GHz <+20 dBm | low power; >30 cm from the antenna |
| Microdiamonds (150–200 µm particulate) | nitrile gloves; sweep/wet cleanup (not respirable nano dust) |
| Silver paste / conductive epoxy | nitrile gloves |

**General:** SDS per product; eyewash; no food/drink; waste in separate containers.

---

*End of the P0·2.0 protocol (vertical 3-layer stack, NV⁻ microdiamonds in TEOS sol-gel, graphene/MoS₂ detector). For the exact emitter chemistry see the master §1; for the NV⁻ physics see the diamond variant `FABRICATION_PROTOCOL_Diamond.md` §2 and §5.2.*