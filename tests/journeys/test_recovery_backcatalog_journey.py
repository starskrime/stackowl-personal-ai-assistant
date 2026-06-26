"""Back-catalog corruption fix — a false win must not be learned (Branch 4b).

The positive-only learner mines ``task_outcomes`` rows where ``failure_class IS
NULL`` (the critic scorer, the tool-outcome miner, and the reflection trigger all
gate on it). Before Branch 4b the turn outcome's ``success`` / ``failure_class``
were derived from ``state.errors`` ALONE — so a ``verified=False`` false win
(success=True, no exception) persisted as ``success=True, failure_class=NULL`` and
was mined as a WIN. That is exactly how the ``instagram_media_extractor`` class
(claims success, produces nothing) reinforced itself.

This drives a REAL turn through AsyncioBackend where a write tool claims success but
produces no artifact (``verified=False``), then asserts the persisted outcome is NOT
a trustworthy success — ``failure_class`` is set and ``success`` is False — so the
positive-only learner skips it. Positive-only learning is UNCHANGED; only the SIGNAL
it keys on is made trustworthy. Mocks ONLY the AI provider.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.memory.outcome_store import TaskOutcomeStore
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
    db: DbPool,
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
        db_pool=db,
    )


@pytest.mark.asyncio
async def test_unverified_write_persists_non_trustworthy_outcome(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A turn whose only effect was a verified=False false win must persist as a
    NON-trustworthy outcome (failure_class set, success False) so the positive-only
    learner never mines it as a win."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    write_tool = _WriteNeverSucceeds()
    tool_registry = ToolRegistry()
    tool_registry.register(write_tool)

    call_tool = _FakeMessage(content='ACTION: save_report\n```json\n{"name": "q3"}\n```')
    confident = _FakeMessage(content="All done! Saved it.")
    client = _FakeClient([_FakeResponse(call_tool), _FakeResponse(confident)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    services = _build_services(provider, owl_registry, tool_registry, tmp_db)
    backend = AsyncioBackend(services=services)

    trace = "trace-backcatalog-falsewin"
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    msg = IngressMessage(
        text="save my Q3 report to a file", session_id="sess-backcatalog",
        channel="cli", trace_id=trace,
    )
    decision = scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
        interactive=True,
    )
    await backend.run(state)

    assert write_tool.calls, "the write tool never ran — the scenario never occurred."

    store = TaskOutcomeStore(tmp_db)
    outcome = await store.get_by_trace_id(trace)
    assert outcome is not None, "no task_outcome was persisted for the turn."

    # The corruption: success=True + failure_class=None → mined as a win. The fix
    # makes the SIGNAL trustworthy: an unverified effect is NOT a clean success.
    assert outcome.failure_class is not None, (
        "BACK-CATALOG CORRUPTION: a verified=False false win persisted with "
        "failure_class=NULL — the positive-only miner/scorer/reflection will mine it "
        "as a WIN. failure_class must reflect the unverified effect so it is skipped."
    )
    assert outcome.success is False, (
        "a turn whose only effect was unverified must not persist success=True."
    )
