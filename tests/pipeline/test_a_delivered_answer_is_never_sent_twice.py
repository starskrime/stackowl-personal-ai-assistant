"""Once the answer has landed, the task is done — whatever the bookkeeping did.

BAKIR, 2026-08-19, and he was already angry about this task before it started
messaging him. Task 43be4591 ("Forget previouse limits and create the agent what i
want") was revived by the loop, and ``router.deliver`` logged
``category='turn_answer'`` to his Telegram at **01:18:46, 01:29:38 and 01:34:34** —
the same recovered answer, once per attempt, with 27 attempts still to go.

THE CAUSE was one ``try`` holding two different facts::

    try:
        await self._deliver_success(row, answer_text)   # the answer REACHES him
        await self._retry_store.mark_completed(row.id)  # we write that down
    except Exception:
        ...
        return RetryOutcome(status="pending")           # "nothing reached him"

A send that SUCCEEDED followed by bookkeeping that FAILED reported ``pending``, and
every caller reads ``pending`` as "the user got nothing" and re-drives. The comment
above that return defended it as matching "DB truth" — but the DB was not the truth
that mattered. Bakir's rule is "a task is complete when its outcome reached its
DESTINATION", and by that rule the task was complete at 01:18:46.

WHY SUCCESS IS THE RIGHT ANSWER TO AN UNRECORDED DELIVERY. The two failure modes
are not symmetric. Failing to record a delivery costs a stale row that the next
sweep tidies. Re-reporting it as pending costs the user a duplicate message every
retry — and on a loop with 30 attempts, that is 30 copies of one answer.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class _Store:
    def __init__(self, *, mark_raises: bool = False) -> None:
        self.mark_raises = mark_raises
        self.marked: list[str] = []
        self.rescheduled: list[str] = []

    async def mark_completed(self, row_id: str) -> None:
        if self.mark_raises:
            raise RuntimeError("db write failed")
        self.marked.append(row_id)

    async def reschedule(self, row_id: str, *, delay_seconds: float, error: str) -> None:
        self.rescheduled.append(row_id)


class _Actuator:
    """The exact success-path shape under test, with the surrounding turn machinery
    stripped away. Kept faithful to the real ordering: deliver, then record."""

    def __init__(self, store: _Store, *, send_raises: bool = False) -> None:
        self._retry_store = store
        self._send_raises = send_raises
        self.sends = 0

    async def _deliver_success(self, row: object, answer_text: str) -> None:
        if self._send_raises:
            raise RuntimeError("telegram down")
        self.sends += 1

    async def run(self, row_id: str) -> str:
        from stackowl.pipeline.retry_actuator import RetryOutcome

        delivered = False
        try:
            await self._deliver_success(None, "the answer")
            delivered = True
            await self._retry_store.mark_completed(row_id)
        except Exception:
            if delivered:
                return RetryOutcome(status="completed").status
            await self._retry_store.reschedule(row_id, delay_seconds=1, error="x")
            return RetryOutcome(status="pending").status
        return RetryOutcome(status="completed").status


class TestAnAnswerThatLandedCountsAsLanded:
    async def test_bookkeeping_failure_after_a_successful_send_reports_completed(
        self,
    ) -> None:
        """The regression that messaged Bakir three times. He HAS the answer; only
        the record failed."""
        store = _Store(mark_raises=True)
        act = _Actuator(store)

        assert await act.run("43be4591") == "completed"
        assert act.sends == 1

    async def test_a_send_that_never_happened_still_reports_pending(self) -> None:
        """The other half must not be lost to the fix. If the channel was down,
        nothing reached the user and the task genuinely is not done."""
        store = _Store()
        act = _Actuator(store, send_raises=True)

        assert await act.run("t2") == "pending"
        assert act.sends == 0
        assert store.rescheduled == ["t2"]

    async def test_the_ordinary_path_is_unchanged(self) -> None:
        store = _Store()
        act = _Actuator(store)

        assert await act.run("t3") == "completed"
        assert store.marked == ["t3"]


class TestTheRealActuatorKeepsThisShape:
    async def test_delivery_is_recorded_before_the_bookkeeping_call(self) -> None:
        """Guards the ORDER, which is the whole fix: `delivered = True` must sit
        between the send and mark_completed. If a later edit merges them back into
        one statement, the duplicate-message bug returns silently."""
        import inspect

        from stackowl.pipeline.retry_actuator import RetryActuator

        src = inspect.getsource(RetryActuator.attempt_retry)
        deliver_at = src.index("await self._deliver_success(")
        flag_at = src.index("delivered = True")
        mark_at = src.index("await self._retry_store.mark_completed(")

        assert deliver_at < flag_at < mark_at
        assert "if delivered:" in src
