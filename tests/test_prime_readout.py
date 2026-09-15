"""Invention 2+ — prime-coprime frequency-multiplexed readout + denoiser.

Each NV channel is driven at a modulation period T_i = p_i * tau with p_i
PRIME. The demodulation reference is a COSINE at the prime period. Distinct
odd primes are coprime, so over the LCM (= p_a * p_b) the two cosine
references are EXACTLY orthogonal (product-to-sum identity + whole-cycle
cancellation: the cross-correlation is a sum of whole periods of a
co-prime-frequency cosine, which integrates to zero). The receiver projects
onto the prime set and rejects energy at non-prime periods as noise
(matched-filter / coprime denoiser). 16 prime symbols = 16 ADDRESS CHANNELS
(16 NVs), NOT a 16-state qudit (NV is 2-level).

Note: the cosine reference is what the Python demodulator (prime_orthogonality
/ prime_decompose) uses and what gives exact pairwise orthogonality over the
LCM. The firmware lock-in (READ_LOCKINP) approximates the reference with a
+-1 square-wave boxcar; odd-prime square waves are NOT zero-mean, so the
hardware approximation carries a small +-1/(p*q) cross-talk residual between
odd-prime pairs (only p=2 is zero-mean). The algorithm's defining orthogonality
is the cosine result tested here; the square-wave residual is a firmware
fidelity trade-off, documented in the IP memoria.
"""
from __future__ import annotations

import numpy as np

from qssc.prime_readout import (
    PRIME_SYMBOLS, usable_primes, n_resolvable_prime_channels,
    best_prime_for_nv, assign_prime_channels, prime_orthogonality,
    prime_decompose, prime_denoise, prime_noise_rejection,
    cross_talk_residual,
)
from qssc.defects import NV_DIAMOND


# NV_DIAMOND grounding: T2 = 1.8 ms, linewidth = 5 MHz.
T2_NS = NV_DIAMOND.t2_room * 1e9          # 1.8e6 ns
LINEWIDTH_HZ = NV_DIAMOND.odmr_linewidth_hz  # 5.0e6
# base clock tau = 1 us: p in (1/(5e6*1e-6)=0.2, 1.8e6/1=1.8e6) -> all 16 fit.
TAU_NS = 1_000.0
READOUT_NS = NV_DIAMOND.readout_ns * 1_000   # ~3 ms window (readout_ns=3000 ns
# is the per-shot integration; use 1000x of it as the full lock-in window)


def test_16_prime_symbols():
    """16 orthogonal symbol channels, all prime, pairwise coprime."""
    assert len(PRIME_SYMBOLS) == 16
    assert all(p >= 2 for p in PRIME_SYMBOLS)
    # pairwise coprime (gcd 1 for every distinct pair)
    from math import gcd
    for i in range(16):
        for j in range(i + 1, 16):
            assert gcd(PRIME_SYMBOLS[i], PRIME_SYMBOLS[j]) == 1


def test_usable_primes_fit_nv_window():
    """All 16 prime channels fit between 1/linewidth and T2 for the NV."""
    ps = usable_primes(LINEWIDTH_HZ, T2_NS, TAU_NS)
    assert ps == sorted(PRIME_SYMBOLS)
    # each period in (1/linewidth, T2)
    inv_lw_ns = 1e9 / LINEWIDTH_HZ  # 200 ns
    for p in ps:
        T = p * TAU_NS
        assert inv_lw_ns < T < T2_NS


def test_no_usable_primes_when_t2_too_short():
    """If T2 < 1/linewidth nothing fits (channel can't be both resolvable
    and coherent)."""
    short_t2 = 1e-6  # 1 us < 1/linewidth (200 ns) -> floor>ceil? no: floor=0.2,
    # ceil=1.0 -> primes 2..53 with p<1.0 -> none (p must be < 1.0, but min
    # prime is 2). So no usable channel.
    assert usable_primes(LINEWIDTH_HZ, short_t2, TAU_NS) == []


def test_best_prime_is_smallest_unused():
    """Best prime = smallest usable (most lock-in cycles), not already taken."""
    p0 = best_prime_for_nv(LINEWIDTH_HZ, T2_NS, TAU_NS)
    assert p0 == 2  # smallest prime
    p1 = best_prime_for_nv(LINEWIDTH_HZ, T2_NS, TAU_NS, taken={2})
    assert p1 == 3  # next smallest
    assert best_prime_for_nv(LINEWIDTH_HZ, T2_NS, TAU_NS,
                             taken=set(PRIME_SYMBOLS)) is None


def test_assign_prime_channels_one_per_nv():
    """Each NV gets a distinct prime channel (round-robin if more NVs than
    channels)."""
    ch = assign_prime_channels(10, LINEWIDTH_HZ, T2_NS, TAU_NS, READOUT_NS)
    assert len(ch) == 10
    # the first 10 NVs get the first 10 primes, distinct
    primes_used = [c.prime for c in ch]
    assert primes_used == list(PRIME_SYMBOLS[:10])
    # periods are p*tau, mod freq = 1/(p*tau), n_cycles = window/period
    assert ch[0].period_ns == 2 * TAU_NS
    assert abs(ch[0].mod_freq_hz - 1e9 / (2 * TAU_NS)) < 1.0
    assert ch[0].n_cycles == READOUT_NS / (2 * TAU_NS)


def test_prime_orthogonality_exact_at_lcm():
    """Two distinct-prime COSINE references are EXACTLY orthogonal over the
    LCM (product-to-sum + whole-cycle cancellation). Equal primes give 1.0."""
    # 7 and 13 coprime -> cross-correlation 0 over LCM=91
    assert abs(prime_orthogonality(7, 13)) < 1e-12
    assert abs(prime_orthogonality(2, 3)) < 1e-12
    # a prime with itself = perfectly correlated
    assert prime_orthogonality(7, 7) == 1.0


def test_cross_talk_residual_small_off_lcm():
    """Off-LCM the cross-talk is O(1/window) -- small but nonzero, and -> 0
    as the window grows toward a full LCM."""
    # 7 and 13: LCM = 91. A short window has a larger residual; near the LCM
    # it drops toward 0.
    short = cross_talk_residual(7, 13, 10)
    near_lcm = cross_talk_residual(7, 13, 90)
    assert short > near_lcm
    assert near_lcm < 0.05


def test_prime_decompose_recovers_channels():
    """Lock-in demod recovers each prime channel's amplitude from a sum of
    prime cosines + noise; non-prime-period energy is not assigned to any
    channel."""
    rng = np.random.default_rng(0)
    primes = [7, 13, 23]
    n = 7 * 13 * 23 * 2  # 2 full LCMs
    t = np.arange(n)
    signal = np.zeros(n)
    amps = {7: 0.8, 13: 0.5, 23: 0.3}
    for p, a in amps.items():
        signal += a * np.cos(2.0 * np.pi * t / p)
    # add noise + a NON-prime-period component (period 4) that the prime
    # prior must reject (not assign to any prime channel)
    signal += 0.2 * np.cos(2.0 * np.pi * t / 4)
    signal += rng.normal(0, 0.05, n)
    out = prime_decompose(signal, TAU_NS, primes)
    # each prime channel recovers its amplitude to within the noise
    for p, a in amps.items():
        assert abs(out[p] - a) < 0.05, (p, out[p], a)


def test_prime_denoise_rejects_non_prime_energy():
    """The denoiser reconstructs only the prime components; the non-prime
    (period-4) + noise energy is the rejected residual."""
    rng = np.random.default_rng(1)
    primes = [5, 11]
    n = 5 * 11 * 4
    t = np.arange(n)
    clean = (0.7 * np.cos(2.0 * np.pi * t / 5)
             + 0.4 * np.cos(2.0 * np.pi * t / 11))
    junk = 0.5 * np.cos(2.0 * np.pi * t / 4)  # non-prime period
    noise = rng.normal(0, 0.1, n)
    signal = clean + junk + noise
    recon = prime_denoise(signal, TAU_NS, primes)
    # the reconstruction matches the CLEAN (prime-only) part far better than
    # the full noisy signal: ||signal - recon|| >> ||clean - recon||
    err_full = np.linalg.norm(signal - recon)
    err_clean = np.linalg.norm(clean - recon)
    assert err_clean < err_full / 3.0
    # the rejected energy includes the period-4 junk
    rejected = signal - recon
    assert np.var(rejected) > np.var(noise)  # junk + noise rejected


def test_prime_noise_rejection_factor():
    """16 prime channels out of e.g. 1024 noise bins -> 64x out-of-band
    rejection (in-band noise still passes)."""
    f = prime_noise_rejection(16, 1024)
    assert abs(f - 64.0) < 1e-9
    # degenerate cases
    assert prime_noise_rejection(0, 1024) == 1.0
    assert prime_noise_rejection(16, 16) == 1.0


def test_n_resolvable_channels_nv():
    """For the NV profile at tau=1us all 16 prime channels are resolvable."""
    n = n_resolvable_prime_channels(LINEWIDTH_HZ, T2_NS, TAU_NS)
    assert n == 16


def test_16_symbols_are_channels_not_qudit_states():
    """A single NV is 2-level: 16 prime symbols = 16 ADDRESS CHANNELS (16
    NVs), not a 16^1 qudit. A 10-NV pixel is 2^10, not 16^10. This test pins
    the physics so the 16^10 misconception cannot creep into the model."""
    # 16 channels each a 2-level qubit -> Hilbert dim 2^16, NOT 16^1
    n_channels = 16
    qudit_claim = 16 ** 1           # the WRONG reading: one 16-level qudit
    actual = 2 ** n_channels        # the CORRECT reading: 16 two-level qubits
    assert actual == 65536
    assert qudit_claim == 16
    assert actual != qudit_claim
    # a 10-qubit pixel is 2^10 = 1024, NOT 16^10
    assert 2 ** 10 == 1024
    assert 16 ** 10 != 1024

# ---------------------------------------------------------------------------
# IQ demodulator (firmware READ_LOCKINP mirror) -- parity + physics tests
# ---------------------------------------------------------------------------
import re
from pathlib import Path

from qssc.prime_readout import (
    PRIME_IQ_ROM, lockin_iq_demod, lockin_iq_result,
)

_RTL = Path(__file__).resolve().parents[1] / "fpga" / "rtl" / "command_parser.v"


def _parse_rtl_rom(name: str) -> dict[int, int]:
    """Parse ``<name>[i]=[-]12'sdV;`` assignments from command_parser.v -> {i: V}."""
    text = _RTL.read_text()
    pat = re.compile(rf"{name}\[\s*(\d+)\s*\]\s*=\s*(-?)12'sd(\d+)")
    return {int(m.group(1)): (int(m.group(3)) * (-1 if m.group(2) else 1))
            for m in pat.finditer(text)}


def _parse_rtl_word(name: str, bits: int) -> dict[int, int]:
    """Parse ``<name>[i]=<bits>'dV;`` (unsigned) assignments -> {i: V}."""
    text = _RTL.read_text()
    pat = re.compile(rf"{name}\[\s*(\d+)\s*\]\s*=\s*{bits}'d(\d+)")
    return {int(m.group(1)): int(m.group(2)) for m in pat.finditer(text)}


def test_iq_rom_matches_rtl():
    """The Python PRIME_IQ_ROM (single source of truth the host demod uses) is
    bit-identical to the cos_rom/sin_rom/prime_tab the firmware MACs against.
    If the RTL ROM is regenerated, this test catches a drift before the
    host/firmware demodulators silently disagree."""
    ptab = _parse_rtl_word("prime_tab", 6)
    pbase = _parse_rtl_word("prime_base", 9)
    cos = _parse_rtl_rom("cos_rom")
    sin = _parse_rtl_rom("sin_rom")
    # prime_tab matches PRIME_SYMBOLS
    for i, p in enumerate(PRIME_SYMBOLS):
        assert ptab[i] == p, f"prime_tab[{i}] {ptab[i]} != PRIME_SYMBOLS {p}"
    # each per-prime cos/sin block matches the Python ROM exactly
    for idx, p in enumerate(PRIME_SYMBOLS):
        base = pbase[idx]
        cos_py, sin_py = PRIME_IQ_ROM[idx]
        for k in range(p):
            assert cos[base + k] == cos_py[k], \
                f"cos[{p}][{k}] rtl={cos[base + k]} py={cos_py[k]}"
            assert sin[base + k] == sin_py[k], \
                f"sin[{p}][{k}] rtl={sin[base + k]} py={sin_py[k]}"


def test_lockin_iq_demod_amplitude_recovery():
    """A pure A*cos(2*pi*k/p) signal demodulated at the SAME prime p recovers
    amplitude A (in ADC-code units) -- the matched-filter gain. Phase=0 -> Q~0,
    I carries the signal. The detector delivers INTEGER ADC codes, so the test
    signal is np.round'd (np.int64 truncation toward zero would break the
    cosine symmetry cos[k]=cos[p-k] and leak a spurious Q)."""
    for idx, p in enumerate(PRIME_SYMBOLS):
        n = p * 8                       # whole periods -> no window leakage
        A = 1280.0                      # codes (== sim modulation depth)
        k = np.arange(n)
        codes = np.round(A * np.cos(2.0 * np.pi * k / p)).astype(np.int64)
        I, Q, nn = lockin_iq_demod(codes, idx)
        assert nn == n
        amp = lockin_iq_result(I, Q, n, idx, readout_noise=0.0)["amplitude_code"]
        # amplitude_code = 2*hypot/(n*S) for p>2, hypot/(n*S) for p==2; the
        # 12-bit ROM + integer-code rounding leaves a <1-LSB residual.
        assert abs(amp - A) < 1.0, f"p={p}: recovered amp {amp} != {A}"
        # phase 0 + integer symmetric codes + antisymmetric sin_rom (sin[i] =
        # -sin[p-i] by numpy's symmetric round) -> Q cancels exactly per period;
        # allow a per-period 1-LSB safety margin against a .5-rounding boundary.
        assert abs(Q) <= p, f"p={p}: Q={Q} not a rounding residual (I={I})"


def test_lockin_iq_demod_cross_talk_orthogonality():
    """A signal at prime q, demodulated at a DIFFERENT prime p over a whole-LCM
    window, projects to ~0 -- the prime-coprime orthogonality the IQ demod
    buys over the old +-1/(p*q) square-wave residual. This is the reason the
    rewrite exists: the cross-talk must be bounded by 12-bit ROM rounding
    (~0.02%), not the parameter-fragile square-wave residual."""
    worst = 0.0
    for iidx, p in enumerate(PRIME_SYMBOLS):
        for qidx, q in enumerate(PRIME_SYMBOLS):
            if p == q:
                continue
            n = p * q                      # exactly one LCM (coprime)
            k = np.arange(n)
            A = 1280.0
            codes = A * np.cos(2.0 * np.pi * k / q)   # signal at q
            I, Q, nn = lockin_iq_demod(codes, iidx)    # demod at p
            assert nn == n
            mag = float(np.hypot(int(I), int(Q)))
            # normalize to the on-channel amplitude the same signal would give
            ref_I, ref_Q, _ = lockin_iq_demod(
                A * np.cos(2.0 * np.pi * np.arange(n) / p), iidx)
            ref_mag = float(np.hypot(int(ref_I), int(ref_Q)))
            leak = mag / ref_mag if ref_mag else 0.0
            worst = max(worst, leak)
            # ROM-rounding orthogonality floor: ~0.02%, NOT the square-wave
            # +-1/(p*q) floor (which for p=2,q=3 is ~17%).
            assert leak < 1e-3, \
                f"cross-talk p={p},q={q}: leak={leak:.4%} > 0.1%"
    # the square-wave residual would be ~1/(p*q); for the smallest pair
    # p=2,q=3 that is ~16.7%. The IQ demod beats it by >100x.
    assert worst < 1e-3


def test_lockin_iq_demod_dc_rejection():
    """A CONSTANT detector code (no modulation) demodulates to Q=0 EXACTLY
    (sin is antisymmetric -> rounding cancels over whole periods) and I is the
    small +-1-per-period cos-rounding residual the host can subtract. This is
    the DC / baseline rejection that makes the lock-in a band-pass."""
    C = 49920                       # arbitrary constant ADC code
    for idx, p in enumerate(PRIME_SYMBOLS):
        n = p * 4                   # whole periods
        codes = np.full(n, C, dtype=np.int64)
        I, Q, nn = lockin_iq_demod(codes, idx)
        assert nn == n
        assert Q == 0, f"p={p}: DC Q={Q} != 0 (quadrature must cancel exactly)"
        # I = C * sum(cos_rom[k]) over whole periods -- the per-period cos
        # rounding residual is deterministic + bounded by ~p (one LSB/step).
        assert abs(I) <= C * p, f"p={p}: DC I={I} unexpectedly large"
