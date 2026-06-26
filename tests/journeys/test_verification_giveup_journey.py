"""Verification → honest floor, end-to-end through the gateway (Branch 2).

THE integration test for the verification primitive: a WRITE tool that reports
``success=True`` but produces NO artifact (verify() ⇒ verified=False) must, on a
real turn driven from the gateway, have its confident "done!" draft REPLACED by the
deterministic honest floor — because the effect was claimed but never measured.

This is the live-path proof that ``verified`` reaches the give-up floor: before
Branch 2 the ledger recorded the write as a success and the confident draft shipped;
after Branch 2 the unverified write is an effectful FAILURE and the floor owns the
turn. Driven through the REAL AsyncioBackend; mocks ONLY the AI provider. Harness
mirrors ``tests/journeys/test_all_tools_failed_floor.py``.
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
from stackowl.tools.verification import verify_artifact

# A distinctive confident claim the floor must NOT let through.
_CONFIDENT_DRAFT = "All done! I saved your report to disk successfully. CONFIDENT_CLAIM_TOKEN"


class _WriteClaimsButNoArtifact(Tool):
    """A 'write' tool that reports success but never produces its named artifact —
    the disguised --simulate shape. verify() observes the absence → verified=False."""

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
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",   # effectful — an unverified failure must floor
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        # Claims success, names an artifact that was never written.
        return ToolResult(
            success=True, output="saved the report", duration_ms=1.0,
            artifact_path="/nonexistent/stackowl/report-never-written.txt",
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
        # Judge fails OPEN (delivered:true) — the floor must fire INDEPENDENTLY of it.
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
async def test_unverified_write_claim_is_floored_through_gateway(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write tool reports success but produced no artifact (verified=False). On a
    real turn the confident 'done!' draft must be REPLACED by the honest floor —
    the user is never told it worked when reality says it did not."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    write_tool = _WriteClaimsButNoArtifact()
    tool_registry = ToolRegistry()
    tool_registry.register(write_tool)

    # Iter 0: call the write tool. Iter 1: model returns a CONFIDENT success draft.
    call_tool = _FakeMessage(
        content='ACTION: save_report\n```json\n{"name": "q3"}\n```', tool_calls=None,
    )
    confident = _FakeMessage(content=_CONFIDENT_DRAFT, tool_calls=None)
    client = _FakeClient([_FakeResponse(call_tool), _FakeResponse(confident)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    services = _build_services(provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "save my Q3 report to a file", "sess-verif-floor", "trace-verif-floor", backend,
    )

    assert write_tool.calls, "the write tool never ran — the scenario never occurred."
    # The user must NOT be left in silence.
    assert delivered.strip(), "verified=False write → user got SILENCE; floor must answer."
    # The confident, unverified claim must NOT reach the user — the floor replaced it.
    assert "CONFIDENT_CLAIM_TOKEN" not in delivered, (
        "VERIFICATION FLOOR FAIL: a write claimed success but produced no artifact "
        "(verified=False), yet the confident 'done!' draft was delivered. The honest "
        "floor must own this turn."
    )
