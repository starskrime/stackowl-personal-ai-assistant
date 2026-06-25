"""T2 / F095 LM-8 — all-tools-failed (non-consequential read) → NON-EMPTY honest answer.

The F095 merge filter (``tc.error is None``) stops a failed tool's error body from
being delivered as the answer. That is safe ONLY because the floor band still owns
the turn: a turn whose ONLY tool failed (a non-consequential READ, so the
consequential-giveup floor does NOT fire) and which produced no model draft must
still hand the user a NON-EMPTY honest response — never silence.

Driven end-to-end through the REAL AsyncioBackend; mocks ONLY the AI provider.
Mirrors the harness from ``tests/journeys/test_no_dressed_up_giveup_journey.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry


class _ReadTool(Tool):
    """A non-consequential READ tool that FAILS with a non-empty error body."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "read-only fetch tool"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"q": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            capability_tag=None,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(
            success=False,
            output="RAW_FAILURE_BODY: 503 upstream unavailable",
            error="fetch failed: 503",
            duration_ms=1.0,
        )


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
            '{"delivered": true, "reason": "ok"}'
            if "AGENT DRAFT REPLY" in joined
            else "secretary"
        )
        return CompletionResult(
            content=content, input_tokens=1, output_tokens=1,
            model="router-judge-fake", provider_name="router-judge-fake", duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield "secretary"


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

    async def create(self, **kwargs: Any) -> _FakeResponse:
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


def _make_main_provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama", protocol="openai", base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b", tier="powerful",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


def _build_services(
    provider: OpenAIProvider, owl_registry: OwlRegistry, tool_registry: ToolRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    router = _RouterJudgeProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    preg.register_mock("standard-judge", router, tier="standard")
    preg.register_mock("fast", router, tier="fast")
    return StepServices(
        provider_registry=preg, owl_registry=owl_registry, tool_registry=tool_registry,
    )


async def _execute_turn(text: str, session: str, trace: str, backend: AsyncioBackend) -> str:
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    msg = IngressMessage(text=text, session_id=session, channel="cli", trace_id=trace)
    decision = scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
        interactive=True,
    )
    final_state = await backend.run(state)
    return "".join(c.content for c in final_state.responses)


@pytest.mark.asyncio
async def test_all_tools_failed_read_still_gets_nonempty_answer(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-consequential read tool fails and the model returns an EMPTY draft.
    The user must STILL get a non-empty honest answer, and the raw failure body
    must NEVER be delivered as the answer."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    read_tool = _ReadTool("fetch_data")
    tool_registry = ToolRegistry()
    tool_registry.register(read_tool)

    # Iter 0: call the failing read tool. Iter 1: model returns an EMPTY draft.
    call_tool = _FakeMessage(
        content="ACTION: fetch_data\n```json\n{\"q\": \"latest\"}\n```", tool_calls=None,
    )
    empty_draft = _FakeMessage(content="", tool_calls=None)
    client = _FakeClient([_FakeResponse(call_tool), _FakeResponse(empty_draft)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    services = _build_services(provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "fetch the latest data for me", "sess-all-failed", "trace-all-failed", backend,
    )

    # The user must NOT be left in silence — the floor band owns the turn.
    assert delivered.strip(), (
        "LM-8 FAIL: all tools failed + empty draft → user got SILENCE. "
        "The floor must produce a non-empty honest answer."
    )
    # The raw failed-tool error body must never be the answer.
    assert "RAW_FAILURE_BODY" not in delivered, (
        "F095 FAIL: a failed tool's raw error body leaked into the delivered answer."
    )
    assert read_tool.calls, "the read tool never ran — the failure never occurred."
