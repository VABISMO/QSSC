# Ququint path to the QSSC device

Status: analysis document. Companion to `qssc/ququint.py` (Tier 0, done,
results snapshot in `ququint_sim_results.json`). The bench inventory this
path is written against lives in the collaboration scratch space
(`vabismo_lab_inventory.md`), not in this repo.

**Honesty rule (inherited from the prereg): nothing below may be described as
"measured on the chip". Only Tier 0 numbers exist, and they are simulation
model numbers.**

## 0. What "Ququint" is here

Ququint = GF(5)-qudit processing: one physical carrier holds a 5-level system,
arithmetic runs in F_5, and the entangling primitive is the cubic-phase gate
CCZ_d: |a,b,c> -> omega^{a*b*c} |a,b,c>, omega = exp(2i*pi/5) — the d=5
magic state for qudit stabilizer codes (injected as CCZ|+++>).

Why it is interesting at all: the qudit CCZ needs **4 magic gates vs 7 T gates**
for the equivalent qubit CCZ (Campbell et al., QEC 2016 — magic-state
distillation thresholds, 36.3% for d=5 per the sim-layer docstring), and the
distillation threshold for prime d=5 is high enough that a noisy device can
run the magic-state factory instead of distilling forever.

The gate alphabet (`qssc/ququint.py`): X_d (shift mod 5), Z_d (phase),
F_d (DFT), CCZ_d. All are single-/two-/three-qudit unitaries on a d=5
register.

## 1. Tier 0 — simulation (DONE)

`ququint_sim_results.json`, seed 42, 4000 shots/point, own depolarizing
Monte-Carlo on GF(5) statevectors:

| p (per-gate depol) | qubit CCZ (7 T, 13 steps) | qudit CCZ (4 M, 4 steps) | advantage |
|---|---|---|---|
| 0.001 | 0.994 | 0.9952 | +0.0012 |
| 0.01  | 0.9333 | 0.9645 | +0.0312 |
| 0.05  | 0.7322 | 0.8600 | +0.1278 |
| 0.1   | 0.5515 | 0.7165 | +0.1650 |

Phase check passed: CCZ matrix trace element matches exp(2i*pi/5)
(real 0.309017, imag 0.951057).

Grades: gate counts = literature/decomposition argument; fidelities = this
file's own model; **not hardware**.

## 2. Tier 1 — GF(3) on the existing NV stack (nearest term)

The QSSC stack has NO native 5-level system (sim-layer honesty contract):
NV electron used as 2-level, 14N I=1, 13C I=1/2. But it DOES have native
**3-level** systems, twice over:

* NV electron: S=1, ms = -1, 0, +1 — a full spin-1 triplet. The repo's
  teaching model deliberately uses only the 0 <-> +1 two-level subspace;
  the -1 level physically exists and has its own ODMR transition.
* 14N nucleus: I = 1 -> exactly 3 levels.

So GF(3) (d=3) is a native encoding in hardware that is already on the
bench. Ternary CCZ is the same diagonal cubic phase with omega =
exp(2i*pi/3); the qutrit magic-state story (Campbell et al.) is at least as
favorable as d=5.

What is missing to use it:

1. **Sim** (cheap, do first): extend `qssc/ququint.py` with D=3 and
   re-run the CCZ advantage table. Grade: sim_constant.
2. **Control**: drive both ODMR transitions (0 <-> +1 at ~2.87 GHz and
   0 <-> -1 at ~2.87 GHz - 2*gamma_e*B; a small bias field B separates the
   two dips). Needs a 2-tone microwave source or frequency hopping — the
   ADF4351 covers 2.87 GHz natively. Grade: engineering_target.
3. **Readout**: 3-level readout = sequential ODMR readout at both dips
   (standard technique, e.g. ms-resolved readout). The AD7616 chain in the
   repo already treats the ODMR dip as the observable. Grade: engineering_target.

Tier 1 is the cheapest way to get a real multi-level qudit result on this
collaboration's own hardware: d=3 first, d=5 only after Tier 2 exists.

## 3. Tier 2 — GF(5) via a 5-level subspace of 51V (I=7/2) in SiC

The sim-layer contract says GF(5) "needs a spin-2 (I=2) nucleus". That is
true for a **full-manifold** encoding, but not the only route: any nucleus
with 2I+1 >= 5 supplies a valid 5-level **subspace**. The cheapest realistic
candidate:

* **51V** (vanadium): I = 7/2, natural abundance 99.75% -> 8 nuclear Zeeman
  levels; a 5-of-8 subspace (e.g. m = -2..+2) is an exact GF(5) register.
* Material route: **vanadium-doped SiC is commercially standard** —
  high-purity semi-insulating 4H-SiC wafers are vanadium-compensated.
  Substitutional V4+ on the Si site (3d^5) has a textbook EPR hyperfine
  octet from 51V.
* Precedent for the encoding style: multi-level nuclear-spin qudits in
  solid-state donors are an established program (high-I nuclei such as
  209Bi, I = 9/2, in silicon). Grade: literature-anchored; the specific
  citation still needs to be added before any hardware step cites this.

**Honesty on numbers:** the repo's `SiC-VSi` profile row
(`qssc/defects.py`, T1 340 us, T2 40 us, D = 70 MHz,
readout error 0.10) is the **silicon vacancy defect**, not substitutional
vanadium. Same host crystal, different defect. Those T1/T2 values may be
used for 51V in SiC only as an order-of-magnitude prior; nuclear-spin
coherence times are typically far longer than electronic ones, but **no
SiC:V T1/T2 exists in the profile** — grade: proposal, needs its own
characterization.

### Leakage — the real cost of 5-of-8

The sim layer assumes exact mod-5 arithmetic: X5 maps |+2> -> |-2>. A
physical 5-of-8 subspace instead leaks m = +2 -> +3 at the boundary. Two
mitigations:

* encode on the 5 central levels and drive only subspace-internal
  transitions (leakage probability per gate must be measured and then
  added to the sim);
* or use the full 8-level ladder and re-define the GF(5) arithmetic as a
  5-level subspace code with leakage detection (bigger change to the
  architecture).

The current sim numbers do NOT include leakage — a `D=5, n_levels=8`
variant of `qssc/ququint.py` is the right next sim step.

### Control requirements (mapped to the actual bench)

51V nuclear Zeeman ladder: adjacent transitions at nu = (gamma_V/2*pi) * B,
gamma_V/2pi = 11.2 MHz/T. Realistic bench magnets (0.1-0.7 T) put the
ladder at **1-8 MHz — below the ADF4351's ~35 MHz lower bound**. The V4+
electronic defect (S=3/2, ZFS tens of MHz) is fine on the ADF4351, but the
**nuclear RF tier is not**. This is the concrete Tier-2 hardware gap:

1. Coherent RF source, 1-100 MHz, phase-coherent with <us switching —
   a DDS (AD9910-class) or FPGA + mixer, NOT the MCP4921 DAC (DC SPI
   device, no coherent phase). Grade: required purchase/build.
2. RF amplifier + tuned coil/resonator for the nuclear band.
3. Bias-field magnet + Hall probe (also needed for Tier 1 dip separation).
4. The quadrupole interaction (I = 7/2 > 1/2) makes the ladder
   non-equidistant, which is what makes level-selective driving possible
   at all with a single tone per transition — this is standard for
   half-integer nuclei, grade: literature-anchored.

### Readout — the weakest link

Nuclear-state readout will be indirect: via the hyperfine-coupled V4+
electronic defect (EPR/ODMR with hyperfine selectivity), possibly with
cross-relaxation amplification. This is the least-specified link on the
path; treat it as open research, not engineering. Grade: open.

## 4. Tier 3 — native I=2 nucleus (reject option)

Exactly-5-level single nucleus: **36Cl** (I = 2, t_1/2 = 3.01e5 y) or
similar. Full-manifold GF(5) with zero structural leakage — but radioactive
(implantation, dosimetry, licensing). Only worth revisiting if Tier 2's
leakage or readout fails. Grade: reject unless forced.

## 5. Decision tree

```
Tier 0  sim d=5 CCZ advantage               DONE (model numbers)
Tier 1a sim d=3 CCZ advantage (D=3 variant)  ~1 day, no hardware
Tier 1b NV triplet 2-tone ODMR control       bench, after board arrives
Tier 1c 3-level sequential readout demo      bench
        -> first real multi-level qudit result (d=3, native defect)
Tier 2a sim d=5 with 8-level leakage model   ~2-3 days sim
Tier 2b SiC:V sample + coherent RF chain     hardware project
Tier 2c SiC:V 51V nuclear-qudit readout      open research
Tier 3  36Cl implant                         only if 2b/2c fail
```

The pre-registered QSSC benchmark (collaboration scratch space, not in
this repo) stays a 2-level (qubit) result —
ququint changes the gate counts and the noise channel, not the
prime-readout/ZNE/DD predictions being tested there.

## 6. What is claimed vs what exists (one line each)

* CCZ advantage numbers: own simulation, seed 42 — model.
* 4-M vs 7-T CCZ counts: literature/decomposition argument.
* 36.3% d=5 threshold: literature (Campbell et al., per the `qssc/ququint.py`
  docstring).
* Native d=3 in the NV stack: property of the defect physics — literature.
* 51V I=7/2, 99.75%, SiC:V commercial: literature.
* SiC:V T1/T2 at 300 K: **unknown** — the SiC-VSi profile row is a different
  defect; do not reuse those numbers without a note.
* Anything on the actual boards: nothing measured yet.