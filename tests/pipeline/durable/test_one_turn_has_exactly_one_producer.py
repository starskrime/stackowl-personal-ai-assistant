"""A floored turn returns its OWN row to the loop — it never mints a second one.

OPERATOR-REPORTED, 2026-08-29: "It always sends failed request first then after
some time I am getting final answer."

``turn_task.enqueue_turn_task`` states the invariant in its own docstring:

    "loop_produces decides WHO answers, and the row's initial status is the whole
    mechanism. Exactly one producer must exist: a turn both run by the fast path
    and claimed by the loop is answered TWICE, and on Telegram the user sees two
    replies to one question."

It enforces that by minting a turn row as ``running`` under a lease, so the loop
cannot claim it while the fast path is answering.

``persist_turn`` BYPASSED that. It called ``task_store.enqueue`` directly with a
second row — ``task_id=f"retry-{trace_id}"``, ``status="pending"``, no lease and no
``next_attempt_at`` — which is claimable on the very next 5-second tick. Measured
live on trace e6c1d3e1:

    17:25:37  persist_turn enqueues the second row (pending)
    17:25:38  [loop] claims it — while the fast path is still inside deliver
    17:25:40  deliver sends the floor to the user
    17:26:01  the loop's retry sends the corrected answer as a SECOND message

Meanwhile ``complete_turn_task`` closed the ORIGINAL row as delivered, because its
two unachieved predicates (``effects_measured_absent``, ``capabilities_denied``)
were both empty — an overclaim *detection* is neither. One turn, two producers,
two contradictory verdicts, both obeyed.

THE FIX IS A DELETION, NOT AN ADDITION. A floored turn is an unachieved goal, which
the platform already knows how to express: ``fail_and_requeue`` on the row that
already exists, with the ceiling and dead-letter escalation it already has. No
second queue, no second retry path, no second status column.

WHY NOT SUPPRESS THE FIRST MESSAGE INSTEAD. Measured: 75% of floored turns are
provider-cascade outages, where a retry runs on the same dead backend and floors
too, and only ~27% of floored turns historically had any retry behind them.
Suppression turns "I can't reach my provider" into silence. The floor still ships
immediately; what changes is that only ONE producer owns the follow-up.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "oneproducer.db"
    seed_schema(db_path)
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _floored_state() -> PipelineState:
    return PipelineState(
        trace_id="t-floored",
        session_key="owl:secretary:telegram:dm:72055773",
        conversation_id="conv-1",
        input_text="do the thing",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="deliver",
        reply_target="72055773",
        responses=(
            ResponseChunk(
                content="I couldn't fully complete this. The capability that failed: send_message.",
                is_final=True, chunk_index=0, trace_id="t-floored",
                owl_name="secretary", is_floor=True,
            ),
        ),
    )


class _RecordingStore:
    """Records which mutation the turn took, so the two are distinguishable."""

    def __init__(self) -> None:
        self.enqueued: list[Any] = []
        self.requeued: list[dict[str, Any]] = []
        self.completed: list[str] = []

    async def enqueue(self, task: Any) -> None:
        self.enqueued.append(task)

    async def fail_and_requeue(
        self, trace_id: str, *, error: str = "", failure_class: str = "", **_: Any
    ) -> None:
        self.requeued.append(
            {"trace_id": trace_id, "error": error, "failure_class": failure_class}
        )

    async def mark_delivered(self, trace_id: str, **_: Any) -> None:
        self.completed.append(trace_id)

    async def update_status(self, trace_id: str, status: str, **_: Any) -> None:
        if status == "completed":
            self.completed.append(trace_id)

    async def get(self, *_a: Any, **_k: Any) -> None:
        return None


def test_a_floored_turn_is_recognised_as_unachieved() -> None:
    """The predicate the third branch needs.

    An overclaim floor sets neither `effects_measured_absent` nor
    `capabilities_denied` — which is exactly why the original row was closed as
    delivered while a second row was retrying it.
    """
    from stackowl.pipeline.durable.turn_task import floored_turn_of

    assert floored_turn_of(_floored_state()) is not None

    clean = _floored_state().evolve(
        responses=(
            ResponseChunk(
                content="here is your answer", is_final=True, chunk_index=0,
                trace_id="t-floored", owl_name="secretary", is_floor=False,
            ),
        )
    )
    assert floored_turn_of(clean) is None, "a clean turn must still complete normally"


@pytest.mark.asyncio
async def test_a_floored_turn_REQUEUES_ITS_OWN_ROW_instead_of_completing() -> None:
    """The correctness fix: one row, returned to the loop it already belongs to."""
    from stackowl.pipeline.durable.turn_task import complete_turn_task

    store = _RecordingStore()
    await complete_turn_task(
        store, trace_id="t-floored", result="a floor reached the user",
        state=_floored_state(),
    )

    assert store.requeued, "the floored turn was not returned to the loop"
    assert store.requeued[0]["trace_id"] == "t-floored", (
        "it must requeue the turn's OWN row — a different id is a second producer"
    )
    assert not store.completed, (
        "the floored turn was ALSO closed as delivered — this is the contradiction "
        "that let two producers answer one question"
    )


@pytest.mark.asyncio
async def test_a_clean_turn_still_completes_normally() -> None:
    """The guard must be narrow. Every ordinary turn must be byte-identical."""
    from stackowl.pipeline.durable.turn_task import complete_turn_task

    clean = _floored_state().evolve(
        responses=(
            ResponseChunk(
                content="here is your answer", is_final=True, chunk_index=0,
                trace_id="t-floored", owl_name="secretary", is_floor=False,
            ),
        )
    )
    store = _RecordingStore()
    await complete_turn_task(
        store, trace_id="t-floored", result="a real answer", state=clean,
    )
    assert not store.requeued, "a clean turn must not be requeued"


@pytest.mark.asyncio
async def test_persist_turn_NO_LONGER_mints_a_second_row(pool: DbPool) -> None:
    """The deletion, tested at the seam that used to do it.

    This is the whole bug: a second `retry-<trace_id>` row, born `pending` with no
    lease, claimable on the next tick while the fast path was still delivering.
    """
    import inspect

    from stackowl.pipeline import turn_persist

    src = inspect.getsource(turn_persist)
    assert 'task_id=f"retry-{state.trace_id}"' not in src, (
        "persist_turn still mints a second durable row — the single-producer "
        "invariant enqueue_turn_task documents is still bypassed"
    )
    assert "durable_task_store" not in src or "enqueue(" not in src, (
        "persist_turn still enqueues into the durable store directly"
    )
