"""GatewayLink — the durable gateway's view of the (restartable) core connection.

Implements the :class:`~stackowl.runtime.turn_client.TurnClient` ``submit`` seam
over the socket: ``submit(msg)`` opens a demux reader for the turn, spawns the
channel adapter's ``send`` over that reader (unchanged consumer), and forwards
the message as an IngressFrame. ``run(conn)`` routes one core connection's
outbound frames back to the channel adapters:

* ChunkFrame      -> StreamDemux (-> the turn's reader -> adapter.send)
* SendTextFrame   -> adapter.send_text (proactive/out-of-band)
* ClarifyAskFrame -> adapter clarify delivery
* ProgressEventFrame -> the gateway EventBus (TUI render)
* Hello           -> a (re)connected, ready core: flush any buffered submits
* RestartNotice   -> the core is about to exec-replace: start buffering
* Goodbye         -> core lifecycle

**Survives a core restart.** The core exec-replaces itself on a code change; its
socket drops and the durable gateway's listener accepts the fresh core. Between
those, ``submit`` BUFFERS inbound messages (the TUI never blocks) and
``finalize`` ends any cut turn's reader so no spinner dangles. ``set_connection``
/ ``drop_connection`` are driven by the gateway's accept handler — one
``run(conn)`` per core connection. Because the core decides STEER/STOP/NEW
internally, the gateway needs no steer/stop RPCs.
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
    """Socket-backed TurnClient + outbound frame router, resilient to core restart."""

    def __init__(
        self,
        adapters: Mapping[str, _Adapter],
        demux: StreamDemux | None = None,
        event_bus: _EventSink | None = None,
    ) -> None:
        self._adapters = adapters
        self._demux = demux if demux is not None else StreamDemux()
        self._event_bus = event_bus
        self._send_tasks: set[asyncio.Task[None]] = set()
        # Connection state: None during the gap between a core exec-replace and
        # the fresh core's reconnect. ``_buffering`` is set the moment a restart
        # notice arrives (before the drop) so no in-flight message is sent to a
        # core that is tearing down.
        self._conn: FrameConnection | None = None
        self._buffering = False
        self._pending: list[IngressMessage] = []

    # --- connection lifecycle (driven by the gateway accept handler) -------

    def set_connection(self, conn: FrameConnection) -> None:
        """Bind the current core connection (called per accepted connection)."""
        self._conn = conn
        log.gateway.info("[ipc] gateway link: core connection bound")

    def drop_connection(self) -> None:
        """Forget the current connection — subsequent submits buffer until reconnect."""
        self._conn = None
        self._buffering = True
        log.gateway.info("[ipc] gateway link: core connection dropped — buffering")

    async def finalize(self) -> None:
        """End every cut turn's reader so no spinner dangles after a drop."""
        await self._demux.finalize_all()

    # --- TurnClient.submit -------------------------------------------------

    async def submit(self, msg: IngressMessage) -> None:
        if self._conn is None or self._buffering:
            # Gap between core restarts: hold the message; flush on the next Hello.
            self._pending.append(msg)
            log.gateway.info(
                "[ipc] gateway link: buffering message during core restart",
                extra={"_fields": {"session_id": msg.session_id, "queued": len(self._pending)}},
            )
            return
        await self._do_submit(msg)

    async def _do_submit(self, msg: IngressMessage) -> None:
        adapter = self._adapters.get(msg.channel)
        if adapter is None:
            log.gateway.error(
                "[ipc] gateway link: submit for unregistered channel — dropping",
                extra={"_fields": {"channel": msg.channel, "request_id": msg.trace_id}},
            )
            return
        assert self._conn is not None
        # Open the reader and spawn the (unchanged) adapter consumer BEFORE the
        # core can stream the first chunk back, so no chunk is missed.
        reader = self._demux.register(msg.trace_id)
        task = asyncio.create_task(
            adapter.send(cast("AsyncIterator[ResponseChunk]", reader))
        )
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)
        await self._conn.send(ingress_to_frame(msg))

    async def _flush_pending(self) -> None:
        """Replay buffered messages once a fresh, ready core is connected."""
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        log.gateway.info(
            "[ipc] gateway link: flushing buffered messages after reconnect",
            extra={"_fields": {"count": len(pending)}},
        )
        for msg in pending:
            with contextlib.suppress(Exception):
                await self._do_submit(msg)

    # --- outbound frame router (one call per core connection) --------------

    async def run(self, conn: FrameConnection) -> None:
        async for frame in conn:
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
            # A (re)connected core that has finished booting: it can receive now,
            # so stop buffering and flush anything queued during the gap.
            log.gateway.info(
                "[ipc] gateway link: core ready (hello)",
                extra={"_fields": {"core_pid": frame.core_pid}},
            )
            self._buffering = False
            await self._flush_pending()
        elif isinstance(frame, RestartNoticeFrame):
            # The core is about to exec-replace itself — buffer from now so no
            # message is sent into a tearing-down core.
            log.gateway.info(
                "[ipc] gateway link: core restarting — buffering",
                extra={"_fields": {"reason": frame.reason}},
            )
            self._buffering = True
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
