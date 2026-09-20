"""Story 4.4 -- ``store.park_for_decision``/``store.resume_command_after_
answer``, and the durable-state resume sweep
(``pipeline.durable.command_resume_sweep.resume_resolved_parked_commands``).

Proves the I/O matrix's own park/resume/sweep rows directly against the
store and a real ``DbPool`` (``tmp_db``, migrated through 0152) -- never a
double, since the behavior under test IS the SQL (the partial unique index,
the conditional UPDATEs, the durable-state read).

``TestLoopDispatchParksOnCommandNeedsDecision`` at the bottom covers the
OTHER caller (``loop.py``'s tick-driven ``_dispatch``) with a store double,
mirroring ``test_a_retry_never_fabricates_a_fallback_chat.py``'s own
``TaskLoop.__new__`` + mocked-store idiom for its sibling
``NoAddresseeCompletion`` branch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.db.pool import DbPool
from stackowl.journal import needs_you
from stackowl.journal.enums import NeedsYouKind
from stackowl.pipeline.durable.command_resume_sweep import (
    resume_resolved_parked_commands,
)
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask

pytestmark = pytest.mark.asyncio

_COMMAND_TYPE = "widget.needs_a_decision"


def _command_task(n: int = 1) -> DurableTask:
    return DurableTask(
        task_id=f"cmd-c{n}", goal=f"command:{_COMMAND_TYPE}", status="running",
        kind="command", command_type=_COMMAND_TYPE, command_id=f"c{n}",
        requester_kind="owner", trigger_kind="command",
        lease_owner="worker-1",
    )


async def _park(store: DurableTaskStore, task: DurableTask, *, outcome: str) -> str | None:
    return await store.park_for_decision(
        task.task_id, command_type=_COMMAND_TYPE, command_id=task.command_id or "",
        requester_kind="owner", outcome=outcome,
        payload_summary='{"widget_id": "w1"}',
    )


async def _answer(db: DbPool, *, item_id: str, answer: str) -> None:
    async with db.transaction() as conn:
        result = await needs_you.resolve(
            conn, item_id=item_id, answer=answer, resolved_by="telegram:owner",
        )
    assert result.outcome == "resolved"


class TestParkForDecision:
    async def test_parks_the_row_holding_no_worker(self, tmp_db: DbPool) -> None:
        store = DurableTaskStore(tmp_db)
        task = _command_task()
        await store.create(task)
        await _park(store, task, outcome="needs_step_up")

        row = await store.get(task.task_id)
        assert row.status == "parked"
        assert row.gate_verdict == "needs_step_up"
        assert row.lease_owner is None
        assert row.lease_expires_at is None

    async def test_opens_one_approval_item_with_no_expiry(self, tmp_db: DbPool) -> None:
        store = DurableTaskStore(tmp_db)
        task = _command_task()
        await store.create(task)
        item_id = await _park(store, task, outcome="needs_approval")
        assert item_id is not None

        rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE id = ?", (item_id,),
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["kind"] == NeedsYouKind.APPROVAL.value
        assert row["waiter_kind"] == needs_you.WAITER_KIND_COMMAND
        assert row["waiter_id"] == task.task_id
        assert row["expires_at"] is None

    async def test_a_second_park_call_for_the_same_command_id_is_idempotent(
        self, tmp_db: DbPool,
    ) -> None:
        """Lease-reclaim race (I/O matrix): the second call no-ops the open
        via the partial unique index -- still exactly one open item."""
        store = DurableTaskStore(tmp_db)
        task = _command_task()
        await store.create(task)
        first_item_id = await _park(store, task, outcome="needs_step_up")
        second_item_id = await _park(store, task, outcome="needs_step_up")

        assert first_item_id is not None
        assert second_item_id == first_item_id

        rows = await tmp_db.fetch_all(
            "SELECT COUNT(*) AS n FROM needs_you WHERE waiter_kind = ? AND waiter_id = ?",
            (needs_you.WAITER_KIND_COMMAND, task.task_id),
        )
        assert rows[0]["n"] == 1


class TestResumeCommandAfterAnswer:
    async def test_approved_reruns_the_handler_with_no_second_item(
        self, tmp_db: DbPool,
    ) -> None:
        store = DurableTaskStore(tmp_db)
        task = _command_task()
        await store.create(task)
        item_id = await _park(store, task, outcome="needs_step_up")
        assert item_id is not None
        await _answer(tmp_db, item_id=item_id, answer="approved")

        await store.resume_command_after_answer(task.task_id, approved=True)

        row = await store.get(task.task_id)
        assert row.status == "pending"
        assert row.gate_verdict == "approved"
        assert row.lease_owner is None

        # The item's dedupe_key is resolved and free -- re-parking (a
        # hypothetical re-decision) would open a FRESH item, never collide
        # with the resolved one, proving no duplicate is possible.
        count = await tmp_db.fetch_all(
            "SELECT COUNT(*) AS n FROM needs_you WHERE waiter_id = ?",
            (task.task_id,),
        )
        assert count[0]["n"] == 1

    async def test_denied_requeues_through_the_existing_ceiling_rules(
        self, tmp_db: DbPool,
    ) -> None:
        store = DurableTaskStore(tmp_db)
        task = _command_task()
        await store.create(task)
        item_id = await _park(store, task, outcome="needs_step_up")
        assert item_id is not None
        await _answer(tmp_db, item_id=item_id, answer="denied")

        await store.resume_command_after_answer(
            task.task_id, approved=False, reason="denied",
        )

        row = await store.get(task.task_id)
        # One denial, well under the default 30-attempt ceiling -- requeued,
        # not dead-lettered (I/O matrix: "requeued/dead-lettered per
        # existing ceiling rules").
        assert row.status == "pending"
        assert row.last_failure_class == "command_denied"
        assert row.attempt_count == 1


class TestResumeResolvedParkedCommandsSweep:
    async def test_resumes_an_approved_row_using_only_durable_state(
        self, tmp_db: DbPool,
    ) -> None:
        """Simulates 'after a restart': fresh store/task objects, nothing
        held in memory from the park step but the db_pool itself."""
        setup_store = DurableTaskStore(tmp_db)
        task = _command_task()
        await setup_store.create(task)
        item_id = await _park(setup_store, task, outcome="needs_step_up")
        assert item_id is not None
        await _answer(tmp_db, item_id=item_id, answer="approved")
        del setup_store  # nothing from here on reuses this handle

        resumed = await resume_resolved_parked_commands(tmp_db)

        assert resumed == [task.task_id]
        fresh_store = DurableTaskStore(tmp_db)
        row = await fresh_store.get(task.task_id)
        assert row.status == "pending"
        assert row.gate_verdict == "approved"

    async def test_resumes_a_denied_row(self, tmp_db: DbPool) -> None:
        setup_store = DurableTaskStore(tmp_db)
        task = _command_task()
        await setup_store.create(task)
        item_id = await _park(setup_store, task, outcome="needs_step_up")
        assert item_id is not None
        await _answer(tmp_db, item_id=item_id, answer="denied")

        resumed = await resume_resolved_parked_commands(tmp_db)

        assert resumed == [task.task_id]
        row = await DurableTaskStore(tmp_db).get(task.task_id)
        assert row.last_failure_class == "command_denied"

    async def test_a_still_open_item_is_left_parked(self, tmp_db: DbPool) -> None:
        store = DurableTaskStore(tmp_db)
        task = _command_task()
        await store.create(task)
        await _park(store, task, outcome="needs_step_up")

        resumed = await resume_resolved_parked_commands(tmp_db)

        assert resumed == []
        row = await store.get(task.task_id)
        assert row.status == "parked"

    async def test_a_row_never_parked_is_ignored(self, tmp_db: DbPool) -> None:
        """A pending goal task never touches the sweep at all."""
        store = DurableTaskStore(tmp_db)
        await store.create(DurableTask(task_id="goal-1", goal="do a thing", status="pending"))

        resumed = await resume_resolved_parked_commands(tmp_db)
        assert resumed == []

    async def test_candidate_query_failure_returns_empty_never_raises(
        self, tmp_db: DbPool,
    ) -> None:
        class _BoomPool:
            async def fetch_all(self, *a: object, **k: object) -> list[dict]:
                raise RuntimeError("db unavailable")

        resumed = await resume_resolved_parked_commands(_BoomPool())  # type: ignore[arg-type]
        assert resumed == []


class TestLoopDispatchParksOnCommandNeedsDecision:
    """The tick-driven caller: ``loop.py::TaskLoop._dispatch`` catches
    ``CommandNeedsDecisionError`` BEFORE its generic ``except Exception`` and
    parks through the store, never reporting a handler failure that never
    happened."""

    async def test_dispatch_parks_instead_of_requeuing_as_a_failure(self) -> None:
        from stackowl.commands.spec.errors import CommandNeedsDecisionError
        from stackowl.pipeline.durable.loop import TaskLoop

        store = MagicMock()
        store.park_for_decision = AsyncMock(return_value="item-1")
        store.fail_and_requeue = AsyncMock()
        store.mark_delivered = AsyncMock()
        store.reclaim_expired = AsyncMock()
        store.count_prior_reshaping_failures = AsyncMock(return_value=0)

        async def _runner(_task: object) -> str:
            raise CommandNeedsDecisionError(
                "widget.needs_a_decision", "needs_step_up", '{"widget_id": "w1"}',
            )

        loop = TaskLoop.__new__(TaskLoop)
        loop._store = store            # noqa: SLF001
        loop._runner = _runner         # noqa: SLF001
        task = MagicMock()
        task.task_id = "cmd-t1"
        task.command_id = "c1"
        task.requester_kind = "owner"
        task.last_failure_class = ""

        await loop._dispatch(task)     # noqa: SLF001

        store.fail_and_requeue.assert_not_awaited()
        store.mark_delivered.assert_not_awaited()
        store.park_for_decision.assert_awaited_once_with(
            "cmd-t1", command_type="widget.needs_a_decision", command_id="c1",
            requester_kind="owner", outcome="needs_step_up",
            payload_summary='{"widget_id": "w1"}',
        )

    async def test_a_park_failure_never_raises_into_the_gather(self) -> None:
        """``_safe_park`` mirrors ``_safe_fail``/``_safe_complete_unaddressed``:
        a park failure is logged, never propagated."""
        from stackowl.commands.spec.errors import CommandNeedsDecisionError
        from stackowl.pipeline.durable.loop import TaskLoop

        store = MagicMock()
        store.park_for_decision = AsyncMock(side_effect=RuntimeError("db down"))
        store.reclaim_expired = AsyncMock()
        store.count_prior_reshaping_failures = AsyncMock(return_value=0)

        async def _runner(_task: object) -> str:
            raise CommandNeedsDecisionError("widget.x", "needs_approval", "{}")

        loop = TaskLoop.__new__(TaskLoop)
        loop._store = store            # noqa: SLF001
        loop._runner = _runner         # noqa: SLF001
        task = MagicMock()
        task.task_id = "cmd-t2"
        task.command_id = "c2"
        task.requester_kind = "owner"
        task.last_failure_class = ""

        await loop._dispatch(task)     # noqa: SLF001 — must not raise
