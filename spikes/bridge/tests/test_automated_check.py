"""Drives Chromium via Playwright against the kit's real generated cert.

This is the story's own "done" bar per the epic: the kit passes its
automated check against Chromium on the build host. It proves the
certificate chain, the redirect, the subnet refusal, and result writing,
and writes results/B1-build-host-chromium.json. Real-device trust
ceremonies (iOS/Android/desktop, by hand) are Story 1.6's job, not this
story's — nothing here requires a phone.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright")

from playwright.async_api import Error as PlaywrightError  # noqa: E402

from bridge_spike import ca as ca_module  # noqa: E402
from bridge_spike import check as check_module  # noqa: E402
from bridge_spike.server import BridgeServer, ServerConfig  # noqa: E402


async def test_automated_check_passes_against_chromium(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(check_module, "RESULTS_DIR", tmp_path)
    from bridge_spike import server as server_module

    monkeypatch.setattr(server_module, "RESULTS_DIR", tmp_path)

    result = await check_module.run_check(install_name="automated-check-test.local")

    assert result.ok, f"check failed: {result.steps} / {result.detail}"
    assert result.steps["certificate_chain"] is True
    assert result.steps["redirect"] is True
    assert result.steps["result_writing"] is True
    assert result.steps["subnet_refusal"] is True

    assert result.result_path.exists()
    written = json.loads(result.result_path.read_text())
    assert written["kit_version"] == check_module.KIT_VERSION
    assert written["ok"] is True
    assert written["browser"] == "chromium"
    assert "timestamp" in written
    assert "os" in written


async def test_certificate_chain_check_fails_for_a_leaf_not_signed_by_the_pinned_ca() -> None:
    """Pinning only the CA's SPKI (never the leaf's) must still require the
    served leaf to chain-validate back up to that specific CA -- a leaf
    signed by an unrelated CA must not pass just because some CA is pinned."""
    install_name = "automated-check-wrong-ca-test.local"
    served_artifacts = ca_module.setup(install_name)
    unrelated_ca_artifacts = ca_module.setup(install_name)
    wrong_spki_pin = check_module._spki_pin(unrelated_ca_artifacts.ca_cert_pem)

    port = check_module._free_port()
    config = ServerConfig(install_name=install_name, port=port, ca_artifacts=served_artifacts, host="127.0.0.1")
    server = BridgeServer(config)
    runner = await server.start()
    try:
        with pytest.raises(PlaywrightError):
            await check_module._check_certificate_chain_and_redirect(
                install_name, port, wrong_spki_pin, steps={}, detail={}
            )
    finally:
        await runner.cleanup()
