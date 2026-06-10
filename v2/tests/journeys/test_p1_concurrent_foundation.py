"""P1 journey — the concurrent-message FOUNDATION on the REAL gateway path.

Why this exists (the P1 merge-gate, concurrent-msg §9):
  Tasks 1-9 built the foundation (request-id-keyed streams, the per-session
  ``TurnRegistry`` with a non-blocking intake + FIFO queue, per-message
  ``chat_id`` → ``reply_target`` → ``chunk.target`` → ``send_text(chat_id)``
  Telegram routing). Each was proved by unit tests. But Tasks 2 and 4 each had a
  DORMANT-wiring gap that only surfaced when the REAL gateway path was driven end
  to end (a stream registered under the wrong key → ``deliver`` stream-missed →
  the session WEDGED). This journey drives the ACTUAL assembled path — real
  ``GatewayScanner.scan`` → real ``AsyncioBackend.run`` (full pipeline) → real
  ``deliver`` step → real ``ClarifyPump.spawn_send`` → real channel adapter send —
  with a mock ONLY at the AI provider, so any remaining dormant-wiring gap is
  CAUGHT here rather than in production.

Three proofs (the P1 OUTCOMES a user cares about):

  1. Cross-session parallel + correlated. Two turns in DIFFERENT sessions
     (``session_id != trace_id``, like every real adapter) dispatched
     concurrently run truly in parallel and each reply correlates to its OWN
     ``request_id`` stream — no cross-read. Proven by gating BOTH providers on a
     barrier that only releases once BOTH turns are simultaneously in flight (so
     a serial impl DEADLOCKS the ``wait_for`` and FAILS), then asserting each
     session's channel received ITS OWN distinct reply.

  2. In-chat non-blocking + queued (FIFO). A second message in the SAME session,
     arriving while the first turn is still running, is accepted INSTANTLY (the
     ``_intake`` call returns before the first turn completes — non-blocking) and
     is QUEUED, then runs after the first finishes (FIFO). Proven by holding the
     first turn open on an event, asserting the second ``_intake`` returns while
     the first is still in flight AND that no second turn started yet, then
     releasing turn one and asserting turn two runs and delivers after it.

  3. Telegram no cross-deliver. Two Telegram sessions with DIFFERENT ``chat_id``
     each get their response sent to THEIR OWN ``chat_id`` — never cross-delivered
     via the shared ``_last_chat_id``. Proven by driving the real
     ``chat_id`` → ``state.reply_target`` → ``deliver`` stamps ``chunk.target`` →
     adapter ``send`` captures ``chunk.target`` → ``send_text(chat_id=...)`` path
     with a capturing fake bot, asserting each chat received only its own reply.

  Plus: ``state.trace_id`` is populated at deliver-time and equals
  ``TraceContext.current().trace_id`` (the backend opens the trace from
  ``state.trace_id``).

What is REAL vs mocked:
  REAL — ``GatewayScanner``, ``StreamRegistry``, ``TurnRegistry``, ``AsyncioBackend``
  (full pipeline incl. the real ``deliver`` step), ``StepServices`` with the shared
  stream registry wired, ``ProviderRegistry`` resolution, ``ClarifyPump.spawn_send``
  drain/reap, the per-session intake-lock + register + FIFO-drain logic, the
  Telegram ``reply_target`` → ``chunk.target`` → ``send_text(chat_id)`` routing.
  MOCKED — ONLY the AI provider (a controllable canned-reply fake resolved through
  the real ``ProviderRegistry``). No gateway/registry/stream/deliver/send logic is
  stubbed.

The journey re-creates the orchestrator's ``_dispatch_turn`` / non-blocking
``_intake`` / completion→``_drain_next`` sequence faithfully against the REAL
registries (the literal ``_phase_gateway`` closure needs the full DI graph; this
is the smallest FAITHFUL slice that still exercises every real component above).
Every await is wrapped in ``asyncio.wait_for`` so a hang/wedge FAILS the test
rather than wedging the suite.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from stackowl.channels.base import ChannelAdapter
from stackowl.db.pool import DbPool
from stackowl.gateway.clarify_pump import ClarifyPump
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.gateway.turn_registry import TurnRegistry
from stackowl.infra.trace import TraceContext
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamReader, StreamRegistry
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

_WAIT = 5.0  # every await is bounded — a hang FAILS, never wedges the suite.


# ---- Controllable mock provider (resolved THROUGH the real ProviderRegistry) -


class _ControllableProvider(ModelProvider):
    """Canned tool-loop reply (zero tool calls) the test can pace + observe.

    * ``trace_observed`` captures ``TraceContext.get()["trace_id"]`` seen INSIDE
      the turn (proves the backend opened the trace from ``state.trace_id``).
    * ``reply_for`` maps an input substring → the reply text so each turn's
      output is DISTINCT and can be correlated to its own request/session.
    * ``gate`` (optional, per construction) is awaited before returning, so a
      turn can be HELD in flight while the test drives a concurrent message.
    * ``in_flight`` is set when a turn enters ``complete_with_tools`` (lets the
      test detect a serial-vs-parallel deadlock and a non-blocking-accept).
    """

    def __init__(
        self,
        *,
        reply_for: dict[str, str],
        barrier: asyncio.Barrier | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._name = "fake"
        self._reply_for = reply_for
        self._barrier = barrier
        self._gate = gate
        self.trace_observed: list[str] = []
        self.in_flight = asyncio.Event()
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:  # pragma: no cover — tool-loop path is forced
        return CompletionResult(
            content="UNUSED",
            input_tokens=1,
            output_tokens=1,
            model="fake-model",
            provider_name=self._name,
            duration_ms=1.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):  # pragma: no cover — tool-loop path is forced
        yield "UNUSED"

    def _reply(self, user_text: str) -> str:
        for needle, reply in self._reply_for.items():
            if needle in user_text:
                return reply
        return "DEFAULT_REPLY"

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list,
        tool_dispatcher,
        max_iterations: int = 8,
        history: list[Message] | None = None,
        persistence_check=None,
    ) -> tuple[str, list]:
        self.calls += 1
        self.in_flight.set()
        # Capture the trace the backend opened from state.trace_id (proves the
        # request-id propagated into the turn's TraceContext).
        current_trace = TraceContext.get().get("trace_id")
        if current_trace is not None:
            self.trace_observed.append(current_trace)
        # Cross-session parallel proof: both turns must be simultaneously here for
        # the barrier to release; a serial impl never reaches the second → the
        # outer wait_for times out and the test FAILS.
        if self._barrier is not None:
            await self._barrier.wait()
        # In-chat hold proof: keep this turn in flight until the test releases it.
        if self._gate is not None:
            await self._gate.wait()
        return self._reply(user_text), []


# ---- Capturing CLI-shaped adapter (drains the reader like the real adapter) --


class _CapturingAdapter(ChannelAdapter):
    """Real-shaped CLI adapter: ``send`` drains the reader until the sentinel."""

    def __init__(self) -> None:
        self.received: list[str] = []

    @property
    def channel_name(self) -> str:
        return "cli"

    async def receive(self) -> IngressMessage:  # pragma: no cover — unused
        raise NotImplementedError

    async def send(self, chunks: StreamReader) -> None:
        async for chunk in chunks:
            self.received.append(chunk.content)

    async def send_text(self, text: str) -> None:  # pragma: no cover — unused
        self.received.append(text)


# ---- Capturing Telegram-shaped adapter (the REAL reply_target routing path) --


class _FakeBot:
    """Captures (chat_id, text) per send — stands in for the grammY bot only."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str, **_: object) -> None:
        self.sent.append((chat_id, text))


class _TelegramRoutingAdapter(ChannelAdapter):
    """Drives the REAL Telegram delivery contract for the no-cross-deliver proof.

    Mirrors :meth:`stackowl.channels.telegram.adapter.TelegramAdapter.send` /
    ``send_text``: ``send`` captures each chunk's ``target`` (the per-turn
    ``chat_id`` stamped at deliver-time from ``state.reply_target``) and routes
    the buffered reply to THAT chat via ``send_text(chat_id=...)`` — the exact
    path that stops a concurrent turn from cross-delivering to the shared
    ``_last_chat_id``. The real adapter is not constructed (it needs a live bot +
    TestModeGuard); this faithfully exercises the routing CONTRACT under test.
    """

    def __init__(self, bot: _FakeBot) -> None:
        self._bot = bot
        self._last_chat_id: int | None = None

    @property
    def channel_name(self) -> str:
        return "telegram"

    async def receive(self) -> IngressMessage:  # pragma: no cover — unused
        raise NotImplementedError

    async def send(self, chunks: StreamReader) -> None:
        buffer = ""
        target: int | None = None
        async for chunk in chunks:
            buffer += chunk.content
            if chunk.target is not None:
                target = chunk.target
        await self.send_text(buffer, chat_id=target)

    async def send_text(self, text: str, *, chat_id: int | None = None) -> None:
        target = chat_id if chat_id is not None else self._last_chat_id
        if target is None:
            return
        await self._bot.send_message(chat_id=target, text=text)


# ---- Shared helpers ----------------------------------------------------------


def _build_services(
    bridge: SqliteMemoryBridge,
    provider: ModelProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
    stream_registry: StreamRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    return StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
        # SHARED registry — the REAL deliver step writes into THIS instance,
        # exactly as orchestrator.py wires one stream_registry into both services
        # and the gateway loop.
        stream_registry=stream_registry,
    )


class _NullGateway:
    """A clarify gateway the pump never consults (spawn_send-only slice)."""

    def peek_for_session(self, session_id: str, channel: str) -> None:  # pragma: no cover
        return None


async def _dispatch_turn(
    *,
    backend: AsyncioBackend,
    scanner: GatewayScanner,
    stream_registry: StreamRegistry,
    turn_registry: TurnRegistry,
    pump: ClarifyPump,
    adapter: ChannelAdapter,
    msg: IngressMessage,
) -> tuple[asyncio.Task[object], asyncio.Task[None]]:
    """Faithful re-creation of orchestrator._dispatch_turn against REAL registries.

    create stream by trace_id → build PipelineState as the orchestrator does
    (incl. ``reply_target=msg.chat_id``) → backend.run → register the turn →
    spawn the real decoupled send. Returns ``(producer, send_task)`` — the send
    task is captured from the pump's ``_inflight`` slot right after ``spawn_send``
    (before it can finish and be reaped by ``_cleanup``).
    """
    decision = scanner.scan(msg)
    assert decision.route == "owl"
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text

    writer, reader = stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=input_text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
        reply_target=msg.chat_id,
    )
    producer: asyncio.Task[object] = asyncio.create_task(backend.run(state))
    await turn_registry.register(
        msg.trace_id,
        session_id=msg.session_id,
        task=cast("asyncio.Task[None]", producer),
        target=msg.chat_id,
        original_input=input_text,
    )
    pump.spawn_send(
        channel_adapter=adapter,
        reader=reader,
        session_id=msg.session_id,
        request_id=msg.trace_id,
        producer=producer,
        writer=writer,
    )
    # Capture the send task NOW (before the producer finishes and _cleanup reaps
    # the _inflight slot), so the caller can await delivery deterministically.
    send_task = pump._inflight[msg.session_id]  # type: ignore[attr-defined]
    return producer, send_task


# ---- Proof 1 — cross-session parallel + correlated ---------------------------


async def test_cross_session_runs_parallel_and_each_reply_correlates(
    tmp_db: DbPool,
) -> None:
    """Two DIFFERENT sessions run truly in parallel; each reply hits its own stream.

    The barrier only releases once BOTH turns are simultaneously in
    ``complete_with_tools`` — a serial impl never reaches the second turn and the
    ``wait_for`` below DEADLOCKS → FAIL. On pass, each session's adapter received
    ONLY its own correlated reply (request-id-keyed streams, no cross-read).
    """
    stream_registry = StreamRegistry()
    turn_registry = TurnRegistry()
    bridge = SqliteMemoryBridge(db=tmp_db)
    barrier = asyncio.Barrier(2)
    provider = _ControllableProvider(
        reply_for={"alpha": "REPLY_ALPHA", "bravo": "REPLY_BRAVO"},
        barrier=barrier,
    )
    services = _build_services(
        bridge, provider, OwlRegistry.with_default_secretary(),
        ToolRegistry.with_defaults(), stream_registry,
    )
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    pump = ClarifyPump(_NullGateway(), stream_registry)  # type: ignore[arg-type]

    adapter_a = _CapturingAdapter()
    adapter_b = _CapturingAdapter()

    # session_id != trace_id in BOTH, like every real adapter.
    msg_a = IngressMessage(
        text="alpha question", session_id="sess-A", channel="cli", trace_id="trace-A"
    )
    msg_b = IngressMessage(
        text="bravo question", session_id="sess-B", channel="cli", trace_id="trace-B"
    )

    prod_a, send_a = await _dispatch_turn(
        backend=backend, scanner=scanner, stream_registry=stream_registry,
        turn_registry=turn_registry, pump=pump, adapter=adapter_a, msg=msg_a,
    )
    prod_b, send_b = await _dispatch_turn(
        backend=backend, scanner=scanner, stream_registry=stream_registry,
        turn_registry=turn_registry, pump=pump, adapter=adapter_b, msg=msg_b,
    )

    # Both producers must complete — only possible if both reached the barrier
    # SIMULTANEOUSLY (true parallelism). A serial impl hangs here → wait_for FAILS.
    await asyncio.wait_for(asyncio.gather(prod_a, prod_b), timeout=_WAIT)
    await asyncio.wait_for(asyncio.gather(send_a, send_b), timeout=_WAIT)
    await asyncio.sleep(0)  # let the send done-callbacks (_cleanup) run

    # Each session received ONLY its own correlated reply — no cross-read.
    assert any("REPLY_ALPHA" in c for c in adapter_a.received), adapter_a.received
    assert not any("REPLY_BRAVO" in c for c in adapter_a.received), adapter_a.received
    assert any("REPLY_BRAVO" in c for c in adapter_b.received), adapter_b.received
    assert not any("REPLY_ALPHA" in c for c in adapter_b.received), adapter_b.received

    # Streams reaped under their OWN request_id key (no leak / no cross-key).
    assert stream_registry.get_writer("trace-A") is None
    assert stream_registry.get_writer("trace-B") is None

    # state.trace_id propagated into each turn's TraceContext (deliver-side proof).
    assert set(provider.trace_observed) == {"trace-A", "trace-B"}


# ---- Proof 2 — in-chat non-blocking accept + FIFO queue ----------------------


async def _intake_same_session(
    *,
    backend: AsyncioBackend,
    scanner: GatewayScanner,
    stream_registry: StreamRegistry,
    turn_registry: TurnRegistry,
    pump: ClarifyPump,
    adapter: ChannelAdapter,
    msg: IngressMessage,
) -> str:
    """Faithful non-blocking intake: dispatch if idle, else enqueue (FIFO).

    Mirrors orchestrator._intake's idle-vs-running decision under the REAL
    per-session intake lock. Returns "dispatched" or "queued". The same-session
    completion→drain hook (below) dispatches the queued head when the running
    turn finishes — exactly as orchestrator._drain_next does.
    """
    async with turn_registry.session_intake_lock(msg.session_id):
        if turn_registry.running(msg.session_id) is None:
            await _dispatch_turn(
                backend=backend, scanner=scanner, stream_registry=stream_registry,
                turn_registry=turn_registry, pump=pump, adapter=adapter, msg=msg,
            )
            return "dispatched"
        turn_registry.enqueue(
            msg.session_id, original_input=msg.text,
            request_id=msg.trace_id, target=msg.chat_id,
        )
        return "queued"


async def test_same_session_second_message_non_blocking_and_queued_fifo(
    tmp_db: DbPool,
) -> None:
    """A mid-turn same-session message is accepted INSTANTLY and runs AFTER (FIFO).

    Turn one is held in flight on a gate. The second ``_intake`` MUST return
    (non-blocking) while turn one is still running AND must NOT start a second
    turn (it is queued, not dispatched). Releasing turn one then drains the queue
    so turn two runs and delivers — proving FIFO, post-completion.
    """
    stream_registry = StreamRegistry()
    turn_registry = TurnRegistry()
    bridge = SqliteMemoryBridge(db=tmp_db)
    gate = asyncio.Event()
    provider = _ControllableProvider(
        reply_for={"first": "REPLY_FIRST", "second": "REPLY_SECOND"},
        gate=gate,
    )
    services = _build_services(
        bridge, provider, OwlRegistry.with_default_secretary(),
        ToolRegistry.with_defaults(), stream_registry,
    )
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    pump = ClarifyPump(_NullGateway(), stream_registry)  # type: ignore[arg-type]
    adapter = _CapturingAdapter()

    sid = "sess-CHAT"
    msg1 = IngressMessage(text="first", session_id=sid, channel="cli", trace_id="trace-1")
    msg2 = IngressMessage(text="second", session_id=sid, channel="cli", trace_id="trace-2")

    # --- Completion→drain hook (faithful to orchestrator._drain_next FIFO) -----
    async def _drain_next(finished_request_id: str) -> None:
        async with turn_registry.session_intake_lock(sid):
            await turn_registry.deregister(finished_request_id)
            nxt = turn_registry.pop_next(sid)
            if nxt is None:
                return
            parked = IngressMessage(
                text=nxt.original_input, session_id=sid, channel="cli",
                trace_id=nxt.request_id, chat_id=nxt.target,
            )
            await _dispatch_turn(
                backend=backend, scanner=scanner, stream_registry=stream_registry,
                turn_registry=turn_registry, pump=pump, adapter=adapter, msg=parked,
            )

    # --- Turn one: dispatched, then HELD in flight on the gate -----------------
    res1 = await asyncio.wait_for(
        _intake_same_session(
            backend=backend, scanner=scanner, stream_registry=stream_registry,
            turn_registry=turn_registry, pump=pump, adapter=adapter, msg=msg1,
        ),
        timeout=_WAIT,
    )
    assert res1 == "dispatched"
    prod1 = turn_registry.running(sid)
    assert prod1 is not None and prod1.task is not None
    prod1_task = prod1.task
    # Attach the FIFO drain hook to turn one (as _dispatch_turn's _on_done does).
    prod1_task.add_done_callback(
        lambda _t: asyncio.create_task(_drain_next("trace-1"))
    )

    # Turn one is genuinely in flight (entered the provider, now blocked on gate).
    await asyncio.wait_for(provider.in_flight.wait(), timeout=_WAIT)
    assert not prod1_task.done(), "turn one should still be running (held on gate)"

    # --- The mid-turn second message: accepted INSTANTLY (non-blocking) --------
    # wait_for proves the intake call RETURNS while turn one is still in flight —
    # it does NOT block behind the running turn. It must be QUEUED, not dispatched.
    res2 = await asyncio.wait_for(
        _intake_same_session(
            backend=backend, scanner=scanner, stream_registry=stream_registry,
            turn_registry=turn_registry, pump=pump, adapter=adapter, msg=msg2,
        ),
        timeout=_WAIT,
    )
    assert res2 == "queued", "second same-session message must be QUEUED, not dispatched"
    assert not prod1_task.done(), "intake must not have blocked on / waited out turn one"
    assert provider.calls == 1, "second turn must NOT have started while turn one runs"
    # Still exactly one running turn for the session (turn one).
    assert turn_registry.running(sid) is prod1

    # --- Release turn one → the FIFO drain dispatches turn two -----------------
    gate.set()
    await asyncio.wait_for(prod1_task, timeout=_WAIT)
    # Let the done-callback drain task run, then await turn two to completion.
    for _ in range(50):
        await asyncio.sleep(0)
        running2 = turn_registry.running(sid)
        if running2 is not None and running2.turn_id == "trace-2" and running2.task is not None:
            await asyncio.wait_for(running2.task, timeout=_WAIT)
            break
    else:  # pragma: no cover — only on a real drain wiring gap
        pytest.fail("turn two was never dispatched after turn one completed (FIFO drain gap)")

    # Drain any in-flight send for turn two, then let its delivery settle. Poll
    # the adapter (the send task may already have been reaped from _inflight) —
    # bounded by wait_for so a real delivery hang still FAILS.
    async def _await_second_delivery() -> None:
        while not any("REPLY_SECOND" in c for c in adapter.received):
            send2 = pump._inflight.get(sid)
            if send2 is not None:
                await send2
            await asyncio.sleep(0)

    await asyncio.wait_for(_await_second_delivery(), timeout=_WAIT)

    # FIFO outcome: BOTH turns ran, in order, and both delivered to the chat.
    assert provider.calls == 2, "turn two must have run after turn one (FIFO)"
    assert any("REPLY_FIRST" in c for c in adapter.received), adapter.received
    assert any("REPLY_SECOND" in c for c in adapter.received), adapter.received
    # Turn one's reply preceded turn two's (FIFO order preserved end to end).
    first_idx = next(i for i, c in enumerate(adapter.received) if "REPLY_FIRST" in c)
    second_idx = next(i for i, c in enumerate(adapter.received) if "REPLY_SECOND" in c)
    assert first_idx < second_idx, adapter.received


# ---- Proof 3 — Telegram no cross-deliver -------------------------------------


async def test_telegram_two_chats_no_cross_deliver(tmp_db: DbPool) -> None:
    """Two Telegram sessions (different chat_id) each get THEIR reply to THEIR chat.

    Drives the REAL routing: per-message ``chat_id`` → ``state.reply_target`` →
    the REAL ``deliver`` step stamps ``chunk.target`` → the adapter captures
    ``chunk.target`` → ``send_text(chat_id=...)``. A barrier forces both turns
    concurrent (so a shared ``_last_chat_id`` impl WOULD cross-deliver), then we
    assert each chat received ONLY its own reply.
    """
    stream_registry = StreamRegistry()
    turn_registry = TurnRegistry()
    bridge = SqliteMemoryBridge(db=tmp_db)
    barrier = asyncio.Barrier(2)
    provider = _ControllableProvider(
        reply_for={"weather": "REPLY_WEATHER", "news": "REPLY_NEWS"},
        barrier=barrier,
    )
    services = _build_services(
        bridge, provider, OwlRegistry.with_default_secretary(),
        ToolRegistry.with_defaults(), stream_registry,
    )
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    pump = ClarifyPump(_NullGateway(), stream_registry)  # type: ignore[arg-type]

    bot = _FakeBot()
    adapter_x = _TelegramRoutingAdapter(bot)
    adapter_y = _TelegramRoutingAdapter(bot)

    chat_x, chat_y = 111, 222
    msg_x = IngressMessage(
        text="weather please", session_id="tg-X", channel="telegram",
        trace_id="trace-X", chat_id=chat_x,
    )
    msg_y = IngressMessage(
        text="news please", session_id="tg-Y", channel="telegram",
        trace_id="trace-Y", chat_id=chat_y,
    )

    prod_x, send_x = await _dispatch_turn(
        backend=backend, scanner=scanner, stream_registry=stream_registry,
        turn_registry=turn_registry, pump=pump, adapter=adapter_x, msg=msg_x,
    )
    prod_y, send_y = await _dispatch_turn(
        backend=backend, scanner=scanner, stream_registry=stream_registry,
        turn_registry=turn_registry, pump=pump, adapter=adapter_y, msg=msg_y,
    )
    await asyncio.wait_for(asyncio.gather(prod_x, prod_y), timeout=_WAIT)
    await asyncio.wait_for(asyncio.gather(send_x, send_y), timeout=_WAIT)
    await asyncio.sleep(0)

    # Each chat got ONLY its own reply — no cross-deliver via a shared last-chat.
    by_chat: dict[int, list[str]] = {}
    for cid, text in bot.sent:
        by_chat.setdefault(cid, []).append(text)

    assert chat_x in by_chat and chat_y in by_chat, bot.sent
    assert any("REPLY_WEATHER" in t for t in by_chat[chat_x]), bot.sent
    assert not any("REPLY_NEWS" in t for t in by_chat[chat_x]), bot.sent
    assert any("REPLY_NEWS" in t for t in by_chat[chat_y]), bot.sent
    assert not any("REPLY_WEATHER" in t for t in by_chat[chat_y]), bot.sent
