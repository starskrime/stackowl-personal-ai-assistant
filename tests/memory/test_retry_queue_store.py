"""Tests for RetryQueueStore (Story 1.2, failure-retry-loop).

Runs against a real DbPool with migration 0082 applied (via the shared
tmp_db fixture in tests/conftest.py, which runs MigrationRunner), proving the
store against the actual retry_queue column set — not a hand-typed mock.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.retry_queue_store import RetryQueueStore, _retry_delay_minutes

pytestmark = pytest.mark.asyncio


async def test_retry_delay_minutes_doubles_and_caps() -> None:
    """FX-02: 1, 2, 4, 8 minutes, then held at the 10-minute cap."""
    assert _retry_delay_minutes(1) == 1
    assert _retry_delay_minutes(2) == 2
    assert _retry_delay_minutes(3) == 4
    assert _retry_delay_minutes(4) == 8
    assert _retry_delay_minutes(5) == 10  # would be 16 uncapped
    assert _retry_delay_minutes(20) == 10


async def test_insert_pending_then_get_due_returns_deserialized_row(tmp_db: DbPool) -> None:
    store = RetryQueueStore(tmp_db)

    retry_id = await store.insert_pending(
        trace_id="trace-1", session_id="sess-1", goal="do the thing",
        banned_capabilities=["cronjob"],
    )
    assert isinstance(retry_id, str)
    assert retry_id

    due = await store.get_due(limit=25)
    assert len(due) == 1
    row = due[0]
    assert row.trace_id == "trace-1"
    assert row.status == "pending"
    assert row.attempt_count == 0
    assert row.banned_capabilities == ["cronjob"]
    assert isinstance(row.banned_capabilities, list)


async def test_backfill_channel_message_updates_only_that_trace(tmp_db: DbPool) -> None:
    store = RetryQueueStore(tmp_db)
    await store.insert_pending(
        trace_id="trace-2", session_id="sess-1", goal="do the thing",
        banned_capabilities=[],
    )
    other_id = await store.insert_pending(
        trace_id="trace-other", session_id="sess-1", goal="unrelated",
        banned_capabilities=[],
    )

    await store.backfill_channel_message(
        trace_id="trace-2", channel_chat_id=555, channel_message_id=999,
    )

    due = await store.get_due(limit=25)
    by_trace = {r.trace_id: r for r in due}
    assert by_trace["trace-2"].channel_chat_id == "555"
    assert by_trace["trace-2"].channel_message_id == "999"
    assert isinstance(by_trace["trace-2"].channel_chat_id, str)
    assert by_trace["trace-other"].channel_chat_id is None
    assert other_id  # sanity: id was returned


async def test_get_latest_pending_for_session_returns_stringified_backfill(
    tmp_db: DbPool,
) -> None:
    store = RetryQueueStore(tmp_db)
    await store.insert_pending(
        trace_id="trace-3", session_id="sess-solo", goal="do the thing",
        banned_capabilities=[],
    )
    await store.backfill_channel_message(
        trace_id="trace-3", channel_chat_id=555, channel_message_id=999,
    )

    row = await store.get_latest_pending_for_session("sess-solo")
    assert row is not None
    assert row.channel_chat_id == "555"
    assert row.channel_message_id == "999"


async def test_get_latest_pending_for_session_returns_none_when_absent(
    tmp_db: DbPool,
) -> None:
    store = RetryQueueStore(tmp_db)
    assert await store.get_latest_pending_for_session("no-such-session") is None


async def test_mark_attempt_failed_below_cap_stays_pending_and_appends_capability(
    tmp_db: DbPool,
) -> None:
    store = RetryQueueStore(tmp_db)
    retry_id = await store.insert_pending(
        trace_id="trace-4", session_id="sess-1", goal="do the thing",
        banned_capabilities=["a"],
    )

    row = await store.mark_attempt_failed(
        retry_id=retry_id, newly_failed_capability="b", error="boom",
    )

    assert row.status == "pending"
    assert row.attempt_count == 1
    assert row.banned_capabilities == ["a", "b"]


async def test_mark_attempt_failed_reaches_cap_at_three_attempts(tmp_db: DbPool) -> None:
    store = RetryQueueStore(tmp_db)
    retry_id = await store.insert_pending(
        trace_id="trace-5", session_id="sess-1", goal="do the thing",
        banned_capabilities=[],
    )

    row = await store.mark_attempt_failed(
        retry_id=retry_id, newly_failed_capability="a", error="boom-1",
    )
    assert row.status == "pending"
    assert row.attempt_count == 1

    row = await store.mark_attempt_failed(
        retry_id=retry_id, newly_failed_capability="b", error="boom-2",
    )
    assert row.status == "pending"
    assert row.attempt_count == 2

    row = await store.mark_attempt_failed(
        retry_id=retry_id, newly_failed_capability="c", error="boom-3",
    )
    assert row.status == "failed"
    assert row.attempt_count == 3


async def test_mark_attempt_failed_next_retry_at_grows_with_attempt_count(
    tmp_db: DbPool,
) -> None:
    """FX-02: the re-arm delay after the 2nd failure is longer than after the 1st."""
    from datetime import UTC, datetime

    store = RetryQueueStore(tmp_db)
    retry_id = await store.insert_pending(
        trace_id="trace-backoff", session_id="sess-1", goal="do the thing",
        banned_capabilities=[],
    )

    before_first = datetime.now(UTC)
    row = await store.mark_attempt_failed(
        retry_id=retry_id, newly_failed_capability="a", error="boom-1",
    )
    first_delay = datetime.fromisoformat(row.next_retry_at) - before_first

    before_second = datetime.now(UTC)
    row = await store.mark_attempt_failed(
        retry_id=retry_id, newly_failed_capability="b", error="boom-2",
    )
    second_delay = datetime.fromisoformat(row.next_retry_at) - before_second

    assert second_delay > first_delay


async def test_mark_attempt_failed_missing_row_raises_value_error(tmp_db: DbPool) -> None:
    store = RetryQueueStore(tmp_db)

    with pytest.raises(ValueError, match="not-a-real-id"):
        await store.mark_attempt_failed(
            retry_id="not-a-real-id", newly_failed_capability="a", error="boom",
        )


async def test_mark_attempt_failed_does_not_duplicate_banned_capability(
    tmp_db: DbPool,
) -> None:
    store = RetryQueueStore(tmp_db)
    retry_id = await store.insert_pending(
        trace_id="trace-6", session_id="sess-1", goal="do the thing",
        banned_capabilities=["shell"],
    )

    row = await store.mark_attempt_failed(
        retry_id=retry_id, newly_failed_capability="shell", error="boom",
    )

    assert row.banned_capabilities == ["shell"]
    assert row.attempt_count == 1


async def test_mark_attempt_failed_truncates_last_error_to_2000_chars(
    tmp_db: DbPool,
) -> None:
    store = RetryQueueStore(tmp_db)
    retry_id = await store.insert_pending(
        trace_id="trace-7", session_id="sess-1", goal="do the thing",
        banned_capabilities=[],
    )

    row = await store.mark_attempt_failed(
        retry_id=retry_id, newly_failed_capability="a", error="x" * 5000,
    )

    assert row.last_error is not None
    assert len(row.last_error) == 2000


async def test_mark_completed_sets_status_and_excludes_from_get_due(
    tmp_db: DbPool,
) -> None:
    store = RetryQueueStore(tmp_db)
    retry_id = await store.insert_pending(
        trace_id="trace-8", session_id="sess-1", goal="do the thing",
        banned_capabilities=[],
    )

    await store.mark_completed(retry_id)

    due = await store.get_due(limit=25)
    assert retry_id not in {r.id for r in due}


async def test_queries_are_owner_scoped(tmp_db: DbPool) -> None:
    store_a = RetryQueueStore(tmp_db, owner_id="owner-a")
    store_b = RetryQueueStore(tmp_db, owner_id="owner-b")

    await store_a.insert_pending(
        trace_id="trace-owner-a", session_id="sess-1", goal="do the thing",
        banned_capabilities=[],
    )

    assert await store_b.get_due(limit=25) == []
    assert await store_b.get_latest_pending_for_session("sess-1") is None
    assert len(await store_a.get_due(limit=25)) == 1


async def test_mark_attempt_failed_concurrent_calls_do_not_lose_an_increment(
    tmp_db: DbPool,
) -> None:
    """Two overlapping mark_attempt_failed calls on the same row must not race.

    Regression test for the read-then-write atomicity fix (DbPool.transaction):
    without it, two concurrent SELECT-then-UPDATE calls can both read
    attempt_count=0 and both write attempt_count=1, silently losing one
    increment and one banned-capability append.
    """
    store = RetryQueueStore(tmp_db)
    retry_id = await store.insert_pending(
        trace_id="trace-race", session_id="sess-1", goal="do the thing",
        banned_capabilities=[],
    )

    results = await asyncio.gather(
        store.mark_attempt_failed(retry_id=retry_id, newly_failed_capability="a", error="e1"),
        store.mark_attempt_failed(retry_id=retry_id, newly_failed_capability="b", error="e2"),
    )

    final = max(results, key=lambda r: r.attempt_count)
    assert final.attempt_count == 2
    assert set(final.banned_capabilities) == {"a", "b"}


async def test_mark_attempt_failed_on_already_failed_row_raises_value_error(
    tmp_db: DbPool,
) -> None:
    store = RetryQueueStore(tmp_db)
    retry_id = await store.insert_pending(
        trace_id="trace-9", session_id="sess-1", goal="do the thing",
        banned_capabilities=[],
    )
    for i in range(3):
        await store.mark_attempt_failed(
            retry_id=retry_id, newly_failed_capability=f"cap-{i}", error="boom",
        )

    with pytest.raises(ValueError, match="not pending"):
        await store.mark_attempt_failed(
            retry_id=retry_id, newly_failed_capability="cap-4", error="boom-again",
        )


async def test_mark_attempt_failed_on_completed_row_raises_value_error(
    tmp_db: DbPool,
) -> None:
    store = RetryQueueStore(tmp_db)
    retry_id = await store.insert_pending(
        trace_id="trace-10", session_id="sess-1", goal="do the thing",
        banned_capabilities=[],
    )
    await store.mark_completed(retry_id)

    with pytest.raises(ValueError, match="not pending"):
        await store.mark_attempt_failed(
            retry_id=retry_id, newly_failed_capability="a", error="boom",
        )


async def test_mark_completed_on_missing_row_does_not_raise(tmp_db: DbPool) -> None:
    store = RetryQueueStore(tmp_db)
    await store.mark_completed("no-such-retry-id")  # no-op, logged, not raised


async def test_backfill_channel_message_on_missing_row_does_not_raise(tmp_db: DbPool) -> None:
    store = RetryQueueStore(tmp_db)
    await store.backfill_channel_message(
        trace_id="no-such-trace", channel_chat_id=1, channel_message_id=2,
    )  # no-op, logged, not raised


async def test_backfill_channel_message_duplicate_trace_id_stamps_only_one_row(
    tmp_db: DbPool,
) -> None:
    """Two pending rows sharing a trace_id (schema allows it) — backfill must
    touch exactly one, not both, bounding the blast radius of the missing
    UNIQUE constraint on trace_id (deferred-work item against migration 0082).
    """
    store = RetryQueueStore(tmp_db)
    id_1 = await store.insert_pending(
        trace_id="trace-dup", session_id="sess-1", goal="first", banned_capabilities=[],
    )
    id_2 = await store.insert_pending(
        trace_id="trace-dup", session_id="sess-1", goal="second", banned_capabilities=[],
    )

    await store.backfill_channel_message(
        trace_id="trace-dup", channel_chat_id=777, channel_message_id=888,
    )

    due = await store.get_due(limit=25)
    by_id = {r.id: r for r in due}
    stamped = [r for r in (by_id[id_1], by_id[id_2]) if r.channel_chat_id == "777"]
    assert len(stamped) == 1


async def test_get_due_rejects_non_positive_limit(tmp_db: DbPool) -> None:
    store = RetryQueueStore(tmp_db)
    with pytest.raises(ValueError, match="limit must be >= 1"):
        await store.get_due(limit=0)
    with pytest.raises(ValueError, match="limit must be >= 1"):
        await store.get_due(limit=-1)


async def test_insert_pending_truncates_goal_to_4000_chars(tmp_db: DbPool) -> None:
    store = RetryQueueStore(tmp_db)
    await store.insert_pending(
        trace_id="trace-long-goal", session_id="sess-1", goal="x" * 9000,
        banned_capabilities=[],
    )

    due = await store.get_due(limit=25)
    assert len(due[0].goal) == 4000


async def test_reschedule_pushes_row_out_of_due_window(tmp_db: DbPool) -> None:
    """A flood-controlled delivery failure must push next_retry_at into the
    future — the row stays 'pending' but drops out of get_due() until the
    delay elapses, so the next sweep tick does NOT immediately re-hammer the
    still-banned channel."""
    store = RetryQueueStore(tmp_db)
    retry_id = await store.insert_pending(
        trace_id="trace-flood", session_id="sess-1", goal="do the thing",
        banned_capabilities=[],
    )

    await store.reschedule(retry_id, delay_seconds=120, error="Flood control exceeded")

    due = await store.get_due(limit=25)
    assert due == []


async def test_reschedule_does_not_touch_attempt_count_or_banned(tmp_db: DbPool) -> None:
    store = RetryQueueStore(tmp_db)
    retry_id = await store.insert_pending(
        trace_id="trace-flood-2", session_id="sess-1", goal="do the thing",
        banned_capabilities=["cronjob"],
    )

    await store.reschedule(retry_id, delay_seconds=0, error="Flood control exceeded")

    due = await store.get_due(limit=25)
    row = due[0]
    assert row.status == "pending"
    assert row.attempt_count == 0
    assert row.banned_capabilities == ["cronjob"]
    assert row.last_error == "Flood control exceeded"


async def test_reschedule_ignores_missing_row(tmp_db: DbPool) -> None:
    store = RetryQueueStore(tmp_db)
    # Must not raise for an id that doesn't exist — logs a warning and returns.
    await store.reschedule("no-such-id", delay_seconds=60, error="x")
