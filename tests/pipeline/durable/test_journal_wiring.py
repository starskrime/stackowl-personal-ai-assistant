"""Every durable-task lifecycle transition records its own journal event, in
the same transaction as the change (spec 2.1 AC).

Drives real ``enqueue``/``claim``/``mark_delivered``/``mark_completed_unaddressed``/
``fail_and_requeue`` calls against a real migrated database and asserts exactly
one matching ``journal_events`` row lands per transition, with ``record_ref``
pointing at the task row.
"""

from __future__ import annotations

import json

import pytest

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask

pytestmark = pytest.mark.asyncio


async def _journal_rows(db: DbPool, event_type: str, task_id: str) -> list[dict]:
    return await db.fetch_all(
        "SELECT * FROM journal_events WHERE type = ? AND target_id = ?",
        (event_type, task_id),
    )


class TestEnqueueRecordsOneEvent:
    async def test_enqueue_writes_exactly_one_task_enqueued_row(
        self, tmp_db: DbPool
    ) -> None:
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(
            task_id="wire-enq-1", goal="answer", status="pending",
            trigger_kind="chat", max_attempts=7,
        ))

        rows = await _journal_rows(tmp_db, "task.enqueued", "wire-enq-1")
        assert len(rows) == 1
        row = rows[0]
        assert row["outcome"] == "pending"
        ref = json.loads(row["record_ref"])
        assert ref["kind"] == "sqlite"
        assert ref["locator"]["task_id"] == "wire-enq-1"
        attrs = json.loads(row["attrs"])
        assert attrs["trigger_kind"] == "chat"
        assert attrs["max_attempts"] == 7

    async def test_enqueue_rolled_back_by_a_duplicate_task_id_leaves_no_row(
        self, tmp_db: DbPool
    ) -> None:
        """The insert half of ``enqueue`` can itself fail (a duplicate primary
        key) -- proves the journal row never lands independently of the state
        change it describes, in either direction."""
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(task_id="wire-dup-1", goal="g", status="pending"))

        with pytest.raises(Exception):  # noqa: B017 — sqlite IntegrityError, not our concern here
            await store.enqueue(DurableTask(task_id="wire-dup-1", goal="g2", status="pending"))

        rows = await _journal_rows(tmp_db, "task.enqueued", "wire-dup-1")
        assert len(rows) == 1  # only the FIRST enqueue's row


class TestClaimRecordsOnlyOnAWin:
    async def test_a_winning_claim_writes_exactly_one_task_claimed_row(
        self, tmp_db: DbPool
    ) -> None:
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(task_id="wire-claim-1", goal="g", status="pending"))

        won = await store.claim("wire-claim-1", worker="worker-a", lease_seconds=60)
        assert won is True

        rows = await _journal_rows(tmp_db, "task.claimed", "wire-claim-1")
        assert len(rows) == 1
        attrs = json.loads(rows[0]["attrs"])
        assert attrs["lease_owner"] == "worker-a"
        assert attrs["lease_seconds"] == 60
        assert rows[0]["actor_id"] == "worker-a"

    async def test_a_losing_claim_writes_no_event(self, tmp_db: DbPool) -> None:
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(task_id="wire-claim-2", goal="g", status="pending"))
        assert await store.claim("wire-claim-2", worker="worker-a") is True

        # Second claim loses the compare-and-set (already 'running').
        assert await store.claim("wire-claim-2", worker="worker-b") is False

        rows = await _journal_rows(tmp_db, "task.claimed", "wire-claim-2")
        assert len(rows) == 1  # only worker-a's winning claim
        assert rows[0]["actor_id"] == "worker-a"


class TestFinishingRecordsTheCompletionMode:
    async def test_mark_delivered_records_completion_mode_delivered(
        self, tmp_db: DbPool
    ) -> None:
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(task_id="wire-fin-1", goal="g", status="pending"))
        await store.claim("wire-fin-1", worker="w1")

        await store.mark_delivered("wire-fin-1", result="the answer is 42")

        rows = await _journal_rows(tmp_db, "task.finished", "wire-fin-1")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "ok"
        assert json.loads(rows[0]["attrs"])["completion_mode"] == "delivered"

    async def test_mark_completed_unaddressed_records_completion_mode_unaddressed(
        self, tmp_db: DbPool
    ) -> None:
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(task_id="wire-fin-2", goal="g", status="pending"))
        await store.claim("wire-fin-2", worker="w1")

        await store.mark_completed_unaddressed("wire-fin-2", result="done, nobody waiting")

        rows = await _journal_rows(tmp_db, "task.finished", "wire-fin-2")
        assert len(rows) == 1
        assert json.loads(rows[0]["attrs"])["completion_mode"] == "unaddressed"

    async def test_a_delivery_proof_matching_no_row_records_nothing(
        self, tmp_db: DbPool
    ) -> None:
        """The AC's third path: an UPDATE that matches zero rows must not
        fabricate a journal row for a completion that never happened."""
        store = DurableTaskStore(tmp_db)

        await store.mark_delivered("wire-fin-does-not-exist", result="x")

        rows = await _journal_rows(tmp_db, "task.finished", "wire-fin-does-not-exist")
        assert rows == []


class TestDeadLetteringRecordsTheGiveUp:
    async def test_a_permanent_failure_records_exactly_one_dead_lettered_row(
        self, tmp_db: DbPool
    ) -> None:
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(task_id="wire-dl-1", goal="g", status="pending"))
        await store.claim("wire-dl-1", worker="w1")

        status = await store.fail_and_requeue(
            "wire-dl-1", error="unauthorized", failure_class="auth",
        )
        assert status == "dead_letter"

        rows = await _journal_rows(tmp_db, "task.dead_lettered", "wire-dl-1")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "dead_lettered"
        attrs = json.loads(rows[0]["attrs"])
        assert attrs["permanent"] is True
        assert attrs["failure_class"] == "auth"
        assert attrs["dependency_ids"] is None

    async def test_an_ordinary_retry_records_no_dead_lettered_row(
        self, tmp_db: DbPool
    ) -> None:
        """The non-permanent, non-exhausted branch (plain requeue) is
        deliberately OUT of this story's scope -- it must record nothing."""
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(
            task_id="wire-retry-1", goal="g", status="pending", max_attempts=30,
        ))
        await store.claim("wire-retry-1", worker="w1")

        status = await store.fail_and_requeue("wire-retry-1", error="transient blip")
        assert status == "pending"

        rows = await _journal_rows(tmp_db, "task.dead_lettered", "wire-retry-1")
        assert rows == []

    async def test_a_dependency_cascade_records_the_failed_dependency_id(
        self, tmp_db: DbPool
    ) -> None:
        """``_deps_satisfied``'s cascade path -- a dependency that dead-lettered
        takes its dependent down too, and the event names which dependency."""
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(
            task_id="wire-dep-parent", goal="dep", status="pending",
        ))
        await store.claim("wire-dep-parent", worker="w1")
        await store.fail_and_requeue(
            "wire-dep-parent", error="unauthorized", failure_class="auth",
        )
        await store.enqueue(DurableTask(
            task_id="wire-dep-child", goal="child", status="pending",
            depends_on=("wire-dep-parent",),
        ))

        claimable = await store.claimable(limit=10)
        assert "wire-dep-child" not in [t.task_id for t in claimable]

        rows = await _journal_rows(tmp_db, "task.dead_lettered", "wire-dep-child")
        assert len(rows) == 1
        attrs = json.loads(rows[0]["attrs"])
        assert attrs["dependency_ids"] == "wire-dep-parent"
        assert attrs["failure_class"] == "dependency_failed"

    async def test_a_permanent_failure_opens_an_incident_needs_you_item(
        self, tmp_db: DbPool
    ) -> None:
        """Story 3.1 (AD-28) -- the SAME real call site that already proves
        ``task.dead_lettered`` records also proves the give-up opens a
        durable `incident` item, not just a standalone
        ``tests/journal/test_recorder.py`` unit."""
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(task_id="wire-dl-ny-1", goal="g", status="pending"))
        await store.claim("wire-dl-ny-1", worker="w1")

        await store.fail_and_requeue(
            "wire-dl-ny-1", error="unauthorized", failure_class="auth",
        )

        item_rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE dedupe_key = ?",
            ("incident:owner:wire-dl-ny-1",),
        )
        assert len(item_rows) == 1
        assert item_rows[0]["intensity"] == "high"
        opened_rows = await _journal_rows(tmp_db, "needs_you.opened", "wire-dl-ny-1")
        assert len(opened_rows) == 1


class TestARealCallSiteRollsBackTheTaskRowWithTheJournalRow:
    async def test_a_failing_journal_record_rolls_back_claims_own_update(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Not just the standalone proof in ``tests/journal/test_recorder.py``
        (which never touches ``DurableTaskStore``) -- this forces
        ``journal.record`` itself to fail INSIDE a real wired call site
        (``claim()``) and proves the task's own UPDATE never survives either,
        because both run in the same ``DbPool.transaction()`` block."""
        import stackowl.pipeline.durable.store as store_module

        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(task_id="wire-rb-1", goal="g", status="pending"))

        async def _boom(conn: object, event: object) -> str:
            raise RuntimeError("simulated journal.record failure")

        monkeypatch.setattr(store_module, "journal_record", _boom)

        with pytest.raises(RuntimeError, match="simulated journal.record failure"):
            await store.claim("wire-rb-1", worker="w1")

        # The claim's own UPDATE (status -> 'running', lease_owner -> 'w1')
        # must NOT have committed — same transaction, same rollback.
        task = await store.get("wire-rb-1")
        assert task.status == "pending"
        assert task.lease_owner is None

        rows = await _journal_rows(tmp_db, "task.claimed", "wire-rb-1")
        assert rows == []
