"""Unit tests for E8-S3 SessionRegistry — spawn/dup/cap/clear/sweep/touch.

The registry is clock-injected (a fake monotonic clock), so the idle-TTL is driven
deterministically by advancing the clock rather than sleeping. The A2A mailbox
drain on clear/sweep is asserted against a REAL :class:`A2AQueue` (a message is
enqueued for a session's owl, then proved gone after the session is cleared/reaped).
"""

from __future__ import annotations

import pytest

from stackowl.messaging.a2a import A2AMessage, A2AQueue
from stackowl.owls.delegation_limits import MAX_LIVE_SESSIONS
from stackowl.owls.session_registry import SessionRegistry, SessionRegistryError


class _FakeClock:
    """Injectable clock with a hand-advanced monotonic time (ARCH-99)."""

    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def monotonic(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds

    async def async_sleep(self, seconds: float) -> None:  # pragma: no cover — unused
        return None


def _msg(to_owl: str) -> A2AMessage:
    return A2AMessage.now(
        from_owl="caller", to_owl=to_owl, content="orphan", message_type="event", trace_id="t",
    )


def test_spawn_then_reachable_by_label() -> None:
    reg = SessionRegistry(clock=_FakeClock())
    handle = reg.spawn("researcher", "scout")
    assert handle.label == "researcher"
    assert handle.owl_name == "scout"
    assert reg.get("researcher") is handle
    assert [h.label for h in reg.all()] == ["researcher"]


def test_duplicate_label_is_structured_error() -> None:
    reg = SessionRegistry(clock=_FakeClock())
    reg.spawn("dup", "scout")
    with pytest.raises(SessionRegistryError) as exc:
        reg.spawn("dup", "librarian")
    assert exc.value.reason == "duplicate_label"
    assert "dup" in exc.value.detail
    # The original survives — no silent overwrite.
    assert reg.get("dup").owl_name == "scout"


def test_capacity_cap_ninth_session_is_structured_error() -> None:
    reg = SessionRegistry(clock=_FakeClock())
    for i in range(MAX_LIVE_SESSIONS):
        reg.spawn(f"s{i}", "scout")
    assert len(reg.all()) == MAX_LIVE_SESSIONS
    with pytest.raises(SessionRegistryError) as exc:
        reg.spawn("one-too-many", "scout")
    assert exc.value.reason == "too_many_sessions"
    assert len(reg.all()) == MAX_LIVE_SESSIONS


def test_clear_session_removes_and_drains_mailbox() -> None:
    queue = A2AQueue()
    reg = SessionRegistry(a2a_queue=queue, clock=_FakeClock())
    reg.spawn("worker", "scout")
    # Orphan message waiting in scout's mailbox.
    queue.send(_msg("scout"))
    assert queue.queue_depth("scout") == 1

    assert reg.clear_session("worker") is True
    assert reg.get("worker") is None
    # Mailbox drained — no leak.
    assert queue.queue_depth("scout") == 0
    # Clearing a gone session is a no-op, not an error.
    assert reg.clear_session("worker") is False


def test_clear_session_does_not_drain_a_live_same_owl_session() -> None:
    """Regression: two sessions share one owl; clearing one must NOT eat the
    other live session's pending mailbox (mailbox is owl-keyed)."""
    queue = A2AQueue()
    reg = SessionRegistry(a2a_queue=queue, clock=_FakeClock())
    reg.spawn("label_a", "scout")
    reg.spawn("label_b", "scout")  # same owl, still live
    queue.send(_msg("scout"))  # a LIVE message for label_b
    assert queue.queue_depth("scout") == 1

    assert reg.clear_session("label_a") is True
    # label_b still live → scout's mailbox must be PRESERVED.
    assert queue.queue_depth("scout") == 1
    # Clearing the LAST same-owl session now drains it (no leak).
    assert reg.clear_session("label_b") is True
    assert queue.queue_depth("scout") == 0


def test_sweep_reaps_idle_past_ttl_and_drains() -> None:
    clock = _FakeClock()
    queue = A2AQueue()
    reg = SessionRegistry(a2a_queue=queue, clock=clock, idle_ttl_seconds=100.0)
    reg.spawn("idle", "scout")
    reg.spawn("fresh", "librarian")
    queue.send(_msg("scout"))

    # Advance past the TTL for both, then refresh only 'fresh' so it survives.
    clock.advance(150.0)
    reg.touch("fresh")  # fresh.last_active = now → not idle

    reaped = reg.sweep()
    assert reaped == 1
    assert reg.get("idle") is None
    assert reg.get("fresh") is not None
    # The reaped session's mailbox was drained.
    assert queue.queue_depth("scout") == 0


def test_sweep_nothing_idle_returns_zero() -> None:
    clock = _FakeClock()
    reg = SessionRegistry(clock=clock, idle_ttl_seconds=100.0)
    reg.spawn("a", "scout")
    clock.advance(10.0)  # well within TTL
    assert reg.sweep() == 0
    assert reg.get("a") is not None


def test_touch_bumps_last_active() -> None:
    clock = _FakeClock()
    reg = SessionRegistry(clock=clock)
    handle = reg.spawn("s", "scout")
    first = handle.last_active
    clock.advance(42.0)
    refreshed = reg.touch("s")
    assert refreshed is not None
    assert refreshed.last_active == first + 42.0
    # Touching a missing label is a no-op (None), not a raise.
    assert reg.touch("nope") is None


def test_handle_carries_no_history_field() -> None:
    # Continuity is the bridge's job — the handle is identity + activity ONLY.
    reg = SessionRegistry(clock=_FakeClock())
    handle = reg.spawn("s", "scout")
    assert not hasattr(handle, "history")
    assert not hasattr(reg, "set_history")


def test_clear_all_drains_every_mailbox() -> None:
    queue = A2AQueue()
    reg = SessionRegistry(a2a_queue=queue, clock=_FakeClock())
    reg.spawn("a", "scout")
    reg.spawn("b", "librarian")
    queue.send(_msg("scout"))
    queue.send(_msg("librarian"))

    cleared = reg.clear_all()
    assert cleared == 2
    assert reg.all() == []
    assert queue.queue_depth("scout") == 0
    assert queue.queue_depth("librarian") == 0


def test_drain_without_queue_is_safe() -> None:
    # No a2a_queue wired — clear/sweep must still succeed (degraded, not crash).
    reg = SessionRegistry(clock=_FakeClock())
    reg.spawn("s", "scout")
    assert reg.clear_session("s") is True
