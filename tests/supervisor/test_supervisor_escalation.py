"""Supervisor escalation + stuck-task watchdog tests (S3 findings F-73, F-74).

F-74 — the give-up floor (``_MAX_CONSECUTIVE_FAILURES``) must ESCALATE (emit a
recoverable signal / operator notification) rather than silently parking the
task dead.

F-73 — a task that runs live-but-stuck (forever, making no progress) must be
detected by a max-runtime watchdog, restarted, and escalated, instead of being
treated as healthy.
"""

from __future__ import annotations

import asyncio

from stackowl.supervisor.supervisor import (
    EscalationEvent,
    SupervisedTask,
    Supervisor,
    make_supervised_task,
)


class FakeClock:
    """Monotonic clock that advances on sleep; async_sleep yields once then returns."""

    def __init__(self) -> None:
        self._t = 0.0

    def monotonic(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds

    async def async_sleep(self, seconds: float) -> None:
        self._t += seconds
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# F-74 — escalate on the give-up floor
# ---------------------------------------------------------------------------


async def test_escalation_hook_fires_on_give_up_floor() -> None:
    events: list[EscalationEvent] = []

    async def _always_fail() -> None:
        raise RuntimeError("boom")

    sup = Supervisor(clock=FakeClock(), on_escalation=events.append)
    sup.register(make_supervised_task("failing", _always_fail))
    await sup.start()

    async def _wait_failed() -> None:
        while sup.health().get("failing") != "failed":
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait_failed(), timeout=5.0)

    reasons = [e.reason for e in events]
    assert "max_failures" in reasons
    floor = next(e for e in events if e.reason == "max_failures")
    assert floor.task_id == "failing"
    assert floor.consecutive_failures >= 5


async def test_escalation_hook_can_be_async() -> None:
    events: list[EscalationEvent] = []

    async def _hook(event: EscalationEvent) -> None:
        events.append(event)

    async def _always_fail() -> None:
        raise RuntimeError("boom")

    sup = Supervisor(clock=FakeClock(), on_escalation=_hook)
    sup.register(make_supervised_task("failing", _always_fail))
    await sup.start()

    async def _wait_failed() -> None:
        while sup.health().get("failing") != "failed":
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait_failed(), timeout=5.0)
    assert any(e.reason == "max_failures" for e in events)


async def test_escalation_hook_failure_is_isolated() -> None:
    """A throwing escalation hook must not break the supervisor's own bookkeeping."""

    def _bad_hook(event: EscalationEvent) -> None:
        raise ValueError("hook exploded")

    async def _always_fail() -> None:
        raise RuntimeError("boom")

    sup = Supervisor(clock=FakeClock(), on_escalation=_bad_hook)
    sup.register(make_supervised_task("failing", _always_fail))
    await sup.start()

    async def _wait_failed() -> None:
        while sup.health().get("failing") != "failed":
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait_failed(), timeout=5.0)
    assert sup.health()["failing"] == "failed"


async def test_no_hook_still_marks_failed() -> None:
    """Backwards-compatible: without a hook the floor still parks the task failed."""

    async def _always_fail() -> None:
        raise RuntimeError("boom")

    sup = Supervisor(clock=FakeClock())
    sup.register(make_supervised_task("failing", _always_fail))
    await sup.start()

    async def _wait_failed() -> None:
        while sup.health().get("failing") != "failed":
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait_failed(), timeout=5.0)
    assert sup.health()["failing"] == "failed"


# ---------------------------------------------------------------------------
# F-73 — max-runtime watchdog for a live-but-stuck task
# ---------------------------------------------------------------------------


async def test_stuck_task_is_detected_and_escalated() -> None:
    events: list[EscalationEvent] = []
    runs = 0

    async def _stuck() -> None:
        nonlocal runs
        runs += 1
        await asyncio.sleep(1000)  # never makes progress

    sup = Supervisor(
        clock=FakeClock(),
        on_escalation=events.append,
        max_run_seconds=0.05,
    )
    sup.register(make_supervised_task("stuck", _stuck))
    await sup.start()

    async def _wait_failed() -> None:
        while sup.health().get("stuck") != "failed":
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait_failed(), timeout=5.0)

    # The watchdog tripped, restarted, and ultimately escalated.
    assert any(e.reason == "stuck_timeout" for e in events)
    assert any(e.reason == "max_failures" for e in events)
    assert runs >= 2  # was actually restarted, not parked on first trip


# ---------------------------------------------------------------------------
# F-75 — tight-loop guard for no-op rapid clean returns
# ---------------------------------------------------------------------------


async def test_tight_loop_noop_clean_returns_are_escalated() -> None:
    """A task that returns cleanly+instantly forever is a spin, not health (F-75)."""
    events: list[EscalationEvent] = []
    runs = 0

    async def _noop() -> None:
        nonlocal runs
        runs += 1
        # returns immediately, doing no real work

    sup = Supervisor(
        clock=FakeClock(),
        on_escalation=events.append,
        max_tight_loop_returns=3,
    )
    sup.register(make_supervised_task("spin", _noop))
    await sup.start()

    async def _wait_failed() -> None:
        while sup.health().get("spin") != "failed":
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait_failed(), timeout=5.0)

    assert any(e.reason == "tight_loop" for e in events)
    spin = next(e for e in events if e.reason == "tight_loop")
    assert spin.task_id == "spin"
    assert runs >= 3  # actually invoked repeatedly before the guard tripped


async def test_slow_clean_return_is_not_flagged_as_tight_loop() -> None:
    """A clean return that did real (slow) work resets the guard — unchanged behaviour."""
    events: list[EscalationEvent] = []
    clock = FakeClock()
    runs = 0

    class _SlowClean(SupervisedTask):
        @property
        def task_id(self) -> str:
            return "slow"

        async def run(self) -> None:
            nonlocal runs
            runs += 1
            clock.advance(1.0)  # simulate real wall-clock work elapsing

    sup = Supervisor(
        clock=clock,
        on_escalation=events.append,
        max_tight_loop_returns=3,
        tight_loop_seconds=0.001,
    )
    sup.register(_SlowClean())
    await sup.start()

    while runs < 6:
        await asyncio.sleep(0.01)

    assert sup.health()["slow"] == "running"
    assert not any(e.reason == "tight_loop" for e in events)
    await sup.stop()


async def test_no_hook_tight_loop_still_marks_failed() -> None:
    """Backwards-compatible: without a hook the tight-loop guard still parks failed."""
    runs = 0

    async def _noop() -> None:
        nonlocal runs
        runs += 1

    sup = Supervisor(clock=FakeClock(), max_tight_loop_returns=3)
    sup.register(make_supervised_task("spin", _noop))
    await sup.start()

    async def _wait_failed() -> None:
        while sup.health().get("spin") != "failed":
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait_failed(), timeout=5.0)
    assert sup.health()["spin"] == "failed"


async def test_watchdog_disabled_by_default_lets_long_task_run() -> None:
    """With no max_run_seconds a legitimately long-lived loop stays running."""
    started = asyncio.Event()

    async def _long() -> None:
        started.set()
        await asyncio.sleep(1000)

    sup = Supervisor(clock=FakeClock())
    sup.register(make_supervised_task("long", _long))
    await sup.start()
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert sup.health()["long"] == "running"
    await sup.stop()
    assert sup.health()["long"] == "stopped"
