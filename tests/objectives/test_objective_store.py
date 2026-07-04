"""ObjectiveStore — owner-scoped persistence for standing objectives (1A).

An objective is a persistent intent the assistant decomposes into ordered
sub-goals and works across many autonomous turns until done/blocked. The store
round-trips the objective, its ordered sub-goals, and an activity-event log,
all owner-scoped via :class:`OwnedRepository`.
"""

from __future__ import annotations

import uuid as uuid_module
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.objectives.model import Objective, SubgoalSpec
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


# ------------------------- Task 3: replace_subgoal_with_children (atomicity) -


async def test_replace_subgoal_with_children_uses_one_transaction() -> None:
    """Source-scan guard (mirrors test_fts_base_atomic.py's precedent): the
    shift+insert+delete sequence must be routed through DbPool.transaction(),
    never separate auto-committing statements."""
    import inspect

    src = inspect.getsource(ObjectiveStore.replace_subgoal_with_children)
    assert "transaction(" in src


async def test_replace_subgoal_with_children_inserts_and_deletes_together(
    pool: DbPool,
) -> None:
    store = ObjectiveStore(pool, "principal-default")
    await store.create(_objective())
    subs = await store.add_subgoals("obj-1", ["step a", "step b"])
    parent = subs[0]

    children = await store.replace_subgoal_with_children(
        "obj-1", parent,
        [SubgoalSpec(description="child 1"), SubgoalSpec(description="child 2")],
        depth=1,
    )

    assert [c.description for c in children] == ["child 1", "child 2"]
    all_subs = await store.list_subgoals("obj-1")
    # Parent gone, children took its run-order slot (in ascending position order),
    # later sub-goal shifted back after them — positions need not stay contiguous,
    # only correctly ORDERED (next_pending_subgoal sorts by position).
    assert [s.description for s in all_subs] == ["child 1", "child 2", "step b"]
    positions = [s.position for s in all_subs]
    assert positions == sorted(positions) and len(set(positions)) == 3
    assert all(s.decomposition_depth == 1 for s in all_subs[:2])
    assert all(s.subgoal_id != parent.subgoal_id for s in all_subs)


async def test_replace_subgoal_with_children_rolls_back_atomically_on_failure(
    pool: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure partway through the shift+insert+delete sequence must roll back
    EVERYTHING — the parent must survive intact (not deleted, not left at a
    shifted position with an orphan child already committed). Otherwise a crash
    mid-sequence would leave the original sub-goal alive alongside its own
    already-inserted children, and the driver would re-split/re-run it a
    second time on restart (the exact double-execution bug this method fixes).

    Forces a genuine SQL failure (a PRIMARY KEY collision on the second child's
    INSERT) rather than mocking away the transaction primitive — the same style
    tests/memory/test_fts_base_atomic.py uses for the base+FTS atomicity guard.
    """
    import sqlite3

    from stackowl.objectives import store as store_mod

    store = ObjectiveStore(pool, "principal-default")
    await store.create(_objective())
    subs = await store.add_subgoals("obj-1", ["only step"])
    parent = subs[0]

    collision_id = uuid_module.uuid4()
    monkeypatch.setattr(store_mod.uuid, "uuid4", lambda: collision_id)

    # A genuine SQL constraint failure (PRIMARY KEY collision on the 2nd child's
    # INSERT) — NOT a stand-in for "method missing"/AttributeError, so this only
    # passes when the transaction genuinely rolled back a real mid-sequence error.
    with pytest.raises(sqlite3.IntegrityError):
        await store.replace_subgoal_with_children(
            "obj-1", parent,
            [SubgoalSpec(description="child 1"), SubgoalSpec(description="child 2")],
            depth=1,
        )

    all_subs = await store.list_subgoals("obj-1")
    # Nothing committed: the parent survives, unmoved, and no orphan child exists.
    assert [s.subgoal_id for s in all_subs] == [parent.subgoal_id]
    assert all_subs[0].position == parent.position
    assert all_subs[0].description == "only step"
