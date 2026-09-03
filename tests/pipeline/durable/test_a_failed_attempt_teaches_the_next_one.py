"""What burned on this attempt must reach the next one, or the loop retries blind.

BAKIR'S OWN CONTRACT, quoted in `fail_and_requeue`'s docstring: *"if it fails,
again moving back to pending and adding previous failure or action details. So next
loop when it picks it, it also looks: is any previous one? Yes — learn from that
experience."*

IT WAS NOT TRUE ON THE RETRY PATH. The learning was computed and then discarded
twice:

1. ``_handle_failure`` passed ``newly_failed_capability`` to
   ``RetryQueueStore.mark_attempt_failed`` — a table the ONE-loop migration stopped
   writing (``insert_pending`` has zero callers). It raises, is swallowed, and the
   capability is written nowhere. 19 live occurrences of
   "_handle_failure: mark_attempt_failed failed".
2. ``RetryOutcome`` carried ``status`` and nothing else, so even a caller that
   wanted the learning could not read it — and ``task_loop_runner`` raises on a
   non-completed status, discarding the outcome entirely.
3. ``loop._safe_fail`` then calls ``fail_and_requeue`` WITHOUT ``banned=``, though
   that parameter exists and merges.

MEASURED CONSEQUENCE. Task retry-8b7c4029 failed identically 74 times over 14h33m,
attempt_count 72 against max_attempts 30, because every attempt re-tread the
capability the previous one had already proven dead. `banned_capabilities` is the
mechanism that was supposed to stop exactly that, and nothing was filling it.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.pipeline.retry_actuator import RetryOutcome


def test_the_outcome_can_CARRY_what_burned() -> None:
    """Without a field there is nowhere for the learning to travel."""
    assert RetryOutcome(status="pending").banned == ()
    assert RetryOutcome(status="pending", banned=("web_fetch",)).banned == ("web_fetch",)


@pytest.mark.asyncio
async def test_a_floored_retry_REPORTS_the_capability_it_burned() -> None:
    """The actuator knows it — `_pick_newly_failed` computes it. It must say so."""
    from unittest.mock import AsyncMock, MagicMock

    from stackowl.memory.retry_queue_store import RetryQueueRow
    from stackowl.pipeline.retry_actuator import RetryActuator

    # NO STORE TO SIMULATE ANY MORE. This used to hand in an AsyncMock whose
    # mark_attempt_failed raised ValueError("no matching row") — the dead table's
    # real behaviour, since retry_queue lost its writer on 2026-08-28. That call
    # is deleted (2026-09-03), so the capability now travels on the return value
    # by construction rather than out of an except branch.
    actuator = RetryActuator(backend=MagicMock(), channel_registry=MagicMock())
    row = RetryQueueRow(id="retry-x", trace_id="retry-x", session_key="s", goal="g")

    outcome = await actuator._handle_failure(
        row, "still floored", newly_failed_capability="web_fetch",
    )
    assert outcome.banned == ("web_fetch",), (
        "the capability this attempt burned was computed and then dropped — the "
        "next attempt will re-tread it, which is how 8b7c4029 failed 74 times"
    )


class TestTheLoopActuallyRecordsIt:
    @pytest.mark.asyncio
    async def test_safe_fail_forwards_banned_to_the_store(self) -> None:
        """The last link. fail_and_requeue accepts `banned` and merges it."""
        from stackowl.pipeline.durable.loop import TaskLoop

        recorded: dict[str, Any] = {}

        class _Store:
            async def fail_and_requeue(
                self, task_id: str, *, error: str = "", failure_class: str = "",
                banned: tuple[str, ...] = (),
            ) -> str:
                recorded.update(
                    task_id=task_id, banned=banned, failure_class=failure_class
                )
                return "pending"

        loop = TaskLoop.__new__(TaskLoop)
        loop._store = _Store()  # type: ignore[attr-defined]

        task = type("T", (), {"task_id": "retry-x"})()
        await loop._safe_fail(
            task, error="boom", failure_class="floored_turn", banned=("web_fetch",),
        )
        assert recorded.get("banned") == ("web_fetch",), (
            "the loop dropped the learning on the floor between the runner and the "
            "store — every attempt then re-treads the same dead capability"
        )


    @pytest.mark.asyncio
    async def test_the_runner_attaches_what_burned_to_the_exception_it_raises(
        self,
    ) -> None:
        """The link between the actuator and the loop.

        The runner can only signal failure by raising, so the learning has to ride
        the exception. Without this the loop's `getattr(exc, "banned_capabilities")`
        reads an empty tuple forever — a reader with no writer, which is the same
        shape as the write-with-no-reader this whole arc keeps finding.
        """
        from unittest.mock import AsyncMock, MagicMock

        from stackowl.pipeline.durable.task_loop_runner import build_task_runner
        from stackowl.pipeline.retry_actuator import RetryOutcome

        actuator = MagicMock()
        actuator.attempt_retry = AsyncMock(
            return_value=RetryOutcome(status="pending", banned=("web_fetch",))
        )
        runner = build_task_runner(actuator)
        task = type("T", (), {
            "task_id": "retry-x", "goal": "g", "session_key": "s",
            "banned_capabilities": (), "attempt_count": 1, "last_error": None,
            "channel": "telegram", "destination": "telegram:123",
        })()

        with pytest.raises(RuntimeError) as caught:
            await runner(task)
        assert getattr(caught.value, "banned_capabilities", ()) == ("web_fetch",), (
            "the runner dropped the learning, so the loop has nothing to forward"
        )
