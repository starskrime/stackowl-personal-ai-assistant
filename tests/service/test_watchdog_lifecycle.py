"""F142 — WatchdogService really pings systemd and gracefully no-ops without it.

The orchestrator used to import a dead ``startup.watchdog.WatchdogSec`` stub and
call it as two one-shots — a recurring systemd watchdog timer was satisfied
exactly never, so systemd would kill/restart a healthy process. The real
``service.watchdog.WatchdogService`` has a recurring ping loop; this asserts the
loop pings ``WATCHDOG=1`` repeatedly under a fake systemd env, sends ``READY=1``
once, and is a clean no-op off-systemd.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.service.watchdog import WatchdogService


@pytest.mark.asyncio
async def test_recurring_pings_under_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``WATCHDOG_USEC`` set, start() schedules a recurring task that emits
    ``WATCHDOG=1`` at least twice (proves it is NOT a one-shot)."""
    # A tiny watchdog interval so the loop pings quickly: usec/2 = 0.01s.
    monkeypatch.setenv("WATCHDOG_USEC", "20000")
    sent: list[str] = []
    monkeypatch.setattr(WatchdogService, "_sd_notify", staticmethod(lambda state: sent.append(state)))

    wd = WatchdogService()
    wd.start()
    try:
        # Wait until at least two pings have landed (recurring, not one-shot).
        for _ in range(200):
            if sent.count("WATCHDOG=1") >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        wd.stop()

    assert sent.count("WATCHDOG=1") >= 2, sent


@pytest.mark.asyncio
async def test_send_ready_emits_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """``send_ready`` emits ``READY=1`` exactly once under systemd."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    sent: list[str] = []
    monkeypatch.setattr(WatchdogService, "_sd_notify", staticmethod(lambda state: sent.append(state)))

    wd = WatchdogService()
    wd.send_ready()

    assert sent == ["READY=1"]


@pytest.mark.asyncio
async def test_off_systemd_everything_noops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off-systemd (no env), start() schedules nothing and send_ready emits nothing
    — clean no-op on macOS/Windows/Jetson."""
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    sent: list[str] = []
    monkeypatch.setattr(WatchdogService, "_sd_notify", staticmethod(lambda state: sent.append(state)))

    wd = WatchdogService()
    wd.start()
    wd.send_ready()
    await asyncio.sleep(0.05)
    wd.stop()

    assert sent == []
    assert wd._task is None  # no task scheduled off-systemd
