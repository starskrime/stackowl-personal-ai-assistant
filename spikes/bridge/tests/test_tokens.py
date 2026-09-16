"""Bearer-token issuance and signed-request verification (AD-17, FR67-68):
every failure -- unknown token, bad/replayed nonce, stale timestamp, bad
signature, a copied token with no signature at all -- must be
indistinguishable from every other one in what the caller receives back.
"""

from __future__ import annotations

import base64
import hashlib
import time

import pytest
from bridge_spike import tokens as tokens_module
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as ec_utils


def _new_device_keypair() -> tuple[ec.EllipticCurvePrivateKey, bytes]:
    from cryptography.hazmat.primitives import serialization

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_key, public_der


def _raw_signature(private_key: ec.EllipticCurvePrivateKey, message: bytes) -> bytes:
    """WebCrypto's ECDSA signatures are raw `r || s` (32 bytes each), not
    the DER `cryptography`'s own `.sign()` produces -- this mirrors what a
    real browser's device key would send."""
    der_signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = ec_utils.decode_dss_signature(der_signature)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _signed_request(
    tokens: tokens_module.TokenStore,
    nonces: tokens_module.NonceStore,
    private_key: ec.EllipticCurvePrivateKey,
    token: str,
    method: str = "GET",
    path: str = "/api/whoami",
    body: bytes = b"",
    *,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> tokens_module.Device:
    nonce = nonce or nonces.issue()
    timestamp = timestamp or str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}".encode()
    signature_b64 = base64.b64encode(_raw_signature(private_key, message)).decode()
    return tokens_module.verify_signed_request(
        tokens=tokens,
        nonces=nonces,
        token=token,
        method=method,
        path=path,
        timestamp=timestamp,
        nonce=nonce,
        signature_b64=signature_b64,
        body=body,
    )


def test_issue_binds_a_token_to_the_devices_public_key() -> None:
    store = tokens_module.TokenStore()
    _private_key, public_der = _new_device_keypair()
    device = store.issue("first-device", public_der)
    assert store.lookup(device.token) is device


def test_issue_rejects_a_non_ec_p256_key() -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    store = tokens_module.TokenStore()
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    from cryptography.hazmat.primitives import serialization

    rsa_public_der = rsa_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    with pytest.raises(ValueError):
        store.issue("first-device", rsa_public_der)


def test_a_correctly_signed_request_is_accepted() -> None:
    tokens = tokens_module.TokenStore()
    nonces = tokens_module.NonceStore()
    private_key, public_der = _new_device_keypair()
    device = tokens.issue("first-device", public_der)

    verified = _signed_request(tokens, nonces, private_key, device.token)
    assert verified is device


def test_a_copied_token_replayed_with_no_valid_signature_is_refused() -> None:
    """The AC's own scenario: an attacker has the bearer token but not the
    private key, so they cannot produce ANY valid signature."""
    tokens = tokens_module.TokenStore()
    nonces = tokens_module.NonceStore()
    _real_private_key, public_der = _new_device_keypair()
    device = tokens.issue("first-device", public_der)

    attacker_private_key, _attacker_public_der = _new_device_keypair()
    with pytest.raises(tokens_module.Unauthorized):
        _signed_request(tokens, nonces, attacker_private_key, device.token)


def test_an_unknown_token_is_refused() -> None:
    tokens = tokens_module.TokenStore()
    nonces = tokens_module.NonceStore()
    private_key, _public_der = _new_device_keypair()
    with pytest.raises(tokens_module.Unauthorized):
        _signed_request(tokens, nonces, private_key, "not-a-real-token")


def test_a_missing_token_is_refused() -> None:
    tokens = tokens_module.TokenStore()
    nonces = tokens_module.NonceStore()
    with pytest.raises(tokens_module.Unauthorized):
        tokens_module.verify_signed_request(
            tokens=tokens,
            nonces=nonces,
            token=None,
            method="GET",
            path="/api/whoami",
            timestamp=str(int(time.time())),
            nonce=nonces.issue(),
            signature_b64="not-real",
            body=b"",
        )


def test_missing_signing_headers_are_refused() -> None:
    tokens = tokens_module.TokenStore()
    nonces = tokens_module.NonceStore()
    _private_key, public_der = _new_device_keypair()
    device = tokens.issue("first-device", public_der)
    with pytest.raises(tokens_module.Unauthorized):
        tokens_module.verify_signed_request(
            tokens=tokens,
            nonces=nonces,
            token=device.token,
            method="GET",
            path="/api/whoami",
            timestamp=None,
            nonce=None,
            signature_b64=None,
            body=b"",
        )


def test_a_replayed_nonce_is_refused_the_second_time() -> None:
    tokens = tokens_module.TokenStore()
    nonces = tokens_module.NonceStore()
    private_key, public_der = _new_device_keypair()
    device = tokens.issue("first-device", public_der)
    nonce = nonces.issue()
    timestamp = str(int(time.time()))

    # First use succeeds...
    _signed_request(tokens, nonces, private_key, device.token, nonce=nonce, timestamp=timestamp)
    # ...replaying the exact same signed request (same nonce) does not.
    with pytest.raises(tokens_module.Unauthorized):
        _signed_request(tokens, nonces, private_key, device.token, nonce=nonce, timestamp=timestamp)


def test_a_stale_timestamp_is_refused() -> None:
    tokens = tokens_module.TokenStore()
    nonces = tokens_module.NonceStore()
    private_key, public_der = _new_device_keypair()
    device = tokens.issue("first-device", public_der)
    ancient_timestamp = str(int(time.time()) - tokens_module.TIMESTAMP_SKEW_SECONDS - 60)
    with pytest.raises(tokens_module.Unauthorized):
        _signed_request(tokens, nonces, private_key, device.token, timestamp=ancient_timestamp)


def test_a_signature_over_the_wrong_path_is_refused() -> None:
    """The signature covers the request path -- a valid signature minted for
    one path must not authorize a different one."""
    tokens = tokens_module.TokenStore()
    nonces = tokens_module.NonceStore()
    private_key, public_der = _new_device_keypair()
    device = tokens.issue("first-device", public_der)
    nonce = nonces.issue()
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(b"").hexdigest()
    message = f"GET\n/api/whoami\n{timestamp}\n{nonce}\n{body_hash}".encode()
    signature_b64 = base64.b64encode(_raw_signature(private_key, message)).decode()

    with pytest.raises(tokens_module.Unauthorized):
        tokens_module.verify_signed_request(
            tokens=tokens,
            nonces=nonces,
            token=device.token,
            method="POST",
            path="/api/device-requests/approve",
            timestamp=timestamp,
            nonce=nonce,
            signature_b64=signature_b64,
            body=b"",
        )


def test_unauthorized_response_is_always_the_same_status_and_body() -> None:
    response_one = tokens_module.unauthorized_response("reason one")
    response_two = tokens_module.unauthorized_response("a completely different reason")
    assert response_one.status == response_two.status == 401
    assert response_one.text == response_two.text == tokens_module.UNAUTHORIZED_BODY


def test_enrollment_ticket_store_redeems_exactly_once() -> None:
    store = tokens_module.EnrollmentTicketStore()
    ticket = store.mint("second-device")
    assert store.redeem(ticket) == "second-device"
    assert store.redeem(ticket) is None


def test_enrollment_ticket_store_rejects_an_unknown_ticket() -> None:
    store = tokens_module.EnrollmentTicketStore()
    assert store.redeem("not-a-real-ticket") is None
