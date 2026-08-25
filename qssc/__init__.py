"""Quantum Solid State Chip (QSSC) — room-temperature spin-defect quantum stack.

A Python package + Qiskit backend + REST API + (separate) React frontend that
together model, simulate and control a low-cost solid-state spin-defect quantum
chip operating at room temperature.  The canonical (default) defect family is
the nitrogen-vacancy (NV-) centre in CVD diamond — the only family here whose
room-temperature coherence (T2 ~ 1.8 ms in 12C-enriched diamond) is long enough
to compute seriously at 300 K.  The boron vacancy (VB-) in hBN and the silicon
vacancy (V_Si) in SiC are kept as sensor variants.  Readout is on-chip: an 8x8
CQD ASE/LED emitter array pumps each site and a graphene/MoS2 photodetector
array reads it back through an FPGA lock-in — no camera.
"""
from .defects import (
    DefectProfile, HBN_VB, NV_DIAMOND, SIC_VS, PROFILES, get_profile,
    coherence_at_temperature,
)
from .simulator import (
    SimulatedFPGA, QubitSite, auto_align, simulate_odmr, simulate_cqd_pulse,
    make_detector_response, xy8_decoupling, estimate_coherence_xy8,
)
from .backend import (
    QSSCBackend, QSSCJob, FPGAConnection, EmitterController, DetectorInterface,
    MicrowaveController, CalibrationManager, QubitReadoutProcessor,
    make_connection,
)
from .circuits import (
    is_prime, select_coprime, mod_mult_gate, create_period_finding_circuit,
    postprocess_period, create_shor_circuit, create_qft_nos_circuit,
    postprocess_period_swapless, create_math_circuit, grover_search,
    build_user_circuit, UserCircuitError,
)

__version__ = "2.0.0"
__all__ = [
    "DefectProfile", "HBN_VB", "NV_DIAMOND", "SIC_VS", "PROFILES", "get_profile",
    "coherence_at_temperature",
    "SimulatedFPGA", "QubitSite", "auto_align", "simulate_odmr",
    "simulate_cqd_pulse", "make_detector_response", "xy8_decoupling",
    "estimate_coherence_xy8",
    "QSSCBackend", "QSSCJob", "FPGAConnection", "EmitterController",
    "DetectorInterface", "MicrowaveController", "CalibrationManager",
    "QubitReadoutProcessor", "make_connection",
    "is_prime", "select_coprime", "mod_mult_gate", "create_period_finding_circuit",
    "postprocess_period", "create_shor_circuit", "create_qft_nos_circuit",
    "postprocess_period_swapless", "create_math_circuit",
    "grover_search", "build_user_circuit", "UserCircuitError", "__version__",
]