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
    ConsentRequestFrame,
    ConsentResponseFrame,
    GoodbyeFrame,
    HelloFrame,
    ProgressEventFrame,
    RestartNoticeFrame,
    SendFileFrame,
    SendTextFrame,
)
from stackowl.ipc.stream_bridge import StreamDemux
from stackowl.runtime.message_bridge import ingress_to_frame

if TYPE_CHECKING:  # pragma: no cover — typing only
    from collections.abc import AsyncIterator

    from stackowl.pipeline.streaming import ResponseChunk
    from stackowl.tools.consent import ConsentRequest, ConsentScope


class _Adapter(Protocol):
    """The slice of a channel adapter the gateway link uses for delivery."""

    @property
    def channel_name(self) -> str: ...  # noqa: D102

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None: ...  # noqa: D102

    async def send_text(self, text: str) -> None: ...  # noqa: D102

    async def send_file(  # noqa: D102
        self, file_path: str, caption: str | None = ..., *, chat_id: str | int | None = ...
    ) -> None: ...


class _EventSink(Protocol):
    def emit(self, event: str, payload: object) -> None: ...  # noqa: D102


class _ConsentRouter(Protocol):
    """The gateway's RoutingPrompter slice used to resolve a consent request."""

    async def prompt(self, req: ConsentRequest) -> ConsentScope: ...  # noqa: D102


class GatewayLink:
    """Socket-backed TurnClient + outbound frame router, resilient to core restart."""

    def __init__(
        self,
        adapters: Mapping[str, _Adapter],
        demux: StreamDemux | None = None,
        event_bus: _EventSink | None = None,
        consent_router: _ConsentRouter | None = None,
    ) -> None:
        # Mutable copy so channels started after construction (Telegram/Slack/…
        # in gateway role) can register themselves via register_adapter.
        self._adapters: dict[str, _Adapter] = dict(adapters)
        self._demux = demux if demux is not None else StreamDemux()
        self._event_bus = event_bus
        # The gateway's RoutingPrompter (holds the real per-channel consent UI).
        # None in tests / CLI-only configs.
        self._consent_router = consent_router
        self._send_tasks: set[asyncio.Task[None]] = set()
        self._aux_tasks: set[asyncio.Task[None]] = set()
        # Connection state: None during the gap between a core exec-replace and
        # the fresh core's reconnect. ``_buffering`` is set the moment a restart
        # notice arrives (before the drop) so no in-flight message is sent to a
        # core that is tearing down.
        self._conn: FrameConnection | None = None
        self._buffering = False
        self._pending: list[IngressMessage] = []
        # F-35 — submitted-but-unfinished turns, keyed by trace_id (the request_id
        # used for demux routing AND core idempotency). A turn forwarded to a live
        # core lives here until its stream closes (is_final) — at which point it is
        # removed. If the core CRASHES mid-turn (drop_connection -> finalize), the
        # still-in-flight entries are moved back into ``_pending`` so the next Hello
        # REPLAYS them with the SAME trace_id (the core dedupes a double-execute),
        # instead of the goal evaporating and the user having to re-ask.
        self._inflight: dict[str, IngressMessage] = {}

    def register_adapter(self, channel_name: str, adapter: _Adapter) -> None:
        """Add a channel adapter so its turns route over the split (gateway role).

        Inbound: ``submit`` for ``msg.channel == channel_name`` opens a demux
        reader and spawns this adapter's ``send``. Outbound: ``SendTextFrame`` /
        the streamed answer route back here by channel / trace_id. The core (which
        owns the pipeline) handles the turn; this is pure I/O transport.
        """
        self._adapters[channel_name] = adapter
        log.gateway.info(
            "[ipc] gateway link: channel adapter registered",
            extra={"_fields": {"channel": channel_name}},
        )

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
        """End every cut turn's reader so no spinner dangles after a drop.

        F-35: before clearing the cut readers, move any still-in-flight turn (one
        whose stream never closed — the core crashed mid-turn) back into
        ``_pending`` so the next ``Hello`` replays its goal. The trace_id is reused,
        so the core dedupes a double-execute; the user's objective survives the
        crash instead of evaporating. Idempotent: ``_inflight`` is emptied here.
        """
        if self._inflight:
            requeued = list(self._inflight.values())
            self._inflight = {}
            # Prepend so a crash-replayed turn keeps FIFO order ahead of messages
            # that arrived during the gap.
            self._pending[:0] = requeued
            log.gateway.warning(
                "[ipc] gateway link: core cut mid-turn — requeuing in-flight turns",
                extra={"_fields": {"requeued": len(requeued)}},
            )
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
        # F-35 — track the turn as in-flight (keyed by trace_id) from the instant it
        # is forwarded, so a mid-turn core crash can replay it on reconnect. Removed
        # when its stream closes (is_final) in ``_route``.
        self._inflight[msg.trace_id] = msg
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
            if frame.is_final:
                # F-35 — the turn's stream closed normally; it is no longer
                # in-flight, so a later crash must NOT replay it.
                self._inflight.pop(frame.trace_id, None)
        elif isinstance(frame, SendTextFrame):
            adapter = self._adapters.get(frame.channel)
            if adapter is not None:
                await adapter.send_text(frame.text)
        elif isinstance(frame, SendFileFrame):
            adapter = self._adapters.get(frame.channel)
            if adapter is not None:
                # Target the specific chat when the core resolved one (telegram);
                # otherwise the adapter's default destination.
                if frame.target is not None:
                    await adapter.send_file(
                        frame.file_path, frame.caption, chat_id=frame.target
                    )
                else:
                    await adapter.send_file(frame.file_path, frame.caption)
        elif isinstance(frame, ClarifyAskFrame):
            await self._deliver_clarify(frame)
        elif isinstance(frame, ConsentRequestFrame):
            # Resolving consent BLOCKS on the user (button press, up to ~2 min) —
            # never inline in the frame loop, or it would stall every other turn's
            # chunks. Spawn it; the decision returns as a ConsentResponseFrame.
            task = asyncio.create_task(self._handle_consent(frame))
            self._aux_tasks.add(task)
            task.add_done_callback(self._aux_tasks.discard)
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
        # Route by the originating channel (falls back to the only adapter for the
        # CLI-only case). Render the question + any choices as a numbered list;
        # the user's typed reply on the same session+channel resolves the parked
        # turn core-side via the normal message path (no button round-trip needed).
        channel = frame.channel or next(iter(self._adapters), "")
        adapter = self._adapters.get(channel)
        if adapter is None:
            return
        text = frame.question
        if frame.choices:
            lines = [f"{i + 1}. {c}" for i, c in enumerate(frame.choices)]
            text = frame.question + "\n" + "\n".join(lines)
        with contextlib.suppress(Exception):
            await adapter.send_text(text)

    async def _handle_consent(self, frame: ConsentRequestFrame) -> None:
        from stackowl.tools.consent import ConsentRequest, ConsentScope

        scope = ConsentScope.DENY
        if self._consent_router is not None:
            try:
                req = ConsentRequest(
                    tool_name=frame.tool_name,
                    channel=frame.channel,
                    session_id=frame.session_id,
                    category=frame.category,
                    summary=frame.summary,
                    allow_relaxation=frame.allow_relaxation,
                )
                scope = await self._consent_router.prompt(req)
            except Exception as exc:  # noqa: BLE001 — fail closed on any error
                log.gateway.warning(
                    "[ipc] gateway link: consent prompt failed — denying",
                    extra={"_fields": {"consent_id": frame.consent_id, "error": str(exc)}},
                )
                scope = ConsentScope.DENY
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.send(
                    ConsentResponseFrame(
                        consent_id=frame.consent_id, scope=scope.value
                    )
                )
