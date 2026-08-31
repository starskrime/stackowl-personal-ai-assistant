"""The self-healing sweep: identity rows whose owner no longer exists.

Bakir, 2026-08-31, choosing between a one-off migration and a standing sweep:
"A self-healing sweep, not a one-off — a scheduled reconciler that deletes rows
whose owner no longer exists, running until it finds nothing. Fixes today's
damage AND anything a future gap creates." Daily. And on bounding it: "No cap,
just log every deletion", with recoverability supplied instead by "snapshot the
deleted rows before deleting" — which shipped first, deliberately, because an
uncapped deleting sweep with no snapshot is the 2026-08-30 purge with a timer.

WHAT IT REMOVES, measured on the live database the day it was written:
    owl_dna with no owls row          6
    owl_dna_authored with no owls row 11
    dna_checkpoints with no owls row  1
    skill_ownership, skill gone     110

WHAT IT DOES NOT TOUCH, and why that is not an omission. ``skills_fts`` holds 147
rows for skills that no longer exist, but it is an INDEX, not identity — the
repair for a stale index is a RESYNC, and deleting from an FTS table row by row
is how FTS indexes get corrupted. Different mechanism, different item.

THE ONE GUARD, AND IT IS NOT A CAP. If the OWNER table is empty, every dependent
row looks orphaned and an uncapped sweep would delete all of them. Bakir's own
rule: "an empty table is a QUESTION, not an answer." He rejected a cap on VOLUME;
this is a precondition against an obviously broken premise, which is a different
thing and would have caught the exact query error that makes a sweep dangerous.

IT DELETES OWLS THROUGH OwlStore.delete, not with its own SQL — so the cascade
and the deletion record come for free, and there is no second copy of "what it
means to remove an owl".
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.handlers.orphan_reconciliation import (
    OrphanReconciliationHandler,
)
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def db(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    path = tmp_path / "sweep.db"
    pool = DbPool(db_path=path)
    await pool.open()
    seed_schema(path)
    yield pool
    await pool.close()


async def _owl_row(db: DbPool, name: str) -> None:
    await db.execute(
        "INSERT INTO owls (name, display_name, role, lifecycle, origin, manifest_json,"
        " owner_id, updated_at) VALUES (?, '', 'generic', 'on_demand', '', '{}',"
        " 'principal-default', '2026-08-31T00:00:00Z')",
        (name,),
    )


async def _dna(db: DbPool, name: str) -> None:
    await db.execute(
        "INSERT INTO owl_dna (owl_name, updated_at) VALUES (?, 1000.0)", (name,)
    )


async def _skill(db: DbPool, name: str) -> None:
    await db.execute(
        "INSERT INTO skills (name, source, path, description, when_to_use, body_text,"
        " loaded_at, updated_at) VALUES (?, 'learned', '/p', 'd', 'w', 'b', 1.0, 1.0)",
        (name,),
    )


async def _ownership(db: DbPool, owl: str, skill: str) -> None:
    await db.execute(
        "INSERT INTO skill_ownership (owner_id, owl_name, skill_name, attached_at)"
        " VALUES ('principal-default', ?, ?, 1000.0)",
        (owl, skill),
    )


async def _count(db: DbPool, sql: str) -> int:
    # Rows are keyed by COLUMN NAME, not position — rows[0][0] raises KeyError.
    rows = await db.fetch_all(sql.replace("COUNT(*)", "COUNT(*) AS c", 1))
    return int(rows[0]["c"]) if rows else 0


# =========================================================================== #
# 1. It removes what has no owner
# =========================================================================== #


async def test_dna_for_an_owl_that_no_longer_exists_is_removed(db: DbPool) -> None:
    await _owl_row(db, "alive")
    await _skill(db, "a-real-skill")
    await _dna(db, "alive")
    await _dna(db, "ghost")  # no owls row

    result = await OrphanReconciliationHandler(db).sweep()

    assert await _count(db, "SELECT COUNT(*) FROM owl_dna WHERE owl_name='ghost'") == 0
    assert await _count(db, "SELECT COUNT(*) FROM owl_dna WHERE owl_name='alive'") == 1
    assert result.deleted >= 1


async def test_ownership_of_a_skill_that_no_longer_exists_is_removed(db: DbPool) -> None:
    """Phantom ownership: the boot hydrator re-attaches dead skill names forever."""
    await _owl_row(db, "alive")
    # The skills table must be POPULATED, or the empty-owner guard correctly
    # refuses this rule — a fresh install with no skills is not permission to
    # delete every ownership row.
    await _skill(db, "a-real-skill")
    await _ownership(db, "alive", "a-real-skill")
    await _ownership(db, "alive", "long-gone-skill")

    await OrphanReconciliationHandler(db).sweep()

    assert await _count(
        db, "SELECT COUNT(*) FROM skill_ownership WHERE skill_name='long-gone-skill'"
    ) == 0


async def test_it_records_what_it_deleted_before_deleting(db: DbPool) -> None:
    """The condition Bakir made an UNCAPPED sweep acceptable on."""
    import json

    from stackowl.audit.deletions import DELETION_EVENT

    await _owl_row(db, "alive")
    await _skill(db, "a-real-skill")
    await _dna(db, "ghost")

    await OrphanReconciliationHandler(db).sweep()

    rows = await db.fetch_all(
        "SELECT details FROM audit_log WHERE event_type = ?", (DELETION_EVENT,)
    )
    assert rows, "an uncapped sweep deleted rows and recorded nothing"
    assert any("ghost" in str(r["details"]) for r in rows)
    assert any(json.loads(str(r["details"]))["rows"] for r in rows)


# =========================================================================== #
# 2. The guard that is not a cap
# =========================================================================== #


async def test_an_empty_owner_table_stops_the_sweep_dead(db: DbPool) -> None:
    """"An empty table is a QUESTION, not an answer."

    With zero owls, EVERY dna row looks orphaned. A sweep that proceeded would
    delete the lot on what is far more likely a broken read than a real state.
    """
    await _dna(db, "a")
    await _dna(db, "b")

    result = await OrphanReconciliationHandler(db).sweep()

    assert result.refused, "the sweep ran against an empty owner table"
    assert await _count(db, "SELECT COUNT(*) FROM owl_dna") == 2, "it deleted anyway"


async def test_a_healthy_owner_table_does_not_trigger_the_guard(db: DbPool) -> None:
    """BOTH owner tables must be populated — the sweep spans owls AND skills."""
    await _owl_row(db, "alive")
    await _dna(db, "alive")
    await _skill(db, "a-real-skill")
    await _ownership(db, "alive", "a-real-skill")

    result = await OrphanReconciliationHandler(db).sweep()

    assert not result.refused
    assert result.deleted == 0, "it deleted something that had an owner"


# =========================================================================== #
# 3. Idempotence and safety
# =========================================================================== #


async def test_running_twice_finds_nothing_the_second_time(db: DbPool) -> None:
    """"Running until it finds nothing" is only meaningful if it converges."""
    await _owl_row(db, "alive")
    await _skill(db, "a-real-skill")
    await _dna(db, "ghost")

    first = await OrphanReconciliationHandler(db).sweep()
    second = await OrphanReconciliationHandler(db).sweep()

    assert first.deleted >= 1
    assert second.deleted == 0


async def test_a_clean_database_deletes_nothing_and_records_nothing(db: DbPool) -> None:
    from stackowl.audit.deletions import DELETION_EVENT

    await _owl_row(db, "alive")
    await _dna(db, "alive")
    await _ownership(db, "alive", "real-skill")
    await db.execute(
        "INSERT INTO skills (name, source, path, description, when_to_use, body_text,"
        " loaded_at, updated_at) VALUES ('real-skill','learned','/p','d','w','b',1.0,1.0)"
    )

    result = await OrphanReconciliationHandler(db).sweep()

    assert result.deleted == 0
    assert await _count(
        db, f"SELECT COUNT(*) FROM audit_log WHERE event_type='{DELETION_EVENT}'"
    ) == 0


async def test_a_broken_table_does_not_stop_the_others(db: DbPool) -> None:
    """One unreadable table must not abandon the rest of the sweep."""
    await _owl_row(db, "alive")
    await _skill(db, "a-real-skill")
    await _dna(db, "ghost")
    handler = OrphanReconciliationHandler(db)
    handler._RULES = (*handler._RULES, ("nonexistent_table", "col", "SELECT bad"))  # type: ignore[attr-defined]

    result = await handler.sweep()  # must not raise

    assert await _count(db, "SELECT COUNT(*) FROM owl_dna WHERE owl_name='ghost'") == 0
    assert result.errors >= 1
