"""aiohttp HTTPS app for the Bridge TLS spike: IPv4-only, subnet-gated, redirect-first (AD-13, NFR25, FR75).

Design notes on the two "Never" guards this module enforces:

- Subnet refusal happens at the HTTP layer, after the TLS handshake but
  before any route handler runs. A production Bridge would filter at TCP
  accept-time, before TLS even begins; for this throwaway spike, refusing
  with a logged `403` at the first middleware is the simplest thing that is
  still fully testable and still genuinely blocks every response body from
  reaching a disallowed source — it does not relax the check itself.
- The redirect middleware runs second (after the subnet gate), so a
  disallowed source is refused before its Host header is even inspected.
- No plain-HTTP `web.TCPSite` is ever constructed — `BridgeServer.start()`
  is the only place a listener is created, and it always requires an
  `ssl.SSLContext`.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import ssl
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web
from aiohttp.typedefs import Handler

from . import ca as ca_module

logger = logging.getLogger("bridge_spike.server")

STATIC_DIR = Path(__file__).parent / "static"
RESULTS_DIR = Path(__file__).parent.parent / "results"
DEVICE_CLASS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KIT_VERSION = "0.1.0"

_STATIC_FILES = {
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "application/javascript"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}


def _from_ip_command() -> list[ipaddress.IPv4Interface]:
    """Directly-attached IPv4 interfaces, parsed from Linux's `ip -o -4 addr show`."""
    try:
        proc = subprocess.run(
            ["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=3, check=True
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return []
    interfaces: list[ipaddress.IPv4Interface] = []
    for line in proc.stdout.splitlines():
        for token in line.split():
            if "/" not in token or token.count(".") != 3:
                continue
            try:
                iface = ipaddress.ip_interface(token)
            except ValueError:
                continue
            if isinstance(iface, ipaddress.IPv4Interface) and not iface.ip.is_loopback:
                interfaces.append(iface)
    return interfaces


def _parse_netmask(raw: str) -> str | None:
    if raw.startswith("0x"):
        try:
            return str(ipaddress.IPv4Address(int(raw, 16)))
        except ValueError:
            return None
    return raw


def _from_ifconfig_command() -> list[ipaddress.IPv4Interface]:
    """Directly-attached IPv4 interfaces, parsed from `ifconfig` (macOS / BSD / older Linux)."""
    try:
        proc = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=3, check=True)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return []
    interfaces: list[ipaddress.IPv4Interface] = []
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("inet "):
            continue
        tokens = line.split()
        try:
            addr = tokens[1]
            if "/" in addr:
                iface = ipaddress.ip_interface(addr)
            else:
                if "netmask" not in tokens:
                    continue
                mask = _parse_netmask(tokens[tokens.index("netmask") + 1])
                if mask is None:
                    continue
                iface = ipaddress.ip_interface(f"{addr}/{mask}")
        except (ValueError, IndexError):
            continue
        if isinstance(iface, ipaddress.IPv4Interface) and not iface.ip.is_loopback:
            interfaces.append(iface)
    return interfaces


def local_ipv4_interfaces() -> list[ipaddress.IPv4Interface]:
    """Best-effort discovery of this host's own directly-attached IPv4 interfaces.

    Tries Linux's `ip` first, then `ifconfig`. If neither tool is usable,
    logs a warning and returns an empty list — callers then allow ONLY
    loopback, never falling open to "allow everything" just because
    discovery failed.
    """
    interfaces = _from_ip_command() or _from_ifconfig_command()
    if not interfaces:
        logger.warning(
            "Could not discover any local IPv4 interfaces via `ip` or `ifconfig` "
            "-- the subnet allowlist will accept loopback connections only, and "
            "no LAN address is available to advertise over mDNS."
        )
    return interfaces


def local_ipv4_networks() -> list[ipaddress.IPv4Network]:
    """Directly-attached IPv4 subnets, filtered to private ranges only.

    AD-13 calls for "directly attached private subnets" specifically — a
    directly-attached interface that happens to carry a public IPv4 address
    must not be treated as part of the home-network allowlist.
    """
    return [iface.network for iface in local_ipv4_interfaces() if iface.network.is_private]


def primary_local_ipv4_address() -> str | None:
    """This host's first directly-attached, private IPv4 address, or None if
    none could be discovered (e.g. `ip`/`ifconfig` both unavailable, or every
    directly-attached interface carries a public address)."""
    for iface in local_ipv4_interfaces():
        if iface.network.is_private:
            return str(iface.ip)
    return None


def is_source_allowed(peer_ip: str | None, networks: list[ipaddress.IPv4Network] | None = None) -> bool:
    """True only for loopback or an address inside a subnet directly attached to this host."""
    if peer_ip is None:
        return False
    try:
        addr = ipaddress.ip_address(peer_ip)
    except ValueError:
        return False
    if not isinstance(addr, ipaddress.IPv4Address):
        return False
    if addr.is_loopback:
        return True
    if networks is None:
        networks = local_ipv4_networks()
    return any(addr in net for net in networks)


@dataclass
class ServerConfig:
    install_name: str
    port: int
    ca_artifacts: ca_module.CAArtifacts
    host: str = "0.0.0.0"  # nosec: IPv4-only bind, all local IPv4 interfaces.


class BridgeServer:
    """The kit's single HTTPS app: subnet gate, redirect-first, static PWA, results endpoint."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        # Discovered once at construction, not per-request: local_ipv4_networks()
        # shells out to `ip`/`ifconfig` (up to a 3s timeout), which would stall
        # the event loop for every concurrent connection if re-run inside the
        # subnet-guard middleware.
        self._local_networks = local_ipv4_networks()
        self.app = web.Application(middlewares=[self._subnet_guard, self._redirect_guard])
        self._install_routes()

    def _install_routes(self) -> None:
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_post("/api/results", self._handle_submit_result)
        for path, (filename, content_type) in _STATIC_FILES.items():
            self.app.router.add_get(path, self._make_static_handler(filename, content_type))

    # -- middlewares ---------------------------------------------------

    @staticmethod
    def _peer_ip(request: web.Request) -> str | None:
        if request.transport is None:
            return None
        peername = request.transport.get_extra_info("peername")
        if not peername:
            return None
        return str(peername[0])

    @web.middleware
    async def _subnet_guard(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        peer_ip = self._peer_ip(request)
        if not is_source_allowed(peer_ip, networks=self._local_networks):
            remedy = (
                f"Refused connection from {peer_ip!r}: source is not loopback or a subnet "
                "directly attached to this host. Remedy: connect from a device on the same "
                "local network as the Bridge spike host, or use 127.0.0.1 for the automated check."
            )
            logger.warning(remedy)
            raise web.HTTPForbidden(text="Connection refused: source subnet not allowed.")
        return await handler(request)

    @web.middleware
    async def _redirect_guard(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        hostname = (request.host or "").split(":")[0]
        if hostname != self.config.install_name:
            target = f"https://{self.config.install_name}:{self.config.port}{request.path_qs}"
            raise web.HTTPTemporaryRedirect(target)
        return await handler(request)

    # -- routes ----------------------------------------------------------

    async def _handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=(STATIC_DIR / "index.html").read_text(), content_type="text/html")

    def _make_static_handler(self, filename: str, content_type: str) -> Handler:
        path = STATIC_DIR / filename
        is_binary = content_type.startswith("image/")

        async def handler(request: web.Request) -> web.Response:
            if is_binary:
                return web.Response(body=path.read_bytes(), content_type=content_type)
            return web.Response(text=path.read_text(), content_type=content_type)

        return handler

    async def _handle_submit_result(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="body must be JSON") from None
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="body must be a JSON object")

        device_class = str(payload.get("device_class") or "")
        note = str(payload.get("note") or "").strip()
        if not DEVICE_CLASS_RE.match(device_class):
            raise web.HTTPBadRequest(text="device_class must match ^[A-Za-z0-9_-]{1,64}$")
        if not note:
            raise web.HTTPBadRequest(text="note is required")

        result = {
            "kit_version": KIT_VERSION,
            "install_name": self.config.install_name,
            "device_class": device_class,
            "passed": payload.get("passed") is True,
            "note": note,
            "browser": payload.get("browser"),
            "os": payload.get("os"),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = RESULTS_DIR / f"B1-{device_class}.json"
        out_path.write_text(json.dumps(result, indent=2))
        return web.json_response({"ok": True, "path": str(out_path)})

    # -- lifecycle ---------------------------------------------------------

    def build_ssl_context(self) -> ssl.SSLContext:
        """Load the (already-signed) leaf cert+key into a server SSL context.

        The leaf key is written to a temp file only for the instant
        `load_cert_chain` needs a path — the directory is removed immediately
        after loading. This is the leaf key, never the CA key (which was
        already dropped by `ca.setup()` before this ever runs).

        The chain served is leaf + CA cert, not the leaf alone: a device that
        already trusts the CA doesn't need the CA cert repeated here, but
        `bridge_spike.check` validates trust by pinning only the CA's SPKI
        (never the leaf's -- pinning the leaf too would let an unrelated,
        unsigned cert sharing that key pass), which requires the CA cert to
        actually be present in the served chain for path-building to reach it.
        """
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        with tempfile.TemporaryDirectory(prefix="bridge-spike-leaf-") as tmp_dir:
            cert_path = Path(tmp_dir) / "leaf-chain.pem"
            key_path = Path(tmp_dir) / "leaf-key.pem"
            cert_path.write_bytes(self.config.ca_artifacts.leaf_cert_pem + self.config.ca_artifacts.ca_cert_pem)
            key_path.write_bytes(self.config.ca_artifacts.leaf_key_pem)
            context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        return context

    async def start(self) -> web.AppRunner:
        """Bind and start the one HTTPS listener. IPv4-only; never plain HTTP."""
        ssl_context = self.build_ssl_context()
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, host=self.config.host, port=self.config.port, ssl_context=ssl_context)
        await site.start()
        return runner
