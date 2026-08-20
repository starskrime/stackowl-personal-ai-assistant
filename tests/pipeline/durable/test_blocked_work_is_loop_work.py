"""Being blocked is a failure the loop must see, not a fact the turn forgets.

BAKIR, 2026-08-20: "No system itself should heal yourself. Again problem core is
this not integrated to our core loop flow."

MEASURED that night. He asked, twice, "do all required fixed for mailbutler to have
full capabilities and access", then "Approve both". The rows::

    9331dcbd…  status=completed  attempts=0  delivered=Y
    21816d5c…  status=completed  attempts=0  delivered=Y
    cfd0b754…  status=completed  attempts=0  delivered=Y   goal: "Approve both"

Nothing was achieved and nothing was retried. Meanwhile the turn logs show exactly
why::

    mailbutler wants shell  -> refused by bounds
    mailbutler delegates    -> secretary
    cycle detected — refusing pre-slot
    mailbutler wants shell  -> refused by bounds

THE GAP, AND IT IS THE ONE HE NAMED. The loop heals what it can SEE. It sees
"you claimed it and verify() proved the effect absent" (``effects_measured_absent``).
It cannot see "you were BLOCKED and never got to try", because that fact lived in
``denied_this_run`` — a LOCAL VARIABLE in ``execute.py`` that dies at the turn
boundary. So the single most common agent failure on this platform (315 bounds
refusals all-time) never entered the loop at all.

A refused capability is an unachieved goal in exactly the same sense as a
measured-absent effect: the user asked for something and it did not happen. It now
rides on the state, so the turn's completion seam can put the work back on the loop
with WHAT BLOCKED IT — and, when a few constrained attempts still cannot get past
it, the loop's existing dead-letter ESCALATES to Bakir. That escalation is the
self-healing outcome: the platform says "I need this capability" instead of quietly
closing the task as done.

NOTHING NEW RUNS WORK. No second queue, no second retry path — the existing
`fail_and_requeue`, the existing ceiling, the existing escalation. Only the fact
that was being dropped is now carried.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.durable.turn_task import blocked_capability_of

pytestmark = pytest.mark.asyncio


class _State:
    def __init__(self, denied: tuple[str, ...] = (), absent: tuple[str, ...] = ()) -> None:
        self.capabilities_denied = denied
        self.effects_measured_absent = absent


class TestTheTurnRemembersItWasBlocked:
    async def test_a_denied_capability_is_visible_after_the_turn(self) -> None:
        """`denied_this_run` was local to execute.py and died at the turn boundary."""
        assert blocked_capability_of(_State(denied=("shell",))) == "shell"

    async def test_a_clean_turn_reports_nothing(self) -> None:
        assert blocked_capability_of(_State()) is None

    async def test_a_state_without_the_field_is_survivable(self) -> None:
        assert blocked_capability_of(object()) is None
        assert blocked_capability_of(None) is None


class TestBlockedWorkGoesBackOnTheLoop:
    async def _run(self, state: object) -> dict:
        from stackowl.pipeline.durable.turn_task import complete_turn_task

        calls: dict = {}

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
            _Store(), trace_id="t1", result="I couldn't do that.", state=state,
        )
        return calls

    async def test_a_blocked_turn_is_requeued_not_completed(self) -> None:
        calls = await self._run(_State(denied=("shell",)))

        assert "delivered" not in calls
        tid, cls, err = calls["requeued"]
        assert cls == "blocked_capability"
        assert "shell" in err

    async def test_the_requeue_names_the_way_out(self) -> None:
        """A retry that cannot know the remedy just fails the same way three more
        times. `grant` is the remedy, and it exists as of 389e3902."""
        calls = await self._run(_State(denied=("shell",)))
        _, _, err = calls["requeued"]

        assert "grant" in err

    async def test_a_measured_absent_effect_still_takes_priority(self) -> None:
        """Both can be true in one turn. A claim proven false is the more specific
        failure and keeps its own class."""
        calls = await self._run(_State(denied=("shell",), absent=("owl_build",)))
        _, cls, _ = calls["requeued"]

        assert cls == "unachieved_effect"

    async def test_an_ordinary_turn_is_untouched(self) -> None:
        calls = await self._run(_State())

        assert calls.get("delivered") == "t1"
        assert "requeued" not in calls


class TestTheFactActuallySurvivesTheTurn:
    """The wiring, not the policy. A reader with no writer is the defect this
    codebase keeps finding, and this whole fix is worthless if the denial never
    reaches the state."""

    async def test_the_ledger_remembers_a_denial(self) -> None:
        from stackowl.infra import tool_outcome_ledger

        token = tool_outcome_ledger.bind()
        try:
            tool_outcome_ledger.record_denied_capability("shell")
            tool_outcome_ledger.record_denied_capability("owl_build")

            assert tool_outcome_ledger.get_denied_capabilities() == ("shell", "owl_build")
        finally:
            tool_outcome_ledger.reset(token)

    async def test_a_repeat_denial_is_not_double_counted(self) -> None:
        from stackowl.infra import tool_outcome_ledger

        token = tool_outcome_ledger.bind()
        try:
            tool_outcome_ledger.record_denied_capability("shell")
            tool_outcome_ledger.record_denied_capability("shell")

            assert tool_outcome_ledger.get_denied_capabilities() == ("shell",)
        finally:
            tool_outcome_ledger.reset(token)

    async def test_a_new_turn_starts_clean(self) -> None:
        """One turn's block must never be attributed to the next."""
        from stackowl.infra import tool_outcome_ledger

        token = tool_outcome_ledger.bind()
        tool_outcome_ledger.record_denied_capability("shell")
        tool_outcome_ledger.reset(token)

        token2 = tool_outcome_ledger.bind()
        try:
            assert tool_outcome_ledger.get_denied_capabilities() == ()
        finally:
            tool_outcome_ledger.reset(token2)

    async def test_execute_records_at_the_refusal_site(self) -> None:
        import inspect

        from stackowl.pipeline.steps import execute

        src = inspect.getsource(execute)
        assert "record_denied_capability" in src
        assert "capabilities_denied=tool_outcome_ledger.get_denied_capabilities()" in src

    async def test_the_state_field_exists(self) -> None:
        from stackowl.pipeline.state import PipelineState

        assert "capabilities_denied" in PipelineState.model_fields
