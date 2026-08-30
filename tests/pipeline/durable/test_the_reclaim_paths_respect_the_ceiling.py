"""A reclaimed task must reach its ceiling, not climb past it forever.

MEASURED 2026-08-29 on the live table: of 1,261 rows carrying a max_attempts,
exactly ONE exceeded it — and it is the single most expensive row the platform has
ever produced::

    retry-8b7c4029-9a13-4409-9103-0c14c3b470c9   attempt_count 72   max_attempts 30

Its trace (`recover-retry-8b7c40`) billed **2,617,846 input tokens across 294 model
calls** — 2.4x past a ceiling that was supposed to stop it at 30.

WHY IT CLIMBED. `attempt_count` has THREE writers and only ONE of them checks the
ceiling:

  * `fail_and_requeue` — reads the row, computes attempts+1, and dead-letters at
    the ceiling. Correct.
  * `claim_for_recovery(count_attempt=True)` — increments, sets 'recovering'.
    No ceiling check.
  * `reclaim_expired` — increments, sets 'pending'. No ceiling check.

The last two re-arm the work. So a task that reliably hangs is reclaimed, charged
an attempt, and handed straight back to the loop — for ever, or until it happens to
fail through the one path that does check.

THE DOCSTRING ASSERTS THE OPPOSITE, which is why this survived review.
`reclaim_expired` says: "Counts the attempt — a task that reliably kills its worker
must still reach the ceiling rather than cycle for ever." It counts. Nothing on
that path ever compares the count to anything. Counting toward a ceiling nobody
checks is not reaching it.

AND THE PATH IS HOT, not theoretical: `task_liveness_sweep.execute: reclaimed stale
running task(s)` fired 131 times, plus 9 lease-expiry reclaims. 140 increments that
could never terminate a task.

This is the same shape as the token ceiling fixed earlier today — a bound enforced
on one path while another path bypasses it — and the same shape as the two-producer
retry bug: one rule, several writers, and only one of them knows it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "ceiling.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def _seed(
    store: DurableTaskStore, task_id: str, *, attempts: int, max_attempts: int = 30,
    status: str = "running", expired_lease: bool = False,
) -> None:
    now = datetime.now(tz=UTC)
    await store.enqueue(DurableTask(
        task_id=task_id, owner_id=DEFAULT_PRINCIPAL_ID, goal="g", status="pending",
        max_attempts=max_attempts, created_at=now, updated_at=now,
    ))
    sql = (
        "UPDATE tasks SET status=?, attempt_count=?"
        + (", lease_expires_at=?, lease_owner='dead-worker'" if expired_lease else "")
        + " WHERE task_id=?"
    )
    params: list[object] = [status, attempts]
    if expired_lease:
        params.append((now - timedelta(hours=1)).isoformat())
    params.append(task_id)
    await store._db.execute(sql, tuple(params))


async def _row(pool: DbPool, task_id: str) -> dict:
    rows = await pool.fetch_all(
        "SELECT status, attempt_count FROM tasks WHERE task_id=?", (task_id,)
    )
    return dict(rows[0])


# ---------------------------------------------------------------------------
# reclaim_expired — the lease-expiry path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclaim_expired_DEAD_LETTERS_at_the_ceiling(pool: DbPool) -> None:
    """The defect. A hanging task must stop, not be handed back to the loop."""
    store = DurableTaskStore(pool)
    await _seed(store, "hung", attempts=29, max_attempts=30, expired_lease=True)

    await store.reclaim_expired()

    row = await _row(pool, "hung")
    assert row["attempt_count"] == 30
    assert row["status"] == "dead_letter", (
        f"a task at its ceiling was re-armed as {row['status']!r} — this is how "
        "retry-8b7c4029 reached 72 attempts against a max of 30 and billed 2.6M "
        "input tokens"
    )


@pytest.mark.asyncio
async def test_reclaim_expired_still_re_arms_a_task_with_budget_left(pool: DbPool) -> None:
    """The guard must be narrow — crash safety is the whole point of this sweep."""
    store = DurableTaskStore(pool)
    await _seed(store, "crashed", attempts=2, max_attempts=30, expired_lease=True)

    await store.reclaim_expired()

    row = await _row(pool, "crashed")
    assert row["status"] == "pending", "a recoverable task was abandoned"
    assert row["attempt_count"] == 3


# ---------------------------------------------------------------------------
# claim_for_recovery — the stale-sweep path (131 live firings)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_for_recovery_does_not_re_arm_past_the_ceiling(pool: DbPool) -> None:
    """The hotter of the two paths: 131 stale reclaims, none ceiling-aware."""
    store = DurableTaskStore(pool)
    await _seed(store, "stale", attempts=29, max_attempts=30)

    await store.claim_for_recovery("stale", count_attempt=True)

    row = await _row(pool, "stale")
    assert row["status"] == "dead_letter", (
        f"a task at its ceiling was claimed for ANOTHER run as {row['status']!r}"
    )


@pytest.mark.asyncio
async def test_claim_for_recovery_still_works_below_the_ceiling(pool: DbPool) -> None:
    """Boot recovery and the live sweep must keep working."""
    store = DurableTaskStore(pool)
    await _seed(store, "fresh", attempts=1, max_attempts=30)

    claimed = await store.claim_for_recovery("fresh", count_attempt=True)

    assert claimed is True
    row = await _row(pool, "fresh")
    assert row["status"] == "recovering"
    assert row["attempt_count"] == 2


@pytest.mark.asyncio
async def test_a_NON_counting_reclaim_never_dead_letters(pool: DbPool) -> None:
    """count_attempt=False is a clean restart, not a failed attempt.

    The site's own reasoning: "this box exec-replaces the core on every src/
    change — charging it would march good work toward dead_letter on a purely
    operational restart." A restart must not be able to kill a task at all.
    """
    store = DurableTaskStore(pool)
    await _seed(store, "restarted", attempts=29, max_attempts=30)

    claimed = await store.claim_for_recovery("restarted", count_attempt=False)

    assert claimed is True
    row = await _row(pool, "restarted")
    assert row["status"] == "recovering", "an operational restart killed a live task"
    assert row["attempt_count"] == 29, "a restart was charged as an attempt"
