"""E2-S3 — DurableTaskStore round-trips task_envelope; NULL ⇄ None."""

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
    db_path = tmp_path / "envelope.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _task(task_id: str, envelope: BoundsSpec | None) -> DurableTask:
    now = datetime.now(tz=UTC)
    return DurableTask(
        task_id=task_id,
        owner_id="principal-default",
        goal="g",
        status="running",
        owl_name="o",
        channel="cli",
        task_envelope=envelope,
        created_at=now,
        updated_at=now,
    )


async def test_roundtrips_envelope(pool: DbPool) -> None:
    """A non-None BoundsSpec envelope survives a create/get round-trip."""
    store = DurableTaskStore(pool, "principal-default")
    env = BoundsSpec(tools=frozenset({"a", "tool_search"}))
    await store.create(_task("task-env-1", env))
    assert (await store.get("task-env-1")).task_envelope == env


async def test_none_envelope_is_sql_null(pool: DbPool) -> None:
    """A None envelope is stored as a SQL NULL (not the string 'null')."""
    store = DurableTaskStore(pool, "principal-default")
    await store.create(_task("task-env-2", None))
    rows = await pool.fetch_all(
        "SELECT task_envelope FROM tasks WHERE task_id = ?", ("task-env-2",)
    )
    assert rows[0]["task_envelope"] is None
    assert (await store.get("task-env-2")).task_envelope is None


async def test_deny_all_envelope_roundtrips(pool: DbPool) -> None:
    """frozenset() (deny-all) is distinct from None (unrestricted) after round-trip."""
    store = DurableTaskStore(pool, "principal-default")
    env = BoundsSpec(tools=frozenset())
    await store.create(_task("task-env-3", env))
    got = await store.get("task-env-3")
    assert got.task_envelope is not None
    assert got.task_envelope.tools == frozenset()
