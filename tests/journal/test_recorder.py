"""``journal.record`` is atomic with the change it records (spec 2.1, AD-24).

The whole point of ``journal.record(conn, event)`` is transactional-outbox
semantics: it takes the CALLER's own open ``DbPool.transaction()`` connection
and never opens or commits one of its own, so it commits or rolls back with
whatever else runs in that block. These two tests are the AC's atomicity proof
directly, with no other subsystem (no ``DurableTaskStore``) in the way --
``tests/pipeline/durable/test_journal_wiring.py`` proves the real wiring.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.journal import (
    ActorKind,
    JournalEvent,
    Outcome,
    RecordRef,
    record,
)
from stackowl.journal.task_events import TaskEnqueuedAttrs

pytestmark = pytest.mark.asyncio


def _event(**over: object) -> JournalEvent:
    defaults: dict[str, object] = {
        "type": "task.enqueued",
        "schema_version": 1,
        "actor_kind": ActorKind.AUTONOMOUS,
        "actor_id": "principal-default",
        "target_kind": ActorKind.OWNER,
        "target_id": "t1",
        "outcome": Outcome.PENDING,
        "record_ref": RecordRef(
            kind="sqlite", locator={"table": "tasks", "task_id": "t1"},
        ),
        "attrs": TaskEnqueuedAttrs(
            trigger_kind="chat", depends_on_count=0, max_attempts=30,
        ),
    }
    defaults.update(over)
    return JournalEvent(**defaults)  # type: ignore[arg-type]


class TestTheJournalRowIsAtomicWithTheChangeItRecords:
    async def test_a_forced_rollback_leaves_no_journal_row(self, tmp_db: DbPool) -> None:
        """AC: "a forced rollback leaves no event." The body calls ``record()``
        successfully and THEN raises -- the row it just inserted must not
        survive the rollback."""
        with pytest.raises(RuntimeError, match="simulated failure"):
            async with tmp_db.transaction() as conn:
                await record(conn, _event(target_id="rollback-1"))
                raise RuntimeError("simulated failure after record()")

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("rollback-1",)
        )
        assert rows == []

    async def test_a_commit_leaves_exactly_one_row_with_the_right_envelope(
        self, tmp_db: DbPool
    ) -> None:
        """AC: "a commit always leaves one" -- and the row carries the envelope
        fields the caller supplied, plus a UUIDv7 ``event_id``."""
        async with tmp_db.transaction() as conn:
            event_id = await record(conn, _event(target_id="commit-1"))

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("commit-1",)
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["event_id"] == event_id
        # UUIDv7 text: 36 chars, version nibble '7' at the documented offset.
        assert len(event_id) == 36
        assert event_id[14] == "7"
        assert row["type"] == "task.enqueued"
        assert row["schema_version"] == 1
        assert row["actor_kind"] == "autonomous"
        assert row["actor_id"] == "principal-default"
        assert row["target_kind"] == "owner"
        assert row["target_id"] == "commit-1"
        assert row["outcome"] == "pending"
        assert row["record_ref"] is not None
        assert row["attention"] is None  # Story 2.2 populates this, not 2.1
        assert row["intensity"] is None

    async def test_two_records_in_the_same_transaction_both_survive_the_commit(
        self, tmp_db: DbPool
    ) -> None:
        """Not a single-insert primitive by accident -- proves the shared
        connection/lock plumbing tolerates more than one call per block, the
        shape ``enqueue()`` needs (its own INSERT + UPDATE + one journal row)."""
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="multi-1"))
            await record(conn, _event(target_id="multi-2"))

        rows = await tmp_db.fetch_all(
            "SELECT target_id FROM journal_events WHERE target_id IN (?, ?)",
            ("multi-1", "multi-2"),
        )
        assert {r["target_id"] for r in rows} == {"multi-1", "multi-2"}
