"""Pydantic request models for the QSSC API.

All request bodies live here so the route modules import one consistent set.
The field-gradient and pixel-size bounds reference the shared coil constants
(:mod:`api._guards`) and the array-calibration ceilings (:mod:`qssc.array_calibration`).
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from qssc.array_calibration import (
    nv_ceiling_for_size, PIXEL_SIZE_MAX_UM, PIXEL_SIZE_MIN_UM,
)

from ._guards import GRAD_COIL_MAX_MHZ_PER_UM


class RunRequest(BaseModel):
    algorithm: str = "shor"          # shor | period | bell | grover | math
    profile: str = "NV"
    temperature_k: float = Field(300.0, ge=0.001, le=600)
    shots: int = Field(2048, ge=1, le=8192)
    seed: int = Field(42, ge=0, le=2_147_483_647)
    noise_mode: str = "real"   # real (default, full physics) | phenomenological | none
    # Hardware-real mode: drive the real FPGA board (AD7616 readout, ADF4351
    # pulses, TLC5947 emitters, QRNG, ODMR calibration) over QSSC_FPGA_PORT.
    # False (default) = Simulator (Aer + the on-chip readout model). The
    # quantum bitstring always comes from Aer (the chip is a sensor array,
    # not a gate-model QC); ``hardware`` selects whether the I/O layer is a
    # real board or the in-process simulator. No board + hardware=True ->
    # {"error": "no-fpga-detected"} (never a silent sim).
    hardware: bool = False
    # algorithm-specific
    n: int = Field(15, ge=2, le=100_000)            # shor / period
    a: Optional[int] = None          # base (auto if None)
    b: Optional[int] = None          # math operand b (mapped to num2)
    bits: Optional[int] = None       # math word size (accepted, derived if None)
    op: str = "add"                  # math op
    num1: float = 2.0
    num2: Optional[float] = None
    num_qubits: int = Field(2, ge=1, le=64)         # bell / qft / qrand (full 64-site chip via MPS)
    n_items: Optional[int] = None    # grover: search space = 2^n_items
    marked: Optional[int] = None     # grover: marked index
    iterations: int = Field(1, ge=1, le=100)        # grover
    # qft
    x: int = 1                       # qft: input computational-basis state |x>
    # qrand (quantum RNG -> cryptographic key + self-signed cert)
    num_bytes: int = Field(32, ge=1, le=64)        # qrand: key size in bytes
    common_name: str = "QSSC-quantum-device"  # qrand: certificate CN
    # bb84 quantum key distribution
    num_bits: int = Field(128, ge=1, le=2048)      # bb84: qubits/rounds to exchange
    eavesdrop: bool = False         # bb84: simulate intercept-resend Eve
    # Pixel mode + invention toggles (the panel switches). pixel_mode selects
    # the circuit-qubit model: register (one pixel's N-NV register, within-
    # pixel all-to-all dipole 2q, the default) or ensemble (64 chip pixels,
    # no within-pixel 2q). n_nv_per_pixel / register_line_freqs_hz override
    # the register width (the measured resolvable ODMR line count from the
    # calibration's decompose_odmr). gradient_mhz_per_um (the applied field
    # gradient) + register_line_freqs_hz close the calibration
    # -> circuit loop: the register sits on the real addressed frequencies at
    # that gradient, not the synthetic 6 MHz grid. prime_readout toggles the
    # prime-coprime lock-in denoiser (default on at the panel).
    pixel_mode: str = "register"          # register | ensemble
    n_nv_per_pixel: Optional[int] = Field(None, ge=1, le=64)
    register_nv_spacing_nm: float = Field(2.75, gt=0)
    gradient_mhz_per_um: float = Field(0.0, ge=0.0, le=GRAD_COIL_MAX_MHZ_PER_UM)
    pixel_size_um: float = Field(200.0, gt=0, le=PIXEL_SIZE_MAX_UM)
    register_line_freqs_hz: Optional[List[int]] = None
    prime_readout: bool = False
    n_noise_bins: int = Field(64, ge=1, le=4096)
    # Classical anti-noise filter stack (300 K, SNR-protective).
    # noise_env presets the environmental noise channels ("none"=off -> back-
    # compat); anti_noise is the filter config dict (empty/None -> back-compat).
    # A DD sequence extends the spin T2 used for coherence survival.
    noise_env: str = "none"
    anti_noise: Optional[dict] = None


class CustomCircuitRequest(BaseModel):
    code: str                        # OpenQASM 2 or Python snippet (defines `qc`)
    profile: str = "NV"
    temperature_k: float = Field(300.0, ge=0.001, le=600)
    shots: int = Field(2048, ge=1, le=4096)
    seed: int = Field(42, ge=0, le=2_147_483_647)
    noise_mode: str = "real"   # real (default, full physics) | phenomenological | none
    hardware: bool = False     # Hardware-real mode (see RunRequest.hardware)
    # Cap at 20 qubits: the chip has 64 physical sites but the circuit is
    # simulated classically (Aer statevector / density matrix, O(2^n) memory).
    # 20 qubits is a safe statevector size (~16 MB) on the free Render worker;
    # circuits wider than the backend's SIM_MAX_QUBITS (28) are rejected with a
    # clear error rather than silently faked. See qssc.backend.SIM_MAX_QUBITS.
    max_qubits: int = Field(20, ge=1, le=20)
    # Pixel mode + noise toggles (see RunRequest).
    pixel_mode: str = "register"
    n_nv_per_pixel: Optional[int] = Field(None, ge=1, le=64)
    register_nv_spacing_nm: float = Field(2.75, gt=0)
    gradient_mhz_per_um: float = Field(0.0, ge=0.0, le=GRAD_COIL_MAX_MHZ_PER_UM)
    pixel_size_um: float = Field(200.0, gt=0, le=PIXEL_SIZE_MAX_UM)
    register_line_freqs_hz: Optional[List[int]] = None
    prime_readout: bool = False
    n_noise_bins: int = Field(64, ge=1, le=4096)
    # Classical anti-noise filter stack (see RunRequest).
    noise_env: str = "none"
    anti_noise: Optional[dict] = None


class ParallelRunRequest(BaseModel):
    """64-parallel distributed computing.

    Run one circuit per pixel across the 64-pixel array (64 INDEPENDENT ~10-NV
    processors at 300 K) and aggregate. The chip's throughput is 64x a single
    pixel, not one 640-qubit circuit (64x10 > 63-qubit MPS cap)."""
    # extra="allow" so the per-algorithm params the frontend sends (n_items,
    # marked, x, op, a, b, num1, num2, bits, num_bytes, common_name, num_bits,
    # eavesdrop, iterations -- the same fields RunRequest declares) pass through
    # to run_parallel's sample-mode circuit builder + postprocess. Without this
    # Pydantic drops them and grover/qft/math build with defaults (marked=0...).
    model_config = ConfigDict(extra="allow")
    mode: str = "sample"        # shor | sample | custom
    profile: str = "NV"
    temperature_k: float = Field(300.0, ge=0.001, le=600)
    shots: int = Field(2048, ge=1, le=8192)
    seed: int = Field(42, ge=0, le=2_147_483_647)
    noise_mode: str = "real"
    n_pixels: int = Field(64, ge=1, le=64)   # pixels to use (1..64)
    # shor mode
    n: int = Field(15, ge=2, le=63)
    # sample mode
    num_qubits: int = Field(4, ge=1, le=64)
    # sample: which demo algorithm to build per pixel (bell/grover/qft/math/
    # qrand). Omitted -> uniform H+measure (the legacy sample behaviour).
    algorithm: Optional[str] = None
    qasm: Optional[str] = None  # sample: a QASM circuit (else uniform H+measure)
    # custom mode
    circuits: Optional[list] = None  # list of QASM strings, one per pixel
    # Pixel mode + noise toggles (see RunRequest).
    pixel_mode: str = "register"
    n_nv_per_pixel: Optional[int] = Field(None, ge=1, le=64)
    register_nv_spacing_nm: float = Field(2.75, gt=0)
    gradient_mhz_per_um: float = Field(0.0, ge=0.0, le=GRAD_COIL_MAX_MHZ_PER_UM)
    pixel_size_um: float = Field(200.0, gt=0, le=PIXEL_SIZE_MAX_UM)
    register_line_freqs_hz: Optional[list] = None
    prime_readout: bool = False
    n_noise_bins: int = Field(64, ge=1, le=4096)
    # Opt in to the cached per-pixel calibration (each pixel runs
    # at its MEASURED register width, narrow pixels skipped). Default False =
    # the uniform-register back-compat path. Calibrate first via
    # /api/circuit/calibrate-array, then set use_calibration=true here.
    use_calibration: bool = False
    # Classical anti-noise filter stack (see RunRequest).
    noise_env: str = "none"
    anti_noise: Optional[dict] = None


class CalibrateArrayRequest(BaseModel):
    """Calibrate the 64-pixel array's per-pixel qubit register
    width (a MEASURED quantity: the number of ODMR lines the field gradient
    makes resolvable in each pixel, capped by the NVs present).

    ``source="sim"`` synthesizes each pixel's ODMR spectrum and counts the
    resolved lines analytically; ``source="real"`` drives the ``RUN_ALL_ODMR``
    firmware verb (sweep MW, read all 64 detectors per point) and counts the
    lines from the measured codes. Both produce the same
    ``ArrayCalibration`` shape and persist it to
    ``hardware/calibration/array_calibration.json`` so subsequent
    ``run-parallel`` calls run each pixel at its own calibrated width."""
    profile: str = "NV"
    gradient_mhz_per_um: float = Field(1.75, ge=0.0, le=GRAD_COIL_MAX_MHZ_PER_UM)
    # Optional explicit MCP4921 DAC code (0..4095) for the real path's
    # SET_GRADIENT write. When None (default) the code is derived from
    # gradient_mhz_per_um via gradient_dac_code_from_mhz; supplying it lets an
    # API caller program the coil directly without going through the MHz->code
    # rounding (e.g. to reproduce a prior calibration's exact code).
    gradient_code: Optional[int] = Field(None, ge=0, le=4095)
    pixel_size_um: float = Field(200.0, ge=PIXEL_SIZE_MIN_UM, le=PIXEL_SIZE_MAX_UM)
    seed: int = Field(42, ge=0, le=2_147_483_647)
    temperature_k: float = Field(300.0, ge=0.001, le=600)
    source: str = "sim"          # sim | real
    max_nv_per_microdiamond: int = Field(300, ge=1, le=1000)  # compute-stone NVs (100-300)
    n_points: int = Field(400, ge=32, le=4096)  # real path sweep points
    # Charge-sharing centroiding (sub-pixel detector resolution).
    # When true, build a SubPixelModel (sigma_sheet_mm sheet spreading length)
    # and report spatial_channels = 64*S + per-pixel sub_pixel_position. This is
    # detector imaging metadata; it does NOT change register_width.
    sub_pixel: bool = False
    sigma_sheet_mm: float = Field(1.0, gt=0.0, le=10.0)
    # N-shot lock-in averaging raises per-line SNR by sqrt(N), so
    # dimmer lone-NV ODMR lines clear the detection bar -> the MEASURED
    # register_width rises toward min(resolvable, nv_present). 1 = no averaging.
    integration_shots: int = Field(1, ge=1, le=10000)
    # Classical anti-noise filter stack (300 K, SNR-protective).
    # noise_env presets the environmental channels: "lab"/"quiet"/"noisy"/"none"
    # (none = off -> bit-identical to the prior model). anti_noise is the filter
    # config dict (thermal_servo_gain, vibration_iso_residual, emi_shield_residual,
    # feed_forward_channels, feed_forward_rejection, common_mode_reject,
    # dd_sequence, dd_n_blocks); empty/None -> all-off -> back-compat.
    noise_env: str = "none"
    anti_noise: Optional[dict] = None
    # Compressed-sensing super-resolution ODMR. When > 0, recover
    # each pixel's line set by OMP sparse recovery over the gradient-bin
    # dictionary with superres_factor = this value (0.6 typical; 1.0 = hard-
    # threshold behaviour). Deconvolves overlapping lines + lowers the SNR bar.
    superres: Optional[float] = Field(None, ge=0.0, le=1.0)
    # Bayesian quantum-limited readout. When 0 < value < 1, recover
    # lines by the one-sided Bonferroni-corrected matched-filter detector at this
    # confidence (0.99 typical). Extracts marginal lone-NV lines the hard bar
    # misses. Mutually exclusive with superres (superres takes precedence).
    bayesian: Optional[float] = Field(None, gt=0.0, lt=1.0)

    @model_validator(mode="after")
    def _clamp_nv_to_stone_volume(self):
        # A microdiamond can only hold as many NVs as its volume allows (300 NV
        # @ 200 um, scaling as size^3 at <10 ppm). Clamp the requested cap to
        # that physical ceiling so a raw-JSON request can never ask for more NVs
        # than the stone can carry. The frontend mirrors this; calibrate_array
        # re-clamps as defense-in-depth.
        ceil = nv_ceiling_for_size(self.pixel_size_um)
        if self.max_nv_per_microdiamond > ceil:
            self.max_nv_per_microdiamond = ceil
        return self


class ScriptRequest(BaseModel):
    name: str
    code: str = Field(..., max_length=100_000)  # bound the stored blob (audit #107)
    id: Optional[str] = None


class FpgaCommandRequest(BaseModel):
    command: str