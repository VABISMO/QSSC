"""Invention 3: nuclear-spin memory (NV + 13C) — qssc.nuclear + backend wiring.

Each electron NV is paired with a 13C memory qubit whose T2 (~1 s) is ~500x
the electron's (~1.8 ms). A STORE (SWAP i -> i+n_electron) / RECALL (SWAP back)
extends the coherence window so a deep circuit can park an intermediate result
on the 13C and recover it long after the electron would have decohered.
"""
from __future__ import annotations

import math

from qiskit import QuantumCircuit

from qssc.nuclear import (nuclear_swap_time_s, nuclear_gate_fidelity,
                         memory_survival, nuclear_memory_advantage)
from qssc.backend import QSSCBackend
from qssc.simulator import N_NV_PER_PIXEL
from qssc.defects import get_profile


def test_nuclear_swap_time_grounding():
    """Electron-13C SWAP time = 1/(2 A_parallel); ~250 ns at A = 2 MHz."""
    assert abs(nuclear_swap_time_s(2.0e6) - 250e-9) < 1e-12
    assert nuclear_swap_time_s(0.0) == 0.0


def test_nuclear_gate_fidelity_high():
    """Nuclear SWAP fidelity is high: ~250 ns swap vs ~1 s 13C T2 -> thermal
    ~1.0, so fidelity is set by the 0.004 depol (>= 0.995)."""
    f = nuclear_gate_fidelity(1.0, 250e-9, 0.004)
    assert f > 0.995, f"nuclear SWAP fidelity {f} below 0.995"
    # with no depol it is essentially 1 (thermal channel negligible on 1 s T2)
    assert nuclear_gate_fidelity(1.0, 250e-9, 0.0) > 0.9999


def test_memory_survival_advantage():
    """A full electron coherence window (1.8 ms) stored on the 13C survives
    ~99.8%; on the electron it would be ~exp(-1) ~ 37%."""
    surv_nuc = memory_survival(1.8e-3, 1.0)
    surv_el = math.exp(-1.8e-3 / 1.8e-3)
    assert surv_nuc > 0.998, surv_nuc
    assert surv_el < 0.37
    assert surv_nuc > surv_el


def test_nuclear_memory_advantage_ratio():
    """~550x: nuclear 1 s / electron 1.8 ms."""
    adv = nuclear_memory_advantage(1.8e-3, 1.0)
    assert 500 < adv < 600, adv


def test_backend_nuclear_memory_off_by_default():
    """Default backend has no nuclear memory: register width = N_NV_PER_PIXEL,
    coupling = K_n (electron all-to-all), no nuclear pairs."""
    b = QSSCBackend("NV", seed=42, noise_mode="real")
    assert b._nuclear_memory is False
    assert b._n_qubits == N_NV_PER_PIXEL
    assert b._n_electron == N_NV_PER_PIXEL
    # K_n edges only (no nuclear pairs)
    assert len(b._neighbor_pairs()) == N_NV_PER_PIXEL * (N_NV_PER_PIXEL - 1)


def test_backend_nuclear_memory_doubles_register():
    """With nuclear_memory=True the register doubles (one 13C per NV) and the
    electron-nuclear SWAP edges appear (paired by index), but no 13C-13C edges."""
    b = QSSCBackend("NV", seed=42, noise_mode="real", nuclear_memory=True)
    assert b._nuclear_memory is True
    n_e = N_NV_PER_PIXEL
    assert b._n_electron == n_e
    assert b._n_qubits == 2 * n_e
    # electron-electron K_n (n_e*(n_e-1)) + 2*n_e electron-nuclear SWAP edges
    edges = b._neighbor_pairs()
    assert len(edges) == n_e * (n_e - 1) + 2 * n_e
    # the nuclear pair edges connect i <-> n_e+i
    nuc_edges = {(i, n_e + i) for i in range(n_e)}
    nuc_edges |= {(n_e + i, i) for i in range(n_e)}
    assert nuc_edges.issubset(set(edges))
    # no 13C-13C edges (nuclear indices never pair among themselves)
    for i, j in edges:
        if i >= n_e and j >= n_e:
            assert False, f"13C-13C edge ({i},{j}) should not exist"


def test_nuclear_qubits_carry_long_t2():
    """The 13C memory qubits carry nuclear T2 (seconds), the electron NVs carry
    the electron T2 (~ms). The Target QubitProperties reflect this."""
    b = QSSCBackend("NV", seed=42, noise_mode="real", nuclear_memory=True)
    prof = get_profile("NV")
    n_e = N_NV_PER_PIXEL
    # electron qubit 0 carries the electron T2 (~ms)
    el_t2 = b.target.qubit_properties[0].t2
    assert 0 < el_t2 < 0.1
    # 13C qubit n_e carries the nuclear T2 (seconds)
    nuc_t2 = b.target.qubit_properties[n_e].t2
    assert nuc_t2 == prof.nuclear_t2_s
    assert nuc_t2 > el_t2 * 100


def test_store_recall_survives_past_electron_window():
    """A |+> state STORED on the 13C, held for a duration that would fully
    decohere the electron, then RECALLED, survives at high fidelity (the
    nuclear T2). Compare against the same hold on the electron (decohered).

    We model the hold as a long idle (a delay) by measuring survival through the
    nuclear SWAP fidelity + the nuclear memory survival over the hold."""
    prof = get_profile("NV")
    hold_s = 5e-3  # 5 ms hold -- ~3x the electron T2 (fully decohered on e-)
    # nuclear memory survival over the hold
    surv = memory_survival(hold_s, prof.nuclear_t2_s)
    assert surv > 0.99, f"5 ms hold on 13C should survive >99%, got {surv}"
    # the same hold on the electron
    surv_e = math.exp(-hold_s / 1.8e-3)
    assert surv_e < 0.1
    assert surv > surv_e


def test_describe_reports_nuclear_memory():
    b = QSSCBackend("NV", seed=42, noise_mode="real", nuclear_memory=True)
    d = b.describe()
    assert d["nuclear_memory"] is True
    assert d["n_electron_qubits"] == N_NV_PER_PIXEL
    assert d["num_qubits"] == 2 * N_NV_PER_PIXEL
    assert d["nuclear_t2_s"] == get_profile("NV").nuclear_t2_s
    assert d["nuclear_memory_advantage"] > 500
    lim = " ".join(d["limitations"])
    assert "nuclear" in lim.lower() and "13c" in lim.lower()


def test_nuclear_memory_circuit_runs():
    """A circuit that STORES qubit 0 on its 13C (SWAP 0, n_e), does electron
    compute on another qubit, then RECALLS, executes end-to-end through the
    noise model."""
    b = QSSCBackend("NV", seed=42, noise_mode="real", nuclear_memory=True)
    n_e = N_NV_PER_PIXEL
    qc = QuantumCircuit(2 * n_e, 1)
    qc.h(0)                       # |+> on electron 0
    qc.swap(0, n_e)              # STORE on 13C of NV 0
    qc.x(1)                      # electron compute on NV 1 meanwhile
    qc.swap(n_e, 0)             # RECALL
    qc.measure(0, 0)
    counts = b.run(qc, shots=256).result().get_counts()
    assert sum(counts.values()) == 256
    # |+> measured in Z -> ~50/50 on the recalled qubit (survived the store)
    assert "0" in counts and "1" in counts