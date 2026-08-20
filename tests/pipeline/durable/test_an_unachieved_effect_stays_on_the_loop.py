"""Delivering an apology is not the same as doing the work.

BAKIR, 2026-08-19: "if I'm asking to do something, he does." MEASURED across every
log the platform has written:

    overclaim.detected      134
    overclaim.corrected      51     (the WORDING was corrected)
    overclaim.refulfilled     1     (the WORK was actually redone)
    overclaim.refulfil_failed 1
    ------------------------------
    detected and then nothing:  132

By culprit: retrieval 87, **owl_build 16**, web_fetch 14, scheduling_commit 12.

So the platform already KNOWS when it claimed something it did not do. The delivery
gate makes ONE in-turn corrective attempt; when that fails it floors the answer to an
honest "I couldn't complete this" and the turn completes as DELIVERED. The reply
reached him, so by the loop's rule the task was done — while the thing he asked for
had not happened. 132 times.

THE DISTINCTION THIS FIXES. For a question, the outcome IS the answer and delivery
is achievement. For an EFFECTFUL request ("create the agent"), the outcome is the
effect, and an apology about the effect is not the effect. A turn whose own
verification MEASURED the promised effect absent has not achieved its goal, so it
must go back on the loop carrying what failed — which is the loop's stated contract:
"a failure returns the row to pending WITH what failed, so the next attempt is
constrained rather than blind."

WHY MEASURED-ABSENT AND NOT MERELY UNVERIFIED. ``effects_measured_absent`` is the
strict subset whose own verify() OBSERVED the effect to be missing. Re-driving those
cannot double a side effect, because nothing landed. An unknown outcome is left
alone — the burden of proof stays on the claim, and an uncertain effect is never
re-run on a guess.

THE ATTEMPT BUDGET IS DELIBERATELY SMALL. A chat turn's ordinary ceiling is 30. An
effect that has already been measured absent once, with the failure fed back, is
either fixable in a few tries or needs Bakir — and each attempt can produce another
message to him. It dead-letters quickly instead, which ESCALATES to him once. That
is the honest trade, stated rather than hidden: a few retries and one escalation,
never a silent grind.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.durable.turn_task import unachieved_effect_of

pytestmark = pytest.mark.asyncio


class _State:
    def __init__(self, absent: tuple[str, ...] = ()) -> None:
        self.effects_measured_absent = absent


class TestItRecognisesWorkThatDidNotHappen:
    async def test_a_measured_absent_effect_is_unachieved(self) -> None:
        """owl_build is 16 of the 132 — Bakir's agent creation."""
        assert unachieved_effect_of(_State(("owl_build",))) == "owl_build"

    async def test_the_first_of_several_is_reported(self) -> None:
        got = unachieved_effect_of(_State(("owl_build", "cronjob")))

        assert got in ("owl_build", "cronjob")

    async def test_a_clean_turn_has_nothing_unachieved(self) -> None:
        assert unachieved_effect_of(_State(())) is None

    async def test_a_state_without_the_field_is_survivable(self) -> None:
        """Never let bookkeeping cost a delivered turn."""
        assert unachieved_effect_of(object()) is None

    async def test_none_is_survivable(self) -> None:
        assert unachieved_effect_of(None) is None


class TestTheTurnGoesBackOnTheLoopInsteadOfCompleting:
    async def test_an_unachieved_turn_is_requeued_not_delivered(self) -> None:
        """The reply DID reach him — the honest floor. But the work did not happen,
        so the row must not record achievement."""
        from stackowl.pipeline.durable.turn_task import complete_turn_task

        calls: dict[str, object] = {}

        class _Store:
            async def mark_delivered(self, task_id: str, *, result: str) -> None:
                calls["delivered"] = task_id

            async def fail_and_requeue(
                self, task_id: str, *, error: str, failure_class: str = "",
                banned: tuple[str, ...] = (),
            ) -> str:
                calls["requeued"] = (task_id, failure_class, error)
                return "pending"

        await complete_turn_task(
            _Store(), trace_id="t1", result="I could not complete this.",
            state=_State(("owl_build",)),
        )

        assert "delivered" not in calls, "an unachieved effect must not read as done"
        tid, cls, err = calls["requeued"]  # type: ignore[misc]
        assert tid == "t1"
        assert cls == "unachieved_effect"
        assert "owl_build" in err, "the next attempt has to know WHAT did not happen"

    async def test_an_achieved_turn_still_completes_normally(self) -> None:
        """The ordinary path must be untouched — this is every working turn."""
        from stackowl.pipeline.durable.turn_task import complete_turn_task

        calls: dict[str, object] = {}

        class _Store:
            async def mark_delivered(self, task_id: str, *, result: str) -> None:
                calls["delivered"] = task_id

            async def fail_and_requeue(self, task_id: str, **kw: object) -> str:
                calls["requeued"] = task_id
                return "pending"

        await complete_turn_task(
            _Store(), trace_id="t2", result="here is your answer",
            state=_State(()),
        )

        assert calls.get("delivered") == "t2"
        assert "requeued" not in calls

    async def test_a_requeue_that_fails_never_costs_the_turn(self) -> None:
        from stackowl.pipeline.durable.turn_task import complete_turn_task

        class _Store:
            async def mark_delivered(self, task_id: str, *, result: str) -> None:
                raise AssertionError("must not be called")

            async def fail_and_requeue(self, task_id: str, **kw: object) -> str:
                raise RuntimeError("db down")

        await complete_turn_task(
            _Store(), trace_id="t3", result="x", state=_State(("owl_build",)),
        )

    async def test_no_state_behaves_exactly_as_before(self) -> None:
        """Callers that pass no state keep the original contract byte-for-byte."""
        from stackowl.pipeline.durable.turn_task import complete_turn_task

        calls: dict[str, object] = {}

        class _Store:
            async def mark_delivered(self, task_id: str, *, result: str) -> None:
                calls["delivered"] = task_id

        await complete_turn_task(_Store(), trace_id="t4", result="answer")

        assert calls.get("delivered") == "t4"
