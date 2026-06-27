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
async def test_ping_skipped_when_liveness_check_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-85 — when a liveness_check is supplied and reports a critical subsystem
    DOWN, the loop must NOT ping WATCHDOG=1, so systemd's watchdog-timeout can
    restart a deadlocked-but-spinning process."""
    monkeypatch.setenv("WATCHDOG_USEC", "20000")
    sent: list[str] = []
    monkeypatch.setattr(WatchdogService, "_sd_notify", staticmethod(lambda state: sent.append(state)))

    wd = WatchdogService()
    wd.start(liveness_check=lambda: False)  # critical subsystem down → never ping
    try:
        await asyncio.sleep(0.1)  # many intervals (0.01s each) elapse
    finally:
        wd.stop()

    assert sent.count("WATCHDOG=1") == 0, sent


@pytest.mark.asyncio
async def test_ping_resumes_when_liveness_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-85 — a liveness_check that flips False→True gates the ping per-tick:
    no ping while down, pings again once healthy."""
    monkeypatch.setenv("WATCHDOG_USEC", "20000")
    sent: list[str] = []
    monkeypatch.setattr(WatchdogService, "_sd_notify", staticmethod(lambda state: sent.append(state)))
    healthy = {"value": False}

    wd = WatchdogService()
    wd.start(liveness_check=lambda: healthy["value"])
    try:
        await asyncio.sleep(0.05)
        assert sent.count("WATCHDOG=1") == 0
        healthy["value"] = True
        for _ in range(200):
            if sent.count("WATCHDOG=1") >= 1:
                break
            await asyncio.sleep(0.01)
    finally:
        wd.stop()

    assert sent.count("WATCHDOG=1") >= 1, sent


@pytest.mark.asyncio
async def test_async_liveness_check_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-85 — an async liveness_check (e.g. HealthAggregator-backed) is awaited."""
    monkeypatch.setenv("WATCHDOG_USEC", "20000")
    sent: list[str] = []
    monkeypatch.setattr(WatchdogService, "_sd_notify", staticmethod(lambda state: sent.append(state)))

    async def _alive() -> bool:
        return True

    wd = WatchdogService()
    wd.start(liveness_check=_alive)
    try:
        for _ in range(200):
            if sent.count("WATCHDOG=1") >= 1:
                break
            await asyncio.sleep(0.01)
    finally:
        wd.stop()

    assert sent.count("WATCHDOG=1") >= 1, sent


@pytest.mark.asyncio
async def test_ping_continues_when_liveness_check_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-85 — a liveness_check that itself errors must NOT silence the watchdog
    (fail-OPEN on probe error: a broken probe should not cause a false restart)."""
    monkeypatch.setenv("WATCHDOG_USEC", "20000")
    sent: list[str] = []
    monkeypatch.setattr(WatchdogService, "_sd_notify", staticmethod(lambda state: sent.append(state)))

    def _boom() -> bool:
        raise RuntimeError("probe broken")

    wd = WatchdogService()
    wd.start(liveness_check=_boom)
    try:
        for _ in range(200):
            if sent.count("WATCHDOG=1") >= 1:
                break
            await asyncio.sleep(0.01)
    finally:
        wd.stop()

    assert sent.count("WATCHDOG=1") >= 1, sent


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
