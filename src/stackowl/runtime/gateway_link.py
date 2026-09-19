"""GatewayLink — the durable gateway's view of the (restartable) core connection.

Implements the :class:`~stackowl.runtime.turn_client.TurnClient` ``submit`` seam
over the socket: ``submit(msg)`` opens a demux reader for the turn, spawns the
channel adapter's ``send`` over that reader (unchanged consumer), and forwards
the message as an IngressFrame. ``run(conn)`` routes one core connection's
outbound frames back to the channel adapters:

* ChunkFrame      -> StreamDemux (-> the turn's reader -> adapter.send)
* SendTextFrame   -> adapter.send_text (proactive/out-of-band)
* ClarifyAskFrame -> adapter clarify delivery
* JournalEventFrame -> narrate + re-emit ``pipeline_step_changed`` on the
  gateway EventBus (Spec 2.5 — split-mode TUI progress, from the journal)
* Hello           -> a (re)connected, ready core: catch up the journal from
  the last delivered cursor, THEN flush any buffered submits
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
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol, cast

from stackowl.exceptions import LinkAuthenticationError
from stackowl.gateway.scanner import IngressMessage
from stackowl.infra.observability import log
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import (
    ChunkFrame,
    ClarifyAskFrame,
    ConsentRequestFrame,
    ConsentResponseFrame,
    DeleteMessageFrame,
    EphemeralSentFrame,
    GoodbyeFrame,
    HelloFrame,
    JournalEventFrame,
    RestartNoticeFrame,
    SendEphemeralFrame,
    SendFileFrame,
    SendTextFrame,
    TasksEnqueuedFrame,
)
from stackowl.ipc.stream_bridge import StreamDemux
from stackowl.journal import fanout as journal_fanout
from stackowl.journal import write_gate
from stackowl.journal.enums import ActorKind, NeedsYouKind, Outcome
from stackowl.journal.models import JournalEvent, RecordRef
from stackowl.journal.narrator import NarrationResult, narrate
from stackowl.journal.registry import get_registry
from stackowl.runtime import link_auth, link_health
from stackowl.runtime.hello import evaluate_hello
from stackowl.runtime.message_bridge import ingress_to_frame

if TYPE_CHECKING:  # pragma: no cover — typing only
    from collections.abc import AsyncIterator

    from stackowl.journal.fanout import RowFetcher
    from stackowl.pipeline.streaming import ResponseChunk
    from stackowl.tools.consent import ConsentRequest, ConsentScope


class _Adapter(Protocol):
    """The slice of a channel adapter the gateway link uses for delivery."""

    @property
    def channel_name(self) -> str: ...  # noqa: D102

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None: ...  # noqa: D102

    async def send_text(  # noqa: D102
        self, text: str, *, chat_id: str | int | None = ...
    ) -> object: ...

    async def send_file(  # noqa: D102
        self, file_path: str, caption: str | None = ..., *, chat_id: str | int | None = ...
    ) -> None: ...

    async def send_ephemeral(self, chat_id: str | int, text: str) -> int: ...  # noqa: D102

    async def delete_message(  # noqa: D102
        self, chat_id: str | int, message_id: int
    ) -> bool: ...


class _EventSink(Protocol):
    def emit(self, event: str, payload: object) -> None: ...  # noqa: D102


class _ConsentRouter(Protocol):
    """The gateway's RoutingPrompter slice used to resolve a consent request."""

    async def prompt(self, req: ConsentRequest) -> ConsentScope: ...  # noqa: D102


class _NeedsYouNotifier(Protocol):
    """The gateway's TelegramIncidentAlertNotifier slice — pushes a newly
    -opened `incident`/`alert` item that has no live waiter (Story 3.6)."""

    async def deliver_opened(  # noqa: D102
        self, item_id: str, kind: NeedsYouKind, chat_id: int, text: str,
    ) -> None: ...


# F-38 — user-facing notice when a buffered turn can no longer be resumed after a
# restart, having exhausted its bounded replay retries. No internals, channel-safe.
_REPLAY_FAILURE_NOTICE = (
    "Sorry — I couldn't resume an earlier request after a restart, so it didn't "
    "go through. Please send it again."
)

# Spec 2.5 — journal fan-out's ``pipeline_step_changed`` re-emission needs a
# bounded "train" width for ``PipelineStrip`` (``tui/widgets/pipeline_strip.py``),
# the same nominal value ``pipeline/progress/emitter.py``'s ``_NOMINAL_TOTAL_STEPS``
# uses for the mono-mode path — duplicated here rather than imported, matching
# this diff's own choice to duplicate the ``"pipeline_step_changed"`` event name
# literal too, rather than adding a runtime/ -> pipeline/ cross-subsystem import
# for two small UI constants.
_NOMINAL_TOTAL_STEPS = 8

# Review fix — the page size `_catch_up_journal` requests per `read_since`
# call. Explicit (not just inherited from `read_since`'s own default) so the
# catch-up loop's drain-every-page logic knows exactly what "a full page"
# means: it keeps reading while a page comes back exactly this size, and
# stops once one comes back smaller — so a gap wider than one page (a real
# outage, not just a single reconnect racing one commit) is never left
# under-delivered (AC3: "no loss ... across a real restart").
_JOURNAL_CATCH_UP_PAGE_LIMIT = 200


def _unify_gateway_enabled() -> bool:
    """ADR-2 flag read (``unify_gateway_recovery``). Fail-safe to True (the owner-approved
    default) on any config error — a flag read must never break turn replay. Consulted ONLY
    on the replay-failure path, so a healthy reconnect never constructs Settings here."""
    try:
        from stackowl.config.settings import cached_settings

        return bool(cached_settings().unify_gateway_recovery)
    except Exception:  # noqa: BLE001 — a flag read must never raise into the gateway link
        return True


class GatewayLink:
    """Socket-backed TurnClient + outbound frame router, resilient to core restart."""

    # F-38 — how many times a buffered turn is replayed before it is surfaced to
    # the user as undeliverable (rather than silently retried forever or dropped).
    _MAX_REPLAY_ATTEMPTS = 3

    def __init__(
        self,
        adapters: Mapping[str, _Adapter],
        demux: StreamDemux | None = None,
        event_bus: _EventSink | None = None,
        consent_router: _ConsentRouter | None = None,
        recovery: object | None = None,
        *,
        link_secret: str,
        journal_fetcher: RowFetcher | None = None,
        needs_you_notifier: _NeedsYouNotifier | None = None,
    ) -> None:
        # Spec 2.4 — this gateway boot's per-boot secret (runtime.link_auth),
        # verified against every connected core's real Hello in `_route`
        # BEFORE the existing evaluate_hello compatibility check. REQUIRED
        # (no default): a GatewayLink with no secret to check against would
        # silently accept an unauthenticated link.
        self._link_secret = link_secret
        # Mutable copy so channels started after construction (Telegram/Slack/…
        # in gateway role) can register themselves via register_adapter.
        self._adapters: dict[str, _Adapter] = dict(adapters)
        # ADR-2 — the one recovery authority. The buffered-turn replay retry DECISION
        # (F-38) delegates to its ``should_retry`` predicate (flag ``unify_gateway_recovery``)
        # so one policy governs every subsystem's recovery. Lazily constructed (the actuator
        # lives in the pipeline layer); injectable for tests.
        self._recovery = recovery
        self._demux = demux if demux is not None else StreamDemux()
        self._event_bus = event_bus
        # Story 3.6 — the gateway's TelegramIncidentAlertNotifier, pushed to on
        # `needs_you.opened` for `incident`/`alert` items (the two kinds with
        # no live waiter of their own). `None` in tests / CLI-only configs /
        # a gateway with no Telegram adapter configured — the delivery
        # branch below is then a no-op, never a crash.
        self._needs_you_notifier = needs_you_notifier
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
        # Spec 2.3 — this gateway's OWN Hello, bound per-connection (fresh per
        # accept, never cached across a core reconnect — AD-33). Compared
        # against every inbound HelloFrame in `_route` via `evaluate_hello`.
        self._local_hello: HelloFrame | None = None
        self._pending: list[IngressMessage] = []
        # F-35 — submitted-but-unfinished turns, keyed by trace_id (the request_id
        # used for demux routing AND core idempotency). A turn forwarded to a live
        # core lives here until its stream closes (is_final) — at which point it is
        # removed. If the core CRASHES mid-turn (drop_connection -> finalize), the
        # still-in-flight entries are moved back into ``_pending`` so the next Hello
        # REPLAYS them with the SAME trace_id (the core dedupes a double-execute),
        # instead of the goal evaporating and the user having to re-ask.
        self._inflight: dict[str, IngressMessage] = {}
        # F-38 — per-turn replay attempt counter (keyed by trace_id). A buffered
        # turn whose replay raises is re-queued and retried on the next Hello up
        # to ``_MAX_REPLAY_ATTEMPTS``; cleared on success or after surfacing.
        self._replay_attempts: dict[str, int] = {}
        # Spec 2.5 — the ONLY de-dup authority for journal fan-out (Boundaries
        # & Constraints): a pushed frame or a catch-up row whose cursor is
        # <= this watermark is silently dropped; a higher one is delivered and
        # advances it. Initializes to 0 (a fresh gateway boot's default: skip
        # history, disclosed in Design Notes) and is re-based to
        # ``current_max_cursor`` by the caller at boot in the real orchestrator
        # wiring, mirroring the core push loop's own boot-time initialization.
        self._last_delivered_cursor = 0
        # Spec 2.5 — verification fix: the journal `cursor` is a GLOBAL,
        # ever-growing counter across every table row, never a per-turn step
        # count. Emitting it as BOTH `step_index` and `total_steps` (an
        # earlier draft of this wiring) made ``PipelineStrip`` — which renders
        # ``for i in range(total_steps): filled if i < step_index`` — always
        # show every glyph filled on an unboundedly-growing strip (step_index
        # == total_steps on every single delivery). A small local counter,
        # wrapped at the SAME nominal width ``pipeline/progress/emitter.py``
        # already uses for the mono-mode path, keeps the widget's bounded
        # "train" contract instead.
        self._progress_step_index = 0
        # Spec 2.5 — the gateway's own DbPool, injected so ``_catch_up_journal``
        # can run a bounded ``read_since`` on a Hello (re)connect. ``None`` in
        # tests that never exercise catch-up (mirrors ``recovery``/``event_bus``'s
        # own optional-injection shape above).
        self._journal_fetcher = journal_fetcher

    def set_initial_cursor(self, cursor: int) -> None:
        """Seed the journal watermark at boot (own process start).

        Called once by the real orchestrator wiring, right after construction
        and before any Hello can arrive, with ``journal.fanout.current_max_cursor``
        (Boundaries & Constraints: "Both loops' cursor watermark initializes to
        ``SELECT MAX(cursor)`` at boot ... neither process ever floods a fresh
        connection with full history"). The constructor itself still defaults
        the watermark to 0 so a bare ``GatewayLink()`` in a unit test needs no
        DB at all.
        """
        self._last_delivered_cursor = cursor

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

    def set_needs_you_notifier(self, notifier: _NeedsYouNotifier) -> None:
        """Wire the TelegramIncidentAlertNotifier once the Telegram adapter is
        up (Story 3.6) — mirrors ``register_adapter``'s own post-construction
        wiring seam: ``GatewayLink`` is constructed before the per-channel
        blocks that build the real Telegram adapter run (``_phase_gateway``).
        """
        self._needs_you_notifier = notifier
        log.gateway.info("[ipc] gateway link: needs_you incident/alert notifier wired")

    # --- connection lifecycle (driven by the gateway accept handler) -------

    def set_connection(self, conn: FrameConnection, *, local_hello: HelloFrame) -> None:
        """Bind the current core connection (called per accepted connection).

        ``local_hello`` is THIS gateway's own, freshly-computed Hello (AD-33 —
        never cached across a core reconnect) — stored so the HelloFrame
        branch in ``_route`` can evaluate the incoming core's Hello against it.
        """
        self._conn = conn
        self._local_hello = local_hello
        log.gateway.info("[ipc] gateway link: core connection bound")

    def drop_connection(self) -> None:
        """Forget the current connection — subsequent submits buffer until reconnect."""
        self._conn = None
        self._buffering = True
        # Spec 2.3 — a lost link means this gateway can no longer prove its
        # schema/registry match the core that just vanished; refuse new
        # journal writes until the NEXT confirmed-compatible Hello.
        write_gate.pause_writes("gateway-core link lost")
        log.gateway.info("[ipc] gateway link: core connection dropped — buffering")

    @property
    def consecutive_hello_mismatches(self) -> int:
        """The SAME counter ``GatewayCoreLinkHealthContributor`` reports on —
        delegates to ``runtime.link_health`` so ``_supervise_core`` (which polls
        this to decide whether to keep respawning the core) and the health
        sweep can never disagree."""
        return link_health.mismatch_count()

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
                extra={"_fields": {"session_key": msg.session_key, "queued": len(self._pending)}},
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
        task.add_done_callback(self._make_send_cleanup(msg))
        await self._conn.send(ingress_to_frame(msg))

    async def notify_tasks_enqueued(self) -> None:
        """Tell core "work is waiting, wake the loop now" (Story 4.3, AD-1).

        For a gateway-role caller of ``commands/spec/submit.py::
        submit_command(..., run_inline=False, notify_enqueued=...)`` — a
        process with no in-process ``TaskLoop`` to claim against. Sends the
        payload-free ``TasksEnqueuedFrame``; core's ``_core_frame_loop`` wakes
        its own loop on receipt. Best-effort, mirroring ``_handle_consent``'s
        own "no connection, or the send itself fails -> log and move on"
        shape — a lost wake costs one tick of latency, never the row (the
        tick is still the safety net).
        """
        if self._conn is None:
            log.gateway.debug(
                "[ipc] gateway link: notify_tasks_enqueued: no connection — "
                "the tick loop will still pick this up",
            )
            return
        try:
            await self._conn.send(TasksEnqueuedFrame())
        except Exception as exc:  # noqa: BLE001 — best-effort, never the row's problem
            log.gateway.warning(
                "[ipc] gateway link: notify_tasks_enqueued: send failed — "
                "the tick loop will still pick this up",
                exc_info=exc,
            )

    def _make_send_cleanup(
        self, msg: IngressMessage,
    ) -> Callable[[asyncio.Task[None]], None]:
        """Build the done-callback for a spawned ``adapter.send`` task.

        Mirrors ``ClarifyPump._cleanup`` on the core side: the gateway is the
        ONLY place that sees a real channel-delivery failure (a flood-control
        ban, a network error) — the core's own send task is just forwarding
        ChunkFrames over the socket and completes as soon as the local sentinel
        is consumed, oblivious to what happens downstream. Before this, a bare
        ``task.add_done_callback(self._send_tasks.discard)`` dropped the task
        reference without ever inspecting ``task.exception()``, so a real
        delivery failure (e.g. Telegram's ``RetryAfter``) was never logged and
        never reached the recovery authority — the turn's computed answer was
        silently lost with no trace beyond the adapter's own low-level warning.
        """
        def _cleanup(task: asyncio.Task[None]) -> None:
            self._send_tasks.discard(task)
            if task.cancelled():
                return
            exc = task.exception()
            if exc is None:
                return
            log.gateway.error(
                "[ipc] gateway link: adapter.send failed — response was not "
                "delivered to the user",
                exc_info=exc,
                extra={"_fields": {
                    "request_id": msg.trace_id, "channel": msg.channel,
                    "session_key": msg.session_key,
                }},
            )
            from stackowl.pipeline.recovery_actuator import Failure, RecoveryActuator

            if self._recovery is None:
                self._recovery = RecoveryActuator()
            failure = Failure(
                name="gateway_link.send_task",
                kind="send_task",
                consequential=True,
                error=str(exc),
            )
            recovery_task = asyncio.create_task(self._recovery.recover(failure))  # type: ignore[attr-defined]
            self._aux_tasks.add(recovery_task)
            recovery_task.add_done_callback(self._aux_tasks.discard)

        return _cleanup

    async def _flush_pending(self) -> None:
        """Replay buffered messages once a fresh, ready core is connected.

        F-38: a replay that raises is NOT silently dropped. A transient fault
        (the fresh core's socket faltering) re-queues the turn for the next
        ``Hello``, up to ``_MAX_REPLAY_ATTEMPTS``; once exhausted the turn is
        surfaced to its originating channel as a visible failure notice instead
        of vanishing. A turn forwarded successfully clears its attempt counter.
        """
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        log.gateway.info(
            "[ipc] gateway link: flushing buffered messages after reconnect",
            extra={"_fields": {"count": len(pending)}},
        )
        for msg in pending:
            try:
                await self._do_submit(msg)
            except Exception as exc:  # noqa: BLE001 — one bad replay must not drop the rest
                # The turn never reached the core; it must not stay tracked as
                # in-flight (that map is the crash-replay source, and _pending now
                # owns this turn's fate).
                self._inflight.pop(msg.trace_id, None)
                attempts = self._replay_attempts.get(msg.trace_id, 0) + 1
                if attempts < self._MAX_REPLAY_ATTEMPTS and self._may_retry_replay(exc):
                    self._replay_attempts[msg.trace_id] = attempts
                    self._pending.append(msg)
                    log.gateway.warning(
                        "[ipc] gateway link: replay failed — re-queued for retry",
                        extra={
                            "_fields": {
                                "request_id": msg.trace_id,
                                "channel": msg.channel,
                                "attempt": attempts,
                                "error": str(exc),
                            }
                        },
                    )
                else:
                    self._replay_attempts.pop(msg.trace_id, None)
                    log.gateway.error(
                        "[ipc] gateway link: replay exhausted retries — notifying channel",
                        exc_info=exc,
                        extra={
                            "_fields": {
                                "request_id": msg.trace_id,
                                "channel": msg.channel,
                                "attempts": attempts,
                            }
                        },
                    )
                    await self._notify_replay_failure(msg)
            else:
                self._replay_attempts.pop(msg.trace_id, None)

    def _may_retry_replay(self, exc: Exception) -> bool:
        """Whether a failed turn replay may be retried — the ONE recovery authority decides (ADR-2).

        When ``unify_gateway_recovery`` is on (default) the retry-vs-surface decision is
        delegated to :meth:`RecoveryActuator.should_retry` over a typed ``Failure`` instead of
        the inline replay-budget guard. A lost in-flight turn is non-consequential and
        transient-by-policy (a faulted fresh-core socket self-heals on the next Hello), so the
        authority returns True and the outcome is byte-identical to the inline ``attempts <
        _MAX_REPLAY_ATTEMPTS`` gate — the policy now lives in ONE place. Flag off ⇒ the inline
        budget gate decides alone (the actuator is not consulted), byte-identical to pre-ADR.
        A flag-read error fails safe to the unified path (the owner-approved default)."""
        if not _unify_gateway_enabled():
            return True
        from stackowl.pipeline.recovery_actuator import Failure, RecoveryActuator

        if self._recovery is None:
            self._recovery = RecoveryActuator()
        failure = Failure(
            name="gateway_replay",
            kind="gateway_turn",
            transient=True,
            consequential=False,
            error=str(exc),
        )
        return bool(self._recovery.should_retry(failure))  # type: ignore[attr-defined]

    async def _notify_replay_failure(self, msg: IngressMessage) -> None:
        """Surface a permanently-undeliverable replayed turn to its channel (F-38)."""
        adapter = self._adapters.get(msg.channel)
        if adapter is None:
            log.gateway.error(
                "[ipc] gateway link: replay failed for unregistered channel — dropping",
                extra={"_fields": {"channel": msg.channel, "request_id": msg.trace_id}},
            )
            return
        try:
            await adapter.send_text(_REPLAY_FAILURE_NOTICE)
        except Exception as exc:  # noqa: BLE001 — notice delivery is best-effort
            log.gateway.error(
                "[ipc] gateway link: failed to deliver replay-failure notice",
                exc_info=exc,
                extra={"_fields": {"channel": msg.channel, "request_id": msg.trace_id}},
            )

    # --- outbound frame router (one call per core connection) --------------

    async def run(self, conn: FrameConnection) -> None:
        async for frame in conn:
            # Spec 2.4 — a failed link-secret check must NOT be swallowed by the
            # general "one bad frame must not kill the connection" resilience
            # wrapper below: it means this peer failed identity verification,
            # and the connection MUST end (the `_accept_core` `finally` already
            # calls drop_connection()/finalize() on any exit from `run`, so
            # re-raising here is sufficient — no new pause/buffer plumbing).
            try:
                await self._route(frame)
            except LinkAuthenticationError:
                raise
            except Exception:  # noqa: BLE001 — every OTHER bad frame stays non-fatal
                pass

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
                # Target the specific chat when the core resolved one; otherwise
                # the adapter's default destination.
                if frame.target is not None:
                    await adapter.send_text(frame.text, chat_id=frame.target)
                else:
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
        elif isinstance(frame, SendEphemeralFrame):
            task = asyncio.create_task(self._handle_send_ephemeral(frame))
            self._aux_tasks.add(task)
            task.add_done_callback(self._aux_tasks.discard)
        elif isinstance(frame, DeleteMessageFrame):
            adapter = self._adapters.get(frame.channel)
            if adapter is not None:
                with contextlib.suppress(Exception):
                    await adapter.delete_message(frame.target, frame.message_id)
        elif isinstance(frame, JournalEventFrame):
            await self._deliver_journal_event(frame)
        elif isinstance(frame, HelloFrame):
            # Spec 2.4 — the per-boot link secret is verified FIRST, before the
            # Spec 2.3 compatibility check below: a peer that already passed the
            # peer-PID check (`_accept_core`, before any Hello was even sent)
            # still must prove it is running WITH the secret this gateway boot
            # minted, not just a same-PID process that raced in some other way.
            # WHY THIS CHECK EXISTS ON TOP OF THE PEER-PID CHECK: during the
            # crash-respawn window, `core_proc_holder["proc"]` can briefly still
            # hold the PID of the core process that just died, before
            # `_supervise_core` reassigns it to the freshly spawned replacement.
            # If the OS reused that now-dead PID for an unrelated same-user
            # process in that narrow window, `authorize_peer` alone could be
            # fooled into treating it as the supervised core. That impostor has
            # no way to know this gateway boot's `link_secret`, so
            # `verify_link_secret` independently refuses it even when the
            # peer-PID check alone would not have.
            # A failure here is a SecurityError subclass that PROPAGATES OUT of
            # `run()`'s frame loop (never `link_health.note_mismatch` — that
            # counter drives `_supervise_core`'s stand-down-respawning decision
            # for genuine version skew, and counting an impersonation attempt
            # there would let an attacker force the gateway to stop respawning
            # the real core — a self-inflicted DoS).
            if not link_auth.verify_link_secret(
                expected=self._link_secret, presented=frame.link_secret,
            ):
                raise LinkAuthenticationError(
                    "Hello link_secret did not match this gateway boot's secret",
                    remedy=(
                        "the connected core did not present this gateway boot's "
                        "link secret — a code-change restart (os.execv) inherits "
                        "it unchanged, so this should only fire for a genuinely "
                        "unauthenticated peer; restart the gateway if this "
                        "persists across every reconnect"
                    ),
                    context={"sender_pid": frame.sender_pid},
                )
            # Spec 2.3 — a (re)connected core's Hello is evaluated against THIS
            # gateway's own (bound at `set_connection` time) before it is
            # trusted to receive. `self._local_hello` is None only if a
            # HelloFrame arrives with no prior `set_connection` — treated as an
            # incompatible link, fail-safe, rather than crashing on frame.
            local = self._local_hello
            verdict = (
                evaluate_hello(local=local, remote=frame, local_is_core=False)
                if local is not None
                else None
            )
            if verdict is None or not verdict.compatible:
                log.gateway.error(
                    "[ipc] gateway link: Hello mismatch — refusing link",
                    extra={"_fields": {
                        "local": local.model_dump() if local is not None else None,
                        "remote": frame.model_dump(),
                    }},
                )
                if local is not None:
                    link_health.note_mismatch(local, frame)
                write_gate.pause_writes("gateway/core Hello mismatch")
                # Defense-in-depth — reinforces the AC's "refuses to activate
                # the link": not reachable via today's real wiring (a core
                # sends exactly one Hello per connection, so `_buffering` is
                # already True from `set_connection`'s caller), but a link
                # that failed compatibility must never read as active.
                self._buffering = True
                return
            link_health.note_match()
            write_gate.resume_writes()
            # Spec 2.5, AC3 — a core os.execv restart or a lost link never loses
            # or duplicates a journal row: BEFORE anything else resumes, catch
            # up every row committed during the gap from this gateway's own
            # last delivered cursor. Runs before `_flush_pending` so a replayed
            # turn's own progress events land in the right order relative to
            # the catch-up rows already ahead of them.
            #
            # Review fix — a raise out of `_catch_up_journal` (a transient DB
            # error, a poisoned row escaping its own inner guard) must NEVER
            # propagate out of this `HelloFrame` branch: `run()`'s per-frame
            # wrapper swallows any non-`LinkAuthenticationError` SILENTLY, which
            # would skip `self._buffering = False` / `_flush_pending()` below and
            # leave every buffered turn stuck with no user-visible error. Logged
            # loudly here instead, and the reconnect still completes — a missed
            # catch-up row still self-heals on the NEXT reconnect or live push.
            try:
                await self._catch_up_journal()
            except Exception as exc:  # noqa: BLE001 — must never block the reconnect below
                log.gateway.error(
                    "[ipc] gateway link: journal catch-up failed — reconnect "
                    "continues anyway (a missed row self-heals on the next "
                    "reconnect or live push)",
                    exc_info=exc,
                )
            # A (re)connected core that has finished booting: it can receive now,
            # so stop buffering and flush anything queued during the gap.
            log.gateway.info(
                "[ipc] gateway link: core ready (hello)",
                extra={"_fields": {"sender_pid": frame.sender_pid}},
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

    # --- Spec 2.5 — journal fan-out (split-mode TUI progress) --------------

    async def _deliver_journal_event(self, frame: JournalEventFrame) -> None:
        """One pushed ``JournalEventFrame`` on the LIVE path (after commit).

        Dedup/ordering share :meth:`_deliver_journal_row` with the catch-up
        path below — the ``last_delivered_cursor`` watermark is the ONE
        authority (Boundaries & Constraints), so a live push racing a
        reconnect catch-up read is absorbed the same way a genuine wire
        duplicate is.
        """
        await self._deliver_journal_row(
            cursor=frame.cursor,
            event_id=frame.event_id,
            event_type=frame.event_type,
            schema_version=frame.schema_version,
            occurred_at=frame.occurred_at,
            actor_kind=frame.actor_kind,
            actor_id=frame.actor_id,
            device_id=frame.device_id,
            target_kind=frame.target_kind,
            target_id=frame.target_id,
            outcome=frame.outcome,
            attention=frame.attention,
            intensity=frame.intensity,
            record_ref=frame.record_ref,
            attrs=frame.attrs,
            trace_id=frame.trace_id,
            duration_ms=frame.duration_ms,
        )

    async def _catch_up_journal(self) -> None:
        """A (re)connected core's Hello: read every row committed during the
        gap (core ``os.execv`` restart or a lost link) BEFORE live frame
        delivery resumes (Spec 2.5, AC3).

        ``self._journal_fetcher is None`` (no DbPool injected — e.g. most
        unit tests) is a no-op: there is nothing to catch up FROM, and the
        live push path alone still delivers everything from here on.

        Review fix — drains EVERY page, not just the first: a single
        ``read_since`` call is capped at ``_JOURNAL_CATCH_UP_PAGE_LIMIT``
        rows, so a gap wider than that (a real outage, not a brief
        disconnect) would otherwise be silently, permanently
        under-delivered. Keeps reading while a page comes back full-size and
        stops once one comes back smaller.
        """
        if self._journal_fetcher is None:
            log.gateway.debug(
                "[ipc] gateway link: journal catch-up skipped — no fetcher injected",
            )
            return
        total_delivered = 0
        while True:
            rows = await journal_fanout.read_since(
                self._journal_fetcher, self._last_delivered_cursor,
                limit=_JOURNAL_CATCH_UP_PAGE_LIMIT,
            )
            if not rows:
                break
            for row in rows:
                await self._deliver_journal_row(
                    cursor=row.cursor,
                    event_id=row.event_id,
                    event_type=row.type,
                    schema_version=row.schema_version,
                    occurred_at=row.occurred_at,
                    actor_kind=row.actor_kind,
                    actor_id=row.actor_id,
                    device_id=row.device_id,
                    target_kind=row.target_kind,
                    target_id=row.target_id,
                    outcome=row.outcome,
                    attention=row.attention,
                    intensity=row.intensity,
                    record_ref=row.record_ref,
                    attrs=row.attrs,
                    trace_id=row.trace_id,
                    duration_ms=row.duration_ms,
                )
            total_delivered += len(rows)
            if len(rows) < _JOURNAL_CATCH_UP_PAGE_LIMIT:
                break
        log.gateway.info(
            "[ipc] gateway link: journal catch-up",
            extra={"_fields": {"row_count": total_delivered}},
        )

    async def _deliver_journal_row(
        self,
        *,
        cursor: int,
        event_id: str,
        event_type: str,
        schema_version: int,
        occurred_at: str,
        actor_kind: str,
        actor_id: str,
        device_id: str | None,
        target_kind: str,
        target_id: str,
        outcome: str,
        attention: str | None,
        intensity: str | None,
        record_ref: dict[str, object] | None,
        attrs: dict[str, object],
        trace_id: str | None,
        duration_ms: int | None,
    ) -> None:
        """Narrate one journal row and re-emit ``pipeline_step_changed`` —
        shared by both the live push path and the reconnect catch-up path.

        Dedup: the ``last_delivered_cursor`` watermark is the ONLY authority
        (Boundaries & Constraints) — a ``cursor <= last_delivered_cursor`` is
        silently dropped; a higher one is delivered and advances it.

        The typed ``attrs`` model is reconstructed from the wire dict via the
        registry BEFORE calling ``journal.narrate()`` — safe only because
        Story 2.3's Hello registry-digest check already guarantees the
        gateway and core share one identical registry (Design Notes).
        """
        # 1. ENTRY
        log.gateway.debug(
            "[ipc] gateway link: _deliver_journal_row: entry",
            extra={"_fields": {
                "cursor": cursor, "event_id": event_id, "event_type": event_type,
            }},
        )
        # 2. DECISION — the watermark is the one de-dup authority.
        if cursor <= self._last_delivered_cursor:
            log.gateway.debug(
                "[ipc] gateway link: journal row dropped — at or below watermark",
                extra={"_fields": {
                    "cursor": cursor, "event_id": event_id,
                    "last_delivered_cursor": self._last_delivered_cursor,
                }},
            )
            return
        try:
            spec = get_registry().get(event_type)
            typed_attrs = spec.attrs_model.model_validate(attrs)
            event = JournalEvent(
                type=event_type,
                schema_version=schema_version,
                occurred_at=occurred_at,
                actor_kind=ActorKind(actor_kind),
                actor_id=actor_id,
                device_id=device_id,
                target_kind=ActorKind(target_kind),
                target_id=target_id,
                outcome=Outcome(outcome),
                attention=attention,
                intensity=intensity,
                record_ref=RecordRef.model_validate(record_ref) if record_ref else None,
                attrs=typed_attrs,
                trace_id=trace_id,
                duration_ms=duration_ms,
            )
            narration = await narrate(event)
        except Exception as exc:
            # Never expected in real wiring (the Hello registry-digest check
            # guarantees a shared registry) — but a poisoned single row must
            # not wedge fan-out forever behind it (AD-9: never block on a
            # hole). Logged loudly, watermark still advances past it.
            log.gateway.error(
                "[ipc] gateway link: journal row could not be narrated — "
                "skipped, watermark still advances",
                exc_info=exc,
                extra={"_fields": {
                    "cursor": cursor, "event_id": event_id, "event_type": event_type,
                }},
            )
            self._last_delivered_cursor = cursor
            return
        self._last_delivered_cursor = cursor
        # Story 3.6 — the two needs_you-specific Telegram delivery hooks,
        # riding this SAME journal fan-out path (every committed row, live
        # push or reconnect catch-up alike) so they see every resolution
        # regardless of which process/surface produced it. Best-effort: never
        # allowed to raise into the fan-out loop below.
        if event_type == "needs_you.opened":
            await self._deliver_needs_you_opened(event, narration)
        elif event_type == "needs_you.resolved":
            await self._edit_needs_you_resolved(event, narration)
        if self._event_bus is not None:
            # `cursor` is a global, ever-growing counter — never a per-turn
            # step count — so it must not be emitted as `step_index`/
            # `total_steps` directly (see `__init__`'s comment on
            # `_progress_step_index`). Wrap a small local counter at the same
            # nominal width `pipeline/progress/emitter.py` uses for the
            # mono-mode path, so `PipelineStrip`'s bounded "train" contract
            # (`i < step_index` over `range(total_steps)`) still holds.
            self._progress_step_index = (self._progress_step_index % _NOMINAL_TOTAL_STEPS) + 1
            self._event_bus.emit(
                "pipeline_step_changed",
                {
                    "step_name": narration.full,
                    "step_index": self._progress_step_index,
                    "total_steps": _NOMINAL_TOTAL_STEPS,
                },
            )
        # 4. EXIT
        log.gateway.debug(
            "[ipc] gateway link: _deliver_journal_row: exit — delivered",
            extra={"_fields": {"cursor": cursor, "event_id": event_id}},
        )

    async def _deliver_needs_you_opened(
        self, event: JournalEvent, narration: NarrationResult,
    ) -> None:
        """Push a newly-opened `incident`/`alert` item to Telegram (Story 3.6).

        `approval`/`question` items are excluded here — they are already
        delivered by their own live prompters (a turn is BLOCKED waiting for
        them), and delivering them again here would double-send. Best-effort:
        a missing notifier/adapter, an unresolved owner chat, or a send
        failure is logged and swallowed — the item stays open until its own
        expiry (I/O matrix), this is only its notification.
        """
        from stackowl.journal.needs_you import NeedsYouOpenedAttrs

        attrs = cast(NeedsYouOpenedAttrs, event.attrs)
        if attrs.kind not in (NeedsYouKind.INCIDENT, NeedsYouKind.ALERT):
            return
        if self._needs_you_notifier is None:
            log.gateway.debug(
                "[ipc] gateway link: needs_you.opened for incident/alert — no "
                "notifier wired, skipped",
                extra={"_fields": {"item_id": attrs.item_id, "kind": attrs.kind.value}},
            )
            return
        try:
            from stackowl.config.settings import cached_settings
            from stackowl.notifications.recipient import resolve_owner_addresses

            addresses = resolve_owner_addresses(cached_settings(), ["telegram"])
            raw_chat_id = addresses.get("telegram")
            if raw_chat_id is None:
                log.gateway.warning(
                    "[ipc] gateway link: needs_you.opened for incident/alert — "
                    "no owner Telegram chat resolved, skipped",
                    extra={"_fields": {"item_id": attrs.item_id, "kind": attrs.kind.value}},
                )
                return
            await self._needs_you_notifier.deliver_opened(
                attrs.item_id, attrs.kind, int(raw_chat_id), narration.full,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, must never block fan-out
            log.gateway.error(
                "[ipc] gateway link: needs_you.opened incident/alert push failed",
                exc_info=exc,
                extra={"_fields": {"item_id": attrs.item_id, "kind": attrs.kind.value}},
            )

    async def _edit_needs_you_resolved(
        self, event: JournalEvent, narration: NarrationResult,
    ) -> None:
        """Cross-surface edit-on-resolve (Story 3.6): whatever Telegram message
        the registry has for this item id (registered by ANY surface — an
        approval's own live prompter, the split-mode clarify text delivery,
        or the incident/alert pusher above) is edited to show the outcome and
        loses its keyboard, regardless of which surface resolved the item.

        A resolving TAP already edited its own message locally and popped the
        registry first (`TelegramConsentPrompter.handle_callback`), so this
        finds nothing registered for that item and is a no-op — it only fires
        for a resolution this process did NOT already show locally (a local
        timeout, the periodic expiry sweep, a future non-Telegram surface).

        Best-effort — matches `_edit_to_decision`'s own fail-open convention:
        a missing adapter or an edit failure is logged and swallowed.
        """
        from stackowl.channels.telegram.needs_you_registry import (
            get_registry as get_needs_you_registry,
        )
        from stackowl.journal.needs_you import NeedsYouResolvedAttrs

        attrs = cast(NeedsYouResolvedAttrs, event.attrs)
        found = get_needs_you_registry().forget(attrs.item_id)
        if found is None:
            return
        chat_id, message_id = found
        adapter = self._adapters.get("telegram")
        edit = getattr(adapter, "edit_message", None)
        if edit is None:
            log.gateway.debug(
                "[ipc] gateway link: needs_you.resolved — no Telegram adapter "
                "with edit_message, skipped",
                extra={"_fields": {"item_id": attrs.item_id}},
            )
            return
        symbol = "✅" if event.outcome is Outcome.OK else "⏳"
        text = f"{symbol} {narration.full}"
        try:
            await edit(chat_id, message_id, text, reply_markup=None)
        except Exception as exc:  # noqa: BLE001 — fail-open, matches _edit_to_decision
            log.gateway.error(
                "[ipc] gateway link: needs_you.resolved cross-surface edit failed",
                exc_info=exc,
                extra={"_fields": {
                    "item_id": attrs.item_id, "chat_id": chat_id, "message_id": message_id,
                }},
            )

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
            sent = await adapter.send_text(text)
            # Story 3.6 -- register the sent message against the durable
            # `question` needs_you item (when one was opened, blocking mode
            # only), so the generic cross-surface `needs_you.resolved` hook
            # (`_deliver_journal_row`, below) can find and edit it later.
            # Defensive getattr throughout: `_Adapter`'s own protocol
            # declares `send_text` -> None, but the real adapters return the
            # sent Message; a missing/absent identity simply skips
            # registration (the split-mode text delivery itself is
            # unaffected either way).
            if frame.needs_you_item_id is not None:
                message_id = getattr(sent, "message_id", None)
                chat_id = getattr(sent, "chat_id", None)
                if message_id is not None and chat_id is not None:
                    from stackowl.channels.telegram.needs_you_registry import (
                        get_registry as get_needs_you_registry,
                    )

                    get_needs_you_registry().remember(
                        frame.needs_you_item_id, chat_id=chat_id, message_id=message_id,
                    )

    async def _handle_consent(self, frame: ConsentRequestFrame) -> None:
        """Rebuild the request from the wire and route it to the real prompter.

        Story 3.5 — a frame that crosses the link with no ``reply_target`` is
        refused OUTRIGHT: ``self._consent_router`` is never invoked, so it can
        never fall back to guessing a chat id from ``session_key`` (the class
        of bug ``ConsentRequest.reply_target``'s own docstring names). Only a
        frame that carries a real ``reply_target`` reaches the router.
        """
        from stackowl.tools.consent import ConsentRequest, ConsentScope

        # 1. ENTRY
        log.gateway.debug(
            "[ipc] gateway link: _handle_consent: entry",
            extra={"_fields": {
                "consent_id": frame.consent_id, "channel": frame.channel,
                "tool_name": frame.tool_name,
            }},
        )
        # 2. DECISION — no reply_target on the wire ⇒ refuse, never route to a guess.
        if frame.reply_target is None:
            log.gateway.error(
                "[ipc] gateway link: consent frame missing reply_target — denying, "
                "never routed to a guessed destination",
                extra={"_fields": {
                    "consent_id": frame.consent_id, "channel": frame.channel,
                    "tool_name": frame.tool_name,
                }},
            )
            scope = ConsentScope.DENY
        else:
            scope = ConsentScope.DENY
            if self._consent_router is not None:
                try:
                    # 3. STEP — rebuild the in-process request and route it.
                    req = ConsentRequest(
                        tool_name=frame.tool_name,
                        channel=frame.channel,
                        session_key=frame.session_key,
                        reply_target=frame.reply_target,
                        item_id=frame.item_id,
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
        # 4. EXIT
        log.gateway.debug(
            "[ipc] gateway link: _handle_consent: exit",
            extra={"_fields": {"consent_id": frame.consent_id, "scope": scope.value}},
        )

    async def _handle_send_ephemeral(self, frame: SendEphemeralFrame) -> None:
        """Send a muted probe on the real adapter and report its message_id back.

        Spawned (never inline) so a slow/failing send can't stall the frame
        loop's other traffic. ``message_id`` stays ``-1`` (sentinel — nothing
        deletable) on a missing adapter or send failure; the core's
        ``send_ephemeral`` caller already tolerates that as a fallback.
        """
        adapter = self._adapters.get(frame.channel)
        message_id = -1
        if adapter is None:
            log.gateway.error(
                "[ipc] gateway link: ephemeral send for unregistered channel",
                extra={"_fields": {"channel": frame.channel, "request_id": frame.request_id}},
            )
        else:
            try:
                message_id = await adapter.send_ephemeral(frame.target, frame.text)
            except Exception as exc:  # noqa: BLE001 — report sentinel id, never crash the link
                log.gateway.error(
                    "[ipc] gateway link: ephemeral send failed",
                    exc_info=exc,
                    extra={"_fields": {"channel": frame.channel, "request_id": frame.request_id}},
                )
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.send(
                    EphemeralSentFrame(
                        request_id=frame.request_id, message_id=message_id
                    )
                )
