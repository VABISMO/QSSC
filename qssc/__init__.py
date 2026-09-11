"""Quantum Solid State Chip (QSSC) — room-temperature spin-defect quantum stack.

A Python package + Qiskit backend + REST API + (separate) React frontend that
together model, simulate and control a low-cost solid-state spin-defect quantum
chip operating at room temperature.  The canonical (default) defect family is
the nitrogen-vacancy (NV-) centre in CVD diamond — the only family here whose
room-temperature coherence (T2 ~ 1.8 ms in 12C-enriched diamond) is long enough
to compute seriously at 300 K.  The boron vacancy (VB-) in hBN and the silicon
vacancy (V_Si) in SiC are kept as sensor variants.  Readout is on-chip and
fully parallel: 64 CQD ASE/LED emitters (3x TLC5947 constant-current drivers)
pump the sites and 64 graphene/MoS2 photodetectors (each with its own TIA,
digitised by 4x AD7616 16-bit simultaneous-sampling ADCs) read them back
through an FPGA lock-in — no camera, no analog MUX, no serial scan.
"""
from .defects import (
    DefectProfile, HBN_VB, NV_DIAMOND, SIC_VS, PROFILES, get_profile,
    coherence_at_temperature, optical_coherence_at_temperature,
)
from .simulator import (
    SimulatedFPGA, QubitSite, auto_align, simulate_odmr, simulate_cqd_pulse,
    make_detector_response, xy8_decoupling, estimate_coherence_xy8,
)
from .backend import (
    QSSCBackend, QSSCJob, FPGAConnection, EmitterController, DetectorInterface,
    MicrowaveController, CalibrationManager, QubitReadoutProcessor,
    make_connection, NoFPGADetected,
)
from .circuits import (
    is_prime, select_coprime, mod_mult_gate, create_period_finding_circuit,
    postprocess_period, create_shor_circuit, create_qft_nos_circuit,
    postprocess_period_swapless, create_math_circuit, grover_search,
    build_user_circuit, UserCircuitError, perfect_power,
)
from .sensors import (
    lockin_integrated_readout, compass_measure, gyro_measure,
    CompassResult, GyroResult,
)
from .magnetometry import (
    magnetometer_sweep, synthetic_field_map, MagnetometerResult,
)
from .nuclear import (
    nuclear_swap_time_s, nuclear_gate_fidelity, memory_survival,
    nuclear_memory_advantage,
)
from .prime_readout import (
    PRIME_SYMBOLS, PrimeChannel, usable_primes, n_resolvable_prime_channels,
    best_prime_for_nv, assign_prime_channels, prime_orthogonality,
    prime_decompose, prime_denoise, prime_noise_rejection, cross_talk_residual,
)

__version__ = "2.0.0"
__all__ = [
    "DefectProfile", "HBN_VB", "NV_DIAMOND", "SIC_VS", "PROFILES", "get_profile",
    "coherence_at_temperature", "optical_coherence_at_temperature",
    "SimulatedFPGA", "QubitSite", "auto_align", "simulate_odmr",
    "simulate_cqd_pulse", "make_detector_response", "xy8_decoupling",
    "estimate_coherence_xy8",
    "QSSCBackend", "QSSCJob", "FPGAConnection", "EmitterController",
    "DetectorInterface", "MicrowaveController", "CalibrationManager",
    "QubitReadoutProcessor", "make_connection", "NoFPGADetected",
    "is_prime", "select_coprime", "mod_mult_gate", "create_period_finding_circuit",
    "postprocess_period", "create_shor_circuit", "create_qft_nos_circuit",
    "postprocess_period_swapless", "create_math_circuit",
    "grover_search", "build_user_circuit", "UserCircuitError", "perfect_power",
    "lockin_integrated_readout", "compass_measure", "gyro_measure",
    "CompassResult", "GyroResult",
    "magnetometer_sweep", "synthetic_field_map", "MagnetometerResult",
    "nuclear_swap_time_s", "nuclear_gate_fidelity", "memory_survival",
    "nuclear_memory_advantage",
    "PRIME_SYMBOLS", "PrimeChannel", "usable_primes",
    "n_resolvable_prime_channels", "best_prime_for_nv",
    "assign_prime_channels", "prime_orthogonality", "prime_decompose",
    "prime_denoise", "prime_noise_rejection", "cross_talk_residual",
    "__version__",
]