"""``record_command_execution`` — the AD-26 receipt guard, tested directly
(Story 4.3). Every future migrated mutator (4.7-4.10) reuses this ONE
function, and until now it was only exercised indirectly through
``tests/scheduler/test_pause_resume_are_commands.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.commands.spec.idempotency import record_command_execution
from stackowl.db.pool import DbPool
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "idempotency.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def test_first_call_for_a_command_id_returns_true(db: DbPool) -> None:
    async with db.transaction() as conn:
        newly = await record_command_execution(conn, "cmd-1", "scheduling.pause_job")
    assert newly is True

    rows = await db.fetch_all("SELECT * FROM command_receipts WHERE command_id = ?", ("cmd-1",))
    assert len(rows) == 1
    assert rows[0]["command_type"] == "scheduling.pause_job"


async def test_second_call_for_the_same_command_id_returns_false(db: DbPool) -> None:
    async with db.transaction() as conn:
        await record_command_execution(conn, "cmd-2", "scheduling.pause_job")

    async with db.transaction() as conn:
        newly = await record_command_execution(conn, "cmd-2", "scheduling.pause_job")

    assert newly is False
    rows = await db.fetch_all("SELECT * FROM command_receipts WHERE command_id = ?", ("cmd-2",))
    assert len(rows) == 1  # still exactly one row — no duplicate, no overwrite


async def test_two_different_command_ids_each_independently_return_true(
    db: DbPool,
) -> None:
    async with db.transaction() as conn:
        first = await record_command_execution(conn, "cmd-3", "scheduling.pause_job")
    async with db.transaction() as conn:
        second = await record_command_execution(conn, "cmd-4", "scheduling.resume_job")

    assert first is True
    assert second is True
    rows = await db.fetch_all("SELECT command_id FROM command_receipts ORDER BY command_id")
    assert [r["command_id"] for r in rows] == ["cmd-3", "cmd-4"]
