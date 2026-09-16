# EXPERIMENT QSSC-004: PCB Physics Audit — HARDWARE_DESIGN.md, datasheets, RTL

- **Date:** 2026-09-05
- **Base:** main @ `f5b887a` — all `file:line` citations refer to this revision
- **Methodology:** SciMind4SystemicRigorMind 4.0 (documented in
  [`docs/agents/audits.md`](../agents/audits.md), PR #12) — steelman first,
  every number re-derived from the component datasheets, doc ↔ RTL ↔ PCB
  cross-consistency, claims fixed before verdicts.
- **Inputs:** `hardware/HARDWARE_DESIGN.md` (1531 lines),
  `hardware/datasheets/DATASHEETS.md`, `hardware/datasheets/BOM.csv`,
  `hardware/datasheets/pdfs/ADF4351.pdf` (pp. 5–18),
  `hardware/datasheets/pdfs/GVA-84_plus.pdf` (pp. 1–3),
  `fpga/rtl/{adc_reader,top_level,command_parser,adf4351_ctrl}.v`,
  `fpga/constraints.xdc`, and the author-shared
  `docs/QSSC_fabrication_protocol_diamond.pdf` (17 pp., currently untracked
  in the repo).

**Verdict: EVIDENCE GRADE B.** The electronics, RF chain and RTL are
datasheet-verified correct. Six documentation defects were found (F1–F6),
two of them physics-level; plus three documented-as-open engineering gaps
(F10–F12). None of the findings requires a board respin — all are
documentation / RTL-comment fixes best applied before the PCB order.

---

## 1. What was verified correct (steelman)

| Area | Check | Result |
|---|---|---|
| ADF4351 frequency plan | `RF_OUT = f_PFD·(INT + FRAC/MOD) = 25 MHz·(114 + 100/125)` | **2870.00 MHz exact** |
| ADF4351 PFD | REFIN 25 MHz (FPGA `refclk_out` = 100 MHz/4), R = 1 (`adf4351_ctrl.v:136-138`) | 25 MHz — legal (< 32 MHz fractional-PFD limit) |
| Prescaler | PR1 = 1 (8/9), INT = 114 ≥ N_MIN = 75; the RTL comment correctly notes 4/5 is invalid above 3.6 GHz (`adf4351_ctrl.v:127-134`) | valid across the full 2.2–4.4 GHz band |
| ADF4351 SPI timing | datasheet Table 2: CLK high/low ≥ 25 ns → ≤ 20 MHz; RTL SCLK = 100 MHz/8 = 12.5 MHz | compliant — the /8 divider is *required*, not merely safe |
| ADF4351 registers R0–R5 | bit-level comparison against datasheet Figs. 26–28 (`adf4351_ctrl.v:125-152`) | all valid: CP = 7, LDF = 1 (int-N), PD_POL = 1 (passive filter), MUXOUT = LD_PIN = 01 (digital lock detect), BAND_SEL = 200, MTLD = 1 |
| ADF4351 pinout / NC | datasheet Table 5 + Fig. 3 | `DATASHEETS.md` pad table matches 1:1 (pads 1–33 incl. EP); NC list (SW 5, RFOUTA− 13, RFOUTB+/− 14/15, LD 25) datasheet-conform; VREF (C36) and TEMP (C37) decoupling exactly per Table 5 |
| GVA-84+ application circuit | pin 1 RF-IN DC-blocked (C34), pin 3 RF-OUT & DC-IN with RFC choke (L2 100 nH) + DC-block (C35), self-biased +5 V | exactly the datasheet application circuit |
| GVA-84+ thermals | 5 V × 108 mA typ = 0.54 W × 64 °C/W | ≈ 35 °C case rise — uncritical |
| AD7616 strapping | hardware mode (SER/PAR = HIGH), 1-wire (SER1W = LOW → SDOA), sequencer (SEQEN = HIGH), ±5 V via HW_RNGSEL1/0 = 1/0, internal reference (REFSEL = LOW), BUSY of chip 0 only | consistent across §15C, `DATASHEETS.md`, `adc_reader.v` header, `gen_qssc_kicad.py` |
| Detector RTL ↔ PCB ↔ doc | `adc_reader.v` ↔ `constraints.xdc` (A18/B18/D18/E18/M18/F18/G18/J18/L18, LVCMOS33, FAST/8 on SCLK) ↔ J12 pin map (Würth 61302011121/61002021121) | fully coherent; GND-flanked signal pins |
| TIA math (§15C) | f_c = 1/(2π·1 MΩ·1 pF) = 159 kHz; 0.13 pA/√Hz × √159 kHz ≈ 51 pA RMS → 51 µV | correct (`HARDWARE_DESIGN.md:1255-1259`) |
| TLC5947 | I_OUT = 41 × 1.20 V / 4.87 kΩ = 10.1 mA/ch; 33 V OUT rating; internal 4 MHz oscillator (no Gsclk needed); 12 V anode headroom vs 4–8 V turn-on | correct |
| Power hardening (§15B) | SS14 backfeed protection, 22 µF AMS1117 bulk, π-filter, per-rail decoupling, reset RC | sound |

## 2. Findings

### F1 · HIGH — Optical link budget contradicts itself by ~4 orders of magnitude

**Defect.** The fabrication-protocol PDF states the emitter target as
"target radiance ≥ 10 cd/m²" (§1) while §5.2 of the same document computes
with "~1 mW at the sample".

**Evidence / physics.** At 532 nm, 10 cd/m² ≈ 15 mW·sr⁻¹·m⁻² radiant; a
1 mm² Lambertian pixel then emits ≈ 45–50 nW. Through the chain the PDF
itself specifies — absorption 1.5–78 % (0.5 mm path at the protocol's NV
density range), PL quantum yield ~10 %, diamond escape cone ~1/(4n²) ≈ 4.3 %
(n = 2.4) — the PL reaching the detector is ≈ 3–160 **pW**. With graphene
responsivity 0.01–0.1 A/W that is 0.03–16 **pA** — below the TIA's own
51 pA RMS noise floor (`HARDWARE_DESIGN.md:1257`). With MoS₂ photogating
(10–100 A/W) it is 30 pA–16 nA — marginal. At "~1 mW at the sample" every
number gains 4 decades and the chain works comfortably. Feasibility of the
entire readout chain therefore flips on which of the two numbers is real,
and **§5.1 (EL test) never measures optical power.**

**Fix.** Add a photodiode power measurement (at-the-sample, µW-class) as a
mandatory step of the §5.1 EL test; state the radiance → W link budget
explicitly. Also commit the PDF to the repo so the target is versioned
(it is currently shared out-of-band only).

### F2 · HIGH — Three divergent chip concepts; the PCB matches none of the PDF

**Defect.** Three mutually incompatible chip definitions are in circulation:

| Source | Concept |
|---|---|
| Fabrication-protocol PDF | 25×25 mm ITO glass, 8×8 1 mm² pixels, 3×3×0.5 mm CVD NV plate, SU-8 waveguides, edge coupling, TEC under Si carrier |
| `HARDWARE_DESIGN.md` §15 | 20×20 mm two-faced die, 129 pads @ 0.30 mm pitch, Ironwood SBT socket (emitter face) + ACF head (detector face), no waveguides, detectors direct on the top face |
| Author's stated current build (chat) | 20×20 mm, spin-coated Adámas NV **microdiamonds**, TEOS sol-gel instead of SU-8, fully parallel readout |

The PCB (both variants) targets row 2; what is actually being fabricated is
row 3.

**Physics consequence.** Spin-coated microdiamond layers are
surface-dominated: literature T₂ for µm-class particles is µs-to-tens-of-µs
class, not the 1.8 ms bulk ¹²C-enriched CVD value the compute claims rest on
(README "Defect platforms" table). The author's own assessment ("just
diamonds pass the simulation test … others just valid for sensor arrays")
concedes this. As built, the hardware is an honest **sensor array**, not a
compute substrate.

**Fix.** Declare one canonical concept (recommended: the 20×20 mm two-faced
die, since the PCB is built around it), synchronise PDF + README +
HARDWARE_DESIGN to it, and scope the 300 K compute claim to bulk-enriched
CVD explicitly.

### F3 · MEDIUM — "All 16 channels sample simultaneously" is contradicted by the repo's own RTL

**Defect.** `HARDWARE_DESIGN.md:604` ("the AD7616 samples all 16 of its
channels simultaneously on a single [CONVST]") and `:1265` ("all 16 channels
sample at the same instant on the CONVST edge") — plus the generic
"simultaneous-sampling" wording at :19, :135, :302, :344, :447, :478, :585,
:602, :618, :862, :955, :1233, :1241, :1272, :1299.

**Evidence.** The AD7616 is a **dual-SAR**: each CONVST converts one VA/VB
**pair** (2 channels); the on-board sequencer needs **8 CONVST pulses per
full 16-channel scan**. The repo's own RTL states exactly this:
`fpga/rtl/adc_reader.v:17-19` — "sequencer enabled (SEQEN=HIGH) -> 8 CONVST
pulses auto-scan all 16 channels per chip (each CONVST returns one VA/VB
pair = 2 channels)". What *is* simultaneous: the shared `adc_convst` starts
one pair-conversion on all 4 chips at once (`:862` is defensible as
written). Also `:618` ("up to 1 MSPS/channel") overstates by ~8×: the
1 MSPS figure is per pair in direct mode; in the wired sequencer mode it is
~125 kSPS per channel per chip, full-frame scan of order 10 kHz per RTL
timing.

**Impact.** None functional — the frame rate exceeds the 1 kHz lock-in
modulation by > 10×. But the documentation mis-describes the hardware it
ships.

**Fix.** Replace the 16-channel simultaneity wording with pair-wise (VA/VB)
simultaneity per CONVST + 8 CONVST per full scan; state the aggregate
per-channel rate.

### F4 · MEDIUM — TIA bandwidth: §8 says ~106 kHz, §15C says 159 kHz (159 kHz is correct)

**Defect.** `HARDWARE_DESIGN.md:306` ("1 pF → ~106 kHz") and `:599`
("≈ 106 kHz") vs `:1255`: 1/(2π·1e6·1e-12) ≈ **159 kHz** (and :1257/:1259
build on 159 kHz). The §15C value is arithmetically correct; §8 is an
internal inconsistency.

**Fix.** Change :306 and :599 to ≈ 159 kHz.

### F5 · MEDIUM — RF output estimate "+18 to +20 dBm" uses the 2 GHz gain figure at a 2.87 GHz operating point

**Defect.** `HARDWARE_DESIGN.md:499-500`: "Expected output ≈ +18 to +20 dBm
depending on GVA-84+ gain (+18.4 dB @2 GHz) and band."

**Evidence.** GVA-84_plus.pdf p. 2 (5 V, 25 °C): gain typ **18.4 dB @2 GHz**
but **16.0 dB @3 GHz** (→ ~16 dB at the 2.87 GHz NV and ~15.5 dB at the
3.46 GHz hBN operating points). ADF4351.pdf Fig. 28 (R4 D2:D1): RF_PWR 00 /
01 / 10 / 11 → **−4 / −1 / +2 / +5 dBm**. Output therefore: **+12…+18 dBm**
at low/mid drive; +20…+21 dBm only at RF_PWR = 11 near P1dB (20.6 dBm typ
@2 GHz).

**Impact.** The stated range overestimates typical operation by ~5 dB and
the +20 dBm board-level safety limit can be marginally exceeded at max
drive.

**Fix.** Restate the expected output vs RF_PWR using the in-band gain
values; either cap RF_PWR at 2 (RTL/host default) or update the safety
statement.

### F6 · MINOR — ADF4351 CP-current comment is wrong by more than one code step

**Defect.** `adf4351_ctrl.v:136`: "CP=7(~2.19mA)".

**Evidence.** ADF4351.pdf Fig. 26 (I_CP table): 2.19 mA = **code 6 (0110)**,
code 7 (0111) = **2.50 mA** (at RSET = 5.1 kΩ). The register really contains
`4'd7` (`:138`). The board populates **R73 = 4.7 kΩ** (`HARDWARE_DESIGN.md:1412`),
so per the datasheet relation I_CP = 25.5/R_SET all table values scale by
5.1/4.7 → **≈ 2.71 mA actual**. Loop bandwidth scales with √I_CP, so the
documented ~50 kHz is in reality ~56 kHz.

**Impact.** None (loop remains far inside stability bounds), but the
commented constant is 24 % off the real charge-pump current.

**Fix.** Correct the comment to "CP=7 (≈2.5 mA @5.1k; ≈2.71 mA with R73=4.7k)"
or load CP = 6 if 2.19 mA was the intent.

### F7 · MINOR — Stale references (deleted docs/, phantom local PDFs, ACF-vs-SBT self-contradiction)

- `HARDWARE_DESIGN.md:525, :1450` and `DATASHEETS.md:217, :350` cite
  `docs/FABRICATION_PROTOCOL*.md` — deleted in commit `a3ab46b`.
- `DATASHEETS.md:212, :283` claim local PDFs `pdfs/TLC5947.pdf` and
  `pdfs/TLV9004.pdf` — the directory contains only 74HCT240, 74LVC1G04,
  ADF4351, GVA-84_plus.
- `DATASHEETS.md:30-34` describes the **emitter** face contacted via ACF
  pressure contact, while `:341-342` correctly states Ironwood SBT,
  "NOT ACF". (The detector-face ACF paragraph at :267 is current.) The :30-34
  paragraph is a pre-pivot leftover.

**Fix.** Delete or rewrite the three stale spots; download the two missing
datasheets or drop the "Local PDF" lines.

### F8 · MINOR — `adc_top.v` no longer exists (now `adc_reader.v`); README module count self-contradicts

- `HARDWARE_DESIGN.md:440, :1243, :1281, :1504` reference `adc_top.v` — the
  actual module is `fpga/rtl/adc_reader.v`.
- `README.md:182` says "8 modules", `README.md:313` (and :32) say 9 — the
  tree contains 9 (`top_level`, `command_parser`, `uart_rx`, `uart_tx`,
  `spi_master`, `adf4351_ctrl`, `emitter_array`, `adc_reader`,
  `tlc5947_ctrl`).

**Fix.** s/adc_top/adc_reader/ in the four places; s/8 modules/9 modules/ at
README:182.

### F9 · MINOR — RTL comment: adc_reader SCLK is 12.5 MHz, not 25 MHz

`adc_reader.v:240`: "4 clk cycles per half -> 25 MHz". 100 MHz/8 = **12.5 MHz**
(the design is correct — and must be ≤ 20 MHz for ADF4351-compliance on the
PLL SPI, see Table 2; the comment is what is wrong).

**Fix.** s/25 MHz/12.5 MHz/.

### F10 · OPEN — Mechanical: head board located only by J12

The 150×140 mm head board aligns to the main board solely through the
2×10 2.54 mm J12 connector; stack height and ACF bond force are deferred.
No standoffs/pins are documented. (Documented as open in §15; listed for
completeness before ordering.)

### F11 · OPEN — 50 Ω geometry

0.30 mm trace / 0.15 mm gap microstrip on εr 4.3 computes to ≈ 43 Ω, not
50 Ω; deferred to a field solver in the docs. Resolve before impedance-
controlled fab.

### F12 · OPEN — Thermal budget of the sandwich

Die emitters 64 × (4–8 V) × 10.1 mA ≈ 2.6–5.1 W plus 3× TLC5947 at
≈ 0.9–1.7 W each, against a "TEC < 5 W" statement; the heat path through
the two-board sandwich is not modelled anywhere.

## 3. Ockham (BIC)

One process explains all six documentation defects: the *electronics* was
designed against the datasheets (and survives that comparison), while the
*physics narratives* (simultaneity, dBm chain, radiance) were carried over
from the pre-AD7616 design generation and never re-derived. No competing
hypothesis needed.

## 4. Systemic impact

The board will very likely work as a **sensor readout card**. Whether it
works as a **quantum-chip readout** is decided not by the electronics but by
the open question in F1: how many nW/mW actually arrive at the detector?
That question costs one photodiode (~50 EUR); the board costs considerably
more.

---

**EVIDENCE GRADE: B**
**P-VALUE:** < 0.001 for the arithmetic findings (F3–F6: each reproducible
directly from datasheet tables or the repo's own RTL); F1's contradiction is
statuted inside the source document itself.
**CONCLUSION:** No nonsense in the electronics — the RF chain, ADC strapping,
TIA math, emitter driver and RTL are datasheet-verified sound. Two
physics-level risks should be closed **before the PCB order**: (1) measure
the actual optical power at the sample (F1), (2) pin down which chip concept
is canonical and scope the compute claim to bulk CVD (F2).
**STRATEGIC VECTOR:** ① F1 → photodiode measurement in the EL test. ②
F3–F9 → documentation fixes (mechanical seds, no design change). ③ F2 →
single-source-of-truth chip concept. ④ F10–F12 → resolve before fab release.

*Self-disclosure: SciMind4SystemicRigorMind 4.0 audit. All numeric claims
were taken from the datasheets shipped in this repo (ADF4351.pdf pp. 5–18,
GVA-84_plus.pdf pp. 1–3) or re-derived from the repo's own RTL — none from
memory. The three concept sources (fabrication PDF, HARDWARE_DESIGN.md,
author chat) were treated as independent artefacts and deliberately *not*
harmonised; the divergence is the finding. Auditor: independent agent
review, not the board author.*