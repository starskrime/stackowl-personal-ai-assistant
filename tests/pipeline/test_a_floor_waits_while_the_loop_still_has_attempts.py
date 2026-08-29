"""Bakir does not see the failure while the loop is still working on it.

HIS WORDS, 2026-08-29: "I do not want to get failed response if loop not finalized
yet."

THE SHAPE, and why the qualifier is the whole design. Unconditional suppression was
measured LETHAL: 75% of floored turns are provider-cascade outages where the retry
runs on the same dead backend and floors too, and historically only ~27% of floored
turns had any retry behind them at all (136 of 505). Suppressing every floor turns
"I can't reach my provider" into silence.

"If the loop is not finalized yet" is CONDITIONAL suppression, gated on the loop
actually owning the follow-up — which only became answerable once the duplicate
producer was removed, because before that two rows held contradictory verdicts.

THE PROPERTY THAT MAKES IT SELF-GUARANTEEING — no held-message store, no deadline
timer, no escalation to trust::

    attempt 1 (fast path)  floors -> fail_and_requeue -> 'pending'     -> SUPPRESS
    attempt 2 (loop)       floors -> fail_and_requeue -> 'pending'     -> SUPPRESS
    attempt 3 (loop)       floors -> CEILING reached  -> 'dead_letter' -> DELIVER

The ceiling IS the delivery deadline. Bakir gets exactly one message: the corrected
answer if any attempt succeeds, or the honest floor the moment the loop stops
trying. `floored_turn` is on the 3-attempt ceiling for precisely this reason — on
the default 30 with backoff (5,15,60,300,900) the wait would be hours.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(*, is_floor: bool = True, destination_target: str | None = "72055773") -> PipelineState:
    return PipelineState(
        trace_id="t-supp",
        session_key="owl:secretary:telegram:dm:72055773",
        conversation_id="c-1",
        input_text="do the thing",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="deliver",
        reply_target=destination_target,
        responses=(
            ResponseChunk(
                content="I couldn't fully complete this. The capability that failed: send_message.",
                is_final=True, chunk_index=0, trace_id="t-supp",
                owl_name="secretary", is_floor=is_floor,
            ),
        ),
    )


class _Store:
    def __init__(self, requeue_status: str) -> None:
        self._status = requeue_status
        self.requeued: list[str] = []
        self.completed: list[str] = []

    async def fail_and_requeue(self, task_id: str, **_: Any) -> str:
        self.requeued.append(task_id)
        return self._status

    async def mark_delivered(self, trace_id: str, **_: Any) -> None:
        self.completed.append(trace_id)

    async def update_status(self, trace_id: str, status: str, **_: Any) -> None:
        if status == "completed":
            self.completed.append(trace_id)

    async def get(self, *_a: Any, **_k: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_a_requeued_floor_is_HELD_not_sent() -> None:
    """The requirement, stated as Bakir stated it."""
    from stackowl.pipeline.durable.turn_task import complete_turn_task

    store = _Store(requeue_status="pending")
    outcome = await complete_turn_task(
        store, trace_id="t-supp", result="floor", state=_state(),
    )
    assert outcome == "requeued", (
        "deliver cannot suppress unless completion REPORTS that the loop took "
        "ownership — suppressing on 'floored' alone silences turns nothing retries"
    )


@pytest.mark.asyncio
async def test_a_floor_at_the_CEILING_is_delivered() -> None:
    """The guarantee. Without this, suppression is silence with extra steps."""
    from stackowl.pipeline.durable.turn_task import complete_turn_task

    store = _Store(requeue_status="dead_letter")
    outcome = await complete_turn_task(
        store, trace_id="t-supp", result="floor", state=_state(),
    )
    assert outcome != "requeued", (
        "the loop has stopped trying, so the held floor MUST now reach the user"
    )


@pytest.mark.asyncio
async def test_a_clean_turn_reports_completed_and_is_never_held() -> None:
    from stackowl.pipeline.durable.turn_task import complete_turn_task

    store = _Store(requeue_status="pending")
    outcome = await complete_turn_task(
        store, trace_id="t-supp", result="a real answer",
        state=_state(is_floor=False),
    )
    assert outcome != "requeued"
    assert not store.requeued, "a clean turn must never be requeued"


class TestTheDeliverSeam:
    @pytest.mark.asyncio
    async def test_a_held_floor_is_not_written_to_the_stream(self) -> None:
        """Measure the EFFECT: nothing reaches the writer."""
        from stackowl.pipeline.steps import deliver as dm

        written: list[Any] = []

        class _Writer:
            async def write(self, chunk: Any) -> None:
                written.append(chunk)

            async def close(self) -> None: ...

        assert await dm._suppress_floor_while_the_loop_retries(
            _state(), outcome="requeued",
        ) is True
        assert not written

    @pytest.mark.asyncio
    async def test_an_unaddressed_destination_is_NEVER_held(self) -> None:
        """22 live rows carry a bare channel and are proven undeliverable.

        Holding a floor for a row the loop can never deliver to is permanent
        silence, so the floor goes out immediately regardless of the requeue.
        """
        from stackowl.pipeline.steps import deliver as dm

        assert await dm._suppress_floor_while_the_loop_retries(
            _state(destination_target=None), outcome="requeued",
        ) is False

    @pytest.mark.asyncio
    async def test_a_dead_lettered_floor_is_NEVER_held(self) -> None:
        from stackowl.pipeline.steps import deliver as dm

        assert await dm._suppress_floor_while_the_loop_retries(
            _state(), outcome="dead_letter",
        ) is False
