"""A floored turn retries on ITS OWN row of the one loop — not in a queue of its own.

Bakir, 2026-08-28: "I do not like how you going in circle. You are fixing which
we do not need actually instead of fixing core of issue not issue itself."

He is right, and this is the core he named. His standing rule of 2026-08-17:
everything the platform does is a TASK on ONE loop, and no implementation may
duplicate logic or code that already runs work. "Work to do" was living in FOUR
tables — tasks(1,324), retry_queue(5,766), objective_subgoals(72), jobs(105) —
so stopping one runaway took three separate actions, orphans fell between
engines, and every fix had to be made in each of them. That is the circle.

THE TWO ENGINES DISAGREED ABOUT THE MOST IMPORTANT THING. `tasks` caps at
max_attempts (30) and then reaches dead_letter. retry_queue's own docstring says
it re-arms "forever — no attempt cap, no terminal give-up". The unbounded one is
what messaged Bakir for hours: 5,766 rows, every one addressed to telegram.

WHAT THIS FILE USED TO ASSERT, AND WHY IT CHANGED — recorded rather than deleted,
because the invariants survived even though their OWNER moved.

The first migration moved the retry off `retry_queue` and into a `tasks` row that
``persist_turn`` minted itself (``retry-<trace_id>``). That was still TWO
producers for one turn, and it produced the operator report of 2026-08-29: "it
always sends failed request first then after some time I am getting final answer."
Measured on trace e6c1d3e1 — the second row was enqueued at 17:25:37, claimed by
the loop at 17:25:38 while the fast path was still inside deliver, which sent the
floor at 17:25:40; the loop's answer arrived at 17:26:01 as a second message.

So ``persist_turn`` no longer enqueues anything. The floor is expressed where every
other unachieved outcome already is: ``floored_turn_of`` -> ``complete_turn_task``
-> ``fail_and_requeue`` ON THE ROW THAT ALREADY EXISTS.

THE TWO INCIDENTS ARE STILL PAID FOR, by one row rather than by a dedup rule over
many — which is why the assertions below moved rather than disappearing:

* 2026-07-16 — every floored turn minting its own row, each firing independently,
  reading as the agent contradicting itself. There is now ONE row per turn BY
  CONSTRUCTION, so there is nothing left to deduplicate. Asserted as: the requeue
  targets the turn's own trace_id, and nothing is enqueued.
* 2026-07-21 — a second floor while one was pending was silently DROPPED, and
  nothing ever retried it. A repeat floor is now another failed attempt against the
  same row's attempt_count, which is also what bounds it.
* A REPLAY does not enqueue again — still true, and now true for every turn.
* Bookkeeping NEVER blocks delivery.

VERIFIED LIVE, 2026-08-29, trace 916bb4b77e5f406bb6f258ad045bb57c: the turn floored,
returned to its own row twice (`outcome: pending`), reached `dead_letter` on the
third, and the row closed at attempt_count 5. Bounded, terminating, one row.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


class _FakeTaskStore:
    """Records every mutation so a second producer cannot hide as a first."""

    def __init__(self) -> None:
        self.enqueued: list[Any] = []
        self.requeued: list[dict[str, Any]] = []
        self.completed: list[str] = []

    async def enqueue(self, task: Any) -> None:
        self.enqueued.append(task)

    async def fail_and_requeue(
        self, trace_id: str, *, error: str = "", failure_class: str = "", **_: Any
    ) -> str:
        self.requeued.append(
            {"trace_id": trace_id, "error": error, "failure_class": failure_class}
        )
        return "pending"

    async def update_status(self, trace_id: str, status: str, **_: Any) -> None:
        if status == "completed":
            self.completed.append(trace_id)

    async def mark_delivered(self, trace_id: str, **_: Any) -> None:
        self.completed.append(trace_id)

    async def get(self, *_a: Any, **_k: Any) -> None:
        return None


def _floored_state(
    *,
    trace_id: str = "t1",
    session_key: str = "owl:secretary:telegram:dm:72055773",
    channel: str = "telegram",
    reply_target: int | str | None = 72055773,
    retry_replay: bool = False,
) -> PipelineState:
    return PipelineState(
        trace_id=trace_id,
        session_key=session_key,
        input_text="find me remote staff SWE roles",
        channel=channel,
        owl_name="secretary",
        pipeline_step="deliver",
        reply_target=reply_target,
        retry_replay=retry_replay,
        responses=(
            ResponseChunk(
                content="I couldn't fully complete this.",
                is_final=True, chunk_index=0, trace_id=trace_id,
                owl_name="secretary", is_floor=True,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# The retry now belongs to the row that already exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_floored_turn_goes_back_on_the_ONE_loop() -> None:
    """THE core change. The retry lives on the turn's own row, not a new table."""
    from stackowl.pipeline.durable.turn_task import complete_turn_task

    store = _FakeTaskStore()
    await complete_turn_task(
        store, trace_id="t1", result="a floor reached the user",
        state=_floored_state(),
    )

    assert store.requeued, "the floored turn was never returned to the loop"
    assert store.requeued[0]["trace_id"] == "t1", (
        "the retry must target the turn's OWN row; a different id is a second producer"
    )
    assert not store.enqueued, (
        "a floored turn minted a NEW row — that is the two-producer bug of trace "
        "e6c1d3e1, which answered one question twice"
    )


@pytest.mark.asyncio
async def test_the_retry_carries_WHAT_FAILED_not_just_that_it_failed() -> None:
    """A blind retry runs the same failing path again.

    The loop's own contract: a failure returns the row to pending *with what
    failed*, so the next attempt is constrained rather than blind.
    """
    from stackowl.pipeline.durable.turn_task import (
        FLOORED_TURN_CLASS,
        complete_turn_task,
    )

    store = _FakeTaskStore()
    await complete_turn_task(
        store, trace_id="t1", result="floor", state=_floored_state(),
    )
    rec = store.requeued[0]
    assert rec["failure_class"] == FLOORED_TURN_CLASS, (
        f"the requeue is unclassified ({rec['failure_class']!r}) — the ceiling and "
        "the escalation both key on the class"
    )
    assert rec["error"].strip(), "the retry was requeued with no reason to act on"


@pytest.mark.asyncio
async def test_a_second_floor_is_another_ATTEMPT_never_a_second_row() -> None:
    """Incidents 2026-07-16 and 2026-07-21, both, in one assertion.

    Two floors on one session used to mint two independently-firing rows (07-16),
    and the fix for that silently DROPPED the second floor (07-21). Against one row
    per turn, a repeat floor is simply another attempt — bounded by attempt_count
    and never dropped.
    """
    from stackowl.pipeline.durable.turn_task import complete_turn_task

    store = _FakeTaskStore()
    await complete_turn_task(
        store, trace_id="t1", result="floor", state=_floored_state(trace_id="t1"),
    )
    await complete_turn_task(
        store, trace_id="t1", result="floor", state=_floored_state(trace_id="t1"),
    )

    assert not store.enqueued, "a second floor minted a row (incident 2026-07-16)"
    assert len(store.requeued) == 2, (
        f"the second floor was DROPPED ({len(store.requeued)} requeues) — that is "
        "incident 2026-07-21, where nothing ever retried it"
    )
    assert {r["trace_id"] for r in store.requeued} == {"t1"}


@pytest.mark.asyncio
async def test_a_clean_turn_is_never_requeued() -> None:
    """The guard must stay narrow — every ordinary turn must be untouched."""
    from stackowl.pipeline.durable.turn_task import complete_turn_task

    clean = _floored_state().evolve(
        responses=(
            ResponseChunk(
                content="here are the roles I found", is_final=True, chunk_index=0,
                trace_id="t1", owl_name="secretary", is_floor=False,
            ),
        )
    )
    store = _FakeTaskStore()
    await complete_turn_task(store, trace_id="t1", result="real answer", state=clean)
    assert not store.requeued, "a clean turn was returned to the loop"


@pytest.mark.asyncio
async def test_bookkeeping_never_blocks_delivery() -> None:
    """B5. A store that raises must not take the user's answer down with it."""
    from stackowl.pipeline.durable.turn_task import complete_turn_task

    class _Exploding(_FakeTaskStore):
        async def fail_and_requeue(self, *a: Any, **k: Any) -> str:
            raise RuntimeError("db is gone")

    got = await complete_turn_task(
        _Exploding(), trace_id="t1", result="floor", state=_floored_state(),
    )
    assert got == "requeue_failed", (
        "a requeue we could not perform must report itself, not claim ownership — "
        "claiming it would HOLD the floor and the user would get nothing at all"
    )


# ---------------------------------------------------------------------------
# And the producer that was deleted must stay deleted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_turn_enqueues_NOTHING(monkeypatch: Any) -> None:
    """The deletion, at the seam that used to do it.

    Driven through the real ``persist_turn`` rather than by reading source, so a
    re-introduced enqueue on any path is caught, not just the one string.
    """
    from tests.pipeline._retry_seam import run_floored_turn

    store = _FakeTaskStore()
    await run_floored_turn(store, monkeypatch)
    assert not store.enqueued, (
        "persist_turn minted a durable row again — one turn, two producers, and on "
        "Telegram the user sees two replies to one question"
    )


@pytest.mark.asyncio
async def test_a_replay_also_enqueues_nothing(monkeypatch: Any) -> None:
    """A replay's floor belongs to the row already tracking it, or rows multiply."""
    from tests.pipeline._retry_seam import run_floored_turn

    store = _FakeTaskStore()
    await run_floored_turn(store, monkeypatch, retry_replay=True)
    assert not store.enqueued
