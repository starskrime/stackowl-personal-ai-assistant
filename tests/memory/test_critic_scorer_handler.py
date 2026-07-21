"""CriticScorerHandler — per-model provider config threading.

Task 17 of the per-model provider config plan: ``execute()`` resolves a
provider ONCE per call via ``get_with_cascade`` and reuses it
across a batch loop of ``_score_one`` calls. This must prove the resolved
MODEL STRING reaches every row's ``provider.complete(...)`` call, not just
the first — a per-row hardcoded ``model=""`` would silently ignore
per-model provider config for every row after the first (or all of them).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.critic_scorer_handler import CriticScorerHandler
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.registry import ModelRoute, ProviderRegistry
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio

_RESOLVED_MODEL = "critic-tier-fast-v7"


@pytest.fixture(autouse=True)
def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The handler calls assert_not_test_mode — neutralize as other handler tests do."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)


@dataclass
class _ModelCapturingProvider:
    """Records the ``model`` kwarg passed to every ``complete()`` call —
    lets a test pin down that the SAME resolved model reaches EVERY row in
    a multi-row batch, not just the first."""

    captured_models: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "stub-critic"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str = "", **kwargs: object
    ) -> CompletionResult:
        self.captured_models.append(model)
        return CompletionResult(
            content=json.dumps({"score": 0.75}),
            model=model or "stub-default",
            provider_name="stub",
            input_tokens=0, output_tokens=0, duration_ms=1.0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "critic_scorer_model_threading.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _job() -> Job:
    return Job(
        job_id="critic_scorer-test", handler_name="critic_scorer",
        schedule="every 10m", idempotency_key="critic_scorer",
        last_run_at=None, next_run_at="2026-07-01T00:00:00+00:00", status="running",
    )


async def _seed_unscored_outcomes(db: DbPool, n: int) -> None:
    store = TaskOutcomeStore(db)
    for i in range(n):
        await store.record(
            trace_id=f"critic-row-{i}", session_id="s", owl_name="secretary", channel="cli",
            success=True, latency_ms=10.0, tool_call_count=0,
            failure_class=None, step_durations={}, input_text="do a thing",
            response_text="solid answer",
        )


async def test_resolved_model_reaches_every_row_in_a_multirow_batch(db: DbPool) -> None:
    """3 unscored rows, ONE provider resolution per execute() — the SAME
    resolved model string must reach ALL THREE per-row provider.complete()
    calls, proving the batch loop doesn't just thread it into the first."""
    n = 3
    await _seed_unscored_outcomes(db, n)

    provider = _ModelCapturingProvider()
    registry = ProviderRegistry()
    registry.register_mock(
        "critic-provider", provider,
        models=(ModelRoute(model=_RESOLVED_MODEL, tiers=("fast",)),),
    )

    handler = CriticScorerHandler(db=db, provider_registry=registry, critic_tier="fast")
    result = await handler.execute(_job())

    assert result.success is True
    assert result.metadata["scored"] == n

    # The load-bearing assertion: every row's complete() call carried the
    # SAME specific resolved model string — not "" and not just row 0.
    assert provider.captured_models == [_RESOLVED_MODEL] * n

    store = TaskOutcomeStore(db)
    for i in range(n):
        out = await store.get_by_trace_id(f"critic-row-{i}")
        assert out is not None
        assert out.quality_score == 0.75
