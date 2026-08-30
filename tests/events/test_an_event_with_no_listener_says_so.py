"""An event delivered to nobody must say so once, at runtime.

CLAUDE.md's defect #1 is "a write with no reader ... Measure the EFFECT, never
trust the call." `EventBus.emit` is that shape exactly: it iterates
``self._handlers.get(event, [])``, so an event with no subscribers runs the loop
body zero times and logs NOTHING. Emitting into silence is indistinguishable from
delivering.

WHY THE RUNTIME AND NOT A GREP — demonstrated on myself, three times, in the
half-hour before writing this.

  1. A literal-only scan of `subscribe("name", ...)` found ONE subscribed event
     against six emitted, and I nearly recorded "five dead events, including the
     entire budget-alert path".
  2. It is wrong. There are ten `.subscribe(` calls and most pass a VARIABLE:
     `EventDeliveryBridge` loops over `_ALLOWED_EVENTS`, and the TUI coordinator
     loops over a handler map.
  3. The truth needed three files and a live log line to establish:
     `_ALLOWED_EVENTS = {budget_exceeded, budget_80pct_alert,
     conversation_cost_report}`, confirmed by
     "[notifications] event_bridge.register: subscribed proactive events {count: 3}".

So subscription in this tree is DYNAMIC, and static analysis of it is unreliable
by construction. The only trustworthy answer to "does anything listen?" is the one
the bus itself has at emit time. That is what this adds.

ONCE PER EVENT NAME PER PROCESS, deliberately. You need to know THAT an event goes
nowhere, not every time it happens — and an unsubscribed event inside a hot loop
would otherwise become the log-spam version of the very problem it reports. This
is the D16.1 lesson applied to the bus: log the POPULATION, not the occurrence.

KNOWN DEAD ON PURPOSE. D01.4 records `owl_edited` as deliberately-kept debt: "not
D01.4's to fix, and deleting a hook someone [may need]". Re-measured 2026-08-30 —
still 1 emitter, 0 subscribers. This does not delete it; it makes it announce
itself instead of being rediscovered by grep every few weeks.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.events.bus import EventBus


def test_an_unsubscribed_event_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    """The defect: emitting to nobody is currently silent."""
    bus = EventBus()
    with caplog.at_level(logging.INFO):
        bus.emit("nobody_listens", {"x": 1})

    hits = [r for r in caplog.records if "nobody_listens" in str(r.__dict__)]
    assert hits, (
        "an event was emitted to zero subscribers and nothing said so — a write "
        "with no reader, which is this codebase's most common defect shape"
    )


def test_a_DELIVERED_event_stays_quiet(caplog: pytest.LogCaptureFixture) -> None:
    """The guard must be narrow — normal delivery must not become noisy."""
    bus = EventBus()
    bus.subscribe("heard", lambda payload: None)
    with caplog.at_level(logging.INFO):
        bus.emit("heard", {"x": 1})

    assert not [r for r in caplog.records if "no subscriber" in r.getMessage().lower()]


def test_it_reports_ONCE_per_event_name(caplog: pytest.LogCaptureFixture) -> None:
    """An unsubscribed event in a hot loop must not become log spam.

    That would be the logging version of the problem being reported.
    """
    bus = EventBus()
    with caplog.at_level(logging.INFO):
        for _ in range(50):
            bus.emit("noisy_orphan", {"x": 1})

    hits = [r for r in caplog.records if "noisy_orphan" in str(r.__dict__)]
    assert len(hits) == 1, f"reported {len(hits)} times for one event name"


def test_two_different_orphans_are_BOTH_reported(caplog: pytest.LogCaptureFixture) -> None:
    """Once per NAME, not once per process — or the second one hides."""
    bus = EventBus()
    with caplog.at_level(logging.INFO):
        bus.emit("orphan_a", None)
        bus.emit("orphan_b", None)

    names = str([r.__dict__ for r in caplog.records])
    assert "orphan_a" in names and "orphan_b" in names


def test_a_LATE_subscriber_silences_it(caplog: pytest.LogCaptureFixture) -> None:
    """Subscription order is not guaranteed at boot.

    An event emitted before its subscriber registers is not dead — it is early.
    Once someone listens, it must stop being reported.
    """
    bus = EventBus()
    bus.emit("late_listener", None)
    bus.subscribe("late_listener", lambda payload: None)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        bus.emit("late_listener", None)

    assert not [r for r in caplog.records if "late_listener" in str(r.__dict__)]


def test_reporting_NEVER_breaks_an_emit() -> None:
    """B5. The bus must deliver even if its own bookkeeping is unhappy."""
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe("works", seen.append)
    bus.emit("works", {"ok": True})
    bus.emit("orphan_too", None)
    assert seen == [{"ok": True}]
