# qssc/simulator.py
"""Physics + hardware simulator for the Quantum Solid State Chip (QSSC).

This module is the room-temperature software twin of the physical chip.  It
models the optical readout path (laser pump, galvo / VCSEL array, camera /
on-chip photodetector array), the spin-defect Hamiltonian (ODMR with hyperfine
structure) and the dynamical-decoupling toolbox, all parametrised by a
:class:`~qssc.defects.DefectProfile`.

The simulator is deliberately dependency-light: NumPy is the only hard
requirement.  OpenCV is used for image-based readout when available and falls
back to a pure-NumPy detector otherwise, so the package runs in a minimal
environment (and inside CI).
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .defects import DefectProfile, get_profile, coherence_at_temperature

logger = logging.getLogger(__name__)

try:  # optional, used only for contour-based readout
    import cv2  # type: ignore
    _HAS_CV2 = True
except Exception:  # pragma: no cover - exercised only when cv2 absent
    cv2 = None
    _HAS_CV2 = False


# ---------------------------------------------------------------------------
# Simulated FPGA connection (mirrors the UART protocol of the real firmware)
# ---------------------------------------------------------------------------

class SimulatedFPGA:
    """In-process stand-in for the Artix-7 control board.

    Understands the same text commands as ``fpga/rtl/command_parser.v``:

    * ``FIRE_LASER <row> <col> <duration_ms>``
    * ``APPLY_PULSE <qubit> <freq_khz> <amp> <duration_ns> <phase>``
    * ``CAPTURE_FRAME`` and ``CAPTURE_DARK``

    The ``APPLY_PULSE`` fields are **decimal integers** matching the firmware
    parser and ``hardware/HARDWARE_DESIGN.md`` §5: ``freq_khz`` in kHz,
    ``amp`` 0..3, ``phase`` in milliradians. Keeping the wire format identical
    means the same Python control code drives the simulator and the real
    hardware — :class:`~qssc.backend.MicrowaveController` emits exactly this
    integer form.
    """

    GRID = 8

    def __init__(self, profile: DefectProfile | str = "hBN-VB",
                 temperature_k: float = 300.0, noise: float = 0.1,
                 image_size: Tuple[int, int] = (480, 640), seed: Optional[int] = None):
        self.profile = get_profile(profile) if isinstance(profile, str) else profile
        self.temperature_k = temperature_k
        self.noise = noise
        self.image_size = image_size
        self.rng = np.random.default_rng(seed)
        self.active_lasers: set[Tuple[int, int]] = set()
        self.sim_states: Dict[int, int] = {}
        self.sim_freq = self.profile.zero_field_splitting
        self.frame_counter = 0
        t1, t2 = coherence_at_temperature(self.profile, temperature_k)
        self.t1, self.t2 = t1, t2

    # -- command interface -------------------------------------------------
    def send_command(self, command: str) -> str:
        parts = command.split()
        head = parts[0]
        try:
            if head == "FIRE_LASER":
                row, col = int(parts[1]), int(parts[2])
                self.active_lasers.add((row, col))
                self.sim_states[row * self.GRID + col] = 1
                return "OK"
            if head == "APPLY_PULSE":
                # Canonical wire format (matches command_parser.v and
                # HARDWARE_DESIGN.md §5): five decimal integers.
                #   APPLY_PULSE <qubit> <freq_khz> <amp> <duration_ns> <phase>
                # freq_khz -> GHz, phase (milliradians) -> radians for the
                # physics model below.
                qubit = int(parts[1])
                freq_khz = int(parts[2])
                amp = int(parts[3])
                dur = int(parts[4])
                phase_mrad = int(parts[5])
                freq_ghz = freq_khz / 1e6
                phase_rad = phase_mrad / 1000.0
                return self._apply_pulse(qubit, freq_ghz, amp, dur, phase_rad)
            if head == "CAPTURE_DARK":
                self.active_lasers.clear()
                self.sim_states.clear()
                return "OK"
            if head == "CAPTURE_FRAME":
                return "OK"
            return "ERR"
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("sim command failed: %s (%s)", command, exc)
            return "ERR"

    def _apply_pulse(self, qubit, freq_ghz, amp, dur, phase_rad) -> str:
        # Reject if the drive is far off resonance (proxy for the PLL lock
        # check). freq_ghz is GHz; self.sim_freq is Hz.
        if abs(freq_ghz * 1e9 - self.sim_freq) > self.noise * self.sim_freq:
            return "ERR"
        q = qubit
        self.sim_states.setdefault(q, 0)
        # phase arrives in radians (converted from integer milliradians on the
        # wire). Tolerance ~1 mrad matches the integer-mrad quantisation.
        if abs(phase_rad - math.pi) < 1e-3:          # X gate (phase ≈ π)
            self.sim_states[q] = 1 - self.sim_states[q]
        elif abs(phase_rad) > 1e-3:                  # RZ/RY: stochastic error
            if self.rng.random() < self.noise:
                self.sim_states[q] = 1 - self.sim_states[q]
        return "OK"

    # -- image generation --------------------------------------------------
    def receive_image(self, size: Optional[Tuple[int, int]] = None) -> np.ndarray:
        size = size or self.image_size
        image = np.zeros(size, dtype=np.uint8)
        cell_w = size[1] / self.GRID
        cell_h = size[0] / self.GRID
        yy, xx = np.mgrid[0:size[0], 0:size[1]]
        for row, col in list(self.active_lasers):
            cx = int(col * cell_w + cell_w / 2)
            cy = int(row * cell_h + cell_h / 2)
            r = max(8, int(min(cell_w, cell_h) / 4))
            image += (255 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r ** 2))).astype(np.uint8)
        # shot + read noise, capped to 8 bit
        image = np.clip(image, 0, 255).astype(np.uint8)
        noise = self.rng.normal(0, 6, size).astype(np.int16)
        image = np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        self.active_lasers.clear()
        self.frame_counter += 1
        return image

    def close(self):  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Optical readout primitives
# ---------------------------------------------------------------------------

def simulate_laser_pulse(intensity: float = 1.0, duration: float = 1e-6) -> dict:
    """Return a noisy laser-pulse record (intensity, duration, ADC value)."""
    inten = float(np.clip(np.random.normal(intensity, 0.05 * intensity), 0, 2))
    dur = float(max(duration + np.random.normal(0, 0.1e-6), 1e-9))
    return {"intensity": inten, "duration_s": dur, "adc": int(inten * 4095)}


def simulate_galvo_scan(area: Tuple[float, float] = (10.0, 10.0),
                        step: float = 0.5) -> List[Tuple[float, float]]:
    """List of (x, y) coordinates visited by a raster scan (µm)."""
    xs = int(area[0] / step); ys = int(area[1] / step)
    return [(x * step, y * step) for x in range(xs) for y in range(ys)]


def make_camera_frame(centres: Sequence[dict], shape: Tuple[int, int] = (480, 640),
                      sigma: float = 5.0, background: float = 20.0,
                      fluorescence_factor: float = 1.0) -> np.ndarray:
    """Render a fluorescence frame with Poisson shot noise."""
    frame = np.random.normal(background, 5.0, shape)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    for c in centres:
        x, y, inten = c["x"], c["y"], c["intensity"] * fluorescence_factor
        frame += inten * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))
    frame = np.random.poisson(np.clip(frame, 0, None))
    return np.clip(frame, 0, 255).astype(np.uint8)


def detect_defects(frame: np.ndarray, threshold: int = 50) -> List[dict]:
    """Locate bright defects in a frame (OpenCV contours or NumPy fallback)."""
    if _HAS_CV2:
        _, th = cv2.threshold(frame, threshold, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for cnt in contours:
            m = cv2.moments(cnt)
            if m["m00"] > 0:
                cx = int(m["m10"] / m["m00"]); cy = int(m["m01"] / m["m00"])
                out.append({"x": cx, "y": cy, "intensity": float(frame[cy, cx])})
        return out
    # pure-NumPy fallback: local maxima above threshold
    out = []
    ys, xs = np.where(frame > threshold)
    seen = set()
    for y, x in zip(ys, xs):
        key = (y // 10, x // 10)
        if key in seen:
            continue
        seen.add(key)
        out.append({"x": int(x), "y": int(y), "intensity": float(frame[y, x])})
    return out


@dataclass
class QubitSite:
    qubit_id: int
    row: int
    col: int
    x_um: float
    y_um: float
    intensity: float
    t1: float          # s
    t2: float          # s
    readout_err: float
    usable: bool = True

    def as_dict(self) -> dict:
        d = asdict(self)
        d["t1_ms"] = self.t1 * 1e3
        d["t2_us"] = self.t2 * 1e6
        return d


def auto_align(profile: DefectProfile | str = "hBN-VB",
                grid: int = 8, galvo_area: Tuple[float, float] = (20.0, 20.0),
                temperature_k: float = 300.0, max_retries: int = 5,
                rng: Optional[np.random.Generator] = None,
                progress=None) -> List[QubitSite]:
    """Detect and register qubit sites on the chip, returning ``grid**2`` sites.

    Mirrors the calibration flow of the real hardware (fire lasers, capture a
    frame, locate defects, map to a grid) but here driven purely by the
    simulator physics so it is deterministic and fast.
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    rng = rng or np.random.default_rng()
    t1, t2 = coherence_at_temperature(prof, temperature_k)
    shape = (96, 128)  # small frame keeps calibration fast; grid cell ~12x16 px
    n_needed = grid * grid
    sites: List[QubitSite] = []
    task = None
    if progress is not None:
        task = progress.add_task("Aligning spin defects...", total=max_retries)
    for attempt in range(max_retries):
        area = galvo_area
        density = prof.defect_density_per_um2
        n_emitters = int(rng.uniform(5, max(6, density * area[0] * area[1] * 4)))
        margin_x = max(8, shape[1] // 8)
        margin_y = max(8, shape[0] // 8)
        centres = [{
            "x": rng.uniform(margin_x, shape[1] - margin_x),
            "y": rng.uniform(margin_y, shape[0] - margin_y),
            "intensity": rng.uniform(60, 220),
        } for _ in range(n_emitters)]
        frame = make_camera_frame(centres, shape, fluorescence_factor=prof.fluorescence_factor)
        detected = detect_defects(frame, threshold=50)
        # de-duplicate and map to grid cells
        cells: Dict[Tuple[int, int], dict] = {}
        for d in detected:
            cr = int(d["y"] / shape[0] * grid)
            cc = int(d["x"] / shape[1] * grid)
            cr = min(max(cr, 0), grid - 1); cc = min(max(cc, 0), grid - 1)
            key = (cr, cc)
            if key not in cells or d["intensity"] > cells[key]["intensity"]:
                cells[key] = d
        # build the full grid in index order; pad empty cells with dark sites
        sites = []
        for idx in range(n_needed):
            r, c = divmod(idx, grid)
            d = cells.get((r, c))
            if d:
                x_um = d["x"] * area[0] / shape[1]
                y_um = d["y"] * area[1] / shape[0]
                # Optical readout error anchored to the defect profile's nominal
                # value, mildly modulated by site brightness (brighter emitters
                # read out a little more reliably). Bounded 0.7x..1.3x nominal so
                # a dim site can never collapse the error far below the physical
                # readout floor -- the old unbounded sigmoid shrank it ~10x
                # (detected intensities cluster near the detection threshold).
                bright = 1.0 / (1.0 + math.exp((100.0 - d["intensity"]) / 20.0))
                err = prof.readout_error * (1.3 - 0.6 * bright)
                sites.append(QubitSite(
                    qubit_id=idx, row=r, col=c,
                    x_um=x_um, y_um=y_um, intensity=d["intensity"],
                    t1=t1, t2=t2, readout_err=err,
                    usable=d["intensity"] > 100 and t2 > 1e-6,
                ))
            else:
                sites.append(QubitSite(qubit_id=idx, row=r, col=c,
                                       x_um=0.0, y_um=0.0, intensity=0.0,
                                       t1=t1, t2=t2, readout_err=prof.readout_error,
                                       usable=False))
        if progress is not None and task is not None:
            progress.update(task, advance=1)
        if sum(s.usable for s in sites) >= max(8, n_needed // 2):
            break
    return sites[:n_needed]


# ---------------------------------------------------------------------------
# ODMR and coherence
# ---------------------------------------------------------------------------

def simulate_odmr(profile: DefectProfile | str = "hBN-VB",
                  freqs_ghz: Optional[np.ndarray] = None,
                  b_field_t: float = 1e-3, linewidth_mhz: float = 5.0,
                  contrast_depth: float = 0.03) -> Tuple[np.ndarray, np.ndarray]:
    """Analytic ODMR spectrum with hyperfine replicas.

    Builds the dip pattern for a spin-1 defect coupled to ``n_equivalent_nuclei``
    equivalent nuclei of spin ``nuclear_spin``.  The hyperfine fingerprint is the
    n-fold convolution of the single-nucleus line set: each nucleus contributes
    ``2I+1`` sublevels at ``A*mi + Q*mi**2``, so n equivalent nuclei produce
    ``(2I+1)**n`` (weighted, often degenerate) lines.

    * V_B- in hBN: 3 equivalent 14N (I=1) -> the characteristic **7-line** pattern
      (degeneracies 1:3:6:7:6:3:1, spacing A/h ~ 47 MHz).  This is the V_B-
      fingerprint (Gottscholl 2020).
    * NV in diamond: 1 14N -> 3 lines (current behaviour, preserved).
    * V_Si in SiC: 1 29Si (I=1/2) -> 2 lines.

    The two electron spin transitions (ms=0 <-> +-1) are Zeeman-split by gamma*B.
    Returns frequencies (GHz) and contrast (0..1, dips), with the deepest line at
    ``contrast_depth``.
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    if freqs_ghz is None:
        f0 = prof.d_ghz
        freqs_ghz = np.linspace(f0 - 0.08, f0 + 0.08, 400)
    D = prof.zero_field_splitting
    ge = prof.gyromagnetic_ratio
    A = prof.hyperfine_coupling
    Q = prof.quadrupole_splitting
    I = prof.nuclear_spin
    n = max(1, int(getattr(prof, "n_equivalent_nuclei", 1)))
    # two electron transitions shifted by the Zeeman term
    transitions = [D + ge * b_field_t, D - ge * b_field_t]

    # single-nucleus sublevels m_i in {-I, -I+1, ..., I}
    n_sub = int(round(2 * I)) + 1
    sub = [-I + k for k in range(n_sub)]

    # build the n-fold convolution of hyperfine offsets; cap the combinatorial
    # blow-up (our profiles use n<=3 -> <=27 combos, well within the guard).
    n_combos = n_sub ** n
    if n_combos > 512:  # pragma: no cover - defensive, no profile hits this
        offsets = [A * mi + Q * mi * mi for mi in sub]
    else:
        from itertools import product as _product
        offsets = []
        for combo in _product(sub, repeat=n):
            m_tot = sum(combo)
            q_tot = sum(mi * mi for mi in combo)
            offsets.append(A * m_tot + Q * q_tot)
    # degeneracy of the most-populated line (amplitude normalisation so the
    # deepest dip == contrast_depth and relative intensities are correct,
    # e.g. 1:3:6:7:6:3:1 for V_B-).
    from collections import Counter as _Counter
    _c = _Counter(round(o, 3) for o in offsets)
    d_max = max(_c.values()) if _c else 1

    contrast = np.ones_like(freqs_ghz)
    sigma = linewidth_mhz * 1e6
    f_hz = freqs_ghz * 1e9
    for ft in transitions:
        for off in offsets:
            centre = ft + off
            amp = contrast_depth / d_max
            dip = amp * np.exp(-((f_hz - centre) ** 2) / (2 * sigma ** 2))
            contrast = contrast - dip
    contrast = np.clip(contrast, 0.0, 1.0)
    return freqs_ghz, contrast


def xy8_decoupling(n_blocks: int = 1) -> List[str]:
    """Return the XY8 pulse sequence (8 pulses per block) as gate labels."""
    block = ["X", "Y", "X", "Y", "Y", "X", "Y", "X"]
    return block * max(1, int(n_blocks))


def estimate_coherence_xy8(t2: float, n_blocks: int = 1, tau_s: float = 1e-6) -> float:
    """Toy estimator of the extended coherence under XY8 (exponential gain)."""
    return t2 * (2 ** n_blocks) * (1 - math.exp(-tau_s / max(t2, 1e-12)))