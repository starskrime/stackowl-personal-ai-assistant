"""SkillSynthesizerHandler — per-model provider config threading.

Task 18 of the per-model provider config plan: ``execute()`` resolves a
provider + model via ``get_with_cascade`` (replacing the old
``get_with_cascade`` wrapper, which silently discarded the model) and threads
``model=`` into the :class:`SkillSynthesizer` it constructs. This proves the
resolved model string reaches ``SkillSynthesizer``'s internal discover-phase
``provider.complete()`` call end-to-end through the handler's real wiring —
complementing the more granular per-call-site tests in
``test_skill_synthesizer.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.owls.registry import OwlRegistry
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.registry import ModelRoute, ProviderRegistry
from stackowl.scheduler.job import Job
from stackowl.skills.assembly import SkillsAssembly
from stackowl.skills.synthesizer_handler import SkillSynthesizerHandler
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

_RESOLVED_MODEL = "synth-tier-fast-v3"


@pytest.fixture(autouse=True)
def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The handler calls assert_not_test_mode — neutralize as other handler tests do."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)


class _ModelCapturingProvider:
    """Records the ``model`` kwarg passed to every ``complete()`` call — lets
    a test pin down that the resolved model string reaches the internal
    provider call SkillSynthesizer's discover phase makes."""

    def __init__(self) -> None:
        self.seen_models: list[str] = []

    @property
    def name(self) -> str:
        return "stub-synth"

    @property
    def protocol(self) -> str:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str = "", **kwargs: object
    ) -> CompletionResult:
        self.seen_models.append(model)
        return CompletionResult(
            content=(
                '{"name": "irrelevant", "description": "d", "when_to_use": "w", '
                '"body": "# Steps\\n1. go"}'
            ),
            model=model or "stub-default",
            provider_name="stub-synth",
            input_tokens=0, output_tokens=0, duration_ms=1.0,
        )


def _job() -> Job:
    return Job(
        job_id="skill_synthesizer-test", handler_name="skill_synthesizer",
        schedule="every 1d", idempotency_key="skill_synthesizer",
        last_run_at=None, next_run_at="2026-07-01T00:00:00+00:00", status="running",
    )


async def _seed_outcomes(
    db: DbPool, *, sequence: tuple[str, ...], n: int = 3, quality: float = 0.85,
) -> None:
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


async def test_handler_threads_resolved_model_into_synthesizer(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """SkillSynthesizerHandler.execute() must resolve (provider, model) via
    get_with_cascade and thread that exact model string into the
    SkillSynthesizer it constructs — proving it reaches SkillSynthesizer's
    internal discover-phase provider.complete() call end to end.

    Genuinely discriminating: if execute() kept calling the old
    get_with_cascade wrapper (model discarded) and/or kept constructing
    SkillSynthesizer without model=, seen_models would be [""] instead of the
    sentinel value below.
    """
    skills_root = tmp_path / "ws" / "skills"
    skills_root.mkdir(parents=True)
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
    )
    await _seed_outcomes(tmp_db, sequence=("web_fetch", "shell"), n=3)

    provider = _ModelCapturingProvider()
    registry = ProviderRegistry()
    registry.register_mock(
        "synth-provider", provider,
        models=(ModelRoute(model=_RESOLVED_MODEL, tiers=("fast",)),),
    )

    handler = SkillSynthesizerHandler(
        db=tmp_db, provider_registry=registry, skill_store=components.store,
        skills_root=skills_root, synth_tier="fast",
    )
    result = await handler.execute(_job())

    assert result.success is True
    # The load-bearing assertion: the discover phase's provider.complete()
    # call received the SAME resolved model string get_with_cascade
    # returned — not the hardcoded empty default.
    assert provider.seen_models == [_RESOLVED_MODEL], (
        f"expected provider.complete to receive the resolved model, got: {provider.seen_models!r}"
    )
