"""PROV-3 (F093) — DurableTaskStore persists cumulative cost across resume."""

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
    db_path = tmp_path / "cost.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _task(task_id: str) -> DurableTask:
    now = datetime.now(tz=UTC)
    return DurableTask(
        task_id=task_id, owner_id="principal-default", goal="g",
        status="running", owl_name="o", channel="cli",
        created_at=now, updated_at=now,
    )


async def test_new_task_defaults_to_zero_accumulated_cost(pool: DbPool) -> None:
    store = DurableTaskStore(pool, "principal-default")
    await store.create(_task("c-1"))
    assert await store.get_accumulated_cost("c-1") == 0.0


async def test_set_then_get_roundtrips_cumulative_cost(pool: DbPool) -> None:
    store = DurableTaskStore(pool, "principal-default")
    await store.create(_task("c-2"))
    await store.set_accumulated_cost("c-2", 0.42)
    assert abs(await store.get_accumulated_cost("c-2") - 0.42) < 1e-9
    # A later attempt overwrites with the new ABSOLUTE cumulative total (monotonic).
    await store.set_accumulated_cost("c-2", 0.90)
    assert abs(await store.get_accumulated_cost("c-2") - 0.90) < 1e-9


async def test_missing_task_reads_zero_not_error(pool: DbPool) -> None:
    store = DurableTaskStore(pool, "principal-default")
    assert await store.get_accumulated_cost("nope") == 0.0


async def test_set_on_missing_task_fails_loud(pool: DbPool) -> None:
    store = DurableTaskStore(pool, "principal-default")
    with pytest.raises(DurableTaskNotFoundError):
        await store.set_accumulated_cost("ghost", 1.0)


async def test_cross_owner_cost_is_isolated(pool: DbPool) -> None:
    a = DurableTaskStore(pool, "owner-a")
    now = datetime.now(tz=UTC)
    await a.create(DurableTask(
        task_id="shared", owner_id="owner-a", goal="g", status="running",
        owl_name="o", channel="cli", created_at=now, updated_at=now,
    ))
    await a.set_accumulated_cost("shared", 5.0)
    # A store bound to a different owner cannot see owner-a's task → reads 0.0.
    b = DurableTaskStore(pool, "owner-b")
    assert await b.get_accumulated_cost("shared") == 0.0
