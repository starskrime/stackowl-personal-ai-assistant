"""GatewayLink — the durable gateway's view of the core connection.

Implements the :class:`~stackowl.runtime.turn_client.TurnClient` ``submit`` seam
over the socket: ``submit(msg)`` opens a demux reader for the turn, spawns the
channel adapter's ``send`` over that reader (unchanged consumer), and forwards
the message as an IngressFrame. Its ``run`` loop routes the core's outbound
frames back to the channel adapters:

* ChunkFrame      -> StreamDemux (-> the turn's reader -> adapter.send)
* SendTextFrame   -> adapter.send_text (proactive/out-of-band)
* ClarifyAskFrame -> adapter clarify delivery
* ProgressEventFrame -> the gateway EventBus (TUI render)
* Hello/Goodbye/RestartNotice -> core lifecycle (operator-visible)

Because the core decides STEER/STOP/NEW internally (its ``_intake`` runs the
inflight router), the gateway needs no steer/stop RPCs — it just submits every
inbound message and a clarify reply is simply another submitted message.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, cast

from stackowl.gateway.scanner import IngressMessage
from stackowl.infra.observability import log
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import (
    ChunkFrame,
    ClarifyAskFrame,
    GoodbyeFrame,
    HelloFrame,
    ProgressEventFrame,
    RestartNoticeFrame,
    SendTextFrame,
)
from stackowl.ipc.stream_bridge import StreamDemux
from stackowl.runtime.message_bridge import ingress_to_frame

if TYPE_CHECKING:  # pragma: no cover — typing only
    from collections.abc import AsyncIterator

    from stackowl.pipeline.streaming import ResponseChunk


class _Adapter(Protocol):
    """The slice of a channel adapter the gateway link uses for delivery."""

    @property
    def channel_name(self) -> str: ...  # noqa: D102

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None: ...  # noqa: D102

    async def send_text(self, text: str) -> None: ...  # noqa: D102


class _EventSink(Protocol):
    def emit(self, event: str, payload: object) -> None: ...  # noqa: D102


class GatewayLink:
    """Socket-backed TurnClient + outbound frame router for the gateway."""

    def __init__(
        self,
        conn: FrameConnection,
        adapters: Mapping[str, _Adapter],
        demux: StreamDemux | None = None,
        event_bus: _EventSink | None = None,
    ) -> None:
        self._conn = conn
        self._adapters = adapters
        self._demux = demux if demux is not None else StreamDemux()
        self._event_bus = event_bus
        self._send_tasks: set[asyncio.Task[None]] = set()

    # --- TurnClient.submit -------------------------------------------------

    async def submit(self, msg: IngressMessage) -> None:
        adapter = self._adapters.get(msg.channel)
        if adapter is None:
            log.gateway.error(
                "[ipc] gateway link: submit for unregistered channel — dropping",
                extra={"_fields": {"channel": msg.channel, "request_id": msg.trace_id}},
            )
            return
        # Open the reader and spawn the (unchanged) adapter consumer BEFORE the
        # core can stream the first chunk back, so no chunk is missed.
        reader = self._demux.register(msg.trace_id)
        # StreamReader is an async-iterable the adapter consumes with `async for`;
        # cast to the adapter's AsyncIterator param (same shape the in-process
        # spawn_send already relies on).
        task = asyncio.create_task(
            adapter.send(cast("AsyncIterator[ResponseChunk]", reader))
        )
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)
        await self._conn.send(ingress_to_frame(msg))

    # --- outbound frame router --------------------------------------------

    async def run(self) -> None:
        async for frame in self._conn:
            with contextlib.suppress(Exception):
                await self._route(frame)

    async def _route(self, frame: object) -> None:
        if isinstance(frame, ChunkFrame):
            await self._demux.feed(frame)
        elif isinstance(frame, SendTextFrame):
            adapter = self._adapters.get(frame.channel)
            if adapter is not None:
                await adapter.send_text(frame.text)
        elif isinstance(frame, ClarifyAskFrame):
            await self._deliver_clarify(frame)
        elif isinstance(frame, ProgressEventFrame):
            if self._event_bus is not None:
                self._event_bus.emit(frame.event, frame.payload)
        elif isinstance(frame, HelloFrame):
            log.gateway.info(
                "[ipc] gateway link: core connected",
                extra={"_fields": {"core_pid": frame.core_pid}},
            )
        elif isinstance(frame, RestartNoticeFrame):
            log.gateway.info(
                "[ipc] gateway link: core restarting",
                extra={"_fields": {"reason": frame.reason}},
            )
        elif isinstance(frame, GoodbyeFrame):
            log.gateway.info("[ipc] gateway link: core said goodbye")

    async def _deliver_clarify(self, frame: ClarifyAskFrame) -> None:
        adapter = self._adapters.get(
            # ClarifyAskFrame has no channel; deliver on the session's adapter via
            # send_text as the base path (rich button delivery lands with the fork).
            next(iter(self._adapters), "")
        )
        if adapter is not None:
            with contextlib.suppress(Exception):
                await adapter.send_text(frame.question)
