"""D01.7 slice 3b part 6 — invariant I4's remaining two conditions.

Bakir's Q12 protects a lane from expiry under FOUR conditions. Two were already
enforced (a running background process, a pending clarify). The other two — an
in-flight DURABLE TASK and an ACTIVE OBJECTIVE — could not be asked at all,
because neither table stored the lane. Migration 0098 adds it; these tests pin
what "active on this lane" means.

WHY THE QUERIES ARE OWNER-AGNOSTIC. DurableTaskStore and ObjectiveStore are
OwnedRepository subclasses: every read is bound to one principal. The busy-check
cannot use them. Objectives are created under DEFAULT_PRINCIPAL_ID
(``objective_tool.py``) while a lane's identity_key is the PERSON, so an
owner-scoped read would match nothing and invariant I4 would be a silent no-op for
the third time in this item. The lane itself is the scope here: a row carrying
``session_key = <this lane>`` belongs to this conversation whichever principal id
happened to be stamped on it, and the check returns a BOOLEAN, never row content.
"""

from __future__ import annotations

import datetime

import pytest

from stackowl.db.pool import DbPool
from stackowl.objectives.store import any_active_objective_for_lane
from stackowl.pipeline.durable.store import any_active_task_for_lane

pytestmark = pytest.mark.asyncio

LANE = "owl:Brain:telegram:dm:123"
OTHER_LANE = "owl:Brain:telegram:dm:999"


async def _insert_task(db: DbPool, *, task_id: str, status: str,
                       session_key: str | None = LANE,
                       owner: str = "principal-default") -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat()
    await db.execute(
        "INSERT INTO tasks (task_id, owner_id, goal, status, current_step,"
        " created_at, updated_at, session_key) VALUES (?, ?, 'g', ?, 0, ?, ?, ?)",
        (task_id, owner, status, now, now, session_key),
    )


async def _insert_objective(db: DbPool, *, objective_id: str, status: str,
                            session_key: str | None = LANE,
                            owner: str = "principal-default") -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat()
    await db.execute(
        "INSERT INTO objectives (objective_id, owner_id, intent, status,"
        " created_at, updated_at, session_key) VALUES (?, ?, 'i', ?, ?, ?, ?)",
        (objective_id, owner, status, now, now, session_key),
    )


# ------------------------------------------------------------- durable tasks


@pytest.mark.parametrize("status", ["pending", "running", "recovering", "parked"])
async def test_a_non_terminal_task_makes_the_lane_busy(tmp_db: DbPool,
                                                      status: str) -> None:
    """Every non-terminal status counts, including `parked`.

    A parked task is waiting on a human, which is exactly the work a 4 AM sweep
    must not sever — it is suspended, not finished.
    """
    await _insert_task(tmp_db, task_id=f"t-{status}", status=status)
    assert await any_active_task_for_lane(tmp_db, LANE) is True


@pytest.mark.parametrize("status", ["completed", "failed"])
async def test_a_terminal_task_does_not_hold_the_lane_open(tmp_db: DbPool,
                                                          status: str) -> None:
    """The live DB holds 722 tasks, all terminal. If these counted, no lane would
    ever roll again."""
    await _insert_task(tmp_db, task_id=f"t-{status}", status=status)
    assert await any_active_task_for_lane(tmp_db, LANE) is False


async def test_a_task_on_another_lane_is_not_this_lane_s_business(
    tmp_db: DbPool,
) -> None:
    await _insert_task(tmp_db, task_id="t-other", status="running",
                       session_key=OTHER_LANE)
    assert await any_active_task_for_lane(tmp_db, LANE) is False


async def test_a_task_with_no_lane_never_blocks_anyone(tmp_db: DbPool) -> None:
    """Historical rows and non-turn-born primitives carry NULL. A NULL lane must
    not match every lane, or one legacy row freezes every boundary for ever."""
    await _insert_task(tmp_db, task_id="t-null", status="running", session_key=None)
    assert await any_active_task_for_lane(tmp_db, LANE) is False


async def test_the_check_ignores_which_principal_owns_the_row(
    tmp_db: DbPool,
) -> None:
    """THE POINT OF THE OWNER-AGNOSTIC QUERY. An owner-scoped read would miss this
    row and I4 would silently never fire."""
    await _insert_task(tmp_db, task_id="t-someone-else", status="running",
                       owner="principal-somebody-entirely-different")
    assert await any_active_task_for_lane(tmp_db, LANE) is True


async def test_an_empty_lane_is_not_busy(tmp_db: DbPool) -> None:
    assert await any_active_task_for_lane(tmp_db, LANE) is False


async def test_an_empty_lane_key_is_never_treated_as_a_match(
    tmp_db: DbPool,
) -> None:
    """A caller with no lane must not accidentally match NULL-lane rows."""
    await _insert_task(tmp_db, task_id="t-null2", status="running", session_key=None)
    assert await any_active_task_for_lane(tmp_db, "") is False


# --------------------------------------------------------------- objectives


@pytest.mark.parametrize("status", ["active", "blocked"])
async def test_a_live_objective_makes_the_lane_busy(tmp_db: DbPool,
                                                   status: str) -> None:
    """`blocked` counts: an objective stalled on a blocker is still in flight and
    still expects its conversation to exist when the blocker clears."""
    await _insert_objective(tmp_db, objective_id=f"o-{status}", status=status)
    assert await any_active_objective_for_lane(tmp_db, LANE) is True


@pytest.mark.parametrize("status", ["done", "abandoned"])
async def test_a_finished_objective_releases_the_lane(tmp_db: DbPool,
                                                      status: str) -> None:
    await _insert_objective(tmp_db, objective_id=f"o-{status}", status=status)
    assert await any_active_objective_for_lane(tmp_db, LANE) is False


async def test_an_objective_on_another_lane_is_ignored(tmp_db: DbPool) -> None:
    await _insert_objective(tmp_db, objective_id="o-other", status="active",
                            session_key=OTHER_LANE)
    assert await any_active_objective_for_lane(tmp_db, LANE) is False


async def test_an_objective_under_a_different_principal_still_counts(
    tmp_db: DbPool,
) -> None:
    """Objectives are created under DEFAULT_PRINCIPAL_ID while a lane's identity is
    the person — the precise mismatch that would have made this a no-op."""
    await _insert_objective(tmp_db, objective_id="o-other-owner", status="active",
                            owner="principal-someone-else")
    assert await any_active_objective_for_lane(tmp_db, LANE) is True


# ------------------------------------------------- the model carries the lane


async def test_a_durable_task_round_trips_its_lane(tmp_db: DbPool) -> None:
    """The column is useless unless create() writes it and get() reads it back."""
    from stackowl.pipeline.durable.store import DurableTaskStore
    from stackowl.pipeline.durable.task import DurableTask

    now = datetime.datetime.now(datetime.UTC)
    store = DurableTaskStore(tmp_db)
    await store.create(DurableTask(
        task_id="t-roundtrip", owner_id="principal-default", goal="g",
        status="running", session_key=LANE, created_at=now, updated_at=now,
    ))
    assert (await store.get("t-roundtrip")).session_key == LANE


async def test_an_objective_round_trips_its_lane(tmp_db: DbPool) -> None:
    from stackowl.objectives.model import Objective
    from stackowl.objectives.store import ObjectiveStore

    store = ObjectiveStore(tmp_db)
    await store.create(Objective(
        objective_id="o-roundtrip", owner_id="principal-default", intent="i",
        session_key=LANE,
    ))
    assert (await store.get("o-roundtrip")).session_key == LANE
