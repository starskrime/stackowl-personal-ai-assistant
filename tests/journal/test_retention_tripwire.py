"""AD-4/NFR45's tripwire: a subsystem prune window shorter than journal
retention is a real defect (the referenced row could be gone before the event
that references it). Proven today against the REAL owner-authorized values --
``TaskLoopSettings.prune_completed_after_days`` (tasks) and
``db_reclaim._RUN_HISTORY_RETENTION_DAYS`` (job_runs) -- asked directly, never
restated as a copy (mirrors
``tests/audit/test_a_tunable_states_its_value_in_one_place.py``'s style: the
constant this test reasons about is the one the production code actually
holds, not a hand-typed number that could drift from it).

Story 2.11 raised ``JOURNAL_RETENTION_DAYS`` to AD-6's real 30-day default
(derived from ``JournalSettings()``, see ``journal/retention.py``'s
docstring) and raised both real subsystem windows this test checks to 30 in
the same change (DW-17), so this check stays honest today rather than
tautological. ``test_a_shorter_fake_window_fails_the_tripwire`` is the
control that proves that: a fake window regressed below the constant must
actually raise.
"""

from __future__ import annotations

import pytest

from stackowl.config.task_loop_settings import TaskLoopSettings
from stackowl.journal.retention import JOURNAL_RETENTION_DAYS
from stackowl.scheduler.handlers.db_reclaim import _RUN_HISTORY_RETENTION_DAYS


def _assert_window_not_shorter_than_retention(name: str, window_days: float) -> None:
    """AD-4, verbatim: "Owning rows referenced by record_ref are kept at
    least as long as journal retention; a tripwire fails any referenced
    record kind whose prune window is shorter." The one assertion every
    checked window runs through, so there is exactly one way to fail it.
    """
    assert window_days >= JOURNAL_RETENTION_DAYS, (
        f"{name}'s prune window ({window_days} day(s)) is SHORTER than the "
        f"journal's own retention ({JOURNAL_RETENTION_DAYS} day(s)) -- a "
        "job/task row a journal event references could be pruned before the "
        "event that references it is (AD-4)"
    )


@pytest.mark.tripwire
class TestTodaysRealPruneWindowsPassTheTripwire:
    def test_task_prune_window_is_not_shorter_than_journal_retention(self) -> None:
        # The REAL default, constructed from the settings model itself -- not
        # a hand-typed copy of the number it currently holds.
        _assert_window_not_shorter_than_retention(
            "TaskLoopSettings.prune_completed_after_days",
            TaskLoopSettings().prune_completed_after_days,
        )

    def test_job_run_prune_window_is_not_shorter_than_journal_retention(self) -> None:
        _assert_window_not_shorter_than_retention(
            "db_reclaim._RUN_HISTORY_RETENTION_DAYS", _RUN_HISTORY_RETENTION_DAYS,
        )


@pytest.mark.tripwire
def test_a_shorter_fake_window_fails_the_tripwire() -> None:
    """THE CONTROL. A fake window constructed strictly below the current
    constant must actually FAIL -- proves the assertion above is a real
    comparison against the constant's CURRENT value, not a tautology that
    would pass no matter what ``JOURNAL_RETENTION_DAYS`` held."""
    with pytest.raises(AssertionError):
        _assert_window_not_shorter_than_retention(
            "a hypothetical regressed prune window",
            JOURNAL_RETENTION_DAYS - 1,
        )
