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

import asyncio
import base64
import hashlib
import json
import math
import os
import platform
import socket
import struct
import tempfile
import time
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import aiohttp
import http_ece
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as ec_utils
from playwright.async_api import async_playwright

from . import ca as ca_module
from . import push as push_module
from . import push_stub as push_stub_module
from . import stream as stream_module
from . import webtransport_cert as webtransport_cert_module
from . import webtransport_server as webtransport_server_module
from .server import KIT_VERSION, RESULTS_DIR, SECURITY_HEADERS, BridgeServer, ServerConfig

DEFAULT_CHECK_INSTALL_NAME = "bridge-spike-check.local"


@dataclass
class CheckResult:
    ok: bool
    steps: dict[str, bool]
    detail: dict[str, str]
    result_path: Path
    passkey_result_path: Path | None = None
    push_mic_result_path: Path | None = None
    stream_result_path: Path | None = None
    csp_result_path: Path | None = None


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


def _write_continuous_tone_wav(path: Path) -> None:
    """A short 440Hz sine tone, 44.1kHz stereo 16-bit PCM -- the format
    Chromium's `--use-file-for-fake-audio-capture` wants on Linux. Chromium
    loops this file by default (no `%noloop` suffix on the launch arg), so
    every sample of it is non-silent -- unlike the fake device's OWN default
    ("beep sounds by default" separated by multi-second silent gaps per
    Chromium's own docs), which would make a short capture window flaky:
    a getUserMedia() read could easily land entirely inside a silent gap."""
    sample_rate = 44100
    duration_seconds = 1
    frequency_hz = 440
    amplitude = 16000
    frame_count = sample_rate * duration_seconds
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = bytearray()
        for i in range(frame_count):
            sample = int(amplitude * math.sin(2 * math.pi * frequency_hz * i / sample_rate))
            frames += struct.pack("<hh", sample, sample)
        wav_file.writeframes(bytes(frames))


async def _check_push_and_mic(
    install_name: str,
    port: int,
    spki_pin: str,
    server: BridgeServer,
    steps: dict[str, bool],
    detail: dict[str, str],
) -> None:
    """Proves Story 1.3's AC: the VAPID public key's availability/shape and
    real encrypted+signed delivery to the kit's own local stand-in (AC1),
    endpoint refusal (AC2, NFR29), the service worker's push handling and
    metadata-only cache scoping plus notificationclick routing both on and
    off the home network (AC4, FR25/FR26), and microphone capture with
    permission persistence (AC5) -- all against the kit's real server and a
    real Chromium browser.

    Two things a headless build host cannot produce for real, each handled
    the same documented way this module already handles "subnet refusal
    cannot be produced by a live network probe" at the top of this file:

    - A real OS-level notification click has no automation surface at all
      (unlike WebAuthn, there is no CDP equivalent) -- sw.js exposes a
      message-driven test seam that calls the exact SAME decision function
      the real notificationclick listener calls (see sw.js's docstring
      there).
    - Registering the kit's own local push stand-in AS a push subscription:
      NFR29 rightly refuses a loopback endpoint over the real route, but the
      only "relay" available on a build host IS that loopback stand-in (a
      real non-loopback relay is explicitly forbidden for this story's own
      completion bar). The loopback refusal is patched for exactly the one
      registration call that targets the stand-in, the same way
      _check_subnet_refusal patches is_source_allowed for exactly one
      request -- the refusal itself is proven UNPATCHED, separately, first.
    """
    push_stub = push_stub_module.PushStub()
    await push_stub.start()
    fake_audio_fd, fake_audio_path_str = tempfile.mkstemp(suffix=".wav")
    os.close(fake_audio_fd)
    fake_audio_path = Path(fake_audio_path_str)
    _write_continuous_tone_wav(fake_audio_path)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                args=[
                    f"--host-resolver-rules=MAP {install_name} 127.0.0.1",
                    f"--ignore-certificate-errors-spki-list={spki_pin}",
                    "--use-fake-device-for-media-stream",
                    f"--use-file-for-fake-audio-capture={fake_audio_path}",
                    "--use-fake-ui-for-media-stream",
                    # captureLevel() calls resume() itself (see app.js), but
                    # this check drives it via page.evaluate rather than a
                    # real click -- belt-and-suspenders against Chromium's
                    # autoplay policy suspending the AudioContext with no
                    # user-gesture history at all.
                    "--autoplay-policy=no-user-gesture-required",
                ]
            )
            try:
                origin = f"https://{install_name}:{port}"
                context = await browser.new_context()
                await context.grant_permissions(["microphone", "notifications"], origin=origin)
                page = await context.new_page()
                await page.goto(f"{origin}/", wait_until="load")
                await page.evaluate("async () => { await navigator.serviceWorker.ready; }")

                # Sign in a device without spending the kit's one-time setup
                # code or re-running the WebAuthn ceremony already proven by
                # _check_passkey_and_device_approval -- see docstring.
                ticket = server.enrollment_tickets.mint("push-mic-check-device")
                enrollment = await page.evaluate(
                    "([ticket, name]) => window.BridgeAuth.completeDeviceEnrollment(ticket, name)",
                    [ticket, "push-mic-check-device"],
                )
                steps["push_mic_device_signed_in"] = (
                    bool(enrollment.get("token")) and enrollment.get("device_name") == "push-mic-check-device"
                )
                detail["push_mic_device_signed_in"] = json.dumps(enrollment)

                # AC1 (first half): the VAPID public key is available in
                # exactly the shape PushManager.subscribe's
                # applicationServerKey needs -- an uncompressed P-256 point.
                key_result = await page.evaluate("() => window.BridgePush.getVapidPublicKey()")
                key_shape_ok = False
                if key_result.get("ok"):
                    padded = key_result["key"] + "=" * (-len(key_result["key"]) % 4)
                    raw = base64.urlsafe_b64decode(padded)
                    key_shape_ok = len(raw) == 65 and raw[0] == 0x04
                steps["vapid_public_key_available"] = key_shape_ok
                detail["vapid_public_key_available"] = json.dumps(key_result)

                # AC2 (NFR29): endpoint refusal, through the real signed
                # route, UNPATCHED.
                refusals: dict[str, bool] = {}
                for label, endpoint in (
                    ("non_https", "http://push.example.com/x"),
                    ("loopback", "https://127.0.0.1/x"),
                    ("private", "https://10.1.2.3/x"),
                    ("link_local", "https://169.254.1.1/x"),
                ):
                    result = await page.evaluate(
                        "endpoint => window.BridgePush.subscribeWithInfo({endpoint, keys: {p256dh: 'x', auth: 'y'}})",
                        endpoint,
                    )
                    refusals[label] = result.get("status") == 400
                steps["push_endpoint_refusals"] = bool(refusals) and all(refusals.values())
                detail["push_endpoint_refusals"] = json.dumps(refusals)

                # AC1 (second half) + AC6: subscribe to the kit's OWN local
                # stand-in, then send a real push through it.
                client_key = ec.generate_private_key(ec.SECP256R1())
                p256dh = (
                    base64.urlsafe_b64encode(
                        client_key.public_key().public_bytes(
                            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                        )
                    )
                    .rstrip(b"=")
                    .decode()
                )
                auth_secret = os.urandom(16)
                auth = base64.urlsafe_b64encode(auth_secret).rstrip(b"=").decode()
                subscription_info = {
                    "endpoint": f"{push_stub.endpoint_base}/check-subscription",
                    "keys": {"p256dh": p256dh, "auth": auth},
                }
                with patch("bridge_spike.server.push_module.validate_endpoint", return_value=None):
                    subscribe_result = await page.evaluate(
                        "info => window.BridgePush.subscribeWithInfo(info)", subscription_info
                    )
                steps["push_subscribe_to_local_stand_in"] = bool(subscribe_result.get("ok"))
                detail["push_subscribe_to_local_stand_in"] = json.dumps(subscribe_result)

                device = server.tokens.lookup(enrollment.get("token", ""))
                stored = server.push_subscriptions.for_device(device.device_id) if device else []

                send_ok = False
                decrypted_matches = False
                metadata = push_module.build_metadata(
                    item_id="check-push-item", kind="alert", intensity=0.42, rendering="Automated check push"
                )
                if stored:
                    ca_cert_fd, ca_cert_path_str = tempfile.mkstemp(suffix=".pem")
                    ca_cert_path = Path(ca_cert_path_str)
                    try:
                        os.close(ca_cert_fd)
                        ca_cert_path.write_bytes(push_stub.ca_artifacts.ca_cert_pem)
                        session = requests.Session()
                        session.verify = str(ca_cert_path)
                        try:
                            await asyncio.to_thread(
                                push_module.send_push,
                                server.config.vapid_private_key,
                                stored[0],
                                metadata,
                                requests_session=session,
                            )
                            send_ok = True
                        except push_module.WebPushException as exc:
                            detail["push_send_error"] = str(exc)
                    finally:
                        ca_cert_path.unlink(missing_ok=True)

                    if send_ok and push_stub.received:
                        received = push_stub.received[-1]
                        auth_header = received.headers.get("Authorization", "")
                        vapid_signed = auth_header.startswith("vapid ") and (
                            push_module.vapid_public_key_b64url(server.config.vapid_private_key) in auth_header
                        )
                        encrypted = received.headers.get("Content-Encoding") == "aes128gcm"
                        not_plaintext = True
                        try:
                            json.loads(received.body)
                            not_plaintext = False
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                        decrypted = http_ece.decrypt(
                            received.body,
                            salt=None,
                            key=None,
                            private_key=client_key,
                            dh=None,
                            auth_secret=auth_secret,
                            version="aes128gcm",
                        )
                        decrypted_matches = json.loads(decrypted) == metadata
                        send_ok = send_ok and vapid_signed and encrypted and not_plaintext
                        detail["push_delivered_encrypted_and_signed"] = json.dumps(
                            {"vapid_signed": vapid_signed, "encrypted": encrypted, "not_plaintext": not_plaintext}
                        )
                if "push_delivered_encrypted_and_signed" not in detail:
                    reason = "no subscription stored" if not stored else "no push received by the local stand-in"
                    detail["push_delivered_encrypted_and_signed"] = json.dumps({"skipped": reason})
                steps["push_delivered_encrypted_and_signed"] = send_ok
                steps["push_payload_decrypts_to_metadata_only"] = decrypted_matches

                # AC4 (FR25): SW push handling + metadata-only cache
                # scoping, via CDP ServiceWorker.deliverPushMessage -- the
                # same documented mechanism DevTools' own "Push" test button
                # in the Application panel uses. By the time a real SW's
                # push handler sees event.data, the payload has ALREADY been
                # decrypted upstream (browser/OS) in a real deployment --
                # delivering plaintext here through the same trusted-event
                # path faithfully exercises the same SW code a real
                # (decrypted) push event would run.
                cdp = await context.new_cdp_session(page)
                registration_id: str | None = None
                activated = asyncio.Event()

                def _on_registration(params: dict) -> None:
                    nonlocal registration_id
                    for reg in params.get("registrations", []):
                        if not reg.get("isDeleted", False):
                            registration_id = reg.get("registrationId")

                def _on_version(params: dict) -> None:
                    for version in params.get("versions", []):
                        if version.get("status") == "activated":
                            activated.set()

                cdp.on("ServiceWorker.workerRegistrationUpdated", _on_registration)
                cdp.on("ServiceWorker.workerVersionUpdated", _on_version)
                await cdp.send("ServiceWorker.enable")
                try:
                    await asyncio.wait_for(activated.wait(), timeout=5)
                except TimeoutError:
                    # The registration/activation dump can race ServiceWorker.enable
                    # on a slow host -- a reload forces fresh registration events.
                    await page.reload(wait_until="load")
                    await asyncio.wait_for(activated.wait(), timeout=10)

                if registration_id is None:
                    raise RuntimeError(
                        "bridge_spike.check: no service worker registrationId was observed via CDP "
                        "ServiceWorker.workerRegistrationUpdated before activation -- cannot call "
                        "ServiceWorker.deliverPushMessage"
                    )

                cache_metadata = push_module.build_metadata(
                    item_id="cache-scoping-item", kind="question", intensity=0.1, rendering="Cache scoping check"
                )
                await cdp.send(
                    "ServiceWorker.deliverPushMessage",
                    {"origin": origin, "registrationId": registration_id, "data": json.dumps(cache_metadata)},
                )

                cached = None
                for _attempt in range(10):
                    cached = await page.evaluate(
                        "async (itemId) => {"
                        "  const cache = await caches.open('bridge-spike-push-summaries-v1');"
                        "  const match = await cache.match(`/__push-summary__/${encodeURIComponent(itemId)}`);"
                        "  return match ? await match.json() : null;"
                        "}",
                        cache_metadata["id"],
                    )
                    if cached is not None:
                        break
                    await asyncio.sleep(0.2)
                steps["push_delivered_to_service_worker"] = cached is not None
                steps["cache_holds_metadata_only"] = cached == cache_metadata and (
                    cached is not None and set(cached.keys()) == {"id", "kind", "intensity", "rendering"}
                )
                detail["cache_holds_metadata_only"] = json.dumps(cached)

                # notificationclick, away from home: force the reachability
                # probe to fail via real network interception (never a
                # code-level patch) -- the SW must decide to open the cached
                # summary, never an error page.
                await context.route("**/api/auth/nonce", lambda route: route.abort())
                away_result = await page.evaluate(
                    "metadata => window.BridgePush.simulateNotificationClick(metadata)", cache_metadata
                )
                await context.unroute("**/api/auth/nonce")
                away_decision = away_result.get("result") or {}
                steps["notificationclick_away_from_home"] = (
                    bool(away_result.get("ok"))
                    and away_decision.get("action") == "opened_offline_summary"
                    and f"item={cache_metadata['id']}" in (away_decision.get("url") or "")
                )
                detail["notificationclick_away_from_home"] = json.dumps(away_result)

                # notificationclick, home network: reachable for real (the
                # kit's own server is up) -- no interception at all.
                home_result = await page.evaluate(
                    "metadata => window.BridgePush.simulateNotificationClick(metadata)", cache_metadata
                )
                home_decision = home_result.get("result") or {}
                steps["notificationclick_on_home_network"] = (
                    bool(home_result.get("ok"))
                    and home_decision.get("action") == "opened_item"
                    and f"item={cache_metadata['id']}" in (home_decision.get("url") or "")
                )
                detail["notificationclick_on_home_network"] = json.dumps(home_result)

                # The offline summary page itself renders the cached
                # metadata -- no error page. Still network-reachable here
                # (only /api/auth/nonce was intercepted above, and that
                # interception was already removed).
                await page.goto(f"{origin}/offline-summary.html?item={cache_metadata['id']}", wait_until="load")
                # The page's own script reads the cache asynchronously and
                # is not awaited by the "load" event -- poll briefly rather
                # than assume it has finished by the time goto() returns.
                summary_metadata = None
                for _attempt in range(10):
                    summary_metadata = await page.evaluate("() => window.__bridgeOfflineSummaryMetadata")
                    if summary_metadata is not None:
                        break
                    await asyncio.sleep(0.2)
                steps["offline_summary_page_renders_cached_metadata"] = summary_metadata == cache_metadata
                detail["offline_summary_page_renders_cached_metadata"] = json.dumps(summary_metadata)

                # AC5: microphone capture (real getUserMedia + fake-device
                # audio) and permission persistence across a close/reopen.
                await page.goto(f"{origin}/", wait_until="load")
                capture_result = await page.evaluate("() => window.BridgeMic.captureLevel(300)")
                steps["mic_capture_produces_a_nonzero_level"] = (
                    bool(capture_result.get("ok")) and capture_result.get("level", 0) > 0
                )
                detail["mic_capture_produces_a_nonzero_level"] = json.dumps(capture_result)

                await page.close()
                reopened_page = await context.new_page()
                await reopened_page.goto(f"{origin}/", wait_until="load")
                persisted_state = await reopened_page.evaluate("() => window.BridgeMic.permissionState()")
                steps["mic_permission_persists_after_close_and_reopen"] = persisted_state == "granted"
                detail["mic_permission_persists_after_close_and_reopen"] = persisted_state
            finally:
                await browser.close()
    finally:
        await push_stub.stop()
        fake_audio_path.unlink(missing_ok=True)


# -- Story 1.4: live stream over WebTransport with automatic SSE fallback --
#
# There is no CDP surface for WebTransport (unlike WebAuthn's virtual
# authenticator or push's ServiceWorker.deliverPushMessage), so every
# browser-side proof below is driven through `window.BridgeStream` --
# exactly the code path a real phone runs, never a second reimplementation.
# "Deliberately throttled client / replayer never blocks" is server-side
# backpressure rather than browser interop, so it is proven the SAME way
# _check_subnet_refusal proves the subnet guard: a real HTTP client against
# the real running server, no browser involved at all.

STREAM_METRIC_SAMPLE_COUNT = 3


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


def _p50_p95(values: list[float]) -> dict[str, float]:
    return {"p50": _percentile(values, 50), "p95": _percentile(values, 95)}


def _process_rss_kb(pid: int) -> int:
    """Resident memory (KB) of the given process, read from
    `/proc/<pid>/status` -- Linux only, matching this kit's own documented
    test-run/build-host assumption. Never raises: a missing sample beats
    crashing the whole check over an unavailable metric."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (FileNotFoundError, OSError, ValueError, IndexError):
        pass
    return 0


async def _wait_until(condition: Callable[[], Awaitable[bool]], *, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if await condition():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(interval)


def _stream_device_keypair_and_token(server: BridgeServer, device_name: str) -> tuple[ec.EllipticCurvePrivateKey, str]:
    """Mints a signed-in device directly against the server's own stores --
    the same shortcut `_check_push_and_mic` already documents: no need to
    spend the kit's one-time setup code or re-run WebAuthn, already proven
    by `_check_passkey_and_device_approval`."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    device = server.tokens.issue(device_name, public_der)
    return private_key, device.token


def _sign_stream_request(
    server: BridgeServer, private_key: ec.EllipticCurvePrivateKey, method: str, path: str, body: bytes = b""
) -> dict[str, str]:
    nonce = server.nonces.issue()
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}".encode()
    der_signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = ec_utils.decode_dss_signature(der_signature)
    raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return {
        "X-Bridge-Timestamp": timestamp,
        "X-Bridge-Nonce": nonce,
        "X-Bridge-Signature": base64.b64encode(raw_signature).decode(),
    }


class _SseEnvelopeReader:
    """Reads `count`-at-a-time SSE envelopes off one `aiohttp.ClientResponse`
    across repeated calls. The decode buffer is instance state, not a local
    that resets every call: a single `iter_any()` chunk often contains more
    than one frame (or a trailing partial frame), and a plain per-call
    buffer would silently discard whatever arrived after the envelope that
    satisfied an earlier call's `count` -- exactly the kind of loss this
    check exists to detect, but in the check's own harness instead of the
    server under test."""

    def __init__(self, response: aiohttp.ClientResponse) -> None:
        self._chunks = response.content.iter_any()
        self._buffer = ""

    async def read_n(self, count: int) -> list[dict]:
        envelopes: list[dict] = []

        def _drain_buffer() -> bool:
            while "\n\n" in self._buffer:
                frame, self._buffer = self._buffer.split("\n\n", 1)
                if frame.startswith("data: "):
                    envelopes.append(json.loads(frame[len("data: ") :]))
                    if len(envelopes) >= count:
                        return True
            return False

        if _drain_buffer():
            return envelopes
        async for chunk in self._chunks:
            self._buffer += chunk.decode()
            if _drain_buffer():
                return envelopes
        return envelopes


async def _abort_route(route: object) -> None:
    await route.abort()  # type: ignore[attr-defined]


async def _check_slow_client_resync_and_replayer_never_blocks(
    install_name: str, port: int, server: BridgeServer, steps: dict[str, bool], detail: dict[str, str]
) -> None:
    """A deliberately throttled client (never reads its response body) has
    its queue overflow and gets `resync`, while a second, healthy client
    keeps advancing the whole time -- proving the replayer/fan-out was
    never blocked by the slow one (NFR46, AD-38).

    The slow client's queue is shrunk via `server.stream_hub`'s own bound
    JUST long enough to register that one client (confirmed by its own
    `hello` frame arriving before the bound is restored) -- a live kit does
    not need an artificially tiny default queue for this to eventually
    happen for real, only a much longer flood than this check can afford
    to wait for.
    """
    slow_key, slow_token = _stream_device_keypair_and_token(server, "slow-stream-check-device")
    other_key, other_token = _stream_device_keypair_and_token(server, "other-stream-check-device")
    base_url = f"https://127.0.0.1:{port}"
    host_header = f"{install_name}:{port}"

    # Both clients resume from the CURRENT head, not 0 -- the check server's
    # background replayer (server.stream_hub.start(), begun once for the
    # whole run_check()) may already have recorded many events by the time
    # this step runs. Starting from 0 would hand each client an unbounded
    # backfill (events_after() is deliberately NOT subject to the bounded
    # queue -- it reads recorded history directly) that would drown out the
    # very overflow signal this check is trying to isolate.
    start_cursor = server.stream_hub.head_cursor

    async with aiohttp.ClientSession() as session:
        original_maxsize = server.stream_hub._client_queue_maxsize  # noqa: SLF001 -- deliberate, test-only throttle
        slow_headers = {
            "Host": host_header,
            "Authorization": f"Bearer {slow_token}",
            **_sign_stream_request(server, slow_key, "GET", "/api/stream/sse"),
        }
        server.stream_hub._client_queue_maxsize = 2  # noqa: SLF001
        try:
            slow_response = await session.get(
                f"{base_url}/api/stream/sse?cursor={start_cursor}", headers=slow_headers, ssl=False
            )
            slow_reader = _SseEnvelopeReader(slow_response)
            # blocks until the slow client's hello -- registration confirmed
            await asyncio.wait_for(slow_reader.read_n(1), timeout=5)
        finally:
            server.stream_hub._client_queue_maxsize = original_maxsize  # noqa: SLF001 -- every OTHER client gets the real bound

        other_headers = {
            "Host": host_header,
            "Authorization": f"Bearer {other_token}",
            **_sign_stream_request(server, other_key, "GET", "/api/stream/sse"),
        }
        other_response = await session.get(
            f"{base_url}/api/stream/sse?cursor={start_cursor}", headers=other_headers, ssl=False
        )
        other_reader = _SseEnvelopeReader(other_response)
        await asyncio.wait_for(other_reader.read_n(1), timeout=5)  # the healthy client's own hello

        for i in range(50):
            server.stream_hub.record_now(kind="alert", intensity=float(i) / 50, rendering=f"flood-{i}")

        try:
            other_events = await asyncio.wait_for(other_reader.read_n(5), timeout=5)
        except TimeoutError:
            other_events = []
        steps["replayer_never_blocks_other_clients"] = len(other_events) >= 5
        detail["replayer_never_blocks_other_clients"] = f"healthy_client_received={len(other_events)}_of_5_expected"

        try:
            slow_events = await asyncio.wait_for(slow_reader.read_n(1), timeout=5)
        except TimeoutError:
            slow_events = []
        steps["slow_client_gets_resync"] = any(e.get("type") == "resync" for e in slow_events)
        detail["slow_client_gets_resync"] = json.dumps(slow_events)

        slow_response.close()
        other_response.close()


async def _check_webtransport_and_sse(
    install_name: str,
    port: int,
    spki_pin: str,
    server: BridgeServer,
    steps: dict[str, bool],
    detail: dict[str, str],
    metrics: dict[str, dict[str, float]],
) -> None:
    """Proves Story 1.4's AC end to end through real Chromium, driven via
    `window.BridgeStream`: WebTransport connect, automatic SSE fallback,
    resume-after-a-dropped-connection with no loss/dupes (NFR13, proven by
    comparing sent vs. received cursors), staleness within roughly one
    heartbeat timeout (NFR11), and two-tab leader hand-off with no cursor
    gap (AD-31) -- plus connect/resume-replay time and this process's own
    memory as P50/P95 across `STREAM_METRIC_SAMPLE_COUNT` repeated samples.
    """
    origin = f"https://{install_name}:{port}"
    connect_times_ms: list[float] = []
    resume_times_ms: list[float] = []
    memory_samples_kb: list[float] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            args=[
                f"--host-resolver-rules=MAP {install_name} 127.0.0.1",
                f"--ignore-certificate-errors-spki-list={spki_pin}",
            ]
        )
        try:

            async def new_signed_in_page(device_name: str):
                ctx = await browser.new_context()
                pg = await ctx.new_page()
                await pg.goto(f"{origin}/", wait_until="load")
                ticket = server.enrollment_tickets.mint(device_name)
                enrollment = await pg.evaluate(
                    "([ticket, name]) => window.BridgeAuth.completeDeviceEnrollment(ticket, name)",
                    [ticket, device_name],
                )
                return ctx, pg, enrollment

            async def sample_connect_and_resume(device_name: str, *, record_first_sign_in: bool = False):
                """Connects, drops, and resumes once. Returns
                `(connected, gap_resumed_cleanly, cursor_before_drop, log_after_resume)`.
                Only a SUCCESSFUL sample's timings are added to the shared
                `connect_times_ms`/`resume_times_ms` metric lists -- a timed-out
                sample must never corrupt the P50/P95 metrics with a partial or
                meaningless measurement."""
                ctx, pg, enrollment = await new_signed_in_page(device_name)
                if record_first_sign_in:
                    steps["stream_device_signed_in"] = bool(enrollment.get("token"))
                    detail["stream_device_signed_in"] = json.dumps(enrollment)
                try:
                    started = time.monotonic()
                    await pg.evaluate("() => window.BridgeStream.connect()")
                    connected = await _wait_until(
                        lambda: pg.evaluate("() => window.BridgeStream.getCarrier() === 'webtransport'"), timeout=8
                    )
                    if connected:
                        connect_times_ms.append((time.monotonic() - started) * 1000)
                        memory_samples_kb.append(float(_process_rss_kb(os.getpid())))

                    cursor_before_drop = await pg.evaluate("() => window.BridgeStream.getCursor()")
                    log_before_drop = await pg.evaluate("() => window.BridgeStream.getReceivedCursorLog()")
                    resume_started = time.monotonic()
                    await pg.evaluate("() => window.BridgeStream.simulateDrop()")
                    resumed = await _wait_until(
                        lambda: pg.evaluate(
                            "cursorBefore => window.BridgeStream.getCursor() > cursorBefore", cursor_before_drop
                        ),
                        timeout=8,
                    )
                    if resumed:
                        resume_times_ms.append((time.monotonic() - resume_started) * 1000)
                    log_after_resume = await pg.evaluate("() => window.BridgeStream.getReceivedCursorLog()")
                finally:
                    await ctx.close()
                new_cursors = log_after_resume[len(log_before_drop) :]
                no_duplicates = len(log_after_resume) == len(set(log_after_resume))
                continuous_from_drop = (
                    bool(new_cursors)
                    and new_cursors[0] == cursor_before_drop + 1
                    and new_cursors == list(range(new_cursors[0], new_cursors[0] + len(new_cursors)))
                )
                gap_resumed_cleanly = resumed and no_duplicates and continuous_from_drop
                return connected, gap_resumed_cleanly, cursor_before_drop, log_after_resume

            sample_results = [
                await sample_connect_and_resume(
                    f"stream-check-device-{sample_index}", record_first_sign_in=(sample_index == 0)
                )
                for sample_index in range(STREAM_METRIC_SAMPLE_COUNT)
            ]
            # EVERY sample must succeed -- a flaky/broken carrier on a later
            # sample must not be masked by a passing first sample.
            steps["webtransport_session"] = all(result[0] for result in sample_results)
            detail["webtransport_session"] = (
                f"connected {sum(1 for result in sample_results if result[0])}/{len(sample_results)} samples"
            )
            steps["cursor_resume_no_loss_no_dupes"] = all(result[1] for result in sample_results)
            first_cursor_before_drop, first_log_after_resume = sample_results[0][2], sample_results[0][3]
            detail["cursor_resume_no_loss_no_dupes"] = json.dumps(
                {
                    "gap_resumed_cleanly_samples": f"{sum(1 for result in sample_results if result[1])}/{len(sample_results)}",
                    "cursor_before_drop": first_cursor_before_drop,
                    "log": first_log_after_resume[-20:],
                }
            )

            # -- forced SSE fallback (NFR14) --------------------------------
            fallback_context, fallback_page, _fallback_enrollment = await new_signed_in_page(
                "stream-fallback-check-device"
            )
            await fallback_page.evaluate("() => window.BridgeStream.forceFallback(true)")
            await fallback_page.evaluate("() => window.BridgeStream.connect()")
            fell_back = await _wait_until(
                lambda: fallback_page.evaluate("() => window.BridgeStream.getCarrier() === 'sse'"), timeout=8
            )
            steps["sse_fallback_forced"] = fell_back
            detail["sse_fallback_forced"] = "carrier=sse" if fell_back else "never reached carrier=sse"

            # -- resume-from-cursor with no loss/dupes on the SSE carrier too
            # (NFR13): the AC requires this proven on BOTH carriers, not just
            # WebTransport -- mirrors sample_connect_and_resume's drop/resume/
            # cursor-log comparison above, driven through the real
            # `_connectSse`/`_readEnvelopeLines` browser path.
            sse_cursor_before_drop = await fallback_page.evaluate("() => window.BridgeStream.getCursor()")
            sse_log_before_drop = await fallback_page.evaluate("() => window.BridgeStream.getReceivedCursorLog()")
            await fallback_page.evaluate("() => window.BridgeStream.simulateDrop()")
            sse_resumed = await _wait_until(
                lambda: fallback_page.evaluate(
                    "cursorBefore => window.BridgeStream.getCursor() > cursorBefore", sse_cursor_before_drop
                ),
                timeout=8,
            )
            sse_log_after_resume = await fallback_page.evaluate("() => window.BridgeStream.getReceivedCursorLog()")
            sse_new_cursors = sse_log_after_resume[len(sse_log_before_drop) :]
            sse_no_duplicates = len(sse_log_after_resume) == len(set(sse_log_after_resume))
            sse_continuous_from_drop = (
                bool(sse_new_cursors)
                and sse_new_cursors[0] == sse_cursor_before_drop + 1
                and sse_new_cursors == list(range(sse_new_cursors[0], sse_new_cursors[0] + len(sse_new_cursors)))
            )
            steps["sse_cursor_resume_no_loss_no_dupes"] = sse_resumed and sse_no_duplicates and sse_continuous_from_drop
            detail["sse_cursor_resume_no_loss_no_dupes"] = json.dumps(
                {"cursor_before_drop": sse_cursor_before_drop, "log": sse_log_after_resume[-20:]}
            )

            # -- staleness within roughly one heartbeat timeout (NFR11) -----
            # Every stream route is intercepted and aborted for real (never
            # a code-level patch, same principle _check_push_and_mic's
            # notificationclick-away-from-home step uses) so the reconnect
            # loop's own retries keep failing and lastActivityAt stays
            # frozen -- a genuinely sustained outage, not a one-off blip.
            await fallback_context.route("**/api/stream/**", _abort_route)
            await fallback_page.evaluate("() => window.BridgeStream.simulateDrop()")
            went_stale = await _wait_until(
                lambda: fallback_page.evaluate("() => window.BridgeStream.isStale()"),
                timeout=stream_module.DEFAULT_HEARTBEAT_INTERVAL_SECONDS * 4,
            )
            steps["stale_after_missed_heartbeat"] = went_stale
            detail["stale_after_missed_heartbeat"] = json.dumps({"went_stale": went_stale})
            await fallback_context.unroute("**/api/stream/**")
            await fallback_context.close()

            # -- leader hand-off on tab close, no cursor gap (AD-31) --------
            leader_context, leader_page, _leader_enrollment = await new_signed_in_page("stream-leader-check-device")
            await leader_page.evaluate("() => window.BridgeStream.connect()")
            await _wait_until(
                lambda: leader_page.evaluate("() => window.BridgeStream.getCarrier() !== null"), timeout=8
            )
            follower_page = await leader_context.new_page()
            await follower_page.goto(f"{origin}/", wait_until="load")
            await follower_page.evaluate("() => window.BridgeStream.connect()")
            await asyncio.sleep(0.3)  # lets the follower's lock request actually queue behind the held lock
            leader_was_leader = await leader_page.evaluate("() => window.BridgeStream.isLeader()")
            follower_was_leader_before = await follower_page.evaluate("() => window.BridgeStream.isLeader()")
            cursor_before_close = await leader_page.evaluate("() => window.BridgeStream.getCursor()")
            await leader_page.close()  # the real scenario: the leader TAB closes
            # `isLeader()` flips true the MOMENT the Web Lock is (re-)granted,
            # before the new leader's own reconnect has actually completed --
            # waiting on cursor >= cursor_before_close too is what actually
            # proves "no cursor gap", not just "took over eventually".
            handed_off = await _wait_until(
                lambda: follower_page.evaluate(
                    "cursorBefore => window.BridgeStream.isLeader() && window.BridgeStream.getCursor() >= cursorBefore",
                    cursor_before_close,
                ),
                timeout=8,
            )
            cursor_after_handoff = await follower_page.evaluate("() => window.BridgeStream.getCursor()")
            steps["leader_handoff_no_cursor_gap"] = (
                leader_was_leader
                and not follower_was_leader_before
                and handed_off
                and cursor_after_handoff >= cursor_before_close
            )
            detail["leader_handoff_no_cursor_gap"] = json.dumps(
                {
                    "leader_was_leader": leader_was_leader,
                    "handed_off": handed_off,
                    "cursor_before_close": cursor_before_close,
                    "cursor_after_handoff": cursor_after_handoff,
                }
            )
            await leader_context.close()
        finally:
            await browser.close()

    await _check_slow_client_resync_and_replayer_never_blocks(install_name, port, server, steps, detail)

    metrics["connect_time_ms"] = _p50_p95(connect_times_ms)
    metrics["resume_replay_time_ms"] = _p50_p95(resume_times_ms)
    metrics["server_process_memory_kb"] = _p50_p95(memory_samples_kb)
    detail["local_network_access_prompt"] = json.dumps(
        {
            "applicable": False,
            "reason": (
                "not exercised by headless Chromium on the build host -- a real device's Local Network "
                "Access prompt is Story 1.6's job"
            ),
        }
    )
    detail["cert_hash_checklist"] = json.dumps(
        {
            # A static config-presence check, NOT a claim that the browser
            # ever fetched or used the hashes -- `cert_hashes_worked` below
            # is the actual over-the-wire signal (a real WebTransport
            # session only succeeds if the fetched hashes matched).
            "webtransport_cert_store_configured": server.config.webtransport_cert_store is not None,
            "cert_hashes_worked": bool(connect_times_ms) and steps.get("webtransport_session", False),
        }
    )


# -- Story 1.5: the strict CSP/Trusted Types policy holds on every browser --
#
# Everything below is driven against the kit's real global security-headers
# middleware and the real committed frontend/build/ output at /csp-check/ --
# never a second reimplementation of either. SECURITY_HEADERS is imported
# from .server (never re-declared here) so the enforced policy and the
# asserted one can never drift apart.

FRONTEND_SRC_DIR = Path(__file__).parent.parent / "frontend" / "src"


def _count_trusted_types_create_policy_calls(src_dir: Path) -> int:
    """Counts `.createPolicy(` occurrences across this kit's OWN front-end
    source (`.ts`/`.svelte` files under `frontend/src/` -- never
    `node_modules/`, never the built bundle). NFR22 ("exactly one named
    Trusted Types policy exists across the kit's front-end code") is scoped
    to code this kit wrote, not to every `trustedTypes.createPolicy` call
    any dependency's own runtime might make internally -- see
    _check_csp_and_frontend_build's docstring.

    `//` line comments are stripped before counting, via `_strip_line_comment`
    (string-literal-aware, so a `//` inside a `'`/`"`/`` ` `` string is never
    mistaken for a comment start) -- so a comment that merely MENTIONS
    `.createPolicy(` -- e.g. explaining this exact rule -- can never be
    miscounted as a second call site, and a real second call site after a
    `//`-containing string literal on the same line is never undercounted.
    """
    count = 0
    for path in sorted(src_dir.rglob("*")):
        if path.suffix not in {".ts", ".svelte"}:
            continue
        for line in path.read_text().splitlines():
            count += _strip_line_comment(line).count(".createPolicy(")
    return count


def _strip_line_comment(line: str) -> str:
    """Returns `line` with any trailing `//` line comment removed -- but,
    unlike a naive `line.split("//", 1)[0]`, tracks whether the scan
    position is inside a `'`/`"`/`` ` `` string literal first, so a `//`
    that is merely PART OF a string (e.g. an `"https://..."` substring
    preceding a real second `.createPolicy(` call later on the same line)
    is never mistaken for a comment start -- which would truncate the line
    before that real call site and undercount it.
    """
    in_string: str | None = None
    i = 0
    length = len(line)
    while i < length:
        ch = line[i]
        if in_string is not None:
            if ch == "\\":
                i += 2  # skip the escaped character too -- e.g. an escaped quote
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_string = ch
            i += 1
            continue
        if ch == "/" and i + 1 < length and line[i + 1] == "/":
            return line[:i]
        i += 1
    return line


async def _check_csp_and_frontend_build(
    install_name: str,
    port: int,
    spki_pin: str,
    steps: dict[str, bool],
    detail: dict[str, str],
) -> None:
    """Proves Story 1.5's AC end to end through real Chromium: the exact
    AD-36 header set on `/csp-check/`, the owl mark rendered both as a DOM
    `<img>` and inside the Three.js scene, the WebGPU-unavailable-on-
    headless-Chromium fallback to WebGL2, that the kit's one named Trusted
    Types policy is actually created exactly once (proving it is real code,
    not zero -- dead/never-registered -- and not more than one, NFR22), zero
    `securitypolicyviolation` events on a real page load, and a self-test
    proving that violation detector is not vacuous (a deliberate raw-string
    assignment to a dynamically created `<script>` element's `.textContent`,
    in a disposable context -- a genuine Trusted-Types-gated sink that
    `require-trusted-types-for 'script'` blocks for real page mutations;
    see the self-test's own inline comment for why a bare `eval()` call
    through `page.evaluate` does NOT work here).
    """
    origin = f"https://{install_name}:{port}"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            args=[
                f"--host-resolver-rules=MAP {install_name} 127.0.0.1",
                f"--ignore-certificate-errors-spki-list={spki_pin}",
            ]
        )
        try:
            # `add_init_script` runs before ANY page script, including the
            # frontend build's own module evaluation -- the only way to
            # observe every `securitypolicyviolation` event from the very
            # first tick, rather than racing App.svelte's own onMount.
            init_script = """
                window.__cspViolations = [];
                document.addEventListener('securitypolicyviolation', (event) => {
                    window.__cspViolations.push({
                        violatedDirective: event.violatedDirective,
                        blockedURI: event.blockedURI,
                    });
                });
            """
            context = await browser.new_context()
            await context.add_init_script(init_script)
            page = await context.new_page()
            response = await page.goto(f"{origin}/csp-check/", wait_until="load")

            # -- exact header set (Playwright's response.headers keys are
            # already lower-cased) --------------------------------------
            response_headers = response.headers if response else {}
            header_matches = {
                name: response_headers.get(name.lower()) == value for name, value in SECURITY_HEADERS.items()
            }
            steps["csp_header_set_exact"] = bool(response) and response.ok and all(header_matches.values())
            detail["csp_header_set_exact"] = json.dumps(
                {"expected": SECURITY_HEADERS, "matches": header_matches}
            )

            # -- owl mark rendered in the DOM, imported from
            # logo/stackowl-mark.svg (AD-40) ------------------------------
            img_loaded = await page.evaluate(
                "() => { const img = document.querySelector('[data-testid=\"owl-mark-img\"]'); "
                "return !!img && img.complete && img.naturalWidth > 0; }"
            )
            steps["owl_mark_rendered_in_dom"] = bool(img_loaded)
            detail["owl_mark_rendered_in_dom"] = json.dumps({"img_loaded": img_loaded})

            # -- WebGPU unavailable on default headless Chromium ->
            # WebGPURenderer auto-falls back to WebGL2 (scene.ts) ----------
            backend_ready = await _wait_until(
                lambda: page.evaluate(
                    "() => !!(window.__cspCheckRendererBackend && window.__cspCheckRendererBackend())"
                ),
                timeout=8,
            )
            backend = (
                await page.evaluate("() => window.__cspCheckRendererBackend()") if backend_ready else None
            )
            steps["webgl2_fallback_backend"] = backend == "webgl2"
            detail["webgl2_fallback_backend"] = json.dumps({"backend": backend})

            # -- exactly one named Trusted Types policy call across the
            # kit's OWN front-end source (NFR22) -- a STATIC source count,
            # deliberately never a runtime count of every
            # `trustedTypes.createPolicy` call the page makes: Svelte's own
            # compiled runtime registers a second, unrelated policy
            # (`svelte-trusted-html`, in
            # svelte/internal/client/dom/reconciler.js) purely to mount
            # static markup templates -- framework code this kit never
            # wrote, which a runtime count could never distinguish from
            # this kit's own one call in App.svelte/scene.ts.
            policy_call_count = _count_trusted_types_create_policy_calls(FRONTEND_SRC_DIR)
            steps["trusted_types_policy_created_exactly_once"] = policy_call_count == 1
            detail["trusted_types_policy_created_exactly_once"] = json.dumps(
                {"count": policy_call_count, "src_dir": str(FRONTEND_SRC_DIR)}
            )

            # -- ... and genuinely exercised, not dead code (NFR22): the
            # renderer-backend status label is built ONLY through the
            # Trusted-Types-gated Range.createContextualFragment sink (see
            # App.svelte) -- if that label reflects the real resolved
            # backend, the policy demonstrably ran for real.
            label_ready = await _wait_until(
                lambda: page.evaluate(
                    "() => { const el = document.querySelector('[data-testid=\"renderer-backend\"]'); "
                    "return !!el && el.textContent.trim().length > 0; }"
                ),
                timeout=8,
            )
            label_text = (
                await page.evaluate(
                    "() => document.querySelector('[data-testid=\"renderer-backend\"]').textContent.trim()"
                )
                if label_ready
                else None
            )
            steps["trusted_types_policy_genuinely_used"] = bool(label_ready) and label_text == backend
            detail["trusted_types_policy_genuinely_used"] = json.dumps(
                {"label_text": label_text, "backend": backend}
            )

            # -- zero securitypolicyviolation events on this real page load -
            violations = await page.evaluate("() => window.__cspViolations")
            steps["zero_csp_violations_on_real_page"] = violations == []
            detail["zero_csp_violations_on_real_page"] = json.dumps({"violations": violations})

            await context.close()

            # -- detector self-test, in a DISPOSABLE context (never the one
            # above) so a deliberately tripped violation can never pollute
            # the "zero violations" proof just made. Note: a bare
            # `page.evaluate(() => eval(...))` does NOT reproduce a real
            # violation here -- Playwright/CDP's `Runtime.evaluate` is
            # exempt from the page's own CSP eval restriction (a documented
            # devtools-evaluation carve-out), so it would silently succeed
            # and make this detector vacuous. Assigning a raw string to a
            # dynamically created `<script>` element's `.textContent` is a
            # genuine TrustedScript sink that IS enforced for real DOM
            # mutations made from page.evaluate -- it throws inside the
            # page (caught below) and fires a real
            # `securitypolicyviolation` with `violatedDirective:
            # 'require-trusted-types-for'`.
            self_test_context = await browser.new_context()
            await self_test_context.add_init_script(init_script)
            self_test_page = await self_test_context.new_page()
            await self_test_page.goto(f"{origin}/csp-check/", wait_until="load")
            await self_test_page.evaluate(
                "() => { try { "
                "const s = document.createElement('script'); "
                "s.textContent = 'window.__deliberateInlineScriptRan = true;'; "
                "document.head.appendChild(s); "
                "} catch (err) { /* expected: require-trusted-types-for blocks the raw-string assignment */ } }"
            )
            await _wait_until(
                lambda: self_test_page.evaluate("() => window.__cspViolations.length > 0"), timeout=5
            )
            self_test_violations = await self_test_page.evaluate("() => window.__cspViolations")
            self_test_script_ran = await self_test_page.evaluate("() => window.__deliberateInlineScriptRan === true")
            steps["csp_violation_detector_self_test"] = (
                bool(self_test_violations)
                and any(
                    "trusted-types" in (violation.get("violatedDirective") or "")
                    for violation in self_test_violations
                )
                and not self_test_script_ran
            )
            detail["csp_violation_detector_self_test"] = json.dumps(
                {"violations": self_test_violations, "script_ran": self_test_script_ran}
            )
            await self_test_context.close()
        finally:
            await browser.close()


async def run_check(install_name: str = DEFAULT_CHECK_INSTALL_NAME) -> CheckResult:
    """Start a real instance of the kit's server and prove the story's AC,
    including the passkey/device-key/device-approval ceremonies driven
    through a CDP virtual authenticator and the push/microphone proofs
    (Story 1.3), the WebTransport/SSE stream proofs (Story 1.4), and the
    CSP/Trusted-Types/frontend-build proofs (Story 1.5), then write
    `results/B1-build-host-chromium.json`,
    `results/B1-passkey-desktop-chrome-automated.json`,
    `results/B1-push-mic-desktop-chrome-automated.json`,
    `results/B2-carrier-desktop-chrome-automated.json`, and
    `results/B5-desktop-chrome-automated.json`."""
    port = _free_port()
    artifacts = ca_module.setup(install_name)
    webtransport_cert_store = webtransport_cert_module.WebTransportCertStore(install_name)
    config = ServerConfig(
        install_name=install_name,
        port=port,
        ca_artifacts=artifacts,
        host="127.0.0.1",
        webtransport_cert_store=webtransport_cert_store,
    )
    server = BridgeServer(config)
    runner = await server.start()
    # Constructing the listener is pure in-memory setup (never fails), so it
    # is safe before the try below; the actual bind (`.start()`) is not --
    # it must run inside the try so a bind failure still triggers the
    # finally's `runner.cleanup()` instead of leaking the HTTPS listener.
    webtransport_listener = webtransport_server_module.WebTransportListener(
        install_name=install_name,
        port=port,
        host="127.0.0.1",
        cert_store=webtransport_cert_store,
        tokens=server.tokens,
        nonces=server.nonces,
        hub=server.stream_hub,
    )

    steps: dict[str, bool] = {}
    detail: dict[str, str] = {}
    passkey_steps: dict[str, bool] = {}
    passkey_detail: dict[str, str] = {}
    push_mic_steps: dict[str, bool] = {}
    push_mic_detail: dict[str, str] = {}
    stream_steps: dict[str, bool] = {}
    stream_detail: dict[str, str] = {}
    stream_metrics: dict[str, dict[str, float]] = {}
    csp_steps: dict[str, bool] = {}
    csp_detail: dict[str, str] = {}
    try:
        server.stream_hub.start()
        await webtransport_listener.start()
        spki_pin = _spki_pin(artifacts.ca_cert_pem)
        await _check_certificate_chain_and_redirect(install_name, port, spki_pin, steps, detail)
        await _check_subnet_refusal(install_name, port, steps, detail)
        await _check_passkey_and_device_approval(install_name, port, spki_pin, server, passkey_steps, passkey_detail)
        await _check_push_and_mic(install_name, port, spki_pin, server, push_mic_steps, push_mic_detail)
        await _check_webtransport_and_sse(
            install_name, port, spki_pin, server, stream_steps, stream_detail, stream_metrics
        )
        await _check_csp_and_frontend_build(install_name, port, spki_pin, csp_steps, csp_detail)
    finally:
        await webtransport_listener.stop()
        await server.stream_hub.stop()
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

    push_mic_ok = bool(push_mic_steps) and all(push_mic_steps.values())
    push_mic_payload = {
        "kit_version": KIT_VERSION,
        "check": "push-mic-desktop-chrome-automated",
        "install_name": install_name,
        "browser": "chromium",
        "os": platform.platform(),
        "steps": push_mic_steps,
        "detail": push_mic_detail,
        "ok": push_mic_ok,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    push_mic_result_path = _write_result(push_mic_payload, "B1-push-mic-desktop-chrome-automated.json")

    stream_ok = bool(stream_steps) and all(stream_steps.values())
    stream_payload = {
        "kit_version": KIT_VERSION,
        "check": "stream-carrier-desktop-chrome-automated",
        "install_name": install_name,
        "browser": "chromium",
        "os": platform.platform(),
        "steps": stream_steps,
        "detail": stream_detail,
        "metrics": stream_metrics,
        "ok": stream_ok,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    stream_result_path = _write_result(stream_payload, "B2-carrier-desktop-chrome-automated.json")

    csp_ok = bool(csp_steps) and all(csp_steps.values())
    csp_payload = {
        "kit_version": KIT_VERSION,
        "check": "csp-frontend-desktop-chrome-automated",
        "install_name": install_name,
        "browser": "chromium",
        "os": platform.platform(),
        "steps": csp_steps,
        "detail": csp_detail,
        "ok": csp_ok,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    csp_result_path = _write_result(csp_payload, "B5-desktop-chrome-automated.json")

    return CheckResult(
        ok=ok and passkey_ok and push_mic_ok and stream_ok and csp_ok,
        steps={**steps, **passkey_steps, **push_mic_steps, **stream_steps, **csp_steps},
        detail={**detail, **passkey_detail, **push_mic_detail, **stream_detail, **csp_detail},
        result_path=result_path,
        passkey_result_path=passkey_result_path,
        push_mic_result_path=push_mic_result_path,
        stream_result_path=stream_result_path,
        csp_result_path=csp_result_path,
    )


def _write_result(payload: dict[str, object], filename: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / filename
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path
