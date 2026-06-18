"""DurableTask link fields round-trip through the store (Story D1 §4)."""

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
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def test_link_fields_default_to_root() -> None:
    now = datetime.now(tz=UTC)
    t = DurableTask(
        task_id="t", owner_id=DEFAULT_PRINCIPAL_ID, goal="g", status="pending",
        created_at=now, updated_at=now,
    )
    assert t.parent_task_id is None
    assert t.parent_owl is None
    assert t.delegate_key is None
    assert t.lease_owner is None
    assert t.superseded is False


async def test_child_link_fields_round_trip(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    now = datetime.now(tz=UTC)
    await store.create(
        DurableTask(
            task_id="child-1", owner_id=DEFAULT_PRINCIPAL_ID, goal="sub", status="running",
            parent_task_id="parent-1", parent_owl="secretary", delegate_key="dk-abc",
            lease_owner="lease-holder", superseded=True,
            created_at=now, updated_at=now,
        )
    )
    got = await store.get("child-1")
    assert got.parent_task_id == "parent-1"
    assert got.parent_owl == "secretary"
    assert got.delegate_key == "dk-abc"
    assert got.lease_owner == "lease-holder"
    assert got.superseded is True
