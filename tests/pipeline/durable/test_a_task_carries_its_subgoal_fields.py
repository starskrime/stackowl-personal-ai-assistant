"""The fields absorbed from objective_subgoals must ROUND TRIP.

objective_subgoals duplicated 11 of its 18 columns onto `tasks`, including
STATUS, and already carried a task_id — every subgoal ran AS a task and mirrored
the outcome back. Two status columns for one unit of work, and on 2026-08-28 they
diverged: 44 subgoals read pending/running while no task was running.

Migration 0126 gives `tasks` the six fields it lacked. Storing them without
SELECTING them back would make every one read None for ever — a write with no
reader, which is the single most common defect shape in this tree and the exact
thing this collapse is meant to remove.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask

OWNER = "principal-default"


@pytest.mark.asyncio
async def test_subgoal_fields_survive_a_round_trip(tmp_db: DbPool) -> None:
    """THE regression. Written and never read is the same as never written."""
    store = DurableTaskStore(tmp_db, OWNER)
    await store.enqueue(DurableTask(
        task_id="sg-1", owner_id=OWNER, goal="search the boards", status="pending",
        position=3, verified=True, estimated_complexity="medium",
        decomposition_depth=1, worktree_path="/tmp/wt", story_branch="story/42",
    ))

    got = await store.get("sg-1")

    assert got.position == 3
    assert got.verified is True
    assert got.estimated_complexity == "medium"
    assert got.decomposition_depth == 1
    assert got.worktree_path == "/tmp/wt"
    assert got.story_branch == "story/42"


@pytest.mark.asyncio
async def test_an_ordinary_task_pays_nothing_for_them(tmp_db: DbPool) -> None:
    """The control. A chat turn, a cron task and a retry set none of these."""
    store = DurableTaskStore(tmp_db, OWNER)
    await store.enqueue(DurableTask(
        task_id="chat-1", owner_id=OWNER, goal="hello", status="pending",
    ))

    got = await store.get("chat-1")

    assert got.position is None
    assert got.verified is None
    assert got.worktree_path is None


@pytest.mark.asyncio
async def test_verified_False_is_not_lost_as_None(tmp_db: DbPool) -> None:
    """A tri-state stored as INTEGER: 0 must come back False, not None.

    "not verified" and "never checked" are different answers, and collapsing them
    would let an unverified result read as unexamined.
    """
    store = DurableTaskStore(tmp_db, OWNER)
    await store.enqueue(DurableTask(
        task_id="sg-2", owner_id=OWNER, goal="g", status="pending", verified=False,
    ))

    assert (await store.get("sg-2")).verified is False


@pytest.mark.asyncio
async def test_the_LOOP_sees_them_too_not_just_get(tmp_db: DbPool) -> None:
    """The path production actually takes.

    `get()` goes through _fetch_owned (SELECT *), so it picks up new columns for
    free — which makes a round-trip test via get() pass without proving anything
    about the loop. `claimable()` builds an EXPLICIT column list, and that is what
    the loop calls to decide what to run. A field the loop cannot see is a field
    the objective work does not have.
    """
    store = DurableTaskStore(tmp_db, OWNER)
    await store.enqueue(DurableTask(
        task_id="sg-loop", owner_id=OWNER, goal="ordered work", status="pending",
        position=2, decomposition_depth=1,
    ))

    claimable = await store.claimable()

    row = next(t for t in claimable if t.task_id == "sg-loop")
    assert row.position == 2, (
        "the loop cannot see `position`, so ordered objective work would run in "
        "arbitrary order"
    )
    assert row.decomposition_depth == 1
