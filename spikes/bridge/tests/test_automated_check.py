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

    # The CDP virtual-authenticator steps (Story 1.2): setup-code refusal
    # off-network, passkey create() AND get(), a copied-token replay refused,
    # and a two-context matching-code device approval.
    assert result.steps["setup_code_refused_off_network"] is True
    assert result.steps["passkey_registration"] is True
    assert result.steps["passkey_authentication"] is True
    assert result.steps["token_replay_refused"] is True
    assert result.steps["matching_code_shown_on_both"] is True
    assert result.steps["matching_code_approval"] is True

    # Story 1.3: push (VAPID key shape, endpoint refusal, real
    # encrypted+signed delivery to the local stand-in, SW cache scoping,
    # notificationclick on/off the home network) and microphone.
    assert result.steps["push_mic_device_signed_in"] is True
    assert result.steps["vapid_public_key_available"] is True
    assert result.steps["push_endpoint_refusals"] is True
    assert result.steps["push_subscribe_to_local_stand_in"] is True
    assert result.steps["push_delivered_encrypted_and_signed"] is True
    assert result.steps["push_payload_decrypts_to_metadata_only"] is True
    assert result.steps["push_delivered_to_service_worker"] is True
    assert result.steps["cache_holds_metadata_only"] is True
    assert result.steps["notificationclick_away_from_home"] is True
    assert result.steps["notificationclick_on_home_network"] is True
    assert result.steps["offline_summary_page_renders_cached_metadata"] is True
    assert result.steps["mic_capture_produces_a_nonzero_level"] is True
    assert result.steps["mic_permission_persists_after_close_and_reopen"] is True

    assert result.result_path.exists()
    written = json.loads(result.result_path.read_text())
    assert written["kit_version"] == check_module.KIT_VERSION
    assert written["ok"] is True
    assert written["browser"] == "chromium"
    assert "timestamp" in written
    assert "os" in written

    assert result.passkey_result_path is not None
    assert result.passkey_result_path.exists()
    passkey_written = json.loads(result.passkey_result_path.read_text())
    assert passkey_written["kit_version"] == check_module.KIT_VERSION
    assert passkey_written["check"] == "passkey-desktop-chrome-automated"
    assert passkey_written["ok"] is True

    assert result.push_mic_result_path is not None
    assert result.push_mic_result_path.exists()
    push_mic_written = json.loads(result.push_mic_result_path.read_text())
    assert push_mic_written["kit_version"] == check_module.KIT_VERSION
    assert push_mic_written["check"] == "push-mic-desktop-chrome-automated"
    assert push_mic_written["ok"] is True


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
