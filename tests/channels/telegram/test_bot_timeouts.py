"""PB1: prove the Telegram long-poll + API calls have bounded timeouts.

A network stall MUST raise ``TimedOut`` (caught + reconnected by PTB) within
seconds, never hang indefinitely. We assert the witness: every timeout setter
on ``ApplicationBuilder`` is called with the constant, and ``start_polling`` is
called with ``timeout=TELEGRAM_LONG_POLL_TIMEOUT`` so ``get_updates`` itself is
bounded (long-poll + read-timeout headroom).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stackowl.channels.telegram._bot import (
    TELEGRAM_CONNECT_TIMEOUT,
    TELEGRAM_GET_UPDATES_READ_TIMEOUT,
    TELEGRAM_LONG_POLL_TIMEOUT,
    TELEGRAM_POOL_TIMEOUT,
    TELEGRAM_READ_TIMEOUT,
    TELEGRAM_WRITE_TIMEOUT,
    start_bot,
)


def _fake_builder() -> tuple[MagicMock, MagicMock]:
    """Build a chain-returning MagicMock for ApplicationBuilder()."""
    fake_app = MagicMock()
    fake_app.initialize = AsyncMock()
    fake_app.start = AsyncMock()
    fake_app.bot.get_me = AsyncMock(return_value=MagicMock(id=42, username="bot"))
    fake_app.bot.set_webhook = AsyncMock()
    fake_app.updater = MagicMock()
    fake_app.updater.start_polling = AsyncMock()

    builder = MagicMock()
    for attr in (
        "token",
        "connect_timeout",
        "read_timeout",
        "write_timeout",
        "pool_timeout",
        "get_updates_connect_timeout",
        "get_updates_read_timeout",
        "get_updates_write_timeout",
        "get_updates_pool_timeout",
    ):
        getattr(builder, attr).return_value = builder
    builder.build.return_value = fake_app
    return builder, fake_app


def test_long_poll_invariant() -> None:
    """get_updates_read_timeout MUST exceed long-poll timeout (PTB requirement)."""
    assert TELEGRAM_GET_UPDATES_READ_TIMEOUT > TELEGRAM_LONG_POLL_TIMEOUT


@pytest.mark.asyncio
async def test_start_bot_configures_all_timeouts_in_polling_mode() -> None:
    builder, fake_app = _fake_builder()
    with patch("telegram.ext.ApplicationBuilder", return_value=builder):
        app, bot_id, bot_username = await start_bot("TOKEN", None, "")

    builder.token.assert_called_once_with("TOKEN")
    builder.connect_timeout.assert_called_once_with(TELEGRAM_CONNECT_TIMEOUT)
    builder.read_timeout.assert_called_once_with(TELEGRAM_READ_TIMEOUT)
    builder.write_timeout.assert_called_once_with(TELEGRAM_WRITE_TIMEOUT)
    builder.pool_timeout.assert_called_once_with(TELEGRAM_POOL_TIMEOUT)
    builder.get_updates_connect_timeout.assert_called_once_with(TELEGRAM_CONNECT_TIMEOUT)
    builder.get_updates_read_timeout.assert_called_once_with(
        TELEGRAM_GET_UPDATES_READ_TIMEOUT
    )
    builder.get_updates_write_timeout.assert_called_once_with(TELEGRAM_WRITE_TIMEOUT)
    builder.get_updates_pool_timeout.assert_called_once_with(TELEGRAM_POOL_TIMEOUT)

    fake_app.updater.start_polling.assert_awaited_once_with(
        drop_pending_updates=True,
        timeout=TELEGRAM_LONG_POLL_TIMEOUT,
    )
    assert app is fake_app
    assert bot_id == 42
    assert bot_username == "bot"


@pytest.mark.asyncio
async def test_start_bot_webhook_mode_still_carries_timeouts() -> None:
    builder, fake_app = _fake_builder()
    with patch("telegram.ext.ApplicationBuilder", return_value=builder):
        await start_bot("TOKEN", "https://example/hook", "secret")

    builder.connect_timeout.assert_called_once_with(TELEGRAM_CONNECT_TIMEOUT)
    builder.read_timeout.assert_called_once_with(TELEGRAM_READ_TIMEOUT)
    builder.write_timeout.assert_called_once_with(TELEGRAM_WRITE_TIMEOUT)
    builder.pool_timeout.assert_called_once_with(TELEGRAM_POOL_TIMEOUT)
    fake_app.bot.set_webhook.assert_awaited_once()
    fake_app.updater.start_polling.assert_not_called()


@pytest.mark.asyncio
async def test_start_polling_blackhole_fault_injection() -> None:
    """Black-hole: a stalled get_updates raises TimedOut, doesn't hang forever."""
    import asyncio

    from telegram.error import TimedOut

    builder, fake_app = _fake_builder()

    async def _stall(*_: Any, **__: Any) -> None:
        await asyncio.sleep(0)
        raise TimedOut("simulated stalled get_updates")

    fake_app.updater.start_polling = AsyncMock(side_effect=_stall)

    with patch("telegram.ext.ApplicationBuilder", return_value=builder), pytest.raises(TimedOut):
        await start_bot("TOKEN", None, "")
