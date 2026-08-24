"""ESC-45 — a skill cannot have been ignored before it could be seen.

BAKIR, 2026-08-23: "fix retrieval first, then re-judge". ESC-44 shipped and is
verified live, so the re-judge is due.

WHAT THE CURATOR WAS ABOUT TO DO. 92 stale learned skills sit ~51 days idle
against ARCHIVE_AFTER_DAYS = 60, so within days they would have been archived in
essentially one pass. Their idle time was measured from `loaded_at` — when they
entered the catalogue — and until ESC-44 the catalogue was cut ALPHABETICALLY at
roughly a dozen of 160. Those skills were not ignored; they were never shown.
Their idle clock measured retrieval's failure and charged it to them.

It had already happened once: 8 of 14 shipped BUILTINS are archived for non-use,
including plan-and-track, recover-and-retry and web-automation. They were never
chosen and rejected — they were never offered.

WHY A FLOOR RATHER THAN A DATA MIGRATION. The obvious fix is to bump `loaded_at`
on the affected rows. That mutates a provenance field to mean something it does
not — "when it entered the catalogue" would become "when we last felt guilty" —
and it is a one-shot that has to be got right in a single pass over live data.
A floor on the idle CLOCK needs no migration, is idempotent by construction,
states its own reason in one line, and stops mattering by itself once the window
has passed.

DELIBERATELY NOT AN UN-ARCHIVE. Bakir chose "reset the clock, then let decay run"
over the option that also restored the 8 builtins. Archived skills stay archived;
this only affects what happens NEXT.
"""

from __future__ import annotations

import time

from stackowl.skills.lifecycle import (
    ARCHIVE_AFTER_DAYS,
    STALE_AFTER_DAYS,
    VISIBILITY_FLOOR_EPOCH,
    SkillCurator,
)

_DAY = 86_400.0


class _Row:
    def __init__(
        self, *, loaded_at: float, last_used_at: float | None = None,
        n_executions: int = 0,
    ) -> None:
        self.loaded_at = loaded_at
        self.last_used_at = last_used_at
        self.n_executions = n_executions
        self.success_rate = None
        self.name = "s"
        self.lifecycle_state = "active"
        self.pinned = 0


def _idle_days(row: _Row, now: float) -> float:
    return SkillCurator._idle_seconds(None, row, now) / _DAY  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

def test_a_never_shown_skill_does_not_age_from_before_it_was_visible() -> None:
    """The 92-skill population. Loaded long ago, never once presented."""
    now = VISIBILITY_FLOOR_EPOCH + 5 * _DAY
    row = _Row(loaded_at=VISIBILITY_FLOOR_EPOCH - 51 * _DAY)
    assert _idle_days(row, now) == 5.0, (
        "idle must be measured from when the skill became visible, not from when "
        "it was written into a catalogue that never showed it"
    )


def test_it_is_no_longer_at_the_archive_cliff() -> None:
    """Before the floor these were ~51 days into a 60-day window."""
    now = VISIBILITY_FLOOR_EPOCH + 5 * _DAY
    row = _Row(loaded_at=VISIBILITY_FLOOR_EPOCH - 51 * _DAY)
    assert _idle_days(row, now) < ARCHIVE_AFTER_DAYS
    assert _idle_days(row, now) < STALE_AFTER_DAYS


# ---------------------------------------------------------------------------
# It must not become a shield
# ---------------------------------------------------------------------------

def test_decay_still_runs_after_a_fair_window() -> None:
    """The point is a fair chance, not immunity. A skill visible for a full
    archive window and still unused is genuinely unused."""
    now = VISIBILITY_FLOOR_EPOCH + (ARCHIVE_AFTER_DAYS + 1) * _DAY
    row = _Row(loaded_at=VISIBILITY_FLOOR_EPOCH - 51 * _DAY)
    assert _idle_days(row, now) > ARCHIVE_AFTER_DAYS


def test_a_skill_LOADED_after_the_floor_is_unaffected() -> None:
    """The floor must not hand new skills a free window they never needed."""
    loaded = VISIBILITY_FLOOR_EPOCH + 10 * _DAY
    now = loaded + 40 * _DAY
    assert _idle_days(_Row(loaded_at=loaded), now) == 40.0


def test_a_USED_skill_still_ages_from_its_last_use() -> None:
    """A skill that ran has a real signal; the floor must not overwrite it."""
    now = VISIBILITY_FLOOR_EPOCH + 50 * _DAY
    row = _Row(
        loaded_at=VISIBILITY_FLOOR_EPOCH - 90 * _DAY,
        last_used_at=VISIBILITY_FLOOR_EPOCH + 20 * _DAY,
        n_executions=7,
    )
    assert _idle_days(row, now) == 30.0


def test_a_use_from_BEFORE_the_floor_is_still_floored() -> None:
    """It was used, then the catalogue stopped showing it. Same unfairness."""
    now = VISIBILITY_FLOOR_EPOCH + 5 * _DAY
    row = _Row(
        loaded_at=VISIBILITY_FLOOR_EPOCH - 100 * _DAY,
        last_used_at=VISIBILITY_FLOOR_EPOCH - 40 * _DAY,
        n_executions=3,
    )
    assert _idle_days(row, now) == 5.0


# ---------------------------------------------------------------------------
# Unchanged guarantees
# ---------------------------------------------------------------------------

def test_a_missing_clock_still_refuses_to_age() -> None:
    """"A missing timestamp must never be read as 'infinitely old'" — unchanged."""
    row = _Row(loaded_at=0.0)
    row.loaded_at = 0.0
    assert _idle_days(row, time.time()) == 0.0


def test_the_floor_is_in_the_past_and_fixed() -> None:
    """A constant, not `now`. If it drifted forward nothing would ever age."""
    assert VISIBILITY_FLOOR_EPOCH < time.time()
    assert VISIBILITY_FLOOR_EPOCH > 1_700_000_000  # sane epoch, not 0
