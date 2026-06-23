"""Stream bridge — carry the per-turn ResponseChunk stream across the socket.

The in-process design (``pipeline/streaming.py``) gives every turn its own
``StreamWriter``/``StreamReader`` pair over a private ``asyncio.Queue``; the
producer (``backend.run``) writes, the consumer (``adapter.send``) drains until
the ``is_final`` sentinel. The split keeps that contract byte-identical on both
ends and only swaps the middle:

* **Core side** — ``SocketStreamWriter`` exposes the same ``write``/``close`` as
  ``StreamWriter`` but serialises each chunk to a ``ChunkFrame`` on the shared
  connection. ``backend.run`` is unchanged.
* **Gateway side** — ``StreamDemux`` receives ChunkFrames (all turns multiplexed
  on one connection), routes them by ``trace_id`` into a private queue, and hands
  back a real ``StreamReader`` so ``adapter.send`` is unchanged.

One wrinkle vs the in-process sentinel: ``StreamWriter.close`` writes a sentinel
with ``trace_id=""`` (fine when the reader shares the queue). Over the socket the
demux must ROUTE the close, so ``SocketStreamWriter.close`` stamps the real
request_id. The reader still breaks on ``is_final`` and never yields it, so the
adapter is none the wiser.
"""

from __future__ import annotations

import asyncio

from stackowl.infra.observability import log
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import ChunkFrame
from stackowl.pipeline.streaming import (
    ResponseChunk,
    StreamReader,
    StreamRegistry,
    StreamWriter,
)


def chunk_to_frame(chunk: ResponseChunk) -> ChunkFrame:
    """Serialise a ResponseChunk to its wire frame (field-for-field)."""
    return ChunkFrame(
        content=chunk.content,
        is_final=chunk.is_final,
        chunk_index=chunk.chunk_index,
        trace_id=chunk.trace_id,
        owl_name=chunk.owl_name,
        duration_ms=chunk.duration_ms,
        kind=chunk.kind,
        target=chunk.target,
        is_floor=chunk.is_floor,
    )


def frame_to_chunk(frame: ChunkFrame) -> ResponseChunk:
    """Reconstruct a ResponseChunk from a wire frame (field-for-field)."""
    return ResponseChunk(
        content=frame.content,
        is_final=frame.is_final,
        chunk_index=frame.chunk_index,
        trace_id=frame.trace_id,
        owl_name=frame.owl_name,
        duration_ms=frame.duration_ms,
        kind=frame.kind,
        target=frame.target,
        is_floor=frame.is_floor,
    )


class SocketStreamWriter(StreamWriter):
    """Core-side stream writer: same ``write``/``close`` as ``StreamWriter``.

    Emits each chunk as a ``ChunkFrame`` on the shared core->gateway connection.
    Drop-in for ``StreamWriter`` (subclasses it for type-compatibility in the
    registry) so ``backend.run`` / the deliver path is unchanged. The inherited
    queue is never used — ``write``/``close`` go to the socket instead.
    """

    def __init__(self, conn: FrameConnection, request_id: str) -> None:
        super().__init__(asyncio.Queue())  # unused; satisfies the base contract
        self._conn = conn
        self._request_id = request_id

    async def write(self, chunk: ResponseChunk) -> None:
        await self._conn.send(chunk_to_frame(chunk))

    async def close(self) -> None:
        # Mirror StreamWriter.close's sentinel, but stamp the real request_id so
        # the gateway StreamDemux can ROUTE the close to this turn's reader (the
        # reader breaks on is_final and never yields it).
        await self._conn.send(
            ChunkFrame(
                content="",
                is_final=True,
                chunk_index=-1,
                trace_id=self._request_id,
                owl_name="",
            )
        )


class _DeadReader(StreamReader):
    """A StreamReader that yields nothing — the core has no local consumer.

    In the split core, the per-turn output is consumed on the GATEWAY side, so
    the reader returned alongside the socket writer must never be drained for
    output. ``spawn_send`` still iterates it (and harmlessly gets nothing).
    """

    def __init__(self) -> None:
        queue: asyncio.Queue[ResponseChunk] = asyncio.Queue()
        # Pre-load the is_final sentinel so the inherited _iter breaks at once
        # and yields nothing — no override needed.
        queue.put_nowait(
            ResponseChunk(content="", is_final=True, chunk_index=-1,
                          trace_id="", owl_name="")
        )
        super().__init__(queue)


class SocketStreamRegistry(StreamRegistry):
    """Core-side StreamRegistry whose writers stream over the socket.

    ``create`` registers a :class:`SocketStreamWriter` (so the deliver step and
    progress emitter, which look the writer up by ``get_writer(trace_id)``, write
    ChunkFrames to the gateway) and returns a dead reader (no local consumer).
    ``get_writer``/``remove`` are inherited and operate on the shared dict.
    """

    def __init__(self, conn: FrameConnection) -> None:
        super().__init__()
        self._conn = conn

    def create(self, request_id: str) -> tuple[StreamWriter, StreamReader]:
        writer: StreamWriter = SocketStreamWriter(self._conn, request_id)
        self._writers[request_id] = writer
        log.gateway.debug(
            "[ipc] socket stream registry: registered request",
            extra={"_fields": {"request_id": request_id}},
        )
        return writer, _DeadReader()


class StreamDemux:
    """Gateway-side fan-out: route multiplexed ChunkFrames into per-turn readers.

    Reuses ``StreamRegistry`` so each registered request gets a real queue-backed
    ``StreamReader`` (drains until ``is_final``) — exactly what ``adapter.send``
    already consumes.
    """

    def __init__(self) -> None:
        self._registry = StreamRegistry()

    def register(self, request_id: str) -> StreamReader:
        """Open a reader for a turn before its first chunk arrives."""
        _writer, reader = self._registry.create(request_id)
        return reader

    async def feed(self, frame: ChunkFrame) -> None:
        """Route one inbound ChunkFrame to its turn's reader; clean up on close."""
        writer = self._registry.get_writer(frame.trace_id)
        if writer is None:
            # A chunk for a turn we never registered (or already closed). Never
            # silent — a routing miss must be visible.
            log.gateway.warning(
                "[ipc] stream demux: chunk for unknown request — dropping",
                extra={"_fields": {"request_id": frame.trace_id, "is_final": frame.is_final}},
            )
            return
        await writer.write(frame_to_chunk(frame))
        if frame.is_final:
            self._registry.remove(frame.trace_id)
