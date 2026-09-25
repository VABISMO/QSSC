"""Nuclear-spin memory: NV + ¹³C memory qubits.

Each NV electron spin in the pixel register can be paired with a nearby ¹³C
nucleus -- an engineered single-¹³C, since the 12C-enriched stone has few
native ¹³C (the enrichment that gives the electron its 1.8 ms T2 removes the
¹³C bath). The ¹³C is a long-lived MEMORY qubit: its spin T2 is ~seconds (vs
the electron's ~1.8 ms), so a circuit can STORE an intermediate result on the
¹³C, keep computing on the electron (or re-initialise it), and RECALL it
later. This extends the effective coherence window from ~1.8 ms to ~1 s,
roughly a 500x stretch -- the difference between a circuit that decoheres
mid-Shor and one that survives.

Physics (grounded in DefectProfile.nuclear_t2_s / nuclear_hyperfine_hz):
  * electron-nuclear SWAP gate time  t_SWAP ~ 1 / (2 A_parallel)  (a controlled
    rotation at the hyperfine coupling), ~250 ns at A = 2 MHz -- FASTER than
    the ~200 ns electron-electron dipole CZ.
  * ¹³C nuclear T2 ~ 1 s (no decoupling; > 80 s with decoupling, Cramer 2016).
  * memory fidelity during storage ~ exp(-t_store / nuclear_t2) -- the memory
    decays ~1000x slower than the electron register.

Numbers come from the DefectProfile (nuclear_hyperfine_hz, nuclear_t2_s); they
are NOT quoted by the supplier (the datasheet has no nuclear-T2 spec) and are
flagged the same way as the gate-timing engineering targets (see defects.py).
"""
from __future__ import annotations

import math


def nuclear_swap_time_s(nuclear_hyperfine_hz: float) -> float:
    """Electron-¹³C SWAP gate time (seconds).

    The SWAP is a controlled rotation at the NV-¹³C hyperfine coupling
    A_parallel; a full population transfer takes ~1/(2 A). At A = 2 MHz this is
    250 ns -- faster than the ~200 ns electron-electron dipole CZ, because the
    hyperfine coupling is stronger than the dipole coupling at the ~3 nm
    register spacing. Returns 0 if no coupling is set (no nuclear memory).
    """
    if nuclear_hyperfine_hz <= 0.0:
        return 0.0
    return 1.0 / (2.0 * nuclear_hyperfine_hz)


def nuclear_gate_fidelity(nuclear_t2_s: float, swap_time_s: float,
                          depol: float) -> float:
    """Electron-¹³C SWAP gate fidelity: thermal relaxation over the (short)
    swap time against the (long) nuclear T2, composed with the depolarizing
    drive error. Mirrors qssc.dipole.dipole_gate_fidelity.

    Returns 1.0 if no nuclear memory is configured.
    """
    if nuclear_t2_s <= 0.0 or swap_time_s <= 0.0:
        return 1.0
    # nuclear T1 ~ T2*2 (relaxation limited) as a conservative cap; the thermal
    # channel uses t1 ~ 2*t2 so the T1 and T2 Kraus terms are both meaningful.
    t1 = 2.0 * nuclear_t2_s
    # Average gate infidelity of a non-unital relaxation channel over the
    # Haar orbit (the QSSC uses a generic controlled-rotation, not a unital
    # axis).  With <r_z> = 0 over the orbit the Bloch-sphere average collapses
    # to F_avg = (3 + 2 e^{-t/T2} + e^{-t/T1}) / 6, hence the dephasing weight
    # is 2 (not 4) and the T1 weight is 1 — matching qssc.dipole.
    p_t1 = 1.0 - math.exp(-swap_time_s / t1)
    p_t2 = 1.0 - math.exp(-swap_time_s / nuclear_t2_s)
    thermal = 1.0 - (p_t1 + 2.0 * p_t2) / 6.0
    return max(0.0, min(1.0, thermal * (1.0 - depol)))


def memory_survival(storage_time_s: float, nuclear_t2_s: float) -> float:
    """Fraction of the stored quantum state surviving ``storage_time_s`` on the
    ¹³C memory. Pure dephasing exp(-t/T2) -- the memory's defining advantage is
    that T2 is ~1 s so a ~1.8 ms store (an entire electron coherence window)
    preserves ~99.8% of the state, vs ~37% on the electron.
    """
    if nuclear_t2_s <= 0.0:
        return 0.0
    return math.exp(-max(0.0, storage_time_s) / nuclear_t2_s)


def nuclear_memory_advantage(electron_t2_s: float, nuclear_t2_s: float) -> float:
    """How many electron-coherence windows fit in one nuclear T2 -- the
    headline compute-enabler ratio. ~550x for electron 1.8 ms / nuclear 1 s.
    """
    if electron_t2_s <= 0.0 or nuclear_t2_s <= 0.0:
        return 0.0
    return nuclear_t2_s / electron_t2_s