"""Recovery actuator → retry-once on an unverified effect, end-to-end (Branch 4a).

THE integration test for the recovery ACTUATOR (the half that turns HONEST into
AGENTIC). Where Branch 2 made a ``verified=False`` false-win trip the honest floor,
Branch 4a makes the dispatch *act* on it: a non-consequential effectful tool that
claims success but whose artifact is not observed (``verified=False``) is RETRIED
ONCE through the same guarded path before the turn surrenders.

* ``test_unverified_effect_is_retried_then_delivered`` — a write tool that produces
  nothing on attempt 1 but writes its artifact on attempt 2. The actuator retries,
  the second attempt verifies, and the confident draft is DELIVERED (not floored).
  Proves the retry rung fires and a transient unverified effect self-heals.
* ``test_retry_is_bounded_to_once_then_floored`` — a write tool that NEVER produces
  its artifact is retried exactly once (two execute calls for one model action) and
  the turn is still floored. Proves retry is bounded and the floor still owns a
  genuinely broken capability.

Driven through the REAL AsyncioBackend; mocks ONLY the AI provider. Harness mirrors
``tests/journeys/test_verification_giveup_journey.py``.
"""

from __future__ import annotations

from pathlib import Path
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
from stackowl.tools.verification import verify_artifact

_CONFIDENT_DRAFT = "All done! I saved your report successfully. CONFIDENT_CLAIM_TOKEN"


class _WriteFailsThenSucceeds(Tool):
    """A 'write' tool that produces NO artifact on its first call (verified=False)
    but writes the real file on its second call (verified=True) — a transient
    unverified effect the retry rung should heal."""

    def __init__(self, artifact: Path) -> None:
        self.calls: list[dict[str, object]] = []
        self._artifact = artifact

    @property
    def name(self) -> str:
        return "save_report"

    @property
    def description(self) -> str:
        return "save a report to a file"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"name": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description, parameters=self.parameters,
            action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        if len(self.calls) >= 2:
            # Second attempt actually produces the artifact.
            self._artifact.write_text("the real report body", encoding="utf-8")
        return ToolResult(
            success=True, output="saved the report", duration_ms=1.0,
            artifact_path=str(self._artifact),
        )

    async def verify(
        self, args: dict[str, object], result: ToolResult, *, started_at: float
    ) -> bool | None:
        return verify_artifact(result.artifact_path, not_before=started_at)


class _WriteNeverSucceeds(Tool):
    """A 'write' tool that always claims success but never produces its artifact."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "save_report"

    @property
    def description(self) -> str:
        return "save a report to a file"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"name": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description, parameters=self.parameters,
            action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(
            success=True, output="saved the report", duration_ms=1.0,
            artifact_path="/nonexistent/stackowl/never-written.txt",
        )

    async def verify(
        self, args: dict[str, object], result: ToolResult, *, started_at: float
    ) -> bool | None:
        return verify_artifact(result.artifact_path, not_before=started_at)


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
async def test_unverified_effect_is_retried_then_delivered(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write tool that produces nothing on attempt 1 but writes its artifact on
    attempt 2 must be RETRIED by the actuator; the verified second attempt then
    delivers the confident draft instead of flooring the turn."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    artifact = tmp_path / "q3-report.txt"
    write_tool = _WriteFailsThenSucceeds(artifact)
    tool_registry = ToolRegistry()
    tool_registry.register(write_tool)

    call_tool = _FakeMessage(content='ACTION: save_report\n```json\n{"name": "q3"}\n```')
    confident = _FakeMessage(content=_CONFIDENT_DRAFT)
    client = _FakeClient([_FakeResponse(call_tool), _FakeResponse(confident)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    services = _build_services(provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "save my Q3 report to a file", "sess-retry-heal", "trace-retry-heal", backend,
    )

    assert len(write_tool.calls) == 2, (
        "RECOVERY ACTUATOR FAIL: an unverified effect (success=True, verified=False) "
        f"was not retried — expected 2 execute calls, saw {len(write_tool.calls)}."
    )
    assert artifact.exists(), "the retry never produced the artifact."
    assert "CONFIDENT_CLAIM_TOKEN" in delivered, (
        "the retried attempt verified (verified=True) but its success draft was not "
        "delivered — the actuator should treat a healed effect as a real success."
    )


@pytest.mark.asyncio
async def test_retry_is_bounded_to_once_then_floored(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write tool that never produces its artifact is retried EXACTLY once (two
    execute calls for one model action), then the honest floor owns the turn."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    write_tool = _WriteNeverSucceeds()
    tool_registry = ToolRegistry()
    tool_registry.register(write_tool)

    call_tool = _FakeMessage(content='ACTION: save_report\n```json\n{"name": "q3"}\n```')
    confident = _FakeMessage(content=_CONFIDENT_DRAFT)
    client = _FakeClient([_FakeResponse(call_tool), _FakeResponse(confident)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    services = _build_services(provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "save my Q3 report to a file", "sess-retry-bounded", "trace-retry-bounded", backend,
    )

    assert len(write_tool.calls) == 2, (
        "retry must be bounded to ONE re-attempt — expected exactly 2 execute calls, "
        f"saw {len(write_tool.calls)}."
    )
    assert delivered.strip(), "user got SILENCE; the floor must answer."
    assert "CONFIDENT_CLAIM_TOKEN" not in delivered, (
        "a write that never produced its artifact (verified=False on both attempts) "
        "still shipped its confident claim — the floor must own this turn."
    )
