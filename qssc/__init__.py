"""Quantum Solid State Chip (QSSC) — room-temperature spin-defect quantum stack.

A Python package + Qiskit backend + REST API + (separate) React frontend that
together model, simulate and control a low-cost solid-state spin-defect quantum
chip operating at room temperature.  The default defect family is the boron
vacancy (VB-) in hexagonal boron nitride; nitrogen-vacancy centres in diamond
and silicon-vacancies in SiC are also supported.
"""
from .defects import (
    DefectProfile, HBN_VB, NV_DIAMOND, SIC_VS, PROFILES, get_profile,
    coherence_at_temperature,
)
from .simulator import (
    SimulatedFPGA, QubitSite, auto_align, simulate_odmr, simulate_laser_pulse,
    simulate_galvo_scan, make_camera_frame, detect_defects, xy8_decoupling,
    estimate_coherence_xy8,
)
from .backend import (
    QSSCBackend, QSSCJob, FPGAConnection, LaserArrayController, CameraInterface,
    MicrowaveController, CalibrationManager, QubitImageProcessor, make_connection,
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
    "simulate_laser_pulse", "simulate_galvo_scan", "make_camera_frame",
    "detect_defects", "xy8_decoupling", "estimate_coherence_xy8",
    "QSSCBackend", "QSSCJob", "FPGAConnection", "LaserArrayController",
    "CameraInterface", "MicrowaveController", "CalibrationManager",
    "QubitImageProcessor", "make_connection",
    "is_prime", "select_coprime", "mod_mult_gate", "create_period_finding_circuit",
    "postprocess_period", "create_shor_circuit", "create_qft_nos_circuit",
    "postprocess_period_swapless", "create_math_circuit",
    "grover_search", "build_user_circuit", "UserCircuitError", "__version__",
]