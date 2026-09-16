"""HTTP-level coverage for the push subscribe/unsubscribe routes added to
bridge_spike.server (Story 1.3): the unauthenticated VAPID public-key route,
the signed-request gate on subscribe/unsubscribe, and endpoint-validation
refusal (NFR29) at the route layer. Runs over plain HTTP via aiohttp's test
utilities, same as test_server_auth_routes.py -- real TLS is proven by
bridge_spike/check.py instead.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time

from aiohttp.test_utils import TestClient, TestServer
from bridge_spike import ca as ca_module
from bridge_spike import push as push_module
from bridge_spike.server import BridgeServer, ServerConfig
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as ec_utils

INSTALL_NAME = "test-push-routes.local"
PORT = 8443


def _make_server() -> BridgeServer:
    artifacts = ca_module.setup(INSTALL_NAME)
    config = ServerConfig(install_name=INSTALL_NAME, port=PORT, ca_artifacts=artifacts)
    return BridgeServer(config)


def _device_keypair() -> tuple[ec.EllipticCurvePrivateKey, bytes]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_key, public_der


def _raw_signature(private_key: ec.EllipticCurvePrivateKey, message: bytes) -> bytes:
    der_signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = ec_utils.decode_dss_signature(der_signature)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


async def _signed_headers(
    client: TestClient, private_key: ec.EllipticCurvePrivateKey, token: str, method: str, path: str, body: bytes = b""
) -> dict:
    nonce_response = await client.get("/api/auth/nonce", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
    nonce = (await nonce_response.json())["nonce"]
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}".encode()
    signature_b64 = base64.b64encode(_raw_signature(private_key, message)).decode()
    return {
        "Host": f"{INSTALL_NAME}:{PORT}",
        "Authorization": f"Bearer {token}",
        "X-Bridge-Timestamp": timestamp,
        "X-Bridge-Nonce": nonce,
        "X-Bridge-Signature": signature_b64,
    }


async def _enroll_device(client: TestClient, bridge_server: BridgeServer, device_name: str = "first-device"):
    ticket = bridge_server.enrollment_tickets.mint(device_name)
    private_key, public_der = _device_keypair()
    response = await client.post(
        "/api/device/register-key",
        headers={"Host": f"{INSTALL_NAME}:{PORT}"},
        json={"enrollment_ticket": ticket, "device_public_key": base64.b64encode(public_der).decode()},
    )
    assert response.status == 200
    body = await response.json()
    return private_key, body["token"]


async def test_vapid_public_key_route_is_unauthenticated_and_well_shaped() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/api/push/vapid-public-key", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status == 200
        body = await response.json()
        padded = body["key"] + "=" * (-len(body["key"]) % 4)
        raw = base64.urlsafe_b64decode(padded)
        assert len(raw) == 65
        assert raw[0] == 0x04


async def test_vapid_public_key_matches_the_configured_private_key() -> None:
    bridge_server = _make_server()
    expected = push_module.vapid_public_key_b64url(bridge_server.config.vapid_private_key)
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/api/push/vapid-public-key", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        body = await response.json()
        assert body["key"] == expected


async def test_push_subscribe_requires_a_signed_request() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/push/subscribe",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={"endpoint": "https://push.example.com/abc", "keys": {"p256dh": "x", "auth": "y"}},
        )
        assert response.status == 401


async def test_push_unsubscribe_requires_a_signed_request() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/push/unsubscribe",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={"endpoint": "https://push.example.com/abc"},
        )
        assert response.status == 401


async def test_push_subscribe_stores_a_valid_https_public_endpoint() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server, device_name="my-laptop")
        body = {"endpoint": "https://8.8.8.8/abc", "keys": {"p256dh": "p256dh-value", "auth": "auth-value"}}
        headers = await _signed_headers(
            client, private_key, token, "POST", "/api/push/subscribe", body=json.dumps(body).encode()
        )
        response = await client.post("/api/push/subscribe", headers=headers, json=body)
        assert response.status == 200
        assert (await response.json())["ok"] is True

        device_id = bridge_server.tokens.lookup(token).device_id
        stored = bridge_server.push_subscriptions.for_device(device_id)
        assert len(stored) == 1
        assert stored[0].endpoint == "https://8.8.8.8/abc"
        assert stored[0].p256dh == "p256dh-value"
        assert stored[0].auth == "auth-value"


async def test_push_subscribe_rejects_a_non_https_endpoint() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        body = {"endpoint": "http://push.example.com/abc", "keys": {"p256dh": "x", "auth": "y"}}
        headers = await _signed_headers(
            client, private_key, token, "POST", "/api/push/subscribe", body=json.dumps(body).encode()
        )
        response = await client.post("/api/push/subscribe", headers=headers, json=body)
        assert response.status == 400
        assert bridge_server.push_subscriptions.all() == []


async def test_push_subscribe_rejects_a_loopback_endpoint() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        body = {"endpoint": "https://127.0.0.1/abc", "keys": {"p256dh": "x", "auth": "y"}}
        headers = await _signed_headers(
            client, private_key, token, "POST", "/api/push/subscribe", body=json.dumps(body).encode()
        )
        response = await client.post("/api/push/subscribe", headers=headers, json=body)
        assert response.status == 400
        assert bridge_server.push_subscriptions.all() == []


async def test_push_subscribe_rejects_a_private_endpoint() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        body = {"endpoint": "https://10.0.0.5/abc", "keys": {"p256dh": "x", "auth": "y"}}
        headers = await _signed_headers(
            client, private_key, token, "POST", "/api/push/subscribe", body=json.dumps(body).encode()
        )
        response = await client.post("/api/push/subscribe", headers=headers, json=body)
        assert response.status == 400
        assert bridge_server.push_subscriptions.all() == []


async def test_push_subscribe_rejects_a_link_local_endpoint() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        body = {"endpoint": "https://169.254.1.1/abc", "keys": {"p256dh": "x", "auth": "y"}}
        headers = await _signed_headers(
            client, private_key, token, "POST", "/api/push/subscribe", body=json.dumps(body).encode()
        )
        response = await client.post("/api/push/subscribe", headers=headers, json=body)
        assert response.status == 400
        assert bridge_server.push_subscriptions.all() == []


async def test_push_subscribe_rejects_a_missing_keys_body() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        body = {"endpoint": "https://8.8.8.8/abc"}
        headers = await _signed_headers(
            client, private_key, token, "POST", "/api/push/subscribe", body=json.dumps(body).encode()
        )
        response = await client.post("/api/push/subscribe", headers=headers, json=body)
        assert response.status == 400


async def test_push_unsubscribe_removes_a_stored_subscription() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        subscribe_body = {"endpoint": "https://8.8.8.8/abc", "keys": {"p256dh": "x", "auth": "y"}}
        subscribe_headers = await _signed_headers(
            client, private_key, token, "POST", "/api/push/subscribe", body=json.dumps(subscribe_body).encode()
        )
        await client.post("/api/push/subscribe", headers=subscribe_headers, json=subscribe_body)

        unsubscribe_body = {"endpoint": "https://8.8.8.8/abc"}
        unsubscribe_headers = await _signed_headers(
            client, private_key, token, "POST", "/api/push/unsubscribe", body=json.dumps(unsubscribe_body).encode()
        )
        response = await client.post("/api/push/unsubscribe", headers=unsubscribe_headers, json=unsubscribe_body)
        assert response.status == 200
        assert (await response.json())["removed"] is True
        assert bridge_server.push_subscriptions.all() == []


async def test_push_unsubscribe_an_unknown_endpoint_reports_not_removed() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        body = {"endpoint": "https://8.8.8.8/never-subscribed"}
        headers = await _signed_headers(
            client, private_key, token, "POST", "/api/push/unsubscribe", body=json.dumps(body).encode()
        )
        response = await client.post("/api/push/unsubscribe", headers=headers, json=body)
        assert response.status == 200
        assert (await response.json())["removed"] is False
