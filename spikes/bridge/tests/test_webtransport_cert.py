"""Unit tests for bridge_spike.webtransport_cert: validity window, hash
computation, and current/next rotation overlap (AD-14)."""

from __future__ import annotations

import datetime
import hashlib

import pytest
from bridge_spike import webtransport_cert
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

INSTALL_NAME = "test-webtransport.local"


def test_generated_cert_is_valid_for_at_most_14_days() -> None:
    cert = webtransport_cert.generate(INSTALL_NAME)
    delta = cert.not_valid_after - cert.not_valid_before
    assert delta <= datetime.timedelta(days=webtransport_cert.MAX_VALIDITY_DAYS)


def test_generate_refuses_validity_longer_than_the_ceiling() -> None:
    with pytest.raises(ValueError, match="MAX_VALIDITY_DAYS|<= 14"):
        webtransport_cert.generate(INSTALL_NAME, validity_days=webtransport_cert.MAX_VALIDITY_DAYS + 1)


def test_generated_cert_is_ecdsa_p256() -> None:
    cert = webtransport_cert.generate(INSTALL_NAME)
    x509_cert = x509.load_pem_x509_certificate(cert.cert_pem)
    public_key = x509_cert.public_key()
    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    assert isinstance(public_key.curve, ec.SECP256R1)


def test_generated_cert_is_self_signed_not_ca_chained() -> None:
    """No CA involvement at all -- issuer == subject, unlike ca.py's leaf."""
    cert = webtransport_cert.generate(INSTALL_NAME)
    x509_cert = x509.load_pem_x509_certificate(cert.cert_pem)
    assert x509_cert.issuer == x509_cert.subject
    # Raises InvalidSignature if this cert was not signed by its own key.
    x509_cert.public_key().verify(
        x509_cert.signature, x509_cert.tbs_certificate_bytes, ec.ECDSA(x509_cert.signature_hash_algorithm)
    )


def test_sha256_hash_matches_the_der_certificate_digest() -> None:
    cert = webtransport_cert.generate(INSTALL_NAME)
    assert cert.sha256_hash == hashlib.sha256(cert.cert_der).digest()
    assert len(cert.sha256_hash) == 32


def test_store_starts_with_two_distinct_certs() -> None:
    store = webtransport_cert.WebTransportCertStore(INSTALL_NAME)
    assert store.current.sha256_hash != store.next.sha256_hash
    hashes = store.hashes_b64()
    assert len(hashes) == 2
    assert len(set(hashes)) == 2


def test_rotate_promotes_next_to_current_with_overlap() -> None:
    """AD-14's "current and next hashes ... rotated with overlap": the hash
    a client already holds as `next` BEFORE a rotation must still be valid
    (as the new `current`) immediately AFTER it."""
    store = webtransport_cert.WebTransportCertStore(INSTALL_NAME)
    next_hash_before = store.next.sha256_hash_b64
    new_current = store.rotate()
    assert new_current.sha256_hash_b64 == next_hash_before
    assert store.current.sha256_hash_b64 == next_hash_before
    # A fresh, distinct "next" was minted -- the pair never collapses to one.
    assert store.next.sha256_hash_b64 != store.current.sha256_hash_b64


def test_rotate_returns_the_new_current() -> None:
    store = webtransport_cert.WebTransportCertStore(INSTALL_NAME)
    returned = store.rotate()
    assert returned is store.current


def test_repeated_rotation_never_reuses_a_cert() -> None:
    store = webtransport_cert.WebTransportCertStore(INSTALL_NAME)
    seen = {store.current.sha256_hash_b64, store.next.sha256_hash_b64}
    for _ in range(5):
        store.rotate()
        seen.add(store.current.sha256_hash_b64)
        seen.add(store.next.sha256_hash_b64)
    assert len(seen) > 2
