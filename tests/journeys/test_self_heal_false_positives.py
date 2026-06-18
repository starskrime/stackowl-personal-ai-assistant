"""Self-Healing Turn Supervisor — the idempotency-triad false-positive guards (W4.T18).

THE false-positive GUARANTEE (Murat's triad). The structural give-up net
(``is_structural_giveup``: ``tool_failures >= 1 AND successful_tool_calls == 0 AND
_structurally_irrelevant(draft)``) deliberately gates on a TRIVIAL draft so a tool
that "fails" in a turn that is NOT a give-up is never corrupted into a hedge/
fabrication by a spurious nudge. Murat enumerated three realistic turns where a tool
fails but the answer is correct and MUST stand UNCHANGED:

  1. KNOWLEDGE-ANSWER-AFTER-FAILED-SEARCH. "capital of France?" → web_search fails
     (no network) → the model answers "Paris" from knowledge. A SUBSTANTIVE answer →
     ``_structurally_irrelevant`` is False → ``is_structural_giveup`` is False → no
     nudge → the answer STANDS. Proves the substantive-draft gate prevents the false
     positive.

  2. FILE-NOT-FOUND-IS-THE-ANSWER. "does file X exist?" → read_file fails not-found →
     the model answers "No, it doesn't exist." The tool failure IS the information;
     the substantive negative answer must STAND (never nudged into fabricating a
     workaround).

  3. STEER-ABANDONED-CALL. The user steers mid-turn → the in-flight tool call toward
     the OLD goal is abandoned/fails → the model pivots to the NEW instruction and
     answers THAT. The abandoned call's failure must NOT trigger a give-up re-route
     of the OLD goal — the final answer addresses the NEW goal and STANDS.

The KEY assertion for all three: the correct answer is the FINAL delivered text —
idempotent. A spurious nudge, if one ever fired, does not change it.

REAL (everything except the AI provider): the whole ``AsyncioBackend`` pipeline
(scanner → triage → execute → deliver), the REAL ``ToolRegistry`` + ``_dispatch``
(which prefixes a failed ``ToolResult`` with ``TOOL_FAILED_MARKER`` so the provider
records ``failed=True``), the REAL ``OpenAIProvider.complete_with_tools`` ReAct loop,
and the REAL ``build_persistence_check`` → ``decide_nudge`` → ``apply_structural_veto``
path. FAKED: ONLY the main AI provider (a scripted fake OpenAI SDK client) and the
triage-router / persistence judge (a scripted provider on the fast + local tiers).

Cases 1 + 2 are full gateway journeys built on the harness in
``tests/journeys/test_self_heal_lying_judge.py`` / ``test_self_heal_substitution.py``.
Case 3 is a full gateway journey built on the live-steer harness in
``tests/journeys/test_p2p3_steer_merge_gate.py`` (REAL ``route_inflight_message`` →
``TurnRouter`` → ``try_steer`` → mailbox → ``make_steering_callback`` → provider
fold), with a FAILING old-goal tool at the steer boundary — the closest-to-real
reproduction of an abandoned-call-at-a-steer.
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
from stackowl.gateway.inflight_router import InflightAction, route_inflight_message
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.gateway.turn_registry import TurnRegistry
from stackowl.gateway.turn_router import TurnRouter
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamReader, StreamRegistry
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry

# --------------------------------------------------------------------------- #
# A generic failing tool: execute() returns a FAILED ToolResult. The REAL
# _dispatch prefixes the rendered error with TOOL_FAILED_MARKER, so the provider
# records failed=True — that is what (combined with a TRIVIAL draft) would arm the
# structural veto. Here every draft is SUBSTANTIVE, so the veto never fires.
# --------------------------------------------------------------------------- #


class _FailingTool(Tool):
    """A tool whose execute() always fails (host unreachable / file not found)."""

    def __init__(self, name: str, description: str, error: str) -> None:
        self._name = name
        self._description = description
        self._error = error
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"arg": {"type": "string"}},
            "required": ["arg"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(success=False, output="", error=self._error, duration_ms=0.0)


# --------------------------------------------------------------------------- #
# The triage-router / persistence-judge provider (fast + local tiers). Returns the
# owl name for triage routing, and {delivered:true} on a judge prompt so the
# persistence checker is FAIL-OPEN (contributes nothing). With the judge ruling
# delivered on every draft, the ONLY thing that could nudge is the structural veto —
# so if the answer STANDS, it proves the structural gate (not the judge) let it pass.
# --------------------------------------------------------------------------- #


class _RouterJudgeProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "router-judge-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        content = (
            '{"delivered": true, "reason": "looks complete"}'
            if "AGENT DRAFT REPLY" in joined
            else "secretary"
        )
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="router-judge-fake",
            provider_name="router-judge-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield "secretary"


# --------------------------------------------------------------------------- #
# Fake OpenAI SDK client driving a REAL OpenAIProvider (shape from the sibling
# self-heal journeys).
# --------------------------------------------------------------------------- #


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
        self, content: str | None, tool_calls: list[Any] | None = None
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "gemma4:e4b"
        self.usage = None


class _FakeCompletions:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self._i = 0
        self.calls: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append([dict(m) for m in kwargs["messages"]])
        idx = min(self._i, len(self._responses) - 1)
        resp = self._responses[idx]
        self._i += 1
        return resp


class _FakeChat:
    def __init__(self, completions: Any) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: Any) -> None:
        self.chat = _FakeChat(completions)


def _make_main_provider(completions: Any) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b",
        tier="powerful",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = _FakeClient(completions)  # type: ignore[assignment]
    return provider


def _build_services(
    provider: OpenAIProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
    *,
    bridge: SqliteMemoryBridge | None = None,
    stream_registry: StreamRegistry | None = None,
    turn_registry: TurnRegistry | None = None,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    router = _RouterJudgeProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    return StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
        stream_registry=stream_registry,
        turn_registry=turn_registry,
    )


async def _run_gateway_turn(
    *,
    provider: OpenAIProvider,
    tool_registry: ToolRegistry,
    user_text: str,
    session_id: str,
    trace_id: str,
) -> str:
    """Drive the REAL AsyncioBackend (scanner → triage → execute → deliver) end to
    end and return the FINAL delivered text. Mocks ONLY the AI provider + judge."""
    owl_registry = OwlRegistry.with_default_secretary()
    services = _build_services(provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    msg = IngressMessage(
        text=user_text, session_id=session_id, channel="cli", trace_id=trace_id
    )
    decision = scanner.scan(msg)
    input_text = (
        decision.stripped_text if decision.stripped_text is not None else msg.text
    )
    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=input_text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )
    final_state = await backend.run(state)
    return "".join(c.content for c in final_state.responses)


# =========================================================================== #
# Case 1 — KNOWLEDGE-ANSWER-AFTER-FAILED-SEARCH.                                #
# =========================================================================== #


@pytest.mark.asyncio
async def test_knowledge_answer_after_failed_search_stands_unchanged(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """web_search FAILS (no network); the model answers "Paris" from knowledge. A
    SUBSTANTIVE draft → ``is_structural_giveup`` False → no nudge → the answer STANDS
    UNCHANGED. Proves the ``_structurally_irrelevant`` gate prevents the false
    positive (a failed search does NOT corrupt a correct knowledge answer)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    correct_answer = "The capital of France is Paris."

    # Iter 0: call web_search → FAILS (no network). Iter 1: a SUBSTANTIVE correct
    # knowledge answer. The draft is well above the trivial floor → the structural
    # net does NOT classify it as a give-up → no nudge.
    call_search = _FakeMessage(
        content='ACTION: web_search\n```json\n{"arg": "capital of France"}\n```',
        tool_calls=None,
    )
    answer = _FakeMessage(content=correct_answer, tool_calls=None)
    completions = _FakeCompletions([_FakeResponse(call_search), _FakeResponse(answer)])
    provider = _make_main_provider(completions)

    search = _FailingTool(
        "web_search", "Search the web (fails: no network).", "NETWORK_UNREACHABLE"
    )
    tool_registry = ToolRegistry()
    tool_registry.register(search)

    delivered = await _run_gateway_turn(
        provider=provider,
        tool_registry=tool_registry,
        user_text="what is the capital of France?",
        session_id="sess-fp-knowledge",
        trace_id="trace-fp-knowledge-1",
    )

    # Wiring sanity: the search genuinely RAN and FAILED (1 failed, 0 succeeded) —
    # the exact shape that, with a TRIVIAL draft, WOULD arm the structural veto.
    assert search.calls, "web_search never ran — the false-positive scenario is vacuous"

    # KEY ASSERTION — the correct fact is the FINAL delivered text, UNCHANGED.
    assert "Paris" in delivered, (
        "FALSE-POSITIVE BUG: the correct knowledge answer ('Paris') is not the "
        f"delivered text — a failed search corrupted a correct answer. Delivered: {delivered!r}"
    )
    assert correct_answer in delivered, (
        "FALSE-POSITIVE BUG: the substantive knowledge answer was replaced/hedged "
        f"after a failed search. Delivered: {delivered!r}"
    )

    # No spurious nudge: a substantive draft is NOT a structural give-up, so the loop
    # made exactly TWO provider calls (search round + answer round) — no 3rd
    # nudge-driven round. A 3rd call would mean the net OVER-FIRED on the substantive
    # answer (a real false-positive bug).
    assert len(completions.calls) == 2, (
        "FALSE-POSITIVE BUG: the structural net OVER-FIRED — a substantive correct "
        "answer triggered a spurious nudge (a 3rd provider call). The net is too "
        f"aggressive. Provider calls: {len(completions.calls)}"
    )


# =========================================================================== #
# Case 2 — FILE-NOT-FOUND-IS-THE-ANSWER.                                        #
# =========================================================================== #


@pytest.mark.asyncio
async def test_file_not_found_negative_answer_stands_unchanged(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """read_file FAILS not-found; the model answers "No, that file does not exist."
    The tool failure IS the information. The SUBSTANTIVE negative answer must STAND —
    never nudged into fabricating a workaround. Proves the structural gate does not
    treat a correct negative result as a give-up."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    correct_answer = "No, that file does not exist on disk."

    # Iter 0: call read_file → FAILS not-found. Iter 1: a SUBSTANTIVE negative answer
    # (the failure IS the answer). Substantive draft → not a structural give-up.
    call_read = _FakeMessage(
        content='ACTION: read_file\n```json\n{"arg": "/does/not/exist.txt"}\n```',
        tool_calls=None,
    )
    answer = _FakeMessage(content=correct_answer, tool_calls=None)
    completions = _FakeCompletions([_FakeResponse(call_read), _FakeResponse(answer)])
    provider = _make_main_provider(completions)

    reader = _FailingTool(
        "read_file", "Read a file (fails: not found).", "ENOENT_FILE_NOT_FOUND"
    )
    tool_registry = ToolRegistry()
    tool_registry.register(reader)

    delivered = await _run_gateway_turn(
        provider=provider,
        tool_registry=tool_registry,
        user_text="does the file /does/not/exist.txt exist?",
        session_id="sess-fp-notfound",
        trace_id="trace-fp-notfound-1",
    )

    # Wiring sanity: read_file genuinely RAN and FAILED (1 failed, 0 succeeded).
    assert reader.calls, "read_file never ran — the false-positive scenario is vacuous"

    # KEY ASSERTION — the correct negative answer is the FINAL delivered text,
    # UNCHANGED (not nudged into fabricating that the file exists / a workaround).
    assert correct_answer in delivered, (
        "FALSE-POSITIVE BUG: the correct negative answer ('does not exist') was not "
        f"delivered — a not-found tool failure corrupted the answer. Delivered: {delivered!r}"
    )
    # The negative answer must not have been flipped into a fabricated positive.
    assert "does not exist" in delivered.lower(), (
        "FALSE-POSITIVE BUG: the negative result was nudged into fabricating a "
        f"workaround/positive. Delivered: {delivered!r}"
    )

    # No spurious nudge: exactly TWO provider calls (read round + answer round).
    assert len(completions.calls) == 2, (
        "FALSE-POSITIVE BUG: the structural net OVER-FIRED on a substantive negative "
        f"answer (a spurious 3rd call). Provider calls: {len(completions.calls)}"
    )


# =========================================================================== #
# Case 3 — STEER-ABANDONED-CALL (full live-steer gateway journey).             #
# =========================================================================== #
#
# Built on the REAL live-steer harness from test_p2p3_steer_merge_gate.py:
#   route_inflight_message → TurnRouter → try_steer → mailbox →
#   make_steering_callback → provider fold. The OLD-goal tool call FAILS at the
#   steer boundary (abandoned); the model pivots to the NEW goal. We assert the
#   final delivered answer addresses the NEW goal and the OLD goal's tool-failure
#   did NOT re-route the OLD goal (no spurious nudge of the abandoned old work).


_WAIT = 5.0
_NEW_GOAL_MARK = "weather-in-Tokyo"


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


class _NullGateway:
    def peek_for_session(self, session_id: str, channel: str) -> None:  # pragma: no cover
        return None


class _ScriptedClassifier:
    """Stands in for the STEER/NEW verdict (the ONLY classifier mock; the REAL
    TurnRouter still parses explicit signals + drives this verdict)."""

    def __init__(self, steer_for: dict[str, bool]) -> None:
        self._steer_for = steer_for

    async def is_steer(self, *, running_ask: str, message: str) -> bool:
        for needle, verdict in self._steer_for.items():
            if needle in message:
                return verdict
        return False


class _SteerAbandonedCompletions:
    """Iteration 0 → an OLD-goal tool call that FAILS (the abandoned-at-steer call),
    gated so the test can route a STEER onto the live mailbox before iteration 1.
    Iteration 1 → a final answer that pivots to the NEW goal IF the folded
    ``[steering]`` reached the live messages (the load-bearing proof)."""

    def __init__(self, *, gate: asyncio.Event) -> None:
        self._gate = gate
        self._idx = 0
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.in_flight = asyncio.Event()

    def _ancillary(self, msgs: list[dict[str, Any]]) -> _FakeResponse:
        joined = " ".join(str(m.get("content") or "") for m in msgs)
        if "AGENT DRAFT REPLY" in joined or "delivered" in joined.lower():
            return _FakeResponse(
                _FakeMessage(content='{"delivered": true, "reason": "ok"}')
            )
        return _FakeResponse(_FakeMessage(content="secretary"))

    async def create(self, **kwargs: Any) -> _FakeResponse:
        msgs = [dict(m) for m in kwargs["messages"]]
        if "tools" not in kwargs:
            return self._ancillary(msgs)
        self.seen_messages.append(msgs)
        idx = self._idx
        self._idx += 1
        if idx == 0:
            # In flight on the OLD goal: emit a tool call (which will FAIL), then
            # HOLD on the gate so the test routes the mid-turn steer first.
            self.in_flight.set()
            await self._gate.wait()
            tc = _FakeToolCall("c0", "old_goal_tool", '{"arg":"old query"}')
            return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))
        # Iteration 1 — pivot to the NEW goal IF the steer folded; else stay on the
        # (failed) OLD goal — which would be the corruption we are guarding against.
        for m in msgs:
            content = m.get("content")
            if isinstance(content, str) and _NEW_GOAL_MARK in content:
                return _FakeResponse(
                    _FakeMessage(
                        content=f"Pivoted to the new request: the {_NEW_GOAL_MARK} is mild."
                    )
                )
        return _FakeResponse(
            _FakeMessage(content="Still working the old goal (no steer seen).")
        )


class _OldGoalFailingTool(Tool):
    """The OLD-goal tool: its in-flight call FAILS (abandoned at the steer)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "old_goal_tool"

    @property
    def description(self) -> str:
        return "Pursues the OLD goal; fails when abandoned mid-turn at a steer."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"arg": {"type": "string"}}}

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(
            success=False, output="", error="OLD_GOAL_ABANDONED", duration_ms=0.0
        )


@pytest.mark.asyncio
async def test_steer_abandoned_call_does_not_reroute_old_goal(tmp_db: DbPool) -> None:
    """A mid-turn steer arrives WHILE an OLD-goal tool call is in flight; that call
    FAILS (abandoned). The model pivots to the NEW goal and the DELIVERED answer is
    about the NEW goal. The abandoned OLD-goal tool-failure must NOT trigger a
    give-up re-route of the OLD goal — the NEW-goal answer STANDS. Drives the REAL
    route_inflight_message → TurnRouter → try_steer → mailbox → steering callback →
    provider fold chain end to end; mocks ONLY the AI provider + the steer verdict."""
    TestModeGuard._active = False
    try:
        stream_registry = StreamRegistry()
        turn_registry = TurnRegistry()
        bridge = SqliteMemoryBridge(db=tmp_db)
        gate = asyncio.Event()
        completions = _SteerAbandonedCompletions(gate=gate)
        provider = _make_main_provider(completions)

        tool_registry = ToolRegistry.with_defaults()
        old_tool = _OldGoalFailingTool()
        tool_registry.register(old_tool, replace=True)

        owl_registry = OwlRegistry.with_default_secretary()
        services = _build_services(
            provider,
            owl_registry,
            tool_registry,
            bridge=bridge,
            stream_registry=stream_registry,
            turn_registry=turn_registry,
        )
        backend = AsyncioBackend(services=services)
        scanner = GatewayScanner(owl_registry=owl_registry)
        pump = ClarifyPump(cast("Any", _NullGateway()), stream_registry)
        adapter = _CapturingAdapter()
        router = TurnRouter(cast("Any", _ScriptedClassifier({"instead": True})))

        sid = "sess-fp-steer"
        msg = IngressMessage(
            text="research the OLD goal", session_id=sid, channel="cli",
            trace_id="trace-fp-steer",
        )

        # Dispatch the running (OLD-goal) turn; it enters iteration 0 and HOLDS.
        decision = scanner.scan(msg)
        input_text = (
            decision.stripped_text if decision.stripped_text is not None else msg.text
        )
        writer, reader = stream_registry.create(msg.trace_id)
        state = PipelineState(
            trace_id=msg.trace_id,
            session_id=msg.session_id,
            input_text=input_text,
            channel=msg.channel,
            owl_name=decision.target,
            pipeline_step="start",
            interactive=True,
        )
        producer: asyncio.Task[object] = asyncio.create_task(backend.run(state))
        await turn_registry.register(
            msg.trace_id, session_id=msg.session_id,
            task=cast("asyncio.Task[None]", producer), target=None,
            original_input=input_text,
        )
        pump.spawn_send(
            channel_adapter=adapter, reader=reader, session_id=msg.session_id,
            request_id=msg.trace_id, producer=producer, writer=writer,
        )
        send_task = pump._inflight[msg.session_id]  # type: ignore[attr-defined]

        await asyncio.wait_for(completions.in_flight.wait(), timeout=_WAIT)
        running = turn_registry.running(sid)
        assert running is not None and not producer.done()

        # Mid-turn steer → REAL router (verdict STEER) → REAL try_steer onto the live
        # mailbox. The OLD-goal tool call is abandoned at this boundary.
        steer_msg = f"instead, tell me the {_NEW_GOAL_MARK}"
        outcome = await asyncio.wait_for(
            route_inflight_message(
                router=router, registry=turn_registry, running=running,
                text=steer_msg, session_id=sid,
                request_id_new="trace-fp-steer-new", target=None,
            ),
            timeout=_WAIT,
        )
        assert outcome.action is InflightAction.HANDLED, outcome
        assert running.steering_mailbox.qsize() == 1, "steer must be on the live mailbox"

        # Release iteration 0 → the OLD-goal tool runs + FAILS, the REAL steering
        # callback drains the mailbox, the fold splices the NEW goal into iteration 1.
        gate.set()
        await asyncio.wait_for(producer, timeout=_WAIT)
        await asyncio.wait_for(send_task, timeout=_WAIT)
        await asyncio.sleep(0)

        delivered = "".join(adapter.received)

        # Wiring sanity: the OLD-goal tool genuinely RAN and FAILED (the abandoned
        # call) — the exact failure that must NOT re-route the OLD goal.
        assert old_tool.calls, "the OLD-goal tool never ran — the abandon is vacuous"

        # The steer folded into iteration 1's live messages.
        assert len(completions.seen_messages) == 2, completions.seen_messages
        assert any(
            isinstance(m.get("content"), str) and _NEW_GOAL_MARK in m["content"]
            for m in completions.seen_messages[1]
        ), "the folded [steering] NEW goal must reach iteration 1's messages"

        # KEY ASSERTION — the DELIVERED answer addresses the NEW goal and STANDS. The
        # abandoned OLD-goal tool-failure did NOT trigger a give-up re-route of the
        # OLD goal (the answer is NOT "still working the old goal").
        assert _NEW_GOAL_MARK in delivered, (
            "FALSE-POSITIVE BUG: the delivered answer does not address the NEW goal — "
            f"the abandoned OLD-goal failure corrupted the pivot. Delivered: {delivered!r}"
        )
        assert "old goal" not in delivered.lower(), (
            "FALSE-POSITIVE BUG: the abandoned OLD-goal tool-failure re-routed the OLD "
            f"goal instead of letting the NEW-goal answer stand. Delivered: {delivered!r}"
        )

        # No spurious nudge re-routing the abandoned old goal: exactly TWO ReAct
        # rounds (old-goal-tool round + new-goal answer round). A 3rd would mean the
        # abandoned failure armed a give-up nudge of the old goal.
        assert len(completions.seen_messages) == 2, (
            "FALSE-POSITIVE BUG: the abandoned OLD-goal failure triggered a spurious "
            f"nudge round. ReAct rounds: {len(completions.seen_messages)}"
        )
    finally:
        TestModeGuard._active = True
