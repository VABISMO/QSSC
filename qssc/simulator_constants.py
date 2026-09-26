# qssc/simulator_constants.py
"""On-chip readout constants shared across the simulator package.

These are the 64-channel CQD ASE emitter + graphene/MoS2 detector + lock-in
readout parameters. They are split out from :mod:`qssc.simulator` so the
extracted submodules (:mod:`qssc.simulator_response`, :mod:`qssc.simulator_site`)
can import them without creating an import cycle back into :mod:`qssc.simulator`.
``qssc.simulator`` re-exports every name here, so ``from qssc.simulator import X``
keeps resolving unchanged.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# On-chip readout constants (CQD ASE emitter + graphene/MoS2 detector + lock-in)
# ---------------------------------------------------------------------------
# 64 parallel channels: 64 emitters (3x TLC5947) + 64 detectors (4x AD7616).
# The die is a 128-pad part -- 64 emitter pads on the bottom edge, 64 detector
# pads on the top edge -- addressed by a single linear index 0..63 (no 8x8
# row/col crossbar; every channel is independent and simultaneous).
N_SITES = 64
# Per-pixel NV register width: each of the 64 pixels holds a small register of
# NV centres in ONE microdiamond, all-to-all dipole-coupled (1/r^3, ~3 nm apart
# -> ~2 MHz coupling -> ~200 ns CZ gate). The circuit model simulates ONE
# pixel's register (64x10 = 640 exceeds the 63-qubit MPS cap, so the 64 pixels
# are independent parallel processors at 300 K, NOT one 640-qubit machine).
# Cross-pixel 2q is optical-bus only, cryo (V_HOM -> 1 at cryo, ~2e-7 at 300 K).
N_NV_PER_PIXEL = 10
# The AD7616 is a 16-bit bipolar +/-5V ADC. Detector samples are 16-bit
# (0..65535), the digitised TIA output -- NOT 12-bit XADC codes. The 1 kHz
# lock-in demodulation that turns these raw samples into a PL/ODMR signal is
# done host-side (mod_sync is the reference); the FPGA delivers raw samples.
ADC_BITS = 16
ADC_MAX = (1 << ADC_BITS) - 1            # 65535
# Each detector electrode sits under one emitter channel and reads a *small
# ensemble* of defects (not a single isolated spin), so the on-resonance PL dip
# is the ensemble ODMR contrast, not the large single-NV spin-readout contrast.
# Real NV-ensemble ODMR at 300 K is 3-8%; 5% is the representative mid-range and matches
# the CW spectrum model (simulate_odmr, contrast_depth=0.03, same order).
ODMR_CONTRAST = 0.05                      # ensemble ODMR dip fraction at resonance
ODMR_LINEWIDTH_HZ = 5.0e6                 # ODMR linewidth (~5 MHz; matches simulate_odmr)
# Bright/dark/background are 16-bit AD7616 codes, scaled from the original
# 12-bit XADC values by *16 (3200->51200, 3040->48640, 100->1600) so the
# fractional saturation, ODMR dip fraction and lock-in SNR are preserved.
BRIGHT_CODE = 51200                       # pumped ensemble, spin ms=0 (bright) -> full PL
DARK_CODE = 48640                         # pumped ensemble at resonance -> BRIGHT*(1-ODMR_CONTRAST)
BG_CODE = 1600                            # unpumped electrode -> detector background floor
# Post-lock-in photocurrent noise (ADC counts). The 1 kHz lock-in averages many
# MW-modulation cycles, suppressing broadband noise so the small 5% dip (~2560
# counts) is resolved at ~4 sigma in a single read. The per-qubit *quantum*
# readout error (profile.readout_error, ~8% for NV) is the spin
# initialisation/projection imperfection, injected separately in the Qiskit
# noise model -- it is decoupled from this detector SNR, as on real hardware.
READOUT_NOISE = 640                       # std dev of lock-in photocurrent noise (16-bit counts)
ASE_GAIN = 1.15                            # ASE concentrates the CQD emission into the waveguide
LOCKIN_MOD_HZ = 1000.0                    # MW modulation frequency for the lock-in readout
DETECT_THRESHOLD = 16000                  # lock-in amplitude above which a site counts as coupled
SITE_FAB_YIELD = 0.9                      # fraction of channels the fabrication step wires correctly