"""Migration 0151 (the COMMAND task kind) and ``claimable()``'s reserved
COMMAND slot (Story 4.3, AD-26).

Migration coverage mirrors ``tests/db/test_migration_0125_...``'s own
"schema_at(N-1), then apply, then check the real columns" shape rather than
asserting only that ``seed_schema`` (which already runs every migration,
0151 included) did not raise — a fresh database proves nothing an upgrading
device's own 0150-then-0151 path does not also need to prove.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from tests._migration_helpers import migrations_up_to, schema_at
from tests._schema_template import seed_schema

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask

# The migration tests below are synchronous (raw sqlite3); only the
# claimable()/get_by_command_id tests further down need asyncio — marked
# individually rather than via a blanket module-level `pytestmark` so the
# sync tests are not mis-flagged.


# =============================================================================
# Migration 0151 itself
# =============================================================================


def test_0151_applies_cleanly_on_top_of_0150() -> None:
    """The real upgrade path: a device sitting at 0150 applies 0151 next."""
    db_path = schema_at(150)
    conn = sqlite3.connect(db_path)
    cols_before = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    assert "kind" not in cols_before
    conn.close()

    results = MigrationRunner(
        db_path=db_path, migrations_dir=migrations_up_to(151),
    ).run()
    applied = [r for r in results if r.version == "0151"]
    assert len(applied) == 1
    assert applied[0].action == "applied"

    conn = sqlite3.connect(db_path)
    cols_after = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    for col in (
        "kind", "command_type", "command_payload", "command_id",
        "requester_kind", "nonce", "utterance_id",
    ):
        assert col in cols_after
    # `kind` defaults to 'goal' for every existing row — legacy rows are
    # byte-identical after the migration.
    conn.execute(
        "INSERT INTO tasks (task_id, owner_id, goal, status, current_step, "
        "created_at, updated_at) VALUES ('t1', 'o1', 'g', 'pending', 0, '', '')"
    )
    kind = conn.execute("SELECT kind FROM tasks WHERE task_id='t1'").fetchone()[0]
    assert kind == "goal"

    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "command_receipts" in tables
    conn.close()


def test_0151_is_idempotent_on_a_database_already_at_0151() -> None:
    """Applying 0151 to a fresh database that already has it is a no-op —
    ``ADD COLUMN`` is a one-shot statement, so the runner's own
    already-applied bookkeeping (schema_migrations) must be what prevents a
    second run from re-executing it (a second literal ``ADD COLUMN`` would
    raise "duplicate column name")."""
    db_path = schema_at(151)
    results = MigrationRunner(
        db_path=db_path, migrations_dir=migrations_up_to(151),
    ).run()
    matching = [r for r in results if r.version == "0151"]
    assert matching == [] or all(r.action == "skipped" for r in matching)


def test_command_id_uniqueness_is_enforced_but_only_when_set() -> None:
    db_path = schema_at(151)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tasks (task_id, owner_id, goal, status, current_step, "
        "kind, command_id, created_at, updated_at) VALUES "
        "('t1', 'o1', 'g', 'pending', 0, 'command', 'cmd-1', '', '')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tasks (task_id, owner_id, goal, status, current_step, "
            "kind, command_id, created_at, updated_at) VALUES "
            "('t2', 'o1', 'g', 'pending', 0, 'command', 'cmd-1', '', '')"
        )
    conn.rollback()
    # Two plain goal rows with NO command_id never collide (the index is
    # partial, scoped to `command_id IS NOT NULL`).
    conn.execute(
        "INSERT INTO tasks (task_id, owner_id, goal, status, current_step, "
        "created_at, updated_at) VALUES ('t3', 'o1', 'g', 'pending', 0, '', '')"
    )
    conn.execute(
        "INSERT INTO tasks (task_id, owner_id, goal, status, current_step, "
        "created_at, updated_at) VALUES ('t4', 'o1', 'g', 'pending', 0, '', '')"
    )
    conn.commit()
    conn.close()


# =============================================================================
# claimable()'s reserved COMMAND slot (AD-26)
# =============================================================================


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "command_kind.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _goal_task(n: int) -> DurableTask:
    return DurableTask(
        task_id=f"goal-{n}", goal=f"goal number {n}", status="pending",
    )


def _command_task() -> DurableTask:
    return DurableTask(
        task_id="cmd-task-1", goal="command:widget.op", status="pending",
        kind="command", command_type="widget.op", command_id="cmd-1",
        requester_kind="owner", trigger_kind="command",
    )


async def test_claimable_reserves_a_slot_for_a_pending_command_row(
    db: DbPool,
) -> None:
    store = DurableTaskStore(db)
    for n in range(5):
        await store.create(_goal_task(n))
    await store.create(_command_task())

    # limit=1: five goal rows would otherwise fill the one slot by
    # created_at order (the command row was enqueued LAST) — the reserved
    # slot must still surface it.
    batch = await store.claimable(limit=1)
    assert len(batch) == 1
    assert batch[0].kind == "command"
    assert batch[0].command_id == "cmd-1"


async def test_claimable_with_no_pending_command_returns_only_goal_rows(
    db: DbPool,
) -> None:
    store = DurableTaskStore(db)
    for n in range(3):
        await store.create(_goal_task(n))

    batch = await store.claimable(limit=2)
    assert len(batch) == 2
    assert all(t.kind == "goal" for t in batch)


async def test_claimable_never_duplicates_a_command_row_already_in_the_batch(
    db: DbPool,
) -> None:
    store = DurableTaskStore(db)
    await store.create(_command_task())

    batch = await store.claimable(limit=10)
    command_rows = [t for t in batch if t.kind == "command"]
    assert len(command_rows) == 1


async def test_get_by_command_id_round_trips(db: DbPool) -> None:
    store = DurableTaskStore(db)
    await store.create(_command_task())

    task = await store.get_by_command_id("cmd-1")
    assert task.task_id == "cmd-task-1"
    assert task.command_type == "widget.op"
