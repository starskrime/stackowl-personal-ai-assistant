"""Ephemeral CA + leaf certificate issuance for the Bridge TLS spike (AD-14, NFR23).

Generates a throwaway ECDSA P-256 certificate authority, signs exactly one
server ("leaf") certificate for the install's mDNS host name, and drops the
CA private key from memory before returning. The CA private key is never
written to disk and is not reachable from anywhere once `setup()`/`renew()`
return — it exists only as a local variable inside this module's functions.
"""

from __future__ import annotations

import datetime
import gc
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# ≤825 days per the story's AC. 397 mirrors common CA/Browser Forum leaf
# practice; the CA itself gets the full ceiling since it only ever signs once.
LEAF_VALIDITY_DAYS = 397
CA_VALIDITY_DAYS = 825


@dataclass(frozen=True)
class CAArtifacts:
    """Everything a caller may keep after `setup()`/`renew()`.

    Deliberately has no field for the CA private key — that key never leaves
    the function that generated it.
    """

    install_name: str
    ca_cert_pem: bytes
    leaf_cert_pem: bytes
    leaf_key_pem: bytes
    fingerprint: str
    not_valid_after: datetime.datetime


def fingerprint(cert: x509.Certificate) -> str:
    """SHA-256 fingerprint of `cert`, colon-hex upper-case, e.g. `AA:BB:...`."""
    digest = cert.fingerprint(hashes.SHA256())
    return ":".join(f"{byte:02X}" for byte in digest)


def _build_ca_cert(ca_key: ec.EllipticCurvePrivateKey, install_name: str) -> x509.Certificate:
    not_before = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)
    not_after = not_before + datetime.timedelta(days=CA_VALIDITY_DAYS)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Bridge Spike Ephemeral CA ({install_name})")])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
    )
    return builder.sign(ca_key, hashes.SHA256())


def _sign_leaf_cert(
    ca_key: ec.EllipticCurvePrivateKey,
    ca_cert: x509.Certificate,
    leaf_key: ec.EllipticCurvePrivateKey,
    install_name: str,
) -> x509.Certificate:
    not_before = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)
    not_after = not_before + datetime.timedelta(days=LEAF_VALIDITY_DAYS)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, install_name)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(install_name)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=True,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
    )
    return builder.sign(ca_key, hashes.SHA256())


def _cert_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def setup(install_name: str) -> CAArtifacts:
    """Generate a fresh ephemeral CA, sign one leaf cert, then drop the CA key.

    The CA private key exists only in this function's local scope. It is
    explicitly `del`eted and the interpreter is asked to collect it before
    this function returns, so no caller can ever observe or persist it.
    """
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = _build_ca_cert(ca_key, install_name)
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_cert = _sign_leaf_cert(ca_key, ca_cert, leaf_key, install_name)

    artifacts = CAArtifacts(
        install_name=install_name,
        ca_cert_pem=_cert_pem(ca_cert),
        leaf_cert_pem=_cert_pem(leaf_cert),
        leaf_key_pem=_key_pem(leaf_key),
        fingerprint=fingerprint(ca_cert),
        not_valid_after=leaf_cert.not_valid_after_utc,
    )

    del ca_key
    gc.collect()

    return artifacts


def renew(install_name: str) -> CAArtifacts:
    """Issue a brand-new CA + leaf pair.

    Old leaf certs keep working only until devices re-run the trust ceremony
    against the new CA fingerprint — this function does not revoke anything,
    it just mints a fresh chain the same way `setup()` does.
    """
    return setup(install_name)
