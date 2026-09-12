"""Invention 5: tunable register width via field gradient.

A field gradient across the pixel shifts adjacent microdiamonds' ensemble ODMR
lines apart. The resolvable register width becomes a MEASURED quantity (Case D:
line spacing > linewidth), capped at the resolvable count. With no gradient the
register keeps its nominal ``n_nv_per_pixel`` width.
"""
from __future__ import annotations

import math

from qssc.simulator import (auto_align, N_NV_PER_PIXEL,
                            resolvable_register_width)
from qssc.backend import QSSCBackend
from qssc.defects import get_profile


def test_resolvable_register_width_formula():
    """The helper returns the Case D line count: gradient*pixel / linewidth,
    floored, at least 1 (a flat ensemble still yields one qubit)."""
    prof = get_profile("NV")
    lw_mhz = prof.odmr_linewidth_hz * 1e-6
    # gradient of 1 linewidth per um over a 200 um pixel -> 200 lines (capped
    # later by n_nv_per_pixel in auto_align; the helper itself is unbounded).
    assert resolvable_register_width(1.0, 200.0, lw_mhz) == math.floor(200.0 / lw_mhz)
    # flat / degenerate inputs collapse to one qubit
    assert resolvable_register_width(0.0, 200.0, lw_mhz) == 1
    assert resolvable_register_width(1.0, 200.0, 0.0) == 1


def test_gradient_caps_register_width():
    """A strong gradient caps the register below the nominal 10: only as many
    NVs as the gradient resolves (Case D) are placed on the frequency grid."""
    prof = get_profile("NV")
    lw_mhz = prof.odmr_linewidth_hz * 1e-6
    # gradient that resolves exactly 4 lines across the pixel
    grad = (4 * lw_mhz) / 200.0
    sites = auto_align(prof, register=True, n_nv_per_pixel=N_NV_PER_PIXEL,
                       gradient_mhz_per_um=grad, pixel_size_um=200.0,
                       rng=__import__("numpy").random.default_rng(0))
    assert len(sites) == 4, f"gradient should cap register to 4, got {len(sites)}"
    # all share one pixel, distinct nv_index
    assert {s.pixel_id for s in sites} == {0}
    assert len({s.nv_index for s in sites}) == 4
    # adjacent lines sit ~one linewidth apart (Case D threshold), broadened by
    # the per-NV inhomogeneous detuning jitter (frequency_detuning_spread_hz).
    freqs = sorted(s.frequency for s in sites)
    spacings = [freqs[i + 1] - freqs[i] for i in range(len(freqs) - 1)]
    import statistics
    mean_spacing = statistics.mean(spacings)
    assert abs(mean_spacing - prof.odmr_linewidth_hz) < 2.5e6, (
        f"mean gradient line spacing {mean_spacing} != linewidth "
        f"{prof.odmr_linewidth_hz}")
    assert all(sp > 0 for sp in spacings), "a line spacing is non-positive"


def test_no_gradient_keeps_nominal_width():
    """With no gradient the register keeps its nominal n_nv_per_pixel (10)."""
    sites = auto_align(get_profile("NV"), register=True, n_nv_per_pixel=10,
                       gradient_mhz_per_um=0.0)
    assert len(sites) == 10


def test_backend_reports_gradient_and_width():
    """The backend describe() surfaces the gradient + measured width, and the
    Target width matches the capped register."""
    prof = get_profile("NV")
    lw_mhz = prof.odmr_linewidth_hz * 1e-6
    grad = (3 * lw_mhz) / 200.0  # resolves 3 lines
    b = QSSCBackend("NV", seed=42, noise_mode="real",
                    gradient_mhz_per_um=grad, pixel_size_um=200.0)
    desc = b.describe()
    assert desc["gradient_mhz_per_um"] == grad
    assert desc["n_nv_per_pixel"] == 3
    assert b.target.num_qubits == 3
    # the limitation text names the gradient + measured width
    lim = " ".join(desc["limitations"])
    assert "gradient" in lim.lower() and "measured" in lim.lower()


def test_flat_backend_unaffected():
    """A gradient=0 backend behaves exactly as before (nominal 10-NV register)."""
    b = QSSCBackend("NV", seed=42, noise_mode="real", gradient_mhz_per_um=0.0)
    assert b.n_nv_per_pixel == N_NV_PER_PIXEL
    assert b.target.num_qubits == N_NV_PER_PIXEL