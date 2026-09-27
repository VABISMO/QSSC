# qssc/simulator_site.py
"""Qubit-site calibration + ODMR spectrum model.

Extracted from :mod:`qssc.simulator` (pure move, zero behavior change):
:class:`QubitSite`, the per-pixel register-width resolver, the chip/register
calibration (:func:`auto_align`) and the analytic ODMR spectrum
(:func:`simulate_odmr`). The shared readout constants live in
:mod:`qssc.simulator_constants`; the detector response model is imported from
:mod:`qssc.simulator_response`. ``qssc.simulator`` re-exports every name here.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .defects import DefectProfile, coherence_at_temperature, get_profile
from .simulator_constants import (
    BG_CODE, BRIGHT_CODE, DETECT_THRESHOLD, N_NV_PER_PIXEL, SITE_FAB_YIELD,
)
from .simulator_response import make_detector_response


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
    frequency: float = 0.0   # ms=0<->ms=+-1 transition frequency (Hz), per-site
    usable: bool = True
    # Pixel-register identity (qssc-spin-gate-not-photon: the 2q dipole gate is
    # WITHIN one microdiamond). ``pixel_id`` is which of the 64 die pixels this
    # NV lives in; ``nv_index`` is which NV within that pixel's register. For
    # the chip view (register=False) each site is one pixel (pixel_id == qubit_id,
    # nv_index 0); for the register view (register=True) all sites share one
    # pixel_id and differ by nv_index.
    pixel_id: int = 0
    nv_index: int = 0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["t1_ms"] = self.t1 * 1e3
        d["t2_us"] = self.t2 * 1e6
        d["frequency_ghz"] = self.frequency / 1e9
        return d


def resolvable_register_width(gradient_mhz_per_um: float,
                              pixel_um: float,
                              linewidth_mhz: float) -> int:
    """How many ensemble qubits a field gradient makes resolvable in one pixel.

    A field gradient shifts adjacent microdiamonds' ensemble ODMR lines apart
    by ``gradient * spacing``. The lines are resolvable only while their
    separation exceeds the inhomogeneous linewidth (Case D in the ODMR sim:
    separation > linewidth, else they collapse to one). The total frequency
    spread across the pixel is ``gradient_mhz_per_um * pixel_um``; the number of
    lines that fit side-by-side at one linewidth each is that spread over the
    linewidth. Returns at least 1 (a flat ensemble still yields one qubit).
    """
    if gradient_mhz_per_um <= 0.0 or linewidth_mhz <= 0.0:
        return 1
    return max(1, int(math.floor(gradient_mhz_per_um * pixel_um / linewidth_mhz)))


def auto_align(profile: DefectProfile | str = "NV",
                grid: int = 8, site_pitch_um: float = 2500.0,
                temperature_k: float = 300.0, max_retries: int = 5,
                rng: Optional[np.random.Generator] = None,
                progress=None,
                register: bool = False,
                n_nv_per_pixel: int = N_NV_PER_PIXEL,
                register_line_spacing_hz: float = 6.0e6,
                gradient_mhz_per_um: float = 0.0,
                pixel_size_um: float = 200.0,
                register_line_freqs_hz: Optional[Sequence[float]] = None,
                ) -> List[QubitSite]:
    """Calibrate the chip and build the qubit-site list.

    Two modes:

    * ``register=False`` (chip view, the historical default): mirror the on-chip
      calibration flow (protocol §5.6) and register ``grid**2`` pixel sites --
      one qubit site per die pixel, the 64-pixel hardware view used by
      ``describe()``, the Calibrate tab, the magneto/field sensors and the
      FPGA 64-channel readout. Each site is a pixel (``pixel_id == qubit_id``).

    * ``register=True`` (per-pixel register view, used by the circuit backend):
      build ONE representative pixel's ``n_nv_per_pixel`` NV register -- the
      qubits the QSSCBackend actually simulates, since 64x10 = 640 exceeds the
      63-qubit MPS cap. The NVs share ``pixel_id=0`` (one microdiamond), differ
      by ``nv_index``, and sit on a resolvable ODMR frequency grid
      (``register_line_spacing_hz`` > linewidth) so the global MW antenna
      addresses each by frequency. This is the within-pixel all-to-all dipole
      register (the 300 K on-chip 2q gate, Neumann 2008 / Dolde 2013); the 64
      pixels are independent at 300 K.

    ``site_pitch_um`` is the centre-to-centre pitch of one emitter/detector
    pixel (2.5 mm die pitch = 2500 um; audit #137 -- the prior 2.5 um default
    was a 1000x unit error, sub-wavelength and absurd). In the chip view a site
    is registered as usable when it actually contains defects *and* the
    fabrication step succeeded: the per-site yield is
    ``SITE_FAB_YIELD * (1 - exp(-density * site_area))`` -- a Poisson "at least
    one defect in the pixel" factor times a fabrication-yield factor. This
    replaces the old ``galvo_area`` camera-era leftover, whose
    ``density * galvo_area`` always clipped to 1.0 and made the
    ``usable=False`` branch dead. With the Poisson model the not-usable branch
    is live: e.g. at 300 K an NV site (5/um^2 over 6.25 um^2) is ~always
    populated but still loses ~10% to fab yield, while a sparse hBN V_B- site
    (0.30/um^2) is empty ~15% of the time.

    Returns ``grid**2`` (chip view) or ``n_nv_per_pixel`` (register view)
    :class:`QubitSite` in index order; chip-view sites that fail the yield
    draw are kept as ``usable=False`` placeholders so the grid is always
    complete. Register-view NVs are usable by construction (engineered NVs).
    """
    prof = get_profile(profile) if isinstance(profile, str) else profile
    rng = rng or np.random.default_rng()
    t1, t2 = coherence_at_temperature(prof, temperature_k)
    # ---- per-pixel NV register view (the circuit backend's qubits) ----------
    # ONE microdiamond's n NVs: all pixel_id=0, distinct nv_index, on a
    # resolvable ODMR frequency grid so the global MW antenna addresses each by
    # frequency (Omega << detuning -> see qssc.dipole.addressing_leakage).
    # Within-pixel all-to-all dipole coupling is applied by the backend's
    # _neighbor_pairs (complete graph); these NVs are ~3 nm apart.
    #
    # A field gradient makes the register width a MEASURED
    # quantity: several microdiamonds share a pixel volume, the gradient shifts
    # their ensemble ODMR lines apart, and the resolvable count is set by the
    # Case D rule (separation > linewidth). With gradient_mhz_per_um > 0 the line
    # spacing is derived from the gradient (so neighbouring particles sit one
    # resolvable linewidth apart) and n is capped at that resolvable width.
    if register:
        sites: List[QubitSite] = []
        n = max(1, int(n_nv_per_pixel))
        line_spacing_hz = register_line_spacing_hz
        # MEASURED ODMR line centres (from decompose_odmr in the calibration)
        # override both the uniform grid and the gradient-derived spacing: the
        # register sits on the real resolvable frequency positions, and the
        # measured line count IS the register width N (it overrides
        # n_nv_per_pixel). Priority: measured lines > gradient > uniform 6 MHz.
        measured_freqs = (list(register_line_freqs_hz)
                          if register_line_freqs_hz else None)
        if measured_freqs is not None:
            n = max(1, len(measured_freqs))
        if gradient_mhz_per_um > 0.0 and measured_freqs is None:
            linewidth_mhz = prof.odmr_linewidth_hz * 1e-6
            # the gradient shifts the ensemble ODMR line by gradient*d MHz per um
            # of spatial offset; across the pixel the spread is gradient*pixel.
            # One resolvable linewidth per line -> spacing_hz = gradient*spacing,
            # i.e. one linewidth per particle. Use the per-particle pitch that
            # tiles the pixel with resolvable lines:
            resolvable = resolvable_register_width(
                gradient_mhz_per_um, pixel_size_um, linewidth_mhz)
            n = min(n, resolvable)
            # spacing between adjacent lines = one linewidth (Case D threshold),
            # expressed in Hz. This is the gradient-induced separation at the
            # per-particle pitch (gradient * pitch == linewidth).
            line_spacing_hz = prof.odmr_linewidth_hz
        x_um = 0.5 * site_pitch_um
        y_um = 0.5 * site_pitch_um
        for k in range(n):
            spread = prof.frequency_detuning_spread_hz
            jitter = (float(rng.normal(0.0, spread))
                      if spread > 0 else 0.0)
            if measured_freqs is not None and k < len(measured_freqs):
                # measured ODMR line centre (the truth from decompose_odmr) +
                # small per-NV strain jitter. The register uses the MEASURED
                # frequency grid, not a hardcoded 6 MHz spacing.
                freq = float(measured_freqs[k]) + jitter
            else:
                detune = (k - (n - 1) / 2.0) * line_spacing_hz + jitter
                freq = prof.zero_field_splitting + detune
            sites.append(QubitSite(
                qubit_id=k, row=0, col=0,
                x_um=x_um, y_um=y_um, intensity=float(BRIGHT_CODE),
                t1=t1, t2=t2, readout_err=prof.readout_error,
                frequency=freq, usable=t2 > 1e-6,
                pixel_id=0, nv_index=k,
            ))
        return sites
    # ---- chip view: the 64-pixel hardware grid (historical default) ----------
    n_needed = grid * grid
    site_area_um2 = site_pitch_um * site_pitch_um
    # Poisson ">=1 defect in the pixel" times a fabrication-yield factor.
    p_defect = 1.0 - math.exp(-prof.defect_density_per_um2 * site_area_um2)
    p_usable = float(np.clip(SITE_FAB_YIELD * p_defect, 0.0, 1.0))
    task = None
    if progress is not None:
        task = progress.add_task("Scanning on-chip detector array...", total=max_retries)
    sites: List[QubitSite] = []
    for idx in range(n_needed):
        r, c = divmod(idx, grid)
        # Per-site transition-frequency detuning (microwave addressability):
        # real NV sites are not all on the 2.87 GHz ZFS -- local strain + local
        # B + the 14N hyperfine shift detune each site by ~MHz. That detuning
        # is the individual-addressing mechanism (a global MW antenna hits only
        # the site whose detuning brings it into the ODMR linewidth). Drawn
        # from N(0, spread), clipped to +/-2*spread; 0 spread -> no detuning
        # (all sites identical, back-compat).
        spread = prof.frequency_detuning_spread_hz
        if spread > 0:
            detune = float(np.clip(rng.normal(0.0, spread), -2.0 * spread, 2.0 * spread))
        else:
            detune = 0.0
        freq = prof.zero_field_splitting + detune
        coupled = rng.random() < p_usable
        if coupled:
            code = int(make_detector_response({idx}, prof, rng=rng, n_sites=n_needed)[idx])
            # readout error anchored to the profile nominal value, mildly modulated
            # by site brightness (brighter emitters read out a little more
            # reliably). Bounded 0.7x..1.3x nominal so a dim site can never
            # collapse the error far below the physical readout floor.
            bright = float(np.clip((code - BG_CODE) / max(BRIGHT_CODE - BG_CODE, 1), 0.0, 1.0))
            err = prof.readout_error * (1.3 - 0.6 * bright)
            x_um = (c + 0.5) * site_pitch_um
            y_um = (r + 0.5) * site_pitch_um
            sites.append(QubitSite(
                qubit_id=idx, row=r, col=c,
                x_um=x_um, y_um=y_um, intensity=float(code),
                t1=t1, t2=t2, readout_err=err,
                frequency=freq,
                usable=code > DETECT_THRESHOLD and t2 > 1e-6,
                pixel_id=idx, nv_index=0,
            ))
        else:
            sites.append(QubitSite(qubit_id=idx, row=r, col=c,
                                   x_um=0.0, y_um=0.0, intensity=0.0,
                                   t1=t1, t2=t2, readout_err=prof.readout_error,
                                   frequency=freq, usable=False,
                                   pixel_id=idx, nv_index=0))
    if progress is not None and task is not None:
        progress.update(task, completed=max_retries)
    return sites[:n_needed]


# ---------------------------------------------------------------------------
# ODMR and coherence
# ---------------------------------------------------------------------------

def simulate_odmr(profile: DefectProfile | str = "NV",
                  freqs_ghz: Optional[np.ndarray] = None,
                  b_field_t: float = 1e-3, linewidth_mhz: float = 5.0,
                  contrast_depth: float = 0.03,
                  gradient_mhz_per_um: float = 0.0,
                  pixel_size_um: float = 200.0,
                  n_nv_per_pixel: int = 10) -> Tuple[np.ndarray, np.ndarray]:
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

    **Field gradient:** with ``gradient_mhz_per_um > 0`` the N NVs
    in one pixel spread across distinct ensemble lines. Each NV at
    position ``r_i`` (uniform across the ``pixel_size_um`` pixel) sees a
    position-dependent field, so its resonance shifts by
    ``gradient_mhz_per_um * r_i``. The single hyperfine pattern is replicated at
    ``n_nv_per_pixel`` uniformly spaced positions, spreading the lines across
    ``gradient_mhz_per_um * pixel_size_um`` MHz. When that spread exceeds the
    linewidth the lines resolve (Case D) and ``decompose_odmr`` counts them --
    turning the per-pixel register width into a *measured* quantity. With no
    gradient the spectrum is unchanged (one ensemble line).
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

    # Field-gradient spread. Without a gradient the N NVs share one
    # ensemble line (one position -> one shift of 0). With a gradient the NVs sit
    # at uniform positions across the pixel; each copy of the hyperfine pattern is
    # shifted by gradient * position, so the lines spread and resolve.
    if gradient_mhz_per_um > 0.0 and n_nv_per_pixel > 1:
        n_nv = max(2, int(n_nv_per_pixel))
        # uniform positions across [0, pixel_size_um] (centred so the spread is
        # symmetric about D, matching the no-gradient centre).
        positions_um = np.linspace(-pixel_size_um / 2.0, pixel_size_um / 2.0, n_nv)
        shifts_hz = (gradient_mhz_per_um * positions_um) * 1e6
        # each NV carries 1/n of the ensemble contrast so the integrated dip is
        # conserved whether the lines overlap (no gradient) or separate.
        nv_amp = 1.0 / n_nv
    else:
        shifts_hz = [0.0]
        nv_amp = 1.0

    contrast = np.ones_like(freqs_ghz)
    # ``linewidth_mhz`` is the ODMR FWHM (callers -- e.g. sensors.compass_measure
    # -- pass the profile ``odmr_linewidth_hz`` documented as FWHM).  Convert
    # FWHM -> Gaussian sigma (2*sqrt(2*ln2) ~= 2.355), matching the ODMR line
    # shape in simulator._odmr_sweep / _run_all_odmr and odmr_decompose (which
    # all model the ODMR line as a Gaussian with FWHM = linewidth).  Using FWHM
    # directly as sigma made the rendered compass / CLI ODMR curves 2.355x too
    # wide (actual FWHM ~= 11.77 MHz instead of 5 MHz); the sensitivity maths
    # use ``lw`` as FWHM directly and were already correct.
    sigma = linewidth_mhz * 1e6 / (2.0 * math.sqrt(2.0 * math.log(2.0)))
    f_hz = freqs_ghz * 1e9
    for shift in shifts_hz:
        for ft in transitions:
            for off in offsets:
                centre = ft + off + float(shift)
                amp = (contrast_depth / d_max) * nv_amp
                dip = amp * np.exp(-((f_hz - centre) ** 2) / (2 * sigma ** 2))
                contrast = contrast - dip
    contrast = np.clip(contrast, 0.0, 1.0)
    return freqs_ghz, contrast