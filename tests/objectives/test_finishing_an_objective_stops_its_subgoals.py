"""Ending an objective must end the work it spawned.

MEASURED 2026-08-28, after Bakir said: "Right now, I'm getting so many, like, job
position related messages, but I did remove agent which used to send it."

REMOVING THE AGENT CHANGED NOTHING, and this is why. Seven jobmarket objectives
were all TERMINAL — four ``abandoned``, two ``done``, one ``blocked`` — and 44 of
their sub-goals were still live: 33 ``pending`` and 11 ``running``. Ten of the
eleven running ones belonged to objectives that were already ``done`` or
``abandoned``.

``ObjectiveStore.update_status`` writes the objective row and nothing else. So
"abandon this objective" marked a row and left every sub-goal exactly where it
was, claimable and running, with no owner and nobody expecting their output.

WHY THAT BECAME A MESSAGE FLOOD RATHER THAN JUST STALE ROWS. Each live sub-goal
carries a task, each task retries when it cannot deliver, and the retry queue had
5,766 jobmarket rows addressed to telegram. Orphaned work is not inert here — it
is a running loop that talks to the user.

TWO ROOT CAUSES STACKED, which is why fixing one did not stop the messages.
81f6b7ec fixed the first (a task's destination named the channel and not the
address, so nothing could ever be delivered and everything retried for ever).
This is the second: terminated objectives keep feeding that loop. The first fix
alone left the orphans in place.

A TERMINAL OBJECTIVE IS A DECISION THAT THE WORK IS OVER. If its sub-goals
outlive it, the decision did not take effect — the same "write with no reader"
shape this tree keeps producing, except the unread write is the user's own
instruction to stop.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.objectives.store import ObjectiveStore

OWNER = "principal-default"

TERMINAL = ["done", "abandoned"]


async def _objective_with_subgoals(store: ObjectiveStore) -> str:
    from stackowl.objectives.model import Objective

    obj = Objective(
        objective_id="obj-test01", owner_id=OWNER,
        intent="find remote staff SWE roles", channel="telegram",
    )
    await store.create(obj)
    await store.add_subgoals(obj.objective_id, ["search boards", "dedupe", "notify the user"])
    return obj.objective_id


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", TERMINAL)
async def test_a_terminal_objective_leaves_no_live_subgoals(
    tmp_db: DbPool, terminal_status: str
) -> None:
    """THE regression. 44 sub-goals outlived seven terminal objectives."""
    store = ObjectiveStore(tmp_db, OWNER)
    objective_id = await _objective_with_subgoals(store)

    await store.update_status(objective_id, terminal_status)  # type: ignore[arg-type]

    subgoals = await store.list_subgoals(objective_id)
    live = [s for s in subgoals if s.status in ("pending", "running")]
    assert not live, (
        f"{len(live)} sub-goal(s) still live after the objective went "
        f"{terminal_status} — they keep running and keep messaging the user"
    )


@pytest.mark.asyncio
async def test_a_RUNNING_subgoal_is_stopped_too_not_just_a_pending_one(
    tmp_db: DbPool,
) -> None:
    """Ten of the eleven orphans found in production were RUNNING, not pending.

    Cancelling only the pending ones would have left the actively-executing work
    — which is precisely the half that was producing messages.
    """
    store = ObjectiveStore(tmp_db, OWNER)
    objective_id = await _objective_with_subgoals(store)
    subgoals = await store.list_subgoals(objective_id)
    await store.update_subgoal(subgoals[0].subgoal_id, status="running")

    await store.update_status(objective_id, "abandoned")

    after = await store.list_subgoals(objective_id)
    assert not [s for s in after if s.status == "running"], (
        "a running sub-goal survived its objective being abandoned"
    )


@pytest.mark.asyncio
async def test_a_NON_terminal_transition_leaves_the_work_alone(tmp_db: DbPool) -> None:
    """The control, and the reason this is not just 'cancel everything'.

    `active` and `blocked` are LIVE states — an objective that blocks on a
    question is resumed later, and killing its work would silently discard
    progress the user is waiting on. Only done/abandoned end the work.
    """
    store = ObjectiveStore(tmp_db, OWNER)
    objective_id = await _objective_with_subgoals(store)

    await store.update_status(objective_id, "blocked", blocker="needs a decision")

    subgoals = await store.list_subgoals(objective_id)
    assert [s for s in subgoals if s.status == "pending"], (
        "blocking an objective destroyed its pending work — it must survive to "
        "be resumed"
    )


@pytest.mark.asyncio
async def test_already_finished_subgoals_are_not_rewritten(tmp_db: DbPool) -> None:
    """A done sub-goal keeps its own verdict; the cascade only stops LIVE work."""
    store = ObjectiveStore(tmp_db, OWNER)
    objective_id = await _objective_with_subgoals(store)
    subgoals = await store.list_subgoals(objective_id)
    await store.update_subgoal(subgoals[0].subgoal_id, status="done")

    await store.update_status(objective_id, "done")

    after = {s.subgoal_id: s.status for s in await store.list_subgoals(objective_id)}
    assert after[subgoals[0].subgoal_id] == "done"
