# QSSC Inventions — status catalog

Standalone index of the five chip inventions (1–5) plus the prime-coprime
readout (2+). Each entry lists what it does, the module that implements it in
the **simulator**, its **hardware** status, and what (if anything) must be
touched on the PCB to run it on real hardware.

This file is the single source of truth for "is invention X real in sim /
real / FPGA?" — kept separate so no existing doc has to be edited just for
this branch.

Status keys:
- **SIM ✅** — implemented + tested in the qiskit simulator backend
  (`qssc/`) and/or the REST API (`api/`).
- **HW-fits** — runs on the **current** PCB with a firmware verb (RTL to do).
- **needs-HW** — needs a PCB addition (new DAC / coil / channel) before any
  firmware can drive it; the simulator models the physics today.

Grounding rule (project-wide): physics numbers come only from
`qssc/defects.py` (the NV datasheet values: T2 = 1.8 ms, linewidth = 5 MHz,
detuning spread = 2 MHz). Anything not in the datasheet is flagged
"NOT QUOTED BY THE SUPPLIER" with a literature anchor.

---

## 1. 64-pixel vector magnetometer (4-axis NV <111>)

Each pixel's NV axes (the four <111> tetrahedral directions) split into a
doublet whose gap is 2γ|B·n_i|; a least-squares search over the 2^k sign
combinations recovers B, with a gradiometric reference to break the
genuine tetrahedral sign ambiguity (two B-vectors give identical unsigned
splits).

- **SIM ✅** — `qssc/magnetometry.py` (`PixelMagnetometer`,
  `MagnetometerResult`, `magnetometer_sweep`, `synthetic_field_map`,
  `_recover_b_vector` with `reference_t` sign-locking). Endpoint
  `GET /api/sensor/magneto` (`api/main.py`).
- **Tests** — `tests/test_magnetometer.py` (7 tests: field-map shape,
  B-vector round-trip, noise floor, 64-pixel sweep, differential
  suppression, average mean/std, zero-field floor).
- **HW-fits** — the real path is the **existing** `ODMR_RAMP` /
  `ODMR_SWEEP` FPGA verb (ADF4351 PLL sweep + gated ADC read), then the
  host fits the doublet splits → B per pixel. No new hardware. The
  endpoint today feeds a synthetic field map; the live path = stream the
  sweep, decompose with `qssc/odmr_decompose.py`, recover B.
- **needs-HW** — none for the basic magnetometer. (A reference pixel for
  gradiometric sign-locking is a second detector channel — already on the
  64-detector array.)
- **Commit** — `1cf094d`.

---

## 2+. Prime-coprime frequency-multiplexed readout + denoiser

Each addressable NV channel in a pixel is driven at a modulation period
`T_i = p_i · tau` with `p_i` PRIME (16 symbol channels = the first 16
primes). Distinct primes are pairwise coprime, so the lock-in references
(cosines at `1/(p_i·tau)`) are EXACTLY orthogonal over the LCM
(`= p_a·p_b`, Fourier orthogonality over whole cycles) — zero cross-talk
between prime channels, not merely small. The receiver projects the
fluorescence onto the prime set and rejects energy at non-prime periods
as noise (a coprime matched-filter / denoiser; rejects OUT-of-band noise,
in-band noise inside a prime channel's linewidth still passes).

- **SIM ✅** — `qssc/prime_readout.py` (`PRIME_SYMBOLS`, `usable_primes`,
  `best_prime_for_nv`, `assign_prime_channels`, `prime_orthogonality`,
  `prime_decompose`, `prime_denoise`, `prime_noise_rejection`,
  `cross_talk_residual`). Grounded in the NV profile (T2 = 1.8 ms,
  linewidth = 5 MHz): a channel is usable iff `1/linewidth < p·tau < T2`.
- **Tests** — `tests/test_prime_readout.py` (12 tests incl.
  `test_16_symbols_are_channels_not_qudit_states` pinning that 16 prime
  symbols = 16 ADDRESS CHANNELS = 16 NVs, NOT a 16-state qudit — a 10-NV
  pixel is `2^10`, not `16^10`).
- **HW-fits — DONE** — the new `READ_LOCKINP <site> <n_cycles>
  <half_ns>` FPGA verb (branch `fpga/run-all-prime-lockin`, commit
  `375d394`) demodulates at a host-supplied on/off half-period
  `half_ns = p*tau/2` for the prime `p` this NV's channel uses. The prime
  selection + coprime orthogonality stays in Python (`qssc.prime_readout`);
  the RTL just demodulates at the requested half. Backward compatible:
  `READ_LOCKIN` keeps the 200 us default. One global MW antenna + the 64
  parallel detectors is enough: the prime period is the modulation timing,
  independent of the carrier.
- **needs-HW** — none for the lock-in demod. (Per-NV frequency addressing
  at the carrier needs the per-NV ODMR detuning to exceed the linewidth —
  the existing `frequency_detuning_spread_hz = 2 MHz` model; physically
  this is the microdiamond's inhomogeneous spread, not a new DAC.)
- **Commit** — `bbbc8fe`.

---

## 3. Nuclear-spin memory (NV + ¹³C)

Each electron NV is paired with a ¹³C memory qubit whose T2 (~1 s,
literature — NOT QUOTED BY THE SUPPLIER; Reimer 2016, Taminiau 2014,
Cramer 2016) is ~550× the electron's (~1.8 ms). STORE = SWAP(i, i+n_e),
RECALL = SWAP back; the electron-¹³C SWAP time = 1/(2·A) ~ 250 ns at the
hyperfine A = 2 MHz.

- **SIM ✅** — `qssc/nuclear.py` (`nuclear_swap_time_s`,
  `nuclear_gate_fidelity`, `memory_survival`, `nuclear_memory_advantage`) +
  `qssc/backend.py` (`nuclear_memory=True` doubles the register, electron-
  nuclear SWAP edges in `_neighbor_pairs`, nuclear T2 in the noise model).
  `qssc/defects.py` carries `nuclear_t2_s`, `nuclear_hyperfine_hz`.
- **Tests** — `tests/test_nuclear_memory.py` (10 tests).
- **needs-HW** ❗ — a real ¹³C SWAP is **double-resonance**: MW at 2.87 GHz
  (electron, present) **AND** RF at ~2 MHz (¹³C hyperfine, **ABSENT**).
  The current PCB has ONE MW antenna and NO RF channel at 2 MHz. To run
  this on hardware you must add:
  - an **RF DAC channel at ~2 MHz** (a second synthesizer / DDS + an RF
    coil close to the diamond), and
  - an RTL verb that drives MW + RF in a SWAP-timing pattern (kind 3 in
    `RUN_SEQUENCE`).
  Until that RF channel lands, the simulator is the only place this
  invention runs. No fake RTL — the existing MW cannot do a ¹³C SWAP.
- **Commit** — `745fbb9`.

---

## 4. 64-parallel distributed computing

The 64 die pixels are INDEPENDENT ~10-NV processors at 300 K (1/r³ → 0 at
the 2.5 mm pitch → no cross-pixel coupling): the chip's compute throughput
is 64× a single pixel, NOT one 640-qubit circuit (64×10 > the 63-qubit MPS
cap). `run_array` runs one circuit per pixel with an independent noise seed
per pixel and aggregates.

- **SIM ✅** — `qssc/backend.py` `QSSCBackend.run_array(circuits, shots,
  max_pixels=64)`; `api/runner.py` `run_parallel` (shor / sample / custom);
  `api/main.py` `POST /api/circuit/run-parallel`.
- **Tests** — `tests/test_parallel_computing.py` (8 tests).
- **HW-fits (readout DONE)** — the 64 detectors + 4× AD7616 read all 64
  pixels in parallel. The **existing `READ_DETECTORS` verb**
  (`CMD_READ_ALL`, already in the firmware) already sweeps all 64
  detectors in one command — the parallel-64 READOUT was already there.
  So the 64× throughput readout path needs no new RTL.
- **needs-HW** ❗ — independent per-pixel **gate sequences** (a different
  circuit per pixel simultaneously) need per-pixel MW addressing. Today
  there is ONE global MW antenna, so all pixels see the same MW drive at
  once. Options to run the full invention on hardware:
  - **frequency-multiplexed drive** — address each pixel's NVs at a
    distinct ODMR frequency (uses the prime readout's orthogonality, but
    at the MW carrier, not just the modulation), so different pixels run
    different gates in the same wall-clock window. Needs the ADF4351 to
    hop fast per gate (already a PLL; feasibility TBD by the lock time).
  - **per-pixel MW coils** — 64 small loop antennas, one per pixel
    (a PCB revision; large).
  Until one of those lands, hardware "parallel" = the same circuit on
  every pixel (the 64× throughput path, via the existing `READ_DETECTORS`), not 64 different
  circuits at once.
- **Commit** — `570d051`.

---

## 5. Tunable register width via field gradient

A field gradient across the die makes adjacent NVs (within a pixel) sit
one ODMR linewidth apart, so the per-pixel register width becomes a
MEASURED quantity: `resolvable = floor(gradient · pixel_size / linewidth)`
(Case D threshold; separation > linewidth else the lines collapse).

- **SIM ✅** — `qssc/simulator.py` `resolvable_register_width(...)` +
  `auto_align(gradient_mhz_per_um=, pixel_size_um=)` caps `n` to the
  resolvable count; `qssc/backend.py` `__init__(gradient_mhz_per_um=,
  pixel_size_um=)`.
- **Tests** — `tests/test_field_gradient_register.py` (5 tests).
- **needs-HW** ❗ — a **field-gradient coil + DAC** (a small pair of
  anti-Helmholtz coils or a shaped pole piece + a DAC channel the FPGA
  drives). The current PCB has NO gradient coil. To run this on hardware:
  - add the **gradient coil + DAC** to the PCB, and
  - an RTL verb that sets the DAC current per sweep (then `ODMR_SWEEP`
    measures the line count → the register width).
  The simulator models the physics today; the PCB needs the coil.
- **Commit** — `8428f96`.

---

## Supporting modules (already implemented, used by the inventions)

- `qssc/odmr_decompose.py` — ODMR spectral decomposition into NV lines
  (Gaussian/Lorentzian peak-finding), `b_field_from_odmr`,
  `count_nv_centers`. Used by invention 1 (B-field) and the base readout
  for 2+. SIM ✅.
- `qssc/dipole.py` — within-pixel NV-NV dipole coupling (1/r³), CZ gate
  time, addressing leakage, gate fidelity. Used by the per-pixel
  register model. SIM ✅.
- `qssc/sensors.py` — lock-in integrated readout, compass/gyro. SIM ✅.

---

## Summary table

| # | Invention | SIM | HW-fits (RTL status) | needs-HW (PCB add) |
|---|---|---|---|---|
| 1 | 64-pixel vector magnetometer | ✅ | ODMR_SWEEP → B (existing verb) | — |
| 2+ | Prime-coprime readout + denoiser | ✅ | `READ_LOCKINP` verb DONE (`375d394`) | — |
| 3 | Nuclear-spin memory (NV+¹³C) | ✅ | — | RF DAC channel at ~2 MHz + RF coil + RTL kind 3 |
| 4 | 64-parallel distributed computing | ✅ | `READ_DETECTORS` 64-sweep DONE (existing verb) | per-pixel MW (freq-mux or 64 coils) for independent circuits |
| 5 | Gradient register width | ✅ | — | gradient coil + DAC + RTL verb |

## What to touch on the PCB for the needs-HW inventions

1. **RF channel for ¹³C SWAP (invention 3):** add a DDS/synthesizer at
   ~2 MHz + a small RF coil near the diamond die + an FPGA SPI/DAC port
   (mirror the ADF4351 control path). Then add `RUN_SEQUENCE` kind 3
   (MW+RF SWAP timing) in `fpga/rtl/command_parser.v`.
2. **Gradient coil (invention 5):** add a gradient coil pair + a DAC
   channel the FPGA drives + a `SET_GRADIENT` verb. Then `ODMR_SWEEP`
   measures the per-pixel line count = the register width.
3. **Per-pixel MW (invention 4, independent circuits):** either fast
   ADF4351 hopping for frequency-multiplexed per-pixel gates, or 64 small
   per-pixel loop antennas. Then a `RUN_PARALLEL` verb that loads 64
   sequence descriptors.

None of these PCB additions exist today; the simulator carries the physics
until they do.