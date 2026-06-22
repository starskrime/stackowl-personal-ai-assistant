"""Integration journey — a leaked tool call NEVER reaches the user as delivered text.

The weak model emits a tool call as TEXT (an unparsed ACTION block / bare JSON
object) instead of dispatching it. Driven end-to-end through the REAL
AsyncioBackend (mocks ONLY the AI provider client), the delivered answer must be a
clean honest response — never the raw ``{"action": ...}`` / ``ACTION:`` text, and
never silence. Mirrors the harness in tests/journeys/test_all_tools_failed_floor.py.
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

# The leaked tool call the model emits as its "answer" (bare JSON object, the exact
# shape the user reported — a skill_manage create call written as text).
_LEAK = '{"action": "create", "name": "format-guardrail-check", "content": "---nname: x"}'


class _NoopTool(Tool):
    """A registered tool so the turn enters the agentic loop (never actually run here)."""

    @property
    def name(self) -> str:
        return "skill_manage"

    @property
    def description(self) -> str:
        return "create or manage a skill"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"action": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description, parameters=self.parameters,
            action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", error=None, duration_ms=1.0)


class _RouterJudgeProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "router-judge-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        # Route to secretary (standard intent → tool loop); judge fails open as delivered.
        content = '{"delivered": true, "reason": "ok"}' if "AGENT DRAFT REPLY" in joined else "secretary"
        return CompletionResult(
            content=content, input_tokens=1, output_tokens=1,
            model="router-judge-fake", provider_name="router-judge-fake", duration_ms=0.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object):  # type: ignore[override]
        yield "secretary"


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.tool_calls = None


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "gemma4:e4b"


class _FakeCompletions:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response  # the weak model leaks the SAME bad call every round

    async def create(self, **kwargs: Any) -> _FakeResponse:
        return self._response


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.chat = _FakeChat(_FakeCompletions(response))


def _make_main_provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama", protocol="openai", base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b", tier="powerful",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


def _build_services(provider: OpenAIProvider, tool_registry: ToolRegistry) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    router = _RouterJudgeProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    preg.register_mock("fast", router, tier="fast")
    return StepServices(
        provider_registry=preg,
        owl_registry=OwlRegistry.with_default_secretary(),
        tool_registry=tool_registry,
    )


async def _execute_turn(text: str, backend: AsyncioBackend) -> str:
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    msg = IngressMessage(text=text, session_id="sess-leak", channel="cli", trace_id="trace-leak")
    decision = scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start", interactive=True,
    )
    final_state = await backend.run(state)
    return "".join(c.content for c in final_state.responses)


@pytest.mark.asyncio
async def test_leaked_tool_call_never_delivered_to_user(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    tool_registry = ToolRegistry()
    tool_registry.register(_NoopTool())

    provider = _make_main_provider(_FakeClient(_FakeResponse(_FakeMessage(_LEAK))))
    backend = AsyncioBackend(services=_build_services(provider, tool_registry))

    delivered = await _execute_turn("create a guardrail skill for me", backend)

    # The raw tool call must NEVER be delivered.
    assert '"action"' not in delivered, f"leaked tool-call JSON reached the user: {delivered!r}"
    assert "ACTION:" not in delivered, f"leaked ACTION block reached the user: {delivered!r}"
    assert "format-guardrail-check" not in delivered or "skill" in delivered.lower()
    # And the user is never left in silence — an honest answer/floor owns the turn.
    assert delivered.strip(), "leak guard left the user with SILENCE instead of an honest answer"
