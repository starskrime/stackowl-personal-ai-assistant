"""PB0c — bounded gateway-local auto-restart of a dead telegram poll loop.

PB0b made a dead updater DETECTABLE (the liveness row goes stale). PB0c is the
SELF-HEAL: when the gateway's own heartbeat notices its updater stopped, it hands
the failure to the one ``RecoveryActuator`` for a bounded restart-in-place of the
long-poll loop — instead of staying dead until a human restarts (the real 30h
outage). These witnesses pin the bound: exactly ONE restart attempt per outage
episode, a loud error on surrender, and a re-arm on a fresh episode.
"""

from __future__ import annotations

import logging

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings

_LOGGER = "stackowl.telegram"


class _RecordingStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def mark_alive(self, channel: str) -> None:
        self.calls.append(channel)


class _FakeUpdater:
    """Mirrors the PTB Updater contract PB0c relies on: ``stop()`` raises if not
    running (PTB 22.7 behaviour); ``start_polling()`` only flips ``running`` True
    when ``recover_on_restart`` is set (i.e. the restart actually took)."""

    def __init__(self, running: bool, *, recover_on_restart: bool = False) -> None:
        self.running = running
        self.start_polling_calls = 0
        self.stop_calls = 0
        self._recover = recover_on_restart

    async def stop(self) -> None:
        if not self.running:
            raise RuntimeError("This Updater is not running!")
        self.stop_calls += 1
        self.running = False

    async def start_polling(self, **_kwargs: object) -> None:
        self.start_polling_calls += 1
        if self._recover:
            self.running = True


class _FakeApp:
    def __init__(self, updater: _FakeUpdater) -> None:
        self.updater = updater


def _adapter(store: _RecordingStore | None = None) -> TelegramChannelAdapter:
    return TelegramChannelAdapter(
        TelegramSettings(), liveness=store  # type: ignore[arg-type]
    )


# 1. Restart fires on a dead updater ---------------------------------------
async def test_restart_fires_on_dead_updater() -> None:
    adapter = _adapter(_RecordingStore())
    updater = _FakeUpdater(running=False)  # dead
    adapter._bot_app = _FakeApp(updater)

    assert adapter._recovery_attempted is False
    await adapter._beat_once()

    # The dead-updater tick invoked a restart (start_polling on the same app).
    assert updater.start_polling_calls == 1
    assert adapter._recovery_attempted is True


# 2. Recovered path resets the episode flag --------------------------------
async def test_recovered_path_logs_info_and_rearms(caplog) -> None:
    adapter = _adapter(_RecordingStore())
    updater = _FakeUpdater(running=False, recover_on_restart=True)
    adapter._bot_app = _FakeApp(updater)

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await adapter._beat_once()  # dead -> restart -> updater comes back up

    assert updater.start_polling_calls == 1
    assert updater.running is True
    assert any("self-healed" in r.message for r in caplog.records)

    # A subsequent running-True tick clears the episode flag (episode over).
    await adapter._beat_once()
    assert adapter._recovery_attempted is False


# 3. Bound: one attempt across many dead ticks + loud surrender ------------
async def test_bound_single_attempt_and_loud_surrender(caplog) -> None:
    adapter = _adapter(_RecordingStore())
    updater = _FakeUpdater(running=False)  # restart never brings it up
    adapter._bot_app = _FakeApp(updater)

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await adapter._beat_once()  # tick 1 — attempt fires, surrenders
        await adapter._beat_once()  # tick 2 — gated, NO re-attempt
        await adapter._beat_once()  # tick 3 — gated, NO re-attempt

    assert updater.start_polling_calls == 1  # THE bound
    assert adapter._recovery_attempted is True
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("surrender" in r.message.lower() for r in errors)


# 4. New episode re-arms the bound -----------------------------------------
async def test_new_episode_rearms() -> None:
    adapter = _adapter(_RecordingStore())
    updater = _FakeUpdater(running=False)
    adapter._bot_app = _FakeApp(updater)

    await adapter._beat_once()  # episode 1 — attempt + surrender
    assert updater.start_polling_calls == 1

    updater.running = True  # external recovery brings it back
    await adapter._beat_once()  # running tick clears the flag
    assert adapter._recovery_attempted is False

    updater.running = False  # NEW outage episode
    await adapter._beat_once()
    assert updater.start_polling_calls == 2  # a second attempt is allowed


# 5. Webhook / no-app guard -------------------------------------------------
async def test_restart_polling_no_app_is_noop(caplog) -> None:
    adapter = _adapter(_RecordingStore())
    adapter._bot_app = None

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await adapter._restart_polling()  # must not raise

    assert any(r.levelno == logging.WARNING for r in caplog.records)
