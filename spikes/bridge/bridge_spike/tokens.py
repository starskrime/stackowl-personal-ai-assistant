"""Bearer-token issuance and signed-request verification (AD-17, FR67-68).

Every authenticated request must carry a bearer token *plus* a signature,
computed by the browser's non-extractable WebCrypto device key, over
`method\\npath\\ntimestamp\\nnonce\\nbody_hash`. A copied bearer token used
without that private key cannot produce a valid signature, so it is refused
-- and refused with the exact same `401` response as every other failure
here (unknown token, expired nonce, bad signature, stale timestamp). This
module is careful to keep those reasons distinguishable only in its own log
line, never in what a caller receives back: a client that can tell "your
token is wrong" from "your signature is wrong" apart has been handed a free
oracle for forging the other half.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass

from aiohttp import web
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as ec_utils

logger = logging.getLogger("bridge_spike.tokens")

NONCE_TTL_SECONDS = 300.0
ENROLLMENT_TICKET_TTL_SECONDS = 300.0
TIMESTAMP_SKEW_SECONDS = 300
SIGNATURE_LENGTH_P256 = 64  # raw WebCrypto ECDSA signature: r(32) || s(32), IEEE P1363 form
UNAUTHORIZED_BODY = "Unauthorized"


class Unauthorized(Exception):
    """Internal-only: carries the real refusal reason for the log line.
    Every route must convert this to the identical `401` response -- see
    `unauthorized_response()` -- never surfacing `reason` to the caller."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Device:
    device_id: str
    device_name: str
    public_key: ec.EllipticCurvePublicKey
    token: str


class NonceStore:
    """One-time, time-limited server nonces -- the "N" a signature covers,
    so a captured, fully-valid signed request cannot be replayed either."""

    def __init__(self, ttl_seconds: float = NONCE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._issued: dict[str, float] = {}

    def issue(self) -> str:
        nonce = secrets.token_urlsafe(18)
        self._issued[nonce] = time.monotonic() + self._ttl
        return nonce

    def consume(self, nonce: str) -> bool:
        """True at most once per nonce: a second `consume()` of the same
        value -- whether from a genuine retry or a captured replay -- is
        indistinguishable here from an unknown nonce, and refused the same way."""
        expiry = self._issued.pop(nonce, None)
        if expiry is None:
            return False
        return time.monotonic() <= expiry


class EnrollmentTicketStore:
    """A one-time ticket handed from a completed passkey or device-approval
    ceremony to the device-key registration step (`/api/device/register-key`),
    so that route never has to trust a bare, unauthenticated device name."""

    def __init__(self, ttl_seconds: float = ENROLLMENT_TICKET_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._tickets: dict[str, tuple[str, float]] = {}

    def mint(self, device_name: str) -> str:
        ticket = secrets.token_urlsafe(24)
        self._tickets[ticket] = (device_name, time.monotonic() + self._ttl)
        return ticket

    def redeem(self, ticket: str) -> str | None:
        entry = self._tickets.pop(ticket, None)
        if entry is None:
            return None
        device_name, expiry = entry
        if time.monotonic() > expiry:
            return None
        return device_name


class TokenStore:
    """Bearer tokens, each bound to one device's ECDSA P-256 public key."""

    def __init__(self) -> None:
        self._devices: dict[str, Device] = {}  # token -> Device

    @staticmethod
    def parse_device_public_key(public_key_der: bytes) -> ec.EllipticCurvePublicKey:
        """Parses and validates a device's public key on its own (DER SPKI,
        ECDSA P-256) with no `Device`/token side effect, so a caller can
        validate a key BEFORE committing to something a bad key would
        otherwise waste -- e.g. a one-time enrollment ticket. Raises
        `ValueError` with a message that distinguishes "not DER/SPKI at
        all" from "a valid key, but the wrong curve/type"."""
        try:
            public_key = serialization.load_der_public_key(public_key_der)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"device_public_key is not a valid DER-encoded public key: {exc}") from exc
        if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
            public_key.curve, ec.SECP256R1
        ):
            raise ValueError("device key must be a non-extractable WebCrypto ECDSA P-256 public key")
        return public_key

    def issue(self, device_name: str, public_key_der: bytes) -> Device:
        public_key = self.parse_device_public_key(public_key_der)
        token = secrets.token_urlsafe(32)
        device = Device(
            device_id=secrets.token_hex(8), device_name=device_name, public_key=public_key, token=token
        )
        self._devices[token] = device
        return device

    def lookup(self, token: str) -> Device | None:
        return self._devices.get(token)


def _der_signature(raw_signature: bytes) -> bytes:
    """WebCrypto's ECDSA signatures are raw `r || s` (IEEE P1363); `cryptography`'s
    `verify()` wants DER. Converting here keeps that format detail out of the
    signing scheme's own description."""
    if len(raw_signature) != SIGNATURE_LENGTH_P256:
        raise Unauthorized(f"signature must be {SIGNATURE_LENGTH_P256} raw bytes, got {len(raw_signature)}")
    r = int.from_bytes(raw_signature[:32], "big")
    s = int.from_bytes(raw_signature[32:], "big")
    return ec_utils.encode_dss_signature(r, s)


def verify_signed_request(
    *,
    tokens: TokenStore,
    nonces: NonceStore,
    token: str | None,
    method: str,
    path: str,
    timestamp: str | None,
    nonce: str | None,
    signature_b64: str | None,
    body: bytes,
) -> Device:
    """Raise `Unauthorized` for ANY failure. Callers must map every one of
    them to the identical `401` via `unauthorized_response()` -- the reason
    is for the log only."""
    if not token:
        raise Unauthorized("missing bearer token")
    device = tokens.lookup(token)
    if device is None:
        raise Unauthorized("unknown bearer token (never issued, or the process restarted)")
    if not timestamp or not nonce or not signature_b64:
        raise Unauthorized("missing timestamp/nonce/signature header")
    try:
        ts = int(timestamp)
    except ValueError:
        raise Unauthorized("timestamp header is not an integer") from None
    if abs(time.time() - ts) > TIMESTAMP_SKEW_SECONDS:
        raise Unauthorized("timestamp outside the allowed skew window")
    if not nonces.consume(nonce):
        raise Unauthorized("nonce unknown, expired, or already used -- possible replay")
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}".encode()
    try:
        raw_signature = base64.b64decode(signature_b64, validate=True)
        der_signature = _der_signature(raw_signature)
        device.public_key.verify(der_signature, message, ec.ECDSA(hashes.SHA256()))
    except Unauthorized:
        raise
    except (InvalidSignature, ValueError, binascii.Error) as exc:
        # A copied token with no matching private key lands here: it has no
        # way to produce a signature that verifies against this device's
        # public key, so it is refused exactly like every other bad signature.
        raise Unauthorized(f"signature verification failed: {exc}") from exc
    return device


def unauthorized_response(reason: str) -> web.HTTPUnauthorized:
    """The one place every failure in this module becomes an HTTP response --
    always the same status and body, so nothing here ever tells a caller
    *which* check it failed."""
    logger.warning("bridge_spike.tokens: refusing request — %s", reason)
    return web.HTTPUnauthorized(text=UNAUTHORIZED_BODY)
