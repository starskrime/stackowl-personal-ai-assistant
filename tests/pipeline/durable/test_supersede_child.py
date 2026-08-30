"""supersede_child tombstones a timed-out child (Story D1 §9)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    seed_schema(db_path)
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def test_supersede_child_sets_flag(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await store.create_child_task(
        child_task_id="c", parent_task_id="p", parent_owl="secretary",
        delegate_key="dk", goal="sub", owl_name="scout", channel="cli",
    )
    await store.supersede_child("c")
    rec = await store.get("c")
    assert rec.superseded is True
