"""Unit tests for bridge_spike.webtransport_server: 0-RTT disabled,
Origin-check-before-auth ordering, and late/oversized signed-auth-message
closure (AD-13, AD-14, AD-38, NFR25).

These exercise `BridgeWebTransportProtocol`'s own decision logic directly,
with the H3/QUIC connection replaced by lightweight fakes -- a real aioquic
QUIC handshake needs a live UDP socket and a real peer, which is instead
what `bridge_spike/check.py`'s browser-driven WebTransport connect/fallback
steps prove end to end. `test_kit_cli_output.py`-style unit coverage here
is for the refusal DECISIONS: who gets a 403, who gets closed, and in what
order -- not the wire protocol itself.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import time
from unittest.mock import MagicMock

from aioquic.quic.events import ConnectionTerminated, HandshakeCompleted
from bridge_spike import stream as stream_module
from bridge_spike import tokens as tokens_module
from bridge_spike import webtransport_cert as webtransport_cert_module
from bridge_spike import webtransport_server as wt_module
from bridge_spike.webtransport_server import BridgeWebTransportProtocol, WebTransportListener
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as ec_utils

INSTALL_NAME = "test-webtransport-server.local"
PORT = 8443
EXPECTED_ORIGIN = f"https://{INSTALL_NAME}:{PORT}"


def _make_listener() -> WebTransportListener:
    return WebTransportListener(
        install_name=INSTALL_NAME,
        port=PORT,
        host="127.0.0.1",
        cert_store=MagicMock(),
        tokens=tokens_module.TokenStore(),
        nonces=tokens_module.NonceStore(),
        hub=stream_module.StreamHub(),
    )


def _make_protocol(listener: WebTransportListener) -> BridgeWebTransportProtocol:
    """Builds a protocol instance WITHOUT running aioquic's real __init__
    (which needs a live event loop's transport/QuicConnection) -- only the
    attributes this module's own methods touch are set, mirroring how
    `test_ca.py` inspects behavior directly rather than standing up a full
    network stack for a unit test."""
    protocol = object.__new__(BridgeWebTransportProtocol)
    protocol.listener = listener
    protocol._http = MagicMock()
    protocol._pending_auth = {}
    protocol._session_tasks = {}
    protocol._loop = MagicMock()
    protocol._quic = MagicMock()
    protocol.close = MagicMock()  # type: ignore[method-assign]
    protocol.transmit = MagicMock()  # type: ignore[method-assign]
    listener._connections.add(protocol)
    return protocol


def _headers_received(stream_id: int, headers: dict[bytes, bytes]) -> MagicMock:
    event = MagicMock()
    event.stream_id = stream_id
    event.headers = list(headers.items())
    return event


def _device_and_signed_fields(tokens: tokens_module.TokenStore, nonces: tokens_module.NonceStore, cursor: int):
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    device = tokens.issue("wt-test-device", public_der)
    nonce = nonces.issue()
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(str(cursor).encode()).hexdigest()
    message = f"WEBTRANSPORT\n{wt_module.WEBTRANSPORT_PATH}\n{timestamp}\n{nonce}\n{body_hash}".encode()
    der_signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = ec_utils.decode_dss_signature(der_signature)
    raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    signature_b64 = base64.b64encode(raw_signature).decode()
    return device, {"token": device.token, "timestamp": timestamp, "nonce": nonce, "signature": signature_b64}


# -- 0-RTT is refused structurally: never wired at all ----------------------


async def test_start_never_passes_session_ticket_fetcher_or_handler() -> None:
    """0-RTT/session resumption cannot happen without aioquic being given a
    session_ticket_fetcher/session_ticket_handler -- start() must never pass
    either, on every call, not just by omission today."""
    listener = _make_listener()
    captured: dict = {}

    async def fake_serve(host, port, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    original_serve = wt_module.serve
    original_load = WebTransportListener._load_certificate
    wt_module.serve = fake_serve  # type: ignore[assignment]
    WebTransportListener._load_certificate = lambda self, configuration: None  # type: ignore[method-assign]
    try:
        await listener.start()
    finally:
        wt_module.serve = original_serve  # type: ignore[assignment]
        WebTransportListener._load_certificate = original_load  # type: ignore[method-assign]

    assert "session_ticket_fetcher" not in captured
    assert "session_ticket_handler" not in captured
    assert captured.get("retry") is True  # QUIC address validation (AD-38)


# -- certificate rotation reloads the LIVE configuration (AD-14) ------------


async def test_rotate_certificate_reloads_the_live_configuration_with_the_new_current() -> None:
    """`WebTransportCertStore.rotate()`'s own in-memory state is covered by
    test_webtransport_cert.py in isolation -- this proves the other half of
    AD-14's rotation: `WebTransportListener.rotate_certificate()` actually
    reloads the LIVE `QuicConfiguration`'s certificate/key from the store's
    new `current`, not just the store itself (a broken reload path would
    otherwise ship undetected)."""
    cert_store = webtransport_cert_module.WebTransportCertStore(INSTALL_NAME)
    listener = WebTransportListener(
        install_name=INSTALL_NAME,
        port=PORT,
        host="127.0.0.1",
        cert_store=cert_store,
        tokens=tokens_module.TokenStore(),
        nonces=tokens_module.NonceStore(),
        hub=stream_module.StreamHub(),
    )

    original_serve = wt_module.serve

    async def fake_serve(host, port, **kwargs):
        return MagicMock()

    wt_module.serve = fake_serve  # type: ignore[assignment]
    try:
        await listener.start()
    finally:
        wt_module.serve = original_serve  # type: ignore[assignment]

    configuration = listener._configuration  # noqa: SLF001 -- the exact object under test
    assert configuration.certificate.public_bytes(serialization.Encoding.DER) == cert_store.current.cert_der

    listener.rotate_certificate()

    assert configuration.certificate.public_bytes(serialization.Encoding.DER) == cert_store.current.cert_der
    assert configuration.private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ) == cert_store.current.key_pem


# -- connection cap refusal (AD-38) ------------------------------------------


def test_connection_cap_reached_closes_the_new_connection() -> None:
    listener = _make_listener()
    listener.max_connections = 1
    _make_protocol(listener)  # first connection -- fills the cap
    over_cap_protocol = _make_protocol(listener)  # second -- now over the cap

    over_cap_protocol.quic_event_received(
        HandshakeCompleted(alpn_protocol="h3", early_data_accepted=False, session_resumed=False)
    )

    over_cap_protocol.close.assert_called_once()
    over_cap_protocol._http.handle_event.assert_not_called()


# -- Origin checked before auth ----------------------------------------------


def test_wrong_origin_is_refused_with_403_and_never_reaches_pending_auth() -> None:
    listener = _make_listener()
    protocol = _make_protocol(listener)
    event = _headers_received(
        4,
        {
            b":method": b"CONNECT",
            b":protocol": b"webtransport",
            b":path": wt_module.WEBTRANSPORT_PATH.encode(),
            b"origin": b"https://evil.example.com",
        },
    )
    protocol._handle_headers(event)

    protocol._http.send_headers.assert_called_once()
    call_args = protocol._http.send_headers.call_args
    assert call_args.args[0] == 4
    assert (b":status", b"403") in call_args.args[1]
    assert 4 not in protocol._pending_auth


def test_matching_origin_is_admitted_to_pending_auth_with_200() -> None:
    listener = _make_listener()
    protocol = _make_protocol(listener)
    event = _headers_received(
        4,
        {
            b":method": b"CONNECT",
            b":protocol": b"webtransport",
            b":path": wt_module.WEBTRANSPORT_PATH.encode(),
            b"origin": EXPECTED_ORIGIN.encode(),
        },
    )
    protocol._handle_headers(event)

    call_args = protocol._http.send_headers.call_args
    assert (b":status", b"200") in call_args.args[1]
    assert 4 in protocol._pending_auth


def test_non_webtransport_connect_is_refused_before_any_origin_check() -> None:
    listener = _make_listener()
    protocol = _make_protocol(listener)
    event = _headers_received(4, {b":method": b"GET"})
    protocol._handle_headers(event)

    call_args = protocol._http.send_headers.call_args
    assert (b":status", b"400") in call_args.args[1]
    assert 4 not in protocol._pending_auth


# -- late / oversized signed auth message ------------------------------------


def _data_received(session_id: int, data: bytes, *, stream_ended: bool) -> MagicMock:
    event = MagicMock()
    event.session_id = session_id
    event.data = data
    event.stream_ended = stream_ended
    return event


def test_oversized_auth_message_closes_the_session() -> None:
    listener = _make_listener()
    protocol = _make_protocol(listener)
    protocol._pending_auth[4] = wt_module._PendingAuth(deadline=time.monotonic() + wt_module.AUTH_WINDOW_SECONDS)

    oversized = b"x" * (wt_module.AUTH_MAX_BYTES + 1)
    protocol._handle_webtransport_data(_data_received(4, oversized, stream_ended=False))

    protocol.close.assert_called_once()
    assert 4 not in protocol._pending_auth


def test_late_auth_message_closes_the_session_and_never_authenticates() -> None:
    listener = _make_listener()
    protocol = _make_protocol(listener)
    # Deadline already in the past -- as if AUTH_WINDOW_SECONDS had elapsed.
    protocol._pending_auth[4] = wt_module._PendingAuth(deadline=time.monotonic() - 1)

    device, fields = _device_and_signed_fields(listener.tokens, listener.nonces, cursor=0)
    payload = json.dumps({**fields, "cursor": 0}).encode()
    protocol._handle_webtransport_data(_data_received(4, payload, stream_ended=True))

    protocol.close.assert_called_once()
    assert 4 not in protocol._session_tasks


async def test_well_formed_on_time_auth_message_starts_a_session() -> None:
    listener = _make_listener()
    protocol = _make_protocol(listener)
    protocol._pending_auth[4] = wt_module._PendingAuth(deadline=time.monotonic() + wt_module.AUTH_WINDOW_SECONDS)
    protocol._http.create_webtransport_stream = MagicMock(return_value=99)

    device, fields = _device_and_signed_fields(listener.tokens, listener.nonces, cursor=7)
    payload = json.dumps({**fields, "cursor": 7}).encode()
    protocol._handle_webtransport_data(_data_received(4, payload, stream_ended=True))

    protocol.close.assert_not_called()
    assert 4 in protocol._session_tasks
    task = protocol._session_tasks[4]
    task.cancel()
    with contextlib.suppress(BaseException):
        await task


# -- ConnectionTerminated cleanup (cap slot + hub client registry) ----------


async def test_connection_terminated_frees_cap_slot_and_unregisters_hub_client() -> None:
    """`ConnectionTerminated` is what frees a `MAX_CONNECTIONS` cap slot
    (`listener._connections.discard(self)`) and is the only thing that
    cancels an in-flight `_run_session` task -- which is in turn the only
    path that lets `stream_for_client`'s own `finally:
    hub.unregister_client(...)` run for a WebTransport client. Neither half
    of that cleanup had a test: a regression to either the discard or the
    cancel loop would leave a permanently consumed cap slot and a dead
    entry in `StreamHub._clients` with nothing here to catch it."""
    hub = stream_module.StreamHub()
    listener = WebTransportListener(
        install_name=INSTALL_NAME,
        port=PORT,
        host="127.0.0.1",
        cert_store=MagicMock(),
        tokens=tokens_module.TokenStore(),
        nonces=tokens_module.NonceStore(),
        hub=hub,
    )
    protocol = _make_protocol(listener)
    protocol._pending_auth[4] = wt_module._PendingAuth(deadline=time.monotonic() + wt_module.AUTH_WINDOW_SECONDS)
    protocol._http.create_webtransport_stream = MagicMock(return_value=99)

    device, fields = _device_and_signed_fields(listener.tokens, listener.nonces, cursor=0)
    payload = json.dumps({**fields, "cursor": 0}).encode()
    protocol._handle_webtransport_data(_data_received(4, payload, stream_ended=True))
    assert 4 in protocol._session_tasks
    task = protocol._session_tasks[4]
    # Let the freshly-scheduled session task actually register with the hub
    # (`stream_for_client`'s first line) before terminating the connection.
    await asyncio.sleep(0)
    assert len(hub._clients) == 1  # noqa: SLF001 -- the exact registry under test

    assert protocol in listener._connections
    protocol.quic_event_received(
        ConnectionTerminated(error_code=0, frame_type=None, reason_phrase="peer closed")
    )
    assert protocol not in listener._connections  # cap slot freed
    assert protocol._session_tasks == {}

    with contextlib.suppress(BaseException):
        await task  # let the cancelled task -- and the generator's own `finally` -- run to completion

    assert len(hub._clients) == 0  # noqa: SLF001 -- unregister_client() ran


def test_bad_signature_closes_the_session_without_starting_one() -> None:
    listener = _make_listener()
    protocol = _make_protocol(listener)
    protocol._pending_auth[4] = wt_module._PendingAuth(deadline=time.monotonic() + wt_module.AUTH_WINDOW_SECONDS)

    device, fields = _device_and_signed_fields(listener.tokens, listener.nonces, cursor=0)
    fields["signature"] = base64.b64encode(b"\x00" * 64).decode()  # well-formed but wrong
    payload = json.dumps({**fields, "cursor": 0}).encode()
    protocol._handle_webtransport_data(_data_received(4, payload, stream_ended=True))

    protocol.close.assert_called_once()
    assert 4 not in protocol._session_tasks
