"""Phase E — bound tool-observation context so the tool loop can't overflow.

Covers (TDD, gateway-level mandatory):

1. Unit ``truncate_observation`` — short unchanged, long capped + marker, None.
2. Unit ``trim_messages_to_budget`` — oldest observations elided, system +
   first-user + last-2 preserved, under-budget unchanged, malformed unchanged.
3. Provider-loop integration — a fake OpenAI client + a dispatcher returning a
   1,000,000-char string: the appended OBSERVATION is ≤ MAX_OBSERVATION_CHARS
   (+marker), NOT 1M; and across iterations the payload passed to ``create``
   stays under CONTEXT_CHAR_BUDGET. FAILS if the truncation is removed.
4. Gateway smoke — a tool returning a giant result through the REAL backend:
   the turn completes (no over-budget payload) and the (capped) result is usable.
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
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers._truncate import (
    CONTEXT_CHAR_BUDGET,
    MAX_OBSERVATION_CHARS,
    trim_messages_to_budget,
    truncate_observation,
)
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry

# --------------------------------------------------------------------------- #
# 1. truncate_observation
# --------------------------------------------------------------------------- #


def test_truncate_short_text_unchanged() -> None:
    assert truncate_observation("hello") == "hello"


def test_truncate_none_returns_empty() -> None:
    assert truncate_observation(None) == ""


def test_truncate_at_limit_unchanged() -> None:
    text = "x" * MAX_OBSERVATION_CHARS
    assert truncate_observation(text) == text


def test_truncate_long_text_capped_with_marker_and_count() -> None:
    text = "a" * 1_000_000
    out = truncate_observation(text)
    assert len(out) <= MAX_OBSERVATION_CHARS
    assert "output truncated" in out
    omitted = 1_000_000 - MAX_OBSERVATION_CHARS
    assert str(omitted) in out
    # Head preserved.
    assert out.startswith("a")


def test_truncate_respects_custom_limit() -> None:
    # Limit must exceed the fixed marker length to be honored (head + marker ≤ limit).
    out = truncate_observation("a" * 1_000, limit=200)
    assert len(out) <= 200
    assert "output truncated" in out


# --------------------------------------------------------------------------- #
# 2. trim_messages_to_budget
# --------------------------------------------------------------------------- #


def test_trim_under_budget_unchanged() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "1", "content": "small"},
        {"role": "assistant", "content": "done"},
    ]
    before = [dict(m) for m in messages]
    out = trim_messages_to_budget(messages, budget=10_000)
    assert out == before


def test_trim_over_budget_elides_oldest_observations_preserving_anchors() -> None:
    big = "Z" * 5_000
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "the real request"},  # first user — protected
        {"role": "tool", "tool_call_id": "1", "content": big},  # oldest obs — elidable
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "OBSERVATION: " + big},  # react obs — elidable
        {"role": "assistant", "content": "a2"},
        {"role": "tool", "tool_call_id": "2", "content": big},  # last-2 — protected
        {"role": "assistant", "content": "final"},  # last — protected
    ]
    out = trim_messages_to_budget(messages, budget=8_000)

    total = sum(len(str(m.get("content", ""))) for m in out)
    assert total <= 8_000, f"still over budget: {total}"

    # Protected anchors intact.
    assert out[0]["content"] == "sys"
    assert out[1]["content"] == "the real request"
    assert out[-1]["content"] == "final"
    assert out[-2]["content"] == big  # last-2 tool obs not elided

    # The two oldest observations were elided to the placeholder.
    assert out[2]["content"] == "[earlier tool output elided to fit context]"
    assert out[4]["content"] == "[earlier tool output elided to fit context]"


def test_trim_anthropic_list_content_keeps_tool_use_id() -> None:
    big = "Q" * 6_000
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "request"},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": big}],
        },
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "later"},
        {"role": "assistant", "content": "final"},
    ]
    out = trim_messages_to_budget(messages, budget=2_000)
    block = out[2]["content"][0]
    # Pairing preserved: the message is still a tool_result with its tool_use_id.
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tu_1"
    assert block["content"] == "[earlier tool output elided to fit context]"


def test_trim_malformed_does_not_raise() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user"},  # no content
        {"weird": object()},  # junk
        {"role": "tool", "content": None},
    ]
    # Must not raise; returns the same list object.
    out = trim_messages_to_budget(messages, budget=1)
    assert out is messages


def test_trim_empty_list_unchanged() -> None:
    assert trim_messages_to_budget([], budget=1) == []


# --------------------------------------------------------------------------- #
# 3. Provider-loop integration — fake OpenAI client records payload sizes.
# --------------------------------------------------------------------------- #


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[_FakeToolCall] | None = None) -> None:
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
        # Total chars of the `messages` payload received each invocation.
        self.payload_chars: list[int] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        msgs = kwargs["messages"]
        self.calls.append([dict(m) for m in msgs])
        self.payload_chars.append(sum(len(str(m.get("content", ""))) for m in msgs))
        resp = self._responses[self._i]
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


@pytest.mark.asyncio
async def test_react_observation_is_capped_not_one_million(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Three ACTION turns then a final answer; each tool call returns a 1M-char blob.
    action = _FakeMessage(
        content='ACTION: web_search\n```json\n{"query": "x"}\n```', tool_calls=None
    )
    responses = [
        _FakeResponse(action),
        _FakeResponse(action),
        _FakeResponse(action),
        _FakeResponse(_FakeMessage(content="Final.", tool_calls=None)),
    ]
    client = _FakeClient(responses)
    provider = _make_provider(client)

    giant = "G" * 1_000_000

    async def dispatcher(name: str, args: dict[str, Any]) -> str:
        return giant

    schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ]

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="You are an owl.",
        tool_schemas=schemas,
        tool_dispatcher=dispatcher,
    )
    assert text == "Final."

    # Each OBSERVATION appended to messages must be capped — NOT 1M.
    final_payload = client.chat.completions.calls[-1]
    obs = [m for m in final_payload if str(m.get("content", "")).startswith("OBSERVATION:")]
    assert obs, "expected OBSERVATION turns in the payload"
    for m in obs:
        # "OBSERVATION: " prefix + capped body.
        assert len(str(m["content"])) <= MAX_OBSERVATION_CHARS + len("OBSERVATION: ") + 1, (
            "An OBSERVATION was NOT capped — truncate_observation was removed."
        )

    # all_calls stores the capped (not 1M) result.
    for c in calls:
        assert len(c["result"]) <= MAX_OBSERVATION_CHARS

    # Across all iterations the payload handed to create() stays under budget.
    for chars in client.chat.completions.payload_chars:
        assert chars < CONTEXT_CHAR_BUDGET, (
            f"payload {chars} exceeded budget {CONTEXT_CHAR_BUDGET} — context guard failed."
        )


@pytest.mark.asyncio
async def test_native_tool_result_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    tc = _FakeToolCall(id="call_1", name="web_search", arguments='{"query": "x"}')
    responses = [
        _FakeResponse(_FakeMessage(content=None, tool_calls=[tc])),
        _FakeResponse(_FakeMessage(content="Done.", tool_calls=None)),
    ]
    client = _FakeClient(responses)
    provider = _make_provider(client)

    giant = "N" * 1_000_000

    async def dispatcher(name: str, args: dict[str, Any]) -> str:
        return giant

    schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ]

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=schemas,
        tool_dispatcher=dispatcher,
    )
    assert text == "Done."
    final_payload = client.chat.completions.calls[-1]
    tool_msgs = [m for m in final_payload if m.get("role") == "tool"]
    assert tool_msgs
    for m in tool_msgs:
        assert len(str(m["content"])) <= MAX_OBSERVATION_CHARS, (
            "Native tool result was NOT capped."
        )
    assert all(len(c["result"]) <= MAX_OBSERVATION_CHARS for c in calls)


# --------------------------------------------------------------------------- #
# 4. Gateway smoke — giant tool result through the REAL backend.
#    (Mirrors tests/pipeline/test_phaseA_react_gateway_smoke.py.)
# --------------------------------------------------------------------------- #

_TOOL_NAME = "giant_lookup"
_TOOL_HEAD = "GIANT-HEAD-2026-XYZZY"


class _GiantTool(Tool):
    """Read tool returning a 1M-char result whose head is a unique marker."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return _TOOL_NAME

    @property
    def description(self) -> str:
        return "Return a very large blob."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(
            success=True,
            output=_TOOL_HEAD + ("Z" * 1_000_000),
            error=None,
            duration_ms=0.0,
        )


class _GwFakeMessage:
    def __init__(self, content: str | None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _GwFakeChoice:
    def __init__(self, message: _GwFakeMessage) -> None:
        self.message = message


class _GwFakeResponse:
    def __init__(self, message: _GwFakeMessage) -> None:
        self.choices = [_GwFakeChoice(message)]
        self.model = "gemma4:e4b"


class _GwFakeCompletions:
    def __init__(self, responses: list[_GwFakeResponse]) -> None:
        self._responses = responses
        self._i = 0
        self.calls: list[list[dict[str, Any]]] = []
        self.payload_chars: list[int] = []

    async def create(self, **kwargs: Any) -> _GwFakeResponse:
        msgs = kwargs["messages"]
        self.calls.append([dict(m) for m in msgs])
        self.payload_chars.append(sum(len(str(m.get("content", ""))) for m in msgs))
        resp = self._responses[self._i]
        self._i += 1
        return resp


class _GwFakeChat:
    def __init__(self, completions: _GwFakeCompletions) -> None:
        self.completions = completions


class _GwFakeClient:
    def __init__(self, responses: list[_GwFakeResponse]) -> None:
        self.chat = _GwFakeChat(_GwFakeCompletions(responses))


class _RoutingProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "routing-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content="secretary",
            input_tokens=1,
            output_tokens=1,
            model="routing-fake",
            provider_name="routing-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield "secretary"


def _make_real_provider(client: _GwFakeClient) -> OpenAIProvider:
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
    preg.register_mock("router", _RoutingProvider(), tier="fast")
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


@pytest.mark.asyncio
async def test_giant_tool_result_through_gateway_stays_under_budget(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    react_msg = _GwFakeMessage(
        content=f"I'll look.\nACTION: {_TOOL_NAME}\n```json\n{{\"query\": \"q\"}}\n```",
        tool_calls=None,
    )
    final_msg = _GwFakeMessage(content="Looked it up successfully.", tool_calls=None)
    client = _GwFakeClient([_GwFakeResponse(react_msg), _GwFakeResponse(final_msg)])
    provider = _make_real_provider(client)

    bridge = SqliteMemoryBridge(db=tmp_db)
    owl_registry = OwlRegistry.with_default_secretary()
    tool = _GiantTool()
    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    services = _build_services(bridge, provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    msg = IngressMessage(
        text="look it up", session_id="sess-giant", channel="cli", trace_id="trace-giant-1"
    )
    decision = scanner.scan(msg)
    assert decision.route == "owl"

    state = _state_from_decision(
        decision,
        trace_id=msg.trace_id,
        session_id="sess-giant",
        channel=msg.channel,
        raw_text=msg.text,
    )
    final_state = await backend.run(state)

    # The tool ran.
    assert tool.calls, "the giant tool was never dispatched"

    # The turn completed with a non-empty delivered answer.
    delivered = "".join(chunk.content for chunk in final_state.responses)
    assert delivered.strip()

    # Every payload sent to the model stayed under the context budget — the 1M-char
    # result was capped before entering the message list.
    for chars in client.chat.completions.payload_chars:
        assert chars < CONTEXT_CHAR_BUDGET, (
            f"payload {chars} exceeded budget — giant result was not capped."
        )

    # The capped OBSERVATION is still usable: its head marker survived.
    second_payload = client.chat.completions.calls[1]
    obs = [m for m in second_payload if "OBSERVATION:" in str(m.get("content", ""))]
    assert obs
    assert any(_TOOL_HEAD in str(m["content"]) for m in obs), (
        "capped observation lost the head marker — head must be preserved"
    )
