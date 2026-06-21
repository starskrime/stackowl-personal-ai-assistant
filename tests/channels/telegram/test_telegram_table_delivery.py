"""Every Telegram delivery path flattens GFM tables (not just the on-turn reply).

The on-turn path flattened tables via format_response, but the proactive deliverer
and the `custom` notification event sent RAW markdown — so a table in a proactive
message reached the user as broken pipes. These prove flattening on EVERY path:
the shared send_text chokepoint (deliverer / clarify / queue notices) and the
custom notification event, plus the on-turn regression.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stackowl.channels._format import flatten_gfm_tables
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.notifications import (
    NotificationPayload,
    TelegramNotificationDispatcher,
)
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard

_USER = 4242
_TABLE = (
    "Here is the data:\n\n"
    "| ColAlpha | ColBeta |\n"
    "| --- | --- |\n"
    "| 1 | 2 |\n"
)


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text})


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


class _NoQuietHours:
    def should_suppress(self, urgency: str) -> bool:
        return False


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _adapter():  # noqa: ANN202
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({_USER})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    adapter._last_chat_id = _USER  # proactive/notification fallback target
    return adapter, bot


def _flattened(bot: _FakeBot) -> bool:
    """A flattened table arrives as a fenced (```) block; a bypassed one does not."""
    return any("```" in m["text"] for m in bot.messages)


async def test_send_text_flattens_table() -> None:
    """The shared send_text chokepoint (deliverer / clarify / queue) flattens raw GFM."""
    adapter, bot = _adapter()
    await adapter.send_text(_TABLE, chat_id=_USER)
    assert _flattened(bot), f"send_text did not flatten the table; sent={bot.messages!r}"


async def test_custom_notification_flattens_table() -> None:
    """The `custom` notification event flattens raw GFM (proactive bypass closed)."""
    adapter, bot = _adapter()
    dispatcher = TelegramNotificationDispatcher(
        adapter, _NoQuietHours(), formatters={},  # type: ignore[arg-type]
    )
    await dispatcher.dispatch(
        NotificationPayload(event_type="custom", content={"text": _TABLE})
    )
    assert _flattened(bot), f"custom notification did not flatten; sent={bot.messages!r}"


async def test_on_turn_send_still_flattens_table() -> None:
    """Regression: the on-turn reply path still flattens."""
    adapter, bot = _adapter()

    async def _chunks():  # noqa: ANN202
        yield SimpleNamespace(content=_TABLE, target=_USER)

    await adapter.send(_chunks())
    assert _flattened(bot), f"on-turn send did not flatten; sent={bot.messages!r}"


def test_flatten_is_idempotent() -> None:
    once = flatten_gfm_tables(_TABLE)
    assert flatten_gfm_tables(once) == once
