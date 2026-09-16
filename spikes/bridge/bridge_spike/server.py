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

import asyncio
import base64
import binascii
import ipaddress
import json
import logging
import re
import ssl
import subprocess
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web
from aiohttp.typedefs import Handler
from cryptography.hazmat.primitives.asymmetric import ec

from . import ca as ca_module
from . import device_requests as device_requests_module
from . import push as push_module
from . import setup_code as setup_code_module
from . import tokens as tokens_module
from . import webauthn_flow as webauthn_flow_module

logger = logging.getLogger("bridge_spike.server")

STATIC_DIR = Path(__file__).parent / "static"
RESULTS_DIR = Path(__file__).parent.parent / "results"
DEVICE_CLASS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEVICE_NAME_RE = re.compile(r"^[\w .,'-]{1,64}$", re.UNICODE)
KIT_VERSION = "0.1.0"

_STATIC_FILES = {
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "application/javascript"),
    "/app.js": ("app.js", "application/javascript"),
    "/offline-summary.html": ("offline-summary.html", "text/html"),
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
    setup_codes: setup_code_module.SetupCodeStore = field(default_factory=setup_code_module.SetupCodeStore)
    # Generated once per kit run (kit.py's _serve()), mirroring setup_codes'
    # own "minted exactly once, at process start" discipline -- every device
    # this run ever serves subscribes against the SAME VAPID key.
    vapid_private_key: ec.EllipticCurvePrivateKey = field(default_factory=push_module.generate_vapid_keypair)


class BridgeServer:
    """The kit's single HTTPS app: subnet gate, redirect-first, static PWA, results endpoint."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        # Discovered once at construction, not per-request: local_ipv4_networks()
        # shells out to `ip`/`ifconfig` (up to a 3s timeout), which would stall
        # the event loop for every concurrent connection if re-run inside the
        # subnet-guard middleware.
        self._local_networks = local_ipv4_networks()
        self.setup_codes = config.setup_codes
        # RP ID = install name, exact expected origin (AD-16): every device
        # this kit ever serves lands on the same https://<install-name>:<port>
        # origin, so a single WebAuthnCeremony instance is correct here.
        self.webauthn = webauthn_flow_module.WebAuthnCeremony(
            config.install_name, origin=f"https://{config.install_name}:{config.port}"
        )
        self.tokens = tokens_module.TokenStore()
        self.nonces = tokens_module.NonceStore()
        self.enrollment_tickets = tokens_module.EnrollmentTicketStore()
        self.device_requests = device_requests_module.DeviceRequestStore()
        self.push_subscriptions = push_module.PushSubscriptionStore()
        # Set by kit.py when a test Telegram bot is configured, so a new
        # device-approval request also gets pushed there -- BridgeServer
        # itself stays ignorant of Telegram entirely.
        self.on_device_request_created: (
            Callable[[device_requests_module.DeviceRequest], Awaitable[None]] | None
        ) = None
        self.app = web.Application(middlewares=[self._subnet_guard, self._redirect_guard])
        self._install_routes()

    def _install_routes(self) -> None:
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_post("/api/results", self._handle_submit_result)
        self.app.router.add_get("/api/auth/nonce", self._handle_nonce)
        self.app.router.add_post("/api/webauthn/register/options", self._handle_webauthn_register_options)
        self.app.router.add_post("/api/webauthn/register/verify", self._handle_webauthn_register_verify)
        self.app.router.add_post("/api/webauthn/authenticate/options", self._handle_webauthn_authenticate_options)
        self.app.router.add_post("/api/webauthn/authenticate/verify", self._handle_webauthn_authenticate_verify)
        self.app.router.add_post("/api/device/register-key", self._handle_device_register_key)
        self.app.router.add_get("/api/whoami", self._handle_whoami)
        self.app.router.add_post("/api/device-requests", self._handle_create_device_request)
        self.app.router.add_get("/api/device-requests/pending", self._handle_get_pending_device_request)
        self.app.router.add_post("/api/device-requests/approve", self._handle_approve_device_request)
        self.app.router.add_get("/api/device-requests/{request_id}", self._handle_get_device_request_status)
        self.app.router.add_get("/api/push/vapid-public-key", self._handle_push_vapid_public_key)
        self.app.router.add_post("/api/push/subscribe", self._handle_push_subscribe)
        self.app.router.add_post("/api/push/unsubscribe", self._handle_push_unsubscribe)
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

    # -- passkey / device-key / device-approval routes --------------------
    #
    # Every route below is already behind `_subnet_guard` (home network only)
    # and `_redirect_guard` (install-name origin only) -- neither guard is
    # re-implemented here. The setup-code routes have no separate network
    # gate of their own beyond that shared middleware: there is simply no
    # route anywhere that MINTS a setup code (see setup_code.py), so "accepted
    # only from the home network" falls out of the same subnet gate every
    # other route already goes through.

    @staticmethod
    async def _json_body(request: web.Request) -> dict:
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="body must be JSON") from None
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="body must be a JSON object")
        return payload

    async def _require_signed_request(self, request: web.Request) -> tokens_module.Device:
        """The signed-request-verifying decorator the AC calls for: every
        authenticated route calls this first and lets `tokens_module`'s
        `Unauthorized` become the one uniform `401` -- see tokens.py for why
        the reason is logged here, not returned."""
        body = await request.read()
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[len("Bearer ") :] if auth_header.startswith("Bearer ") else None
        try:
            return tokens_module.verify_signed_request(
                tokens=self.tokens,
                nonces=self.nonces,
                token=token,
                method=request.method,
                path=request.path,
                timestamp=request.headers.get("X-Bridge-Timestamp"),
                nonce=request.headers.get("X-Bridge-Nonce"),
                signature_b64=request.headers.get("X-Bridge-Signature"),
                body=body,
            )
        except tokens_module.Unauthorized as exc:
            raise tokens_module.unauthorized_response(exc.reason) from exc

    async def _handle_nonce(self, request: web.Request) -> web.Response:
        del request
        return web.json_response({"nonce": self.nonces.issue()})

    async def _handle_webauthn_register_options(self, request: web.Request) -> web.Response:
        payload = await self._json_body(request)
        setup_code = str(payload.get("setup_code") or "")
        if not self.setup_codes.consume(setup_code):
            logger.warning("bridge_spike.server: passkey registration refused — invalid or already-used setup code")
            raise web.HTTPForbidden(text="invalid or already-used setup code")
        options_json = self.webauthn.begin_registration()
        return web.Response(text=options_json, content_type="application/json")

    async def _handle_webauthn_register_verify(self, request: web.Request) -> web.Response:
        payload = await self._json_body(request)
        if "credential" not in payload:
            raise web.HTTPBadRequest(text="body must include 'credential'")
        try:
            self.webauthn.finish_registration(payload["credential"])
        except webauthn_flow_module.WebAuthnError as exc:
            logger.warning("bridge_spike.server: passkey registration verify failed: %s", exc)
            raise web.HTTPBadRequest(text="passkey registration could not be verified") from exc
        ticket = self.enrollment_tickets.mint(self.webauthn.user_name)
        return web.json_response({"ok": True, "enrollment_ticket": ticket})

    async def _handle_webauthn_authenticate_options(self, request: web.Request) -> web.Response:
        del request
        try:
            options_json = self.webauthn.begin_authentication()
        except webauthn_flow_module.WebAuthnError as exc:
            raise web.HTTPConflict(text=str(exc)) from exc
        return web.Response(text=options_json, content_type="application/json")

    async def _handle_webauthn_authenticate_verify(self, request: web.Request) -> web.Response:
        payload = await self._json_body(request)
        if "credential" not in payload:
            raise web.HTTPBadRequest(text="body must include 'credential'")
        try:
            self.webauthn.finish_authentication(payload["credential"])
        except webauthn_flow_module.WebAuthnError as exc:
            logger.warning("bridge_spike.server: passkey authentication verify failed: %s", exc)
            raise web.HTTPBadRequest(text="passkey authentication could not be verified") from exc
        ticket = self.enrollment_tickets.mint(self.webauthn.user_name)
        return web.json_response({"ok": True, "enrollment_ticket": ticket})

    async def _handle_device_register_key(self, request: web.Request) -> web.Response:
        payload = await self._json_body(request)
        # Decode + fully validate the key BEFORE touching the ticket store:
        # a malformed/wrong-curve key must never burn the one-time
        # enrollment ticket, or the whole ceremony has to be redone just to
        # resend a key.
        try:
            public_key_der = base64.b64decode(str(payload.get("device_public_key") or ""), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise web.HTTPBadRequest(text="device_public_key must be base64-encoded DER SPKI") from exc
        try:
            self.tokens.parse_device_public_key(public_key_der)
        except ValueError as exc:
            # Distinct from the base64/DER text above: this key decoded fine
            # but is the wrong curve/type (or, from parse_device_public_key's
            # own message, still not valid DER content).
            raise web.HTTPBadRequest(text=str(exc)) from exc

        ticket = str(payload.get("enrollment_ticket") or "")
        device_name = self.enrollment_tickets.redeem(ticket)
        if device_name is None:
            raise web.HTTPForbidden(text="invalid or expired enrollment ticket")

        device = self.tokens.issue(device_name, public_key_der)
        return web.json_response({"ok": True, "token": device.token, "device_name": device.device_name})

    async def _handle_whoami(self, request: web.Request) -> web.Response:
        device = await self._require_signed_request(request)
        return web.json_response({"device_name": device.device_name})

    async def _handle_create_device_request(self, request: web.Request) -> web.Response:
        payload = await self._json_body(request)
        device_name = str(payload.get("device_name") or "")
        if not DEVICE_NAME_RE.match(device_name):
            raise web.HTTPBadRequest(text="device_name must match ^[\\w .,'-]{1,64}$")
        try:
            device_request = self.device_requests.create(device_name)
        except device_requests_module.DeviceRequestError as exc:
            raise web.HTTPConflict(text=str(exc)) from exc
        if self.on_device_request_created is not None:
            await self.on_device_request_created(device_request)
        return web.json_response(
            {
                "request_id": device_request.request_id,
                "device_name": device_request.device_name,
                "code": device_request.code,
            }
        )

    async def _handle_get_pending_device_request(self, request: web.Request) -> web.Response:
        await self._require_signed_request(request)
        pending = self.device_requests.get_pending()
        if pending is None:
            return web.json_response({"pending": None})
        return web.json_response(
            {"pending": {"request_id": pending.request_id, "device_name": pending.device_name, "code": pending.code}}
        )

    def approve_device_request(self, request_id: str, code: str) -> device_requests_module.DeviceRequest:
        """Approve + mint-and-assign the enrollment ticket, as ONE step --
        the signed-tap route and the Telegram-callback path (kit.py) both
        call this so they can never silently diverge on what "approved"
        means. Raises `device_requests_module.DeviceRequestError` on any
        refusal (unknown/expired request, mismatched code); callers map that
        to whatever response shape fits their own transport."""
        approved = self.device_requests.approve(request_id, code)
        approved.enrollment_ticket = self.enrollment_tickets.mint(approved.device_name)
        return approved

    async def _handle_approve_device_request(self, request: web.Request) -> web.Response:
        await self._require_signed_request(request)
        payload = await self._json_body(request)
        request_id = str(payload.get("request_id") or "")
        code = str(payload.get("code") or "")
        try:
            self.approve_device_request(request_id, code)
        except device_requests_module.DeviceRequestError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        return web.json_response({"ok": True})

    async def _handle_get_device_request_status(self, request: web.Request) -> web.Response:
        request_id = request.match_info["request_id"]
        found = self.device_requests.get(request_id)
        if found is None:
            raise web.HTTPNotFound(text="unknown device request")
        body: dict[str, object] = {"request_id": found.request_id, "approved": found.approved}
        if found.approved:
            body["enrollment_ticket"] = found.enrollment_ticket
        return web.json_response(body)

    # -- push subscribe/unsubscribe routes (Story 1.3, AD-19, FR26, NFR29) --
    #
    # Both mutating routes are behind `_require_signed_request` -- an already
    # signed-in device only, same as every other authenticated route. The
    # public key route is unauthenticated on purpose: a browser needs it
    # BEFORE it can call `PushManager.subscribe`, i.e. before there is any
    # device key/token to sign a request with at all.

    async def _handle_push_vapid_public_key(self, request: web.Request) -> web.Response:
        del request
        return web.json_response({"key": push_module.vapid_public_key_b64url(self.config.vapid_private_key)})

    async def _handle_push_subscribe(self, request: web.Request) -> web.Response:
        device = await self._require_signed_request(request)
        payload = await self._json_body(request)
        endpoint = str(payload.get("endpoint") or "")
        keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
        p256dh = str(keys.get("p256dh") or "")
        auth = str(keys.get("auth") or "")
        if not p256dh or not auth:
            raise web.HTTPBadRequest(text="body must include keys.p256dh and keys.auth")
        # Validate BEFORE storing (the same ordering _handle_device_register_key
        # already established): a refused endpoint must never reach the store,
        # or a malformed/SSRF-aimed subscription would sit there until the
        # next send attempt discovers it.
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, push_module.validate_endpoint, endpoint)
        except push_module.EndpointRefused as exc:
            logger.warning("bridge_spike.server: push subscribe refused — %s", exc)
            raise web.HTTPBadRequest(text=str(exc)) from exc
        self.push_subscriptions.add(device.device_id, endpoint, p256dh, auth)
        return web.json_response({"ok": True})

    async def _handle_push_unsubscribe(self, request: web.Request) -> web.Response:
        device = await self._require_signed_request(request)
        payload = await self._json_body(request)
        endpoint = str(payload.get("endpoint") or "")
        removed = self.push_subscriptions.remove(device.device_id, endpoint)
        return web.json_response({"ok": True, "removed": removed})

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
