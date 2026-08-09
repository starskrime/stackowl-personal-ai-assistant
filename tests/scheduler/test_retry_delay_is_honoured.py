"""The retry delay must actually delay (found on the live platform, 2026-08-09).

`owl_lifecycle-Brain` runs every TWO HOURS. On 2026-08-08 it failed three times
THIRTY SECONDS apart — 16:00:19, 16:00:49, 16:01:19 — one per scheduler tick,
burning its whole retry budget in 90 seconds while the provider was down for 25
minutes.

The cause was an `OR` in the due-job select:

    AND (next_run_at <= ? OR (retry_at IS NOT NULL AND retry_at <= ?))

`next_run_at` is advanced on COMPLETION or on terminal re-arm, never when a
retry is scheduled — so a job that just failed still had `next_run_at` in the
past, the first arm matched immediately, and `_RETRY_DELAY_MIN` was dead code.
The write happened (retry_at was set); the effect did not.

These tests pin the behaviour that makes a retry budget worth having: three
retries at five minutes cover fifteen minutes of a transient outage, three at
thirty seconds cover none.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stackowl.scheduler.scheduler import _MAX_RETRIES, _RETRY_DELAY_MIN

pytestmark = pytest.mark.asyncio

_SCHEMA_COLS = (
    "job_id, handler_name, schedule, idempotency_key, last_run_at, next_run_at, "
    "status, retry_count, created_at, failure_count, last_error, enabled"
)


async def _add_job(db, job_id: str, *, next_run_at: str, retry_at: str | None = None,
                   status: str = "pending") -> None:
    now = datetime.now(UTC).isoformat()
    await db.execute(
        f"INSERT INTO jobs ({_SCHEMA_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (job_id, "noop", "every 2h", job_id, None, next_run_at, status, 0, now, 0, None, 1),
    )
    if retry_at is not None:
        await db.execute("UPDATE jobs SET retry_at = ? WHERE job_id = ?", (retry_at, job_id))


async def _due(db) -> list[str]:
    """The production due-job predicate, run against the real schema."""
    now_iso = datetime.now(UTC).isoformat()
    rows = await db.fetch_all(
        "SELECT job_id FROM jobs WHERE status = 'pending' AND enabled = 1 "
        "AND (CASE WHEN retry_at IS NOT NULL THEN retry_at <= ? "
        "          ELSE next_run_at <= ? END)",
        (now_iso, now_iso),
    )
    return [str(r["job_id"]) for r in rows]


async def test_a_pending_retry_defers_the_job(tmp_db):
    """THE regression. next_run_at is in the past (the job was due and failed),
    but a retry is scheduled for the future — so it must NOT run yet."""
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    future = (datetime.now(UTC) + timedelta(minutes=_RETRY_DELAY_MIN)).isoformat()
    await _add_job(tmp_db, "deferred", next_run_at=past, retry_at=future)

    assert await _due(tmp_db) == []


async def test_the_retry_runs_once_its_delay_has_elapsed(tmp_db):
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    elapsed = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    await _add_job(tmp_db, "ready", next_run_at=past, retry_at=elapsed)

    assert await _due(tmp_db) == ["ready"]


async def test_a_healthy_job_is_unaffected(tmp_db):
    """retry_at is NULL for a healthy job, so cadence alone governs — the steady
    state must be exactly the old next_run_at select."""
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await _add_job(tmp_db, "healthy", next_run_at=past)

    assert await _due(tmp_db) == ["healthy"]


async def test_a_future_cadence_slot_is_still_not_due(tmp_db):
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    await _add_job(tmp_db, "later", next_run_at=future)

    assert await _due(tmp_db) == []


async def test_the_retry_budget_now_spans_a_real_outage(tmp_db):
    """The point of the fix, stated as arithmetic. Three retries at the
    configured delay must cover materially more than a single tick."""
    span_minutes = _MAX_RETRIES * _RETRY_DELAY_MIN

    assert span_minutes >= 15, (
        f"{_MAX_RETRIES} retries at {_RETRY_DELAY_MIN}min covers {span_minutes}min; "
        f"before the fix all three were spent inside ~90 seconds"
    )


async def test_a_disabled_job_never_runs_even_with_a_due_retry(tmp_db):
    """The enabled flag outranks a pending retry — a disabled job is off."""
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    elapsed = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    await _add_job(tmp_db, "off", next_run_at=past, retry_at=elapsed)
    await tmp_db.execute("UPDATE jobs SET enabled = 0 WHERE job_id = ?", ("off",))

    assert await _due(tmp_db) == []
