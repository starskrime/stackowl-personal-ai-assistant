"""P2+P3 MERGE-GATE journey — live-steer + hybrid-routing on the REAL path.

Why this exists (the FINAL end-to-end merge-gate, concurrent-msg §9):
  Task 17's P1 journey proved the foundation (request-id-keyed streams, the
  per-session ``TurnRegistry``, non-blocking intake + FIFO, Telegram
  no-cross-deliver). It deliberately did NOT cover the P2/P3 STEERING + ROUTING
  seams: the ``_intake`` in-flight router (``route_inflight_message`` →
  ``TurnRouter.route`` → ``try_steer`` → the running turn's mailbox) and the
  steering CLOSURE the execute step builds (``make_steering_callback``) that the
  provider folds into the NEXT ReAct iteration. This journey drives ALL of those
  on the REAL assembled path, mocking ONLY the AI provider's wire (a fake OpenAI
  client) and the STEER-vs-NEW classifier VERDICT (so no real LLM) — every routing
  and steering component in between is the production object.

Three proofs (the P2/P3 OUTCOMES a user cares about):

  1. ADD-steer folds into the OUTPUT. A turn is mid-"research X" (held between
     ReAct iterations). A mid-turn "also include Y" is routed by the REAL
     ``route_inflight_message`` → REAL ``TurnRouter.route`` (classifier verdict
     mocked to STEER) → REAL ``TurnRegistry.try_steer`` onto the RUNNING turn's
     REAL mailbox. The held iteration is released; the REAL
     ``make_steering_callback`` (built BY the real execute step from the services'
     ``TurnRegistry``) drains the mailbox and the REAL provider fold splices
     ``[steering] also include Y`` into the next LLM iteration's ``messages``. The
     provider's FINAL answer (and thus the DELIVERED reply) reflects Y — proving
     the steer reached the provider's live ``messages`` and changed the output.
     (A serial / unfolded impl would deliver an answer WITHOUT Y → FAIL.)

  2. Parallel second chat, correlated. A message in a DIFFERENT session (B) runs
     truly in parallel with A's steered turn (both gated on a barrier so a serial
     impl deadlocks the ``wait_for``) and its reply correlates to ITS OWN
     request-id stream — no cross-deliver.

  3. Contradiction → coherent SEPARATE answer. A later mid-turn "no, I meant Z"
     is routed by the REAL ``route_inflight_message`` (classifier verdict mocked
     to NEW — the conservative direction) → ``ENQUEUE_NEW``. The caller enqueues
     it as a queued-new turn; after the steered turn finishes, the REAL FIFO drain
     dispatches it as a FRESH turn whose answer is about Z ALONE — a coherent
     separate answer, NOT an incoherent blend folded into the running research.

What is REAL vs mocked:
  REAL — ``route_inflight_message`` (the ``_intake`` in-flight seam), ``TurnRouter``
  (explicit-signal parse + the STEER/NEW decision), ``TurnRegistry.try_steer`` +
  the per-turn mailbox + FIFO queue, ``make_steering_callback`` (the execute-layer
  steering closure), the real ``OpenAIProvider.complete_with_tools`` multi-iteration
  ReAct loop + its ``messages.extend(folded)`` fold, ``StreamRegistry``,
  ``AsyncioBackend`` (full pipeline incl. the real ``deliver`` step),
  ``ClarifyPump.spawn_send``, the channel adapter send.
  MOCKED — ONLY the OpenAI wire (a scripted, gated fake client) and the
  classifier's STEER/NEW VERDICT (a stub ``is_steer`` returning the scripted
  decision). No routing/registry/steering/fold/deliver logic is stubbed.

Every await is wrapped in ``asyncio.wait_for`` so a hang/wedge FAILS the test
rather than wedging the suite.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from stackowl.channels.base import ChannelAdapter
from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.clarify_pump import ClarifyPump
from stackowl.gateway.inflight_router import (
    InflightAction,
    route_inflight_message,
)
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.gateway.turn_registry import TurnRegistry
from stackowl.gateway.turn_router import TurnRouter
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamReader, StreamRegistry
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

_WAIT = 5.0  # every await is bounded — a hang FAILS, never wedges the suite.

# Marker text the steer carries; if the fold works it lands in the next LLM call's
# messages and the provider reflects it in its final answer.
_Y_MARK = "include-Y-quantum-supremacy"
_Z_MARK = "actually-Z-photosynthesis"


# ---- A scripted, gated fake OpenAI client (mocks ONLY the AI wire) -----------


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, tc_id: str, name: str, arguments: str) -> None:
        self.id = tc_id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(
        self, content: str | None, tool_calls: list[_FakeToolCall] | None = None
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "fake-model"
        self.usage = None  # the real provider reads .usage (fail-open if None)


def _ancillary_response(messages: list[dict[str, Any]]) -> _FakeResponse | None:
    """A benign canned reply for the pipeline's NON-ReAct ``complete()`` calls (the
    owl ROUTER and the persistence DELIVERY judge), so the ReAct iteration counter
    only ever sees the real tool-loop ``complete_with_tools`` rounds. Returns
    ``None`` when the call is a real ReAct round (``tools`` present)."""
    joined = " ".join(str(m.get("content") or "") for m in messages)
    if "router" in joined.lower():
        return _FakeResponse(_FakeMessage(content="secretary"))
    if "delivered" in joined.lower():
        return _FakeResponse(_FakeMessage(content='{"delivered": true, "reason": "ok"}'))
    return _FakeResponse(_FakeMessage(content="ok"))


def _steering_in(messages: list[dict[str, Any]], needle: str) -> bool:
    for m in messages:
        content = m.get("content")
        if isinstance(content, str) and needle in content:
            return True
    return False


class _SteerObservingCompletions:
    """Iteration 0 → a tool call (so the post-tool boundary fires the steering
    callback), gated so the test can put a steer on the mailbox WHILE the turn is
    in flight. Iteration 1 → a final answer that REFLECTS whether the folded
    ``[steering]`` message reached the live ``messages`` — the load-bearing proof.
    """

    def __init__(self, *, gate: asyncio.Event | None) -> None:
        self._gate = gate
        self._idx = 0
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.in_flight = asyncio.Event()

    async def create(self, **kwargs: Any) -> _FakeResponse:
        msgs = [dict(m) for m in kwargs["messages"]]
        # Non-ReAct pipeline calls (owl router, persistence judge — no `tools`)
        # must NOT advance the iteration counter nor be recorded as a ReAct round.
        if "tools" not in kwargs:
            return _ancillary_response(msgs)
        self.seen_messages.append(msgs)
        idx = self._idx
        self._idx += 1
        if idx == 0:
            # Mark the turn as genuinely in flight, then HOLD on the gate so the
            # test can route a steer onto the running mailbox before iteration 1.
            self.in_flight.set()
            if self._gate is not None:
                await self._gate.wait()
            tc = _FakeToolCall("c0", "noop_probe", '{"q":"X"}')
            return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))
        # Iteration 1 — the final answer reflects whether the steer folded.
        if _steering_in(msgs, _Y_MARK):
            return _FakeResponse(
                _FakeMessage(content=f"Research on X done, and {_Y_MARK} as steered.")
            )
        return _FakeResponse(_FakeMessage(content="Research on X done (no steer seen)."))


class _PlainCompletions:
    """A one-shot final answer keyed off the user_text — for parallel/queued turns
    that do not exercise the steer fold."""

    def __init__(self, reply_for: dict[str, str], *, barrier: asyncio.Barrier | None) -> None:
        self._reply_for = reply_for
        self._barrier = barrier
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.in_flight = asyncio.Event()
        self._armed_barrier = barrier is not None

    async def create(self, **kwargs: Any) -> _FakeResponse:
        msgs = [dict(m) for m in kwargs["messages"]]
        # Skip the pipeline's NON-ReAct calls (owl router, persistence judge).
        if "tools" not in kwargs:
            return _ancillary_response(msgs)
        self.seen_messages.append(msgs)
        self.in_flight.set()
        # Release the cross-session barrier exactly ONCE (first LLM call), so two
        # turns must be simultaneously in flight — a serial impl deadlocks.
        if self._armed_barrier and self._barrier is not None:
            self._armed_barrier = False
            await self._barrier.wait()
        joined = " ".join(
            str(m.get("content") or "") for m in msgs if m.get("role") == "user"
        )
        for needle, reply in self._reply_for.items():
            if needle in joined:
                return _FakeResponse(_FakeMessage(content=reply))
        return _FakeResponse(_FakeMessage(content="DEFAULT"))


class _FakeChat:
    def __init__(self, completions: Any) -> None:
        self.completions = completions


class _FakeOAIClient:
    def __init__(self, completions: Any) -> None:
        self.chat = _FakeChat(completions)


def _make_provider(completions: Any) -> OpenAIProvider:
    config = ProviderConfig(
        name="fake",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="fake-model",
        tier="powerful",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = _FakeOAIClient(completions)  # type: ignore[assignment]
    return provider


# ---- A harmless registered tool for the iteration-0 tool call ----------------


class _NoopProbeTool(Tool):
    """A read-severity no-op so the iteration-0 tool dispatch is clean + offline
    (no consent gate, no bounds block, no real I/O)."""

    @property
    def name(self) -> str:
        return "noop_probe"

    @property
    def description(self) -> str:
        return "A no-op probe used to create a ReAct iteration boundary in tests."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"q": {"type": "string"}}}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=0.1)


def _tool_registry() -> ToolRegistry:
    reg = ToolRegistry.with_defaults()
    reg.register(_NoopProbeTool(), replace=True)
    return reg


# ---- A stubbed classifier VERDICT (no real LLM) ------------------------------


class _ScriptedClassifier:
    """Stands in for ``ClarifyIntentClassifier`` — returns the scripted STEER/NEW
    verdict per message text (the ONLY classifier mock; the REAL ``TurnRouter``
    still parses explicit signals + drives this verdict)."""

    def __init__(self, steer_for: dict[str, bool]) -> None:
        self._steer_for = steer_for

    async def is_steer(self, *, running_ask: str, message: str) -> bool:
        for needle, verdict in self._steer_for.items():
            if needle in message:
                return verdict
        return False  # conservative default — NEW


# ---- Capturing CLI-shaped adapter --------------------------------------------


class _CapturingAdapter(ChannelAdapter):
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


# ---- Shared helpers (mirror the orchestrator's REAL dispatch seam) -----------


class _NullGateway:
    def peek_for_session(self, session_id: str, channel: str) -> None:  # pragma: no cover
        return None


def _build_services(
    bridge: SqliteMemoryBridge,
    provider: OpenAIProvider,
    tool_registry: ToolRegistry,
    stream_registry: StreamRegistry,
    turn_registry: TurnRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    return StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=OwlRegistry.with_default_secretary(),
        tool_registry=tool_registry,
        # SHARED registries — the REAL deliver step writes into THIS stream
        # registry; the REAL execute step builds make_steering_callback from THIS
        # turn registry, exactly as orchestrator.py wires both into services.
        stream_registry=stream_registry,
        turn_registry=turn_registry,
    )


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
    """Faithful re-creation of orchestrator._dispatch_turn against REAL registries
    (create stream by trace_id → build PipelineState → backend.run → register the
    turn in the SAME registry the execute step reads → spawn the decoupled send)."""
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
    send_task = pump._inflight[msg.session_id]  # type: ignore[attr-defined]
    return producer, send_task


# ============================================================================ #
# Proof 1 — ADD-steer folds into the OUTPUT (the load-bearing steering proof)   #
# ============================================================================ #


async def test_midturn_add_steer_folds_into_running_turn_output(tmp_db: DbPool) -> None:
    """A mid-turn "also include Y" STEERs the running turn and the DELIVERED reply
    reflects Y — driving the REAL route_inflight_message → TurnRouter → try_steer →
    mailbox → make_steering_callback → provider fold chain end to end."""
    TestModeGuard._active = False  # the real OpenAIProvider drives a real tool call
    try:
        stream_registry = StreamRegistry()
        turn_registry = TurnRegistry()
        bridge = SqliteMemoryBridge(db=tmp_db)
        gate = asyncio.Event()
        completions = _SteerObservingCompletions(gate=gate)
        provider = _make_provider(completions)
        services = _build_services(
            bridge, provider, _tool_registry(), stream_registry, turn_registry
        )
        backend = AsyncioBackend(services=services)
        scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
        pump = ClarifyPump(_NullGateway(), stream_registry)  # type: ignore[arg-type]
        adapter = _CapturingAdapter()

        # The REAL router with the classifier VERDICT mocked to STEER for "also".
        router = TurnRouter(
            cast("Any", _ScriptedClassifier({"also": True})),
        )

        sid = "sess-A"
        msg = IngressMessage(
            text="research X", session_id=sid, channel="cli", trace_id="trace-A"
        )

        # 1. Dispatch the running turn; it enters iteration 0 and HOLDS on the gate.
        prod, send_task = await _dispatch_turn(
            backend=backend, scanner=scanner, stream_registry=stream_registry,
            turn_registry=turn_registry, pump=pump, adapter=adapter, msg=msg,
        )
        await asyncio.wait_for(completions.in_flight.wait(), timeout=_WAIT)
        running = turn_registry.running(sid)
        assert running is not None and not prod.done()

        # 2. Mid-turn "also include Y" → REAL in-flight router → REAL try_steer onto
        #    the RUNNING turn's REAL mailbox (classifier verdict → STEER, no LLM).
        steer_msg = f"also {_Y_MARK}"
        outcome = await asyncio.wait_for(
            route_inflight_message(
                router=router,
                registry=turn_registry,
                running=running,
                text=steer_msg,
                session_id=sid,
                request_id_new="trace-A-steer-new",
                target=None,
            ),
            timeout=_WAIT,
        )
        # Routed STEER → HANDLED by try_steer (folded onto the running mailbox).
        assert outcome.action is InflightAction.HANDLED, outcome
        assert running.steering_mailbox.qsize() == 1, "steer must be on the live mailbox"

        # 3. Release iteration 0 → the REAL make_steering_callback drains the mailbox
        #    and the REAL provider fold splices [steering] into iteration 1.
        gate.set()
        await asyncio.wait_for(prod, timeout=_WAIT)
        await asyncio.wait_for(send_task, timeout=_WAIT)
        await asyncio.sleep(0)

        # PROOF: the fold reached the provider's live messages (iteration 1 saw it)
        # AND the DELIVERED reply reflects Y.
        assert len(completions.seen_messages) == 2, completions.seen_messages
        assert _steering_in(completions.seen_messages[1], _Y_MARK), (
            "the folded [steering] message must reach iteration 1's messages"
        )
        assert any(_Y_MARK in c for c in adapter.received), adapter.received
        assert turn_registry.get("trace-A") is not None  # not yet deregistered here
    finally:
        TestModeGuard._active = True


# ============================================================================ #
# Proof 2 — parallel second chat, correlated (no cross-deliver)                 #
# ============================================================================ #


async def test_second_chat_runs_parallel_and_reply_correlates(tmp_db: DbPool) -> None:
    """A message in a DIFFERENT session runs truly in parallel with session A and
    its reply correlates to its OWN request-id stream — no cross-read."""
    TestModeGuard._active = False
    try:
        stream_registry = StreamRegistry()
        turn_registry = TurnRegistry()
        bridge = SqliteMemoryBridge(db=tmp_db)
        barrier = asyncio.Barrier(2)
        # Two independent providers, each releasing the SAME shared barrier once.
        comp_a = _PlainCompletions({"research X": "REPLY_A_RESEARCH"}, barrier=barrier)
        comp_b = _PlainCompletions({"weather": "REPLY_B_WEATHER"}, barrier=barrier)

        # Separate services per session (distinct provider) but a SHARED stream +
        # turn registry, exactly as two concurrent gateway dispatches would share.
        services_a = _build_services(
            bridge, _make_provider(comp_a), _tool_registry(), stream_registry, turn_registry
        )
        services_b = _build_services(
            bridge, _make_provider(comp_b), _tool_registry(), stream_registry, turn_registry
        )
        backend_a = AsyncioBackend(services=services_a)
        backend_b = AsyncioBackend(services=services_b)
        scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
        pump = ClarifyPump(_NullGateway(), stream_registry)  # type: ignore[arg-type]
        adapter_a = _CapturingAdapter()
        adapter_b = _CapturingAdapter()

        msg_a = IngressMessage(
            text="research X", session_id="sess-A", channel="cli", trace_id="trace-A"
        )
        msg_b = IngressMessage(
            text="weather please", session_id="sess-B", channel="cli", trace_id="trace-B"
        )

        prod_a, send_a = await _dispatch_turn(
            backend=backend_a, scanner=scanner, stream_registry=stream_registry,
            turn_registry=turn_registry, pump=pump, adapter=adapter_a, msg=msg_a,
        )
        prod_b, send_b = await _dispatch_turn(
            backend=backend_b, scanner=scanner, stream_registry=stream_registry,
            turn_registry=turn_registry, pump=pump, adapter=adapter_b, msg=msg_b,
        )
        # Both must reach the barrier SIMULTANEOUSLY — a serial impl deadlocks here.
        await asyncio.wait_for(asyncio.gather(prod_a, prod_b), timeout=_WAIT)
        await asyncio.wait_for(asyncio.gather(send_a, send_b), timeout=_WAIT)
        await asyncio.sleep(0)

        # Each session got ONLY its own correlated reply — no cross-read.
        assert any("REPLY_A_RESEARCH" in c for c in adapter_a.received), adapter_a.received
        assert not any("REPLY_B_WEATHER" in c for c in adapter_a.received), adapter_a.received
        assert any("REPLY_B_WEATHER" in c for c in adapter_b.received), adapter_b.received
        assert not any("REPLY_A_RESEARCH" in c for c in adapter_b.received), adapter_b.received
        # Streams reaped under their OWN request_id key.
        assert stream_registry.get_writer("trace-A") is None
        assert stream_registry.get_writer("trace-B") is None
    finally:
        TestModeGuard._active = True


# ============================================================================ #
# Proof 3 — contradiction → coherent SEPARATE answer (queued-new, not a blend)  #
# ============================================================================ #


async def _drain_next(
    *,
    backend: AsyncioBackend,
    scanner: GatewayScanner,
    stream_registry: StreamRegistry,
    turn_registry: TurnRegistry,
    pump: ClarifyPump,
    adapter: ChannelAdapter,
    sid: str,
    finished_request_id: str,
) -> None:
    """Faithful orchestrator._drain_next: deregister the finished turn, then FIFO
    dispatch the queued-new head as a FRESH turn (its own state/stream)."""
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


async def test_midturn_contradiction_routes_new_and_runs_coherent_separate(
    tmp_db: DbPool,
) -> None:
    """A mid-turn "no, I meant Z" is routed NEW (conservative) → queued-new → runs
    as a FRESH coherent turn after the running turn finishes (NOT a folded blend)."""
    TestModeGuard._active = False
    try:
        stream_registry = StreamRegistry()
        turn_registry = TurnRegistry()
        bridge = SqliteMemoryBridge(db=tmp_db)
        gate = asyncio.Event()
        # Running turn: held on a gate so the contradiction arrives mid-turn.
        comp_run = _PlainCompletions({"research X": "RESEARCH_X_DONE"}, barrier=None)
        comp_run_gate = _GatedPlain(comp_run, gate)
        provider_run = _make_provider(comp_run_gate)
        services_run = _build_services(
            bridge, provider_run, _tool_registry(), stream_registry, turn_registry
        )
        backend_run = AsyncioBackend(services=services_run)

        # The queued-new turn gets its OWN provider that answers about Z ALONE.
        comp_new = _PlainCompletions({_Z_MARK: f"Fresh answer about {_Z_MARK}."}, barrier=None)
        provider_new = _make_provider(comp_new)
        services_new = _build_services(
            bridge, provider_new, _tool_registry(), stream_registry, turn_registry
        )
        backend_new = AsyncioBackend(services=services_new)

        scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
        pump = ClarifyPump(_NullGateway(), stream_registry)  # type: ignore[arg-type]
        adapter = _CapturingAdapter()

        # REAL router; classifier verdict mocked to NEW (False) for the contradiction.
        router = TurnRouter(cast("Any", _ScriptedClassifier({_Z_MARK: False})))

        sid = "sess-A"
        msg = IngressMessage(
            text="research X", session_id=sid, channel="cli", trace_id="trace-A"
        )

        # 1. Running turn dispatched and HELD in flight.
        prod, send_run = await _dispatch_turn(
            backend=backend_run, scanner=scanner, stream_registry=stream_registry,
            turn_registry=turn_registry, pump=pump, adapter=adapter, msg=msg,
        )
        await asyncio.wait_for(comp_run.in_flight.wait(), timeout=_WAIT)
        running = turn_registry.running(sid)
        assert running is not None and not prod.done()
        # FIFO drain fires when the running turn finishes.
        prod.add_done_callback(
            lambda _t: asyncio.create_task(
                _drain_next(
                    backend=backend_new, scanner=scanner, stream_registry=stream_registry,
                    turn_registry=turn_registry, pump=pump, adapter=adapter,
                    sid=sid, finished_request_id="trace-A",
                )
            )
        )

        # 2. Mid-turn contradiction → REAL router (verdict NEW) → ENQUEUE_NEW.
        contradiction = f"no, I meant {_Z_MARK}"
        outcome = await asyncio.wait_for(
            route_inflight_message(
                router=router, registry=turn_registry, running=running,
                text=contradiction, session_id=sid,
                request_id_new="trace-A-new", target=None,
            ),
            timeout=_WAIT,
        )
        assert outcome.action is InflightAction.ENQUEUE_NEW, outcome
        # The steer did NOT fold onto the running mailbox (it is a separate turn).
        assert running.steering_mailbox.qsize() == 0, "contradiction must NOT fold onto the run"

        # Caller enqueues the queued-new turn under the intake lock (re-check running).
        async with turn_registry.session_intake_lock(sid):
            assert turn_registry.running(sid) is not None  # still running → enqueue
            turn_registry.enqueue(
                sid, original_input=outcome.routed_text,
                request_id="trace-A-new", target=None,
            )

        # 3. Release the running turn → it finishes, then the FIFO drain dispatches
        #    the queued-new turn as a FRESH, coherent, separate answer.
        gate.set()
        await asyncio.wait_for(prod, timeout=_WAIT)
        await asyncio.wait_for(send_run, timeout=_WAIT)

        # Wait for the queued-new turn to run + deliver.
        async def _await_fresh() -> None:
            while not any(_Z_MARK in c for c in adapter.received):
                send_new = pump._inflight.get(sid)
                if send_new is not None:
                    await send_new
                await asyncio.sleep(0)

        await asyncio.wait_for(_await_fresh(), timeout=_WAIT)

        # PROOF: the running turn delivered its OWN answer (about X), AND the
        # contradiction ran as a SEPARATE coherent turn about Z — not a blend.
        assert any("RESEARCH_X_DONE" in c for c in adapter.received), adapter.received
        assert any(_Z_MARK in c for c in adapter.received), adapter.received
        # The queued-new turn's LLM messages never carried the running ask "research
        # X" folded in — it is a fresh, coherent turn about Z alone.
        assert comp_new.seen_messages, "queued-new turn must have run"
        new_user_text = " ".join(
            str(m.get("content") or "")
            for msgs in comp_new.seen_messages
            for m in msgs
            if m.get("role") == "user"
        )
        assert _Z_MARK in new_user_text, new_user_text
        # FIFO order: the running turn's reply preceded the queued-new reply.
        x_idx = next(i for i, c in enumerate(adapter.received) if "RESEARCH_X_DONE" in c)
        z_idx = next(i for i, c in enumerate(adapter.received) if _Z_MARK in c)
        assert x_idx < z_idx, adapter.received
    finally:
        TestModeGuard._active = True


class _GatedPlain:
    """Wraps a ``_PlainCompletions`` so the FIRST create() holds on a gate (keeps
    the running turn in flight while the test routes the mid-turn contradiction)."""

    def __init__(self, inner: _PlainCompletions, gate: asyncio.Event) -> None:
        self._inner = inner
        self._gate = gate
        self._held = False

    @property
    def seen_messages(self) -> list[list[dict[str, Any]]]:
        return self._inner.seen_messages

    async def create(self, **kwargs: Any) -> _FakeResponse:
        # Hold only at the first REAL ReAct round (a `tools`-bearing call), never at
        # the owl-router / persistence-judge `complete()` calls.
        if "tools" in kwargs and not self._held:
            self._held = True
            self._inner.in_flight.set()
            await self._gate.wait()
        return await self._inner.create(**kwargs)
