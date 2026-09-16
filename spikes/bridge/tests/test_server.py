"""Exercises the redirect, subnet refusal (mocking the peer source), and
results-file writing against a running instance of bridge_spike.server.

Runs over plain HTTP via aiohttp's test utilities (no real TLS socket) —
the real TLS handshake against the kit's actual certificate is proven
separately by tests/test_automated_check.py. This file is about the app's
own request-handling logic: which Host is treated as "the install", which
sources are allowed, and what lands in results/.
"""

from __future__ import annotations

import ipaddress
import json
import subprocess

from aiohttp.test_utils import TestClient, TestServer
from bridge_spike import ca as ca_module
from bridge_spike import server as server_module
from bridge_spike.server import BridgeServer, ServerConfig

INSTALL_NAME = "test-server.local"
PORT = 8443  # only used to build the config/redirect target; the real test bind port is ephemeral.


def _make_server() -> BridgeServer:
    artifacts = ca_module.setup(INSTALL_NAME)
    config = ServerConfig(install_name=INSTALL_NAME, port=PORT, ca_artifacts=artifacts)
    return BridgeServer(config)


async def test_request_by_ip_or_wrong_host_redirects_to_install_name() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/some/path", allow_redirects=False)
        assert response.status == 307
        assert response.headers["Location"] == f"https://{INSTALL_NAME}:{PORT}/some/path"
        body = await response.text()
        assert body == "" or "some/path" not in body  # no page content served on the wrong origin


async def test_request_to_install_name_host_is_served() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status == 200
        text = await response.text()
        assert "Bridge TLS Spike" in text


async def test_loopback_source_is_allowed_by_default() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status != 403


async def test_disallowed_source_is_refused_with_logged_remedy(monkeypatch, caplog) -> None:
    bridge_server = _make_server()
    # Mock the peer source the middleware sees, simulating a connection from
    # outside loopback/the local subnet without needing a second real NIC.
    monkeypatch.setattr(BridgeServer, "_peer_ip", staticmethod(lambda request: "203.0.113.5"))

    async with TestClient(TestServer(bridge_server.app)) as client:
        with caplog.at_level("WARNING", logger="bridge_spike.server"):
            response = await client.get("/", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status == 403
        text = await response.text()
        assert "refused" in text.lower() or "not allowed" in text.lower()

    remedy_logs = [record.message for record in caplog.records if "203.0.113.5" in record.message]
    assert remedy_logs, "expected the refusal + remedy to be logged"
    assert "remedy" in remedy_logs[0].lower() or "connect from" in remedy_logs[0].lower()


async def test_disallowed_source_with_wrong_host_is_refused_not_redirected(monkeypatch) -> None:
    """Middleware order regression test: the subnet gate must run BEFORE the
    redirect guard, so a disallowed source is refused before its Host header
    (and thus the install name) is ever inspected -- not handed a redirect
    that discloses the install name to a source that should never see it."""
    bridge_server = _make_server()
    monkeypatch.setattr(BridgeServer, "_peer_ip", staticmethod(lambda request: "203.0.113.5"))

    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/some/path", allow_redirects=False)
        assert response.status == 403
        assert "Location" not in response.headers


async def test_private_subnet_source_is_allowed(monkeypatch) -> None:
    """The "allow" half of the subnet gate: a peer inside a directly-attached
    PRIVATE subnet (not loopback) must be let through, not just refused."""
    private_network = ipaddress.ip_network("10.42.0.0/24")
    monkeypatch.setattr(BridgeServer, "_peer_ip", staticmethod(lambda request: "10.42.0.7"))
    # BridgeServer discovers local_ipv4_networks() once at construction, so the
    # module function must be patched before _make_server() runs.
    monkeypatch.setattr(server_module, "local_ipv4_networks", lambda: [private_network])

    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status != 403


def test_local_ipv4_networks_filters_out_public_directly_attached_subnets(monkeypatch) -> None:
    """Regression test for the real discovery + filtering pipeline (every
    other test here mocks local_ipv4_networks() away entirely): a directly
    attached PUBLIC interface must not survive into the subnet allowlist."""
    ip_output = (
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "2: eth0    inet 192.168.1.50/24 scope global eth0\n"
        "3: eth1    inet 8.8.8.8/24 scope global eth1\n"
    )

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "ip"
        return subprocess.CompletedProcess(cmd, 0, stdout=ip_output, stderr="")

    monkeypatch.setattr(server_module.subprocess, "run", fake_run)

    networks = server_module.local_ipv4_networks()

    assert ipaddress.ip_network("192.168.1.0/24") in networks
    assert ipaddress.ip_network("8.8.8.0/24") not in networks


async def test_results_endpoint_writes_the_checklist_json(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server_module, "RESULTS_DIR", tmp_path)
    bridge_server = _make_server()

    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/results",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={
                "device_class": "desktop-chrome",
                "passed": True,
                "note": "looked fine",
                "browser": "Chromium/999",
                "os": "Linux",
            },
        )
        assert response.status == 200
        body = await response.json()

    out_path = tmp_path / "B1-desktop-chrome.json"
    assert out_path.exists()
    written = json.loads(out_path.read_text())
    assert written["device_class"] == "desktop-chrome"
    assert written["passed"] is True
    assert written["note"] == "looked fine"
    assert written["kit_version"] == server_module.KIT_VERSION
    assert "timestamp" in written
    assert body["path"] == str(out_path)


async def test_results_endpoint_rejects_malformed_device_class(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server_module, "RESULTS_DIR", tmp_path)
    bridge_server = _make_server()

    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/results",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={"device_class": "not a valid class!!", "passed": True, "note": "x"},
        )
        assert response.status == 400
    assert list(tmp_path.iterdir()) == []


async def test_results_endpoint_rejects_missing_note(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server_module, "RESULTS_DIR", tmp_path)
    bridge_server = _make_server()

    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/results",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json={"device_class": "desktop-chrome", "passed": True, "note": "   "},
        )
        assert response.status == 400
    assert list(tmp_path.iterdir()) == []


async def test_results_endpoint_rejects_non_dict_json_body(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server_module, "RESULTS_DIR", tmp_path)
    bridge_server = _make_server()

    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/results",
            headers={"Host": f"{INSTALL_NAME}:{PORT}"},
            json=["not", "a", "dict"],
        )
        assert response.status == 400
    assert list(tmp_path.iterdir()) == []


async def test_results_endpoint_rejects_body_that_is_not_valid_utf8(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server_module, "RESULTS_DIR", tmp_path)
    bridge_server = _make_server()

    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.post(
            "/api/results",
            headers={"Host": f"{INSTALL_NAME}:{PORT}", "Content-Type": "application/json"},
            data=b"\xff\xfe not valid utf-8",
        )
        assert response.status == 400
    assert list(tmp_path.iterdir()) == []


def test_is_source_allowed_accepts_loopback_and_rejects_public_ip() -> None:
    assert server_module.is_source_allowed("127.0.0.1") is True
    assert server_module.is_source_allowed("203.0.113.5", networks=[]) is False
    assert server_module.is_source_allowed(None) is False


async def test_manifest_is_served_as_an_installable_pwa_manifest() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/manifest.webmanifest", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status == 200
        assert response.content_type == "application/manifest+json"
        body = await response.json()
        assert body["display"] == "standalone"
        assert body["icons"]


async def test_service_worker_script_is_served() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/sw.js", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        assert response.status == 200
        assert response.content_type == "application/javascript"
        text = await response.text()
        assert "addEventListener" in text


async def test_icons_referenced_by_the_manifest_are_served() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        for icon_path in ("/icon-192.png", "/icon-512.png"):
            response = await client.get(icon_path, headers={"Host": f"{INSTALL_NAME}:{PORT}"})
            assert response.status == 200
            assert response.content_type == "image/png"


async def test_index_page_registers_the_manifest_and_service_worker() -> None:
    bridge_server = _make_server()
    async with TestClient(TestServer(bridge_server.app)) as client:
        response = await client.get("/", headers={"Host": f"{INSTALL_NAME}:{PORT}"})
        text = await response.text()
        assert 'rel="manifest" href="/manifest.webmanifest"' in text
        assert 'navigator.serviceWorker.register("/sw.js")' in text
