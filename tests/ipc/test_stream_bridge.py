"""Stream bridge — per-turn ResponseChunk stream across the socket.

Covers field-for-field converter round-trip, demux routing by trace_id (incl.
two interleaved turns on one connection), the close sentinel stamped with the
real request_id, and an unknown-request drop.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.commands.response import Action
from stackowl.ipc.client import IpcClient
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import ChunkFrame
from stackowl.ipc.server import IpcServer
from stackowl.ipc.stream_bridge import (
    SocketStreamWriter,
    StreamDemux,
    chunk_to_frame,
    frame_to_chunk,
)
from stackowl.pipeline.streaming import ResponseChunk


def _chunk(**kw) -> ResponseChunk:
    base = dict(content="hi", is_final=False, chunk_index=0, trace_id="t1", owl_name="owl")
    base.update(kw)
    return ResponseChunk(**base)


def test_converter_round_trip_preserves_all_fields() -> None:
    chunk = _chunk(
        content="x\ny", chunk_index=3, duration_ms=12.5, kind="progress",
        target="chan:thread", is_floor=True,
    )
    assert frame_to_chunk(chunk_to_frame(chunk)) == chunk


def test_converter_round_trip_preserves_actions() -> None:
    # Regression: a command reply's tappable buttons (ResponseChunk.actions,
    # added for the button-layer feature) must survive the core->gateway wire
    # hop unchanged — a bare ChunkFrame(**fields) copy that forgets this field
    # silently strips every button from a reply delivered over split IPC.
    chunk = _chunk(
        actions=(Action(label="Remove x", command="/provider remove x", destructive=True),),
    )
    assert frame_to_chunk(chunk_to_frame(chunk)) == chunk


@pytest.fixture
def socket_path(tmp_path):
    return tmp_path / "core.sock"


async def test_turn_stream_flows_core_to_gateway(socket_path) -> None:
    """Core writes chunks via SocketStreamWriter; gateway demux yields them, stops on close."""
    demux = StreamDemux()
    reader = demux.register("t1")

    async def core_handler(conn: FrameConnection) -> None:
        writer = SocketStreamWriter(conn, "t1")
        await writer.write(_chunk(content="Hello ", chunk_index=0))
        await writer.write(_chunk(content="world", chunk_index=1))
        await writer.close()

    server = IpcServer(socket_path)
    # Gateway side: accept the core connection and pump its frames into the demux.
    async def gateway_accept(conn: FrameConnection) -> None:
        async for frame in conn:
            if isinstance(frame, ChunkFrame):
                await demux.feed(frame)

    await server.start(gateway_accept)
    try:
        core_conn = await IpcClient(socket_path).connect(timeout_s=5)
        # Drive the "core" in a task; drain the reader on the "gateway".
        producer = asyncio.create_task(core_handler(core_conn))
        got = [c.content async for c in reader]
        await producer
        await core_conn.aclose()
    finally:
        await server.stop()

    assert got == ["Hello ", "world"]  # sentinel consumed, not yielded


async def test_two_turns_interleaved_on_one_connection(socket_path) -> None:
    """Frames for two trace_ids multiplexed on one socket route to separate readers."""
    demux = StreamDemux()
    r1 = demux.register("t1")
    r2 = demux.register("t2")

    async def core_handler(conn: FrameConnection) -> None:
        w1 = SocketStreamWriter(conn, "t1")
        w2 = SocketStreamWriter(conn, "t2")
        await w1.write(_chunk(content="a1", trace_id="t1"))
        await w2.write(_chunk(content="b1", trace_id="t2"))
        await w1.write(_chunk(content="a2", trace_id="t1"))
        await w1.close()
        await w2.write(_chunk(content="b2", trace_id="t2"))
        await w2.close()

    server = IpcServer(socket_path)

    async def gateway_accept(conn: FrameConnection) -> None:
        async for frame in conn:
            if isinstance(frame, ChunkFrame):
                await demux.feed(frame)

    await server.start(gateway_accept)
    try:
        core_conn = await IpcClient(socket_path).connect(timeout_s=5)
        producer = asyncio.create_task(core_handler(core_conn))
        got1, got2 = await asyncio.gather(
            _collect(r1), _collect(r2)
        )
        await producer
        await core_conn.aclose()
    finally:
        await server.stop()

    assert got1 == ["a1", "a2"]
    assert got2 == ["b1", "b2"]


async def _collect(reader) -> list[str]:
    return [c.content async for c in reader]


async def test_feed_drops_unknown_request(socket_path) -> None:
    demux = StreamDemux()
    # No register() — feeding an unknown trace_id must not raise.
    await demux.feed(ChunkFrame(content="orphan", is_final=False, chunk_index=0,
                                trace_id="ghost", owl_name="owl"))


def test_close_sentinel_carries_request_id() -> None:
    # Unit check of the sentinel-routing fix: close must stamp request_id (not "").
    sent: list[ChunkFrame] = []

    class _FakeConn:
        async def send(self, frame):
            sent.append(frame)

    writer = SocketStreamWriter(_FakeConn(), "real-req")  # type: ignore[arg-type]
    asyncio.run(writer.close())
    assert sent[0].is_final is True
    assert sent[0].chunk_index == -1
    assert sent[0].trace_id == "real-req"


def test_socket_registry_reader_blocks_until_close() -> None:
    """Regression: the per-turn reader must NOT complete until the writer closes.

    The old `_DeadReader` completed instantly, so `spawn_send`'s cleanup removed
    the registry slot BEFORE `deliver`'s `get_writer` ran — and the answer was
    dropped (`stream-miss`). The coupled reader drains only when `close()` drops
    the sentinel, so the slot stays alive across deliver. `write` streams over the
    socket but does NOT end the local reader (the gateway is the renderer).
    """
    from stackowl.ipc.stream_bridge import SocketStreamRegistry

    sent: list[ChunkFrame] = []

    class _FakeConn:
        async def send(self, frame):
            sent.append(frame)

    async def _run() -> None:
        reg = SocketStreamRegistry(_FakeConn())  # type: ignore[arg-type]
        writer, reader = reg.create("req-1")
        assert reg.get_writer("req-1") is writer

        collected: list[ResponseChunk] = []

        async def _drain() -> None:
            async for chunk in reader:
                collected.append(chunk)

        task = asyncio.create_task(_drain())
        await asyncio.sleep(0.02)
        assert not task.done()  # writer open -> reader draining -> slot survives

        await writer.write(_chunk(content="x", trace_id="req-1"))
        await asyncio.sleep(0.02)
        assert not task.done()  # write does NOT end the local reader

        await writer.close()
        await asyncio.wait_for(task, timeout=1)
        assert task.done()
        assert collected == []  # output went over the socket, not the local reader
        assert any(getattr(f, "content", None) == "x" for f in sent)
        assert sent[-1].is_final and sent[-1].trace_id == "req-1"

    asyncio.run(_run())
