"""NV-NV magnetic dipole-dipole coupling and the within-pixel 2-qubit gate.

Two NV centres in the **same** microdiamond, a few nm apart, are coupled by
the magnetic dipole-dipole interaction, which falls as 1/r**3.  At r ~ 2-5 nm
this gives an MHz-scale coupling -- fast enough for a ~200 ns controlled-phase
(CZ) gate well within the spin T2.  This is the 300 K on-chip 2-qubit gate
for the QSSC (Neumann 2008 / Dolde 2013): a SPIN (microwave/dipole) gate, NOT
a photon gate, so it is never penalised by the optical HOM channel.

The coupling does NOT reach across the 2.5 mm pixel pitch (1/r**3 -> zero), so
cross-pixel 2-qubit gates are optical / cryo-only (see qssc.defects optical
channel).  The physical topology is therefore: within-pixel all-to-all (the
few NVs in one microdiamond), cross-pixel none at 300 K.

Constants are grounded in qssc.defects (NV gyromagnetic ratio 28 GHz/T).
"""
from __future__ import annotations

import math

# --- physical constants (SI) ---------------------------------------------
_MU0_OVER_4PI = 1e-7                 # T m / A
_HBAR = 1.054571817e-34              # J s
_H = 6.62607015e-34                  # J s
# NV electron gyromagnetic ratio in Hz/T (gamma_e / 2pi), matches
# qssc.defects.NV_DIAMOND.gyromagnetic_ratio = 28.0e9.
_GAMMA_E_HZ_PER_T = 28.024e9
# NV electron magnetic moment (J/T) = gamma_e (angular) * hbar = g * mu_B.
_NV_MOMENT_J_PER_T = 2.0 * math.pi * _GAMMA_E_HZ_PER_T * _HBAR


def dipole_coupling_hz(r_nm: float) -> float:
    """On-axis NV-NV magnetic dipole-dipole coupling frequency (Hz).

        d = (mu0 / 4pi) * (g mu_B)^2 / (h r^3)

    the secular Ising coupling between two NV electron spins separated by
    ``r_nm`` nanometres.  Off-axis coupling is reduced by the angular factor
    (1 - 3 cos^2 theta); here we return the order-unity on-axis value (the
    gate-time estimate below uses this, conservative up to a 2x geometry
    factor).  Returns the coupling in Hz.

    Examples (rounded): r=2 nm -> ~6.5 MHz, r=3 nm -> ~1.9 MHz,
    r=5 nm -> ~415 kHz, r=10 nm -> ~52 kHz.
    """
    r = r_nm * 1e-9
    return (_MU0_OVER_4PI * _NV_MOMENT_J_PER_T ** 2) / (_H * r ** 3)


def cz_gate_time_ns(r_nm: float) -> float:
    """Controlled-Z gate time (ns) by free evolution under H = d S1z S2z.

    A CZ needs a pi phase on the |11> state, accumulated at angular rate
    2*pi*d, so t_CZ = 1 / (2 d).  At r=3 nm (d ~ 1.9 MHz) this is ~260 ns,
    consistent with the 200 ns 2q-gate target in qssc.defects (which implies
    r ~ 2.7 nm).  Returns the gate time in ns.
    """
    d = dipole_coupling_hz(r_nm)
    return (1.0 / (2.0 * d)) * 1e9


def addressing_leakage(rabi_mhz: float, detuning_mhz: float) -> float:
    """Off-resonant drive leakage of a MW pulse onto a detuned NV.

    A MW drive of Rabi frequency ``rabi_mhz`` addressing one NV leaks onto a
    neighbour detuned by ``detuning_mhz`` with residual excitation fraction
    ~(Omega / Delta)^2 (in the far-detuned limit Delta >> Omega).  This is the
    ODMR-frequency-addressing selectivity: keep Omega << the per-NV detuning
    so each NV is driven independently from the global antenna.  Returns the
    leakage fraction (0..1).
    """
    if detuning_mhz <= 0:
        return 1.0
    return (rabi_mhz / detuning_mhz) ** 2


def dipole_gate_fidelity(
    r_nm: float, t2_s: float, depol_2q: float = 0.004
) -> float:
    """Estimated dipole CZ gate fidelity at the given NV separation.

    Combines (a) T2 decoherence during the gate, exp(-t_gate / T2), and
    (b) the depolarizing error per 2q gate (drive crosstalk / non-ideal MW),
    as in the qssc.defects noise model:

        F ~ exp(-t_gate / T2) * (1 - depol_2q)

    With r=3 nm, T2=1.8 ms (Balasubramanian 2009, 12C), depol_2q=0.004 this is
    ~0.996 (99.6%), matching the qssc.defects NV 2q-gate target.  Returns a
    fidelity in 0..1.
    """
    t_gate = cz_gate_time_ns(r_nm) * 1e-9
    return math.exp(-t_gate / t2_s) * (1.0 - depol_2q)