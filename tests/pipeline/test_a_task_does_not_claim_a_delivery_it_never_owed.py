"""A task said its completion meant delivery, and carried nowhere to deliver to.

MEASURED 2026-09-09 in the live database. `tasks` carries the three columns that
encode Bakir's completion rule — *"a task is complete when its outcome reached its
DESTINATION, not when the function returned"* — `destination`, `achievement`,
`delivered_at`. Of the SIX rows whose achievement reads *"the answer is delivered to
the job's targets"*, **THREE have a NULL destination.**

WHY THAT MATTERS RATHER THAN BEING UNTIDY. `DurableTaskStore._owes_delivery` keys on
`destination`, and `update_status` uses it to REFUSE a `completed` transition for a row
that still owes an answer. A NULL destination makes that guard return False. So for
those three rows the one mechanism enforcing the rule was disabled by a missing field,
while the row's own achievement said delivery was the condition. The record asserted a
contract nothing could hold it to.

THE CAUSE, at `task_runner.py`: `destination` came from `destination_for_turn`, which
correctly returns None when a turn owes nothing (deferred and unaddressed — its parent
delivers), and `achievement` was an unconditional literal on the next line. Two
independent decisions about ONE contract, written adjacently. CLAUDE.md's third defect
shape exactly: *two copies of one rule — one source; have the other ask it.*

AND IT IS THE THIRD ROUND OF THIS DEFECT IN THIS FUNCTION. The comments already there
record the first two, both about getting the destination right: a bare channel name is
not an address (ten jobmarket tasks cycling for hours), then NULL-for-everyone letting
rows complete undelivered. Each fixed the destination. Neither asked whether the
sentence beside it still matched.

WHAT IS NOT CLAIMED. This does not change which rows complete — the guard already keys
on `destination` and still does. It stops the RECORD from asserting a delivery the row
never owed, which is the difference between a database a later mechanism can trust and
one it cannot.
"""

from __future__ import annotations

import inspect

import pytest

from stackowl.pipeline.durable import task_runner
from stackowl.pipeline.durable.turn_task import destination_for_turn


class TestTheTwoFieldsComeFromOneDecision:
    @pytest.mark.tripwire
    def test_the_achievement_asks_the_destination(self) -> None:
        """The fix, at the only site that writes both."""
        source = inspect.getsource(task_runner)

        assert "achievement=(" in source, "the achievement is a flat literal again"
        assert "if _destination" in source, (
            "the achievement no longer asks whether this turn owes a delivery"
        )

    @pytest.mark.tripwire
    def test_the_destination_is_computed_once(self) -> None:
        """Two calls to `destination_for_turn` would be the same defect wearing the
        fix's clothes: the field and the sentence could still disagree if the state
        changed between them."""
        source = inspect.getsource(task_runner)

        assert source.count("destination_for_turn(") == 1, (
            "the destination is derived more than once — the two fields can drift again"
        )

    @pytest.mark.tripwire
    def test_a_turn_that_owes_nothing_does_not_claim_a_delivery(self) -> None:
        """The behaviour, stated against the helper's real contract rather than a
        restatement of it: deferred AND unaddressed means None."""
        owes_nothing = destination_for_turn(
            channel="rca", reply_target=None, defer_delivery=True
        )
        owes_delivery = destination_for_turn(
            channel="telegram", reply_target="telegram:72055773", defer_delivery=False
        )

        assert owes_nothing is None
        assert owes_delivery, "an addressed turn lost its destination"

    def test_the_guard_this_protects_still_keys_on_the_destination(self) -> None:
        """The reason a NULL destination was dangerous, pinned so the rationale above
        cannot quietly stop being true. If `_owes_delivery` ever stops reading
        `destination`, this file's argument needs rewriting, not silently keeping."""
        from stackowl.pipeline.durable.store import DurableTaskStore

        source = inspect.getsource(DurableTaskStore._owes_delivery)  # noqa: SLF001

        assert "destination" in source and "delivered_at" in source
