#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "cryptography>=42,<49",
#     "aiohttp>=3.11,<4",
#     "playwright>=1.59,<2",
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
import signal
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bridge_spike import ca as ca_module  # noqa: E402
from bridge_spike import mdns  # noqa: E402
from bridge_spike.server import (  # noqa: E402
    RESULTS_DIR,
    BridgeServer,
    ServerConfig,
    primary_local_ipv4_address,
)

# `bridge_spike.check` imports playwright, which `start`/`renew` never need --
# imported lazily inside cmd_check()/main() so `uv run --with cryptography
# --with aiohttp python -m pytest spikes/bridge/tests` (no playwright) can
# still import and exercise this module for its non-check subcommands.

DEFAULT_PORT = 8443

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


async def _serve(install_name: str, port: int, *, renewing: bool) -> None:
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

    config = ServerConfig(install_name=install_name, port=port, ca_artifacts=artifacts)
    server = BridgeServer(config)
    runner = await server.start()
    print(f"\nServing https://{install_name}:{port}/ — Ctrl-C to stop.")

    # Ctrl-C (SIGINT) and a plain `kill` (SIGTERM) must both stop the mDNS
    # subprocess and close the listener cleanly -- otherwise avahi-publish-
    # service/dns-sd is orphaned, still advertising a server that is gone.
    # Python does not run a coroutine's `finally` on a bare SIGTERM (there is
    # no default handler), so both signals are wired to the same stop event.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # platform without signal-handler support
            loop.add_signal_handler(sig, stop_event.set)

    try:
        await stop_event.wait()
    finally:
        await runner.cleanup()
        advertisement.stop()


def cmd_start(args: argparse.Namespace) -> int:
    asyncio.run(_serve(args.name, args.port, renewing=False))
    return 0


def cmd_renew(args: argparse.Namespace) -> int:
    asyncio.run(_serve(args.name, args.port, renewing=True))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from bridge_spike import check as check_module  # deferred: only `check` needs playwright

    result = asyncio.run(check_module.run_check(install_name=args.name))
    print(f"\nCheck steps: {result.steps}")
    print(f"Result written to: {result.result_path}")
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
