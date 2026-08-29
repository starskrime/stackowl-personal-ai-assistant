"""One failed heartbeat tick must not end the heartbeat.

MEASURED. 2026-08-28T01:13:55::

    [telegram] adapter.liveness_heartbeat: crashed — receive-liveness signal lost
    {"err": "OperationalError('database is locked')"}

That was the LAST heartbeat. channel_liveness.telegram froze at 01:12:54 and the
health sweep reported ``degraded=['telegram_receive']`` twelve times over the next
24 hours — while Telegram kept working and 8 messages arrived. A false alarm that
could never clear, from a transient SQLite lock (19 such events that day, two
processes sharing one file).

THE LOOP HAD NO GUARD::

    while True:
        await self._beat_once()          # one raise ends the loop for ever
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)

There WAS a done-callback, and its docstring says "Log loudly if the heartbeat
crashed — never a silent fire-and-forget". It logs the death and does nothing
about it. Knowing about a failure mode is not an actuator for it.

A TICK FAILING IS NORMAL — a lock contended, a write briefly blocked. What is not
normal is losing the signal for ever because of one. The loop keeps beating; the
NEXT tick stamps and the alarm clears on its own.
"""

from __future__ import annotations

import asyncio

import pytest


class _Adapter:
    """The heartbeat loop only, bound off the real class.

    Standing up a real TelegramChannelAdapter needs a bot token, an updater and a
    running application, and would test those instead of the property under test.
    The loop and its guard ARE the actuator.
    """

    def __init__(self, fail_ticks: int) -> None:
        from stackowl.channels.telegram.adapter import TelegramChannelAdapter

        self.ticks = 0
        self.stamped = 0
        self._fail = fail_ticks
        self._liveness_heartbeat = TelegramChannelAdapter._liveness_heartbeat.__get__(self)

    async def _beat_once(self) -> None:
        self.ticks += 1
        if self.ticks <= self._fail:
            raise RuntimeError("database is locked")
        self.stamped += 1


@pytest.fixture(autouse=True)
def _fast_heartbeat(monkeypatch: object) -> None:
    import stackowl.channels.telegram.adapter as mod

    monkeypatch.setattr(mod, "HEARTBEAT_INTERVAL_S", 0.01)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_locked_database_does_not_end_the_heartbeat() -> None:
    """THE regression. One transient lock cost 24 hours of false alarm."""
    a = _Adapter(fail_ticks=1)

    task = asyncio.create_task(a._liveness_heartbeat())
    for _ in range(200):
        if a.stamped:
            break
        await asyncio.sleep(0.01)
    task.cancel()

    assert a.stamped >= 1, (
        "the heartbeat died on its first failed tick — the receive-liveness "
        "signal is lost for ever and health false-alarms until a restart"
    )


@pytest.mark.asyncio
async def test_it_keeps_beating_through_a_long_outage() -> None:
    """A lock storm is a run of failures, not one. The signal must resume when the
    contention clears, without anyone intervening."""
    a = _Adapter(fail_ticks=10)

    task = asyncio.create_task(a._liveness_heartbeat())
    for _ in range(400):
        if a.stamped:
            break
        await asyncio.sleep(0.01)
    task.cancel()

    assert a.ticks > 10
    assert a.stamped >= 1, "the heartbeat never recovered after a run of failures"


@pytest.mark.asyncio
async def test_cancellation_still_stops_it() -> None:
    """The control. stop() cancels this task, and a guard that swallowed
    CancelledError would make shutdown hang for ever."""
    a = _Adapter(fail_ticks=0)

    task = asyncio.create_task(a._liveness_heartbeat())
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
