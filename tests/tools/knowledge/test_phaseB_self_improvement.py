"""Phase B — wire existing self-learning + skill-synthesis engines as owl tools.

These tests prove that ``reflect_now`` and ``synthesize_skills`` are thin owl-tool
wrappers that CONSTRUCT THE EXISTING handlers from ``StepServices`` deps and call
their ``.execute()`` — no logic reimplementation:

  * ``reflect_now``  -> :class:`ReflectionWriterHandler` (self-learning)
  * ``synthesize_skills`` -> :class:`SkillSynthesizerHandler` (gap-analysis/skill-build)

Coverage:
  1. Unit: ``reflect_now`` with a stub provider + a seeded low-quality outcome
     constructs the real handler and surfaces its ``written:N`` output; a missing
     service degrades to a structured failure (no raise).
  2. Unit: ``synthesize_skills`` with ≥3 same-tool-sequence successful outcomes
     (quality ≥ 0.75) + a stub provider returning a skill draft authors a learned
     skill (output reflects discovery); missing service degrades structurally.
  3. GATEWAY integration: a weak model emits ``ACTION: reflect_now`` and the tool
     is dispatched on the REAL production path, writing a reflection mid-turn —
     proving the agent can now TRIGGER self-learning instead of waiting for the
     nightly job. FAILS if the tool isn't registered/surfaced.
  4. All three self-improvement tools (``skill_manage``/``reflect_now``/
     ``synthesize_skills``) appear in the secretary's PRESENTED provider schema.

Mock providers only (no live LLM). The handlers call
``TestModeGuard.assert_not_test_mode`` — neutralized via monkeypatch exactly as
the existing gateway/handler smoke tests do.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.owls.registry import OwlRegistry
from stackowl.paths import StackowlHome
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.registry import ProviderRegistry
from stackowl.skills.assembly import SkillsAssembly
from stackowl.tools.knowledge.reflect_now import ReflectNowTool
from stackowl.tools.knowledge.synthesize_skills import SynthesizeSkillsTool
from stackowl.tools.registry import ToolRegistry

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator

    from stackowl.db.pool import DbPool


# --------------------------------------------------------------------------- #
# A scripted provider that satisfies the ModelProvider surface the handlers use
# (``complete(messages, model="")``). Registered through the REAL ProviderRegistry
# at the "fast" tier so the handlers' ``get_with_cascade("fast")`` resolves it.
# --------------------------------------------------------------------------- #


@dataclass
class _ScriptedProvider:
    """Stub provider returning canned strings in order from ``complete``."""

    responses: list[str]
    model_name: str = "stub-fast"
    calls: list[list[Message]] = field(default_factory=list)
    _idx: int = 0

    @property
    def name(self) -> str:
        return "stub-fast"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str = "", **kwargs: object
    ) -> CompletionResult:
        self.calls.append(list(messages))
        if self._idx >= len(self.responses):
            # Mirror the no-pending / nothing-to-do path: a benign empty draft.
            out = "{}"
        else:
            out = self.responses[self._idx]
            self._idx += 1
        return CompletionResult(
            content=out, model=self.model_name, provider_name="stub",
            input_tokens=0, output_tokens=0, duration_ms=1.0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def _registry_with_fast(provider: _ScriptedProvider) -> ProviderRegistry:
    preg = ProviderRegistry()
    preg.register_mock("fast", provider, tier="fast")
    return preg


@pytest.fixture(autouse=True)
def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reused handlers call assert_not_test_mode — neutralize as smoke tests do."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)


# --------------------------------------------------------------------------- #
# Seeding helpers (reuse the production stores, exactly like the handler tests).
# --------------------------------------------------------------------------- #


async def _seed_low_quality_outcome(db: DbPool, *, trace_id: str) -> None:
    """Insert one critic-scored low-quality outcome → eligible for reflection."""
    store = TaskOutcomeStore(db)
    await store.record(
        trace_id=trace_id, session_id="s", owl_name="secretary", channel="cli",
        success=True, latency_ms=10.0, tool_call_count=0,
        failure_class=None, step_durations={}, input_text="do a thing",
        response_text="weak answer",
    )
    out = await store.get_by_trace_id(trace_id)
    assert out is not None
    await store.set_quality_score(out.outcome_id, 0.3)


async def _seed_success_cluster(
    db: DbPool, *, sequence: tuple[str, ...], n: int = 3, quality: float = 0.85,
) -> None:
    """Seed ≥3 successful outcomes with the SAME tool_sequence (discover input)."""
    store = TaskOutcomeStore(db)
    for i in range(n):
        tid = f"trace-{sequence[0]}-{i}"
        await store.record(
            trace_id=tid, session_id="s", owl_name="scout", channel="cli",
            success=True, latency_ms=50.0, tool_call_count=len(sequence),
            failure_class=None, step_durations={},
            input_text=f"do the thing {i}", response_text="done",
            tool_sequence=sequence,
        )
        out = await store.get_by_trace_id(tid)
        assert out is not None
        await store.set_quality_score(out.outcome_id, quality)


# ===========================================================================
# 1. reflect_now — constructs the real ReflectionWriterHandler + returns output
# ===========================================================================


async def test_reflect_now_runs_real_handler_and_writes_reflection(
    tmp_db: DbPool,
) -> None:
    await _seed_low_quality_outcome(tmp_db, trace_id="rn-1")
    provider = _ScriptedProvider(responses=[json.dumps({
        "summary": "the answer was too thin",
        "suggested_strategy": "gather more context before answering",
    })])
    services = StepServices(
        db_pool=tmp_db,
        provider_registry=_registry_with_fast(provider),
        embedding_registry=EmbeddingRegistry(),
        lessons_index=None,  # optional on the handler; reflect still writes
    )
    token = set_services(services)
    try:
        res = await ReflectNowTool().execute()
    finally:
        reset_services(token)

    assert res.success, res.error
    # The tool surfaces the real handler's JobResult.output ("written:N").
    assert "written:1" in res.output
    # The reflection genuinely landed (the real handler ran, not a stub).
    from stackowl.memory.reflection_store import ReflectionStore

    ref = await ReflectionStore(tmp_db).get_by_trace_id("rn-1")
    assert ref is not None
    assert ref.summary == "the answer was too thin"


async def test_reflect_now_missing_service_degrades_structurally(
    tmp_db: DbPool,
) -> None:
    # No provider_registry / embedding_registry wired → structured failure, no raise.
    services = StepServices(db_pool=tmp_db)
    token = set_services(services)
    try:
        res = await ReflectNowTool().execute()
    finally:
        reset_services(token)
    assert res.success is False
    assert "learning subsystem not wired" in (res.error or "")


# ===========================================================================
# 2. synthesize_skills — constructs the real SkillSynthesizerHandler + authors
# ===========================================================================


@pytest.fixture()
async def synth_services(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[StepServices, Path]]:
    """Wire services for synthesize_skills against a tmp StackowlHome.skills_dir().

    The tool builds the handler with ``skills_root=StackowlHome.skills_dir()`` so
    the store must index that SAME root — point both at a tmp workspace.
    """
    workspace = tmp_path / "workspace"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    monkeypatch.setattr(
        StackowlHome, "workspace", classmethod(lambda cls: workspace)
    )
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
    )
    provider = _ScriptedProvider(responses=[json.dumps({
        "name": "scrape-and-process",
        "description": "Fetch web content and shell-process it",
        "when_to_use": "User wants a scraped page run through a script",
        "body": "# Steps\n1. Fetch the page.\n2. Shell-process the content.",
    })])
    services = StepServices(
        db_pool=tmp_db,
        provider_registry=_registry_with_fast(provider),
        skill_store=components.store,
        embedding_registry=EmbeddingRegistry(),
    )
    yield services, skills_root


async def test_synthesize_skills_runs_real_handler_and_authors_skill(
    tmp_db: DbPool, synth_services,  # noqa: ANN001
) -> None:
    services, skills_root = synth_services
    await _seed_success_cluster(tmp_db, sequence=("web_fetch", "shell"), n=3)

    token = set_services(services)
    try:
        res = await SynthesizeSkillsTool().execute()
    finally:
        reset_services(token)

    assert res.success, res.error
    # The real handler's JobResult.output reports the discovery summary.
    assert "created:1" in res.output
    # A learned skill was genuinely authored on disk (the real handler ran).
    written = skills_root / "learned" / "scrape-and-process" / "SKILL.md"
    assert written.exists()
    assert "name: scrape-and-process" in written.read_text(encoding="utf-8")


async def test_synthesize_skills_missing_service_degrades_structurally(
    tmp_db: DbPool,
) -> None:
    # Missing skill_store / provider_registry → structured failure, no raise.
    services = StepServices(db_pool=tmp_db)
    token = set_services(services)
    try:
        res = await SynthesizeSkillsTool().execute()
    finally:
        reset_services(token)
    assert res.success is False
    assert "learning subsystem not wired" in (res.error or "")


# ===========================================================================
# 4. Presented schema — all three self-improvement tools surface to every owl.
# ===========================================================================


def test_self_improvement_tools_in_presented_schema() -> None:
    """skill_manage/reflect_now/synthesize_skills are in the per-owl presented set.

    Drive ``to_provider_schema`` with the per-owl gating path (an EMPTY profile /
    no pins / no hydration) so ONLY the non-evictable base+always tiers appear —
    proving these three are wired into that guaranteed base, not merely registered.
    """
    registry = ToolRegistry.with_defaults()
    schemas = registry.to_provider_schema(
        "openai", profile=[], pins=[], hydrated=set()
    )
    names = {s["function"]["name"] for s in schemas}  # type: ignore[index]
    for expected in ("skill_manage", "reflect_now", "synthesize_skills"):
        assert expected in names, (
            f"{expected!r} not in the per-owl presented base set: {sorted(names)}"
        )


# ===========================================================================
# 3. GATEWAY integration — the agent triggers reflect_now mid-turn via ReAct.
# ===========================================================================


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
        resp = self._responses[self._i]
        self._i += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.chat = _FakeChat(_FakeCompletions(responses))


async def test_agent_triggers_reflect_now_through_gateway(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A weak model emits ACTION: reflect_now → the tool is dispatched mid-turn
    and a reflection is written, on the REAL production gateway path.

    This proves the agent can now TRIGGER self-learning during a turn instead of
    waiting for the nightly scheduler job. It FAILS if reflect_now is not
    registered / not surfaced through the secretary's presented schema.
    """
    from stackowl.config.provider import ProviderConfig
    from stackowl.gateway.scanner import GatewayScanner, IngressMessage
    from stackowl.memory.reflection_store import ReflectionStore
    from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
    from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
    from stackowl.pipeline.state import PipelineState
    from stackowl.providers.base import ModelProvider
    from stackowl.providers.openai_provider import OpenAIProvider

    # Seed an eligible low-quality outcome so the reflection has something to do.
    await _seed_low_quality_outcome(tmp_db, trace_id="gw-reflect-1")

    # --- Fake SDK responses: a NO-native-tool_calls weak model -----------------
    react_msg = _FakeMessage(
        content="I'll reflect on recent outcomes.\nACTION: reflect_now\n```json\n{}\n```",
        tool_calls=None,
    )
    final_msg = _FakeMessage(content="Done — I reflected on my recent work.", tool_calls=None)
    client = _FakeClient([_FakeResponse(react_msg), _FakeResponse(final_msg)])

    config = ProviderConfig(
        name="ollama", protocol="openai",
        base_url="http://localhost:11434/v1", default_model="gemma4:e4b",
        tier="powerful",
    )
    main_provider = OpenAIProvider(config, api_key="")
    main_provider._client = client  # type: ignore[assignment]

    # BOTH the SecretaryRouter (triage) and the reused ReflectionWriterHandler
    # resolve the FAST tier via get_with_cascade("fast"), so ONE fast provider
    # must serve BOTH. It is content-aware: a reflection prompt (asks for a
    # summary/suggested_strategy JSON) → return the reflection JSON; otherwise
    # (the router's owl-selection prompt) → return "secretary".
    _REFLECT_JSON = json.dumps({
        "summary": "recent outputs were thin",
        "suggested_strategy": "ask a clarifying question first",
    })

    class _FastDualProvider(ModelProvider):
        @property
        def name(self) -> str:
            return "fast-dual"

        @property
        def protocol(self) -> Any:  # type: ignore[override]
            return "openai"

        async def complete(
            self, messages: list[Message], model: str = "", **kwargs: object
        ) -> CompletionResult:
            text = " ".join(m.content for m in messages).lower()
            is_reflection = "suggested_strategy" in text or "summary" in text
            content = _REFLECT_JSON if is_reflection else "secretary"
            return CompletionResult(
                content=content, input_tokens=1, output_tokens=1,
                model="fast-dual", provider_name="fast-dual", duration_ms=0.0,
            )

        async def stream(  # type: ignore[override]
            self, messages: list[Message], model: str, **kwargs: object
        ):
            yield "secretary"

    preg = ProviderRegistry()
    preg.register_mock("secretary", main_provider, tier="powerful")
    preg.register_mock("powerful", main_provider, tier="powerful")
    preg.register_mock("fast", _FastDualProvider(), tier="fast")

    bridge = SqliteMemoryBridge(db=tmp_db)
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry.with_defaults()

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    services = StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
        db_pool=tmp_db,
        embedding_registry=EmbeddingRegistry(),
    )
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    msg = IngressMessage(
        text="please reflect on your recent work",
        session_id="sess-reflect-gw", channel="cli", trace_id="trace-reflect-gw-1",
    )
    decision = scanner.scan(msg)
    assert decision.route == "owl"
    assert decision.target == "secretary"

    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    state = PipelineState(
        trace_id=msg.trace_id, session_id="sess-reflect-gw", input_text=input_text,
        channel=msg.channel, owl_name=decision.target,
        pipeline_step="start", interactive=True,
    )
    await backend.run(state)

    # The tool dispatched mid-turn → a reflection genuinely landed for the seeded
    # outcome. This is the whole point: self-learning triggered DURING the turn.
    ref = await ReflectionStore(tmp_db).get_by_trace_id("gw-reflect-1")
    assert ref is not None, (
        "reflect_now was not dispatched mid-turn — the agent could not trigger "
        "self-learning (tool not registered/surfaced, or the ReAct branch broke)."
    )
    assert ref.summary == "recent outputs were thin"
