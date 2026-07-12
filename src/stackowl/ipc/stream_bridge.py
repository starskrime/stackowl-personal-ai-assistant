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
import contextlib

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
        actions=chunk.actions,
        raw_keyboard=chunk.raw_keyboard,
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
        actions=frame.actions,
        raw_keyboard=frame.raw_keyboard,
    )


class SocketStreamWriter(StreamWriter):
    """Core-side stream writer: same ``write``/``close`` as ``StreamWriter``.

    Emits each chunk as a ``ChunkFrame`` on the shared core->gateway connection.
    Drop-in for ``StreamWriter`` (subclasses it for type-compatibility in the
    registry) so ``backend.run`` / the deliver path is unchanged.

    The inherited queue is a LOCAL back-pressure channel (NOT a second output
    path): ``write`` goes only to the socket (the gateway renders), but ``close``
    ALSO drops the ``is_final`` sentinel on the local queue. The per-turn reader
    handed to ``spawn_send`` drains that queue, so ``adapter.send`` blocks until
    the writer is closed — exactly like mono. That ordering is load-bearing:
    ``spawn_send``'s cleanup removes the registry slot when ``adapter.send``
    completes, and it MUST complete after ``deliver`` has looked the writer up,
    not before (the old dead-on-arrival reader removed the slot too early, so
    ``deliver``'s ``get_writer`` missed and the answer was dropped).
    """

    def __init__(
        self,
        conn: FrameConnection,
        request_id: str,
        local_queue: asyncio.Queue[ResponseChunk] | None = None,
    ) -> None:
        super().__init__(local_queue if local_queue is not None else asyncio.Queue())
        self._conn = conn
        self._request_id = request_id

    async def write(self, chunk: ResponseChunk) -> None:
        # Output goes over the socket only; the local reader yields nothing (the
        # gateway is the renderer). Do NOT enqueue locally — that would duplicate.
        await self._conn.send(chunk_to_frame(chunk))

    async def close(self) -> None:
        # Route the close over the socket (stamp the real request_id so the
        # gateway StreamDemux can route it)...
        await self._conn.send(
            ChunkFrame(
                content="",
                is_final=True,
                chunk_index=-1,
                trace_id=self._request_id,
                owl_name="",
            )
        )
        # ...AND release the local reader so the core's adapter.send completes
        # only now — after deliver. This is the back-pressure that mono gets for
        # free from its queue-backed reader.
        await super().close()


class SocketStreamRegistry(StreamRegistry):
    """Core-side StreamRegistry whose writers stream over the socket.

    ``create`` registers a :class:`SocketStreamWriter` (so the deliver step and
    progress emitter, which look the writer up by ``get_writer(trace_id)``, write
    ChunkFrames to the gateway) and returns a real queue-backed reader coupled to
    that writer's ``close``. ``get_writer``/``remove`` are inherited.
    """

    def __init__(self, conn: FrameConnection) -> None:
        super().__init__()
        self._conn = conn

    def create(self, request_id: str) -> tuple[StreamWriter, StreamReader]:
        # One queue shared by writer.close (producer of the sentinel) and the
        # reader (consumer) so adapter.send back-pressures until deliver closes.
        queue: asyncio.Queue[ResponseChunk] = asyncio.Queue()
        writer: StreamWriter = SocketStreamWriter(self._conn, request_id, queue)
        reader = StreamReader(queue)
        self._writers[request_id] = writer
        log.gateway.debug(
            "[ipc] socket stream registry: registered request",
            extra={"_fields": {"request_id": request_id}},
        )
        return writer, reader


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

    async def finalize_all(self) -> None:
        """Terminate every open reader (stream-finalize-on-cut).

        When the core connection drops mid-turn, the readers handed to
        ``adapter.send`` would otherwise hang forever (no more chunks arrive).
        Writing the ``is_final`` sentinel to each ends ``adapter.send`` cleanly so
        no spinner dangles. Idempotent — clears the registry afterwards.
        """
        request_ids = list(self._registry._writers.keys())
        for request_id in request_ids:
            writer = self._registry.get_writer(request_id)
            if writer is not None:
                with contextlib.suppress(Exception):
                    await writer.write(
                        ResponseChunk(
                            content="", is_final=True, chunk_index=-1,
                            trace_id=request_id, owl_name="",
                        )
                    )
            self._registry.remove(request_id)
        if request_ids:
            log.gateway.info(
                "[ipc] stream demux: finalized cut turns",
                extra={"_fields": {"count": len(request_ids)}},
            )

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
