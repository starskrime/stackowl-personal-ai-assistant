"""GatewayLink + CoreLink end-to-end over a real socket (fake dispatch/adapter).

Proves the submit->dispatch->stream->deliver contract, proactive send_text, and
the stream-finalize-on-cut guard when a dispatch crashes — without the real
engine. The orchestrator fork swaps the fake dispatch for the live intake engine.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from stackowl.gateway.scanner import IngressMessage
from stackowl.ipc.client import IpcClient
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.server import IpcServer
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.runtime.core_link import CoreLink, CoreSink
from stackowl.runtime.gateway_link import GatewayLink


class FakeAdapter:
    channel_name = "cli"

    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.texts: list[str] = []
        self.done = asyncio.Event()

    async def send(self, reader) -> None:
        async for chunk in reader:
            self.chunks.append(chunk.content)
        self.done.set()

    async def send_text(self, text: str) -> None:
        self.texts.append(text)


def _msg(text: str = "hello", trace_id: str = "t1") -> IngressMessage:
    return IngressMessage(text=text, session_id="s1", channel="cli", trace_id=trace_id)


async def _run_split(socket_path, dispatch, adapter):
    """Wire a gateway server + core client; return (gateway_link, stop()) once linked."""
    holder: dict = {}
    link_ready = asyncio.Event()

    async def gateway_accept(conn: FrameConnection) -> None:
        link = GatewayLink(conn, adapters={adapter.channel_name: adapter})
        holder["link"] = link
        link_ready.set()
        await link.run()

    server = IpcServer(socket_path)
    await server.start(gateway_accept)
    core_conn = await IpcClient(socket_path).connect(timeout_s=5)
    core_link = CoreLink(core_conn, dispatch)
    core_task = asyncio.create_task(core_link.run())
    await asyncio.wait_for(link_ready.wait(), timeout=5)

    async def stop() -> None:
        await core_conn.aclose()
        # core_link.run() exits cleanly on EOF; cancel only if still pending.
        if not core_task.done():
            core_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await core_task
        await server.stop()

    return holder["link"], stop


@pytest.fixture
def socket_path(tmp_path):
    return tmp_path / "core.sock"


async def test_submit_streams_dispatch_output_to_adapter(socket_path) -> None:
    async def echo(msg: IngressMessage, sink: CoreSink) -> None:
        w = sink.stream_writer()
        await w.write(ResponseChunk(content=f"echo:{msg.text}", is_final=False,
                                    chunk_index=0, trace_id=msg.trace_id, owl_name="owl"))
        await w.write(ResponseChunk(content="!", is_final=False, chunk_index=1,
                                    trace_id=msg.trace_id, owl_name="owl"))
        await sink.close_stream()

    adapter = FakeAdapter()
    link, stop = await _run_split(socket_path, echo, adapter)
    try:
        await link.submit(_msg("hello"))
        await asyncio.wait_for(adapter.done.wait(), timeout=5)
    finally:
        await stop()

    assert adapter.chunks == ["echo:hello", "!"]


async def test_proactive_send_text_routes_to_adapter(socket_path) -> None:
    async def announce(msg: IngressMessage, sink: CoreSink) -> None:
        await sink.send_text("cli", "out-of-band ping")
        await sink.stream_writer().write(ResponseChunk(
            content="done", is_final=False, chunk_index=0,
            trace_id=msg.trace_id, owl_name="owl"))
        await sink.close_stream()

    adapter = FakeAdapter()
    link, stop = await _run_split(socket_path, announce, adapter)
    try:
        await link.submit(_msg())
        await asyncio.wait_for(adapter.done.wait(), timeout=5)
    finally:
        await stop()

    assert adapter.texts == ["out-of-band ping"]
    assert adapter.chunks == ["done"]


async def test_dispatch_crash_still_closes_stream(socket_path) -> None:
    """A dispatch that raises mid-turn must not hang the gateway reader."""
    async def crasher(msg: IngressMessage, sink: CoreSink) -> None:
        await sink.stream_writer().write(ResponseChunk(
            content="partial", is_final=False, chunk_index=0,
            trace_id=msg.trace_id, owl_name="owl"))
        raise RuntimeError("boom")

    adapter = FakeAdapter()
    link, stop = await _run_split(socket_path, crasher, adapter)
    try:
        await link.submit(_msg())
        # The guard's close_stream must terminate the reader despite the crash.
        await asyncio.wait_for(adapter.done.wait(), timeout=5)
    finally:
        await stop()

    assert adapter.chunks == ["partial"]


async def test_submit_unregistered_channel_is_noop(socket_path) -> None:
    async def never(msg, sink):  # pragma: no cover
        raise AssertionError

    adapter = FakeAdapter()
    link, stop = await _run_split(socket_path, never, adapter)
    try:
        # channel "telegram" not registered → submit logs + drops, no crash.
        await link.submit(IngressMessage(text="x", session_id="s", channel="telegram", trace_id="z"))
    finally:
        await stop()
    assert adapter.chunks == []
