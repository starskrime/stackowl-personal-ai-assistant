"""Short-lived, self-signed WebTransport certificates (AD-14).

Unlike `ca.py`'s CA-chained HTTPS leaf (997-day ceiling, verified by chain
+ SPKI pinning), a WebTransport certificate is pinned directly by the
browser via `serverCertificateHashes` -- there is no chain to validate at
all, so it is never signed by `ca.py`'s CA and carries no SAN. Chrome
requires such a certificate be valid for at most 14 days; this module
enforces that ceiling in code, not just by convention.

`WebTransportCertStore` keeps a `current` (the cert actively loaded into
the aioquic listener's TLS configuration) and a `next` (pre-generated, not
yet active) pair in memory only -- exactly like every other secret this kit
handles, nothing here is ever written to disk. Serving both hashes from the
signed snapshot route (`server.py`) and rotating by promoting `next` to
`current` is what AD-14 means by "current and next hashes ... rotated with
overlap": a client that fetched `next`'s hash before a rotation can still
connect once it becomes `current`, with no window where a hash it holds is
invalid.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

# Chrome's ceiling for a WebTransport `serverCertificateHashes` certificate
# (also this story's own AC) -- `generate()` refuses anything longer.
MAX_VALIDITY_DAYS = 14


@dataclass(frozen=True)
class WebTransportCert:
    cert_pem: bytes
    key_pem: bytes
    cert_der: bytes
    sha256_hash: bytes
    not_valid_before: datetime.datetime
    not_valid_after: datetime.datetime

    @property
    def sha256_hash_b64(self) -> str:
        return base64.b64encode(self.sha256_hash).decode()


def _build_cert(common_name: str, key: ec.EllipticCurvePrivateKey, *, validity_days: int) -> x509.Certificate:
    not_before = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)
    not_after = not_before + datetime.timedelta(days=validity_days)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)  # self-signed: issuer == subject, no CA involved at all
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    return builder.sign(key, hashes.SHA256())


def generate(common_name: str, *, validity_days: int = MAX_VALIDITY_DAYS) -> WebTransportCert:
    """A fresh, self-signed (non-CA-chained) ECDSA P-256 certificate, valid
    for at most `MAX_VALIDITY_DAYS`. Raises `ValueError` if a caller ever
    asks for longer, or for zero/negative days (which would produce a
    certificate whose `not_valid_after` precedes or equals
    `not_valid_before`) -- AD-14's 14-day ceiling is enforced here, not left
    to every caller to remember."""
    if validity_days > MAX_VALIDITY_DAYS:
        raise ValueError(f"WebTransport cert validity must be <= {MAX_VALIDITY_DAYS} days, got {validity_days}")
    if validity_days <= 0:
        raise ValueError(f"WebTransport cert validity must be > 0 days, got {validity_days}")
    key = ec.generate_private_key(ec.SECP256R1())
    cert = _build_cert(common_name, key, validity_days=validity_days)
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    return WebTransportCert(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
        key_pem=key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        cert_der=cert_der,
        # `serverCertificateHashes` pins the SHA-256 of the DER CERTIFICATE
        # itself (not its SPKI, unlike ca.py's chain-pinning check.py does
        # for HTTPS) -- this is the exact hash Chrome recomputes and compares.
        sha256_hash=hashlib.sha256(cert_der).digest(),
        not_valid_before=cert.not_valid_before_utc,
        not_valid_after=cert.not_valid_after_utc,
    )


class WebTransportCertStore:
    """Holds the `current` (loaded into the live aioquic TLS configuration)
    and `next` (pre-generated, not yet active) certs, both in memory only."""

    def __init__(self, common_name: str, *, validity_days: int = MAX_VALIDITY_DAYS) -> None:
        self._common_name = common_name
        self._validity_days = validity_days
        self.current = generate(common_name, validity_days=validity_days)
        self.next = generate(common_name, validity_days=validity_days)

    def rotate(self) -> WebTransportCert:
        """Promotes `next` to `current` and generates a fresh `next`.
        Callers that hold a live aioquic `QuicConfiguration` must reload its
        certificate/key from the new `current` right after calling this
        (see `webtransport_server.WebTransportListener.rotate_certificate`)
        -- this method only updates the in-memory pair."""
        self.current = self.next
        self.next = generate(self._common_name, validity_days=self._validity_days)
        return self.current

    def hashes_b64(self) -> list[str]:
        """current+next SHA-256 hashes, base64 -- the shape served by the
        signed cert-hash snapshot route (AD-14: "delivered only over the
        authenticated HTTPS snapshot")."""
        return [self.current.sha256_hash_b64, self.next.sha256_hash_b64]


__all__ = ["MAX_VALIDITY_DAYS", "WebTransportCert", "WebTransportCertStore", "generate"]
