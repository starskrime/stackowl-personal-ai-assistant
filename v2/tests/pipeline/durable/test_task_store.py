"""DurableTaskStore — owner-scoped CRUD + status transitions (Pass 3a)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.exceptions import DurableTaskNotFoundError
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "tasks.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _task(task_id: str, owner_id: str, status: str = "pending") -> DurableTask:
    now = datetime.now(tz=UTC)
    return DurableTask(
        task_id=task_id,
        owner_id=owner_id,
        goal="do the thing",
        status=status,  # type: ignore[arg-type]
        created_at=now,
        updated_at=now,
    )


async def test_create_and_get_roundtrip(pool: DbPool) -> None:
    store = DurableTaskStore(pool, "principal-alice")
    await store.create(_task("t1", "principal-alice"))
    got = await store.get("t1")
    assert got.task_id == "t1"
    assert got.goal == "do the thing"
    assert got.status == "pending"
    assert got.current_step == 0


async def test_get_missing_raises(pool: DbPool) -> None:
    store = DurableTaskStore(pool, "principal-alice")
    with pytest.raises(DurableTaskNotFoundError):
        await store.get("nope")


async def test_cross_owner_get_is_invisible(pool: DbPool) -> None:
    alice = DurableTaskStore(pool, "principal-alice")
    bob = DurableTaskStore(pool, "principal-bob")
    await alice.create(_task("t1", "principal-alice"))
    # Bob cannot see alice's task — owner-scoped get raises.
    with pytest.raises(DurableTaskNotFoundError):
        await bob.get("t1")


async def test_list_is_owner_scoped(pool: DbPool) -> None:
    alice = DurableTaskStore(pool, "principal-alice")
    bob = DurableTaskStore(pool, "principal-bob")
    await alice.create(_task("a1", "principal-alice"))
    await alice.create(_task("a2", "principal-alice"))
    await bob.create(_task("b1", "principal-bob"))
    assert {t.task_id for t in await alice.list()} == {"a1", "a2"}
    assert {t.task_id for t in await bob.list()} == {"b1"}


async def test_list_filters_by_status(pool: DbPool) -> None:
    store = DurableTaskStore(pool, "principal-alice")
    await store.create(_task("p1", "principal-alice", status="pending"))
    await store.create(_task("r1", "principal-alice", status="running"))
    pendings = await store.list(status="pending")
    assert {t.task_id for t in pendings} == {"p1"}


async def test_update_status_persists_fields(pool: DbPool) -> None:
    store = DurableTaskStore(pool, "principal-alice")
    await store.create(_task("t1", "principal-alice"))
    await store.update_status(
        "t1", "running", current_step=3, thread_id="thread-xyz",
    )
    got = await store.get("t1")
    assert got.status == "running"
    assert got.current_step == 3
    assert got.thread_id == "thread-xyz"

    await store.update_status("t1", "completed", result="all done")
    done = await store.get("t1")
    assert done.status == "completed"
    assert done.result == "all done"
    # current_step preserved (not overwritten when omitted)
    assert done.current_step == 3


async def test_update_status_is_owner_scoped(pool: DbPool) -> None:
    alice = DurableTaskStore(pool, "principal-alice")
    bob = DurableTaskStore(pool, "principal-bob")
    await alice.create(_task("t1", "principal-alice"))
    # Bob updating "t1" must NOT touch alice's row (no matching owner row).
    await bob.update_status("t1", "failed", result="hijack")
    still = await alice.get("t1")
    assert still.status == "pending"
    assert still.result is None


async def test_different_owners_can_share_same_task_id(pool: DbPool) -> None:
    """Composite PK (owner_id, task_id) allows two owners to use the same task_id.

    This tests Fix 1 of Pass 3a code-review: the tasks table must be
    owner-scoped at the PK so a cross-owner task_id collision is impossible
    and each owner's get() sees only its own row.
    """
    alice = DurableTaskStore(pool, "principal-alice")
    bob = DurableTaskStore(pool, "principal-bob")
    # Both create a task with the identical task_id — must NOT raise a
    # UNIQUE/PK constraint error (composite PK keeps them on disjoint rows).
    await alice.create(_task("shared-id", "principal-alice", status="pending"))
    await bob.create(_task("shared-id", "principal-bob", status="running"))
    # Each owner's get() returns only its own row.
    alice_task = await alice.get("shared-id")
    bob_task = await bob.get("shared-id")
    assert alice_task.owner_id == "principal-alice"
    assert alice_task.status == "pending"
    assert bob_task.owner_id == "principal-bob"
    assert bob_task.status == "running"
