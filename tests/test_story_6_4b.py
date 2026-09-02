"""Story 6.4 — MemoryCommand + MemoryBudgetEnforcer + migration 0015 tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stackowl.commands.memory_command import MemoryCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.config.settings import MemorySettings, Settings
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from tests._schema_template import seed_schema
from tests._story_6_4_helpers import (  # noqa: F401 — fixtures re-exported
    db,
    insert_committed,
    make_state,
    no_test_mode_guard,
    seed_committed_facts,
)


def _reset_registry() -> None:
    CommandRegistry.reset()


def _text(out: object) -> str:
    """Unwrap a CommandResponse to its text, or pass through a plain str."""
    return out.text if hasattr(out, "text") else out  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# MemoryCommand
# ---------------------------------------------------------------------------


async def test_memory_command_stats(db: DbPool) -> None:
    """D08.1 retargeted /memory at CURATED memory, so stats reports the curated
    files rather than a committed_facts count — a table that has held 0 rows since
    migration 0112 and would have reported 0 forever."""
    _reset_registry()
    from stackowl.memory.curated import USER_TARGET, CuratedMemory

    CuratedMemory().add(USER_TARGET, "the deploy region is eu-west-1", "permanent")
    bridge = SqliteMemoryBridge(db)
    settings = Settings(memory=MemorySettings())
    cmd = MemoryCommand.create_and_register(
        bridge=bridge, settings=settings, db=db, event_bus=EventBus()
    )
    out = await cmd.handle("stats", make_state())
    assert "curated" in out.lower(), out
    assert "1 entr" in out.lower(), f"the seeded entry must be counted: {out!r}"


async def test_memory_command_search_finds_a_curated_entry(db: DbPool) -> None:
    _reset_registry()
    from stackowl.memory.curated import USER_TARGET, CuratedMemory

    CuratedMemory().add(USER_TARGET, "alpha bravo charlie", "permanent")
    bridge = SqliteMemoryBridge(db)
    settings = Settings(memory=MemorySettings())
    cmd = MemoryCommand.create_and_register(
        bridge=bridge, settings=settings, db=db, event_bus=EventBus()
    )
    out = _text(await cmd.handle("search alpha", make_state()))
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


async def test_memory_command_forget_removes_a_SINGLE_match_immediately(
    db: DbPool,
) -> None:
    """PINS CURRENT BEHAVIOUR, and the behaviour CHANGED TWICE — see ESC-8.

    Two tests stood here asserting that `/memory forget X` asks for confirmation and
    leaves the entry alone until `YES` is supplied. D08.1 retargeted /memory at
    curated memory and the rewritten handler removes immediately: `_forget` calls
    `CuratedMemory.remove()` and reports the result. There is no confirmation step
    left to test.

    That was not obviously wrong for a SINGLE match — a curated entry is one line
    in a small text file, trivially re-added, where the old target was a durable
    fact. It was wrong for the substring case: `/memory forget deploy` took every
    entry mentioning deploy.

    ESC-8 resolved it by gating exactly that case. One unambiguous match is still
    immediate, which is what this test pins; more than one asks first and is
    covered in tests/commands/test_forget_confirms_multi_match.py.
    """
    _reset_registry()
    from stackowl.memory.curated import USER_TARGET, CuratedMemory

    CuratedMemory().add(USER_TARGET, "to forget one", "permanent")
    bridge = SqliteMemoryBridge(db)
    settings = Settings(memory=MemorySettings())
    cmd = MemoryCommand.create_and_register(
        bridge=bridge, settings=settings, db=db, event_bus=EventBus()
    )

    out = await cmd.handle("forget to forget one", make_state())

    assert "✓" in out, out
    assert not any(
        "to forget one" in e.text for e in CuratedMemory().entries(USER_TARGET)
    ), "the entry survived a forget that reported success"


async def test_memory_command_forget_reports_a_miss_rather_than_a_false_success(
    db: DbPool,
) -> None:
    """A forget that matched nothing must SAY so. Without this, a typo reads as a
    successful deletion and the user believes something is gone that is not."""
    _reset_registry()
    from stackowl.memory.curated import USER_TARGET, CuratedMemory

    CuratedMemory().add(USER_TARGET, "keep this one", "permanent")
    bridge = SqliteMemoryBridge(db)
    settings = Settings(memory=MemorySettings())
    cmd = MemoryCommand.create_and_register(
        bridge=bridge, settings=settings, db=db, event_bus=EventBus()
    )

    out = await cmd.handle("forget nothing matches this", make_state())

    assert "✗" in out, f"a miss must not report success: {out!r}"
    assert any(
        "keep this one" in e.text for e in CuratedMemory().entries(USER_TARGET)
    ), "an unrelated entry was removed"


async def test_memory_command_unknown_subcommand(db: DbPool) -> None:
    _reset_registry()
    bridge = SqliteMemoryBridge(db)
    settings = Settings(memory=MemorySettings())
    cmd = MemoryCommand.create_and_register(
        bridge=bridge, settings=settings, db=db, event_bus=EventBus()
    )
    out = await cmd.handle("nosuch", make_state())
    assert "usage" in out.lower()


# test_memory_command_reindex stood here. It drove `/memory reindex` into a fake
# LanceDB and asserted two vectors were upserted. D08.1 removed that slash
# subcommand (abb08e09) and D08.2 removed the vector store underneath it, so both
# the trigger and the effect are gone. The reindex capability itself survives as
# the `db reindex-memory` CLI command, repointed at the lessons corpus (ESC-5),
# and is covered by tests/cli/test_reindex_memory.py.


# ---------------------------------------------------------------------------
# MemoryBudgetEnforcer
# ---------------------------------------------------------------------------






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
    seed_schema(db_path)
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
