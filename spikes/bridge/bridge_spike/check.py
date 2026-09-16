"""The kit's own automated "done" check: drives Chromium via Playwright against
the kit's real generated certificate to prove the chain, the redirect, the
subnet refusal, and result writing — on the build host, which is this
story's completion bar. Real-device trust ceremonies are Story 1.6's job.

Trust mechanism: rather than installing the ephemeral CA into the OS/NSS
trust store, this pins Chromium's certificate verifier to the CA's
SubjectPublicKeyInfo hash via `--ignore-certificate-errors-spki-list` — the
standard mechanism headless Chromium testing uses for a custom/self-signed
CA. That flag tells Chromium to treat a served chain as trusted once it
contains a certificate matching one of the given SPKI hashes; exactly which
individual checks it still performs versus suppresses for such a chain is
an internal Chromium detail this module does not claim to fully know, so
it is NOT described here as "only" skipping the OS trust-store lookup.
What does matter, and is deliberate: only the CA cert's SPKI is pinned,
never the leaf's. Pinning the leaf's SPKI too would let any served
certificate carrying that exact key pass — including one that was never
actually signed by this CA — which would silently defeat the point of this
check ("proves the certificate chain"). Pinning only the CA means the
served leaf still has to chain-validate back up to that specific CA to be
accepted, a faithful-enough proxy for "a device that already trusts the
CA" without needing root or a real device (that ceremony belongs to
Story 1.6).

Subnet refusal cannot be produced by a live network probe here: on the
build host the only reachable peers are loopback and the host's own LAN
address, both of which the guard is required to allow. This module proves
refusal by making a real HTTP request against the running server while the
server's own `is_source_allowed` lookup is patched to return False for that
one request — exercising the real middleware wiring, not just the helper
function in isolation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import platform
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from playwright.async_api import async_playwright

from . import ca as ca_module
from .server import KIT_VERSION, RESULTS_DIR, BridgeServer, ServerConfig

DEFAULT_CHECK_INSTALL_NAME = "bridge-spike-check.local"


@dataclass
class CheckResult:
    ok: bool
    steps: dict[str, bool]
    detail: dict[str, str]
    result_path: Path
    passkey_result_path: Path | None = None


def _spki_pin(cert_pem: bytes) -> str:
    cert = x509.load_pem_x509_certificate(cert_pem)
    spki = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(hashlib.sha256(spki).digest()).decode()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _check_certificate_chain_and_redirect(
    install_name: str, port: int, spki_pin: str, steps: dict[str, bool], detail: dict[str, str]
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            args=[
                f"--host-resolver-rules=MAP {install_name} 127.0.0.1",
                f"--ignore-certificate-errors-spki-list={spki_pin}",
            ]
        )
        try:
            context = await browser.new_context()
            page = await context.new_page()

            response = await page.goto(f"https://{install_name}:{port}/", wait_until="load")
            steps["certificate_chain"] = bool(response and response.ok)
            detail["certificate_chain"] = f"status={response.status if response else None}"

            await page.goto(f"https://127.0.0.1:{port}/", wait_until="load")
            final_url = page.url
            steps["redirect"] = final_url.startswith(f"https://{install_name}:{port}")
            detail["redirect"] = f"final_url={final_url}"

            await page.goto(f"https://{install_name}:{port}/", wait_until="load")
            # A distinct device_class from the real "desktop-chrome" a human
            # manually testing Chrome would submit -- otherwise this
            # automated run would silently overwrite that person's result.
            await page.select_option("#device_class", "desktop-chrome-automated")
            await page.select_option("#passed", "true")
            await page.fill("#note", "Automated build-host Chromium check.")
            async with page.expect_response(lambda r: r.url.endswith("/api/results")) as resp_info:
                await page.click("button[type=submit]")
            results_response = await resp_info.value
            result_path = RESULTS_DIR / "B1-desktop-chrome-automated.json"
            steps["result_writing"] = results_response.ok and result_path.exists()
            detail["result_writing"] = str(result_path)
        finally:
            await browser.close()


async def _check_subnet_refusal(
    install_name: str, port: int, steps: dict[str, bool], detail: dict[str, str]
) -> None:
    with patch("bridge_spike.server.is_source_allowed", return_value=False):
        async with aiohttp.ClientSession() as session, session.get(
            f"https://127.0.0.1:{port}/", headers={"Host": f"{install_name}:{port}"}, ssl=False
        ) as response:
            steps["subnet_refusal"] = response.status == 403
            detail["subnet_refusal"] = f"status={response.status}"


async def _new_context_with_virtual_authenticator(
    browser: object, install_name: str, port: int
) -> tuple[object, object, str]:
    """A fresh browser context + page, on the kit's own origin, with a CDP
    virtual authenticator attached -- CTAP2, resident keys, and user
    verification all enabled with automatic presence simulation so
    `navigator.credentials.create()`/`.get()` resolve without any human
    present, which is the whole point of driving WebAuthn from a headless
    build-host check."""
    context = await browser.new_context()
    page = await context.new_page()
    cdp = await context.new_cdp_session(page)
    await cdp.send("WebAuthn.enable")
    result = await cdp.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "internal",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )
    await page.goto(f"https://{install_name}:{port}/", wait_until="load")
    return context, page, result["authenticatorId"]


async def _check_passkey_and_device_approval(
    install_name: str,
    port: int,
    spki_pin: str,
    server: BridgeServer,
    steps: dict[str, bool],
    detail: dict[str, str],
) -> None:
    """Proves, against the kit's real server and a real (virtual-authenticator)
    Chromium WebAuthn ceremony: setup-code refusal from a disallowed source,
    passkey registration (`create()`) AND authentication (`get()`), a copied
    bearer token replayed with no valid signature refused with a uniform
    401, and a two-browser-context matching-code device approval."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            args=[
                f"--host-resolver-rules=MAP {install_name} 127.0.0.1",
                f"--ignore-certificate-errors-spki-list={spki_pin}",
                "--enable-features=WebAuthenticationRemoteDesktopSupport",
            ]
        )
        try:
            context1, page1, _authenticator_id = await _new_context_with_virtual_authenticator(
                browser, install_name, port
            )

            # Setup-code refusal from a disallowed source. This must NOT
            # consume the real setup code -- consumption happens inside the
            # route handler, which the patched subnet guard never reaches --
            # so the SAME code still works for the real registration below.
            with patch("bridge_spike.server.is_source_allowed", return_value=False):
                refusal = await page1.evaluate(
                    "code => window.BridgeAuth.tryRegisterOptions(code)", server.setup_codes.code
                )
            steps["setup_code_refused_off_network"] = refusal.get("status") == 403
            detail["setup_code_refused_off_network"] = f"status={refusal.get('status')}"

            # Passkey registration: navigator.credentials.create().
            registration = await page1.evaluate(
                "code => window.BridgeAuth.registerPasskey(code)", server.setup_codes.code
            )
            steps["passkey_registration"] = bool(registration.get("ok"))
            detail["passkey_registration"] = json.dumps(registration)

            # Passkey authentication: navigator.credentials.get() -- a
            # SEPARATE ceremony from registration, proving "create AND get".
            authentication = await page1.evaluate("() => window.BridgeAuth.authenticatePasskey()")
            steps["passkey_authentication"] = bool(authentication.get("ok"))
            detail["passkey_authentication"] = json.dumps(authentication)

            token = await page1.evaluate("() => window.BridgeAuth.getStoredToken()")

            # A copied bearer token, replayed with no valid signature at
            # all -- refused with the SAME 401 every other failure gets.
            async with aiohttp.ClientSession() as session, session.get(
                f"https://127.0.0.1:{port}/api/whoami",
                headers={"Host": f"{install_name}:{port}", "Authorization": f"Bearer {token}"},
                ssl=False,
            ) as response:
                steps["token_replay_refused"] = response.status == 401
                detail["token_replay_refused"] = f"status={response.status}"

            # Two-context matching-code device approval: context2 is a
            # genuinely separate browser context (no shared storage/session)
            # standing in for a second physical device.
            context2 = await browser.new_context()
            page2 = await context2.new_page()
            await page2.goto(f"https://{install_name}:{port}/", wait_until="load")

            request_result = await page2.evaluate(
                "name => window.BridgeAuth.requestDeviceAccess(name)", "second-device-automated"
            )
            pending_response = await page1.evaluate("() => window.BridgeAuth.getPendingDeviceRequest()")
            pending_info = (pending_response or {}).get("pending") or {}
            steps["matching_code_shown_on_both"] = (
                bool(request_result.get("ok"))
                and bool(pending_response.get("ok"))
                and bool(pending_info)
                and pending_info.get("code") == request_result.get("code")
                and pending_info.get("device_name") == "second-device-automated"
            )
            detail["matching_code_shown_on_both"] = f"request={request_result} pending={pending_response}"

            approval = await page1.evaluate(
                "([id, code]) => window.BridgeAuth.approveDeviceRequest(id, code)",
                [pending_info.get("request_id"), pending_info.get("code")],
            )
            second_device_status = await page2.evaluate(
                "id => window.BridgeAuth.pollDeviceRequestStatus(id)", request_result.get("request_id")
            )
            steps["matching_code_approval"] = bool(approval.get("ok")) and bool(second_device_status.get("approved"))
            detail["matching_code_approval"] = f"approval={approval} status={second_device_status}"

            await context2.close()
            await context1.close()
        finally:
            await browser.close()


async def run_check(install_name: str = DEFAULT_CHECK_INSTALL_NAME) -> CheckResult:
    """Start a real instance of the kit's server and prove the story's AC,
    including the passkey/device-key/device-approval ceremonies driven
    through a CDP virtual authenticator, then write
    `results/B1-build-host-chromium.json` and
    `results/B1-passkey-desktop-chrome-automated.json`."""
    port = _free_port()
    artifacts = ca_module.setup(install_name)
    config = ServerConfig(install_name=install_name, port=port, ca_artifacts=artifacts, host="127.0.0.1")
    server = BridgeServer(config)
    runner = await server.start()

    steps: dict[str, bool] = {}
    detail: dict[str, str] = {}
    passkey_steps: dict[str, bool] = {}
    passkey_detail: dict[str, str] = {}
    try:
        spki_pin = _spki_pin(artifacts.ca_cert_pem)
        await _check_certificate_chain_and_redirect(install_name, port, spki_pin, steps, detail)
        await _check_subnet_refusal(install_name, port, steps, detail)
        await _check_passkey_and_device_approval(install_name, port, spki_pin, server, passkey_steps, passkey_detail)
    finally:
        await runner.cleanup()

    ok = bool(steps) and all(steps.values())
    payload = {
        "kit_version": KIT_VERSION,
        "check": "build-host-chromium",
        "install_name": install_name,
        "browser": "chromium",
        "os": platform.platform(),
        "steps": steps,
        "detail": detail,
        "ok": ok,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    result_path = _write_result(payload, "B1-build-host-chromium.json")

    passkey_ok = bool(passkey_steps) and all(passkey_steps.values())
    passkey_payload = {
        "kit_version": KIT_VERSION,
        "check": "passkey-desktop-chrome-automated",
        "install_name": install_name,
        "browser": "chromium",
        "os": platform.platform(),
        "steps": passkey_steps,
        "detail": passkey_detail,
        "ok": passkey_ok,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    passkey_result_path = _write_result(passkey_payload, "B1-passkey-desktop-chrome-automated.json")

    return CheckResult(
        ok=ok and passkey_ok,
        steps={**steps, **passkey_steps},
        detail={**detail, **passkey_detail},
        result_path=result_path,
        passkey_result_path=passkey_result_path,
    )


def _write_result(payload: dict[str, object], filename: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / filename
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path
