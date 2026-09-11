# qssc/crypto.py
"""Quantum-seeded cryptographic key + certificate generation for the QSSC.

The QSSC chip's spin-defect measurements are a source of random bits.  This
module turns a block of quantum-derived entropy into usable cryptographic
material:

* a 256-bit symmetric key (hex),
* an Ed25519 private/public keypair deterministically derived from a 32-byte
  quantum seed (Ed25519 private keys *are* 32 random bytes), and
* a self-signed X.509 certificate (Ed25519) suitable as a device identity
  certificate for the chip / for TLS mutual auth in the lab network.

The quantum entropy itself is produced by :func:`api.runner.run_circuit` from
ODMR/qubit measurements (see the ``qrand`` algorithm).  Here we only consume
the bytes — any bias in the raw quantum stream should already have been
removed by a randomness extractor (von Neumann) upstream, so the seed passed
in is expected to be close to uniform.
"""
from __future__ import annotations

import datetime


def _crypto_available() -> bool:
    try:
        import cryptography  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def derive_keypair(entropy: bytes, common_name: str = "QSSC-quantum-device",
                   days: int = 365) -> dict:
    """Derive a symmetric key, an Ed25519 keypair and a self-signed cert.

    ``entropy`` must be at least 32 bytes (the Ed25519 seed); any extra bytes
    are used for the certificate serial number.  Returns a dict of PEM strings
    plus the raw seed (hex).  Raises ``ValueError`` if ``cryptography`` is not
    installed or the entropy is too short.
    """
    if not _crypto_available():
        raise ValueError("the 'cryptography' package is required for key/"
                         "certificate generation (pip install cryptography)")
    if len(entropy) < 32:
        raise ValueError("need >=32 bytes of quantum entropy for an Ed25519 seed")

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    seed = entropy[:32]
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    pub = priv.public_key()

    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")

    # serial number from the extra entropy: positive and <= 159 bits per
    # RFC 5280 (a 20-byte INTEGER with the high bit clear). Modulo 2**159-1
    # then +1 keeps the result in [1, 2**159-1]; the old `% 2**159` + 1 could
    # yield exactly 2**159 (a 160-bit value) when the residue was 2**159-1.
    extra = entropy[32:52] if len(entropy) >= 52 else entropy[32:] + b"\x00" * 20
    serial = (int.from_bytes(extra[:20], "big") % (2 ** 159 - 1)) + 1

    cn = (common_name or "QSSC-quantum-device").strip()[:64] or "QSSC-quantum-device"
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    # naive UTC keeps compatibility across cryptography versions
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(pub)
        .serial_number(serial)
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=int(days)))
        .sign(priv, None)  # Ed25519 takes None as the hash algorithm
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")

    return {
        "seed_hex": seed.hex(),
        "symmetric_key_hex": entropy[:32].hex(),
        "private_key_pem": priv_pem,
        "public_key_pem": pub_pem,
        "certificate_pem": cert_pem,
        "common_name": cn,
        # serial returned as a hex string: the full <=159-bit ASN.1 integer is
        # used inside the cert, but the int itself exceeds BSON/int64 (max
        # ~9.2e18) so it cannot be persisted to MongoDB as a number. Storing it
        # as hex keeps the async-job result serialisable without truncating the
        # certificate's real serial number.
        "serial": format(serial, "x"),
        "algorithm": "Ed25519",
        "validity_days": int(days),
    }


def von_neumann_extract(bits: list[int], need: int) -> tuple[list[int], dict]:
    """Von Neumann randomness extractor.

    Consumes pairs of (biased but independent) bits: ``01`` -> 0, ``10`` -> 1,
    ``00``/``11`` -> discarded.  Produces ~uniform output bits regardless of
    the per-bit bias, at the cost of throughput.  Returns the extracted bits
    (length ``need`` if enough raw bits were available) and a small stats dict.
    """
    out: list[int] = []
    pairs = 0
    kept = 0
    i = 0
    n = len(bits)
    while i + 1 < n and len(out) < need:
        a, b = bits[i], bits[i + 1]
        pairs += 1
        if a == 0 and b == 1:
            out.append(0); kept += 1
        elif a == 1 and b == 0:
            out.append(1); kept += 1
        i += 2
    rate = (kept / pairs) if pairs else 0.0
    return out, {"raw_bits": n, "pairs": pairs, "kept": kept,
                 "extract_rate": round(rate, 4),
                 "sufficient": len(out) >= need}


def bits_to_bytes(bits: list[int]) -> bytes:
    """Pack a list of 0/1 bits (MSB first) into bytes, left-justified."""
    n = len(bits)
    nbytes = (n + 7) // 8
    val = 0
    for b in bits:
        val = (val << 1) | (b & 1)
    # left-justify if not a whole multiple of 8
    val <<= (8 * nbytes - n)
    return val.to_bytes(nbytes, "big")


def bytes_to_bits(data: bytes) -> list[int]:
    """Expand bytes into a list of 0/1 bits, MSB first (inverse of
    :func:`bits_to_bytes`). Used by the QRNG consumers to run the health
    gate over a harvested byte block."""
    return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]


def entropy_health(bits: list[int]) -> dict:
    """Lightweight sanity gate for a random bit stream (issue #46).

    The von-Neumann extractor debiases *iid* streams only: a correlated or
    degenerate input can still produce output whose per-pair extraction
    stats look healthy.  This gate checks the two cheapest failure
    signatures of the extracted bits:

    * frequency (monobit): the ones fraction vs 0.5, as a two-sided
      binomial p-value, and
    * lag-1 transitions: P(bit[i+1] != bit[i]) vs 0.5, which iid uniform
      bits satisfy (a stream with strong sample-to-sample correlation
      drifts away from 0.5 or collapses to 0/1).

    ``passed`` is a *sanity* verdict that rejects the degenerate cases
    (constant, alternating, strongly biased or correlated streams) before
    they become an Ed25519 seed.  It is **not** a NIST SP 800-90B health
    test and cannot certify physical randomness — it is the cheap guard
    the docs promise, nothing more.
    """
    import math

    n = len(bits)
    if n < 64:
        return {"n": n, "passed": False, "reason": "too few bits (<64)"}
    ones = sum(b & 1 for b in bits)
    p1 = ones / n
    # frequency: 2*ones - n is ~ Normal(0, n) under iid uniform
    z_freq = abs(2 * ones - n) / math.sqrt(n)
    freq_p = math.erfc(z_freq / math.sqrt(2))          # two-sided
    # lag-1 transitions under iid uniform ~ Binomial(n-1, 0.5)
    n_pairs = max(1, n - 1)
    trans = sum(1 for i in range(n_pairs) if (bits[i] & 1) != (bits[i + 1] & 1))
    z_trans = abs(2 * trans - n_pairs) / math.sqrt(n_pairs)
    trans_p = math.erfc(z_trans / math.sqrt(2))
    min_entropy = (-math.log2(max(p1, 1.0 - p1)) if 0.0 < p1 < 1.0 else 0.0)
    # The p-value thresholds are deliberately loose (1e-4 ~ 3.9 sigma for a
    # two-sided binomial) for a *sanity* gate: the job is to reject degenerate
    # streams (constant, alternating, strongly biased/correlated), not to fail
    # a legitimate fair draw that happens to land 3.5 sigma from 0.5 -- which a
    # 1e-3 threshold did ~0.1% of the time per 128-bit block. The min_entropy
    # floor (>= 0.25 bit) independently rejects constant/strongly-biased streams
    # (p1 > 0.84 or < 0.16), and the lag-1 test rejects correlated streams
    # (trans_p ~ 1e-29 for alternating input). Combined with the harvest retry
    # in api.runner / SimulatedFPGA._harvest_entropy the false-reject rate is
    # negligible; a degenerate stream still fails every retry and is rejected.
    passed = (freq_p >= 1e-4 and trans_p >= 1e-4 and min_entropy >= 0.25)
    return {"n": n, "ones": ones, "p1": round(p1, 4),
            "freq_p": round(freq_p, 4),
            "transition_rate": round(trans / n_pairs, 4),
            "trans_p": round(trans_p, 4),
            "min_entropy_per_bit": round(min_entropy, 3),
            "passed": bool(passed)}