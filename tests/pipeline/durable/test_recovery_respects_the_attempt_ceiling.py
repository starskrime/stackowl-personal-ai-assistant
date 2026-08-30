"""Recovery must not re-claim a task that has already spent its attempts.

MEASURED LIVE, and it ran for fourteen and a half hours.

Task ``retry-8b7c4029-9a13-4409-9103-0c14c3b470c9`` on the live table:

    status        dead_letter
    attempt_count 72          <- against
    max_attempts  30
    delivered_at  NULL
    destination   'rca'       (a channel name, not an address — undeliverable)
    created       2026-08-29T02:22   updated 2026-08-29T16:55   (14h33m)

Its log timeline over those hours::

    74 x [tasks] recovery: claimed orphaned task — reconstructing state
    74 x [tasks] recovery: launched background resume drive
    73 x [loop] the drive finished but the answer has not reached its destination yet

The ceiling lives ONLY in ``store.fail_and_requeue``. This row never went through
it: ``update_status`` correctly refuses to close a row that owes a delivery, and
``recovery`` then simply claims it again. Grep of ``recovery.py`` finds no
``max_attempts``, no ``attempt_count``, no ``dead_letter`` — the only "ceiling"
in that file is ``creation_ceiling``, which is an authorisation bound, not a
retry budget.

That is CLAUDE.md's "never a second engine" rule broken INSIDE the loop package:
a second driver on the loop's own table, with none of the loop's limits.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "ceiling.db"
    seed_schema(db_path)
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def _seed(store: DurableTaskStore, *, attempts: int, max_attempts: int) -> DurableTask:
    now = datetime.now(tz=UTC)
    task = DurableTask(
        task_id="retry-exhausted",
        owner_id=DEFAULT_PRINCIPAL_ID,
        goal="a goal that never lands",
        status="running",
        trigger_kind="retry",
        destination="telegram:123",
        channel="telegram",
        max_attempts=max_attempts,
        created_at=now,
        updated_at=now,
    )
    await store.enqueue(task)
    for _ in range(attempts):
        await store.fail_and_requeue(
            task.task_id, error="still failing", failure_class="transient",
        )
    return task


@pytest.mark.asyncio
async def test_a_task_past_its_ceiling_is_NOT_reclaimed(pool: DbPool) -> None:
    """The guard. Without it, recovery re-drives a spent row for ever."""
    from stackowl.pipeline.durable.recovery import attempts_exhausted

    store = DurableTaskStore(pool)
    task = await _seed(store, attempts=5, max_attempts=3)

    row = await store.get(task.task_id)
    assert row is not None
    assert row.attempt_count >= row.max_attempts, (
        "the fixture cannot show the bug — it must be genuinely past the ceiling"
    )
    assert attempts_exhausted(row) is True


@pytest.mark.asyncio
async def test_a_task_with_attempts_LEFT_is_still_reclaimed(pool: DbPool) -> None:
    """The guard must be narrow.

    Recovery exists to rescue orphaned work; refusing every row would silently
    disable it, which is a worse bug in the opposite direction.
    """
    from stackowl.pipeline.durable.recovery import attempts_exhausted

    store = DurableTaskStore(pool)
    task = await _seed(store, attempts=1, max_attempts=30)

    row = await store.get(task.task_id)
    assert row is not None
    assert attempts_exhausted(row) is False


def test_a_row_with_no_ceiling_information_is_reclaimed() -> None:
    """Fail OPEN, not closed.

    A malformed or legacy row must not be stranded by this guard — the loop's own
    ceiling still bounds it downstream. Refusing on missing data would turn a
    safety check into a silent work-stopper.
    """
    from stackowl.pipeline.durable.recovery import attempts_exhausted

    assert attempts_exhausted(object()) is False
    assert attempts_exhausted(None) is False


@pytest.mark.asyncio
async def test_the_RECOVERER_ITSELF_refuses_a_spent_row(pool: DbPool) -> None:
    """Exercise the CLASS, not just the predicate.

    Written after the first version of this file passed while the module was
    structurally broken: the predicate had been inserted at column 0 inside the
    class body, so every method from `_claim_and_reconstruct` onward silently fell
    OUT of `DurableTaskRecoverer` — and these tests still went green, because they
    only imported the free function. A guard that never touches the object it
    guards is not a guard.
    """
    from unittest.mock import AsyncMock

    from stackowl.pipeline.durable.recovery import DurableTaskRecoverer

    store = DurableTaskStore(pool)
    task = await _seed(store, attempts=5, max_attempts=3)
    row = await store.get(task.task_id)
    assert row is not None

    rec = DurableTaskRecoverer.__new__(DurableTaskRecoverer)
    rec._store = AsyncMock()          # type: ignore[attr-defined]
    rec._owner_id = DEFAULT_PRINCIPAL_ID  # type: ignore[attr-defined]

    assert await rec._claim_and_reconstruct(row) is None
    rec._store.claim_for_recovery.assert_not_awaited()  # type: ignore[attr-defined]
