"""Phase D — real-time persistence enforcer + shell timeout primitive.

Four layers, red->green:

  1. UNIT  — ``judge_delivery`` rules give-up / fails OPEN on malformed output.
  2. PROVIDER-LOOP — a fake OpenAI client whose first "final answer" is a give-up
     (no tool_calls, plain text); a ``persistence_check`` that returns the directive
     once then None makes the loop CONTINUE (append directive + 2nd model call); an
     always-nudge check is BOUNDED at 2 nudges (no infinite loop).
  3. GATEWAY — scanner->backend->execute where the model first gives up, the judge
     (the FAST-tier provider) says not-delivered, and on the nudge the model emits
     ACTION: <tool> and dispatches -> the tool RAN (the agent did NOT give up).
     A fail-if-removed variant proves the execute wiring is load-bearing.
  4. SHELL — ``timeout`` arg honoured + bounded to the ceiling; default unchanged.

Mirrors ``tests/providers/test_react_protocol.py`` (fake SDK client) and
``tests/pipeline/test_phaseA_react_gateway_smoke.py`` (gateway-driving harness).
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE, judge_delivery
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry
from stackowl.tools.system.shell import (
    _TIMEOUT_CEILING_SEC,
    _TIMEOUT_SEC,
    ShellTool,
    _resolve_timeout,
)

# =========================================================================== #
# 1. UNIT — judge_delivery
# =========================================================================== #


class _StubJudgeProvider(ModelProvider):
    """A provider whose ``complete`` returns a fixed raw string (the judge JSON)."""

    def __init__(self, raw: str, *, raise_exc: Exception | None = None) -> None:
        self._raw = raw
        self._raise = raise_exc

    @property
    def name(self) -> str:
        return "stub-judge"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        if self._raise is not None:
            raise self._raise
        return CompletionResult(
            content=self._raw,
            input_tokens=1,
            output_tokens=1,
            model="stub-judge",
            provider_name="stub-judge",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield self._raw


@pytest.mark.asyncio
async def test_judge_rules_giveup() -> None:
    provider = _StubJudgeProvider('{"delivered": false, "reason": "gave up"}')
    delivered, reason = await judge_delivery(
        provider, "do the task", "I cannot help with that.", ["read_file"]
    )
    assert delivered is False
    assert "gave up" in reason


@pytest.mark.asyncio
async def test_judge_rules_delivered() -> None:
    provider = _StubJudgeProvider('{"delivered": true, "reason": "done"}')
    delivered, _reason = await judge_delivery(
        provider, "do the task", "Here is the result.", ["shell"]
    )
    assert delivered is True


@pytest.mark.asyncio
async def test_judge_fails_open_on_malformed() -> None:
    provider = _StubJudgeProvider("this is not json at all {{{")
    delivered, reason = await judge_delivery(provider, "req", "ans", [])
    assert delivered is True  # fail OPEN
    assert reason == "judge-error"


@pytest.mark.asyncio
async def test_judge_fails_open_on_wrong_type() -> None:
    # 'delivered' present but not a bool -> fail open.
    provider = _StubJudgeProvider('{"delivered": "nope", "reason": "x"}')
    delivered, reason = await judge_delivery(provider, "req", "ans", [])
    assert delivered is True
    assert reason == "judge-error"


@pytest.mark.asyncio
async def test_judge_fails_open_on_provider_error() -> None:
    provider = _StubJudgeProvider("", raise_exc=RuntimeError("boom"))
    delivered, reason = await judge_delivery(provider, "req", "ans", [])
    assert delivered is True
    assert reason == "judge-error"


@pytest.mark.asyncio
async def test_judge_clarifying_question_is_not_giveup() -> None:
    """A draft that poses ONE necessary clarifying question must be delivered=True.

    A clarifying question IS taking action — the agent is proceeding by gathering
    the information it needs. The updated judge prompt explicitly carves this out
    so the persistence enforcer never nudges the agent to "try harder" when it
    has legitimately asked the user for a required disambiguating detail.
    """
    provider = _StubJudgeProvider(
        '{"delivered": true, "reason": "clarifying question asked to proceed"}'
    )
    delivered, reason = await judge_delivery(
        provider,
        user_request="set up my project with the right config",
        draft_answer=(
            "Before I set this up, could you tell me which environment you are "
            "targeting — development or production?"
        ),
        tools_tried=[],
    )
    assert delivered is True, (
        "A clarifying question draft must be judged delivered=True — "
        "asking a necessary question is not a give-up."
    )
    assert reason  # non-empty reason is part of the contract


class _CapturingJudgeProvider(ModelProvider):
    """A provider that records the messages it was asked to judge, then returns
    a fixed raw JSON. Lets a test inspect the PROMPT the judge actually sent."""

    def __init__(self, raw: str) -> None:
        self._raw = raw
        self.seen_messages: list[Message] = []

    @property
    def name(self) -> str:
        return "capturing-judge"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self.seen_messages = list(messages)
        return CompletionResult(
            content=self._raw,
            input_tokens=1,
            output_tokens=1,
            model="capturing-judge",
            provider_name="capturing-judge",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield self._raw


@pytest.mark.asyncio
async def test_judge_prompt_lists_tools_and_demands_escape_hatch() -> None:
    """The judge prompt MUST (a) carry the tools_tried list verbatim and
    (b) instruct that a technical/capability limitation claimed WITHOUT having
    run a command / installed-or-built a tool counts as a give-up.

    The verdict itself comes from the stub, so this asserts the WIRING + that the
    prompt asks the right question — global, no tool/site/task names."""
    provider = _CapturingJudgeProvider(
        '{"delivered": false, "reason": "technical excuse, no command run"}'
    )
    delivered, _reason = await judge_delivery(
        provider,
        user_request="process this thing for me",
        draft_answer=(
            "I can't process this — it isn't possible in my environment and "
            "external tools won't work here."
        ),
        tools_tried=["open_url", "read_page"],
    )
    assert delivered is False

    prompt = "\n".join(m.content for m in provider.seen_messages).lower()
    # (a) tools_tried carried verbatim into the prompt.
    assert "open_url" in prompt and "read_page" in prompt
    # (b) the escape-hatch principle is stated: a technical limitation without
    # running a command / installing or building a tool = give-up. Frame-agnostic
    # — assert the load-bearing concepts appear, not any specific tool/site name.
    assert "command" in prompt
    assert "install" in prompt
    assert "limitation" in prompt or "impossible" in prompt or "cannot" in prompt


@pytest.mark.asyncio
async def test_judge_prompt_allows_blocker_after_command_attempt() -> None:
    """When a command-execution-type tool IS in tools_tried, the prompt must
    convey that a specific blocker AFTER that real attempt is acceptable
    (delivered=true) — the escape hatch was genuinely tried."""
    provider = _CapturingJudgeProvider(
        '{"delivered": true, "reason": "blocker after real command attempts"}'
    )
    delivered, _reason = await judge_delivery(
        provider,
        user_request="process this thing for me",
        draft_answer=(
            "I ran the commands and installed the helper, but the resource is "
            "genuinely unavailable, so I cannot proceed."
        ),
        tools_tried=["run_command", "open_url"],
    )
    assert delivered is True

    prompt = "\n".join(m.content for m in provider.seen_messages).lower()
    # The tools_tried list is carried verbatim.
    assert "run_command" in prompt
    # The "blocker AFTER trying the escape hatch is acceptable" distinction is present.
    assert "after" in prompt
    assert "command" in prompt


@pytest.mark.asyncio
async def test_judge_logs_verdict_at_info(caplog: pytest.LogCaptureFixture) -> None:
    """Every judge run logs its verdict at INFO (no-hidden-decision): we must be
    able to see WHY the judge did or did not nudge, even when it does NOT nudge."""
    import logging

    provider = _CapturingJudgeProvider(
        '{"delivered": true, "reason": "produced the outcome"}'
    )
    with caplog.at_level(logging.INFO):
        delivered, _reason = await judge_delivery(
            provider, "do the task", "Here is the result.", ["run_command"]
        )
    assert delivered is True
    assert any("judge verdict" in rec.getMessage() for rec in caplog.records), (
        "the judge must log its verdict at INFO on every run"
    )


# =========================================================================== #
# 1b. UNIT — summarize_tool_outcomes (per-tool ok|failed from each call result)
# =========================================================================== #


def test_summarize_keys_on_explicit_failed_flag() -> None:
    """Outcome is decided FIRST by the typed ``failed`` boolean providers record —
    the result text is clean (no marker), so we must NOT depend on it."""
    from stackowl.pipeline.persistence import summarize_tool_outcomes

    calls = [
        {"name": "browser_navigate", "result": "<page snapshot ...>", "failed": False},
        {"name": "send_file", "result": "send_file: file outside workspace", "failed": True},
    ]
    assert summarize_tool_outcomes(calls) == [
        "browser_navigate(ok)",
        "send_file(failed)",
    ]


def test_summarize_falls_back_to_marker_for_legacy_entry() -> None:
    """Defense-in-depth: a legacy entry lacking the ``failed`` key but still
    carrying the marker in ``result`` → name(failed)."""
    from stackowl.pipeline.persistence import (
        TOOL_FAILED_MARKER,
        summarize_tool_outcomes,
    )

    calls = [
        {"name": "browser_navigate", "result": "<page snapshot ...>"},
        {"name": "send_file", "result": f"{TOOL_FAILED_MARKER}send_file: file outside workspace"},
    ]
    assert summarize_tool_outcomes(calls) == [
        "browser_navigate(ok)",
        "send_file(failed)",
    ]


def test_summarize_flag_overrides_clean_result() -> None:
    """An explicit failed=False on a clean result is honored as ok even though the
    prose mentions an error (we never INVENT a failure)."""
    from stackowl.pipeline.persistence import summarize_tool_outcomes

    calls = [{"name": "shell", "result": "one harmless error was logged", "failed": False}]
    assert summarize_tool_outcomes(calls) == ["shell(ok)"]


def test_summarize_marks_ok_when_result_is_ambiguous() -> None:
    """Conservative fail-open: a result with no failure marker is treated as ok,
    even if its prose happens to mention an error — we never INVENT failures."""
    from stackowl.pipeline.persistence import summarize_tool_outcomes

    calls = [
        {"name": "shell", "result": "the build finished; one harmless error was logged"},
        {"name": "read_file", "result": "contents..."},
    ]
    assert summarize_tool_outcomes(calls) == ["shell(ok)", "read_file(ok)"]


def test_summarize_is_robust_to_missing_keys() -> None:
    """A malformed entry (missing name/result) must not raise — fail-open pure."""
    from stackowl.pipeline.persistence import summarize_tool_outcomes

    calls: list[dict[str, Any]] = [{}, {"name": "x"}, {"result": "y"}]
    out = summarize_tool_outcomes(calls)
    # No crash; each yields some name(ok) entry (ambiguous → ok).
    assert all(o.endswith("(ok)") for o in out)
    assert len(out) == 3


@pytest.mark.asyncio
async def test_judge_prompt_conveys_failed_tool_is_not_delivery() -> None:
    """When the outcome list marks the action the draft CLAIMS it did as failed,
    the prompt SENT to the judge must (a) carry the name(ok|failed) outcome list
    verbatim and (b) state the rule that a failed tool call is NOT delivery.

    The verdict comes from the stub; this asserts WIRING + prompt content —
    global, no tool/site/task names baked into the rule itself."""
    provider = _CapturingJudgeProvider(
        '{"delivered": false, "reason": "claimed a send but the send tool failed"}'
    )
    delivered, _reason = await judge_delivery(
        provider,
        user_request="send me the clip",
        draft_answer="Here's the video you asked for.",
        tools_tried=["browser_navigate(ok)", "send_file(failed)"],
    )
    assert delivered is False

    prompt = "\n".join(m.content for m in provider.seen_messages).lower()
    # (a) the outcome list is carried verbatim into the prompt.
    assert "send_file(failed)" in prompt
    assert "browser_navigate(ok)" in prompt
    # (b) the prompt RELABELS the tools line to convey outcomes (name and outcome),
    # not bare names.
    assert "outcome" in prompt
    # (c) the load-bearing rule: a tool appearing here does NOT mean it succeeded;
    # a failed backing tool call means NOT delivered. Assert the concepts co-occur
    # in one stretch of the prompt (global wording — no tool/site/task names).
    assert "failed" in prompt
    assert "does not mean it succeeded" in prompt or "not mean it succeeded" in prompt


# =========================================================================== #
# Fake OpenAI SDK client (shape from tests/providers/test_react_protocol.py)
# =========================================================================== #


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "gemma4:e4b"


class _FakeCompletions:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self._i = 0
        self.calls: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append([dict(m) for m in kwargs["messages"]])
        # Repeat the LAST response once exhausted, so an always-nudge test that
        # keeps continuing does not IndexError before the budget stops it.
        idx = min(self._i, len(self._responses) - 1)
        resp = self._responses[idx]
        self._i += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.chat = _FakeChat(_FakeCompletions(responses))


def _make_provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b",
        tier="local",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


# =========================================================================== #
# 2. PROVIDER-LOOP — persistence_check continues then accepts; bounded at 2
# =========================================================================== #


@pytest.mark.asyncio
async def test_persistence_check_continues_then_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Call 1: a give-up (no tool_calls, plain refusal text).
    giveup = _FakeMessage(content="I cannot do that.", tool_calls=None)
    # Call 2: a delivered final answer.
    delivered = _FakeMessage(content="Done — here is the result.", tool_calls=None)
    client = _FakeClient([_FakeResponse(giveup), _FakeResponse(delivered)])
    provider = _make_provider(client)

    async def dispatcher(name: str, args: dict[str, Any]) -> str:
        return "unused"

    nudges: list[tuple[str, list[str]]] = []

    async def persistence_check(draft: str, tools_tried: list[str]) -> str | None:
        nudges.append((draft, tools_tried))
        # Nudge ONCE (on the give-up), then accept.
        return PERSISTENCE_DIRECTIVE if len(nudges) == 1 else None

    text, _calls = await provider.complete_with_tools(
        user_text="do the task",
        system_text="sys",
        tool_schemas=[],
        tool_dispatcher=dispatcher,
        persistence_check=persistence_check,
    )

    # The loop CONTINUED: a 2nd model call happened, and we got the 2nd answer.
    assert len(client.chat.completions.calls) == 2
    assert text == "Done — here is the result."
    # The directive was injected as a user turn before the 2nd call.
    second_call = client.chat.completions.calls[1]
    assert any(
        m.get("role") == "user" and m.get("content") == PERSISTENCE_DIRECTIVE
        for m in second_call
    ), f"directive not injected: {second_call!r}"
    assert len(nudges) == 2  # checked on call-1 (nudge) + call-2 (accept)


@pytest.mark.asyncio
async def test_persistence_nudge_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Every response is a give-up; an always-nudge check must STOP after 2 nudges.
    giveup = _FakeMessage(content="I cannot do that.", tool_calls=None)
    client = _FakeClient([_FakeResponse(giveup)])  # repeated by the fake
    provider = _make_provider(client)

    async def dispatcher(name: str, args: dict[str, Any]) -> str:
        return "unused"

    nudge_returns = 0

    async def always_nudge(draft: str, tools_tried: list[str]) -> str | None:
        nonlocal nudge_returns
        nudge_returns += 1
        return PERSISTENCE_DIRECTIVE  # never satisfied

    text, _calls = await provider.complete_with_tools(
        user_text="do the task",
        system_text="sys",
        tool_schemas=[],
        tool_dispatcher=dispatcher,
        persistence_check=always_nudge,
    )

    # Budget is 2: 2 directives injected -> 3 model calls total (initial + 2 nudges),
    # then the budget is spent and the answer is accepted. Never infinite.
    # (Count via the FINAL call's accumulated history — each directive appears once.)
    last_call = client.chat.completions.calls[-1]
    directive_injections = sum(
        1
        for m in last_call
        if m.get("role") == "user" and m.get("content") == PERSISTENCE_DIRECTIVE
    )
    assert directive_injections == 2, f"expected 2 nudges, got {directive_injections}"
    assert len(client.chat.completions.calls) == 3
    assert text == "I cannot do that."  # accepted once budget exhausted


# =========================================================================== #
# 3. GATEWAY — the agent does NOT give up; it escalates and dispatches a tool
# =========================================================================== #

_TOOL_NAME = "do_the_work"
_TOOL_MARKER = "WORK-DONE-PHASE-D"


class _DoWorkTool(Tool):
    """Deterministic read tool: records that it ran, returns a marker."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return _TOOL_NAME

    @property
    def description(self) -> str:
        return "Actually perform the requested work."

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"task": {"type": "string"}},
            "required": ["task"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(success=True, output=_TOOL_MARKER, error=None, duration_ms=0.0)


class _JudgeRoutingProvider(ModelProvider):
    """FAST-tier provider that BOTH routes (triage) AND judges (persistence).

    The triage SecretaryRouter calls complete() expecting an owl name; the
    persistence judge calls complete() expecting JSON. We disambiguate by the
    presence of the judge's distinctive 'AGENT DRAFT REPLY' marker in the prompt.
    The judge always rules NOT delivered, so the give-up is always nudged.
    """

    @property
    def name(self) -> str:
        return "judge-routing-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        if "AGENT DRAFT REPLY" in joined:
            content = '{"delivered": false, "reason": "stopped without acting"}'
        else:
            content = "secretary"
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="judge-routing-fake",
            provider_name="judge-routing-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield "secretary"


def _make_real_provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b",
        tier="powerful",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


def _build_services(
    bridge: SqliteMemoryBridge,
    provider: OpenAIProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    # The FAST tier serves BOTH triage routing AND the persistence judge.
    preg.register_mock("router", _JudgeRoutingProvider(), tier="fast")
    return StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
    )


def _state_from_decision(
    decision: Any, *, trace_id: str, session_id: str, channel: str, raw_text: str
) -> PipelineState:
    input_text = decision.stripped_text if decision.stripped_text is not None else raw_text
    return PipelineState(
        trace_id=trace_id,
        session_id=session_id,
        input_text=input_text,
        channel=channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )


async def _drive_gateway(
    tmp_db: DbPool, *, with_persistence: bool
) -> tuple[_DoWorkTool, PipelineState, _FakeClient]:
    """Run scanner->backend->execute once. ``with_persistence`` toggles the wiring
    by switching the state's gating flags so we can prove the wiring is load-bearing
    without editing src (depth>0 disables enforcement)."""
    # Call 1: a give-up (no ACTION, plain refusal). Call 2 (after nudge): ACTION.
    giveup = _FakeMessage(content="Sorry, I am unable to complete this.", tool_calls=None)
    act = _FakeMessage(
        content=f'ACTION: {_TOOL_NAME}\n```json\n{{"task": "the work"}}\n```',
        tool_calls=None,
    )
    final = _FakeMessage(content=f"Completed: {_TOOL_MARKER}", tool_calls=None)
    client = _FakeClient([_FakeResponse(giveup), _FakeResponse(act), _FakeResponse(final)])
    provider = _make_real_provider(client)

    bridge = SqliteMemoryBridge(db=tmp_db)
    owl_registry = OwlRegistry.with_default_secretary()
    tool = _DoWorkTool()
    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    services = _build_services(bridge, provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    msg = IngressMessage(
        text="please do the work", session_id="sess-D", channel="cli",
        trace_id="trace-D-1",
    )
    decision = scanner.scan(msg)
    state = _state_from_decision(
        decision, trace_id=msg.trace_id, session_id="sess-D",
        channel=msg.channel, raw_text=msg.text,
    )
    if not with_persistence:
        # delegation_depth>0 disables enforcement gating in execute.py — this is the
        # "wiring removed" equivalent that must make the no-giveup assertion FAIL.
        state = state.evolve(delegation_depth=1)
    final_state = await backend.run(state)
    return tool, final_state, client


@pytest.mark.asyncio
async def test_gateway_agent_does_not_give_up(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    tool, final_state, _client = await _drive_gateway(tmp_db, with_persistence=True)

    # The persistence enforcer caught the give-up, nudged, and the agent ESCALATED:
    # it dispatched the tool instead of giving up.
    assert tool.calls, (
        "GATEWAY FAIL: the agent gave up — the tool was never dispatched. The "
        "persistence_check did not catch the give-up / did not continue the loop."
    )
    assert tool.calls[0].get("task") == "the work"
    delivered = "".join(c.content for c in final_state.responses)
    assert _TOOL_MARKER in delivered


@pytest.mark.asyncio
async def test_gateway_fail_if_persistence_disabled(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    # With enforcement gated OFF (depth>0), the give-up is accepted and the tool
    # NEVER runs — proving the persistence wiring is what makes the agent continue.
    tool, _final_state, _client = await _drive_gateway(tmp_db, with_persistence=False)
    assert not tool.calls, (
        "Control FAIL: the tool ran even with persistence disabled — the gateway "
        "test would pass even if the wiring were removed, so it proves nothing."
    )


# =========================================================================== #
# 4. SHELL — timeout arg honoured + bounded; default unchanged
# =========================================================================== #


def test_shell_timeout_default_when_omitted() -> None:
    assert _resolve_timeout(None) == _TIMEOUT_SEC


def test_shell_timeout_honoured_within_ceiling() -> None:
    assert _resolve_timeout(90) == 90.0
    assert _resolve_timeout(90.5) == 90.5


def test_shell_timeout_bounded_to_ceiling() -> None:
    assert _resolve_timeout(10_000) == _TIMEOUT_CEILING_SEC


def test_shell_timeout_invalid_falls_back_to_default() -> None:
    assert _resolve_timeout("not-a-number") == _TIMEOUT_SEC
    assert _resolve_timeout(0) == _TIMEOUT_SEC
    assert _resolve_timeout(-5) == _TIMEOUT_SEC


def test_shell_timeout_in_schema() -> None:
    schema = ShellTool().parameters
    props = schema["properties"]
    assert isinstance(props, dict)
    assert "timeout" in props


@pytest.mark.asyncio
async def test_shell_timeout_triggers_structured_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A short per-call timeout against a sleep must return a structured failure
    # (no-hidden-errors), not raise. Uses python3 (allowlisted) to sleep.
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    tool = ShellTool()
    result = await tool.execute(
        command="python3 -c \"import time; time.sleep(5)\"", timeout=0.2
    )
    assert result.success is False
    assert "timed out" in (result.error or "")
