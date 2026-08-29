"""Terminal work nobody was told about is resolved BY THE PLATFORM.

BAKIR, 2026-08-29: "we are always fixing root of the issue not an issue itself.
Issue itself should be fixed by platform due to self healing capability."

I had offered to either hand-clear 74 stranded rows or escalate them. Both are
wrong: one fixes the issue and not the root, the other fires 72 notifications for
work nobody asked about. The root is that **nothing ever revisits a dead letter
that was never announced**, so it becomes permanent debt the platform can neither
see nor drain.

MEASURED. 74 rows at ``status='dead_letter'`` with ``delivered_at IS NULL``. 72 are
one batch from 2026-08-20 with NULL trigger_kind, NULL failure_class, and a
destination carrying NO ADDRESS ("telegram", "rca" — a channel name, not an
addressee).

WHY NO EXISTING SWEEP FINDS THEM. ``revive_undelivered_failures`` scans
``status='failed'`` and deliberately skips ``dead_letter``, and its reasoning is
correct as written: *"that status is a decision the loop already made AND
ANNOUNCED."* The word ANNOUNCED is doing work that was never true for these rows —
an unaddressable task cannot be announced to anyone.

THE TWO OUTCOMES, and why they differ. A dead letter with a real address has
someone waiting, so it is ESCALATED once through the existing ProactiveDeliverer.
One with no address has nobody waiting, so there is no debt to pay — it is RETIRED,
recorded as reviewed so no later sweep re-examines it. Neither path re-runs the
work: dead_letter stays dead_letter, because the ceiling decision was correct.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "reap.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def _dead_letter(
    store: DurableTaskStore, pool: DbPool, task_id: str, destination: str | None
) -> None:
    now = datetime.now(tz=UTC)
    await store.enqueue(DurableTask(
        task_id=task_id, owner_id=DEFAULT_PRINCIPAL_ID, goal="a goal",
        status="pending", destination=destination, channel="telegram",
        created_at=now, updated_at=now,
    ))
    await pool.execute(
        "UPDATE tasks SET status='dead_letter', delivered_at=NULL "
        "WHERE task_id=? AND owner_id=?",
        (task_id, DEFAULT_PRINCIPAL_ID),
    )


@pytest.mark.asyncio
async def test_an_UNADDRESSABLE_dead_letter_is_retired_not_escalated(
    pool: DbPool,
) -> None:
    """72 of the 74. Nobody is waiting, so nobody is owed a notification."""
    store = DurableTaskStore(pool)
    await _dead_letter(store, pool, "dl-noaddr", "telegram")  # a channel, no addressee

    resolved = await store.resolve_unannounced_dead_letters(limit=50)
    assert resolved == 1

    row = await store.get("dl-noaddr")
    assert row is not None
    assert row.status == "dead_letter", "the ceiling decision must stand"
    assert row.acknowledged_at is not None, "it must never be re-examined"


@pytest.mark.asyncio
async def test_an_ADDRESSED_dead_letter_is_announced_exactly_once(
    pool: DbPool,
) -> None:
    """Someone IS waiting. Telling them is the debt; telling them twice is spam."""
    store = DurableTaskStore(pool)
    await _dead_letter(store, pool, "dl-addr", "telegram:72055773")

    first = await store.resolve_unannounced_dead_letters(limit=50)
    assert first == 1

    second = await store.resolve_unannounced_dead_letters(limit=50)
    assert second == 0, (
        "the sweep re-examined a row it already resolved — on a 5s tick that is a "
        "notification every five seconds, for ever"
    )


@pytest.mark.asyncio
async def test_a_DELIVERED_dead_letter_is_left_alone(pool: DbPool) -> None:
    """The narrow predicate is the safety.

    A dead letter whose outcome DID reach someone owes nothing. Sweeping it would
    re-announce a stop the operator already heard about.
    """
    store = DurableTaskStore(pool)
    await _dead_letter(store, pool, "dl-done", "telegram:72055773")
    await pool.execute(
        "UPDATE tasks SET delivered_at=? WHERE task_id=? AND owner_id=?",
        (datetime.now(tz=UTC).isoformat(), "dl-done", DEFAULT_PRINCIPAL_ID),
    )

    assert await store.resolve_unannounced_dead_letters(limit=50) == 0


@pytest.mark.asyncio
async def test_a_LIVE_task_is_never_touched(pool: DbPool) -> None:
    """Work still in flight must not be retired out from under the loop."""
    store = DurableTaskStore(pool)
    now = datetime.now(tz=UTC)
    await store.enqueue(DurableTask(
        task_id="alive", owner_id=DEFAULT_PRINCIPAL_ID, goal="g", status="pending",
        destination="telegram:1", channel="telegram", created_at=now, updated_at=now,
    ))

    assert await store.resolve_unannounced_dead_letters(limit=50) == 0
    row = await store.get("alive")
    assert row is not None and row.status == "pending"
    assert row.acknowledged_at is None


@pytest.mark.asyncio
async def test_the_sweep_never_raises_into_the_loop(pool: DbPool) -> None:
    """A self-heal that can crash the loop is worse than the debt it drains."""
    store = DurableTaskStore(pool)
    await pool.close()  # the harshest failure available: no database at all

    assert await store.resolve_unannounced_dead_letters(limit=50) == 0
