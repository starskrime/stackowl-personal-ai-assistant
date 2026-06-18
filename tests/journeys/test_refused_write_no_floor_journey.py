"""Refused-write-no-floor journey — a malformed/no-op write must NOT trip the floor.

End-to-end gateway regression for the live incident: a user asked a teaching
question ("help me easy way to remember 2 pattern algorithms"); the weak model
fumbled and called a WRITE-severity tool (`memory`) with an EMPTY required arg.
That validation-refused no-op was tallied as an "unachieved consequential give-up"
and the honest floor REPLACED the helpful answer with
"…The capability that failed: memory." The user got a non-answer for a request
that needed zero memory.

Two seams compose here, both proven end-to-end through the REAL AsyncioBackend:

  L3 — central required-parameter pre-validation refuses the empty-arg call BEFORE
       the tool body runs, recording it as ``side_effect_committed=False``.
  L1 — a refused/no-op write (side_effect_committed=False) is excluded from the
       consequential tally, so ``surface_consequential_giveup_floor`` does NOT fire
       and the model's subsequent helpful answer is delivered unchanged.

Counterpart (the falsification gate that proves we did NOT weaken the floor) lives
in ``test_no_dressed_up_giveup_journey.py``: a GENUINE consequential failure
(side_effect_committed=True) must STILL be replaced by the honest floor.

Mocks ONLY the AI provider. Harness mirrors test_no_dressed_up_giveup_journey.py.
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
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

_SENTINEL = "PATTERN_MNEMONIC_OK"
_FLOOR_SIGNATURE = "capability that failed"


class _WriteToolNeedingAction(Tool):
    """A WRITE-severity tool with a REQUIRED `action` param (mirrors `memory`).

    Records whether execute() was ever reached so the test can prove the L3
    pre-validator refused the malformed call BEFORE the tool body ran.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return "durable semantic facts: add/search/get/forget"

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"action": {"type": "string"}, "content": {"type": "string"}},
            "required": ["action"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(success=True, output="ok", error=None, duration_ms=1.0)


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
    *, consent_gate: ConsequentialActionGate | None = None,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    router = _RouterJudgeProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    return StepServices(
        provider_registry=preg, owl_registry=owl_registry,
        tool_registry=tool_registry, consent_gate=consent_gate,
    )


async def _execute_turn(text: str, session: str, trace: str, backend: AsyncioBackend) -> str:
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    msg = IngressMessage(text=text, session_id=session, channel="cli", trace_id=trace)
    decision = scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start", interactive=True,
    )
    final_state = await backend.run(state)
    return "".join(c.content for c in final_state.responses)


@pytest.mark.asyncio
async def test_refused_write_does_not_floor_and_delivers_answer(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L1+L3 end-to-end: the model calls a WRITE tool with an empty required `action`
    (the live `memory` fumble), then produces a helpful answer. The malformed no-op
    must NOT trip the honest floor; the helpful answer must be delivered.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    mem = _WriteToolNeedingAction()
    tool_registry = ToolRegistry()
    tool_registry.register(mem)

    # Iter 0: malformed `memory` call — empty args (no required `action`).
    # Iter 1: the model's actual helpful answer (seeded with the sentinel).
    call_tool = _FakeMessage(content="ACTION: memory\n```json\n{}\n```", tool_calls=None)
    helpful = _FakeMessage(
        content=(
            f"{_SENTINEL}: To remember the two patterns, pair each with a vivid image "
            "and rehearse them as a two-step story."
        ),
        tool_calls=None,
    )
    client = _FakeClient([_FakeResponse(call_tool), _FakeResponse(helpful)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    services = _build_services(provider, owl_registry, tool_registry, consent_gate=gate)
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "help me easy way to remember 2 pattern algorithms",
        "sess-refused-write-no-floor",
        "trace-refused-write-1",
        backend,
    )

    # OUTCOME 1 — the helpful answer reached the user (asserted via sentinel, not tool shape).
    assert _SENTINEL in delivered, (
        "MERGE-GATE FAIL (L1+L3): the model's helpful answer was NOT delivered — a "
        "malformed/no-op write tripped the give-up floor and replaced the answer. "
        f"Delivered: {delivered!r}"
    )

    # OUTCOME 2 — the honest-floor signature must be ABSENT (no false give-up).
    assert _FLOOR_SIGNATURE not in delivered.lower(), (
        "MERGE-GATE FAIL (L1): the honest floor fired for a request that needed no "
        "consequential action — a refused write must not count as an unachieved "
        f"consequential give-up. Delivered: {delivered!r}"
    )

    # L3 PROOF — the pre-validator refused the empty-arg call BEFORE the tool body ran.
    assert mem.calls == [], (
        "L3 FAIL: the malformed (missing required `action`) call reached the tool body "
        f"instead of being refused pre-execute. Tool calls: {mem.calls!r}"
    )


class _WriteToolEmptyStringOk(Tool):
    """A WRITE tool with a REQUIRED `content` whose empty string is VALID.

    Mirrors write_file (content="" → empty file) / edit (new_string="" → deletion).
    The L3 pre-validator must NOT refuse an explicit empty-string required arg.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "write content to a path (empty content = empty file)"

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description,
            parameters=self.parameters, action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(success=True, output="wrote empty file", error=None, duration_ms=1.0)


@pytest.mark.asyncio
async def test_empty_string_required_arg_is_not_refused(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S1 regression guard: an explicit empty-string required arg (a legitimate
    empty-file write / deletion) must REACH the tool body — L3 only refuses an
    ABSENT or null required param, never an empty string.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    wf = _WriteToolEmptyStringOk()
    tool_registry = ToolRegistry()
    tool_registry.register(wf)

    call_tool = _FakeMessage(
        content='ACTION: write_file\n```json\n{"path": "/tmp/x", "content": ""}\n```',
        tool_calls=None,
    )
    done = _FakeMessage(content="Created the empty file.", tool_calls=None)
    client = _FakeClient([_FakeResponse(call_tool), _FakeResponse(done)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    services = _build_services(provider, owl_registry, tool_registry, consent_gate=gate)
    backend = AsyncioBackend(services=services)

    await _execute_turn(
        "create an empty file", "sess-empty-content", "trace-empty-content-1", backend,
    )

    assert wf.calls == [{"path": "/tmp/x", "content": ""}], (
        "S1 FAIL: L3 pre-validation refused a legitimate empty-string required arg "
        f"(empty-file write). Tool calls: {wf.calls!r}"
    )


class _GenuinelyFailingWriteTool(Tool):
    """A WRITE tool whose body RUNS and FAILS (a real failed write, not a refusal).

    Returns ``side_effect_committed`` at its default (True): the write was attempted
    and may have partially landed. This is the case the floor MUST still catch.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "save_note"

    @property
    def description(self) -> str:
        return "persist a note"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description,
            parameters=self.parameters, action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        # A genuine failure AFTER attempting the write — default side_effect_committed=True.
        return ToolResult(success=False, output="", error="disk full", duration_ms=1.0)


@pytest.mark.asyncio
async def test_genuine_write_failure_still_floors(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falsification gate for the "keep `write` in _EFFECTFUL" decision.

    A genuine WRITE failure (side_effect_committed=True) with a dressed-up "saved it!"
    draft MUST still be replaced by the honest floor. If `write` were removed from the
    effectful set (or the predicate ignored the side-effect flag), this goes RED.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    note = _GenuinelyFailingWriteTool()
    tool_registry = ToolRegistry()
    tool_registry.register(note)

    call_tool = _FakeMessage(
        content='ACTION: save_note\n```json\n{"text": "remember the patterns"}\n```',
        tool_calls=None,
    )
    dressed_up = _FakeMessage(content="Done! I saved it for you.", tool_calls=None)
    client = _FakeClient([_FakeResponse(call_tool), _FakeResponse(dressed_up)])
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)
    services = _build_services(provider, owl_registry, tool_registry, consent_gate=gate)
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "save a note for me", "sess-genuine-write-floor", "trace-genuine-write-1", backend,
    )

    assert note.calls, "sanity: the write tool never ran — the real failure never occurred."
    # The dressed-up claim must be gone; the honest floor must be present.
    assert "saved it" not in delivered.lower(), (
        "MERGE-GATE FAIL: a genuine failed WRITE was dressed up and survived delivery — "
        f"the floor must replace it. Delivered: {delivered!r}"
    )
    floor_honest = "couldn" in delivered.lower() or "could not" in delivered.lower()
    assert floor_honest, (
        "MERGE-GATE FAIL: genuine failed WRITE did not produce the honest floor "
        f"(would go red if `write` were dropped from _EFFECTFUL). Delivered: {delivered!r}"
    )
