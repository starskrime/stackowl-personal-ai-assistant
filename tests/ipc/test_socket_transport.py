"""Integration: IpcServer + IpcClient over a real unix-domain socket.

Covers the duplex round-trip, EOF semantics, the connect-retry race (client
before server), and the core-restart pattern (gateway listener stays up while a
fresh client re-attaches).
"""

from __future__ import annotations

import asyncio
import os

import pytest

from stackowl.ipc.client import IpcClient
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import AckFrame, ChunkFrame, HelloFrame, IngressFrame
from stackowl.ipc.server import IpcServer
from stackowl.runtime import link_auth


@pytest.fixture
def socket_path(tmp_path):
    return tmp_path / "core.sock"


async def test_duplex_round_trip(socket_path) -> None:
    """Client sends an ingress; server echoes a chunk back; both arrive intact."""
    received_on_server: list = []

    async def handler(conn: FrameConnection) -> None:
        frame = await conn.recv()
        received_on_server.append(frame)
        await conn.send(
            ChunkFrame(content="pong", is_final=False, chunk_index=0,
                       trace_id="t1", owl_name="owl")
        )
        await conn.send(
            ChunkFrame(content="", is_final=True, chunk_index=-1,
                       trace_id="t1", owl_name="")
        )

    server = IpcServer(socket_path)
    await server.start(handler)
    try:
        conn = await IpcClient(socket_path).connect(timeout_s=5)
        await conn.send(IngressFrame(text="ping", session_key="s1", channel="cli", trace_id="t1"))
        chunks = []
        async for frame in conn:
            chunks.append(frame)
        await conn.aclose()
    finally:
        await server.stop()

    assert isinstance(received_on_server[0], IngressFrame)
    assert received_on_server[0].text == "ping"
    # The transport is semantics-free: it yields BOTH chunks (incl. the is_final
    # sentinel) and stops on EOF. Interpreting is_final is the stream demux's job.
    assert [c.content for c in chunks] == ["pong", ""]
    assert chunks[-1].is_final is True


async def test_recv_returns_none_on_peer_close(socket_path) -> None:
    async def handler(conn: FrameConnection) -> None:
        await conn.send(HelloFrame(sender_pid=1, highest_migration=1, registry_digest="x"))
        # then hang up

    server = IpcServer(socket_path)
    await server.start(handler)
    try:
        conn = await IpcClient(socket_path).connect(timeout_s=5)
        first = await conn.recv()
        second = await conn.recv()
    finally:
        await server.stop()

    assert isinstance(first, HelloFrame)
    assert second is None  # clean EOF
    assert conn.closed is True


async def test_connect_retries_until_server_is_up(socket_path) -> None:
    """Client started before the server still connects once the server binds."""
    server = IpcServer(socket_path)

    async def late_start() -> None:
        await asyncio.sleep(0.3)
        await server.start(lambda conn: asyncio.sleep(0.05))

    starter = asyncio.create_task(late_start())
    try:
        conn = await IpcClient(socket_path).connect(timeout_s=5, retry_interval_s=0.05)
        assert conn.closed is False
        await conn.aclose()
    finally:
        await starter
        await server.stop()


async def test_connect_times_out_when_no_server(socket_path) -> None:
    with pytest.raises(TimeoutError):
        await IpcClient(socket_path).connect(timeout_s=0.3, retry_interval_s=0.05)


async def test_core_restart_reattaches_to_durable_listener(socket_path) -> None:
    """The gateway listener survives; a second (fresh) client connects after the first."""
    hellos: list[int] = []

    async def handler(conn: FrameConnection) -> None:
        frame = await conn.recv()
        if isinstance(frame, HelloFrame):
            hellos.append(frame.sender_pid)
        await conn.send(AckFrame(ref="hello", status="ok"))

    server = IpcServer(socket_path)
    await server.start(handler)
    try:
        # First "core"
        c1 = await IpcClient(socket_path).connect(timeout_s=5)
        await c1.send(HelloFrame(sender_pid=111, highest_migration=1, registry_digest="x"))
        assert isinstance(await c1.recv(), AckFrame)
        await c1.aclose()

        # Second "core" (post-restart) attaches to the SAME listener
        c2 = await IpcClient(socket_path).connect(timeout_s=5)
        await c2.send(HelloFrame(sender_pid=222, highest_migration=1, registry_digest="x"))
        assert isinstance(await c2.recv(), AckFrame)
        await c2.aclose()
    finally:
        await server.stop()

    assert hellos == [111, 222]


async def test_raw_socket_over_a_real_asyncio_transport_yields_the_real_peer_pid(
    socket_path,
) -> None:
    """Spec 2.4 — ``FrameConnection.raw_socket`` is the ACTUAL code path
    ``_accept_core`` uses for the peer-PID check. Unlike ``test_link_auth.py``
    (a raw ``socket.socketpair()``, which bypasses asyncio's transport layer
    entirely), this drives it over a REAL ``asyncio.start_unix_server`` /
    ``open_unix_connection`` pair — the same transport ``IpcServer``/
    ``IpcClient`` build in production.

    MEASURED here (not assumed): over a real asyncio transport,
    ``get_extra_info("socket")`` returns an ``asyncio.trsock.TransportSocket``
    proxy, NOT a literal ``socket.socket`` — this is exactly why
    ``FrameConnection.raw_socket``/``link_auth.peer_pid`` are typed against
    the structural ``PeerCredSocket`` protocol rather than
    ``isinstance(x, socket.socket)``; a nominal check silently discarded this
    real object and returned ``None`` on every real connection until this
    test caught it.
    """
    observed: dict[str, object] = {}

    async def handler(conn: FrameConnection) -> None:
        observed["raw_socket"] = conn.raw_socket
        observed["peer_pid"] = link_auth.peer_pid(conn.raw_socket)
        await conn.recv()  # wait for the client to hang up

    server = IpcServer(socket_path)
    await server.start(handler)
    try:
        client_conn = await IpcClient(socket_path).connect(timeout_s=5)
        client_peer_pid = link_auth.peer_pid(client_conn.raw_socket)
        await client_conn.aclose()
        await asyncio.sleep(0.05)  # let the server-side handler observe the accept
    finally:
        await server.stop()

    # A real, getsockopt-capable object — never None, never a bare fallback.
    raw = observed.get("raw_socket")
    assert raw is not None
    assert hasattr(raw, "getsockopt")
    assert observed["peer_pid"] == os.getpid()
    assert client_peer_pid == os.getpid()
