"""DurableTaskStore.save_checkpoint / load_checkpoint — persistence (S1).

Tests:
* save then load returns the same blob.
* load on a task with no checkpoint returns None.
* Cross-owner isolation: owner A's checkpoint is invisible to owner B.
* save is idempotent — repeated saves overwrite (latest blob wins).
* Non-existent task_id returns None (invisible-is-missing semantics).

All tests use a real SQLite DB via MigrationRunner (migrations 0001–0046 applied).
No mocks — the real DbPool and OwnedRepository helpers are exercised.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.react_checkpoint import ReActCheckpoint, serialize
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "checkpoint.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _task(task_id: str, owner_id: str) -> DurableTask:
    now = datetime.now(tz=UTC)
    return DurableTask(
        task_id=task_id,
        owner_id=owner_id,
        goal="test goal",
        status="running",
        current_step=0,
        created_at=now,
        updated_at=now,
    )


def _blob(iteration: int = 0) -> str:
    cp = ReActCheckpoint(
        iteration=iteration,
        messages=[{"role": "user", "content": f"turn {iteration}"}],
        tool_call_records=[],
    )
    return serialize(cp)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


async def test_save_then_load_returns_same_blob(pool: DbPool) -> None:
    store = DurableTaskStore(pool, "principal-alice")
    await store.create(_task("t1", "principal-alice"))
    blob = _blob(iteration=2)
    await store.save_checkpoint("t1", blob)
    loaded = await store.load_checkpoint("t1")
    assert loaded == blob


async def test_load_no_checkpoint_returns_none(pool: DbPool) -> None:
    store = DurableTaskStore(pool, "principal-alice")
    await store.create(_task("t2", "principal-alice"))
    loaded = await store.load_checkpoint("t2")
    assert loaded is None


async def test_load_nonexistent_task_returns_none(pool: DbPool) -> None:
    store = DurableTaskStore(pool, "principal-alice")
    loaded = await store.load_checkpoint("does-not-exist")
    assert loaded is None


async def test_save_is_idempotent_latest_wins(pool: DbPool) -> None:
    store = DurableTaskStore(pool, "principal-alice")
    await store.create(_task("t3", "principal-alice"))
    first = _blob(iteration=1)
    second = _blob(iteration=5)
    await store.save_checkpoint("t3", first)
    await store.save_checkpoint("t3", second)
    loaded = await store.load_checkpoint("t3")
    assert loaded == second


# ---------------------------------------------------------------------------
# Cross-owner isolation
# ---------------------------------------------------------------------------


async def test_cross_owner_checkpoint_invisible(pool: DbPool) -> None:
    """Owner A saves a checkpoint; owner B cannot see it via load_checkpoint."""
    alice = DurableTaskStore(pool, "principal-alice")
    bob = DurableTaskStore(pool, "principal-bob")

    await alice.create(_task("shared-id", "principal-alice"))
    blob = _blob(iteration=3)
    await alice.save_checkpoint("shared-id", blob)

    # Bob has no task with this id — load_checkpoint returns None, not Alice's blob.
    result = await bob.load_checkpoint("shared-id")
    assert result is None


async def test_each_owner_has_independent_checkpoint(pool: DbPool) -> None:
    """Two owners with different task_ids each have their own checkpoint."""
    alice = DurableTaskStore(pool, "principal-alice")
    bob = DurableTaskStore(pool, "principal-bob")

    await alice.create(_task("ta", "principal-alice"))
    await bob.create(_task("tb", "principal-bob"))

    alice_blob = _blob(iteration=10)
    bob_blob = _blob(iteration=20)

    await alice.save_checkpoint("ta", alice_blob)
    await bob.save_checkpoint("tb", bob_blob)

    assert await alice.load_checkpoint("ta") == alice_blob
    assert await bob.load_checkpoint("tb") == bob_blob
    # Cross checks — neither can see the other's data.
    assert await alice.load_checkpoint("tb") is None
    assert await bob.load_checkpoint("ta") is None


async def test_cross_owner_write_cannot_corrupt_original(pool: DbPool) -> None:
    """Bob calling save_checkpoint on Alice's task_id must match 0 rows (write isolation).

    Alice creates a task and saves a checkpoint.  Bob (a different owner) then
    attempts to overwrite that same task_id.  Because save_checkpoint carries an
    owner_id predicate, Bob's UPDATE matches no rows and Alice's original blob
    must be intact afterward.
    """
    alice = DurableTaskStore(pool, "principal-alice")
    bob = DurableTaskStore(pool, "principal-bob")

    # Set up: Alice's task with an original checkpoint.
    await alice.create(_task("alice-task-id", "principal-alice"))
    original_blob = _blob(iteration=7)
    await alice.save_checkpoint("alice-task-id", original_blob)

    # Bob attempts to overwrite Alice's task checkpoint (should silently match 0 rows).
    evil_blob = _blob(iteration=99)
    await bob.save_checkpoint("alice-task-id", evil_blob)

    # Alice's checkpoint must be unchanged.
    assert await alice.load_checkpoint("alice-task-id") == original_blob
