"""Story 6.6 (part B) — DreamWorkerJobHandler execution, resume, idempotency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from stackowl.db.pool import DbPool
from stackowl.memory.contradiction_detector import ContradictionDetector
from stackowl.memory.dream_worker import DreamWorkerJobHandler
from stackowl.scheduler.job import Job, JobResult

from tests._story_6_6_helpers import (  # noqa: F401 — re-exports
    FakeBridge,
    FakeKuzu,
    FakePromoter,
    FakePruner,
    db,
    make_handler,
    make_job,
    no_test_mode_guard,
)


# ---------------------------------------------------------------------------
# Full-pass execution
# ---------------------------------------------------------------------------


async def test_dream_worker_full_pass_completes(db: DbPool) -> None:
    """T6 — full pass with mocked sub-services succeeds."""
    handler, promoter, pruner, kuzu = make_handler(db)
    result = await handler.execute(make_job())
    assert result.success is True
    assert promoter.calls == 1
    assert pruner.calls == 1
    assert len(kuzu.calls) == 1


async def test_dream_worker_writes_checkpoint_row(db: DbPool) -> None:
    """T7 — first execute writes a row to dreamworker_runs."""
    handler, *_ = make_handler(db)
    await handler.execute(make_job())
    rows = await db.fetch_all(
        "SELECT run_id, completed_at, phase FROM dreamworker_runs"
    )
    assert len(rows) == 1
    assert rows[0]["completed_at"] is not None
    assert rows[0]["phase"] == "complete"


# ---------------------------------------------------------------------------
# Resume semantics
# ---------------------------------------------------------------------------


async def test_dream_worker_resumes_from_promotion(db: DbPool) -> None:
    """T8 — resumes from 'promotion' when incomplete row exists."""
    started_at = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO dreamworker_runs (run_id, started_at, phase) VALUES (?, ?, ?)",
        ("resume-1", started_at, "promotion"),
    )
    handler, promoter, pruner, kuzu = make_handler(db)
    await handler.execute(make_job())
    rows = await db.fetch_all(
        "SELECT run_id, completed_at FROM dreamworker_runs ORDER BY run_id"
    )
    assert len(rows) == 1
    assert rows[0]["run_id"] == "resume-1"
    assert rows[0]["completed_at"] is not None
    assert promoter.calls == 1
    assert pruner.calls == 1
    assert len(kuzu.calls) == 1


async def test_dream_worker_resumes_from_pruning(db: DbPool) -> None:
    """T9 — resumes from 'pruning' (skips contradiction + promotion)."""
    started_at = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO dreamworker_runs (run_id, started_at, phase) VALUES (?, ?, ?)",
        ("resume-2", started_at, "pruning"),
    )
    handler, promoter, pruner, kuzu = make_handler(db)
    await handler.execute(make_job())
    assert promoter.calls == 0
    assert pruner.calls == 1
    assert len(kuzu.calls) == 1


async def test_dream_worker_stale_incomplete_starts_fresh(db: DbPool) -> None:
    """Stale incomplete row (> 25h) must not resume — start a new run."""
    stale_started = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    await db.execute(
        "INSERT INTO dreamworker_runs (run_id, started_at, phase) VALUES (?, ?, ?)",
        ("stale-1", stale_started, "promotion"),
    )
    handler, *_ = make_handler(db)
    await handler.execute(make_job())
    rows = await db.fetch_all(
        "SELECT run_id, completed_at FROM dreamworker_runs ORDER BY started_at"
    )
    assert len(rows) == 2
    completed = [r for r in rows if r["completed_at"] is not None]
    assert len(completed) == 1
    assert completed[0]["run_id"] != "stale-1"


# ---------------------------------------------------------------------------
# Output / metadata
# ---------------------------------------------------------------------------


async def test_dream_worker_marks_completed_at(db: DbPool) -> None:
    """T10 — completed_at is set on success and parses as ISO."""
    handler, *_ = make_handler(db)
    await handler.execute(make_job())
    rows = await db.fetch_all("SELECT completed_at FROM dreamworker_runs")
    assert rows[0]["completed_at"] is not None
    datetime.fromisoformat(rows[0]["completed_at"].replace("Z", "+00:00"))


async def test_dream_worker_metadata_contains_counts(db: DbPool) -> None:
    """T11 — JobResult.output contains run_id and the four counts."""
    handler, *_ = make_handler(
        db,
        promoter=FakePromoter(n=7),
        pruner=FakePruner(n=4),
    )
    result = await handler.execute(make_job())
    assert result.output is not None
    assert "run_id=" in result.output
    assert "promoted=7" in result.output
    assert "pruned=4" in result.output
    assert "contradictions=" in result.output


# ---------------------------------------------------------------------------
# Idempotency / handler contract
# ---------------------------------------------------------------------------


async def test_dream_worker_idempotency_two_runs(db: DbPool) -> None:
    """T12 — running twice yields two completed runs, no double-counting."""
    handler, promoter, _, _ = make_handler(db)
    await handler.execute(make_job("run-1"))
    await handler.execute(make_job("run-2"))
    rows = await db.fetch_all(
        "SELECT run_id, completed_at FROM dreamworker_runs ORDER BY started_at"
    )
    assert len(rows) == 2
    assert all(r["completed_at"] is not None for r in rows)
    assert promoter.calls == 2


def test_dream_worker_handler_name_is_dream_worker(db: DbPool) -> None:
    """T17 — handler_name returns the string 'dream_worker'."""
    handler = DreamWorkerJobHandler(
        bridge=FakeBridge(db),  # type: ignore[arg-type]
        promoter=FakePromoter(),  # type: ignore[arg-type]
        pruner=FakePruner(),  # type: ignore[arg-type]
        kuzu_handler=FakeKuzu(),  # type: ignore[arg-type]
        detector=ContradictionDetector(),
    )
    assert handler.handler_name == "dream_worker"


async def test_dream_worker_phase_persisted_before_work(db: DbPool) -> None:
    """T18 — phase update reaches the DB before the next phase's work runs."""
    seen_phases: list[str] = []

    class _SpyPromoter:
        calls = 0

        async def promote_eligible(self) -> int:
            rows = await db.fetch_all(
                "SELECT phase FROM dreamworker_runs LIMIT 1"
            )
            seen_phases.append(rows[0]["phase"])
            _SpyPromoter.calls += 1
            return 0

    handler, _, _, _ = make_handler(db, promoter=_SpyPromoter())  # type: ignore[arg-type]
    await handler.execute(make_job())
    assert seen_phases == ["promotion"]


async def test_dream_worker_kuzu_phase_invokes_kuzu_handler(db: DbPool) -> None:
    """T20 — kuzu_sync phase calls KuzuSyncJobHandler.execute exactly once."""
    handler, _, _, kuzu = make_handler(db)
    await handler.execute(make_job())
    assert len(kuzu.calls) == 1
    assert kuzu.calls[0].handler_name == "kuzu_sync"


# ---------------------------------------------------------------------------
# Sanity: JobResult shape
# ---------------------------------------------------------------------------


async def test_dream_worker_returns_job_result_instance(db: DbPool) -> None:
    """Smoke — execute returns a JobResult with matching job_id."""
    handler, *_ = make_handler(db)
    job = make_job("smoke-1")
    result = await handler.execute(job)
    assert isinstance(result, JobResult)
    assert result.job_id == job.job_id
    assert isinstance(result, Job) is False  # sanity: not a Job
