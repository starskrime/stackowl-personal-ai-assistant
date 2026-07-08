"""WebhookReceiver.run() must honor the SupervisedTask contract every other
task in this codebase follows (see JobScheduler.run()'s `while True` poll
loop): block until cancelled/stopped, never return after a clean bind.

Root cause this guards: Supervisor._run_with_backoff calls task.run() in a
loop FOREVER regardless of whether the previous call returned cleanly or
raised. Before this fix, WebhookReceiver.run() bound the HTTP listener then
returned immediately — the supervisor treated that as "the task finished" and
re-invoked run() ~1s later, creating a SECOND aiohttp site that collided with
the first (still-bound, never-stopped) socket on the same port. Five
consecutive failed rebinds then permanently parked the task "failed", even
though the original listener was silently still serving.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from stackowl.config.settings import Settings
from stackowl.config.webhook_settings import WebhookSettings
from stackowl.webhooks import receiver as receiver_mod
from stackowl.webhooks.receiver import WebhookReceiver


class _FakeRouter:
    def add_post(self, path: str, handler: object) -> None:  # noqa: ANN001
        pass


class _FakeApp:
    def __init__(self) -> None:
        self.router = _FakeRouter()


class _FakeRunner:
    def __init__(self, app: object) -> None:  # noqa: ANN001
        self.app = app
        self.cleaned_up = False

    async def setup(self) -> None:
        pass

    async def cleanup(self) -> None:
        self.cleaned_up = True


class _FakeSite:
    def __init__(self, runner: _FakeRunner, host: str, port: int) -> None:
        self.runner = runner
        self.host = host
        self.port = port
        self.stopped = False

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self.stopped = True


class _FakeWeb:
    Application = _FakeApp
    AppRunner = _FakeRunner
    TCPSite = _FakeSite


def _make_receiver(monkeypatch: pytest.MonkeyPatch) -> WebhookReceiver:
    monkeypatch.setattr(receiver_mod, "_import_aiohttp_web", lambda: _FakeWeb)
    settings = Settings(webhook=WebhookSettings(enabled=True, sources={}))
    return WebhookReceiver(scheduler=MagicMock(), settings=settings, db=None)


@pytest.mark.asyncio
async def test_run_blocks_after_bind_instead_of_returning(monkeypatch):
    """The bug: run() returned right after a successful bind. The fix: it
    must still be running (task not done) well after the bind completes."""
    receiver = _make_receiver(monkeypatch)

    task = asyncio.create_task(receiver.run())
    await asyncio.sleep(0.05)

    assert not task.done(), "run() returned after binding — supervisor will re-invoke it and double-bind"
    assert receiver._bound is True

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_run_cleans_up_listener_on_cancellation(monkeypatch):
    """On cancellation (the real shutdown path — Supervisor.stop() cancels the
    asyncio task directly, it does NOT call WebhookReceiver.stop()), the site
    and runner must still be torn down so the OS socket is released."""
    receiver = _make_receiver(monkeypatch)

    task = asyncio.create_task(receiver.run())
    await asyncio.sleep(0.05)
    site = receiver._site
    runner = receiver._runner
    assert site is not None and runner is not None

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert site.stopped is True
    assert runner.cleaned_up is True
    assert receiver._bound is False


@pytest.mark.asyncio
async def test_run_returns_cleanly_when_stop_is_called_directly(monkeypatch):
    """If something ever does call .stop() directly (currently no caller does,
    but the method exists for that purpose), run() must wake up and return —
    not hang forever waiting on a stop_event nobody will set."""
    receiver = _make_receiver(monkeypatch)

    task = asyncio.create_task(receiver.run())
    await asyncio.sleep(0.05)

    await receiver.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert receiver._bound is False
