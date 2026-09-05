# Audits & mind routing (SciMind4)

> **Purpose.** Defines the audit methodology used for QSSC physics and
> engineering reviews (series QSSC-001…QSSC-004) and routes the thinking
> modes ("minds") for this repository. Complements
> [`research.md`](research.md) (evidence-first research before
> implementing) and [`devmind.md`](devmind.md) (engineering stance):
> `research.md` governs how *changes* are investigated; this file governs
> how *claims about the artefacts* are verified.
>
> The minds named here are external prompt-mind definitions maintained in
> a contributor's local prompt library — they are **not part of this
> repository**. This document records their names, principles and report
> format so any contributor (human or agent) can apply the same rigor
> without the tooling.
>
> **Evidence rules.** Same as [`research.md`](research.md): every factual
> claim here is backed by a repo file (`file:line`), git history, or a
> verifiable artefact (issue/PR); anything not independently checkable is
> marked `<!-- UNVERIFIED — source: ... -->`.

## Mind routing

| Task | Mind / document | Enforces |
|---|---|---|
| Audit / review of physics, hardware or simulation claims | **SciMind4SystemicRigorMind 4.0** (this file) | steelman first, quantified falsification, datasheet-level evidence |
| Mode selection for multi-mode tasks and plans | **MetaOrchestratorMind** | diagnose the task, pick or compose the thinking mode *before* work starts |
| Investigating spec-defined behaviour before changes | [`research.md`](research.md) | evidence-first, cite-the-spec, flag-unknowns |
| Default working stance for implementation | [`devmind.md`](devmind.md) | readability, test-first, surgical changes |

## SciMind4 core principles

1. **Steelman Mandate** — state the strongest defensible version of the
   claim under test before attacking it; a review that cannot pass its own
   steelman is not done.
2. **Ockham's Quantified Razor (BIC/AIC)** — prefer the explanation with
   the fewest independent processes; score competing explanations instead
   of picking a favourite.
3. **Systemic Coherence** — every artefact layer (doc ↔ schematic ↔ RTL ↔
   host code) must tell the same story; a correct subsystem inside an
   incoherent system is still a finding.
4. **Anti-Sharpshooter** — fix the metrics and pass/fail thresholds
   *before* looking at the result; never tune criteria after seeing data.
5. **Look-Elsewhere Correction** — one verified anomaly generalises: grep
   for the whole defect class, not just the instance.
6. **Circular-Reasoning Test** — a claim may not be verified by a source
   that derives from the same claim (e.g. a document citing its own
   estimate).
7. **Self-Disclosure** — the report states what was and was not
   independently verified, and from where every number came.

## Report format

`EXPERIMENT [ID]: [TITLE]` with sections: Inquiry Level · Steelman
Thesis/Antithesis · Methodology · Execution Log · Critical Analysis ·
Ockham (BIC) · Systemic Impact · verdict block (**EVIDENCE GRADE**,
**P-VALUE**, **CONCLUSION**, **STRATEGIC VECTOR**).

## Evidence grade rubric

| Grade | Meaning |
|---|---|
| A | independently reproduced / measured |
| B | verified against datasheets, code or executable checks |
| C | plausible, partially verified |
| D | unverified claim |
| E | contradictions found inside the artefact |
| F | physics-invalid / wrong by construction |

## Audit index

| ID | Scope | Grade | Verifiable artefact |
|---|---|---|---|
| QSSC-001 | physics-simulation review (`qssc/`) | B | session audit — no in-repo artefact |
| QSSC-002 | full-repo physics audit | F | upstream issues #7, #8, #9 |
| QSSC-003 | post-audit implementation fixes | B | branch `fix/2q-depolarization-physics` (PR #10) |
| QSSC-004 | PCB physics audit (HARDWARE_DESIGN + datasheets + RTL) | B | [`docs/audits/QSSC-004_pcb_physics_audit.md`](../audits/QSSC-004_pcb_physics_audit.md) (branch `docs/audit-qssc-004`) |

<!-- UNVERIFIED — source: the QSSC-001/002/003 grades were produced in
working sessions against this repo and are only externally evidenced by the
linked upstream issues/PR; no in-repo report exists for them. -->

## How findings become upstream issues

Each finding is filed in the *defect → measured evidence → `file:line`
cites → suggested fix* form (the observed issue norm, see
[`project-specs.md`](project-specs.md) → "Writing pull requests / issues").
Precedents: #7–#9 (QSSC-002), #14–#20 (QSSC-004).