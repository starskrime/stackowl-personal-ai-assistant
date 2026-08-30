"""D01.7 slice 5c — a boundary cannot lose its summary (DEBT-11).

THE DEFECT. Q15's durability begins when the consumer ENQUEUES a job. The window
between publishing `session.rollover` and that enqueue is in-memory and
fire-and-forget, and `expiry_finalized` makes the double-announce guard suppress
any second announcement of the same boundary. So a rollover published with no live
consumer is lost permanently and silently.

Observed on the live platform: the sweeper finalised a lane at 09:03:04Z and
published; nothing was listening; the 14:41:55Z message correctly declined to
re-announce. Zero jobs, zero summaries, no error.

THE FIX. The enqueue becomes recoverable from PERSISTED state instead of from a
live subscriber. The lane records which incarnation's summary it has enqueued, and
`conversation_sweep` — which already runs every five minutes — enqueues for any
finalised lane that has no record. The EventBus stays the fast path and the seam
`D09.1`/`D09.3` subscribe to (dedup target X3 stays resolved); the sweep is the
safety net underneath it.

SCOPE, STATED PLAINLY. The backstop covers the SWEEPER path, which is the
unattended 4 AM case Q15 was written for: the sweeper finalises without minting, so
the lane still holds the ended incarnation and the boundary is recoverable from the
row. The ingress path mints immediately and moves the lane on, so a crash in its
publish→enqueue window is not recoverable this way — but a user is present there by
definition, which is the case Q15 explicitly was not worried about.
"""

from __future__ import annotations

import datetime

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.sessions import ChatType, ResetMode, ResetPolicy, SessionSource
from stackowl.sessions.store import SessionStore

pytestmark = pytest.mark.asyncio

UTC = datetime.UTC


def at(day: int, hour: int) -> datetime.datetime:
    return datetime.datetime(2026, 7, day, hour, tzinfo=UTC)


def src() -> SessionSource:
    return SessionSource("Brain", "telegram", ChatType.DM, "123",
                         identity_key="bakir")


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    db = DbPool(db_path=tmp_path / "test.db")
    await db.open()
    seed_schema(tmp_path / "test.db")
    yield SessionStore(db, ResetPolicy(mode=ResetMode.BOTH, at_hour=4),
                       mirror_dir=tmp_path)
    await db.close()


async def _finalise(store: SessionStore) -> str:
    """Drive a real sweeper finalisation and return the lane key."""
    entry, _, _ = await store.resolve_for(src(), at(20, 22))
    await store.sweep(now=at(21, 9))
    return entry.session_key


# ------------------------------------------------------- the recovery query


async def test_a_finalised_lane_is_reported_as_awaiting_its_summary(
    store: SessionStore,
) -> None:
    """The exact live case: the sweeper finalised and nobody was listening."""
    lane = await _finalise(store)
    awaiting = await store.lanes_awaiting_summary()
    assert [e.session_key for e in awaiting] == [lane]


async def test_a_lane_that_never_ended_is_not_awaiting_anything(
    store: SessionStore,
) -> None:
    await store.resolve_for(src(), at(20, 22))
    assert await store.lanes_awaiting_summary() == []


async def test_marking_it_enqueued_takes_it_off_the_list(
    store: SessionStore,
) -> None:
    """Idempotency: the five-minute sweep must not re-enqueue the same boundary
    every five minutes for ever."""
    lane = await _finalise(store)
    entry = await store.get(lane)
    assert entry is not None
    await store.mark_summary_enqueued(lane, entry.conversation_id)
    assert await store.lanes_awaiting_summary() == []


async def test_marking_a_DIFFERENT_incarnation_does_not_silence_this_one(
    store: SessionStore,
) -> None:
    """The record is per INCARNATION, not per lane. A lane that rolls again needs
    its new boundary summarised, and a stale marker must not suppress it."""
    lane = await _finalise(store)
    await store.mark_summary_enqueued(lane, "20260101_000000_deadbeef")
    awaiting = await store.lanes_awaiting_summary()
    assert [e.session_key for e in awaiting] == [lane]


async def test_the_marker_survives_a_round_trip(store: SessionStore) -> None:
    lane = await _finalise(store)
    entry = await store.get(lane)
    assert entry is not None
    await store.mark_summary_enqueued(lane, entry.conversation_id)
    reloaded = await store.get(lane)
    assert reloaded is not None
    assert reloaded.summary_enqueued_for == entry.conversation_id


async def test_a_new_incarnation_clears_the_marker_for_the_next_boundary(
    store: SessionStore,
) -> None:
    """A lane that rolls, is summarised, then rolls again must be summarised again.

    Without this the FIRST summary would silence every later boundary on that lane.
    """
    lane = await _finalise(store)
    first = await store.get(lane)
    assert first is not None
    await store.mark_summary_enqueued(lane, first.conversation_id)

    # The user speaks: a new incarnation is minted through the normal path.
    await store.resolve_for(src(), at(21, 10))
    # ... and later that lane is finalised again.
    await store.sweep(now=at(22, 9))

    awaiting = await store.lanes_awaiting_summary()
    assert [e.session_key for e in awaiting] == [lane], (
        "the second boundary must be recoverable even though the first was enqueued"
    )


# ---------------------------------------------------- the sweep does the work


async def test_the_sweeper_enqueues_for_a_boundary_nobody_heard(
    store: SessionStore,
) -> None:
    """End to end through the real handler: the backstop turns a lost boundary into
    a durable job."""
    from stackowl.scheduler.handlers.conversation_sweep import (
        ConversationSweepHandler,
    )
    from stackowl.scheduler.job import Job

    lane = await _finalise(store)
    enqueued: list[tuple[str, str]] = []

    # The double now has the REAL signature: enqueue_rollover_summary takes `db`
    # as a required POSITIONAL argument. It used to be `(**kwargs)`, which accepts
    # anything — and that is exactly why a production TypeError ("missing 1
    # required positional argument: 'db'") survived here for as long as it did.
    # The handler is likewise constructed WITH a db, as assembly does in
    # production (enforces_all_four_i4_conditions: true in the live registration
    # log), so this test drives the wiring that actually ships.
    async def _fake_enqueue(db: object, **kwargs: object) -> bool:
        assert db is not None, "the backstop must be handed a db to enqueue against"
        enqueued.append((str(kwargs["lane"]), str(kwargs["ended"])))
        return True

    handler = ConversationSweepHandler(
        store, enqueue_summary=_fake_enqueue, db=store._db,  # noqa: SLF001
    )
    await handler.execute(Job(
        job_id="j", handler_name="conversation_sweep", schedule="every 5m",
        idempotency_key="k", last_run_at=None, next_run_at="", status="pending",
    ))

    entry = await store.get(lane)
    assert entry is not None
    assert enqueued == [(lane, entry.conversation_id)]
    # And it is marked, so the next sweep does not do it again.
    assert await store.lanes_awaiting_summary() == []


async def test_a_failed_enqueue_leaves_the_boundary_recoverable(
    store: SessionStore,
) -> None:
    """If the enqueue fails, the marker must NOT be written — otherwise the retry
    the backstop exists to provide is thrown away."""
    from stackowl.scheduler.handlers.conversation_sweep import (
        ConversationSweepHandler,
    )
    from stackowl.scheduler.job import Job

    lane = await _finalise(store)

    async def _failing_enqueue(**kwargs: object) -> bool:
        return False

    handler = ConversationSweepHandler(store, enqueue_summary=_failing_enqueue)
    await handler.execute(Job(
        job_id="j", handler_name="conversation_sweep", schedule="every 5m",
        idempotency_key="k", last_run_at=None, next_run_at="", status="pending",
    ))

    awaiting = await store.lanes_awaiting_summary()
    assert [e.session_key for e in awaiting] == [lane], (
        "a failed enqueue must stay retryable on the next sweep"
    )


async def test_a_sweeper_with_no_enqueue_wired_still_sweeps(
    store: SessionStore,
) -> None:
    """The backstop is optional wiring. Absent it, expiry still works exactly as
    before — an unwired component must not break the component it hangs off."""
    from stackowl.scheduler.handlers.conversation_sweep import (
        ConversationSweepHandler,
    )
    from stackowl.scheduler.job import Job

    await store.resolve_for(src(), at(20, 22))
    handler = ConversationSweepHandler(store)
    result = await handler.execute(Job(
        job_id="j", handler_name="conversation_sweep", schedule="every 5m",
        idempotency_key="k", last_run_at=None, next_run_at="", status="pending",
    ))
    assert result.success is True
