"""DreamWorker reliability — failure tracker, promotion retry, stuck-eligible.

Unit-level (no execute() → no TestModeGuard trip). Exercises the helper layer
directly:
- finalize_run sets status='completed'.
- mark_run_failed records status='failed' + error + completed_at.
- count_stuck_eligible mirrors the promoter eligibility gate (settle window
  included) counting rows still status='staged'.
- promotion retry-once: a promoter that raises once then succeeds completes;
  one that raises twice surfaces the failure.
- stuck-eligible: detected → retry promotes → recorded; if still stuck the
  count is persisted on dreamworker_runs.stuck_eligible and a loud ERROR is
  emitted (the audit_log hash chain has a single canonical writer, so the
  stuck-eligible signal does NOT write a raw audit row).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.dream_worker_helpers import (
    count_stuck_eligible,
    finalize_run,
    mark_run_failed,
    record_stuck_eligible,
    retry_once_promotion,
)

pytestmark = pytest.mark.asyncio

_T = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


async def _new_run(db: DbPool, run_id: str = "r1", phase: str = "promotion") -> None:
    await db.execute(
        "INSERT INTO dreamworker_runs (run_id, started_at, phase) VALUES (?, ?, ?)",
        (run_id, datetime.now(UTC).isoformat(), phase),
    )


async def _insert_staged(
    db: DbPool,
    *,
    fact_id: str | None = None,
    staged_at: datetime = _T - timedelta(minutes=30),
    source_type: str = "conversation_fact",
    confidence: float = 0.9,
    reinforcement_count: int = 1,
    status: str = "staged",
) -> str:
    fid = fact_id or str(uuid.uuid4())
    await db.execute(
        """INSERT INTO staged_facts (
               fact_id, content, source_type, source_ref, confidence,
               staged_at, reinforcement_count, status, embedding, embedding_model
           ) VALUES (?, ?, ?, 'sess', ?, ?, ?, ?, ?, ?)""",
        (
            fid,
            "a fact",
            source_type,
            confidence,
            staged_at.isoformat(),
            reinforcement_count,
            status,
            b"",
            None,
        ),
    )
    return fid


# ---------------------------------------------------------------------------
# Failure tracker
# ---------------------------------------------------------------------------


async def test_finalize_run_sets_status_completed(tmp_db: DbPool) -> None:
    await _new_run(tmp_db)
    await finalize_run(tmp_db, "r1")
    rows = await tmp_db.fetch_all(
        "SELECT status, completed_at, phase FROM dreamworker_runs WHERE run_id='r1'"
    )
    assert rows[0]["status"] == "completed"
    assert rows[0]["completed_at"] is not None
    assert rows[0]["phase"] == "complete"


async def test_mark_run_failed_records_error(tmp_db: DbPool) -> None:
    await _new_run(tmp_db)
    await mark_run_failed(tmp_db, "r1", phase="promotion", error="boom")
    rows = await tmp_db.fetch_all(
        "SELECT status, error, completed_at FROM dreamworker_runs WHERE run_id='r1'"
    )
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"] is not None and "boom" in rows[0]["error"]
    assert rows[0]["completed_at"] is not None


# ---------------------------------------------------------------------------
# count_stuck_eligible — mirrors the promoter gate incl. settle window
# ---------------------------------------------------------------------------


async def test_count_stuck_eligible_counts_settled_staged(tmp_db: DbPool) -> None:
    # eligible & settled → counted
    await _insert_staged(tmp_db, staged_at=_T - timedelta(minutes=30))
    # inside settle window → NOT counted
    await _insert_staged(tmp_db, staged_at=_T - timedelta(minutes=5))
    # low confidence → NOT counted
    await _insert_staged(tmp_db, confidence=0.1, staged_at=_T - timedelta(minutes=30))
    # already committed → NOT counted
    await _insert_staged(tmp_db, status="committed", staged_at=_T - timedelta(minutes=30))

    cutoff = (_T - timedelta(minutes=15)).isoformat()
    count = await count_stuck_eligible(
        tmp_db,
        confidence_threshold=0.8,
        conversation_fact_reinforcement_required=1,
        reinforcement_required=3,
        settle_cutoff=cutoff,
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Bounded retry of the promotion phase
# ---------------------------------------------------------------------------


class _Promoter:
    def __init__(self, fail_times: int, result: int = 2) -> None:
        self._fail_times = fail_times
        self._result = result
        self.calls = 0

    async def promote_eligible(self) -> int:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError(f"promote failure {self.calls}")
        return self._result


async def test_promotion_retry_once_succeeds_after_one_failure() -> None:
    p = _Promoter(fail_times=1)
    promoted = await retry_once_promotion(p.promote_eligible)
    assert promoted == 2
    assert p.calls == 2


async def test_promotion_retry_once_reraises_on_second_failure() -> None:
    p = _Promoter(fail_times=2)
    with pytest.raises(RuntimeError):
        await retry_once_promotion(p.promote_eligible)
    assert p.calls == 2


# ---------------------------------------------------------------------------
# Stuck-eligible: record + re-promote + audit
# ---------------------------------------------------------------------------


async def test_record_stuck_eligible_writes_count(tmp_db: DbPool) -> None:
    await _new_run(tmp_db)
    await record_stuck_eligible(tmp_db, "r1", 5)
    rows = await tmp_db.fetch_all(
        "SELECT stuck_eligible FROM dreamworker_runs WHERE run_id='r1'"
    )
    assert rows[0]["stuck_eligible"] == 5


# ---------------------------------------------------------------------------
# Handler-level wiring (no execute() → no TestModeGuard trip)
# ---------------------------------------------------------------------------


def _handler(promoter: object) -> object:
    from stackowl.memory.dream_worker import DreamWorkerJobHandler

    return DreamWorkerJobHandler(
        bridge=None, promoter=promoter, pruner=None,  # type: ignore[arg-type]
        kuzu_handler=None, detector=None,  # type: ignore[arg-type]
    )


async def test_phase_promotion_retries_once_then_succeeds() -> None:
    from stackowl.memory.dream_worker_helpers import DreamWorkerCheckpoint

    p = _Promoter(fail_times=1, result=4)
    h = _handler(p)
    cp = DreamWorkerCheckpoint(run_id="r1", started_at="2026-06-01T00:00:00+00:00", phase="promotion")
    out = await h._phase_promotion(cp)  # type: ignore[attr-defined]
    assert out.facts_promoted == 4
    assert p.calls == 2


async def test_phase_promotion_reraises_on_double_failure() -> None:
    from stackowl.memory.dream_worker_helpers import DreamWorkerCheckpoint

    p = _Promoter(fail_times=2)
    h = _handler(p)
    cp = DreamWorkerCheckpoint(run_id="r1", started_at="2026-06-01T00:00:00+00:00", phase="promotion")
    with pytest.raises(RuntimeError):
        await h._phase_promotion(cp)  # type: ignore[attr-defined]


async def test_verify_outcome_repromotes_stuck_then_clears(tmp_db: DbPool) -> None:
    """A genuinely-eligible staged fact left behind is re-promoted by verify."""
    from stackowl.memory.dream_worker_helpers import DreamWorkerCheckpoint
    from stackowl.memory.fact_promoter import FactPromoter

    from tests.infra.test_clock import FixedClock

    await _new_run(tmp_db, run_id="rv", phase="complete")
    # Eligible, settled, but NOT yet promoted (simulates a missed promotion).
    await _insert_staged(tmp_db, fact_id="stuck-1", staged_at=_T - timedelta(minutes=30))

    promoter = FactPromoter(
        tmp_db,
        confidence_threshold=0.8,
        conversation_fact_reinforcement_required=1,
        clock=FixedClock(_T),
        settle_minutes=15,
    )
    h = _handler(promoter)
    cp = DreamWorkerCheckpoint(run_id="rv", started_at="2026-06-01T00:00:00+00:00", phase="complete")

    await h._verify_outcome(tmp_db, cp)  # type: ignore[attr-defined]

    # The stuck fact was re-promoted → now in committed_facts.
    committed = await tmp_db.fetch_all(
        "SELECT fact_id FROM committed_facts WHERE fact_id = 'stuck-1'"
    )
    assert committed, "verify_outcome must re-promote the stuck eligible fact"


async def test_verify_outcome_records_count_and_logs_when_still_stuck(
    tmp_db: DbPool, caplog: pytest.LogCaptureFixture
) -> None:
    """When re-promotion still leaves rows stuck, the count is persisted on the
    run row and a loud ERROR is emitted — and NO raw audit_log row is written
    (the audit hash chain has a single canonical writer)."""
    from stackowl.memory.dream_worker_helpers import DreamWorkerCheckpoint

    from tests.infra.test_clock import FixedClock

    await _new_run(tmp_db, run_id="rs", phase="complete")
    await _insert_staged(tmp_db, fact_id="never-1", staged_at=_T - timedelta(minutes=30))

    class _NoOpPromoter:
        # Mirrors the real promoter's interface but never actually promotes,
        # so the eligible row stays stuck through both passes.
        def eligibility_params(self) -> dict[str, object]:
            return {
                "confidence_threshold": 0.8,
                "conversation_fact_reinforcement_required": 1,
                "reinforcement_required": 3,
                "settle_cutoff": (FixedClock(_T).now() - timedelta(minutes=15)).isoformat(),
            }

        async def promote_eligible(self) -> int:
            return 0

    h = _handler(_NoOpPromoter())
    cp = DreamWorkerCheckpoint(run_id="rs", started_at="2026-06-01T00:00:00+00:00", phase="complete")

    with caplog.at_level(logging.ERROR):
        await h._verify_outcome(tmp_db, cp)  # type: ignore[attr-defined]

    # The count was recorded on the run row — the user-facing failure tracker.
    run = await tmp_db.fetch_all(
        "SELECT stuck_eligible FROM dreamworker_runs WHERE run_id = 'rs'"
    )
    assert run[0]["stuck_eligible"] >= 1
    # The unresolved signal was surfaced loudly at ERROR.
    assert any(
        rec.levelno == logging.ERROR and "still stuck" in rec.getMessage()
        for rec in caplog.records
    ), "verify_outcome must emit a loud ERROR when memories remain stuck"
    # No raw audit_log row written — the hash chain stays intact.
    audit_rows = await tmp_db.fetch_all(
        "SELECT event_type FROM audit_log WHERE event_type = 'memory.stuck_eligible'"
    )
    assert audit_rows == []


# ---------------------------------------------------------------------------
# execute()-level: terminal status recorded on the run row.
# (TestModeGuard disabled via the 6.6 helper fixture pattern.)
# ---------------------------------------------------------------------------


@pytest.fixture()
def no_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *a, **kw: None,
    )


async def test_execute_clean_run_status_completed(
    tmp_db: DbPool, no_test_mode_guard: None
) -> None:
    from tests._story_6_6_helpers import make_handler, make_job

    handler, _, _, _ = make_handler(tmp_db)
    result = await handler.execute(make_job())
    assert result.success is True
    rows = await tmp_db.fetch_all(
        "SELECT status FROM dreamworker_runs ORDER BY started_at DESC LIMIT 1"
    )
    assert rows[0]["status"] == "completed"


async def test_execute_phase_failure_status_failed(
    tmp_db: DbPool, no_test_mode_guard: None
) -> None:
    from tests._story_6_6_helpers import FakePruner, make_handler, make_job

    class _BoomPruner(FakePruner):
        async def prune(self):  # type: ignore[override]
            raise RuntimeError("prune exploded")

    handler, _, _, _ = make_handler(tmp_db, pruner=_BoomPruner())
    result = await handler.execute(make_job())
    assert result.success is False
    rows = await tmp_db.fetch_all(
        "SELECT status, error FROM dreamworker_runs ORDER BY started_at DESC LIMIT 1"
    )
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"] is not None and "prune exploded" in rows[0]["error"]
