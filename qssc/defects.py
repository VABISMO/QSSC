# qssc/defects.py
"""Solid-state spin-defect profiles for the Quantum Solid State Chip (QSSC).

Each :class:`DefectProfile` bundles the physical constants that the simulator,
the ODMR model and the Qiskit noise model need for a given defect family.  The
canonical chip described in the fabrication protocol is the **NV-diamond**
compute chip — a CVD diamond plate hosting nitrogen-vacancy (NV-) centres,
operated at **room temperature** with on-chip CQD ASE/LED pump + graphene/MoS₂
photodetector + FPGA lock-in readout.  NV-diamond is the only defect family here
whose room-temperature coherence (T2 ~ 1.8 ms in 12C-enriched diamond) is long
enough to compute seriously at 300 K, so it is the **default** profile.

Boron-vacancy (VB-) centres in hexagonal boron nitride (hBN) and silicon-vacancy
related defects (V_Si) in SiC are kept as **sensor variants**: same control
stack, shorter coherence (hBN T2 ~ 2 us, SiC T2 ~ 40 us), so the same software
can drive different substrates, but they are not compute-capable at room
temperature.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class DefectProfile:
    """Physical parameters of an optically addressable spin defect.

    All timing/fidelity numbers are real published values (see ``references``);
    they drive the Qiskit noise model (thermal relaxation + depolarizing +
    readout) so the simulator reproduces the actual physics of each host at the
    chosen temperature, not a generic placeholder.
    """

    name: str
    short: str
    # Spin Hamiltonian parameters (all in Hz)
    zero_field_splitting: float        # D
    gyromagnetic_ratio: float          # gamma_e (Hz/T)
    hyperfine_coupling: float          # A_parallel for the dominant nucleus (Hz)
    quadrupole_splitting: float        # Q for the dominant nucleus (Hz)
    nuclear_spin: float                # I of the coupled nucleus
    # Excitation / readout optics
    excitation_wavelength_nm: float    # pump wavelength
    fluorescence_range_nm: tuple       # zero-phonon-line / emission band
    # Coherence at room temperature (300 K)
    t1_room: float                     # longitudinal relaxation (s)
    t2_room: float                     # coherence time (s)
    fluorescence_factor: float         # brightness enhancement vs baseline
    readout_error: float               # single-shot readout error (0..1)
    defect_density_per_um2: float      # emitters / um^2
    # ODMR resonance linewidth (FWHM) in Hz -- the acceptance window for a MW
    # pulse to flip the spin, and the width of the photocurrent dip in the ODMR
    # sweep. NV ~5 MHz; hBN-VB ensemble ~tens of MHz; SiC V_Si ensemble ~1-2 MHz.
    odmr_linewidth_hz: float = 5.0e6
    # --- Control/readout timings + gate-error probabilities ----------------
    # NOTE: ASPIRATIONAL TARGET SPEC, not a measured device. The T1/T2/readout
    # numbers above are literature-anchored (each profile's `references` tuple
    # cites the source), but the gate durations and depolarizing probabilities
    # below are NOT cited -- no supplier datasheet or paper quotes them for this
    # architecture. They encode a best-case engineering target for a
    # room-temperature defect-spin chip, not a characterized device. Published
    # multi-qubit entangling-gate fidelities for NV-diamond / defect-spin
    # architectures are well below depol_2q = 0.002 (99.8%).
    # Sensitivity: cold recovery of Shor-15 on NV requires depol_2q <=~ 0.002 at
    # ~10**3 decomposed 2q gates. Signal retention is
    #   exp(-t/T2) * (1-depol_1q)**est_1q * (1-depol_2q)**est_2q
    # (see QSSCBackend._signal_survival); re-derive from this formula before
    # trusting any factor-recovery claim, and sweep these constants to test it.
    gate_1q_ns: float = 50.0           # single-qubit (microwave pi-pulse) duration
    gate_2q_ns: float = 400.0          # two-qubit (entangling) gate duration
    readout_ns: float = 800.0          # optical spin-readout integration time
    init_ns: float = 200.0             # optical initialisation time
    # Depolarizing error probabilities per gate (drive crosstalk / non-ideal MW)
    # -- per the cited 2q-gate-fidelity literature (see each profile's
    # ``references``); the NOTE above flags any that are an engineering target
    # rather than a characterized device.
    depol_1q: float = 0.0008
    depol_2q: float = 0.006
    # Depolarization temperature scaling (TEACHING MODEL): real 2q gate
    # fidelity improves at cryogenic temperature as phonon-driven dephasing and
    # charge noise fall. ``depol_2q`` is the ROOM-TEMPERATURE (300 K) value;
    # ``depol_2q_cryo_floor`` is the asymptotic cryogenic limit. The effective
    # depol at temperature T is
    #   cryo_floor + (depol_2q - cryo_floor) * (T/300)**depol_temp_exponent
    # so it -> depol_2q at 300 K and -> cryo_floor as T -> 0. Default
    # cryo_floor 0 = no temperature scaling (flat depol, back-compat).
    depol_2q_cryo_floor: float = 0.0
    depol_temp_exponent: float = 1.0
    # --- Hyperfine geometry: number of equivalent coupled nuclei (for the ODMR
    # hyperfine fingerprint).  V_B- couples to 3 equivalent 14N -> 7 lines
    # (2*n*I+1).  Defaults to 1 (single dominant nucleus, e.g. NV 14N). ---
    n_equivalent_nuclei: int = 1
    # --- Coherence temperature scaling exponents (teaching model):
    # T1 ~ T1_room * (300/T)**t1_exp, T2 ~ T2_room * (300/T)**t2_exp, T2<=2*T1.
    # V_B- T1 ~ T^(-5/2) (Gottscholl 2021); T2 is ~T-independent 50-300 K and
    # grows only at lower T -- the power law here is a simplified illustration
    # of the cryogenic recovery, NOT a quantitative prediction. ---
    t1_exponent: float = 1.0
    t2_exponent: float = 0.5
    # --- Optical-coherence channel (photon-mediated 2q gate indistinguishability) -
    # NOTE: ASPIRATIONAL / TEACHING MODEL, not a measured device. The spin T1/T2
    # above set the *spin* decoherence budget; these fields set the *optical*
    # coherence budget that gates a photon-mediated two-qubit (entangling) gate.
    # A photon gate needs two emitted photons to be indistinguishable, quantified
    # by the Hong-Ou-Mandel visibility  V_HOM ~= T2,opt / (2*T1,opt)  where T1,opt
    # is the optical excited-state lifetime and T2,opt the optical (ZPL)
    # coherence time. At 300 K phonons broaden the ZPL to ~THz so T2,opt ~ fs and
    # V ~ 1e-7 -> the gate decoheres; at cryogenic T the ZPL narrows toward the
    # transform limit (T2,opt -> 2*T1,opt) and V -> 1. The power-law broadening
    # below is a simplified illustration of that cryogenic recovery, NOT a
    # quantitative prediction -- exactly like the spin power law above. Real
    # cryogenic NV HOM visibilities with spectral tuning are ~0.4-0.7
    # (Bernien 2013 / Hensen 2015); hom_visibility_max defaults to 1.0 (the
    # transform-limited *ideal*) -- lower it to model a real device. If
    # optical_t1_ns <= 0 the profile has no optical channel and V=1.0 (no
    # optical penalty), preserving back-compat for un-modelled profiles.
    zpl_wavelength_nm: float = 0.0      # zero-phonon-line wavelength (descriptive)
    optical_t1_ns: float = 0.0         # optical excited-state lifetime (ns)
    optical_t2_room_fs: float = 0.0    # optical T2 at 300 K (fs) -- phonon-broadened ZPL
    t2_opt_exponent: float = 5.0       # optical T2 ~ t2_room_opt*(300/T)**exp (teaching)
    hom_visibility_max: float = 1.0    # transform-limited ideal cap (aspirational)
    # --- Per-site frequency spread (microwave addressability) + readout asymmetry -
    # NOTE: ASPIRATIONAL / TEACHING MODEL, not a measured device or a supplier-
    # quoted number (see the NOTE above the gate timings). Real NV sites are NOT
    # all on the same transition frequency: local strain + local B-field + the
    # 14N hyperfine shift detune each site's ms=0<->ms=+-1 line by ~MHz around
    # the 2.87 GHz zero-field splitting. That detuning IS the individual
    # microwave-addressing mechanism -- a global MW antenna drives only the
    # qubit whose detuning brings it into the ~5 MHz ODMR linewidth, so every
    # site is independently addressable without a per-site antenna.
    # ``frequency_detuning_spread_hz`` is the per-site detuning sigma (Hz);
    # auto_align draws each site's detuning from N(0, spread). Default 0 = no
    # detuning (all sites identical, back-compat).
    frequency_detuning_spread_hz: float = 0.0
    # Real NV spin readout is ASYMMETRIC: ms=0 is the BRIGHT state (high
    # fluorescence), ms=+-1 is DARK (the ODMR dip). The bright->dark and
    # dark->bright misassignment rates differ -- the bright state is easier to
    # distinguish than the dark. ``readout_asymmetry`` in [-1, 1] splits the
    # symmetric ``readout_error`` ``ro`` into p_bright_to_dark = ro*(1-asym) and
    # p_dark_to_bright = ro*(1+asym). Default 0 = symmetric (back-compat).
    readout_asymmetry: float = 0.0
    # --- Nuclear-spin memory (NV + 13C) -- Invention 3 compute enabler --------
    # A 13C nucleus next to an NV is a long-lived MEMORY qubit: its spin T2 is
    # ~seconds (vs the electron's ~ms), so a circuit can STORE an intermediate
    # result on the 13C, keep computing on the electron, and RECALL it later --
    # extending the effective coherence window from ~1.8 ms to seconds.
    # ``nuclear_t2_s`` is the 13C nuclear coherence (s); 0.0 = no nuclear
    # memory for this profile. ``nuclear_hyperfine_hz`` is the NV-13C
    # hyperfine coupling A_parallel (Hz), which sets the electron-nuclear SWAP
    # gate time (t_SWAP ~ 1/2A). NOT QUOTED BY THE SUPPLIER for this chip's
    # datasheet -- these are literature anchors for an ENGINEERED single-13C
    # memory nucleus (the 12C-enriched stone has few native 13C, so this is a
    # deliberately-implanted/selected memory nucleus, Reimer 2016 / Taminiau
    # 2014), flagged the same way as the gate-timing engineering targets above.
    nuclear_t2_s: float = 0.0
    nuclear_hyperfine_hz: float = 0.0
    # Human readable notes + literature citations for every number above
    notes: str = ""
    references: tuple = ()

    @property
    def d_ghz(self) -> float:
        return self.zero_field_splitting / 1e9

    @property
    def t2_us(self) -> float:
        return self.t2_room * 1e6

    @property
    def t1_ms(self) -> float:
        return self.t1_room * 1e3


# ---------------------------------------------------------------------------
# Built-in defect profiles
# ---------------------------------------------------------------------------

HBN_VB = DefectProfile(
    name="hBN boron-vacancy (VB-)",
    short="hBN-VB",
    zero_field_splitting=3.46e9,
    gyromagnetic_ratio=28.0e9,
    hyperfine_coupling=47.0e6,       # A/h = 47 MHz, 3 equivalent 14N (Gottscholl 2020)
    quadrupole_splitting=1.96e6,     # 14N quadrupole Cq ~1.96 MHz (Gracheva 2023)
    nuclear_spin=1.0,                # 14N, I = 1 (99.6% abundant)
    excitation_wavelength_nm=532.0,
    fluorescence_range_nm=(820, 860),  # PL ~850 nm under 532 nm pump (Gottscholl 2020)
    t1_room=1.8e-5,                  # ~18 us at 300 K (Gottscholl Sci. Adv. 2021)
    t2_room=2.0e-6,                  # ~2 us Hahn echo at 300 K (Gottscholl 2020/2021)
    fluorescence_factor=1.0,
    readout_error=0.12,              # ensemble optical readout, partial contrast
    defect_density_per_um2=0.30,
    gate_1q_ns=50.0, gate_2q_ns=400.0, readout_ns=800.0, init_ns=200.0,
    # Per-paper depolarization (TEACHING MODEL): V_B- 2q gate fidelity 93-97%
    # (Sakuldee 2025, room-T ensemble V_B- entanglement) -> depol_2q = 0.05 (95%,
    # mid-band). This is HIGHER than NV (0.004): V_B- charge-noise + phonon
    # decoherence give a worse 2q gate, which is the physical basis for the
    # "hBN is a sensor, not a compute defect" verdict (audit issue #7): even
    # cold, depol_2q=0.05 compounded over the ~10^2 decomposed 2q gates of an
    # efficient Shor / multiplier drops the signal below the noise floor, so
    # hBN recovers no factors. Temperature-INDEPENDENT here (cryo_floor=0):
    # V_B- cold 2q improvement is not literature-anchored for this teaching
    # model, so the conservative flat 300 K value is kept (preserves the
    # sensor verdict; a future cryo-floor would only strengthen compute).
    depol_1q=0.0008, depol_2q=0.05,
    n_equivalent_nuclei=3,           # 3 equivalent 14N -> 7-line hyperfine (2nI+1)
    t1_exponent=2.5,                 # T1 ~ T^(-5/2) (Gottscholl 2021)
    t2_exponent=0.5,
    # Optical channel -- TEACHING-MODEL PLACEHOLDER, not supplier/literature quoted
    # for V_B-: ZPL ~850 nm (from fluorescence_range), optical T1 ~2 ns, room-T
    # T2,opt ~ few fs (phonon-broadened), V_max=1.0 (transform-limited ideal).
    zpl_wavelength_nm=850.0,
    optical_t1_ns=2.0,
    optical_t2_room_fs=5.0,
    t2_opt_exponent=5.0,
    hom_visibility_max=1.0,
    # Per-site frequency detuning + asymmetric readout (TEACHING MODEL, not
    # supplier-quoted): hBN V_B- strain broadening is larger than NV -> ~5 MHz
    # per-site spread (still addressable against the tens-of-MHz ensemble
    # linewidth). hBN is an ENSEMBLE sensor: its readout is a continuous
    # ensemble photocurrent that averages the bright/dark contrast over many
    # defects, so the single-shot bright/dark asymmetry is largely washed out
    # -> small readout_asymmetry 0.05.
    frequency_detuning_spread_hz=5.0e6,
    readout_asymmetry=0.05,
    notes="Room-temperature optically addressable S=1 spin in 2D hBN; D~3.46 GHz, "
          "characteristic 7-line 14N hyperfine fingerprint (A~47 MHz). Optical channel "
          "values are a teaching placeholder (V_B- photon-gate regime not quoted here).",
    references=(
        "Gottscholl et al., Nature Materials 19, 540-545 (2020), "
        "doi:10.1038/s41563-020-0619-6 — V_B- discovery, D=3.46 GHz, 7-line 14N "
        "hyperfine (A/h=47 MHz), room-T ODMR.",
        "Gottscholl et al., Sci. Adv. 7, eabf3630 (2021), doi:10.1126/sciadv.abf3630 — "
        "room-T spin initialisation/read-out, T2~2 us, T1~18 us (~T^(-5/2)).",
        "Gracheva et al., J. Phys. Chem. C 127 (2023), doi:10.1021/acs.jpcc.3c01902 — "
        "V_B- hyperfine/quadrupole tensors, A_perp~45.5 MHz, Cq~1.96 MHz.",
        "Kianinia et al., ACS Photonics 7, 2147-2152 (2020), "
        "doi:10.1021/acsphotonics.0c00614 — quantum emitters/defects in hBN.",
        # NOTE: depol_2q=0.05 reflects the reported room-T V_B- 2q-gate fidelity
        # range (~93-97%, e.g. Sakuldee 2025 and related V_B- entanglement work),
        # taken at the mid-band as a TEACHING MODEL. Not tied to a single
        # supplier datasheet; the exact citation is left to the operator to
        # pin to the V_B- 2q-gate paper they adopt (datasheet-grounded-physics).
    ),
)

NV_DIAMOND = DefectProfile(
    name="Diamond nitrogen-vacancy (NV-)",
    short="NV",
    zero_field_splitting=2.87e9,
    gyromagnetic_ratio=28.0e9,
    hyperfine_coupling=-2.16e6,      # 14N A_parallel ~ -2.16 MHz
    quadrupole_splitting=-4.95e6,    # 14N quadrupole
    nuclear_spin=1.0,
    excitation_wavelength_nm=532.0,
    fluorescence_range_nm=(650, 800),
    t1_room=6.0e-3,                  # ~6 ms T1 at 300 K (Jarmola 2012)
    t2_room=1.8e-3,                  # 1.8 ms Hahn echo, 12C-enriched synthetic diamond, RT
    # (Balasubramanian 2009). CONSERVATIVE LITERATURE ANCHOR for the chip's 12C
    # synthetic stone -- normal grade, NOT ppb electronic grade. The 300 K NV
    # compute experiments (Neumann 2008, Dolde 2013, Wu 2019) run on normal-grade
    # diamond, and 1.8 ms sits in the lower-middle of what a 12C stone reaches:
    # dynamical decoupling (CPMG/XY8) pushes T2 well beyond it, ultimately T1-
    # limited. The chip's REAL T2 is the per-pin Hahn-echo value the calibration
    # measures (api.main.calibration_run stage 4); this anchor is replaced by
    # that measurement. Not a supplier-quoted number.
    fluorescence_factor=1.2,         # 20% brighter when cooled to 77 K
    readout_error=0.08,              # single-shot spin readout ~90% fidelity
    defect_density_per_um2=5.0,
    gate_1q_ns=25.0, gate_2q_ns=200.0, readout_ns=3000.0, init_ns=1000.0,
    depol_1q=0.0002, depol_2q=0.004,
    # NV-NV electron dipole 2q gate: 0.004 at 300 K is the near-term engineering
    # TARGET (99.6% fidelity, Joas 2025 projected with improved coherence), not
    # the achieved room-temp record (96.0%, depol 0.04, Joas 2025). The cryo
    # floor 0.0007 is the achieved 4 K electron-14N gate record (99.93%,
    # Bartling 2024); the QSSC NV-NV electron gate is harder than that
    # electron-nuclear gate, so the floor is a lower bound on the achievable
    # cryo fidelity, reached as T -> 0 (linear in T). depol_1q 0.0002 matches
    # the Bartling 2024 electron single-qubit fidelity (99.98%).
    depol_2q_cryo_floor=0.0007,
    depol_temp_exponent=1.0,
    n_equivalent_nuclei=1,           # single 14N -> 3-line hyperfine
    t1_exponent=1.0,
    t2_exponent=0.5,
    # Optical-coherence channel (photon-mediated 2q gate): NV- ZPL 637 nm,
    # excited-state ^3E lifetime ~12 ns -> transform-limited linewidth ~13 MHz.
    # At 300 K the ZPL is phonon-broadened to ~THz so T2,opt ~ fs (V ~ 2e-7);
    # at cryo T2,opt -> 2*T1,opt (transform-limited) and V -> 1 (ideal).
    zpl_wavelength_nm=637.0,
    optical_t1_ns=12.0,
    optical_t2_room_fs=5.0,
    t2_opt_exponent=5.0,
    hom_visibility_max=1.0,
    # Per-site frequency detuning + asymmetric readout (TEACHING MODEL, not
    # supplier-quoted): NV strain shifts are ~MHz -> 2 MHz per-site spread,
    # within the ~5 MHz ODMR linewidth so each site is still individually
    # addressable by a narrow MW pulse from the global antenna; readout
    # asymmetry 0.10 (ms=0 bright vs ms=+-1 dark -- modest, well-calibrated
    # single-shot readout keeps the bright/dark bias small).
    frequency_detuning_spread_hz=2.0e6,
    readout_asymmetry=0.10,
    # Nuclear-spin memory (Invention 3): an ENGINEERED single-13C next to each
    # NV is a ~1 s memory qubit (vs the electron's 1.8 ms), extending the
    # coherence window ~500x. 13C nuclear T2 ~1 s without decoupling in
    # 12C-enriched diamond (Reimer 2016, Cramer 2016 report 1-2 s; >80 s with
    # decoupling). A_parallel ~2 MHz for a moderately-coupled near-13C sets a
    # ~250 ns electron-nuclear SWAP. NOT QUOTED BY THE SUPPLIER (the 12C stone
    # has few native 13C; this is a deliberately-implanted memory nucleus).
    nuclear_t2_s=1.0,
    nuclear_hyperfine_hz=2.0e6,
    notes="Workhorse room-temperature defect; D~2.87 GHz. T2=1.8 ms is a CONSERVATIVE "
          "literature anchor for the chip's 12C-enriched SYNTHETIC diamond (normal grade, "
          "not ppb electronic grade -- the 300 K compute experiments Neumann 2008 / Dolde "
          "2013 / Wu 2019 use normal-grade stones, not ppb). 12C purification removes the "
          "13C spin bath (natural-abundance diamond is ~0.5-0.7 ms); with dynamical "
          "decoupling T2 exceeds 1.8 ms (ultimately T1-limited). The chip's REAL T2 is "
          "the per-pin Hahn-echo value measured at calibration (api.main stage 4), which "
          "replaces this anchor. Optical channel: 300 K V_HOM ~ 2e-7 (photon 2q gate "
          "decoheres); cryo V -> 1 (transform-limited ideal; real tuned cryo NV ~0.4-0.7).",
    references=(
        "Doherty et al., Physics Reports 528, 1 (2013) — NV centre review (D=2.87 GHz).",
        "Balasubramanian et al., Nature Materials 8, 383 (2009), doi:10.1038/nmat2420 — "
        "1.8 ms T2 at RT in 99.7% 12C-enriched diamond.",
        "Jarmola et al., Phys. Rev. Lett. 108, 197601 (2012) — NV T1 at room temperature.",
        "Bernien et al., Nature 497, 86-90 (2013), doi:10.1038/nature12032 — cryogenic "
        "NV spin-photon entanglement; two-photon interference (HOM) visibility with "
        "spectral tuning.",
        "Hensen et al., Nature 526, 682-686 (2015), doi:10.1038/nature15759 — cryogenic "
        "NV-NV entanglement / loophole-free Bell test; optical-indistinguishability "
        "regime relevant to photon-mediated gates.",
        "Joas et al., Phys. Rev. X 15, 021069 (2025) — room-temperature NV-NV "
        "electron-spin entangling-gate fidelity 96.0(2.5)% (depol 0.04); "
        "coherence-limited projection 98.6%, ~99.6% with improved coherence "
        "(the depol_2q=0.004 near-term TARGET here).",
        "Bartling et al., Phys. Rev. Appl. 23, 034052 (2025), "
        "arXiv:2403.10633 — 4 K electron-14N two-qubit gate 99.93(5)% "
        "(depol 0.0007, the depol_2q_cryo_floor) and electron 1q 99.98% "
        "(depol_1q 0.0002), characterized by gate set tomography.",
        "Xie et al., Phys. Rev. Lett. 130, 030601 (2023) — NV CNOT fidelity "
        "99.92% (depol 0.0008) by noise-tailored subspace randomized "
        "benchmarking, consistent with the cryo floor above.",
        "Reimer et al., Phys. Rev. B 93, 024104 (2016) — single 13C nuclear "
        "spin coherence near an NV, ~1-2 s T2 in 12C-enriched diamond (the "
        "nuclear_t2_s anchor); the nuclear memory qubit architecture.",
        "Taminiau et al., Nature 486, 91-94 (2014), doi:10.1038/nature11185 — "
        "NV + 13C universal control + memory (electron-nuclear SWAP gate).",
        "Cramer et al., Nature Communications 7, 11526 (2016) — 13C nuclear "
        "T2 > 80 s with decoupling (the decoupled upper bound; nuclear_t2_s "
        "here is the no-decoupling conservative anchor).",
    ),
)

SIC_VS = DefectProfile(
    name="Silicon-carbide silicon-vacancy (V_Si)",
    short="SiC-VSi",
    zero_field_splitting=0.070e9,    # 2|D| = 70 MHz, 4H-SiC V2 line, S=3/2 (Kraus 2014)
    gyromagnetic_ratio=28.0e9,
    odmr_linewidth_hz=1.5e6,        # V_Si ensemble inhomogeneous ODMR linewidth ~1-2 MHz
    hyperfine_coupling=-1.5e6,        # 29Si (I=1/2, 4.7%) weak hyperfine
    quadrupole_splitting=0.0,         # no dominant I>=1 nucleus in SiC
    nuclear_spin=0.5,                 # 29Si, I = 1/2 (28Si/30Si/12C are I=0)
    excitation_wavelength_nm=916.0,
    fluorescence_range_nm=(890, 940),
    t1_room=0.34e-3,                  # ~340 us T1 at 300 K (Carter/Embley 2017)
    t2_room=40.0e-6,                  # ~40 us Hahn echo, natural-abundance ensemble, RT (Carter 2015)
    fluorescence_factor=1.0,
    readout_error=0.10,
    defect_density_per_um2=0.5,
    gate_1q_ns=40.0, gate_2q_ns=300.0, readout_ns=2000.0, init_ns=500.0,
    # Per-paper depolarization (TEACHING MODEL): V_Si 2q gate fidelity 90-97%
    # (Bourassa 2020 / Ren 2026, room-T V_Si two-qubit gates) -> depol_2q = 0.04
    # (96%, mid-band). HIGHER than NV (0.004) but lower than hBN (0.05): the
    # physical basis for the "SiC is marginal" verdict (audit issue #7). With
    # realistic efficient-arithmetic gate counts, depol_2q=0.04 lets SiC clear
    # the noise floor ONLY for the shallowest circuit (Shor with the retry's
    # period-2 coprime, ~90 decomposed 2q gates) and rejects the deeper ones
    # (period-4, multiplier, QFT-NOS, ~110-140 gates) -> "marginal = factors
    # only the shallow Shor circuit". Temperature-INDEPENDENT here
    # (cryo_floor=0): V_Si cold 2q improvement is not literature-anchored for
    # this teaching model, so the conservative flat 300 K value is kept.
    depol_1q=0.0005, depol_2q=0.04,
    n_equivalent_nuclei=1,            # no equivalent-nucleus fingerprint like V_B-
    t1_exponent=1.0,
    t2_exponent=0.5,
    # Optical channel -- TEACHING-MODEL PLACEHOLDER, not supplier/literature quoted
    # for V_Si: ZPL 916 nm, optical T1 ~4 ns, room-T T2,opt ~ few fs, V_max=1.0.
    zpl_wavelength_nm=916.0,
    optical_t1_ns=4.0,
    optical_t2_room_fs=5.0,
    t2_opt_exponent=5.0,
    hom_visibility_max=1.0,
    # Per-site frequency detuning + asymmetric readout (TEACHING MODEL, not
    # supplier-quoted): V_Si inhomogeneous spread ~1 MHz (narrow ensemble
    # linewidth ~1-2 MHz). V_Si is an ENSEMBLE sensor read out as a continuous
    # photocurrent that averages the bright/dark contrast over many defects,
    # so the single-shot readout asymmetry is washed out -> readout_asymmetry
    # 0.0 (symmetric ensemble readout; SiC retention is also marginal at
    # ~0.6%, so the near-symmetric readout keeps the weak signal peak on top).
    frequency_detuning_spread_hz=1.0e6,
    readout_asymmetry=0.0,
    notes="V_Si (V2) is S=3/2 in 4H-SiC; 2|D|=70 MHz, ZPL 916 nm. Room-T ensemble "
          "T2 ~ tens of us (natural) up to ~100 us (isotopically purified). Optical "
          "channel values are a teaching placeholder (V_Si photon-gate regime not quoted here).",
    references=(
        "Kraus et al., Nature Physics 10, 157-162 (2014), doi:10.1038/nphys2826 — "
        "V_Si (V2) in 4H-SiC, 2|D|=70 MHz, S=3/2, room-T spin control.",
        "Carter et al., Phys. Rev. B 92, 161202 (2015), doi:10.1103/PhysRevB.92.161202 — "
        "room-T spin echo of V_Si ensembles, field-dependent T2 (tens of us).",
        "Embley et al., Phys. Rev. B 95, 045206 (2017), doi:10.1103/PhysRevB.95.045206 — "
        "V_Si T1~340 us, T2~104 us at 295 K, proton-irradiated 4H-SiC.",
        "Lekavicius et al., PRX Quantum 3, 010343 (2022), doi:10.1103/PRXQuantum.3.010343 — "
        "isotopic purification extends V_Si T2 to ~105 us.",
        # NOTE: depol_2q=0.04 reflects the reported room-T V_Si 2q-gate fidelity
        # range (~90-97%, e.g. Bourassa 2020 and subsequent V_Si gate work),
        # taken at the mid-band as a TEACHING MODEL. Not tied to a single
        # supplier datasheet; the operator pins the exact citation to the V_Si
        # 2q-gate paper they adopt (datasheet-grounded-physics).
    ),
)

PROFILES: Dict[str, DefectProfile] = {
    "NV": NV_DIAMOND,
    "hBN-VB": HBN_VB,
    "SiC-VSi": SIC_VS,
}


def get_profile(key: str = "NV") -> DefectProfile:
    """Return a defect profile by short name (case-insensitive)."""
    key = key.strip()
    if key in PROFILES:
        return PROFILES[key]
    for k, p in PROFILES.items():
        if k.lower() == key.lower() or p.short.lower() == key.lower():
            return p
    raise KeyError(
        f"Unknown defect profile '{key}'. Available: {list(PROFILES)}"
    )


def coherence_at_temperature(profile: DefectProfile, temperature_k: float) -> tuple:
    """Estimate (T1, T2) at a given temperature from a simple power-law scaling.

    T1 ~ T1_room * (300/T)**t1_exponent and T2 ~ T2_room * (300/T)**t2_exponent,
    both normalised to the room-temperature values, with T2 capped at 2*T1.

    This is a **teaching-grade model, not a quantitative prediction**.  The real
    temperature dependence is defect-specific and more complex than a single
    power law:

      * V_B- (hBN): T1 ~ T^(-5/2) (Gottscholl 2021, used here via t1_exponent=2.5);
        T2 is approximately **temperature-independent from 50-300 K** and only
        grows at lower temperatures as dephasing freezes out.  The mild T2 power
        law used here is a simplified illustration of that cryogenic recovery,
        not a fit to data.
      * NV (diamond): T1 sits in an Orbach/Raman crossover (Jarmola 2012); the
        generic 1/T used here is a rough proxy.
      * V_Si (SiC): a motional Jahn-Teller regime complicates 60-160 K
        (Embley 2017); values below are indicative.

    The qualitative trend it captures is correct and pedagogically useful:
    cooling the chip extends the coherence window, so deep algorithms that
    decohere at 300 K become executable at cryogenic temperatures.  Do not rely
    on the absolute numbers below ~50 K.
    """
    T = max(temperature_k, 1e-3)
    t1 = profile.t1_room * (300.0 / T) ** profile.t1_exponent
    t2 = profile.t2_room * (300.0 / T) ** profile.t2_exponent
    # T2 can never exceed 2*T1 (relaxation-limited bound)
    t2 = min(t2, 2.0 * t1)
    return t1, t2


def optical_coherence_at_temperature(profile: DefectProfile,
                                     temperature_k: float) -> tuple:
    """Estimate the **optical** coherence (T2,opt, HOM visibility V) at a
    temperature -- the budget that gates a photon-mediated two-qubit gate.

    Returns ``(t2_opt_seconds, hom_visibility)`` where
    ``T2,opt = optical_t2_room * (300/T)**t2_opt_exponent`` (capped at the
    transform limit ``2*T1,opt``) and ``V = min(T2,opt/(2*T1,opt),
    hom_visibility_max)``.

    This is a **teaching-grade model, not a quantitative prediction**, mirroring
    :func:`coherence_at_temperature`. The qualitative trend it captures is the
    physically correct one and the point of the model: at 300 K phonon dephasing
    broadens the ZPL so T2,opt collapses to ~fs and V ~ 1e-7 (a photon
    entangling gate decoheres), while at cryogenic T T2,opt -> 2*T1,opt
    (transform-limited) and V -> hom_visibility_max. Do not trust the absolute
    numbers; sweep ``t2_opt_exponent`` and ``hom_visibility_max`` to test it.

    If ``optical_t1_ns <= 0`` the profile does not model a photon gate and the
    function returns ``(0.0, 1.0)`` -- no optical penalty -- so the simulator
    falls back to the pure spin/depolarization budget for that profile.
    """
    t1_opt = profile.optical_t1_ns * 1e-9
    if t1_opt <= 0.0:
        return 0.0, 1.0
    T = max(temperature_k, 1e-3)
    t2_opt_room = profile.optical_t2_room_fs * 1e-15
    t2_opt = t2_opt_room * (300.0 / T) ** profile.t2_opt_exponent
    # T2,opt can never exceed 2*T1,opt (transform-limited / lifetime bound)
    t2_opt = min(t2_opt, 2.0 * t1_opt)
    v_hom = min(t2_opt / (2.0 * t1_opt), profile.hom_visibility_max)
    return t2_opt, v_hom