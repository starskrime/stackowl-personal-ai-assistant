""""Leaving it open for the loop" has to mean the loop can SEE it.

FOUND BY DRIVING A REAL TURN, 2026-08-20, validating the delivery-proof fix. A
one-shot goal targeted at ``cli`` produced its answer and had nowhere to send it::

    22:45:10 [loop] the drive finished but the answer has not reached its
             destination yet — leaving the task open for the delivery path
    22:45:10 [notifications] recipient.from_job: channel has no durable address
    22:45:10 [scheduler] _deliver_answer: rollup=undeliverable
    tasks: task-d11dcf560e41  status=running  destination=cli
                              delivered_at=NULL  result='OK'  attempt_count=0

The honest state — the answer exists, nobody received it — which is exactly what
the delivery-proof fix is for. But then NOTHING HAPPENS TO IT.

``complete_agent_task`` logs "leaving it open for the loop" and returns. The loop
claims ``status='pending'``; this row is ``running``. The only thing that touches a
stale ``running`` row is ``TaskLivenessSweepHandler``, and that path re-drives
through ``DurableTaskRunner`` WITHOUT incrementing ``attempt_count`` — so the row
would be re-driven every 600s, forever, at one model call each, with no ceiling and
no escalation. A claim with no mechanism behind it, and the fix that made the state
honest is what turned it from a silent lie into a silent grind.

THE LOOP ALREADY HAS ALL OF THIS. ``fail_and_requeue`` counts attempts, carries
what failed into the next try, stops at the ceiling and dead-letters with an
operator escalation. Returning the row through it is not a new mechanism — it is
the sentence the log line was already claiming.

AND THE VERDICT ALREADY KNOWS WHICH KIND OF FAILURE IT IS. ``_deliver_answer``'s
own contract distinguishes them: "undeliverable → (no target — retry won't help)"
with ``transient_failure=False``, against "partial"/"failed" which it marks for
retry. So undeliverable maps onto the ``permanent`` failure class — one immediate
dead-letter and one message to the operator, rather than thirty model calls
against a channel that has no address.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class _Store:
    def __init__(self) -> None:
        self.delivered: list[tuple[str, str]] = []
        self.requeued: list[dict[str, object]] = []

    async def mark_delivered(self, task_id: str, *, result: str) -> None:
        self.delivered.append((task_id, result))

    async def fail_and_requeue(
        self, task_id: str, *, error: str, failure_class: str = "",
        banned: tuple[str, ...] = (),
    ) -> str:
        self.requeued.append(
            {"task_id": task_id, "error": error, "failure_class": failure_class}
        )
        return "pending"


async def _complete(store: object, *, status: str, result: str = "OK") -> None:
    from stackowl.pipeline.durable.agent_task import complete_agent_task

    await complete_agent_task(
        store, task_id="task-d11dcf560e41", result=result, delivery_status=status,
    )


class TestAnAnswerThatArrivedIsUnchanged:
    @pytest.mark.parametrize("status", ["completed", "delivered", "suppressed"])
    async def test_a_delivered_answer_completes_and_is_never_requeued(
        self, status: str
    ) -> None:
        """The happy path must not move. Requeueing a delivered task would send the
        user a second copy of an answer they already have."""
        store = _Store()
        await _complete(store, status=status)

        assert store.delivered == [("task-d11dcf560e41", "OK")]
        assert store.requeued == []


class TestAnAnswerThatDidNotArriveGoesBackOnTheLoop:
    async def test_an_undeliverable_answer_is_returned_permanently(self) -> None:
        """The measured case. No durable address means no retry can help, and the
        loop's permanent class is one dead-letter plus one message to the operator
        — not thirty model calls into a channel with nowhere to send."""
        store = _Store()
        await _complete(store, status="undeliverable")

        assert store.delivered == []
        assert len(store.requeued) == 1
        assert store.requeued[0]["failure_class"] == "permanent"
        assert "undeliverable" in str(store.requeued[0]["error"])

    @pytest.mark.parametrize("status", ["partial", "failed"])
    async def test_a_transient_delivery_failure_is_returned_retryable(
        self, status: str
    ) -> None:
        """``_deliver_answer`` marks these for retry, so they must NOT dead-letter
        on the first miss — a channel that was briefly down deserves the ceiling it
        already has."""
        store = _Store()
        await _complete(store, status=status)

        assert store.delivered == []
        assert len(store.requeued) == 1
        assert store.requeued[0]["failure_class"] != "permanent"

    async def test_a_requeue_failure_never_raises(self) -> None:
        """Bookkeeping must not cost the run. The handler owns the JobResult and a
        raise here would turn a delivery problem into a crashed job."""

        class _Broken(_Store):
            async def fail_and_requeue(self, task_id: str, **kw: object) -> str:
                raise RuntimeError("the table is gone")

        await _complete(_Broken(), status="undeliverable")

    async def test_an_empty_answer_is_left_alone(self) -> None:
        """A goal that answered NO_NOTIFY_NEEDED produced nothing to deliver, so it
        owes nothing — requeueing it would grind a correctly-quiet job."""
        store = _Store()
        await _complete(store, status="completed", result="   ")

        assert store.delivered == []
        assert store.requeued == []
