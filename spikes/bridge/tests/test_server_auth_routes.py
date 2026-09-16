"""HTTP-level coverage for the passkey/device-key/device-approval routes
added to bridge_spike.server (Story 1.2). Runs over plain HTTP via aiohttp's
test utilities, same as test_server.py -- real TLS and the real WebAuthn
ceremony (via a CDP virtual authenticator) are proven by
bridge_spike/check.py instead. Passkey verification itself is stubbed here
so these tests isolate the HTTP wiring: setup-code gating, enrollment
tickets, the signed-request decorator, and the device-request state
machine.
"""

from __future__ import annotations

import base64
import hashlib
import time
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer
from bridge_spike import ca as ca_module
from bridge_spike import setup_code as setup_code_module
from bridge_spike import webauthn_flow as webauthn_flow_module
from bridge_spike.server import BridgeServer, ServerConfig
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as ec_utils

INSTALL_NAME = "test-auth-routes.local"
PORT = 8443


def _make_server(setup_codes: setup_code_module.SetupCodeStore | None = None) -> BridgeServer:
    artifacts = ca_module.setup(INSTALL_NAME)
    config = ServerConfig(
        install_name=INSTALL_NAME,
        port=PORT,
        ca_artifacts=artifacts,
        setup_codes=setup_codes or setup_code_module.SetupCodeStore(),
    )
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
    """Bypasses the real WebAuthn ceremony (covered elsewhere) to reach a
    signed-in device+token, exactly as if registration/authentication had
    just minted this enrollment ticket."""
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


async def test_nonce_endpoint_returns_a_fresh_nonce_each_time() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        first = await client.get("/api/auth/nonce", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        second = await client.get("/api/auth/nonce", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert (await first.json())["nonce"] != (await second.json())["nonce"]


async def test_register_options_rejects_a_wrong_setup_code() -> None:
    bridge_server = _make_server(setup_code_module.SetupCodeStore(code="REALCODE"))
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/webauthn/register/options",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={"setup_code": "WRONGCODE"},
        )
        assert response.status == 403


async def test_register_options_accepts_the_real_code_exactly_once() -> None:
    bridge_server = _make_server(setup_code_module.SetupCodeStore(code="REALCODE"))
    async with TestClient(TestServer(bridge_server.app)) as client:
        first = await client.post(
            "/api/webauthn/register/options",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={"setup_code": "REALCODE"},
        )
        assert first.status == 200
        body = await first.json()
        assert body["rp"]["id"] == INSTALL_NAME

        second = await client.post(
            "/api/webauthn/register/options",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={"setup_code": "REALCODE"},
        )
        assert second.status == 403


async def test_register_verify_returns_an_enrollment_ticket_on_success() -> None:
    bridge_server = _make_server()
    with patch.object(bridge_server.webauthn, "finish_registration", return_value=None):
        async with TestClient(TestServer(bridge_server.app)) as client:
            response = await client.post(
                "/api/webauthn/register/verify",
                headers={"Host": f"{INSTALL_NAME}:{PORT}"},
                json={"credential": {"fake": "credential"}},
            )
            assert response.status == 200
            body = await response.json()
            assert body["ok"] is True
            assert body["enrollment_ticket"]


async def test_register_verify_maps_a_webauthn_error_to_400() -> None:
    bridge_server = _make_server()
    with patch.object(
        bridge_server.webauthn, "finish_registration", side_effect=webauthn_flow_module.WebAuthnError("bad")
    ):
        async with TestClient(TestServer(bridge_server.app)) as client:
            response = await client.post(
                "/api/webauthn/register/verify",
                headers={"Host": f"{INSTALL_NAME}:{PORT}"},
                json={"credential": {"fake": "credential"}},
            )
            assert response.status == 400


async def test_authenticate_options_conflict_when_nothing_is_enrolled() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/webauthn/authenticate/options", headers={"Host": f"{INSTALL_NAME}:{PORT}"}
        )
        assert response.status == 409


async def test_authenticate_verify_returns_an_enrollment_ticket_on_success() -> None:
    bridge_server = _make_server()
    with patch.object(bridge_server.webauthn, "finish_authentication", return_value=None):
        async with TestClient(TestServer(bridge_server.app)) as client:
            response = await client.post(
                "/api/webauthn/authenticate/verify",
                headers={"Host": f"{INSTALL_NAME}:{PORT}"},
                json={"credential": {"fake": "credential"}},
            )
            assert response.status == 200
            body = await response.json()
            assert body["ok"] is True
            assert body["enrollment_ticket"]


async def test_authenticate_verify_maps_a_webauthn_error_to_400() -> None:
    bridge_server = _make_server()
    with patch.object(
        bridge_server.webauthn, "finish_authentication", side_effect=webauthn_flow_module.WebAuthnError("bad")
    ):
        async with TestClient(TestServer(bridge_server.app)) as client:
            response = await client.post(
                "/api/webauthn/authenticate/verify",
                headers={"Host": f"{INSTALL_NAME}:{PORT}"},
                json={"credential": {"fake": "credential"}},
            )
            assert response.status == 400


async def test_device_register_key_rejects_an_unknown_ticket() -> None:
    bridge_server = _make_server()
    _private_key, public_der = _device_keypair()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/device/register-key",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={"enrollment_ticket": "not-a-real-ticket", "device_public_key": base64.b64encode(public_der).decode()},
        )
        assert response.status == 403


async def test_device_register_key_rejects_a_malformed_public_key() -> None:
    bridge_server = _make_server()
    ticket = bridge_server.enrollment_tickets.mint("first-device")
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/device/register-key",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={"enrollment_ticket": ticket, "device_public_key": "not-valid-base64-der!!"},
        )
        assert response.status == 400


async def test_whoami_requires_a_signed_request() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/api/whoami", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status == 401
        assert await response.text() == "Unauthorized"


async def test_a_copied_token_with_no_signature_headers_is_refused_with_the_same_401() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        _private_key, token = await _enroll_device(client, bridge_server)
        response = await client.get(
            "/api/whoami", headers={"Host": f"{INSTALL_NAME}:{PORT}", "Authorization": f"Bearer {token}"}
        )
        assert response.status == 401
        assert await response.text() == "Unauthorized"


async def test_whoami_succeeds_with_a_properly_signed_request() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server, device_name="my-laptop")
        headers = await _signed_headers(client, private_key, token, "GET", "/api/whoami")
        response = await client.get("/api/whoami", headers=headers)
        assert response.status == 200
        body = await response.json()
        assert body["device_name"] == "my-laptop"


async def test_device_requests_create_then_conflict_on_a_second_pending_request() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        first = await client.post(
            "/api/device-requests", headers={"Host": f"{INSTALL_NAME}:{PORT}"}, json={"device_name": "second-device"}
        )
        assert first.status == 200
        second = await client.post(
            "/api/device-requests", headers={"Host": f"{INSTALL_NAME}:{PORT}"}, json={"device_name": "third-device"}
        )
        assert second.status == 409


async def test_device_requests_create_rejects_a_malformed_device_name() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/device-requests", headers={"Host": f"{INSTALL_NAME}:{PORT}"}, json={"device_name": ""}
        )
        assert response.status == 400


async def test_device_requests_create_rejects_an_overlong_device_name() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/device-requests",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={"device_name": "x" * 65},  # DEVICE_NAME_RE caps at 64
        )
        assert response.status == 400


async def test_device_requests_create_rejects_a_disallowed_character_in_device_name() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/device-requests",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={"device_name": "second-device; rm -rf /"},  # ';' and '/' are not in DEVICE_NAME_RE
        )
        assert response.status == 400


async def test_device_requests_create_invokes_the_notification_hook() -> None:
    bridge_server = _make_server()
    received = []

    async def _hook(device_request):
        received.append(device_request)

    bridge_server.on_device_request_created = _hook
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/device-requests", headers={"Host": f"{INSTALL_NAME}:{PORT}"}, json={"device_name": "second-device"}
        )
        assert response.status == 200
    assert len(received) == 1
    assert received[0].device_name == "second-device"


async def test_pending_device_request_requires_a_signed_request() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/api/device-requests/pending", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status == 401


async def test_full_second_device_approval_flow() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        # First device is already signed in.
        first_private_key, first_token = await _enroll_device(client, bridge_server, device_name="first-device")

        # Second device requests access.
        create_response = await client.post(
            "/api/device-requests", headers={"Host": f"{INSTALL_NAME}:{PORT}"}, json={"device_name": "second-device"}
        )
        assert create_response.status == 200
        created = await create_response.json()

        # First device sees the SAME name+code pending.
        pending_headers = await _signed_headers(
            client, first_private_key, first_token, "GET", "/api/device-requests/pending"
        )
        pending_response = await client.get("/api/device-requests/pending", headers=pending_headers)
        pending = (await pending_response.json())["pending"]
        assert pending["device_name"] == created["device_name"]
        assert pending["code"] == created["code"]

        # First device approves via a signed tap.
        approve_body = {"request_id": pending["request_id"], "code": pending["code"]}
        import json as _json

        approve_headers = await _signed_headers(
            client,
            first_private_key,
            first_token,
            "POST",
            "/api/device-requests/approve",
            body=_json.dumps(approve_body).encode(),
        )
        approve_response = await client.post(
            "/api/device-requests/approve", headers=approve_headers, json=approve_body
        )
        assert approve_response.status == 200

        # Second device polls its own request id (unauthenticated -- it has
        # no token yet) and gets an enrollment ticket once approved.
        status_response = await client.get(
            f"/api/device-requests/{created['request_id']}", headers={"Host": f"{INSTALL_NAME}:{PORT}"}
        )
        status_body = await status_response.json()
        assert status_body["approved"] is True
        assert status_body["enrollment_ticket"]

        # ...and can now redeem it for its own device key + token.
        register_response = await client.post(
            "/api/device/register-key",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={
                "enrollment_ticket": status_body["enrollment_ticket"],
                "device_public_key": base64.b64encode(_device_keypair()[1]).decode(),
            },
        )
        assert register_response.status == 200
        register_body = await register_response.json()
        assert register_body["device_name"] == "second-device"
        assert register_body["token"] != first_token


async def test_approving_an_unknown_request_id_is_rejected() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        import json as _json

        body = {"request_id": "not-a-real-request", "code": "ANYCODE"}
        headers = await _signed_headers(
            client, private_key, token, "POST", "/api/device-requests/approve", body=_json.dumps(body).encode()
        )
        response = await client.post("/api/device-requests/approve", headers=headers, json=body)
        assert response.status == 400


async def test_approving_the_right_request_id_with_the_wrong_code_is_rejected() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        private_key, token = await _enroll_device(client, bridge_server)
        create_response = await client.post(
            "/api/device-requests", headers={"Host": f"{INSTALL_NAME}:{PORT}"}, json={"device_name": "second-device"}
        )
        created = await create_response.json()

        import json as _json

        body = {"request_id": created["request_id"], "code": "WRONGCD"}
        headers = await _signed_headers(
            client, private_key, token, "POST", "/api/device-requests/approve", body=_json.dumps(body).encode()
        )
        response = await client.post("/api/device-requests/approve", headers=headers, json=body)
        assert response.status == 400

        # The request is still pending -- the mismatch did not consume it.
        pending_headers = await _signed_headers(client, private_key, token, "GET", "/api/device-requests/pending")
        pending_response = await client.get("/api/device-requests/pending", headers=pending_headers)
        assert (await pending_response.json())["pending"]["request_id"] == created["request_id"]


async def test_polling_an_unknown_device_request_is_404() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get(
            "/api/device-requests/does-not-exist", headers={"Host": f"{INSTALL_NAME}:{PORT}"}
        )
        assert response.status == 404
