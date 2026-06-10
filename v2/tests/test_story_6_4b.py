"""Story 6.4 — MemoryCommand + MemoryBudgetEnforcer + migration 0015 tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stackowl.commands.memory_command import MemoryCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.config.settings import MemorySettings, Settings
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.memory.budget_enforcer import MemoryBudgetEnforcer
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.scheduler.job import Job

from tests._story_6_4_helpers import (  # noqa: F401 — fixtures re-exported
    FakeLanceDB,
    db,
    insert_committed,
    make_state,
    no_test_mode_guard,
    seed_committed_facts,
)


def _reset_registry() -> None:
    CommandRegistry.reset()


# ---------------------------------------------------------------------------
# MemoryCommand
# ---------------------------------------------------------------------------


async def test_memory_command_stats(db: DbPool) -> None:
    _reset_registry()
    await insert_committed(db, "stats-1", "x" * 100)
    bridge = SqliteMemoryBridge(db)
    settings = Settings(memory=MemorySettings())
    cmd = MemoryCommand.create_and_register(
        bridge=bridge, settings=settings, db=db, event_bus=EventBus()
    )
    out = await cmd.handle("stats", make_state())
    assert "committed" in out.lower()
    assert "1" in out


async def test_memory_command_search_calls_recall(db: DbPool) -> None:
    _reset_registry()
    await insert_committed(db, "search-1", "alpha bravo charlie")
    bridge = SqliteMemoryBridge(db)
    settings = Settings(memory=MemorySettings())
    cmd = MemoryCommand.create_and_register(
        bridge=bridge, settings=settings, db=db, event_bus=EventBus()
    )
    out = await cmd.handle("search alpha", make_state())
    assert "alpha bravo charlie" in out


async def test_memory_command_search_no_query(db: DbPool) -> None:
    _reset_registry()
    bridge = SqliteMemoryBridge(db)
    settings = Settings(memory=MemorySettings())
    cmd = MemoryCommand.create_and_register(
        bridge=bridge, settings=settings, db=db, event_bus=EventBus()
    )
    out = await cmd.handle("search", make_state())
    assert "usage" in out.lower() or "query" in out.lower()


async def test_memory_command_budget(db: DbPool) -> None:
    _reset_registry()
    await insert_committed(db, "bud-1", "x" * 500)
    bridge = SqliteMemoryBridge(db)
    settings = Settings(memory=MemorySettings(per_user_ceiling_bytes=10_000_000))
    cmd = MemoryCommand.create_and_register(
        bridge=bridge, settings=settings, db=db, event_bus=EventBus()
    )
    out = await cmd.handle("budget", make_state())
    assert "%" in out


async def test_memory_command_delete_requires_confirmation(db: DbPool) -> None:
    _reset_registry()
    await insert_committed(db, "del-cmd-1", "to delete")
    bridge = SqliteMemoryBridge(db)
    settings = Settings(memory=MemorySettings())
    cmd = MemoryCommand.create_and_register(
        bridge=bridge, settings=settings, db=db, event_bus=EventBus()
    )
    out = await cmd.handle("delete del-cmd-1", make_state())
    assert "confirm" in out.lower() or "yes" in out.lower()
    rows = await db.fetch_all("SELECT fact_id FROM committed_facts")
    assert any(r["fact_id"] == "del-cmd-1" for r in rows)


async def test_memory_command_delete_with_confirmation(db: DbPool) -> None:
    _reset_registry()
    await insert_committed(db, "del-cmd-2", "to delete")
    bridge = SqliteMemoryBridge(db)
    settings = Settings(memory=MemorySettings())
    cmd = MemoryCommand.create_and_register(
        bridge=bridge, settings=settings, db=db, event_bus=EventBus()
    )
    out = await cmd.handle("delete del-cmd-2 YES", make_state())
    assert "del-cmd-2" in out or "✓" in out or "removed" in out.lower()
    rows = await db.fetch_all("SELECT fact_id FROM committed_facts")
    assert not any(r["fact_id"] == "del-cmd-2" for r in rows)


async def test_memory_command_unknown_subcommand(db: DbPool) -> None:
    _reset_registry()
    bridge = SqliteMemoryBridge(db)
    settings = Settings(memory=MemorySettings())
    cmd = MemoryCommand.create_and_register(
        bridge=bridge, settings=settings, db=db, event_bus=EventBus()
    )
    out = await cmd.handle("nosuch", make_state())
    assert "usage" in out.lower()


async def test_memory_command_reindex(db: DbPool) -> None:
    _reset_registry()
    emb_blob = np.array([0.1, 0.2, 0.3, 0.4], dtype="<f4").tobytes()
    now = datetime.now(UTC).isoformat()
    for fid in ["rx-1", "rx-2"]:
        await db.execute(
            """INSERT INTO committed_facts
                   (fact_id, content, embedding, embedding_model, committed_at,
                    source_type, source_ref, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (fid, f"content-{fid}", emb_blob, "stub", now, "conv", "s", "[]"),
        )
    fake = FakeLanceDB()
    bridge = SqliteMemoryBridge(db, lancedb=fake)  # type: ignore[arg-type]
    settings = Settings(memory=MemorySettings())
    cmd = MemoryCommand.create_and_register(
        bridge=bridge,
        settings=settings,
        db=db,
        event_bus=EventBus(),
        lancedb=fake,  # type: ignore[arg-type]
    )
    out = await cmd.handle("reindex", make_state())
    assert "2" in out
    assert len(fake.upserts) == 2


# ---------------------------------------------------------------------------
# MemoryBudgetEnforcer
# ---------------------------------------------------------------------------


async def test_budget_enforcer_no_op_when_under(
    db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_committed_facts(db, n=2, content_size=100)
    monkeypatch.setenv("STACKOWL_MEMORY__PER_USER_CEILING_BYTES", "10000000")
    settings = Settings()
    enforcer = MemoryBudgetEnforcer(db=db, settings=settings)
    job = Job(
        job_id="b-1",
        handler_name=enforcer.handler_name,
        schedule="manual",
        idempotency_key="budget:1",
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        retry_count=0,
    )
    result = await enforcer.execute(job)
    assert result.success is True
    assert result.output is not None
    assert "0" in result.output
    rows = await db.fetch_all("SELECT COUNT(*) AS cnt FROM committed_facts")
    assert rows[0]["cnt"] == 2


async def test_budget_enforcer_prunes_when_over(
    db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 5 rows × 300_000 bytes = ~1.5MB, ceiling = 1_000_000 → prunes until under.
    await seed_committed_facts(db, n=5, content_size=300_000, confidence=0.1)
    monkeypatch.setenv("STACKOWL_MEMORY__PER_USER_CEILING_BYTES", "1000000")
    settings = Settings()
    enforcer = MemoryBudgetEnforcer(db=db, settings=settings)
    job = Job(
        job_id="b-2",
        handler_name=enforcer.handler_name,
        schedule="manual",
        idempotency_key="budget:2",
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        retry_count=0,
    )
    result = await enforcer.execute(job)
    assert result.success is True
    rows = await db.fetch_all("SELECT SUM(length(content)) AS s FROM committed_facts")
    total = rows[0]["s"] or 0
    assert total <= 1_000_000


# ---------------------------------------------------------------------------
# Migration 0015
# ---------------------------------------------------------------------------


def test_migration_0015_exists() -> None:
    p = (
        Path(__file__).parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
        / "0015_reindex_queue.sql"
    )
    assert p.exists()
    sql = p.read_text(encoding="utf-8")
    assert "reindex_queue" in sql.lower()


def test_migration_count_is_15(migration_runner: Any) -> None:
    # Name kept historical for log searchability; expected count is now derived
    # dynamically from the actual .sql files on disk (no more manual bumps on
    # every new migration).
    migrations_dir = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
    )
    expected = len(sorted(migrations_dir.glob("*.sql")))
    results = migration_runner.run()
    assert len(results) == expected


async def test_reindex_queue_table_present(tmp_path: Path) -> None:
    db_path = tmp_path / "rq.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        rows = await pool.fetch_all(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='reindex_queue'"
        )
        assert len(rows) == 1
    finally:
        await pool.close()
