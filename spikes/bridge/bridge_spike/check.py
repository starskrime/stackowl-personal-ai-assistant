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
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import aiohttp
import http_ece
import requests
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from playwright.async_api import async_playwright

from . import ca as ca_module
from . import push as push_module
from . import push_stub as push_stub_module
from .server import KIT_VERSION, RESULTS_DIR, BridgeServer, ServerConfig

DEFAULT_CHECK_INSTALL_NAME = "bridge-spike-check.local"


@dataclass
class CheckResult:
    ok: bool
    steps: dict[str, bool]
    detail: dict[str, str]
    result_path: Path
    passkey_result_path: Path | None = None
    push_mic_result_path: Path | None = None


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


async def run_check(install_name: str = DEFAULT_CHECK_INSTALL_NAME) -> CheckResult:
    """Start a real instance of the kit's server and prove the story's AC,
    including the passkey/device-key/device-approval ceremonies driven
    through a CDP virtual authenticator and the push/microphone proofs
    (Story 1.3), then write `results/B1-build-host-chromium.json`,
    `results/B1-passkey-desktop-chrome-automated.json`, and
    `results/B1-push-mic-desktop-chrome-automated.json`."""
    port = _free_port()
    artifacts = ca_module.setup(install_name)
    config = ServerConfig(install_name=install_name, port=port, ca_artifacts=artifacts, host="127.0.0.1")
    server = BridgeServer(config)
    runner = await server.start()

    steps: dict[str, bool] = {}
    detail: dict[str, str] = {}
    passkey_steps: dict[str, bool] = {}
    passkey_detail: dict[str, str] = {}
    push_mic_steps: dict[str, bool] = {}
    push_mic_detail: dict[str, str] = {}
    try:
        spki_pin = _spki_pin(artifacts.ca_cert_pem)
        await _check_certificate_chain_and_redirect(install_name, port, spki_pin, steps, detail)
        await _check_subnet_refusal(install_name, port, steps, detail)
        await _check_passkey_and_device_approval(install_name, port, spki_pin, server, passkey_steps, passkey_detail)
        await _check_push_and_mic(install_name, port, spki_pin, server, push_mic_steps, push_mic_detail)
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

    return CheckResult(
        ok=ok and passkey_ok and push_mic_ok,
        steps={**steps, **passkey_steps, **push_mic_steps},
        detail={**detail, **passkey_detail, **push_mic_detail},
        result_path=result_path,
        passkey_result_path=passkey_result_path,
        push_mic_result_path=push_mic_result_path,
    )


def _write_result(payload: dict[str, object], filename: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / filename
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path
