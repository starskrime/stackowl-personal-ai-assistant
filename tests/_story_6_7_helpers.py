"""Shared fixtures, fakes, and helpers for Story 6.7 tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest

from stackowl.config.settings import MemorySettings, Settings
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.memory.bridge import HealthReport, MemoryBridge
from stackowl.memory.models import MemoryRecord, StagedFact
from stackowl.pipeline.state import PipelineState


@pytest.fixture(autouse=True)
def no_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable :class:`TestModeGuard` for every Story 6.7 test."""
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *a, **kw: None,
    )


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncGenerator[DbPool, None]:
    """Per-test fresh DbPool with all migrations applied."""
    db_path = tmp_path / "story67.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def make_state() -> PipelineState:
    """Minimal :class:`PipelineState` for slash-command tests."""
    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="",
        channel="cli",
        owl_name="secretary",
        pipeline_step="",
    )


def make_settings() -> Settings:
    """Settings instance suitable for MemoryCommand wiring."""
    return Settings(memory=MemorySettings())


def make_staged(
    fact_id: str = "fff00000-0000-0000-0000-000000000001",
    *,
    content: str = "alpha bravo",
    status: Literal["staged", "committed", "rejected"] = "staged",
    source_type: Literal["conversation", "parliament", "manual"] = "conversation",
    confidence: float = 0.5,
    reinforcement_count: int = 0,
) -> StagedFact:
    """Construct a :class:`StagedFact` for fake bridges."""
    return StagedFact(
        fact_id=fact_id,
        content=content,
        source_type=source_type,
        source_ref="sess",
        confidence=confidence,
        reinforcement_count=reinforcement_count,
        status=status,
    )


class FakeBridge(MemoryBridge):
    """Minimal in-memory :class:`MemoryBridge` for command tests."""

    def __init__(self) -> None:
        self.list_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.staged: list[StagedFact] = []
        self.recall_calls: list[tuple[str, int]] = []
        self.recall_results: list[MemoryRecord] = []
        self._by_status: dict[str, list[StagedFact]] = {
            "staged": [],
            "committed": [],
            "rejected": [],
        }

    def seed(self, status: str, fact: StagedFact) -> None:
        self._by_status[status].append(fact)

    async def retrieve(self, query: str, session_id: str) -> str:  # pragma: no cover
        return ""

    async def store(self, content: str, session_id: str) -> None:  # pragma: no cover
        return None

    async def stage(self, fact: StagedFact) -> None:
        self.staged.append(fact)
        self._by_status["staged"].append(fact)

    async def recall(self, query: str, limit: int = 10) -> list[MemoryRecord]:
        self.recall_calls.append((query, limit))
        return list(self.recall_results)

    async def delete(self, fact_id: str) -> None:
        self.delete_calls.append(fact_id)
        for bucket in self._by_status.values():
            bucket[:] = [f for f in bucket if f.fact_id != fact_id]

    async def list_staged(
        self,
        status: Literal["staged", "committed", "rejected"] = "staged",
    ) -> list[StagedFact]:
        self.list_calls.append(status)
        return list(self._by_status.get(status, []))

    async def health(self) -> HealthReport:  # pragma: no cover
        return HealthReport(name="memory.fake", status="ok")


class FakePromoter:
    """Records calls and reports a configurable return value."""

    def __init__(self, success: bool = True) -> None:
        self.force_calls: list[str] = []
        self.promote_eligible_calls: int = 0
        self._success = success

    async def force_promote(self, fact_id: str) -> bool:
        self.force_calls.append(fact_id)
        return self._success

    async def promote_eligible(self) -> int:
        self.promote_eligible_calls += 1
        return 0


def make_record(
    fact_id: str = "rrr00000-0000-0000-0000-000000000001",
    content: str = "committed content",
) -> MemoryRecord:
    """Minimal :class:`MemoryRecord` helper."""
    return MemoryRecord(
        fact_id=fact_id,
        content=content,
        embedding=[],
        embedding_model="stub",
        committed_at=datetime.now(UTC),
        source_type="conversation",
        source_ref="sess",
        tags=[],
    )


__all__: list[str] = [
    "EventBus",
    "FakeBridge",
    "FakePromoter",
    "db",
    "make_record",
    "make_settings",
    "make_staged",
    "make_state",
    "no_test_mode_guard",
]


# Keep a typing-only export so unused-Any warnings don't fire.
_UNUSED: Any = None
