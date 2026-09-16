"""End-to-end coverage for the local push-service stand-in (push_stub.py):
a real HTTPS listener that acks a Web Push POST, proven against push.py's
real `pywebpush`-based sender -- real VAPID JWT signing (RFC 8292), real
`aes128gcm` payload encryption (RFC 8291), decrypted back here to prove the
delivered plaintext is exactly the metadata-only payload (FR26). Never
touches a real push relay -- same principle test_telegram_bot.py's docstring
states for Telegram.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

import http_ece
import requests
from bridge_spike import push as push_module
from bridge_spike import push_stub as push_stub_module
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _fake_browser_subscription_keys() -> tuple[ec.EllipticCurvePrivateKey, str, str]:
    """A P-256 ECDH keypair + random 16-byte auth secret, matching the shape
    a real browser's PushSubscription carries -- used here only so send_push
    has something real to encrypt against and this test can decrypt it back."""
    client_key = ec.generate_private_key(ec.SECP256R1())
    p256dh_raw = client_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    p256dh = base64.urlsafe_b64encode(p256dh_raw).rstrip(b"=").decode()
    auth = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
    return client_key, p256dh, auth


async def test_push_stub_receives_a_real_encrypted_vapid_signed_push(tmp_path) -> None:
    stub = push_stub_module.PushStub()
    await stub.start()
    try:
        vapid_key = push_module.generate_vapid_keypair()
        client_key, p256dh, auth = _fake_browser_subscription_keys()
        subscription = push_module.PushSubscription(
            device_id="device-1", endpoint=f"{stub.endpoint_base}/sub-1", p256dh=p256dh, auth=auth
        )
        metadata = push_module.build_metadata(
            item_id="item-42", kind="approval", intensity=0.9, rendering="Approve the thing?"
        )

        ca_cert_path = tmp_path / "push-stub-ca.pem"
        ca_cert_path.write_bytes(stub.ca_artifacts.ca_cert_pem)
        session = requests.Session()
        session.verify = str(ca_cert_path)

        import asyncio

        await asyncio.to_thread(push_module.send_push, vapid_key, subscription, metadata, requests_session=session)

        assert len(stub.received) == 1
        received = stub.received[0]
        assert received.path == "/push/sub-1"

        # VAPID auth header present, RFC 8292 "vapid" scheme, carrying the
        # public key that matches the private key we signed with.
        auth_header = received.headers["Authorization"]
        assert auth_header.startswith("vapid ")
        assert push_module.vapid_public_key_b64url(vapid_key) in auth_header

        assert received.headers["Content-Encoding"] == "aes128gcm"

        # The wire body must NOT be readable plaintext JSON.
        try:
            json.loads(received.body)
            raised = False
        except (json.JSONDecodeError, UnicodeDecodeError):
            raised = True
        assert raised, "push body must be encrypted, not plaintext JSON"

        # Decrypt it back (as the real browser/OS would) to prove the
        # delivered plaintext is EXACTLY the metadata-only payload.
        auth_secret = base64.urlsafe_b64decode(auth + "=" * (-len(auth) % 4))
        decrypted = http_ece.decrypt(
            received.body,
            salt=None,
            key=None,
            private_key=client_key,
            dh=None,
            auth_secret=auth_secret,
            version="aes128gcm",
        )
        assert json.loads(decrypted) == metadata
    finally:
        await stub.stop()


async def test_push_stub_acks_with_201() -> None:
    stub = push_stub_module.PushStub()
    await stub.start()
    try:
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as handle:
            handle.write(stub.ca_artifacts.ca_cert_pem)
            ca_path = Path(handle.name)
        try:
            import asyncio

            response = await asyncio.to_thread(
                requests.post, f"{stub.endpoint_base}/sub-2", data=b"anything", verify=str(ca_path)
            )
            assert response.status_code == 201
        finally:
            ca_path.unlink(missing_ok=True)
    finally:
        await stub.stop()
