"""Story 1.5 (AD-36): proves `_security_headers_guard` stamps the exact
`SECURITY_HEADERS` set on EVERY response the kit's `BridgeServer` sends --
not just the happy path. Runs directly against `aiohttp.test_utils.TestClient`
(no real TLS socket -- see tests/test_server.py's own module docstring for
why) and asserts the headers on:

  * a normal 200 route (the install-name index page), and
  * an error route raised as `web.HTTPException` deeper in the middleware
    stack (the subnet guard's 403, and the redirect guard's 307) --
    exercising the `except web.HTTPException` branch in
    `_security_headers_guard`, since raising an HTTPException IS aiohttp's
    own response path for those.

`SECURITY_HEADERS` is imported from `bridge_spike.server` (never
re-declared) so this test can never silently drift from the policy the
middleware actually enforces.
"""

from __future__ import annotations

import base64
import hashlib
import time

from aiohttp.test_utils import TestClient, TestServer
from bridge_spike import ca as ca_module
from bridge_spike.server import SECURITY_HEADERS, BridgeServer, ServerConfig
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as ec_utils

INSTALL_NAME = "test-security-headers.local"
PORT = 8443  # only used to build the config/redirect target; the real test bind port is ephemeral.


def _make_server() -> BridgeServer:
    artifacts = ca_module.setup(INSTALL_NAME)
    config = ServerConfig(install_name=INSTALL_NAME, port=PORT, ca_artifacts=artifacts)
    return BridgeServer(config)


# -- signed-request helpers, duplicated from test_server_stream_routes.py
# (this file's own established per-test-module convention -- see that
# file's identical helpers) rather than cross-imported, so this file stays
# independently runnable. Needed only for the SSE-wire-headers test below.


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


def _assert_exact_security_headers(headers) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert headers.get(name) == value, f"{name}: expected {value!r}, got {headers.get(name)!r}"


async def test_normal_response_carries_the_exact_security_header_set() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status == 200
        _assert_exact_security_headers(response.headers)


async def test_csp_check_static_mount_response_carries_the_exact_security_header_set() -> None:
    """The `/csp-check/` route (the Story 1.5 Svelte/Three.js build's own
    index) is a distinct route registered ahead of the general `/` handler --
    prove the outermost middleware still wraps it, not just `/`."""
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/csp-check/", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status == 200
        _assert_exact_security_headers(response.headers)


async def test_subnet_refused_403_response_carries_the_exact_security_header_set(monkeypatch) -> None:
    """The subnet guard raises `web.HTTPForbidden` -- an `HTTPException` --
    from a middleware INSIDE `_security_headers_guard`'s own try/except.
    Without that except branch, this 403 would carry no security headers at
    all (the AC's own "success and error alike")."""
    bridge_server = _make_server()
    monkeypatch.setattr(BridgeServer, "_peer_ip", staticmethod(lambda request: "203.0.113.5"))

    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status == 403
        _assert_exact_security_headers(response.headers)


async def test_redirect_307_response_carries_the_exact_security_header_set() -> None:
    """The redirect guard raises `web.HTTPTemporaryRedirect` -- also an
    `HTTPException` -- for a request by IP/wrong Host. Same proof as the 403
    case, for the other exception-raising middleware in the stack."""
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/some/path", allow_redirects=False)
        assert response.status == 307
        _assert_exact_security_headers(response.headers)


async def test_sse_stream_wire_response_carries_the_exact_security_header_set() -> None:
    """`_handle_stream_sse` builds its own `web.StreamResponse` and calls
    `response.prepare(request)` directly -- which writes headers to the wire
    immediately, before control ever returns to `_security_headers_guard`'s
    post-handler `response.headers.update(...)`. Without the fix in
    `_handle_stream_sse` itself (setting `SECURITY_HEADERS` before
    `prepare()`), a real client streaming `/api/stream/sse` would never
    actually receive them, even though the in-memory `response.headers`
    object the middleware mutates afterward would look correct in a test
    that only checked THAT object. This test opens the route as a real
    streaming client and asserts the headers on the live wire response."""
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        headers = await _signed_headers(client, private_key, token, "GET", "/api/stream/sse")
        async with client.get("/api/stream/sse?cursor=0", headers=headers) as response:
            assert response.status == 200
            _assert_exact_security_headers(response.headers)
