"""aioquic WebTransport listener for the Bridge carrier spike (AD-13,
AD-38, NFR25): binds the SAME port number as the HTTPS TCP site
(`server.py`), over UDP only -- never a second port.

Session admission, in order:

1. ALPN negotiates `h3`; an `H3Connection` is created with WebTransport
   enabled (H3_DATAGRAM negotiated) for this QUIC connection only.
2. A CONNECT request with `:protocol: webtransport` on the expected path is
   the only thing accepted; anything else gets a plain HTTP/3 status and
   the stream ends there.
3. `Origin` is checked against this kit's own origin BEFORE any auth is
   even looked at (NFR25) -- a mismatch is refused with `403` and the
   session is never admitted to the pending-auth state at all.
4. The CONNECT is accepted (`:status 200`), which opens the WebTransport
   session, but no event stream is started yet: the session sits in
   "pending auth" until the client's first WebTransport stream delivers a
   signed auth message (the SAME method/path/timestamp/nonce/signature
   scheme `tokens.verify_signed_request` already checks over HTTPS, with
   `body` bound to the resume cursor so it cannot be replayed against a
   different one). A message that arrives later than `AUTH_WINDOW_SECONDS`
   or exceeds `AUTH_MAX_BYTES` closes the whole QUIC connection with no
   stream data ever sent (AD-13, AD-14, NFR25).
5. Only once verified does the session get its own server-initiated
   unidirectional WebTransport stream, fed by `stream.stream_for_client` --
   the exact same generator the SSE fallback in `server.py` drives.

0-RTT is refused structurally, not by a runtime flag: `serve()` below is
never given a `session_ticket_fetcher`/`session_ticket_handler`, so the TLS
layer never issues or accepts a resumption ticket for this listener, and
early data cannot happen without one (aioquic only offers/accepts 0-RTT
through that exact mechanism). QUIC address validation is the `retry=True`
Retry-packet mechanism `serve()` already provides. Connection and
request-size caps are enforced explicitly below, since aioquic itself
imposes neither.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from dataclasses import dataclass, field

from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.asyncio.server import QuicServer
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import H3Event, HeadersReceived, WebTransportStreamDataReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import ConnectionTerminated, HandshakeCompleted, ProtocolNegotiated, QuicEvent
from cryptography.hazmat.primitives import serialization
from cryptography import x509

from . import tokens as tokens_module
from .stream import StreamHub, stream_for_client
from .webtransport_cert import WebTransportCertStore

logger = logging.getLogger("bridge_spike.webtransport_server")

H3_ALPN = ["h3"]
WEBTRANSPORT_PATH = "/bridge-stream"
AUTH_WINDOW_SECONDS = 5.0
AUTH_MAX_BYTES = 4096
MAX_CONNECTIONS = 64  # AD-38 connection cap
# H3's per-frame datagram capacity -- comfortably above AUTH_MAX_BYTES so a
# well-formed auth message is never itself rejected at the QUIC layer.
MAX_DATAGRAM_FRAME_SIZE = 65536


@dataclass
class _PendingAuth:
    deadline: float
    buffer: bytearray = field(default_factory=bytearray)


class BridgeWebTransportProtocol(QuicConnectionProtocol):
    """One aioquic QUIC connection. `listener` is bound via
    `functools.partial` in `WebTransportListener.start()`, mirroring
    aioquic's own `create_protocol` factory-argument convention (aioquic
    calls `create_protocol(connection, stream_handler=...)` positionally,
    so `listener` must arrive pre-bound rather than as a plain constructor
    argument)."""

    def __init__(self, *args, listener: "WebTransportListener", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.listener = listener
        self._http: H3Connection | None = None
        self._pending_auth: dict[int, _PendingAuth] = {}
        self._session_tasks: dict[int, asyncio.Task] = {}
        listener._connections.add(self)

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, HandshakeCompleted):
            if len(self.listener._connections) > self.listener.max_connections:
                logger.warning(
                    "bridge_spike.webtransport_server: refusing connection -- cap of %d reached",
                    self.listener.max_connections,
                )
                self.close(reason_phrase="connection cap reached")
                return
        elif isinstance(event, ConnectionTerminated):
            self.listener._connections.discard(self)
            for task in self._session_tasks.values():
                task.cancel()
            self._session_tasks.clear()
            return  # connection is gone -- nothing left to hand to H3
        elif isinstance(event, ProtocolNegotiated):
            if event.alpn_protocol in H3_ALPN:
                self._http = H3Connection(self._quic, enable_webtransport=True)

        if self._http is not None:
            for h3_event in self._http.handle_event(event):
                self._h3_event_received(h3_event)

    def _h3_event_received(self, event: H3Event) -> None:
        if isinstance(event, HeadersReceived):
            self._handle_headers(event)
        elif isinstance(event, WebTransportStreamDataReceived):
            self._handle_webtransport_data(event)

    def _handle_headers(self, event: HeadersReceived) -> None:
        assert self._http is not None
        headers = dict(event.headers)
        if headers.get(b":method") != b"CONNECT" or headers.get(b":protocol") != b"webtransport":
            self._http.send_headers(event.stream_id, [(b":status", b"400")], end_stream=True)
            self.transmit()
            return
        if headers.get(b":path") != WEBTRANSPORT_PATH.encode():
            self._http.send_headers(event.stream_id, [(b":status", b"404")], end_stream=True)
            self.transmit()
            return

        # Origin checked BEFORE auth (NFR25) -- a mismatch never even
        # reaches the pending-auth state below.
        origin = headers.get(b"origin", b"").decode(errors="replace")
        if origin != self.listener.expected_origin:
            logger.warning(
                "bridge_spike.webtransport_server: refusing CONNECT -- Origin %r != expected %r",
                origin,
                self.listener.expected_origin,
            )
            self._http.send_headers(event.stream_id, [(b":status", b"403")], end_stream=True)
            self.transmit()
            return

        session_id = event.stream_id
        self._pending_auth[session_id] = _PendingAuth(deadline=time.monotonic() + AUTH_WINDOW_SECONDS)
        # The draft02 header is harmless to send and keeps compatibility
        # with older WebTransport-over-HTTP/3 client implementations that
        # still look for it; current Chrome (RFC 9220) ignores it.
        self._http.send_headers(
            event.stream_id, [(b":status", b"200"), (b"sec-webtransport-http3-draft02", b"1")]
        )
        self.transmit()
        self._loop.call_later(AUTH_WINDOW_SECONDS, self._expire_pending_auth, session_id)

    def _expire_pending_auth(self, session_id: int) -> None:
        if session_id in self._pending_auth:
            logger.warning(
                "bridge_spike.webtransport_server: closing session %s -- signed auth message did not "
                "arrive within %.0fs",
                session_id,
                AUTH_WINDOW_SECONDS,
            )
            self._pending_auth.pop(session_id, None)
            self.close(reason_phrase="auth window expired")

    def _handle_webtransport_data(self, event: WebTransportStreamDataReceived) -> None:
        session_id = event.session_id
        pending = self._pending_auth.get(session_id)
        if pending is None:
            return  # already authenticated (or session unknown/closed): ignore stray data
        pending.buffer.extend(event.data)
        if len(pending.buffer) > AUTH_MAX_BYTES:
            logger.warning(
                "bridge_spike.webtransport_server: closing session %s -- signed auth message exceeded %d bytes",
                session_id,
                AUTH_MAX_BYTES,
            )
            self._pending_auth.pop(session_id, None)
            self.close(reason_phrase="auth message too large")
            return
        if not event.stream_ended:
            return
        self._pending_auth.pop(session_id, None)
        if time.monotonic() > pending.deadline:
            logger.warning(
                "bridge_spike.webtransport_server: closing session %s -- signed auth message arrived "
                "after the %.0fs window",
                session_id,
                AUTH_WINDOW_SECONDS,
            )
            self.close(reason_phrase="auth message too late")
            return
        self._authenticate_and_start(session_id, bytes(pending.buffer))

    def _authenticate_and_start(self, session_id: int, raw: bytes) -> None:
        try:
            message = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning(
                "bridge_spike.webtransport_server: closing session %s -- auth message was not valid JSON",
                session_id,
            )
            self.close(reason_phrase="malformed auth message")
            return
        if not isinstance(message, dict):
            logger.warning(
                "bridge_spike.webtransport_server: closing session %s -- auth message was not a JSON object",
                session_id,
            )
            self.close(reason_phrase="malformed auth message")
            return
        try:
            cursor = int(message.get("cursor") or 0)
        except (TypeError, ValueError):
            self.close(reason_phrase="malformed auth message")
            return
        try:
            tokens_module.verify_signed_request(
                tokens=self.listener.tokens,
                nonces=self.listener.nonces,
                token=message.get("token"),
                method="WEBTRANSPORT",
                path=WEBTRANSPORT_PATH,
                timestamp=message.get("timestamp"),
                nonce=message.get("nonce"),
                signature_b64=message.get("signature"),
                body=str(cursor).encode(),
            )
        except tokens_module.Unauthorized as exc:
            logger.warning("bridge_spike.webtransport_server: closing session %s -- %s", session_id, exc.reason)
            self.close(reason_phrase="unauthorized")
            return
        self._session_tasks[session_id] = asyncio.ensure_future(self._run_session(session_id, cursor))

    async def _run_session(self, session_id: int, cursor: int) -> None:
        assert self._http is not None
        out_stream_id = self._http.create_webtransport_stream(session_id, is_unidirectional=True)
        self.transmit()
        try:
            async for item in stream_for_client(self.listener.hub, cursor):
                line = (json.dumps(item) + "\n").encode()
                self._quic.send_stream_data(out_stream_id, line, end_stream=False)
                self.transmit()
        finally:
            self._session_tasks.pop(session_id, None)


class WebTransportListener:
    """Owns the aioquic UDP listener plus the state every connection needs:
    the expected origin, the shared token/nonce stores (the SAME ones
    `server.py`'s HTTPS routes use -- a device that signed in over HTTPS is
    the same device that can open a stream), and the shared `StreamHub`."""

    def __init__(
        self,
        *,
        install_name: str,
        port: int,
        host: str,
        cert_store: WebTransportCertStore,
        tokens: tokens_module.TokenStore,
        nonces: tokens_module.NonceStore,
        hub: StreamHub,
        max_connections: int = MAX_CONNECTIONS,
    ) -> None:
        self.install_name = install_name
        self.port = port
        self.host = host
        self.expected_origin = f"https://{install_name}:{port}"
        self.cert_store = cert_store
        self.tokens = tokens
        self.nonces = nonces
        self.hub = hub
        self.max_connections = max_connections
        self._connections: set[BridgeWebTransportProtocol] = set()
        self._configuration: QuicConfiguration | None = None
        self._quic_server: QuicServer | None = None

    def _load_certificate(self, configuration: QuicConfiguration) -> None:
        cert = self.cert_store.current
        configuration.certificate = x509.load_pem_x509_certificate(cert.cert_pem)
        configuration.private_key = serialization.load_pem_private_key(cert.key_pem, password=None)

    def rotate_certificate(self) -> None:
        """Promotes the store's `next` cert to `current` and reloads it
        into the LIVE `QuicConfiguration` -- brand-new connections pick it
        up immediately; connections already handshaked keep whichever cert
        they negotiated with (AD-14's "rotated with overlap")."""
        self.cert_store.rotate()
        if self._configuration is not None:
            self._load_certificate(self._configuration)

    async def start(self) -> None:
        configuration = QuicConfiguration(
            alpn_protocols=H3_ALPN,
            is_client=False,
            max_datagram_frame_size=MAX_DATAGRAM_FRAME_SIZE,
        )
        self._load_certificate(configuration)
        self._configuration = configuration
        create_protocol = functools.partial(BridgeWebTransportProtocol, listener=self)
        # AD-13: identical port NUMBER as the HTTPS TCP site, over UDP --
        # `serve()` binds its own UDP socket, entirely independent of
        # aiohttp's TCP bind on the same number. `retry=True` is the QUIC
        # address-validation Retry mechanism (AD-38, NFR25). NEVER pass
        # session_ticket_fetcher/session_ticket_handler here -- see this
        # module's docstring for why that is how 0-RTT stays refused.
        self._quic_server = await serve(
            self.host,
            self.port,
            configuration=configuration,
            create_protocol=create_protocol,
            retry=True,
        )

    async def stop(self) -> None:
        if self._quic_server is not None:
            self._quic_server.close()
            self._quic_server = None


__all__ = [
    "AUTH_MAX_BYTES",
    "AUTH_WINDOW_SECONDS",
    "MAX_CONNECTIONS",
    "WEBTRANSPORT_PATH",
    "BridgeWebTransportProtocol",
    "WebTransportListener",
]
