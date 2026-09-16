"""HTTP-level coverage for the stream-carrier routes added to
bridge_spike.server (Story 1.4): the signed cert-hash snapshot, the
`fetch`-streamed SSE fallback (resume-from-cursor, heartbeat, overflow ->
resync), and the signed POST route. Mirrors test_server_push_routes.py's
pattern -- plain HTTP via aiohttp's test utilities; real TLS/WebTransport
are proven by bridge_spike/check.py instead.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time

from aiohttp.test_utils import TestClient, TestServer
from bridge_spike import ca as ca_module
from bridge_spike import webtransport_cert as webtransport_cert_module
from bridge_spike.server import BridgeServer, ServerConfig
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as ec_utils

INSTALL_NAME = "test-stream-routes.local"
PORT = 8443


def _make_server(*, with_webtransport_certs: bool = True) -> BridgeServer:
    artifacts = ca_module.setup(INSTALL_NAME)
    cert_store = webtransport_cert_module.WebTransportCertStore(INSTALL_NAME) if with_webtransport_certs else None
    config = ServerConfig(install_name=INSTALL_NAME, port=PORT, ca_artifacts=artifacts, webtransport_cert_store=cert_store)
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


async def _read_sse_envelopes(response, count: int) -> list[dict]:
    envelopes: list[dict] = []
    buffer = ""
    async for chunk in response.content.iter_any():
        buffer += chunk.decode()
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            if frame.startswith("data: "):
                envelopes.append(json.loads(frame[len("data: ") :]))
                if len(envelopes) >= count:
                    return envelopes
    return envelopes


# -- cert-hash snapshot -------------------------------------------------------


async def test_cert_hashes_route_requires_a_signed_request() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/api/stream/cert-hashes", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status == 401


async def test_cert_hashes_route_returns_current_and_next() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        headers = await _signed_headers(client, private_key, token, "GET", "/api/stream/cert-hashes")
        response = await client.get("/api/stream/cert-hashes", headers=headers)
        assert response.status == 200
        body = await response.json()
        assert body["hashes"] == bridge_server.config.webtransport_cert_store.hashes_b64()
        assert len(body["hashes"]) == 2


async def test_cert_hashes_route_is_unavailable_with_no_webtransport_configured() -> None:
    bridge_server = _make_server(with_webtransport_certs=False)
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        headers = await _signed_headers(client, private_key, token, "GET", "/api/stream/cert-hashes")
        response = await client.get("/api/stream/cert-hashes", headers=headers)
        assert response.status == 503


# -- SSE fallback --------------------------------------------------------------


async def test_sse_route_requires_a_signed_request() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/api/stream/sse", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status == 401


async def test_sse_route_streams_hello_then_backfill() -> None:
    bridge_server = _make_server()
    bridge_server.stream_hub.record_now(kind="progress", intensity=0.1, rendering="already-recorded")
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        headers = await _signed_headers(client, private_key, token, "GET", "/api/stream/sse")
        async with client.get("/api/stream/sse?cursor=0", headers=headers) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/event-stream")
            envelopes = await _read_sse_envelopes(response, 2)
    assert envelopes[0]["type"] == "hello"
    assert envelopes[1] == {
        "type": "event",
        "cursor": 1,
        "id": "stream-1",
        "kind": "progress",
        "intensity": 0.1,
        "rendering": "already-recorded",
    }


async def test_sse_route_resumes_from_cursor_with_no_loss_or_duplicates() -> None:
    bridge_server = _make_server()
    for i in range(1, 6):
        bridge_server.stream_hub.record_now(kind="progress", intensity=float(i), rendering=str(i))
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        headers = await _signed_headers(client, private_key, token, "GET", "/api/stream/sse")
        async with client.get("/api/stream/sse?cursor=2", headers=headers) as response:
            envelopes = await _read_sse_envelopes(response, 4)  # hello + events 3,4,5
    assert envelopes[0]["type"] == "hello"
    assert [e["cursor"] for e in envelopes[1:]] == [3, 4, 5]


async def test_sse_route_rejects_a_non_integer_cursor() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        headers = await _signed_headers(client, private_key, token, "GET", "/api/stream/sse")
        response = await client.get("/api/stream/sse?cursor=not-a-number", headers=headers)
        assert response.status == 400


async def test_sse_route_sends_a_heartbeat_carrying_head_cursor() -> None:
    bridge_server = _make_server()
    bridge_server.stream_hub.heartbeat_interval_seconds = 0.05
    bridge_server.stream_hub.record_now(kind="progress", intensity=0.1, rendering="one")
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        headers = await _signed_headers(client, private_key, token, "GET", "/api/stream/sse")
        async with client.get("/api/stream/sse?cursor=0", headers=headers) as response:
            envelopes = await asyncio.wait_for(_read_sse_envelopes(response, 3), timeout=5)
    assert envelopes[0]["type"] == "hello"
    assert envelopes[1]["type"] == "event"
    assert envelopes[2] == {"type": "heartbeat", "head_cursor": 1}


async def test_sse_route_overflow_sends_resync_not_loss() -> None:
    bridge_server = _make_server()
    bridge_server.stream_hub._client_queue_maxsize = 2  # force an overflow quickly
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        headers = await _signed_headers(client, private_key, token, "GET", "/api/stream/sse")
        async with client.get("/api/stream/sse?cursor=0", headers=headers) as response:
            # Give the route a moment to register its client, then flood well
            # past the (now tiny) bounded queue before ever reading a byte.
            await asyncio.sleep(0.05)
            for i in range(1, 20):
                bridge_server.stream_hub.record_now(kind="alert", intensity=float(i), rendering=str(i))
            envelopes = await asyncio.wait_for(_read_sse_envelopes(response, 2), timeout=5)
    assert envelopes[0]["type"] == "hello"
    assert envelopes[1]["type"] == "resync"


# -- POST fallback -------------------------------------------------------------


async def test_stream_send_route_requires_a_signed_request() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/stream/send", headers={"Host": f"{INSTALL_NAME}:{PORT}"}, json={"type": "ack"}
        )
        assert response.status == 401


async def test_stream_send_route_accepts_a_signed_body() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        body = {"type": "ack", "cursor": 3}
        headers = await _signed_headers(
            client, private_key, token, "POST", "/api/stream/send", body=json.dumps(body).encode()
        )
        response = await client.post("/api/stream/send", headers=headers, json=body)
        assert response.status == 200
        assert (await response.json()) == {"ok": True, "received": body}
