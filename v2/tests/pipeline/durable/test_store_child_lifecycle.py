"""DurableTaskStore child-lifecycle methods (Story D1 §7)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def test_create_child_task_returns_record(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    rec = await store.create_child_task(
        child_task_id="child-1", parent_task_id="parent-1", parent_owl="secretary",
        delegate_key="dk-1", goal="sub", owl_name="scout", channel="cli",
    )
    assert rec.task_id == "child-1"
    assert rec.parent_task_id == "parent-1"
    assert rec.parent_owl == "secretary"
    assert rec.delegate_key == "dk-1"
    assert rec.status == "running"


async def test_create_child_task_is_idempotent_under_race(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)

    async def _create() -> str:
        rec = await store.create_child_task(
            child_task_id="child-x", parent_task_id="parent-1", parent_owl="secretary",
            delegate_key="dk-x", goal="sub", owl_name="scout", channel="cli",
        )
        return rec.task_id

    a, b = await asyncio.gather(_create(), _create())
    assert a == b == "child-x"
    rows = await pool.fetch_all("SELECT task_id FROM tasks WHERE task_id = 'child-x'", ())
    assert len(rows) == 1, f"exactly one row expected, got {rows}"


async def test_claim_child_lease_first_wins_second_loses(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await store.create_child_task(
        child_task_id="child-l", parent_task_id="p", parent_owl="secretary",
        delegate_key="dk-l", goal="sub", owl_name="scout", channel="cli",
    )
    first = await store.claim_child_lease("child-l", lease_owner="live-parent")
    second = await store.claim_child_lease("child-l", lease_owner="recovery")
    # A same-owner re-claim also loses: once lease_owner is set, the
    # `lease_owner IS NULL` CAS predicate no longer matches, so it returns False
    # and leaves the existing holder untouched (idempotent, no double-execute).
    re_claim = await store.claim_child_lease("child-l", lease_owner="live-parent")
    assert first is True
    assert second is False
    assert re_claim is False
    rec = await store.get("child-l")
    assert rec.lease_owner == "live-parent"


async def test_terminalize_child_sets_terminal_status(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await store.create_child_task(
        child_task_id="child-t", parent_task_id="p", parent_owl="secretary",
        delegate_key="dk-t", goal="sub", owl_name="scout", channel="cli",
    )
    await store.terminalize_child("child-t", "completed", result="answer")
    rec = await store.get("child-t")
    assert rec.status == "completed"
    assert rec.result == "answer"


async def test_list_children_returns_only_that_parents_children(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await store.create_child_task(
        child_task_id="c-a", parent_task_id="P", parent_owl="secretary",
        delegate_key="dk-a", goal="a", owl_name="scout", channel="cli",
    )
    await store.create_child_task(
        child_task_id="c-b", parent_task_id="OTHER", parent_owl="secretary",
        delegate_key="dk-b", goal="b", owl_name="scout", channel="cli",
    )
    kids = await store.list_children("P")
    assert {k.task_id for k in kids} == {"c-a"}


async def test_zombie_children_under_terminal_parents(pool: DbPool) -> None:
    from datetime import UTC, datetime

    from stackowl.pipeline.durable.task import DurableTask

    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    now = datetime.now(tz=UTC)
    # Terminal parent.
    await store.create(DurableTask(
        task_id="P", owner_id=DEFAULT_PRINCIPAL_ID, goal="g", status="completed",
        created_at=now, updated_at=now,
    ))
    # A still-running child under the terminal parent ⇒ a zombie.
    await store.create_child_task(
        child_task_id="zombie", parent_task_id="P", parent_owl="secretary",
        delegate_key="dk-z", goal="sub", owl_name="scout", channel="cli",
    )
    # A child under a still-running parent ⇒ NOT a zombie.
    await store.create(DurableTask(
        task_id="P2", owner_id=DEFAULT_PRINCIPAL_ID, goal="g", status="running",
        created_at=now, updated_at=now,
    ))
    await store.create_child_task(
        child_task_id="live-kid", parent_task_id="P2", parent_owl="secretary",
        delegate_key="dk-lk", goal="sub", owl_name="scout", channel="cli",
    )
    zombies = await store.list_zombie_children()
    assert {z.task_id for z in zombies} == {"zombie"}
