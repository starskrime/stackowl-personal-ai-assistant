"""A floored turn retries as a TASK, not in a second queue of its own.

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

retry_queue held nothing tasks does not already hold — goal, session_key,
banned_capabilities, attempt_count, next_retry_at, last_error, channel, owner —
every column has a `tasks` equivalent. It was pure duplication.

WHAT THE OLD ENGINE GOT RIGHT AND MUST SURVIVE, because deleting a queue is easy
and deleting its hard-won semantics is how a rewrite loses two live incidents:

* ONE in-flight retry per session (incident 2026-07-16: every floored turn minted
  its own row, each firing independently on the 1-minute sweep, reading as the
  agent contradicting itself). Now expressed as `idempotency_key`, which `tasks`
  already has, instead of a hand-rolled get-latest-then-supersede.
* A SECOND floor must not be DROPPED (incident 2026-07-21: it was, and nothing
  ever retried it). The newest ask repoints the same row.
* A REPLAY does not enqueue again — its floor belongs to the row already tracking
  it, or rows multiply.
* Bookkeeping NEVER blocks delivery.
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakeTaskStore:
    def __init__(self) -> None:
        self.enqueued: list[Any] = []

    async def enqueue(self, task: Any) -> None:
        self.enqueued.append(task)


def _retry_tasks(store: _FakeTaskStore) -> list[Any]:
    return [t for t in store.enqueued if t.trigger_kind == "retry"]


@pytest.mark.asyncio
async def test_a_floored_turn_enqueues_a_TASK(monkeypatch: Any) -> None:
    """THE core change. The retry lives on the one loop, not in a second table."""
    from tests.pipeline._retry_seam import run_floored_turn

    store = _FakeTaskStore()
    await run_floored_turn(store, monkeypatch)

    tasks = _retry_tasks(store)
    assert len(tasks) == 1, f"expected one retry task, got {len(tasks)}"
    assert tasks[0].goal, "the retry task carries no goal to retry"


@pytest.mark.asyncio
async def test_the_retry_is_BOUNDED_unlike_the_queue_it_replaces(
    monkeypatch: Any,
) -> None:
    """The behaviour change, stated so nobody restores 'forever' by accident.

    retry_queue re-armed with no cap and no give-up. That is what produced hours
    of messages. A task carries max_attempts and reaches dead_letter.
    """
    from tests.pipeline._retry_seam import run_floored_turn

    store = _FakeTaskStore()
    await run_floored_turn(store, monkeypatch)

    task = _retry_tasks(store)[0]
    assert task.max_attempts > 0, "an unbounded retry is what flooded the channel"


@pytest.mark.asyncio
async def test_one_in_flight_retry_per_session(monkeypatch: Any) -> None:
    """Incident 2026-07-16, preserved through the migration.

    Two floors in one session must not become two independently-firing retries.
    """
    from tests.pipeline._retry_seam import run_floored_turn

    store = _FakeTaskStore()
    await run_floored_turn(store, monkeypatch, trace_id="t1")
    await run_floored_turn(store, monkeypatch, trace_id="t2")

    keys = {t.idempotency_key for t in _retry_tasks(store)}
    assert len(keys) == 1, (
        f"two floors in one session produced {len(keys)} distinct retry keys — "
        "they will fire independently, which is incident 2026-07-16"
    )


@pytest.mark.asyncio
async def test_a_replay_does_not_enqueue_another_retry(monkeypatch: Any) -> None:
    """A replay's floor belongs to the row already tracking it, or rows multiply."""
    from tests.pipeline._retry_seam import run_floored_turn

    store = _FakeTaskStore()
    await run_floored_turn(store, monkeypatch, retry_replay=True)

    assert not _retry_tasks(store), "a retry replay enqueued a second retry"


@pytest.mark.asyncio
async def test_the_retry_task_knows_WHERE_to_reply(monkeypatch: Any) -> None:
    """The destination carries the ADDRESS, not just the channel.

    81f6b7ec: a destination of "telegram" instead of "telegram:<chat_id>" made
    delivery impossible, so nothing ever completed and everything retried for
    ever. A retry that cannot be delivered is the exact loop being removed here.
    """
    from tests.pipeline._retry_seam import run_floored_turn

    store = _FakeTaskStore()
    await run_floored_turn(store, monkeypatch, channel="telegram", reply_target=72055773)

    dest = _retry_tasks(store)[0].destination
    assert dest and ":" in dest, (
        f"retry destination {dest!r} carries no address — reply_target_for_task "
        "returns None and the retry can never be delivered"
    )


@pytest.mark.asyncio
async def test_bookkeeping_never_blocks_delivery(monkeypatch: Any) -> None:
    """B5. A store that raises must not take the user's answer down with it."""
    from tests.pipeline._retry_seam import run_floored_turn

    class _Exploding(_FakeTaskStore):
        async def enqueue(self, task: Any) -> None:
            raise RuntimeError("db is gone")

    await run_floored_turn(_Exploding(), monkeypatch)  # must not raise
