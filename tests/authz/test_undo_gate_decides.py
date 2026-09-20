"""Story 4.5 -- the undo-eligibility gate (`authz.undo.decide_undo`) and its
two constants (`UNDO_WINDOW`, `COMMAND_PRUNE_FLOOR_DAYS`).

Modelled on `test_action_policy_gate_decides.py`'s own style: one test per
scenario, named after it.
"""

from __future__ import annotations

from datetime import timedelta

from stackowl.authz.undo import (
    COMMAND_PRUNE_FLOOR_DAYS,
    UNDO_WINDOW,
    UNDO_WINDOW_DAYS,
    UndoDecision,
    decide_undo,
)
from stackowl.journal.retention import JOURNAL_RETENTION_DAYS


def test_well_inside_the_window_and_not_superseded_is_allowed() -> None:
    result = decide_undo(elapsed=timedelta(hours=1), superseded=False)
    assert result == UndoDecision(allowed=True)


def test_just_under_the_window_is_still_allowed() -> None:
    result = decide_undo(elapsed=UNDO_WINDOW - timedelta(seconds=1), superseded=False)
    assert result.allowed is True


def test_exactly_at_the_window_is_refused_as_expired() -> None:
    result = decide_undo(elapsed=UNDO_WINDOW, superseded=False)
    assert result.allowed is False
    assert result.code == "expired"
    assert result.reason
    assert result.remedy


def test_past_the_window_is_refused_as_expired() -> None:
    result = decide_undo(elapsed=UNDO_WINDOW + timedelta(hours=1), superseded=False)
    assert result.allowed is False
    assert result.code == "expired"


def test_superseded_well_inside_the_window_is_refused() -> None:
    result = decide_undo(elapsed=timedelta(minutes=1), superseded=True)
    assert result.allowed is False
    assert result.code == "superseded"
    assert result.reason
    assert result.remedy


def test_superseded_outranks_expired_when_both_are_true() -> None:
    result = decide_undo(elapsed=UNDO_WINDOW + timedelta(days=1), superseded=True)
    assert result.code == "superseded"


def test_undo_window_is_24_hours() -> None:
    assert timedelta(hours=24) == UNDO_WINDOW


def test_undo_window_days_is_the_ceiling_of_the_window() -> None:
    assert UNDO_WINDOW_DAYS == 1


def test_command_prune_floor_is_never_shorter_than_journal_retention() -> None:
    assert COMMAND_PRUNE_FLOOR_DAYS >= JOURNAL_RETENTION_DAYS


def test_command_prune_floor_is_never_shorter_than_the_undo_window() -> None:
    assert COMMAND_PRUNE_FLOOR_DAYS >= UNDO_WINDOW_DAYS
