"""A row that is deleted leaves a record of what it contained.

Bakir, 2026-08-31, choosing how an UNCAPPED reconciliation sweep stays safe:
"Snapshot the deleted rows before deleting" — the sweep "writes the full row
contents it is about to delete into a durable record, then deletes". And on what
that record must contain: "Enough to reconstruct the row — table, primary key,
the full row contents, and WHY it was judged an orphan. A log saying 'deleted 148
rows' tells you the damage happened and nothing about undoing it."

That last sentence is a description of 2026-08-30: the purge audit row said
count=151 and named two backups that were never written, so the investigation
took hours and the rows were only recoverable by luck, through a DIFFERENT
facility (skill_audit.snapshot_json).

ONE TABLE, NOT A NEW ONE. ``audit_log`` is already the platform's general,
hash-chained audit — 11,053 rows across event types from consent decisions to job
failures, with ``integrity_hash``/``chain_version`` and a retention sweep that
audits its own pruning. Bakir: "a per-store audit table is that mistake in
miniature." So this is an event type on the log that exists, written through the
writer that exists, and it inherits the hash chain for free.

BEFORE, NOT AFTER, and the trade is stated rather than hidden: writing the record
first means a delete that then fails leaves a record of a deletion that did not
happen, which is misleading. Writing it after means a crash between the two loses
the only copy of the data. His instruction was explicit — "then deletes" — and
losing the data is the worse of the two.
"""

from __future__ import annotations

import json

import pytest

from stackowl.audit.deletions import DELETION_EVENT, record_deleted_rows
from stackowl.db.pool import DbPool
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def db(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    path = tmp_path / "audit.db"
    pool = DbPool(db_path=path)
    await pool.open()
    seed_schema(path)
    yield pool
    await pool.close()


async def _events(db: DbPool) -> list[dict]:
    rows = await db.fetch_all(
        "SELECT event_type, actor, target, details FROM audit_log WHERE event_type = ?",
        (DELETION_EVENT,),
    )
    return [dict(r) for r in rows]


# =========================================================================== #
# 1. Enough to reconstruct the row
# =========================================================================== #


async def test_the_record_carries_the_whole_row(db: DbPool) -> None:
    await record_deleted_rows(
        db,
        table="owl_dna",
        rows=[{"owl_name": "ghost", "curiosity": 0.7, "updated_at": 1000.0}],
        reason="owl deleted",
        actor="OwlStore.delete",
    )

    events = await _events(db)
    assert len(events) == 1
    details = json.loads(events[0]["details"])
    assert details["table"] == "owl_dna"
    assert details["reason"] == "owl deleted"
    assert details["rows"][0]["owl_name"] == "ghost"
    assert details["rows"][0]["curiosity"] == 0.7, (
        "the row contents were not preserved — this record cannot undo anything"
    )


async def test_the_record_says_which_table_and_who_did_it(db: DbPool) -> None:
    await record_deleted_rows(
        db, table="skill_ownership", rows=[{"owl_name": "ghost"}],
        reason="owl deleted", actor="OwlStore.delete",
    )
    events = await _events(db)
    assert events[0]["actor"] == "OwlStore.delete"
    assert events[0]["target"] == "skill_ownership"


async def test_it_joins_the_hash_chain(db: DbPool) -> None:
    """The record inherits tamper-evidence from the log it lives on."""
    await record_deleted_rows(
        db, table="owl_dna", rows=[{"owl_name": "ghost"}], reason="r", actor="a"
    )
    rows = await db.fetch_all(
        "SELECT integrity_hash, chain_version FROM audit_log WHERE event_type = ?",
        (DELETION_EVENT,),
    )
    assert rows[0]["integrity_hash"], "the row is not chained"
    assert rows[0]["chain_version"]


# =========================================================================== #
# 2. It can never cost the delete
# =========================================================================== #


async def test_nothing_is_written_for_an_empty_deletion(db: DbPool) -> None:
    """A sweep that finds nothing must not fill the audit with empty records."""
    await record_deleted_rows(db, table="owl_dna", rows=[], reason="r", actor="a")
    assert await _events(db) == []


async def test_a_broken_database_does_not_raise(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Bookkeeping must never cost the operation it is recording."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))

    class _Exploding:
        async def fetch_all(self, *a: object, **k: object) -> list[dict]:
            raise RuntimeError("db is gone")

        async def execute(self, *a: object, **k: object) -> None:
            raise RuntimeError("db is gone")

    await record_deleted_rows(
        _Exploding(),  # type: ignore[arg-type]
        table="owl_dna", rows=[{"owl_name": "ghost"}], reason="r", actor="a",
    )  # must not raise


async def test_a_huge_row_is_bounded(db: DbPool) -> None:
    """A body-carrying row must not be able to bloat the audit unboundedly."""
    await record_deleted_rows(
        db, table="skills", rows=[{"body_text": "x" * 500_000}], reason="r", actor="a"
    )
    events = await _events(db)
    assert len(events[0]["details"]) < 200_000, "the record is unbounded"
    assert json.loads(events[0]["details"])["truncated"] is True


async def test_an_unserialisable_value_does_not_lose_the_record(db: DbPool) -> None:
    """A BLOB column (embeddings) must not stop the rest being recorded."""
    await record_deleted_rows(
        db, table="skills", rows=[{"name": "x", "embedding": b"\x00\x01"}],
        reason="r", actor="a",
    )
    events = await _events(db)
    assert len(events) == 1
    assert json.loads(events[0]["details"])["rows"][0]["name"] == "x"
