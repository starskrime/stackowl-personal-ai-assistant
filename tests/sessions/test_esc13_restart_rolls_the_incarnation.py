"""ESC-13 — a restarted core is a new incarnation.

THE MEASUREMENT, 2026-08-15. D01.6's metric 4 asks whether the system prompt is
byte-identical within one incarnation, which is the invariant D01.1 shipped to
guarantee. Grouped by conversation_id it read 29 of 37 stable (78.4%). All 8 failures
had one shape: same owl, same model, two internally-consistent prompt hashes
separated by a time gap, with the prompt changing SIZE across it — a snapshot
re-minted under a conversation_id that outlived the process holding it. Three of the
eight had a core restart sitting exactly between the two prompt windows.

Bakir chose ROLLING THE conversation_id over persisting the snapshot: a restarted core
IS a new incarnation, it costs nothing, and the alternative asks a snapshot to
outlive the process that owns it.

TWO SUB-DECISIONS THIS FILE PINS, because "roll the id" does not settle them and
getting either wrong would be worse than the bug:

  * A RESTART DOES NOT ANNOUNCE. The other four reasons mean the user's thread of
    talk ended, so a rollover is published and subscribers summarise what closed.
    A restart means only that the process died. 29 core starts were logged on
    2026-08-15 alone, so publishing would invent hundreds of conversation
    boundaries a day and enqueue a summary for each.
  * A RESTART DOES NOT RESET THE COUNTERS. They describe the user's conversation,
    which did not stop because we redeployed — and resetting them would shift the
    idle and daily boundaries every time we ship.
"""

from __future__ import annotations

import datetime

import pytest

from stackowl.sessions.models import ResetReason, SessionEntry
from stackowl.sessions.policy import Branch, ResetMode, ResetPolicy, resolve

NOW = datetime.datetime(2026, 8, 15, 16, 0, tzinfo=datetime.UTC)
BOOT = datetime.datetime(2026, 8, 15, 15, 0, tzinfo=datetime.UTC)
BEFORE_BOOT = datetime.datetime(2026, 8, 15, 14, 0, tzinfo=datetime.UTC)
AFTER_BOOT = datetime.datetime(2026, 8, 15, 15, 30, tzinfo=datetime.UTC)


def _policy() -> ResetPolicy:
    """Boundaries disarmed, so only the restart trigger can fire."""
    return ResetPolicy(mode=ResetMode.NONE)


def _entry(created: datetime.datetime, **kw: object) -> SessionEntry:
    return SessionEntry(
        session_key="owl:secretary:telegram:dm:1",
        conversation_id="20260815_140000_aaaaaaaa",
        owl_name="secretary",
        channel="telegram",
        created_at=created,
        updated_at=created,
        message_count=7,
        completed_turns=3,
        **kw,  # type: ignore[arg-type]
    )


class TestTheTrigger:
    def test_an_incarnation_older_than_the_process_rolls(self) -> None:
        got = resolve(_entry(BEFORE_BOOT), NOW, _policy(), process_started_at=BOOT)

        assert got.mints_new_incarnation
        assert got.reason is ResetReason.RESTART

    def test_an_incarnation_started_under_THIS_process_does_not(self) -> None:
        """The common case by far — most turns follow the previous one with no
        restart between them, and rolling there would be a bug of its own."""
        got = resolve(_entry(AFTER_BOOT), NOW, _policy(), process_started_at=BOOT)

        assert not got.mints_new_incarnation
        assert got.branch is Branch.EXISTING

    def test_no_process_timestamp_means_no_restart_trigger(self) -> None:
        """Callers that do not supply it keep their previous behaviour exactly."""
        got = resolve(_entry(BEFORE_BOOT), NOW, _policy(), process_started_at=None)

        assert not got.mints_new_incarnation


class TestItYieldsToThingsThatMatterMore:
    def test_crash_RECOVERY_still_wins(self) -> None:
        """The interaction most likely to be got wrong. A restart is EXACTLY when
        resume_pending is set, and that branch exists to preserve the incarnation
        so the turn being recovered is not discarded. Rolling first would break
        crash recovery in order to fix a metric."""
        entry = _entry(BEFORE_BOOT, resume_pending=True)

        got = resolve(entry, NOW, _policy(), process_started_at=BOOT)

        assert got.branch is Branch.RESUME
        assert not got.mints_new_incarnation

    def test_a_suspended_lane_still_reports_SUSPENDED(self) -> None:
        """The hard wipe keeps its own reason; a restart must not relabel it."""
        entry = _entry(BEFORE_BOOT, suspended=True)

        got = resolve(entry, NOW, _policy(), process_started_at=BOOT)

        assert got.reason is ResetReason.SUSPENDED

    def test_active_work_suppresses_the_roll(self) -> None:
        """An agent working through a redeploy must not have the conversation cut
        from under it — invariant I4, applied to this trigger like the others."""
        got = resolve(
            _entry(BEFORE_BOOT), NOW, _policy(),
            has_active_work=True, process_started_at=BOOT,
        )

        assert not got.mints_new_incarnation


class TestWhatARestartMeansToTheUser:
    def test_it_is_NOT_announced_as_a_new_conversation(self) -> None:
        """29 core starts were logged on 2026-08-15. Announcing each one would
        tell the user their conversation expired 29 times in a day."""
        assert not ResetReason.RESTART.is_automatic

    def test_it_does_not_end_the_conversation(self) -> None:
        """So no rollover is published and no summary is enqueued for a boundary
        that never happened."""
        assert not ResetReason.RESTART.ends_the_conversation

    @pytest.mark.parametrize(
        "reason",
        [ResetReason.DAILY, ResetReason.IDLE, ResetReason.EXPLICIT,
         ResetReason.CONTEXT_FULL, ResetReason.SUSPENDED],
    )
    def test_every_other_reason_still_ends_it(self, reason: ResetReason) -> None:
        """The new property must not quietly change what the original four mean."""
        assert reason.ends_the_conversation
