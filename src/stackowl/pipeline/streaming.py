"""Streaming contract — ResponseChunk, StreamWriter, StreamReader, StreamRegistry."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel

from stackowl.infra.observability import log

# Discriminates the durable ANSWER body from ephemeral live-PROGRESS updates that
# share the same per-turn stream. Defaulting to "answer" keeps every existing
# construction site byte-identical. Progress chunks are best-effort liveness
# ("Searching the web…"); a channel adapter renders them transiently and MUST NOT
# concatenate them into the answer body. See pipeline/progress/.
ChunkKind = Literal["answer", "progress"]


class ResponseChunk(BaseModel, frozen=True):
    """A single streamed token fragment from the pipeline."""

    content: str
    is_final: bool
    chunk_index: int
    trace_id: str
    owl_name: str
    duration_ms: float | None = None
    # "answer" (default) = durable response body; "progress" = ephemeral live status.
    kind: ChunkKind = "answer"
    # Optional delivery target for fan-out channels (e.g. a Telegram chat_id).
    # None → the channel adapter resolves the destination itself.
    # String targets are for Slack (channel id / thread_ts); int for Telegram chat_id.
    target: int | str | None = None
    # True when this chunk is the deterministic never-empty FLOOR (the zero-provider
    # backstop synthesized at the execute site). A response consisting ONLY of floor
    # chunks is the honest last resort: the critical-failure cascade treats it as
    # NOT-yet-usable so a localized LLM apology (better UX) can REPLACE it when any
    # provider is alive. A genuine (non-floor) response short-circuits the cascade.
    is_floor: bool = False


class StreamWriter:
    """Wraps an asyncio.Queue to provide a typed write/close interface."""

    def __init__(self, queue: asyncio.Queue[ResponseChunk]) -> None:
        self._queue = queue

    async def write(self, chunk: ResponseChunk) -> None:
        await self._queue.put(chunk)

    async def close(self) -> None:
        sentinel = ResponseChunk(
            content="",
            is_final=True,
            chunk_index=-1,
            trace_id="",
            owl_name="",
        )
        await self._queue.put(sentinel)


class StreamReader:
    """Async iterator that yields chunks until the sentinel is received."""

    def __init__(self, queue: asyncio.Queue[ResponseChunk]) -> None:
        self._queue = queue

    def __aiter__(self) -> AsyncIterator[ResponseChunk]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[ResponseChunk]:
        while True:
            chunk = await self._queue.get()
            if chunk.is_final:
                break
            yield chunk


class StreamRegistry:
    """Process-level registry of active stream writers, keyed by request_id.

    request_id == the turn's trace_id. Keying per request (not per session) gives
    each concurrent turn its own slot — cross-session parallelism plus
    request↔response correlation — so a turn's output is NEVER rerouted to another
    turn's stream.
    """

    def __init__(self) -> None:
        self._writers: dict[str, StreamWriter] = {}

    def create(self, request_id: str) -> tuple[StreamWriter, StreamReader]:
        """Create a linked writer/reader pair and register the writer."""
        queue: asyncio.Queue[ResponseChunk] = asyncio.Queue()
        writer = StreamWriter(queue)
        reader = StreamReader(queue)
        self._writers[request_id] = writer
        log.gateway.debug(
            "[stream] registry.create: registered request",
            extra={"_fields": {"request_id": request_id}},
        )
        return writer, reader

    def get_writer(self, request_id: str) -> StreamWriter | None:
        return self._writers.get(request_id)

    def remove(self, request_id: str) -> None:
        self._writers.pop(request_id, None)
        log.gateway.debug(
            "[stream] registry.remove: unregistered request",
            extra={"_fields": {"request_id": request_id}},
        )
