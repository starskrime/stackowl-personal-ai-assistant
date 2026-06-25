"""ObjectiveStore — owner-scoped persistence for standing objectives (1A).

An objective is a persistent intent the assistant decomposes into ordered
sub-goals and works across many autonomous turns until done/blocked. The store
round-trips the objective, its ordered sub-goals, and an activity-event log,
all owner-scoped via :class:`OwnedRepository`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.objectives.model import Objective
from stackowl.objectives.store import ObjectiveNotFoundError, ObjectiveStore


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "objectives.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _objective(objective_id: str = "obj-1", intent: str = "watch X and handle it") -> Objective:
    return Objective(
        objective_id=objective_id,
        owner_id="principal-default",
        intent=intent,
        channel="telegram",
        target_channels=["telegram"],
        target_addresses={"telegram": 12345},
    )


async def test_create_get_roundtrips_objective(pool: DbPool) -> None:
    store = ObjectiveStore(pool, "principal-default")
    await store.create(_objective())
    got = await store.get("obj-1")
    assert got.intent == "watch X and handle it"
    assert got.status == "active"  # default
    assert got.channel == "telegram"
    assert got.target_channels == ["telegram"]
    assert got.target_addresses == {"telegram": 12345}  # native int preserved


async def test_list_filters_by_status(pool: DbPool) -> None:
    store = ObjectiveStore(pool, "principal-default")
    await store.create(_objective("obj-a"))
    await store.create(_objective("obj-b"))
    await store.update_status("obj-b", "done")
    active = await store.list_objectives(status="active")
    assert [o.objective_id for o in active] == ["obj-a"]


async def test_update_status_records_blocker(pool: DbPool) -> None:
    store = ObjectiveStore(pool, "principal-default")
    await store.create(_objective())
    await store.update_status("obj-1", "blocked", blocker="needs an irreversible decision")
    got = await store.get("obj-1")
    assert got.status == "blocked"
    assert got.blocker == "needs an irreversible decision"


async def test_add_and_list_subgoals_in_order(pool: DbPool) -> None:
    store = ObjectiveStore(pool, "principal-default")
    await store.create(_objective())
    subs = await store.add_subgoals("obj-1", ["first step", "second step", "third step"])
    assert [s.position for s in subs] == [0, 1, 2]
    listed = await store.list_subgoals("obj-1")
    assert [s.description for s in listed] == ["first step", "second step", "third step"]
    assert all(s.status == "pending" for s in listed)


async def test_next_pending_subgoal_advances(pool: DbPool) -> None:
    store = ObjectiveStore(pool, "principal-default")
    await store.create(_objective())
    subs = await store.add_subgoals("obj-1", ["a", "b"])
    nxt = await store.next_pending_subgoal("obj-1")
    assert nxt is not None and nxt.description == "a"
    await store.update_subgoal(subs[0].subgoal_id, "done", result="did a")
    nxt2 = await store.next_pending_subgoal("obj-1")
    assert nxt2 is not None and nxt2.description == "b"
    await store.update_subgoal(subs[1].subgoal_id, "done")
    assert await store.next_pending_subgoal("obj-1") is None  # all done


async def test_update_subgoal_persists_result_and_task_id(pool: DbPool) -> None:
    store = ObjectiveStore(pool, "principal-default")
    await store.create(_objective())
    subs = await store.add_subgoals("obj-1", ["only step"])
    await store.update_subgoal(subs[0].subgoal_id, "done", result="answer", task_id="task-abc")
    got = (await store.list_subgoals("obj-1"))[0]
    assert got.status == "done"
    assert got.result == "answer"
    assert got.task_id == "task-abc"


async def test_append_and_list_events(pool: DbPool) -> None:
    store = ObjectiveStore(pool, "principal-default")
    await store.create(_objective())
    await store.append_event("obj-1", "created", "objective created")
    await store.append_event("obj-1", "subgoal_done", "finished step a")
    events = await store.list_events("obj-1")
    assert [e.kind for e in events] == ["created", "subgoal_done"]
    assert events[1].detail == "finished step a"


async def test_owner_scoping_isolates_objectives(pool: DbPool) -> None:
    mine = ObjectiveStore(pool, "principal-default")
    other = ObjectiveStore(pool, "principal-other")
    await mine.create(_objective())
    # Another principal cannot see my objective.
    assert await other.list_objectives() == []
    with pytest.raises(ObjectiveNotFoundError):
        await other.get("obj-1")
