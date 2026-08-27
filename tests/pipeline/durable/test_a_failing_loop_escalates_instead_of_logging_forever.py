"""A loop that fails every tick must say so ONCE, loudly — not ten thousand times.

MEASURED 2026-08-26. A corrupt WAL handle made every tick fail with
``DatabaseError: file is not a database``. The loop faithfully logged
"[loop] tick failed — the loop continues" TEN THOUSAND TWO HUNDRED AND THIRTY-EIGHT
times across roughly five and a half hours. Nothing durable was written in that
window and nobody was told. It was found by a human who happened to be looking at
something else.

The handler's own comment had already named the gap — "a tick that dies means work
piles up in a table nobody drains, WITH NOTHING REPORTING IT" — and then logged at
ERROR and continued. Knowing about a failure mode is not an actuator for it.

REPETITION IS NOT REPORTING. An identical line ten thousand times is
indistinguishable from noise, and the reader who most needs it is the one who is
not watching. Bakir, 2026-08-27, approving this: on a customer device nobody is
watching at all.

So the streak escalates ONCE at CRITICAL and re-arms only after a healthy tick —
a flapping subsystem must not re-flood the channel it just alerted through.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest


class _Loop:
    """The escalation surface only, exercised directly.

    Standing up the whole DurableTaskLoop would need a store, a backend and a
    running event loop, and would test those instead of the property under test.
    The two counters and the threshold ARE the actuator.
    """

    def __init__(self) -> None:
        from stackowl.pipeline.durable.loop import TaskLoop

        self._worker = "loop-test"
        self._consecutive_tick_failures = 0
        self._tick_escalated = False
        self._TICK_FAILURES_BEFORE_ESCALATION = (
            TaskLoop._TICK_FAILURES_BEFORE_ESCALATION
        )
        self._note_tick_ok = TaskLoop._note_tick_ok.__get__(self)
        self._note_tick_failed = TaskLoop._note_tick_failed.__get__(self)


def _criticals(caplog: Any) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.CRITICAL]


def test_a_few_failures_do_not_page_anyone(caplog: Any) -> None:
    """A transient blip is not an incident. Escalating on the first failure would
    make the alarm worthless within a day."""
    caplog.set_level(logging.DEBUG)
    loop = _Loop()

    for _ in range(loop._TICK_FAILURES_BEFORE_ESCALATION - 1):
        loop._note_tick_failed(RuntimeError("transient"))

    assert _criticals(caplog) == []


def test_a_sustained_failure_escalates(caplog: Any) -> None:
    """THE actuator. Ten thousand identical ERROR lines told nobody; one CRITICAL
    is what an operator alerts on."""
    caplog.set_level(logging.DEBUG)
    loop = _Loop()

    for _ in range(loop._TICK_FAILURES_BEFORE_ESCALATION):
        loop._note_tick_failed(RuntimeError("file is not a database"))

    crits = _criticals(caplog)
    assert len(crits) == 1, f"expected exactly one escalation, got {len(crits)}"
    msg = crits[0].getMessage()
    assert "not draining work" in msg
    assert "durable tasks are NOT running" in msg, (
        "the alert must state the IMPACT, not just that something failed — the "
        "person reading it at 3am needs to know what stopped"
    )


def test_it_escalates_exactly_ONCE_however_long_it_keeps_failing(caplog: Any) -> None:
    """The measured outage produced 10,238 failures. If each escalated, the alert
    channel becomes the noise it was meant to cut through."""
    caplog.set_level(logging.DEBUG)
    loop = _Loop()

    for _ in range(loop._TICK_FAILURES_BEFORE_ESCALATION * 25):
        loop._note_tick_failed(RuntimeError("still broken"))

    assert len(_criticals(caplog)) == 1


def test_recovery_is_announced_and_re_arms_the_alarm(caplog: Any) -> None:
    """Silence after an alert is ambiguous — fixed, or still broken and given up?
    And the alarm must re-arm, or a second outage would pass unreported."""
    caplog.set_level(logging.DEBUG)
    loop = _Loop()

    for _ in range(loop._TICK_FAILURES_BEFORE_ESCALATION):
        loop._note_tick_failed(RuntimeError("boom"))
    loop._note_tick_ok()

    assert any("RECOVERED" in r.getMessage() for r in caplog.records)
    assert loop._consecutive_tick_failures == 0
    assert loop._tick_escalated is False

    caplog.clear()
    for _ in range(loop._TICK_FAILURES_BEFORE_ESCALATION):
        loop._note_tick_failed(RuntimeError("again"))
    assert len(_criticals(caplog)) == 1, "the alarm did not re-arm after recovery"


def test_a_healthy_tick_after_a_short_blip_says_nothing(caplog: Any) -> None:
    """Recovery from a streak that never escalated is not news."""
    caplog.set_level(logging.DEBUG)
    loop = _Loop()

    loop._note_tick_failed(RuntimeError("blip"))
    loop._note_tick_ok()

    assert not any("RECOVERED" in r.getMessage() for r in caplog.records)
    assert loop._consecutive_tick_failures == 0


@pytest.mark.parametrize("failures", [1, 5, 19])
def test_the_threshold_is_a_streak_not_a_total(caplog: Any, failures: int) -> None:
    """Intermittent failures separated by successes must not accumulate into a
    false alarm — that is the difference between 'broken' and 'flaky'."""
    caplog.set_level(logging.DEBUG)
    loop = _Loop()

    for _ in range(10):
        for _ in range(failures):
            loop._note_tick_failed(RuntimeError("intermittent"))
        loop._note_tick_ok()

    assert _criticals(caplog) == []
