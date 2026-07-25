"""D01.7 — one test per stated invariant, plus the priority order.

Invariants are those written in ``docs/hermes-mapping/designs/D01.7.md``. Each
test names the one it guards, so a failure says which contract broke rather than
which line moved.

Everything here is a pure function over an injected clock — no database, no
gateway, no waiting until 4 AM (Bakir's Q16 answer: clock injection for the
logic, a live run for the wiring).
"""

from __future__ import annotations

import datetime

import pytest

from stackowl.sessions import (
    Branch,
    ChatType,
    ResetMode,
    ResetPolicy,
    ResetReason,
    SessionSource,
    build_session_key,
    is_shared_lane,
    new_entry,
    new_session_id,
    reset_notice,
    resolve,
    should_suspend_for_restart_loop,
)

UTC = datetime.UTC


def at(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 7, day, hour, minute, tzinfo=UTC)


def dm(owl: str = "Brain", channel: str = "telegram", chat: str = "123") -> SessionSource:
    return SessionSource(owl_name=owl, channel=channel, chat_type=ChatType.DM, chat_id=chat)


# ---------------------------------------------------------------------------
# I1 — session_key never changes for the life of a lane.
# ---------------------------------------------------------------------------

def test_i1_key_is_deterministic() -> None:
    assert build_session_key(dm()) == build_session_key(dm())


def test_i1_key_carries_the_owl_so_a_different_owl_is_a_different_lane() -> None:
    """Bakir's Q1: a different owl is a different conversation. This is the
    divergence from Hermes, who have one agent and so never needed it."""
    assert build_session_key(dm(owl="Brain")) != build_session_key(dm(owl="Scout"))


def test_i1_key_separates_channels() -> None:
    """D01.1 Q4: Telegram / CLI / TUI stay separate threads."""
    assert build_session_key(dm(channel="telegram")) != build_session_key(dm(channel="cli"))


def test_i1_absent_components_are_dropped_not_emitted_empty() -> None:
    """A source that later gains a thread id must produce a genuinely different
    lane, not silently collide with the thread-less form."""
    without = build_session_key(dm())
    with_thread = build_session_key(
        SessionSource("Brain", "telegram", ChatType.DM, "123", thread_id="t9")
    )
    assert without != with_thread
    assert "::" not in without


# ---------------------------------------------------------------------------
# Isolation — Q5: threads shared, main channel per-user.
# ---------------------------------------------------------------------------

def test_group_is_isolated_per_user_by_default() -> None:
    a = SessionSource("Brain", "telegram", ChatType.GROUP, "-100", participant_id="alice")
    b = SessionSource("Brain", "telegram", ChatType.GROUP, "-100", participant_id="bob")
    assert build_session_key(a) != build_session_key(b)
    assert not is_shared_lane(a)


def test_thread_is_shared_by_default() -> None:
    a = SessionSource("Brain", "discord", ChatType.GROUP, "-100", "t1", participant_id="alice")
    b = SessionSource("Brain", "discord", ChatType.GROUP, "-100", "t1", participant_id="bob")
    assert build_session_key(a) == build_session_key(b)
    assert is_shared_lane(a)


def test_dm_is_never_shared_regardless_of_config() -> None:
    assert not is_shared_lane(dm(), group_per_user=False, thread_per_user=True)


# ---------------------------------------------------------------------------
# I2 — a reset always mints a NEW id; ids are never reused.
# ---------------------------------------------------------------------------

def test_i2_ids_are_unique_even_within_the_same_second() -> None:
    now = at(20, 12)
    assert len({new_session_id(now) for _ in range(200)}) == 200


def test_i2_id_is_time_sortable() -> None:
    assert new_session_id(at(20, 9)) < new_session_id(at(20, 10))


# ---------------------------------------------------------------------------
# I3 — exactly one branch executes, in a fixed priority order.
# ---------------------------------------------------------------------------

POLICY = ResetPolicy(mode=ResetMode.BOTH, at_hour=4, idle_minutes=1440)


def test_i3_no_entry_yields_new() -> None:
    assert resolve(None, at(20, 12), POLICY).branch is Branch.NEW


def test_i3_suspended_beats_resume_pending() -> None:
    """A hard wipe must never be overridden by a soft recovery."""
    e = new_entry(dm(), at(20, 12)).evolve(suspended=True, resume_pending=True)
    r = resolve(e, at(20, 12), POLICY)
    assert r.branch is Branch.SUSPENDED
    assert r.mints_new_incarnation


def test_i3_suspended_beats_policy_expiry() -> None:
    e = new_entry(dm(), at(18, 12)).evolve(suspended=True)
    assert resolve(e, at(20, 12), POLICY).branch is Branch.SUSPENDED


def test_i3_resume_pending_beats_policy_and_preserves_the_incarnation() -> None:
    """A crash in the small hours must not be converted into a rollover — that
    would silently discard the very turn we are recovering."""
    e = new_entry(dm(), at(18, 12)).evolve(resume_pending=True)
    r = resolve(e, at(20, 12), POLICY)
    assert r.branch is Branch.RESUME
    assert not r.mints_new_incarnation


def test_i3_quiet_entry_carries_on() -> None:
    e = new_entry(dm(), at(20, 12))
    assert resolve(e, at(20, 12, 5), POLICY).branch is Branch.EXISTING


# ---------------------------------------------------------------------------
# The 4 AM boundary — Bakir signed this off over his own midnight answer.
# ---------------------------------------------------------------------------

def test_daily_boundary_fires_after_4am_for_yesterdays_conversation() -> None:
    e = new_entry(dm(), at(20, 22))          # last active 10pm on the 20th
    r = resolve(e, at(21, 9), POLICY)        # first message 9am on the 21st
    assert r.branch is Branch.EXPIRED
    assert r.reason is ResetReason.DAILY


def test_a_1am_session_is_not_guillotined() -> None:
    """The whole reason 4 AM was chosen over midnight: a session running at 1 AM
    is mid-thought, and midnight would cut it."""
    e = new_entry(dm(), at(20, 23, 30))
    assert resolve(e, at(21, 1), POLICY).branch is Branch.EXISTING


def test_idle_fires_independently_of_the_clock_hour() -> None:
    e = new_entry(dm(), at(18, 12))
    r = resolve(e, at(20, 12), ResetPolicy(mode=ResetMode.IDLE, idle_minutes=1440))
    assert r.branch is Branch.EXPIRED
    assert r.reason is ResetReason.IDLE


def test_mode_none_never_expires() -> None:
    e = new_entry(dm(), at(1, 12))
    assert resolve(e, at(28, 12), ResetPolicy(mode=ResetMode.NONE)).branch is Branch.EXISTING


# ---------------------------------------------------------------------------
# I4 — a lane with active work is NEVER expired. Bakir's Q12 EXTENDS Hermes:
# theirs covers a background process, ours also covers durable tasks, active
# objectives and a pending clarify question.
# ---------------------------------------------------------------------------

def test_i4_active_work_blocks_an_otherwise_due_rollover() -> None:
    e = new_entry(dm(), at(20, 22))
    assert resolve(e, at(21, 9), POLICY, has_active_work=True).branch is Branch.EXISTING


def test_i4_active_work_does_not_rescue_a_suspended_lane() -> None:
    """A hard wipe means the lane is unusable; honouring active work there would
    keep a broken conversation alive forever."""
    e = new_entry(dm(), at(20, 22)).evolve(suspended=True)
    assert resolve(e, at(21, 9), POLICY, has_active_work=True).branch is Branch.SUSPENDED


# ---------------------------------------------------------------------------
# I5 — an automatic reset is always visible, exactly once, and never lies.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("reason", "fragment"),
    [(ResetReason.DAILY, "overnight"), (ResetReason.IDLE, "quiet"),
     (ResetReason.CONTEXT_FULL, "too long")],
)
def test_i5_automatic_resets_produce_a_notice(reason: ResetReason, fragment: str) -> None:
    e = new_entry(dm(), at(20, 12)).evolve(was_auto_reset=True, auto_reset_reason=reason)
    notice = reset_notice(e)
    assert notice is not None
    assert fragment in notice


def test_i5_explicit_new_is_never_reported_as_an_expiry() -> None:
    """The user typed /new on purpose. Telling them their session expired would be
    a lie, and it is why is_fresh_reset is kept distinct from was_auto_reset."""
    e = new_entry(dm(), at(20, 12)).evolve(is_fresh_reset=True)
    assert reset_notice(e) is None


def test_i5_a_quiet_entry_has_no_notice() -> None:
    assert reset_notice(new_entry(dm(), at(20, 12))) is None


# ---------------------------------------------------------------------------
# Stuck-loop escape — Q7: 3 strikes per lane, and it says why.
# ---------------------------------------------------------------------------

def test_stuck_loop_escape_trips_at_the_threshold() -> None:
    e = new_entry(dm(), at(20, 12))
    assert not should_suspend_for_restart_loop(e.evolve(restart_failures=2), POLICY)
    assert should_suspend_for_restart_loop(e.evolve(restart_failures=3), POLICY)


def test_stuck_loop_counter_is_per_lane() -> None:
    """One poisoned conversation must not take the others down with it."""
    poisoned = new_entry(dm(chat="1"), at(20, 12)).evolve(restart_failures=3)
    healthy = new_entry(dm(chat="2"), at(20, 12))
    assert should_suspend_for_restart_loop(poisoned, POLICY)
    assert not should_suspend_for_restart_loop(healthy, POLICY)


# ---------------------------------------------------------------------------
# Config validation — a bad policy fails loudly at construction.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hour", [-1, 24, 99])
def test_invalid_at_hour_is_rejected(hour: int) -> None:
    with pytest.raises(ValueError, match="at_hour"):
        ResetPolicy(at_hour=hour)


def test_invalid_idle_minutes_is_rejected() -> None:
    with pytest.raises(ValueError, match="idle_minutes"):
        ResetPolicy(idle_minutes=0)
