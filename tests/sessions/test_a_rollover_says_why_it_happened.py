"""A conversation that ends must say why, in the log, at INFO.

BAKIR, 2026-08-22: "No idea why but i got 2 times of: — new conversation (the
previous one went quiet) — today in 1 hour."

NEITHER COULD I, and that was the defect. A rollover wrote `auto_reset_reason` to
the lane row and logged NOTHING. That column holds only the LATEST value, so a lane
that rolled on idle and again on a restart keeps no trace of the first. The notice
itself is composed at delivery and never persisted. Measured that day: SIXTEEN
incarnations on `owl:secretary:telegram:dm:72055773`, with gaps of 1, 2, 2 and 3
minutes — and no surviving evidence of why any single one happened.

What could be reconstructed: the `Brain` lane's previous conversation began
2026-08-21T04:04:42 and the new one 2026-08-22T14:57 — a 34-hour gap against a
24-hour threshold, so THAT notice was correct. The second was unrecoverable.

One hypothesis this killed: 24 core restarts that day looked like the cause, until
the other days were counted — 41, 47, 17, 43, 23, 42, 37. Restarts are normal here
and today was on the LOW end. Without the line below, that took a database
excavation to establish and still left half the question open.
"""

from __future__ import annotations

import datetime

import pytest

from stackowl.sessions.models import SessionEntry
from stackowl.sessions.policy import ResetMode, ResetPolicy, ResetReason, resolve

NOW = datetime.datetime(2026, 8, 22, 14, 57, tzinfo=datetime.UTC)


def _entry(updated_minutes_ago: float, *, created_minutes_ago: float | None = None):
    created = NOW - datetime.timedelta(
        minutes=created_minutes_ago
        if created_minutes_ago is not None
        else updated_minutes_ago
    )
    return SessionEntry(
        session_key="owl:Brain:telegram:dm:72055773",
        conversation_id="20260821_040442_aaaaaaaa",
        owl_name="Brain", channel="telegram",
        created_at=created,
        updated_at=NOW - datetime.timedelta(minutes=updated_minutes_ago),
    )


POLICY = ResetPolicy(mode=ResetMode.BOTH, idle_minutes=1440, at_hour=4)


class TestTheDecisionIsAttributable:
    def test_the_live_brain_case_really_was_idle(self) -> None:
        """34 hours against a 24-hour threshold — that notice was correct."""
        decision = resolve(_entry(34 * 60), NOW, POLICY)

        assert decision.mints_new_incarnation
        assert decision.reason is ResetReason.IDLE

    def test_a_lane_touched_minutes_ago_does_NOT_roll_on_idle(self) -> None:
        """The gaps Bakir saw were 1-3 minutes. Idle cannot explain those.

        Pinned so that if a future change makes idle fire on a live conversation,
        it fails here rather than in his chat.
        """
        decision = resolve(_entry(2), NOW, POLICY)

        assert not decision.mints_new_incarnation
        assert decision.reason is None

    def test_active_work_protects_a_live_conversation(self) -> None:
        """Invariant I4 — an agent working must not have its lane cut away."""
        decision = resolve(_entry(34 * 60), NOW, POLICY, has_active_work=True)

        assert not decision.mints_new_incarnation

    def test_a_restart_rolls_the_lane_but_is_NOT_an_idle_notice(self) -> None:
        """The distinction that made this hard to answer.

        A restarted core IS a new incarnation (ESC-13), so every restart rolls every
        lane whose conversation predates the process — SILENTLY, because RESTART is
        not an automatic reason and shows no notice. With ~20-47 restarts a day, the
        rollovers a user SEES are a small and unrepresentative sample of the ones
        that happen.
        """
        process_started = NOW - datetime.timedelta(minutes=5)
        decision = resolve(
            _entry(2, created_minutes_ago=60), NOW, POLICY,
            process_started_at=process_started,
        )

        assert decision.mints_new_incarnation
        assert decision.reason is ResetReason.RESTART
        assert not decision.reason.is_automatic, (
            "RESTART must stay non-automatic — it shows no 'went quiet' notice, and "
            "conflating it with IDLE is what made Bakir's question unanswerable"
        )
