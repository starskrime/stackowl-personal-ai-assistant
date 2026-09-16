#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "cryptography>=42,<49",
#     "aiohttp>=3.11,<4",
#     "playwright>=1.59,<2",
#     "webauthn>=2.0,<3",
#     "python-telegram-bot>=21.0,<23",
#     "pyyaml>=6.0.0,<7",
#     "keyring>=25.0.0,<26",
#     "pywebpush>=2.5,<3",
#     "http-ece>=1.2,<2",
#     "requests>=2.34,<3",
#     "aioquic>=1.3,<2",
# ]
# ///
"""Bridge TLS/mDNS spike kit entrypoint.

THROWAWAY: everything under spikes/bridge/ is a standalone kit with its own
inline dependencies (declared above via PEP 723). It never enters the
platform's pyproject.toml/uv.lock. Run it with:

    uv run spikes/bridge/kit.py <start|renew|check>

`uv run` reads the `# /// script` block above and builds/caches an isolated
environment for this file — no `pyproject.toml`/`uv.lock` edit is involved.

Subcommands:
  start   Generate a fresh ephemeral CA + leaf cert, advertise the install
          name via the OS's mDNS tool, and serve the HTTPS PWA until Ctrl-C.
  renew   Same as start, but framed as a renewal: prints the new fingerprint
          and the guided steps to drop the old CA and trust the new one.
  check   Run the kit's own automated done-check (Chromium via Playwright)
          and write results/B1-build-host-chromium.json. This is the
          story's own completion bar — real-device runs are Story 1.6's.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bridge_spike import ca as ca_module  # noqa: E402
from bridge_spike import mdns  # noqa: E402
from bridge_spike import push as push_module  # noqa: E402
from bridge_spike import setup_code as setup_code_module  # noqa: E402
from bridge_spike import telegram_bot  # noqa: E402
from bridge_spike import webtransport_cert as webtransport_cert_module  # noqa: E402
from bridge_spike.device_requests import DeviceRequest  # noqa: E402
from bridge_spike.server import (  # noqa: E402
    RESULTS_DIR,
    BridgeServer,
    ServerConfig,
    primary_local_ipv4_address,
)
from bridge_spike.webtransport_server import WebTransportListener  # noqa: E402

# `bridge_spike.check` imports playwright, which `start`/`renew` never need --
# imported lazily inside cmd_check()/main() so `uv run --with cryptography
# --with aiohttp python -m pytest spikes/bridge/tests` (no playwright) can
# still import and exercise this module for its non-check subcommands.

DEFAULT_PORT = 8443
# Well under AD-14's 14-day ceiling -- rotating at roughly the halfway
# point of a cert's own validity window leaves generous overlap (the "next"
# cert has been advertised, unused, for days before it ever becomes
# "current") even on a kit process that runs for a long stretch.
WEBTRANSPORT_CERT_ROTATION_INTERVAL_SECONDS = (webtransport_cert_module.MAX_VALIDITY_DAYS // 2) * 24 * 60 * 60

# Condensed per-platform trust steps -- the AC requires these printed to the
# terminal, not just pointed at from the web page. The full guided text (same
# content) also lives on the kit's own landing page in bridge_spike/static/index.html.
_TRUST_STEPS: dict[str, list[str]] = {
    "iOS": [
        "Open the CA certificate file printed above (AirDrop it to the device, or transfer it another way).",
        "Settings > General > VPN & Device Management > select the profile > Install.",
        "Settings > General > About > Certificate Trust Settings > enable full trust for the new CA.",
        "Reload the page.",
    ],
    "Android": [
        "Copy the CA certificate file printed above onto the device.",
        "Settings > Security > Encryption & credentials > Install a certificate > CA certificate.",
        "Confirm the install-time warning (installing a CA certificate is intentional here).",
        "Reload the page.",
    ],
    "Desktop Chrome": [
        'macOS: open the CA certificate file printed above in Keychain Access, set it to "Always Trust".',
        "Linux: copy the CA certificate file printed above into /usr/local/share/ca-certificates/ and run "
        "update-ca-certificates, or import it via certutil into ~/.pki/nssdb for Chrome/Chromium's own NSS store.",
        "Restart the browser, then reload the page.",
    ],
}


def _default_install_name() -> str:
    host = socket.gethostname().split(".")[0].lower() or "host"
    return f"{host}-bridge-spike.local"


def _print_trust_steps(fingerprint: str, install_name: str, port: int, ca_cert_path: Path | None = None) -> None:
    print(f"\nCA fingerprint (SHA-256): {fingerprint}")
    if ca_cert_path is not None:
        print(f"CA certificate file: {ca_cert_path}")
    print(f"Install name: {install_name}   Port: {port}")
    print(f"\nTrust this CA on each device before opening https://{install_name}:{port}/:")
    for platform_name, steps in _TRUST_STEPS.items():
        print(f"\n{platform_name}:")
        for index, step in enumerate(steps, start=1):
            print(f"  {index}. {step}")


def _print_renewal_ceremony(fingerprint: str) -> None:
    print(
        "\nRenewal ceremony: remove the OLD CA from every device's trust "
        "store first, then repeat the trust steps above for this NEW "
        f"fingerprint ({fingerprint}). The old leaf certificate "
        "keeps working only until each device re-trusts the new CA."
    )


def _print_setup_code(code: str) -> None:
    print(f"\nSetup code (first passkey enrolment only, used once): {code}")


def _start_test_telegram_bot(server: BridgeServer) -> telegram_bot.TestTelegramBot | None:
    """Starts the kit's own *test* Telegram bot when a test token is
    configured (AD-37) -- never the platform's bot/token. Returns None
    (terminal-only) when no test token is configured; raises
    `telegram_bot.BotTokenCollisionError` when the configured test token
    equals the platform's resolved `telegram_channel.bot_token`, so the kit
    refuses to start rather than risk knocking the live platform bot offline.
    """
    test_token = os.environ.get(telegram_bot.TEST_BOT_TOKEN_ENV)
    if not test_token:
        return None
    allowed_user_ids = telegram_bot.allowed_user_ids_from_env(os.environ.get(telegram_bot.TEST_ALLOWED_USER_IDS_ENV))

    async def _approve_via_telegram(request_id: str, code: str) -> None:
        server.approve_device_request(request_id, code)

    bot = telegram_bot.TestTelegramBot(test_token, allowed_user_ids, approve_callback=_approve_via_telegram)

    async def _notify_device_request(device_request: DeviceRequest) -> None:
        await bot.send_device_request(device_request.device_name, device_request.code, device_request.request_id)

    server.on_device_request_created = _notify_device_request
    return bot


async def _serve(install_name: str, port: int, *, renewing: bool) -> int:
    artifacts = ca_module.renew(install_name) if renewing else ca_module.setup(install_name)
    print(f"{'Renewed' if renewing else 'Generated'} ephemeral CA + leaf certificate for {install_name!r}.")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ca_cert_path = RESULTS_DIR / "ca.pem"
    ca_cert_path.write_bytes(artifacts.ca_cert_pem)
    _print_trust_steps(artifacts.fingerprint, install_name, port, ca_cert_path)
    if renewing:
        _print_renewal_ceremony(artifacts.fingerprint)

    address = primary_local_ipv4_address()
    advertisement = mdns.start_advertising(install_name, port, address)
    if advertisement.running:
        print(f"\nAdvertising {install_name!r} via: {' '.join(advertisement.command)}")

    # Minted exactly once, here, at process start -- no route anywhere can
    # mint another (see bridge_spike/setup_code.py).
    setup_codes = setup_code_module.SetupCodeStore()
    # Same discipline as the CA above: one VAPID keypair per kit run, wired
    # into the config before the server (and thus every route) exists.
    vapid_private_key = push_module.generate_vapid_keypair()
    # WebTransport's own short-lived, self-signed cert pair (AD-14) -- kept
    # in memory only, exactly like the VAPID key above; the signed cert-hash
    # snapshot route (server.py) reads current+next straight off this config.
    webtransport_cert_store = webtransport_cert_module.WebTransportCertStore(install_name)
    config = ServerConfig(
        install_name=install_name,
        port=port,
        ca_artifacts=artifacts,
        setup_codes=setup_codes,
        vapid_private_key=vapid_private_key,
        webtransport_cert_store=webtransport_cert_store,
    )
    server = BridgeServer(config)

    try:
        bot = _start_test_telegram_bot(server)
    except telegram_bot.BotTokenCollisionError as exc:
        print(f"\n{exc}")
        advertisement.stop()
        return 1

    runner = await server.start()
    # Constructing the listener is pure in-memory setup (never fails), so it
    # is safe before the try below; the actual bind (`.start()`) is not.
    webtransport_listener = WebTransportListener(
        install_name=install_name,
        port=port,
        host=config.host,
        cert_store=webtransport_cert_store,
        tokens=server.tokens,
        nonces=server.nonces,
        hub=server.stream_hub,
    )
    rotation_task: asyncio.Task[None] | None = None
    # EVERYTHING from here on is wrapped in the same try/finally as the
    # signal-wait below: `runner`/`advertisement`/the stream carriers must
    # be cleaned up on ANY exit path once the HTTPS listener is up, not only
    # the Ctrl-C path -- a WebTransport bind failure or a `bot.start()`
    # failure (bad token, no network) must not orphan the mDNS subprocess or
    # leave the HTTPS listener bound.
    try:
        # The one shared event source both stream carriers read from (Story
        # 1.4, AD-31) -- started once the HTTPS listener itself is up,
        # stopped in the same finally block below.
        server.stream_hub.start()
        # The aioquic WebTransport listener: SAME port number as the HTTPS
        # site above, over UDP (AD-13). Bound after `server.start()`
        # succeeds so a TCP bind failure never leaves a UDP listener
        # orphaned with nothing to pair it with.
        await webtransport_listener.start()
        rotation_task = asyncio.ensure_future(_rotate_webtransport_cert_forever(webtransport_listener))

        print(f"\nServing https://{install_name}:{port}/ — Ctrl-C to stop.")
        _print_setup_code(setup_codes.code)

        if bot is not None:
            try:
                await bot.start()
            except Exception as exc:  # noqa: BLE001 — any start failure (bad token, no network, ...)
                print(f"\nCould not start the kit's test Telegram bot: {exc}")
                print(
                    f"Remedy: verify {telegram_bot.TEST_BOT_TOKEN_ENV} is a valid token from @BotFather "
                    "and that this host has network access, then restart the kit."
                )
                return 1
            sent = await bot.send_setup_code(setup_codes.code)
            if sent:
                print("Setup code also sent via the kit's test Telegram bot.")
            else:
                print(
                    "Test Telegram bot started, but the setup code is terminal-only: "
                    f"{telegram_bot.TEST_ALLOWED_USER_IDS_ENV} must name exactly one allowed user id to auto-send it."
                )

        # Ctrl-C (SIGINT) and a plain `kill` (SIGTERM) must both stop the mDNS
        # subprocess and close the listener cleanly -- otherwise avahi-publish-
        # service/dns-sd is orphaned, still advertising a server that is gone.
        # Python does not run a coroutine's `finally` on a bare SIGTERM (there
        # is no default handler), so both signals are wired to the same event.
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):  # platform without signal-handler support
                loop.add_signal_handler(sig, stop_event.set)

        await stop_event.wait()
    finally:
        if bot is not None:
            await bot.stop()
        if rotation_task is not None:
            rotation_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await rotation_task
        await webtransport_listener.stop()
        await server.stream_hub.stop()
        await runner.cleanup()
        advertisement.stop()
    return 0


async def _rotate_webtransport_cert_forever(listener: WebTransportListener) -> None:
    """Rotates the WebTransport cert pair on a fixed interval for as long as
    the kit runs (AD-14). Most kit runs never live long enough to see this
    fire even once, but a long-running instance still gets real rotation
    with overlap rather than a cert pair generated once at boot and never
    touched again."""
    while True:
        await asyncio.sleep(WEBTRANSPORT_CERT_ROTATION_INTERVAL_SECONDS)
        try:
            listener.rotate_certificate()
        except Exception:  # noqa: BLE001 -- one bad rotation must not silently end rotation forever
            logging.getLogger(__name__).exception(
                "bridge_spike.kit: WebTransport certificate rotation failed -- will retry at the next interval"
            )


def cmd_start(args: argparse.Namespace) -> int:
    return asyncio.run(_serve(args.name, args.port, renewing=False))


def cmd_renew(args: argparse.Namespace) -> int:
    return asyncio.run(_serve(args.name, args.port, renewing=True))


def cmd_check(args: argparse.Namespace) -> int:
    from bridge_spike import check as check_module  # deferred: only `check` needs playwright

    result = asyncio.run(check_module.run_check(install_name=args.name))
    print(f"\nCheck steps: {result.steps}")
    print(f"Result written to: {result.result_path}")
    if result.passkey_result_path is not None:
        print(f"Passkey check result written to: {result.passkey_result_path}")
    if result.push_mic_result_path is not None:
        print(f"Push/mic check result written to: {result.push_mic_result_path}")
    if result.stream_result_path is not None:
        print(f"Live stream check result written to: {result.stream_result_path}")
    if result.ok:
        print("PASS — all steps succeeded.")
        return 0
    print("FAIL — see steps above.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--name", default=None, help="install .local host name (default: derived from hostname)"
    )
    common.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTPS port (default: {DEFAULT_PORT})")

    parser = argparse.ArgumentParser(
        prog="kit.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", parents=[common], help="generate CA + cert, advertise, serve")
    start_parser.set_defaults(func=cmd_start)

    renew_parser = subparsers.add_parser("renew", parents=[common], help="issue a new CA + cert, advertise, serve")
    renew_parser.set_defaults(func=cmd_renew)

    check_parser = subparsers.add_parser("check", parents=[common], help="run the automated Chromium done-check")
    check_parser.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.name is None:
        if args.command == "check":
            from bridge_spike import check as check_module  # deferred: only `check` needs playwright

            args.name = check_module.DEFAULT_CHECK_INSTALL_NAME
        else:
            args.name = _default_install_name()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
