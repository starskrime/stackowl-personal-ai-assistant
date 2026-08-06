"""Shared fixtures, stubs, and helpers for Story 6.6 tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.contradiction_detector import ContradictionDetector
from stackowl.memory.dream_worker import DreamWorkerJobHandler
from stackowl.memory.models import MemoryRecord, StagedFact
from stackowl.scheduler.job import Job, JobResult


@pytest.fixture(autouse=True)
def no_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable :class:`TestModeGuard` for every test in this module."""
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *a, **kw: None,
    )


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncGenerator[DbPool, None]:
    """Per-test fresh DbPool with every migration applied."""
    db_path = tmp_path / "story66.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def make_job(job_id: str = "dw-1") -> Job:
    """Minimal :class:`Job` for DreamWorker handler tests."""
    return Job(
        job_id=job_id,
        handler_name="dream_worker",
        schedule="daily@03:00",
        idempotency_key=f"dream_worker:{job_id}",
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
    )


def staged(
    fact_id: str,
    *,
    source_type: str = "conversation",
    embedding: list[float] | None = None,
) -> StagedFact:
    """Build a :class:`StagedFact` with sensible defaults."""
    return StagedFact(
        fact_id=fact_id,
        content=f"content-{fact_id}",
        source_type=source_type,  # type: ignore[arg-type]
        source_ref="sess",
        confidence=0.5,
        embedding=embedding,
        embedding_model="stub" if embedding else None,
    )


def record(
    fact_id: str,
    *,
    source_type: str = "conversation",
    embedding: list[float] | None = None,
) -> MemoryRecord:
    """Build a :class:`MemoryRecord` with sensible defaults."""
    return MemoryRecord(
        fact_id=fact_id,
        content=f"content-{fact_id}",
        embedding=embedding or [],
        embedding_model="stub",
        committed_at=datetime.now(UTC),
        source_type=source_type,
        source_ref="sess",
        tags=[],
    )


class FakeBridge:
    """Minimal SqliteMemoryBridge stand-in carrying the live DbPool."""

    def __init__(self, pool: DbPool) -> None:
        self._db = pool


class FakePromoter:
    """Counts calls and reports a configurable promoted count."""

    def __init__(self, n: int = 2) -> None:
        self._n = n
        self.calls = 0

    async def promote_eligible(self) -> int:
        self.calls += 1
        return self._n


class FakePruner:
    """Returns a fixed :class:`PruneReport`."""

    def __init__(self, n: int = 1) -> None:
        self._n = n
        self.calls = 0

    async def prune(self) -> Any:
        from stackowl.memory.pruner import PruneReport

        self.calls += 1
        return PruneReport(pruned_count=self._n, kept_count=10, errors=[])


class FakeKuzu:
    """Captures every kuzu_sync call for assertions.

    Mirrors the REAL ``KuzuSyncJobHandler.execute(job, *, budget_s=None)``.
    DEBT-19 added the deadline budget to the real handler and this stub was not
    updated, so every dream-worker pass died with "FakeKuzu.execute() got an
    unexpected keyword argument 'budget_s'" — six tests failing for a reason
    unrelated to anything they assert.

    Third instance of the shape DEBT-38 named: a stub that does not track the
    real interface hides a genuine assertion behind a TypeError indefinitely.
    ``budget_s`` is RECORDED, not just accepted — a stub that swallows an
    argument silently is how this class of drift becomes invisible again.
    """

    def __init__(self) -> None:
        self.calls: list[Job] = []
        self.budgets: list[float | None] = []

    async def execute(self, job: Job, *, budget_s: float | None = None) -> JobResult:
        self.calls.append(job)
        self.budgets.append(budget_s)
        return JobResult(
            job_id=job.job_id,
            success=True,
            output="kuzu",
            error=None,
            duration_ms=1.0,
        )


def make_handler(
    pool: DbPool,
    *,
    promoter: FakePromoter | None = None,
    pruner: FakePruner | None = None,
    kuzu: FakeKuzu | None = None,
    detector: ContradictionDetector | None = None,
) -> tuple[DreamWorkerJobHandler, FakePromoter, FakePruner, FakeKuzu]:
    """Build a DreamWorkerJobHandler wired to fakes."""
    p = promoter or FakePromoter()
    pr = pruner or FakePruner()
    k = kuzu or FakeKuzu()
    d = detector or ContradictionDetector()
    handler = DreamWorkerJobHandler(
        bridge=FakeBridge(pool),  # type: ignore[arg-type]
        promoter=p,  # type: ignore[arg-type]
        pruner=pr,  # type: ignore[arg-type]
        kuzu_handler=k,  # type: ignore[arg-type]
        detector=d,
    )
    return handler, p, pr, k


__all__: list[str] = [
    "FakeBridge",
    "FakeKuzu",
    "FakePromoter",
    "FakePruner",
    "db",
    "make_handler",
    "make_job",
    "no_test_mode_guard",
    "record",
    "staged",
]
