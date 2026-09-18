"""AD-38's per-turn write budget: under budget is silent, over budget warns
and degrades journal health exactly once per crossing, and NEVER drops the
event that crossed it (Story 2.7).
"""

from __future__ import annotations

import pytest

from stackowl.journal.health import JournalHealthContributor, reset_for_tests
from stackowl.journal.turn_budget import (
    PROVISIONAL_TURN_EVENT_BUDGET,
    check_and_note,
)
from stackowl.journal.turn_budget import (
    reset_for_tests as reset_budget_for_tests,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_for_tests()
    reset_budget_for_tests()
    yield
    reset_for_tests()
    reset_budget_for_tests()


class TestUnderBudget:
    async def test_no_warning_and_no_degrade_below_budget(self, caplog: pytest.LogCaptureFixture) -> None:
        for _ in range(PROVISIONAL_TURN_EVENT_BUDGET):
            over = check_and_note("trace-under")
            assert over is False

        status = await JournalHealthContributor().health_check()
        assert status.status == "ok"


class TestOverBudget:
    async def test_the_crossing_event_warns_once_and_degrades(self) -> None:
        for _ in range(PROVISIONAL_TURN_EVENT_BUDGET):
            check_and_note("trace-over")

        # The (budget + 1)th call is the crossing -- over_budget=True.
        over = check_and_note("trace-over")
        assert over is True

        status = await JournalHealthContributor().health_check()
        assert status.status == "degraded"
        assert "trace-over" in (status.message or "")

    async def test_further_calls_on_the_same_trace_stay_over_but_do_not_re_warn(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        caplog.set_level(logging.WARNING, logger="stackowl.journal")
        for _ in range(PROVISIONAL_TURN_EVENT_BUDGET + 5):
            check_and_note("trace-loud")

        warnings = [
            r for r in caplog.records
            if "per-turn write budget exceeded" in r.getMessage()
        ]
        assert len(warnings) == 1

    async def test_a_different_trace_is_unaffected(self) -> None:
        for _ in range(PROVISIONAL_TURN_EVENT_BUDGET + 1):
            check_and_note("trace-a")

        # A fresh trace starts its own count from zero.
        assert check_and_note("trace-b") is False

    async def test_the_event_is_never_dropped_regardless_of_the_return_value(self) -> None:
        """AD-38, verbatim: "it never drops events." `check_and_note`'s return
        value is advisory only -- the caller (`turn_events.py`) always proceeds
        to insert, whatever this returns. Proven at the integration layer
        (test_turn_journal_integration.py); this test pins the CONTRACT: the
        function itself never raises or signals "refuse this one"."""
        for _ in range(PROVISIONAL_TURN_EVENT_BUDGET + 50):
            result = check_and_note("trace-never-drops")
            assert isinstance(result, bool)
