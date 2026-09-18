"""``journal/fanout.py`` -- the pure, DB-injected read/notify primitive Spec
2.5 builds split-mode TUI progress on.

``tmp_db`` (``tests/conftest.py``) IS a real ``RowFetcher`` structurally (it
exposes ``fetch_all`` with the exact shape) -- no fake is needed for the read
half; the tests drive `journal.record` against a real SQLite DB, exactly the
way `pipeline/durable/store.py` will.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.db.pool import DbPool
from stackowl.journal import ActorKind, JournalEvent, Outcome, RecordRef, record
from stackowl.journal.fanout import (
    current_max_cursor,
    notify_committed,
    read_since,
    reset_for_tests,
    wait_for_commit,
)
from stackowl.journal.task_events import TaskEnqueuedAttrs

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_commit_signal() -> None:
    """The commit-signal ``asyncio.Event`` is process-global (mirrors
    ``write_gate.py``'s state) -- reset it around every test in this file so
    one test's ``notify_committed()`` can never leak into another's
    ``wait_for_commit()`` call."""
    reset_for_tests()


def _event(**over: object) -> JournalEvent:
    defaults: dict[str, object] = {
        "type": "task.enqueued",
        "schema_version": 1,
        "actor_kind": ActorKind.AUTONOMOUS,
        "actor_id": "principal-default",
        "target_kind": ActorKind.OWNER,
        "target_id": "t1",
        "outcome": Outcome.PENDING,
        "record_ref": RecordRef(kind="sqlite", locator={"table": "tasks", "task_id": "t1"}),
        "attrs": TaskEnqueuedAttrs(trigger_kind="chat", depends_on_count=0, max_attempts=30),
    }
    defaults.update(over)
    return JournalEvent(**defaults)  # type: ignore[arg-type]


class TestReadSince:
    async def test_returns_rows_in_cursor_order(self, tmp_db: DbPool) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="a"))
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="b"))
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="c"))

        rows = await read_since(tmp_db, 0)

        assert [r.target_id for r in rows] == ["a", "b", "c"]
        assert [r.cursor for r in rows] == sorted(r.cursor for r in rows)

    async def test_only_rows_past_the_given_cursor_are_returned(self, tmp_db: DbPool) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="a"))
        first = (await read_since(tmp_db, 0))[0]
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="b"))

        rows = await read_since(tmp_db, first.cursor)

        assert [r.target_id for r in rows] == ["b"]

    async def test_a_cursor_hole_is_skipped_not_blocked(self, tmp_db: DbPool) -> None:
        """A row rolled back (or later pruned) is simply absent -- fan-out
        must never block waiting for a cursor that will never exist (AD-9)."""
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="a"))
        rowcount_before = (await read_since(tmp_db, 0))[0].cursor
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="b"))
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="c"))
        # Delete the middle row directly -- simulates a pruned/rolled-back hole.
        await tmp_db.execute(
            "DELETE FROM journal_events WHERE target_id = ?", ("b",),
        )

        rows = await read_since(tmp_db, rowcount_before)

        assert [r.target_id for r in rows] == ["c"]

    async def test_a_rolled_back_event_never_appears(self, tmp_db: DbPool) -> None:
        """Reuses Story 2.1's forced-rollback precedent (``test_recorder.py``):
        a transaction that records and then raises leaves NO row, so
        ``read_since`` can never see it."""
        with pytest.raises(RuntimeError, match="simulated"):
            async with tmp_db.transaction() as conn:
                await record(conn, _event(target_id="rollback-1"))
                raise RuntimeError("simulated failure after record()")

        rows = await read_since(tmp_db, 0)

        assert all(r.target_id != "rollback-1" for r in rows)

    async def test_attrs_and_record_ref_are_json_decoded(self, tmp_db: DbPool) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, _event(
                target_id="decoded-1",
                attrs=TaskEnqueuedAttrs(trigger_kind="schedule", depends_on_count=2, max_attempts=5),
            ))

        rows = await read_since(tmp_db, 0)
        row = next(r for r in rows if r.target_id == "decoded-1")

        assert row.attrs == {
            "trigger_kind": "schedule", "depends_on_count": 2, "max_attempts": 5,
        }
        assert row.record_ref == {"kind": "sqlite", "locator": {"table": "tasks", "task_id": "t1"}}

    async def test_bounded_by_limit(self, tmp_db: DbPool) -> None:
        for i in range(5):
            async with tmp_db.transaction() as conn:
                await record(conn, _event(target_id=f"lim-{i}"))

        rows = await read_since(tmp_db, 0, limit=2)

        assert len(rows) == 2


class TestCurrentMaxCursor:
    async def test_an_empty_table_returns_zero(self, tmp_db: DbPool) -> None:
        assert await current_max_cursor(tmp_db) == 0

    async def test_reflects_the_highest_committed_cursor(self, tmp_db: DbPool) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="a"))
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="b"))

        rows = await read_since(tmp_db, 0)
        assert await current_max_cursor(tmp_db) == rows[-1].cursor


class TestCommitSignalRoundTrip:
    async def test_notify_then_wait_returns_without_hanging(self) -> None:
        notify_committed()
        await asyncio.wait_for(wait_for_commit(), timeout=1.0)

    async def test_wait_blocks_until_notified(self) -> None:
        woke = asyncio.Event()

        async def _waiter() -> None:
            await wait_for_commit()
            woke.set()

        task = asyncio.create_task(_waiter())
        await asyncio.sleep(0.02)
        assert not woke.is_set()

        notify_committed()
        await asyncio.wait_for(woke.wait(), timeout=1.0)
        await task

    async def test_wait_with_a_timeout_returns_on_expiry_without_a_notify(self) -> None:
        # Must return (never raise) and never hang past the timeout.
        await asyncio.wait_for(wait_for_commit(timeout=0.01), timeout=1.0)
