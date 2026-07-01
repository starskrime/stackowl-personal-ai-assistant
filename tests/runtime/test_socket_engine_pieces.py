"""SocketStreamRegistry + SocketChannelAdapter — the core-side engine bridges.

These let the split core reuse the whole pipeline: deliver/progress write via
``get_writer`` (-> SocketStreamWriter -> ChunkFrames), and the channel I/O rides
the socket. Verified against a fake connection that records frames.
"""

from __future__ import annotations

import asyncio

from stackowl.channels.socket_adapter import SocketChannelAdapter
from stackowl.ipc.frames import ChunkFrame, SendTextFrame
from stackowl.ipc.stream_bridge import SocketStreamRegistry
from stackowl.pipeline.streaming import ResponseChunk


class FakeConn:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, frame) -> None:
        self.sent.append(frame)


def _chunk(content: str, trace_id: str = "t1") -> ResponseChunk:
    return ResponseChunk(content=content, is_final=False, chunk_index=0,
                         trace_id=trace_id, owl_name="owl")


# --- SocketStreamRegistry ---------------------------------------------------


async def test_registry_get_writer_finds_socket_writer() -> None:
    conn = FakeConn()
    reg = SocketStreamRegistry(conn)  # type: ignore[arg-type]
    writer, reader = reg.create("t1")
    # deliver looks the writer up by trace_id and writes; that must hit the socket.
    assert reg.get_writer("t1") is writer
    await reg.get_writer("t1").write(_chunk("hello"))
    await reg.get_writer("t1").close()
    assert [type(f).__name__ for f in conn.sent] == ["ChunkFrame", "ChunkFrame"]
    assert conn.sent[0].content == "hello"
    # close sentinel carries the real request_id so the gateway can route it.
    assert conn.sent[1].is_final and conn.sent[1].trace_id == "t1"


async def test_registry_reader_yields_nothing_and_completes_on_close() -> None:
    # The core's local reader carries NO output (the gateway renders over the
    # socket) but it must stay alive until the writer closes — that back-pressure
    # is what stops spawn_send's cleanup from removing the slot before deliver.
    conn = FakeConn()
    reg = SocketStreamRegistry(conn)  # type: ignore[arg-type]
    writer, reader = reg.create("t1")
    # Drain in the background; it must NOT complete until close().
    collected: list = []

    async def _drain() -> None:
        async for chunk in reader:
            collected.append(chunk)

    task = asyncio.create_task(_drain())
    await asyncio.sleep(0.02)
    assert not task.done()  # back-pressure: open writer keeps the reader alive
    await writer.close()
    await asyncio.wait_for(task, timeout=1)
    assert collected == []  # output went over the socket, not the local reader


def test_registry_remove_clears_writer() -> None:
    conn = FakeConn()
    reg = SocketStreamRegistry(conn)  # type: ignore[arg-type]
    reg.create("t1")
    reg.remove("t1")
    assert reg.get_writer("t1") is None


# --- SocketChannelAdapter ---------------------------------------------------


async def test_adapter_feed_then_receive() -> None:
    from stackowl.gateway.scanner import IngressMessage

    adapter = SocketChannelAdapter(FakeConn(), channel_name="cli")  # type: ignore[arg-type]
    msg = IngressMessage(text="hi", session_id="s1", channel="cli", trace_id="t1")
    adapter.feed(msg)
    assert await adapter.receive() is msg
    assert adapter.channel_name == "cli"


async def test_adapter_send_text_stamps_channel() -> None:
    conn = FakeConn()
    adapter = SocketChannelAdapter(conn, channel_name="telegram")  # type: ignore[arg-type]
    await adapter.send_text("Busy — I'll start that soon.")
    assert isinstance(conn.sent[0], SendTextFrame)
    assert conn.sent[0].channel == "telegram"
    assert conn.sent[0].text == "Busy — I'll start that soon."


async def test_adapter_send_text_with_chat_id_stamps_target() -> None:
    conn = FakeConn()
    adapter = SocketChannelAdapter(conn, channel_name="telegram")  # type: ignore[arg-type]
    await adapter.send_text("targeted", chat_id=123)
    assert isinstance(conn.sent[0], SendTextFrame)
    assert conn.sent[0].target == 123


async def test_adapter_send_text_without_chat_id_leaves_target_none() -> None:
    conn = FakeConn()
    adapter = SocketChannelAdapter(conn, channel_name="telegram")  # type: ignore[arg-type]
    await adapter.send_text("untargeted")
    assert isinstance(conn.sent[0], SendTextFrame)
    assert conn.sent[0].target is None


async def test_adapter_send_forwards_chunks() -> None:
    conn = FakeConn()
    adapter = SocketChannelAdapter(conn, channel_name="cli")  # type: ignore[arg-type]

    async def gen():
        yield _chunk("a")
        yield _chunk("b")

    await adapter.send(gen())
    assert [f.content for f in conn.sent] == ["a", "b"]
    assert all(isinstance(f, ChunkFrame) for f in conn.sent)
