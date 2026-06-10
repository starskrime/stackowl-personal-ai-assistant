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
