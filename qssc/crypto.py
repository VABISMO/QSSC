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