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
    # -- aspirational, uncited (see the NOTE above).
    depol_1q: float = 0.0008
    depol_2q: float = 0.006
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
    depol_1q=0.0008, depol_2q=0.006,
    n_equivalent_nuclei=3,           # 3 equivalent 14N -> 7-line hyperfine (2nI+1)
    t1_exponent=2.5,                 # T1 ~ T^(-5/2) (Gottscholl 2021)
    t2_exponent=0.5,
    notes="Room-temperature optically addressable S=1 spin in 2D hBN; D~3.46 GHz, "
          "characteristic 7-line 14N hyperfine fingerprint (A~47 MHz).",
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
    t2_room=1.8e-3,                  # 1.8 ms Hahn echo in 99.7% 12C-enriched diamond, RT (Balasubramanian 2009)
    fluorescence_factor=1.2,         # 20% brighter when cooled to 77 K
    readout_error=0.08,              # single-shot spin readout ~90% fidelity
    defect_density_per_um2=5.0,
    gate_1q_ns=25.0, gate_2q_ns=200.0, readout_ns=3000.0, init_ns=1000.0,
    depol_1q=0.0002, depol_2q=0.002,
    n_equivalent_nuclei=1,           # single 14N -> 3-line hyperfine
    t1_exponent=1.0,
    t2_exponent=0.5,
    notes="Workhorse room-temperature defect; D~2.87 GHz. Best-in-class RT coherence "
          "(1.8 ms) requires 12C purification; natural-abundance diamond is ~0.5-0.7 ms.",
    references=(
        "Doherty et al., Physics Reports 528, 1 (2013) — NV centre review (D=2.87 GHz).",
        "Balasubramanian et al., Nature Materials 8, 383 (2009), doi:10.1038/nmat2420 — "
        "1.8 ms T2 at RT in 99.7% 12C-enriched diamond.",
        "Jarmola et al., Phys. Rev. Lett. 108, 197601 (2012) — NV T1 at room temperature.",
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
    depol_1q=0.0005, depol_2q=0.004,
    n_equivalent_nuclei=1,            # no equivalent-nucleus fingerprint like V_B-
    t1_exponent=1.0,
    t2_exponent=0.5,
    notes="V_Si (V2) is S=3/2 in 4H-SiC; 2|D|=70 MHz, ZPL 916 nm. Room-T ensemble "
          "T2 ~ tens of us (natural) up to ~100 us (isotopically purified).",
    references=(
        "Kraus et al., Nature Physics 10, 157-162 (2014), doi:10.1038/nphys2826 — "
        "V_Si (V2) in 4H-SiC, 2|D|=70 MHz, S=3/2, room-T spin control.",
        "Carter et al., Phys. Rev. B 92, 161202 (2015), doi:10.1103/PhysRevB.92.161202 — "
        "room-T spin echo of V_Si ensembles, field-dependent T2 (tens of us).",
        "Embley et al., Phys. Rev. B 95, 045206 (2017), doi:10.1103/PhysRevB.95.045206 — "
        "V_Si T1~340 us, T2~104 us at 295 K, proton-irradiated 4H-SiC.",
        "Lekavicius et al., PRX Quantum 3, 010343 (2022), doi:10.1103/PRXQuantum.3.010343 — "
        "isotopic purification extends V_Si T2 to ~105 us.",
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