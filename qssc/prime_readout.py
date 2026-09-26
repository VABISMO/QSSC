"""Prime-coprime frequency-multiplexed readout.

Each addressable qubit channel in a pixel is driven by a pulse train whose
modulation period is a DISTINCT PRIME number of base-clock ticks ``tau``:

    T_i = p_i * tau          (p_i prime)

Because distinct primes are pairwise COPRIME, two such trains never
constructively realign within their LCM (= p_a * p_b * tau): over the LCM
window the pair (t mod p_a, t mod p_b) takes every residue pair exactly once
(Chinese Remainder Theorem), so the cross-correlation of two zero-mean
periodic trains factors into a product of zero means and is EXACTLY zero.
That is the orthogonality the "pulsos que no se pueden solapar por la longitud
de su periodo" intuition rests on: prime periods are mutually orthogonal
symbol channels with zero cross-talk over the LCM, not merely "small" cross-
talk.

The receiver, knowing the only valid modulation periods are the prime set
{p_i}, projects the received fluorescence onto exactly those prime trains
(lock-in demodulation at each p_i) and rejects every component at a non-prime
period as noise. This is a matched-filter / coprime denoiser: it rejects
OUT-of-band noise (energy at non-prime periods). In-band noise (noise that
happens to land inside a prime channel's linewidth) still passes -- the
rejection is by the band-rejection factor, not total. The prime prior is a
decomposition constraint, not magic.

Grounding (qssc.defects.NV_DIAMOND, the only numbers used here):
  T2      = 1.8e-3 s   (Hahn echo, 12C-enriched synthetic, 300 K)
  linewidth = 5.0e6 Hz  (ODMR FWHM; a prime channel must modulate slower than
                        1/linewidth so the drive stays inside the ODMR line)
  detuning spread = 2.0e6 Hz (per-NV resonance fingerprint)

A channel is usable only if its modulation period fits between the inverse
linewidth (the NV's spectral filter) and T2 (the coherence window):

    1 / linewidth  <  T_i = p_i * tau  <  T2

i.e. 1/(linewidth*tau) < p_i < T2/tau. The "best prime for that nanodiamond"
is the SMALLEST usable prime (the most lock-in cycles fit in the readout
window -> best SNR) that is still above the linewidth floor and not already
assigned to a neighbour. The resonance selects WHICH NV responds; the prime
period selects the orthogonal CHANNEL -- they are independent handles.

NOTE: 16 prime symbols give 16 ADDRESS CHANNELS (16 NVs/sub-ensembles per
pixel), NOT a 16-state qudit. A single NV electron spin is 2-level (mS=0,
mS=+-1); 14N is spin-1 (3-level); 13C is spin-1/2 (2-level). No single NV is
a 16-level system. "16 symbols" = 16 frequency-multiplexed NVs, not a wider
qudit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# The first 16 primes -- 16 orthogonal symbol channels. Pairwise coprime by
# construction (every pair of distinct primes is coprime), so their LCM is the
# product and the CRT orthogonality above holds for every pair.
PRIME_SYMBOLS: tuple[int, ...] = (
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53,
)


@dataclass
class PrimeChannel:
    """One prime-period address channel assigned to an NV in a pixel."""

    nv_index: int          # which NV in the pixel this channel drives
    prime: int             # the prime p_i (period = p_i * tau)
    period_ns: float       # T_i = p_i * tau
    mod_freq_hz: float     # 1 / T_i (the lock-in demodulation frequency)
    n_cycles: float        # readout_window / T_i (lock-in averaging cycles)


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0:
        return False
    for f in range(3, int(math.isqrt(n)) + 1, 2):
        if n % f == 0:
            return False
    return True


def usable_primes(linewidth_hz: float, t2_ns: float, tau_ns: float,
                  max_symbols: int = 16) -> list[int]:
    """Return the prime channels that fit between the linewidth floor and T2.

    A channel is usable iff  1/linewidth < p*tau < T2, i.e. the modulation
    stays inside the ODMR line (slower than 1/linewidth) AND faster than T2
    (coherence survives one cycle). Returns the subset of ``PRIME_SYMBOLS``
    (the first ``max_symbols`` primes) that fit, sorted ascending (smallest =
    most lock-in cycles = best SNR first).
    """
    if linewidth_hz <= 0 or t2_ns <= 0 or tau_ns <= 0:
        return []
    floor = 1.0 / (linewidth_hz * tau_ns * 1e-9)   # p_min: T = 1/linewidth
    ceil = t2_ns / tau_ns                          # p_max: T = T2
    out = [p for p in PRIME_SYMBOLS[:max_symbols] if floor < p < ceil]
    return sorted(out)


def n_resolvable_prime_channels(linewidth_hz: float, t2_ns: float,
                                tau_ns: float, max_symbols: int = 16) -> int:
    """How many of the 16 prime symbol channels are usable for this profile."""
    return len(usable_primes(linewidth_hz, t2_ns, tau_ns, max_symbols))


def best_prime_for_nv(linewidth_hz: float, t2_ns: float, tau_ns: float,
                      taken: set[int] | None = None) -> int | None:
    """The best prime for a nanodiamond: the SMALLEST usable prime not already
    assigned to a neighbour.

    Smallest = most lock-in cycles fit in the readout window (best SNR), while
    staying above the linewidth floor (resolvable). The resonance selects
    which NV responds; the prime selects the orthogonal channel -- the "best
    prime for that nanodiamond" is the channel whose period maximises the
    cycle count without smearing the drive outside the ODMR line. Returns
    None if no prime fits (T2 too short / linewidth too wide for this tau).
    """
    taken = taken or set()
    for p in usable_primes(linewidth_hz, t2_ns, tau_ns):
        if p not in taken:
            return p
    return None


def assign_prime_channels(n_nv: int, linewidth_hz: float, t2_ns: float,
                          tau_ns: float, readout_window_ns: float
                          ) -> list[PrimeChannel]:
    """Assign a distinct prime channel to each of ``n_nv`` NVs in a pixel.

    Greedy: NV 0 gets the smallest usable prime (best SNR), NV 1 the next,
    etc. If there are more NVs than usable primes, channels are reused with
    maximum separation (round-robin over the cycle). Each channel's period
    is p*tau, its modulation frequency 1/(p*tau), and its lock-in cycle count
    is readout_window / (p*tau).
    """
    usable = usable_primes(linewidth_hz, t2_ns, tau_ns)
    if not usable or n_nv <= 0:
        return []
    channels: list[PrimeChannel] = []
    for i in range(n_nv):
        p = usable[i % len(usable)]   # round-robin reuse if n_nv > n_channels
        period_ns = p * tau_ns
        channels.append(PrimeChannel(
            nv_index=i, prime=p, period_ns=float(period_ns),
            mod_freq_hz=1.0e9 / period_ns,
            n_cycles=readout_window_ns / period_ns))
    return channels


def prime_orthogonality(prime_a: int, prime_b: int,
                        n_lcm_cycles: int = 1) -> float:
    """Normalized cross-correlation of the two prime-channel lock-in
    references (cosines at f_i = 1/(p_i*tau)) over n*LCM ticks.

    Each channel's lock-in reference is the zero-mean sinusoid at its
    modulation frequency 1/(p_i*tau). Two DISTINCT frequencies are
    EXACTLY orthogonal over any window that is a whole number of BOTH
    periods -- and coprime primes give LCM = p_a*p_b, so over the LCM both
    complete a whole number of cycles and the integral is exactly 0
    (standard Fourier orthogonality). For two EQUAL primes the references
    coincide -> 1.0. (We use the sinusoidal reference, not an odd-period
    +/-1 square wave, because an odd-length square wave is not zero-mean
    over its period and the CRT factoring into 0*0 only holds for zero-mean
    references; the physical lock-in measures the fundamental sinusoid
    anyway.)
    """
    if prime_a == prime_b:
        return 1.0
    if not (_is_prime(prime_a) and _is_prime(prime_b)):
        return float("nan")
    lcm = prime_a * prime_b  # coprime
    n = lcm * max(1, n_lcm_cycles)
    t = np.arange(n)
    ref_a = np.cos(2.0 * np.pi * t / prime_a)
    ref_b = np.cos(2.0 * np.pi * t / prime_b)
    # both are zero-mean with RMS 1/sqrt(2) over the window
    rms = 0.5
    return float(np.dot(ref_a, ref_b) / (n * rms))


def prime_decompose(signal: np.ndarray, tau_ns: float,
                    primes: list[int]) -> dict[int, float]:
    """Lock-in demodulate a fluorescence time series onto each prime channel.

    For each prime p_i the channel amplitude is the in-phase lock-in
    projection:  mean(signal * cos(2*pi*t/p_i)) * 2, over the largest whole
    number of p_i-tick cycles that fit in the signal (the lock-in
    accumulator). Returns {prime: amplitude}. Energy at non-prime periods
    does not project onto any channel -- it is the rejected noise. A pure
    A*cos(2*pi*t/p) input projects to amplitude A on channel p and ~0 on
    every other prime channel (the orthogonality).
    """
    s = np.asarray(signal, dtype=float)
    n = s.size
    out: dict[int, float] = {}
    for p in primes:
        period = p
        n_cycles = n // period
        if n_cycles < 1:
            out[p] = 0.0
            continue
        usable = n_cycles * period
        t = np.arange(usable)
        ref = np.cos(2.0 * np.pi * t / period)
        out[p] = 2.0 * float(np.dot(s[:usable], ref) / usable)
    return out


def prime_denoise(signal: np.ndarray, tau_ns: float,
                  primes: list[int]) -> np.ndarray:
    """Reconstruct the clean (prime-only) part of a signal and reject the rest.

    Demodulates each prime channel (lock-in projection) and resynthesizes the
    sum of the prime cosines at the recovered amplitudes. Returns the clean
    reconstruction; the removed energy = ||signal - clean|| is the out-of-band
    noise the prime prior rejected. Only energy at the prime modulation
    frequencies survives.
    """
    s = np.asarray(signal, dtype=float)
    n = s.size
    amps = prime_decompose(s, tau_ns, primes)
    clean = np.zeros(n)
    for p, a in amps.items():
        period = p
        n_cycles = n // period
        if n_cycles < 1:
            continue
        usable = n_cycles * period
        t = np.arange(usable)
        ref = np.cos(2.0 * np.pi * t / period)
        clean[:usable] += a * ref
    return clean


def prime_noise_rejection(n_symbols: int, n_noise_bins: int) -> float:
    """Out-of-band noise-rejection factor of the prime prior.

    With ``n_symbols`` prime channels out of ``n_noise_bins`` total frequency
    bins in the analysis window, the prior "only prime periods are valid"
    rejects the fraction (1 - n_symbols/n_noise_bins) of broadband noise power
    (the energy outside the prime bins). Returns the SNR improvement factor
    = n_noise_bins / n_symbols (the noise power that survives is the in-bin
    fraction). This is the band-rejection gain; in-band noise (inside a
    prime channel's linewidth) is NOT rejected -- it passes through.
    """
    if n_symbols <= 0 or n_noise_bins <= 0:
        return 1.0
    if n_symbols >= n_noise_bins:
        return 1.0
    return float(n_noise_bins) / float(n_symbols)


def cross_talk_residual(prime_a: int, prime_b: int, window_ticks: int) -> float:
    """The finite-window cross-talk between two prime channels when the
    integration window is NOT a whole LCM.

    Over an exact LCM the cross-correlation is 0 (Fourier orthogonality of the
    two sinusoidal references). Over a sub-LCM or non-multiple window the
    residual is O(1/window) -- the uncompleted cycle leaves a leakage. Returns
    the absolute normalized residual. This quantifies how far off-LCM the
    integration must be before two prime channels start to bleed into each
    other.
    """
    if prime_a == prime_b:
        return 1.0
    lcm = prime_a * prime_b
    n = min(window_ticks, lcm * 4)   # cap to keep it bounded + meaningful
    t = np.arange(n)
    ref_a = np.cos(2.0 * np.pi * t / prime_a)
    ref_b = np.cos(2.0 * np.pi * t / prime_b)
    return abs(float(np.dot(ref_a, ref_b) / (n * 0.5)))