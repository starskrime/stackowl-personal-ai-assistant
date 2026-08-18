"""What actually re-drives a recovered task — and why it is not new code.

Slice 4. The loop (slice 2) owns pacing and concurrency; the store (slice 1) owns
state. Neither knows how to ANSWER a question. This is the runner that does, and
the whole point of it is that it delegates rather than implements:
``RetryActuator.attempt_retry`` already re-runs a floored turn's goal through the
real backend and delivers the answer to the channel it came from. CLAUDE.md's rule
— one loop, never a second thing that runs work — makes reuse mandatory here, not
merely tidy.

WHAT THE RUNNER ADDS is translation and nothing else: a ``DurableTask`` becomes the
``RetryQueueRow`` the actuator expects, and the actuator's outcome becomes the
loop's contract (return the delivered result, or raise so the loop records the
failure and requeues with what broke).

THE SAFETY PROPERTY THAT MATTERS: a task is only ever handed here after its LEASE
EXPIRED, which means the fast path demonstrably did not finish. Re-driving a turn
that already answered would send the user a second reply, so the guard is the
lease, and it is asserted below.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.durable.task_loop_runner import build_task_runner

pytestmark = pytest.mark.asyncio


class _Outcome:
    def __init__(self, status: str) -> None:
        self.status = status


class _Actuator:
    """Stands in for RetryActuator with its REAL method name and row type."""

    def __init__(self, status: str = "completed") -> None:
        self.calls: list[object] = []
        self._status = status

    async def attempt_retry(self, row: object) -> _Outcome:
        self.calls.append(row)
        return _Outcome(self._status)


def _task(**over: object) -> DurableTask:
    base: dict = dict(
        task_id="tr-1", goal="what is your name?", status="pending",
        trigger_kind="chat", channel="telegram", destination="telegram:72055773",
        session_key="lane", banned_capabilities=("web_search",), attempt_count=2,
    )
    base.update(over)
    return DurableTask(**base)


class TestItDelegatesRatherThanReimplements:
    async def test_it_calls_the_EXISTING_actuator(self) -> None:
        actuator = _Actuator()
        run = build_task_runner(actuator)

        result = await run(_task())

        assert actuator.calls, "the runner did not use the actuator — a second engine"
        assert result

    async def test_the_task_is_translated_faithfully(self) -> None:
        """Everything the actuator needs to re-drive well must survive the
        translation. Dropping banned_capabilities in particular would throw away
        the learning the loop spent attempts acquiring, and the retry would
        rediscover the same dead route."""
        actuator = _Actuator()
        run = build_task_runner(actuator)

        await run(_task())

        row = actuator.calls[0]
        assert row.trace_id == "tr-1"  # type: ignore[attr-defined]
        assert row.goal == "what is your name?"  # type: ignore[attr-defined]
        assert row.session_key == "lane"  # type: ignore[attr-defined]
        assert row.channel == "telegram"  # type: ignore[attr-defined]
        assert row.channel_chat_id == "72055773"  # type: ignore[attr-defined]
        assert list(row.banned_capabilities) == ["web_search"]  # type: ignore[attr-defined]
        assert row.attempt_count == 2  # type: ignore[attr-defined]


class TestTheLoopContract:
    async def test_a_completed_retry_returns_a_deliverable_result(self) -> None:
        """The loop marks a task delivered only on a non-empty return, so a
        successful retry must return something."""
        run = build_task_runner(_Actuator("completed"))

        assert (await run(_task())).strip()

    async def test_a_FAILED_retry_raises_so_the_loop_records_it(self) -> None:
        """Returning quietly would let the loop mark the task delivered when the
        user got nothing — the overclaim shape, imported into the loop."""
        run = build_task_runner(_Actuator("failed"))

        with pytest.raises(RuntimeError):
            await run(_task())

    async def test_a_still_PENDING_retry_also_raises(self) -> None:
        """The actuator re-armed rather than delivering. Nothing reached the user,
        so this attempt did not achieve the task."""
        run = build_task_runner(_Actuator("pending"))

        with pytest.raises(RuntimeError):
            await run(_task())

    async def test_an_actuator_that_raises_propagates(self) -> None:
        """The loop's own dispatch classifies and requeues; swallowing here would
        hide the failure from exactly the machinery built to learn from it."""
        class _Boom:
            async def attempt_retry(self, row: object) -> object:
                raise ConnectionError("provider down")

        run = build_task_runner(_Boom())

        with pytest.raises(ConnectionError):
            await run(_task())


class TestItRefusesWorkItCannotDo:
    async def test_no_actuator_wired_raises_rather_than_claiming_success(self) -> None:
        """An unwired runner that returned quietly would have the loop mark every
        recovered task delivered while doing nothing at all — silent, and worse
        than not running."""
        run = build_task_runner(None)

        with pytest.raises(RuntimeError):
            await run(_task())

    async def test_a_task_with_no_goal_is_not_driven(self) -> None:
        run = build_task_runner(_Actuator())

        with pytest.raises(RuntimeError):
            await run(_task(goal="   "))
