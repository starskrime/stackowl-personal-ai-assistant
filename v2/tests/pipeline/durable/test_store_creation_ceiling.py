"""E2-S2 — DurableTaskStore round-trips creation_ceiling; NULL stays None."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.authz.bounds import BoundsSpec
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask


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


def _task(task_id: str, ceiling: BoundsSpec | None) -> DurableTask:
    now = datetime.now(tz=UTC)
    return DurableTask(
        task_id=task_id,
        owner_id="principal-default",
        goal="g",
        status="running",
        owl_name="o",
        channel="cli",
        creation_ceiling=ceiling,
        created_at=now,
        updated_at=now,
    )


async def test_create_get_roundtrips_ceiling(pool: DbPool) -> None:
    """A non-None BoundsSpec ceiling survives a create/get round-trip."""
    store = DurableTaskStore(pool, "principal-default")
    ceiling = BoundsSpec(tools=frozenset({"a", "b"}))
    await store.create(_task("task-ceil-1", ceiling))
    got = await store.get("task-ceil-1")
    assert got.creation_ceiling == ceiling


async def test_none_ceiling_persists_as_sql_null(pool: DbPool) -> None:
    """A None ceiling is stored as a SQL NULL (not the string 'null')."""
    store = DurableTaskStore(pool, "principal-default")
    await store.create(_task("task-ceil-2", None))
    # raw column must be NULL, not the string "null"
    rows = await pool.fetch_all(
        "SELECT creation_ceiling FROM tasks WHERE task_id = ?", ("task-ceil-2",)
    )
    assert rows[0]["creation_ceiling"] is None
    got = await store.get("task-ceil-2")
    assert got.creation_ceiling is None


async def test_deny_all_ceiling_roundtrips(pool: DbPool) -> None:
    """frozenset() (deny-all) is distinct from None (unrestricted) after round-trip."""
    store = DurableTaskStore(pool, "principal-default")
    ceiling = BoundsSpec(tools=frozenset())
    await store.create(_task("task-ceil-3", ceiling))
    got = await store.get("task-ceil-3")
    assert got.creation_ceiling is not None
    assert got.creation_ceiling.tools == frozenset()
