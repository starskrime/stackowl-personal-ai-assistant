"""VAPID push-subscription store, an SSRF-guarding endpoint validator
(NFR29), and a `pywebpush`-based sender proving Web Push delivery (AD-19,
FR26) -- never against a real relay, see `push_stub.py` for the local
stand-in this module's sender is proven against.

The payload this module ever sends is metadata-only *by construction*:
`build_metadata()` has no parameter for arbitrary content, so a caller
cannot leak more than item id, kind, intensity and the narrator's public
rendering through `send_push()` -- there is simply nowhere to put it.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02 as _Vapid
from pywebpush import WebPushException, webpush

logger = logging.getLogger("bridge_spike.push")

# The VAPID "sub" claim is required to be a mailto: or https: contact per
# RFC 8292 -- this kit has no real owner email to put here, so a clearly
# fake, kit-scoped address is used (never resolved, never sent anywhere).
VAPID_SUB_CLAIM = "mailto:owner@bridge-spike.local"

Resolver = Callable[[str], list[str]]


class EndpointRefused(Exception):
    """Raised by `validate_endpoint` for any NFR29 refusal reason."""


def generate_vapid_keypair() -> ec.EllipticCurvePrivateKey:
    """A fresh ECDSA P-256 keypair, generated once per kit run and kept only
    in memory -- mirroring `ca.py`'s "never written to disk" ephemeral-key
    discipline, though (unlike the CA key) this one IS kept for the process
    lifetime: it has to sign every push sent during this run, not just once."""
    return ec.generate_private_key(ec.SECP256R1())


def vapid_public_key_b64url(private_key: ec.EllipticCurvePrivateKey) -> str:
    """The uncompressed EC point, base64url with no padding -- exactly the
    shape the browser's `PushManager.subscribe({applicationServerKey})`
    expects, and what `GET /api/push/vapid-public-key` hands back."""
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _default_resolve(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise EndpointRefused(f"push endpoint host {hostname!r} could not be resolved: {exc}") from None
    # getaddrinfo can return an IPv6 scoped address like "fe80::1%eth0" --
    # the zone suffix isn't part of the address itself for is_link_local
    # purposes and ipaddress.ip_address() rejects it outright.
    return [info[4][0].split("%", 1)[0] for info in infos]


def validate_endpoint(endpoint: str, *, resolve: Resolver = _default_resolve) -> None:
    """Raises `EndpointRefused` for anything NFR29 requires refusing: a
    non-`https` scheme, or a host that resolves -- on ANY of its addresses,
    not just the first, since multi-A DNS rotation could otherwise hide a
    private address behind a public one -- to loopback, private, or
    link-local. An IP-literal host is checked directly with no DNS round
    trip; this is the SSRF guard AD-19 calls for ("Prevents: ... SSRF
    through a push endpoint")."""
    parsed = urlparse(endpoint)
    if parsed.scheme != "https":
        logger.warning("bridge_spike.push: refusing endpoint — scheme is %r, not https", parsed.scheme)
        raise EndpointRefused(f"push endpoint must be https, got {parsed.scheme!r}")
    if not parsed.hostname:
        logger.warning("bridge_spike.push: refusing endpoint — no host in %r", endpoint)
        raise EndpointRefused("push endpoint has no host")
    try:
        candidates = [ipaddress.ip_address(parsed.hostname)]
    except ValueError:
        candidates = [ipaddress.ip_address(addr) for addr in resolve(parsed.hostname)]
    if not candidates:
        logger.warning("bridge_spike.push: refusing endpoint — %r resolved to no addresses at all", endpoint)
        raise EndpointRefused(f"push endpoint host {parsed.hostname!r} resolved to no addresses")
    for addr in candidates:
        if addr.is_loopback or addr.is_private or addr.is_link_local:
            logger.warning(
                "bridge_spike.push: refusing endpoint — %r resolves to disallowed address %s", endpoint, addr
            )
            raise EndpointRefused(f"push endpoint resolves to a disallowed address: {addr}")


@dataclass
class PushSubscription:
    device_id: str
    endpoint: str
    p256dh: str
    auth: str

    def subscription_info(self) -> dict:
        """The exact shape `pywebpush.webpush()` wants, and the same shape a
        real `PushSubscription.toJSON()` produces in the browser."""
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


class PushSubscriptionStore:
    """Subscriptions bound to the device session that created them (AD-19:
    "a subscription row belongs to its device session and is deleted on
    revocation"). In-memory only, like every other store in this kit --
    subscriptions do not survive a kit restart."""

    def __init__(self) -> None:
        self._subscriptions: dict[tuple[str, str], PushSubscription] = {}

    def add(self, device_id: str, endpoint: str, p256dh: str, auth: str) -> PushSubscription:
        subscription = PushSubscription(device_id=device_id, endpoint=endpoint, p256dh=p256dh, auth=auth)
        self._subscriptions[(device_id, endpoint)] = subscription
        return subscription

    def remove(self, device_id: str, endpoint: str) -> bool:
        return self._subscriptions.pop((device_id, endpoint), None) is not None

    def for_device(self, device_id: str) -> list[PushSubscription]:
        return [sub for (did, _endpoint), sub in self._subscriptions.items() if did == device_id]

    def all(self) -> list[PushSubscription]:
        return list(self._subscriptions.values())


def build_metadata(*, item_id: str, kind: str, intensity: object, rendering: str) -> dict:
    """The ONLY payload shape this module ever sends. There is no parameter
    for arbitrary content, so a caller structurally cannot make `send_push`
    carry more than this (FR26: "never full content")."""
    return {"id": item_id, "kind": kind, "intensity": intensity, "rendering": rendering}


def send_push(
    vapid_private_key: ec.EllipticCurvePrivateKey,
    subscription: PushSubscription,
    metadata: dict,
    *,
    vapid_claims: dict | None = None,
    requests_session: object = None,
    ttl: int = 60,
) -> None:
    """Encrypts `metadata` (RFC 8291 `aes128gcm`) and signs a VAPID JWT
    (RFC 8292) via `pywebpush`, then POSTs to the subscription's own
    endpoint. `pywebpush` performs both real cryptographic steps regardless
    of what answers on the other end -- this is what lets this function
    prove "delivery uses standard VAPID with an encrypted payload" against
    the kit's own local stand-in (`push_stub.py`) without ever reaching a
    real relay.

    BLOCKING: `pywebpush.webpush()` uses the synchronous `requests` library
    internally. Every caller from async code (the server route, the
    automated check) MUST run this via `asyncio.to_thread`/an executor --
    never awaited directly, and never called from the event loop thread.
    Raises `pywebpush.WebPushException` on any non-2xx response or transport
    failure; callers decide how to log/report that (this module deliberately
    does not swallow it).
    """
    claims = dict(vapid_claims or {"sub": VAPID_SUB_CLAIM})
    webpush(
        subscription_info=subscription.subscription_info(),
        data=json.dumps(metadata),
        # A Vapid02 (RFC 8292) instance built directly from the already-
        # in-memory key, not a re-serialized PEM string -- pywebpush accepts
        # this (isinstance(vapid_private_key, Vapid01)) and it sidesteps
        # py_vapid's from_string()/from_der() parsing entirely.
        vapid_private_key=_Vapid(private_key=vapid_private_key),
        vapid_claims=claims,
        ttl=ttl,
        requests_session=requests_session,
    )


__all__ = [
    "EndpointRefused",
    "PushSubscription",
    "PushSubscriptionStore",
    "WebPushException",
    "build_metadata",
    "generate_vapid_keypair",
    "send_push",
    "validate_endpoint",
    "vapid_public_key_b64url",
]
