# qssc/backend_controllers.py
"""On-chip emitter / detector / microwave controllers over the FPGA UART link.

These wrap a single :class:`~qssc.backend_fpga.FPGAConnection` (or the
:class:`~qssc.simulator.SimulatedFPGA`) and translate the natural Python-facing
API (GHz / fraction / radians, site indices) into the integer wire protocol the
``fpga/rtl/command_parser.v`` firmware expects. One call site drives both the
simulator and a real Artix-7 board.
"""
from __future__ import annotations

import numpy as np

from .simulator import N_SITES


class EmitterController:
    """Drive the 64-channel on-chip CQD ASE/LED emitter array over the FPGA UART link.

    The FPGA drives 3x TLC5947 constant-current LED drivers over a 4-wire SPI;
    each of the 64 channels has its own 12-bit grayscale word, so multiple
    emitters light simultaneously at independent intensities. The wire commands
    (matching ``fpga/rtl/command_parser.v`` exactly)::

        DRIVE_EMITTER <index> <duration_ms>   pump one channel at full (gs=4095)
        SET_EMITTER <index> <level 0..4095>   arm one channel's grayscale
        DRIVE_PATTERN <duration_ms>           fire ALL armed channels in parallel
        CLEAR_EMITTERS                        disarm all channels (gs=0)

    There is no 8x8 row/col crossbar: every channel is independent and addressed
    by a single linear index 0..63. ``drive`` is the single-channel convenience
    wrapper; ``set_emitter`` + ``drive_pattern`` is the parallel multi-emitter
    path used by the per-run optical readout.
    """

    N_SITES = N_SITES

    def __init__(self, fpga):
        self.fpga = fpga

    def drive(self, index: int, duration_ms: int = 50):
        if not (0 <= index < self.N_SITES):
            raise ValueError("emitter index out of range")
        return self.fpga.send_command(f"DRIVE_EMITTER {index} {duration_ms}")

    def set_emitter(self, index: int, level: int):
        """Arm one channel's 12-bit grayscale (0..4095); fires on DRIVE_PATTERN."""
        if not (0 <= index < self.N_SITES):
            raise ValueError("emitter index out of range")
        if not (0 <= level <= 4095):
            raise ValueError("emitter level out of range 0..4095")
        return self.fpga.send_command(f"SET_EMITTER {index} {level}")

    def drive_pattern(self, duration_ms: int = 50):
        """Fire every armed (gs>0) channel simultaneously for duration_ms."""
        return self.fpga.send_command(f"DRIVE_PATTERN {duration_ms}")

    def clear_emitters(self):
        """Disarm all channels (gs=0)."""
        return self.fpga.send_command("CLEAR_EMITTERS")


class DetectorInterface:
    """Read the on-chip graphene/MoS2 photodetector array over the FPGA UART link.

    Each electrode's TIA output is digitised in parallel by 4x AD7616 16-bit
    simultaneous-sampling ADCs. ``READ_DETECTOR <site>`` returns one 16-bit
    sample (0..65535); ``READ_DETECTORS`` streams all 64 samples
    (space-separated) in a single reply -- the 4 ADCs sample together, so all
    64 channels are captured simultaneously, not scanned.
    """

    N_SITES = N_SITES

    def __init__(self, fpga):
        self.fpga = fpga

    def read(self, site: int) -> int:
        reply = self.fpga.send_command(f"READ_DETECTOR {int(site)}")
        return int(reply)

    def read_all(self) -> np.ndarray:
        reply = self.fpga.send_command("READ_DETECTORS")
        codes = [int(tok) for tok in reply.split()]
        return np.array(codes, dtype=np.int32)

    def read_odmr(self, site: int) -> int:
        """Convenience: single-site 16-bit sample (alias of ``read``)."""
        return self.read(site)


class MicrowaveController:
    """Microwave pulse driver over the FPGA UART link.

    The wire protocol matches ``fpga/rtl/command_parser.v`` and
    ``hardware/HARDWARE_DESIGN.md`` §5 exactly::

        APPLY_PULSE <qubit:int> <freq_khz:int> <amp:int> <duration_ns:int> <phase:int>\\n

    All five fields are decimal integers — the firmware parser rejects decimal
    points and underscores, so a float GHz value or a ``r_c`` two-qubit token
    would be answered ``ERR``.  This class keeps the Python-facing API in the
    natural Qiskit units (GHz / fraction / radians) and converts to the integer
    wire format on the way out, so the *same call* drives the
    :class:`~qssc.simulator.SimulatedFPGA` and a real Artix-7 board.

    Field encoding on the wire:

    * ``qubit``       — site index 0..63 (single int; two-qubit gates are not a
                        hardware primitive — see ``QSSCBackend.limitations()``).
    * ``freq_khz``    — RF frequency in kHz, e.g. ``3460000`` for 3.46 GHz.
    * ``amp``         — ADF4351 output power index 0..3 (``amp`` fraction × 3).
    * ``duration_ns`` — pulse duration in ns. The firmware arms an RF-switch
                        gate timer (100 MHz -> 10 ns/tick) for exactly
                        ``duration_ns`` after the ADF4351 locks, so the pulse
                        width is faithful to the request (the iverilog
                        testbench verifies a 500 ns APPLY_PULSE gates mw_gate
                        for exactly 50 clk cycles).
    * ``phase``       — pulse phase in **milliradians** (0 = +X, 3142 ≈ π).
    """

    def __init__(self, fpga, ref_hz: float = 25e6):
        self.fpga = fpga
        self.ref_hz = ref_hz

    def apply_pulse(self, qubit, freq_ghz: float, amp: float,
                    duration_ns: int, phase: float = 0.0):
        if not isinstance(qubit, (int, np.integer)):
            raise ValueError(
                "qubit must be a single site index (int 0..63); two-qubit "
                "gates are not a hardware primitive — see QSSCBackend.limitations()")
        if not (0.0 <= amp <= 1.0):
            raise ValueError("amp out of [0,1]")
        freq_khz = int(round(freq_ghz * 1e6))            # GHz  -> kHz (int)
        amp_idx = int(round(max(0.0, min(1.0, amp)) * 3))  # [0,1] -> 0..3 power index
        phase_mrad = int(round(phase * 1000))            # radians -> milliradians (int)
        return self.fpga.send_command(
            f"APPLY_PULSE {int(qubit)} {freq_khz} {amp_idx} {int(duration_ns)} {phase_mrad}"
        )