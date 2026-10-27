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
**design-N / addressable-N upper bound**: `resolvable = floor(gradient ·
pixel_size / linewidth)` (Case D threshold; separation > linewidth else the
lines collapse). This is the count of *resolvable* ODMR lines — an upper bound
on the addressable register, not the literal NV count. `decompose_odmr`
resolves the intrinsic ¹⁴N hyperfine doublet even at zero gradient (→ 2
lines), so the line count is best read as "addressable lines", not "measured
NVs"; the per-pixel NV count varies with detector brightness (Invention 1
proxy), see `qubits_per_pixel` in the calibration response.

- **SIM ✅** — `qssc/simulator.py` `resolvable_register_width(...)` +
  `auto_align(gradient_mhz_per_um=, pixel_size_um=,
  register_line_freqs_hz=)` caps `n` to the resolvable count and, when
  measured line centres are supplied, seats the register on the real ODMR
  frequencies instead of the synthetic 6 MHz grid; `qssc/backend.py`
  `__init__(gradient_mhz_per_um=, pixel_size_um=, register_line_freqs_hz=)`.
- **Tests** — `tests/test_field_gradient_register.py` (8 tests incl.
  measured-line override of width + grid, per-pixel qubit array, and the
  no-gradient predicted-1 / array-consistency case).
  - **HW-fits (qssc-grad variant)** ✅ — the **gradient coil + DAC exist**
    on the `qssc-grad` extended variant, which is **local work in progress and
    not published in this repository** (a 1.5 mm PCB copper trace coil +
  MCP4921 12-bit SPI DAC + LM358 servo + IRLML2502 MOSFET + Rsense,
  fully routed, DRC 0/0/0; `tools/gen_qssc_kicad.py build_gradient_board()`).
  The `SET_GRADIENT <code>` RTL verb (`fpga/rtl/command_parser.v` CMD_GRADIENT
  → `dac_ctrl` SPI master) programs the coil current; `ODMR_SWEEP` then
  counts the resolved lines = the register width at that gradient. **RESET
  clears `grad_dac_value` to 0** (G1 fix): the load pulse is deferred to the
  `rst_hold==0` cycle so `dac_ctrl` (synchronously reset by `peri_rst`)
  actually latches the zero — verified by iverilog testbench scenario 20.
  The base `qssc.*` board has no gradient coil; the variant is opt-in.
- **needs-HW** — none on the `qssc-grad` variant (coil + DAC + verb all
  present). The base board remains without a coil.
  counts the resolved lines = the register width at that gradient. This
  is the single biggest 300 K qubit-count lever (0.5 → 2.0 MHz/µm
  lifts a pixel from ~19 to ~55 measured qubits; see Invention 13/14
  for pushing past the noise-bound ~70 toward the 300-NV cap).
- **Commit** — `8428f96` (sim); gradient-variant routing + `SET_GRADIENT`
  RTL + G1 RESET fix on `pcb/gradient-variant-fix`.
---

## 6. Sub-pixel detector resolution via charge-sharing centroiding

The QSSC detector is a **continuous graphene/MoS₂+PEDOT sheet on ITO**
(`DET_COMMON`) read by 64 discrete pads on a 2.5 mm pitch (8×8 grid, 1.0×1.0 mm
active pad area). A fluorescence spot landing on the sheet splits its
photocurrent across the neighbouring pads by the sheet's lateral charge-spreading
length; measuring the per-pad ratio localizes the spot to ~`pitch/(2·SNR)` ≈
15 µm — the resistive-charge-sharing / centroiding principle (wedge-and-strip,
delay-line anodes). This is **detector spatial imaging resolution**: the 64-pad
grid resolves `64 · S` positions, where `S = floor(SNR/threshold) = 8` at the
default readout SNR (`BRIGHT_CODE/READOUT_NOISE = 80`), growing as
`floor(80·√N/10)` with N-shot integration.

**Important limitation (physics):** centroiding *localizes one spot*; it does
**NOT** increase the per-pixel qubit register. The 64 pads are on 2.5 mm pitch
but each pixel's NV active region is only ~200 µm wide, so two microdiamonds
inside one pixel are ≤200 µm apart — far under the ~one-pad-pitch separation
needed for their charge clouds to hit *different* pads. They merge into one blob.
So `register_width` is unchanged by centroiding (it is set by
`min(freq_bins, nv_present, SNR-detectable lines)` — see Invention 7 for the
qubit gain). Centroiding improves **where** a signal can be localized
(magnetometry, calibration, sensor imaging), not **how many** qubits a pixel has.

- **SIM ✅** — `qssc/detector_centroid.py` (`SubPixelModel`,
  `charge_share_weights`, `centroid_estimate`, `localize_spots`,
  `sub_pixel_factor_from_snr`); `calibrate_array(sub_pixel=)` /
  `calibrate_array_from_codes(sub_pixel=)` report `spatial_channels = 64·S` and
  per-pixel `sub_pixel_position`; `SimulatedFPGA(sub_pixel=)` spreads each
  pixel's dip to its neighbours via the charge-share kernel.
- **Tests** — `tests/test_detector_centroid.py` (9), `tests/test_array_calibration.py`
  sub-pixel additions, `tests/test_fpga_sim.py` sub-pixel addition.
- **HW-fits** ✅ — uses the **existing `RUN_ALL_ODMR` verb** (Invention 4+5); the
  64-pad-per-point snapshot is centroided **host-side**. **No new RTL, no new
  pads, no new ADC channels.** Real path in `calibrate_array_from_codes`.
- **needs-HW** ❗ — engineer the graphene/MoS₂ sheet's **lateral resistance**
  (`sigma_sheet_mm`, tunable, same 64 electrodes) so a spot spills to ≥2 pads
  (`sigma` ~ pad_pitch/2). Characterize `sigma_sheet` per die. **No PCB respin**,
  no new electrodes — the sheet resistance is the one engineering knob.
- **Grounding** — `pad_pitch=2.5 mm` + 1.0×1.0 mm active area (fab docs +
  `qssc/magnetometry.py:269`); `SNR=80` (`BRIGHT_CODE/READOUT_NOISE`,
  datasheet-derived); `sigma_sheet_mm` and `threshold=10` are tunable model
  inputs / assumptions, NOT datasheet constants.

---

## 7. N-shot lock-in integration (per-pixel qubit gain)

The per-pixel register width is the number of ODMR lines that clear the
`min_snr=3` detection bar. A lone-NV bin has contrast 0.03; at the real readout
noise (`READOUT_NOISE/BRIGHT_CODE ≈ 0.0125`) its single-shot SNR is
`0.03/0.0125 ≈ 2.4 < 3` → **undetected**. Averaging N frequency-sweep shots
raises the per-line SNR by `√N` (classical, uncorrelated thermal/Johnson readout
noise), so dimmer lone-NV bins clear the bar and the **MEASURED** width rises
toward `min(resolvable, nv_present)`.

Measured (sim, grounded noise, 64 pixels):

| gradient | resolvable | 1 shot (max/avg) | 16 shots | 256 shots |
|---|---|---|---|---|
| g=0.5 | 20 | 10 / 6.8 | 12 / 7.8 | 12 / 7.8 |
| g=1.0 | 40 | 15 / 10.6 | 16 / 11.9 | 16 / 11.7 |
| g=2.0 | 80 | 20 / 14.4 | 25 / 18.4 | 26 / 19.0 |
| g=3.0 | 120 | 20 / 12.5 | 23 / 15.4 | 23 / 15.6 |

**Best case ~26 qubits/pixel (max), ~19 avg, at g=2.0 with integration** — up
from ~20/14 at 1 shot (**+30% per pixel, +32% total working qubits**). Saturates
around 16–256 shots (once all occupied bins are detected, the cap is bin
occupancy + `nv_present`, not SNR). The gradient (g=2.0) remains the dominant
lever; integration adds ~30% on top. Shor bits ≈ (w−1)//3 → +1–2 bits.

- **SIM ✅** — `calibrate_array(integration_shots=N)` / `_pixel_odmr_spectrum`
  scales noise as `1/√N`; `SimulatedFPGA(integration_shots=N)` scales the
  `RUN_ALL_ODMR` readout noise by `1/√N`.
- **Tests** — `tests/test_array_calibration.py` integration additions,
  `tests/test_fpga_sim.py` integration addition, `tests/test_parallel_computing.py`
  endpoint test.
- **HW-fits** ✅ — **zero hardware change**. The host runs the existing
  `RUN_ALL_ODMR` sweep N times and averages (or the firmware accumulates N
  lock-in samples per point). Cost is **measurement time ×N**, not silicon.
- **needs-HW** — none (host-side averaging protocol on the existing verb).
- **Grounding** — `READOUT_NOISE/BRIGHT_CODE` (datasheet readout constants);
  √N scaling is classical for uncorrelated thermal/Johnson noise.

---

## 8. Magnon inter-pixel entanglement bus (YIG) — CRYO

A shared YIG (yttrium iron garnet) magnon mode mediates **cross-pixel** 2-qubit
entanglement: NV spins in different pixels couple to the same magnon mode,
turning the 64 independent ~10-NV pixels into ONE entangled register. This is
the only route past the per-pixel qubit ceiling at room temperature — and it
does NOT work at 300 K.

- **Why not 300 K:** the magnon thermal occupation `n_th = 1/(exp(hf/kT)−1) ≈
  2 200` at 2.87 GHz / 300 K. `n_th` occupies the *same* mode as the signal, so
  it is inseparable from it; the second law forbids passively filtering it out
  (a passive filter would have to be colder than the bath, i.e. a refrigerator).
  Only cooling changes `n_th`: at ~150 mK, `n_th → 0` and the bus becomes
  usable. This is **temperature-bound, not material-bound** — no superconducting
  magnetic material avoids it (all candidates `T_c < 300 K`).
- **SIM** — not modelled (cryo invention, out of scope for the 300 K stack).
- **needs-cryo** ❗ — a dilution refrigerator (~150 mK) + the YIG resonator +
  couplers. No PCB respin helps; the whole chip must be cold.
- **Status** — documented only; not built. Do NOT pair with Invention 9 (the
  anti-noise stack is classical and 300 K; it does NOT touch `n_th`).

---

## 9. Classical anti-noise filter stack (300 K, SNR-protective)

The buildable, room-temperature version of "treat temperature/pressure as a
signal and filter it out." Models the **separable classical** environmental
noise channels (temperature drift, vibration ~30 Hz, EMI ~1 kHz, pressure) and
the filters that attenuate each (thermal servo, vibration isolation, EMI shield,
sensor feed-forward, common-mode rejection, dynamical decoupling). Stacks on
the Invention 7 `1/√N` floor; lower effective noise → more dim lone-NV ODMR
lines clear `min_snr` → the MEASURED per-pixel width rises (~55 → ~70 at
gradient 2.0 MHz/µm with N=16 + the full stack).

- **SIM ✅** — `qssc/noise_filters.py` (`NoiseEnvironment` lab/quiet/noisy
  presets, `AntiNoiseConfig` all-OFF default = back-compat, `effective_noise_sigma`
  composes on the 1/√N floor — fast channels vib/EMI average down by 1/√N, slow
  channels temp/pressure do NOT because correlated, so servo + feed-forward are
  the levers; `effective_t2` DD hahn ~2× / cpmg ~2·n / xy8 ~2ⁿ hard-capped at
  2·T₁; `filter_gain_breakdown`). Wired into `simulator.py`, `array_calibration.py`,
  `backend.py` (`QSSCBackend.anti_noise` applies DD to `self._t2` → deeper
  circuits survive), `api/main.py`+`api/runner.py` (4 request models +
  calibrate-array response), frontend `AntiNoisePanel`+`NoiseBudget`.
- **Tests** — `tests/test_noise_filters.py` (28).
- **HW-fits** ✅ — all filters are host-side or passive enclosure/servo (no new
  RTL, no PCB respin). DD is a pulse-sequence choice on the existing
  `RUN_SEQUENCE` verb.
- **needs-HW** — none.
- **Grounding** — channel sigmas (`temp_drift_counts` etc.) are MODEL INPUTS
  (enclosure characterization), NOT datasheet constants. DD T₁ cap uses
  `coherence_at_temperature` T₁ from `defects.py`. Does NOT touch the magnon
  `n_th` (that is Invention 8, cryo).
- **Commit** — `236d155` (branch `features/anti-noise`, NOT pushed).

---

## 10. ¹³C nuclear cluster register — CANDIDATE (needs-HW)

Beyond Invention 3's single ¹³C, use **N weakly-coupled ¹³C** nuclear spins per
pixel as a multi-qubit memory sub-register. ¹³C T₂ ~ 1 s (NOT QUOTED BY THE
SUPPLIER; Reimer/Taminiau/Cramer literature anchor); RF-addressed at each ¹³C's
hyperfine shift. Adds N memory qubits per pixel **without more NVs or optical
addressing** — the nuclear spins are dark, addressed by RF, read out through the
NV electron spin.

- **SIM** — to do (extends `qssc/nuclear.py`).
- **needs-HW** ❗ — the same RF channel as Invention 3 (DDS ~2 MHz + RF coil),
  broadband enough to address several ¹³C lines. Stacks on Inv 3's PCB addition.

---

## 11. NV nitrogen nuclear-spin (¹⁴N/¹⁵N) register — CANDIDATE (needs-HW)

The NV's own nitrogen nuclear spin is a **free extra qubit per NV** (¹⁴N I=1 =
qutrit; ¹⁵N I=½ = qubit), RF-addressed at a few MHz, long T₂. Distinct from ¹³C
(Inv 3/10): it is on-site, always present, no isotope engineering beyond ¹⁵N
enrichment. One extra memory qubit per NV.

- **SIM** — to do (extends `qssc/nuclear.py`).
- **needs-HW** ❗ — RF channel at the N hyperfine splitting (~2.2 MHz for ¹⁴N),
  shares Inv 3's RF DAC + coil.

---

## 12. Single-shot resonant spin-to-charge-conversion readout — CANDIDATE (needs-HW)

Instead of fluorescence contrast (SNR-limited by photon shot noise), use
resonant spin-to-charge conversion: a resonant laser flips the NV into a
long-lived charge state conditional on the spin, read out by a subsequent probe.
Single-shot readout fidelity → 90%+; the effective readout noise drops sharply,
so far more lone-NV lines clear `min_snr` → the measured width rises toward the
resolvable cap (stacks with Inv 7/9/13/14).

- **SIM** — to do (a readout-fidelity model in `simulator.py`).
- **needs-HW** ❗ — an extra resonant laser line + switching optics. PCB respin
  (optical path). The biggest resolution lever but the most invasive.

---

## 13. Compressed-sensing / sparse super-resolution ODMR — SIM ✅

The ODMR spectrum is **sparse**: at a wide gradient most gradient bins are empty
(a few NVs spread across many bins), so the line vector is k-sparse with
`k ≪ n_bins`. A hard SNR threshold misses dim lone-NV lines, but a sparse-
recovery estimator (orthogonal matching pursuit) over the known gradient-bin
Gaussian dictionary recovers them: joint least-squares over the selected support
**deconvolves overlapping lines** (two NVs in adjacent bins smear into one broad
dip and are resolved as two), and the sparsity prior lowers the per-atom
acceptance bar from `min_snr` (~4) to `superres_factor · min_snr` (~2.4) at a
controlled false-positive rate. Host-side, no HW.

- **SIM** ✅ — `qssc/superres_odmr.py`: `superres_decompose` (OMP),
  `superres_measured_width`. Verified: adjacent-bin overlaps deconvolve to two
  lines; the lowered bar accepts at least as many lines as the hard path.
- **HW-fits** ✅ — runs host-side on the existing `RUN_ALL_ODMR` matrix; no new
  RTL, no PCB respin. Stacks with Inv 7 (averaging) and Inv 9 (anti-noise).

---

## 14. Bayesian / quantum-limited readout — SIM ✅

Replace the hard two-sided `min_snr=4` bar with a **one-sided Bayesian
credible-interval detector** over the same gradient-bin dictionary. For each bin
the matched-filter amplitude estimate and its posterior std
(`σ_j = noise_sigma / ‖A_j‖`, the noise averaged over the line profile) give a
one-sided test `â_j / σ_j > z(confidence)`; a Bonferroni correction over
`n_bins` holds the family-wise false-positive rate at `1 − confidence`. The
matched filter + one-sided test accept marginal lone-NV lines the hard bar
misses (z₀.₉₉ ≈ 2.33 vs min_snr = 4) — the quantum-limited readout. Stacks with
Inv 7/9; complementary to Inv 13 (independent per-bin vs joint OMP).

- **SIM** ✅ — `qssc/bayesian_readout.py`: `bayesian_decompose`,
  `bayesian_measured_width`. Verified: recovers ~2× the hard-threshold width on
  the same spectrum (24–28 vs 9–12 lines); higher confidence → fewer lines
  (monotone).
- **HW-fits** ✅ — host-side statistical estimator on the existing readout; no
  new RTL, no PCB respin.

---

## 15. Parallel VQE / QAOA (classical-quantum loop) — CANDIDATE (host-side)

Run a small variational ansatz on each of the 64 pixels in parallel, aggregate
the expectation values host-side, and close a classical optimizer loop
(parameter update → re-run). Application: chemistry ground-state energy,
combinatorial optimisation. Builds on Invention 4's 64-parallel runner.

- **SIM** — to do (an orchestration layer on `run_parallel`).
- **HW-fits** ✅ — host-side classical-quantum loop on the existing parallel
  runner; no new hardware.

---

## 16. Quantum kernel estimation (QSVM) — CANDIDATE (host-side)

Compute quantum kernel entries `K(x_i, x_j) = |<ψ(x_i)|ψ(x_j)>|` on the 64-pixel
array in parallel (each pixel evaluates one kernel entry), then train a
classical SVM on the kernel matrix. Application: quantum machine learning.

- **SIM** — to do (kernel-evaluation circuits + aggregation).
- **HW-fits** ✅ — host-side; builds on Invention 4.

---

## 17. Distributed / Heisenberg-limited sensing — CANDIDATE (needs-cryo)

Use cross-pixel entanglement (Invention 8 magnon bus) to beat the standard
quantum limit for the 64-pixel sensor array: independent pixels give 1/√N
(√64 = 8× SQL); an entangled GHZ-like state across pixels gives 1/N (Heisenberg,
64×). Application: magnetometry beyond SQL.

- **SIM** — to do (needs the Inv 8 cryo entanglement model).
- **needs-cryo** ❗ — requires Invention 8 (the magnon bus at ~150 mK). At 300 K
  the 64 pixels are independent → standard 1/√N only.

---

## 18. Small QEC code on the register — CANDIDATE (needs-HW + host-side)

Run a small quantum error-correcting code (repetition code, or [[7,1,3]] Steane)
on a pixel's ~10-NV register, using the ¹³C nuclear memory (Inv 3) as the
ancilla/data store. Application: fault-tolerance demonstration. 300 K T₂ and
gate fidelity are marginal, but the nuclear memory's long T₂ helps the slow
syndrome-extraction cycles.

- **SIM** — to do (QEC circuits + syndrome model).
- **needs-HW** ❗ — needs Inv 3's RF channel for nuclear-memory ancilla.

---

## 19. Tensor-gradient magnetic imaging — SIM ✅

Extend Invention 1 (4-axis NV⟨111⟩ vector magnetometer) with the on-PCB gradient
coil (Invention 5) to recover the **B-gradient tensor** (the 3×2 in-plane
Jacobian ``G_ij = ∂B_i/∂x_j`` of the field across the 8×8 array, by finite
differences at the 2.5 mm pitch), not just |B| at each pixel. The tensor
signature localizes and classifies a magnetic source: a point dipole has a
characteristic tensor pattern, so a **dipole localizer** (nonlinear least-squares
fit of the analytic dipole field) recovers the source position and moment — a
richer magnetic image (anomaly / current-source imaging) than the scalar map.

- **SIM** ✅ — `qssc/tensor_gradiometry.py`: `tensor_gradient_map` (per-pixel
  Jacobian), `localize_dipole` (fit to the reconstructed B-vectors),
  `localize_dipole_from_splits` (robust fit to the **raw doublet splits** — the
  sign-ambiguity-free observables — in differential mode, where the DC
  background fixes the split polarity), `tensor_gradient_sweep` (64-pixel image
  + dipole reconstruction). On a noise-free field the localizer recovers the
  source exactly; with shot noise it recovers the position to ~0.4 mm and the
  moment to ~7 % (limited by the 2.5 mm pitch + the 4-axis linewidth floor, not
  the fitter). The per-pixel linewidth floor (~45 µT) sets the minimum resolvable
  source strength — a weaker dipole images as zero, which is the physics, not a
  bug. Stacks with Inv 7 (1/√N averaging lowers the split noise).
- **HW-fits** ✅ — uses the existing 4-axis ODMR readout (Inv 1) + the on-board
  gradient coil (Inv 5, merged PR #79). No new hardware.
- **needs-HW** — none (Inv 5 coil is already on the PCB).

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
- `qssc/detector_centroid.py` — charge-sharing centroiding on the continuous
  graphene/MoS₂ sheet (Invention 6): `SubPixelModel`, `charge_share_weights`,
  `centroid_estimate`, `localize_spots`. Sub-pixel detector imaging; does NOT
  raise the per-pixel qubit count (within-pad spots merge). SIM ✅.

---

## Summary table

| # | Invention | SIM | HW-fits (RTL to do) | needs-HW (PCB add) | needs-cryo |
|---|---|---|---|---|---|
| — | **Pixel-mode selector** (ensemble ↔ register, measured-N) | ✅ | **YES** — `pixel_mode` panel switch + `prime_readout` toggle; both run on existing firmware | — |
| 1 | 64-pixel vector magnetometer (4-axis NV⟨111⟩) | ✅ | ODMR_SWEEP → B (existing verb) | — | — |
| 2+ | Prime-coprime readout + denoiser | ✅ | prime-period `READ_LOCKIN` mode | — | — |
| 3 | Nuclear-spin memory (NV+¹³C) | ✅ | — | RF DAC ~2 MHz + RF coil + RTL kind 3 | — |
| 4 | 64-parallel distributed computing | ✅ | `RUN_ALL` 64-detector sweep | per-pixel MW for independent circuits | — |
| 5 | Gradient register width | ✅ | ✅ coil+DAC ON PCB (PR #79 `d422a2b`) | — (done) | — |
| 6 | Sub-pixel detector (charge-sharing centroiding) | ✅ | host-side centroid on `RUN_ALL` | engineer sheet resistance (no new pads/ADC) | — |
| 7 | N-shot lock-in integration | ✅ | host-side averaging on `RUN_ALL` | — | — |
| 8 | Magnon inter-pixel bus (YIG) | — | — | — | ❗ ~150 mK + YIG |
| 9 | Classical anti-noise filter stack | ✅ | host-side/passive + DD on `RUN_SEQUENCE` | — | — |
| 10 | ¹³C nuclear cluster register | to do | — | RF channel (shares Inv 3) | — |
| 11 | NV nitrogen nuclear-spin (¹⁴N/¹⁵N) register | to do | — | RF channel (shares Inv 3) | — |
| 12 | Resonant spin-to-charge readout | to do | — | extra laser line + optics (respin) | — |
| 13 | Compressed-sensing super-res ODMR | building | host-side on `RUN_ALL` | — | — |
| 14 | Bayesian quantum-limited readout | building | host-side on existing readout | — | — |
| 15 | Parallel VQE / QAOA | to do | host-side on `run_parallel` | — | — |
| 16 | Quantum kernel estimation (QSVM) | to do | host-side on `run_parallel` | — | — |
| 17 | Distributed / Heisenberg-limited sensing | to do | — | — | ❗ needs Inv 8 |
| 18 | Small QEC code on the register | to do | — | RF channel (shares Inv 3) | — |
| 19 | Tensor-gradient magnetic imaging | building | host-side + Inv 5 coil (on PCB) | — | — |

The **pixel-mode selector** is the panel "multiqbit mode" switch (invention
delivery vehicle): `register` (default, one pixel's N-NV register with
within-pixel all-to-all dipole 2q) ↔ `ensemble` (64 chip pixels are the circuit
qubits, one ensemble qubit per pixel, no within-pixel 2q at 300 K). The register
width **N is a measured quantity** — `decompose_odmr` on the ODMR calibration
(`ODMR_SWEEP` verb) counts the resolvable lines, and the backend places NVs on
the measured frequency grid (`register_line_freqs_hz`), not a hardcoded 10. A
bulk microdiamond collapses to one line → ensemble mode is recommended; a
resolved multi-line ODMR → register mode. `qubit = address not NV count`. The
field gradient that spreads the lines (Invention 5) is **not** a panel mode —
  it is a hardware add-on built on the opt-in `qssc-grad` board variant (coil +
  DAC + `SET_GRADIENT` verb), not the base board — the variant's board files are
  local-only and not published in this repository.


## What to touch on the PCB for the needs-HW inventions

1. **RF channel for ¹³C SWAP (invention 3, also 10/11/18):** add a DDS/synthesizer at
   ~2 MHz + a small RF coil near the diamond die + an FPGA SPI/DAC port
   (mirror the ADF4351 control path). Then add `RUN_SEQUENCE` kind 3
   (MW+RF SWAP timing) in `fpga/rtl/command_parser.v`. The same RF channel
   addresses the ¹³C cluster (Inv 10), the nitrogen nuclear spin (Inv 11),
   and the QEC ancilla (Inv 18).
2. **Gradient coil (invention 5):** ✅ **DONE** — on the PCB (`qssc-grad`,
   merged PR #79 `d422a2b`). `dac_ctrl.v` + the `SET_GRADIENT` verb drive the
   coil; `ODMR_SWEEP` measures the per-pixel line count = the register width.
   Also enables Invention 19 (tensor-gradient imaging).
3. **Per-pixel MW (invention 4, independent circuits):** either fast
   ADF4351 hopping for frequency-multiplexed per-pixel gates, or 64 small
   per-pixel loop antennas. Then a `RUN_PARALLEL` verb that loads 64
   sequence descriptors.
4. **Graphene/MoS₂ sheet resistance (invention 6, sub-pixel centroiding):**
   NO new pads, NO new ADC, NO new RTL — centroiding runs host-side on the
   existing `RUN_ALL_ODMR` matrix. The one engineering knob is the detector
   sheet's lateral charge-spreading length (`sigma_sheet` ~ pad_pitch/2): tune
   the graphene/MoS₂+PEDOT sheet resistance so a fluorescence spot spills to
   ≥2 neighbouring pads, and characterize `sigma_sheet` per die.
5. **Resonant spin-to-charge readout (invention 12):** an extra resonant laser
   line + switching optics — the most invasive addition (optical path respin),
   and the biggest single-shot readout-fidelity lever.

Of the three, only inventions 3 (RF ¹³C channel, #75) and 4's independent-circuit
per-pixel MW (#77) still need a PCB addition; the gradient coil (5) is built on
the `qssc-grad` variant (#76 resolved). The simulator carries the physics for
the remaining two. The field gradient is **not** a `pixel_mode` panel switch —
it is a hardware add-on on the opt-in variant board, not the base board.
Inventions 7, 9, 13, 14, 15, 16, 19 need **no PCB respin** — they are host-side
or use hardware already on the board (the Inv 5 gradient coil, the existing
`RUN_ALL_ODMR` / `RUN_SEQUENCE` verbs). Inventions 8 and 17 are cryo-only.
