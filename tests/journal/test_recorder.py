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
from stackowl.exceptions import JournalAttentionSetByEmitterError
from stackowl.journal import (
    ActorKind,
    JournalEvent,
    Outcome,
    RecordRef,
    record,
)
from stackowl.journal.task_events import TaskDeadLetteredAttrs, TaskEnqueuedAttrs

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
        # Story 2.2: attention/intensity are computed by record() itself from
        # the registry -- task.enqueued is registered AMBIENT/no-intensity.
        assert row["attention"] == "ambient"
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


class TestAttentionIsComputedByRecordItselfNeverByTheEmitter:
    """Story 2.2, AD-5: ``record()`` computes ``attention``/``intensity`` from
    the registered ``EventTypeSpec`` -- emitters never classify."""

    async def test_an_ambient_type_is_recorded_ambient_with_no_intensity(
        self, tmp_db: DbPool
    ) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="attn-ambient-1"))

        rows = await tmp_db.fetch_all(
            "SELECT attention, intensity FROM journal_events WHERE target_id = ?",
            ("attn-ambient-1",),
        )
        assert rows[0]["attention"] == "ambient"
        assert rows[0]["intensity"] is None

    async def test_a_give_up_type_is_recorded_needs_you_high(self, tmp_db: DbPool) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, _event(
                type="task.dead_lettered",
                target_id="attn-dl-1",
                outcome=Outcome.DEAD_LETTERED,
                attrs=TaskDeadLetteredAttrs(
                    task_kind="chat", attempt_count=3, max_attempts=3,
                    lease_owner="w1", failure_class="auth", permanent=True,
                ),
            ))

        rows = await tmp_db.fetch_all(
            "SELECT attention, intensity FROM journal_events WHERE target_id = ?",
            ("attn-dl-1",),
        )
        assert rows[0]["attention"] == "needs_you"
        assert rows[0]["intensity"] == "high"

    @pytest.mark.tripwire
    async def test_an_emitter_that_pre_sets_attention_is_refused_before_any_sql_runs(
        self, tmp_db: DbPool
    ) -> None:
        """Cross-cutting invariant, not path-selected -- an emitter making its
        own ambient/needs-you judgment is exactly the per-surface disagreement
        this policy exists to prevent."""
        event = _event(target_id="attn-preset-1", attention="ambient")

        with pytest.raises(JournalAttentionSetByEmitterError):
            async with tmp_db.transaction() as conn:
                await record(conn, event)

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("attn-preset-1",),
        )
        assert rows == []
