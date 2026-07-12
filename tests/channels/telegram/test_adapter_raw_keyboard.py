"""Telegram adapter must dispatch a chunk's ``raw_keyboard`` as the message's
inline keyboard (bypassing the Action-shaped ``build_command_keyboard`` path)
and backfill the sent message's (chat_id, message_id) into the SAME
``ApproachRatingTracker`` singleton `consolidate.py` recorded the pending vote
on — see task-6 brief."""

from __future__ import annotations

import types
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import ResponseChunk

pytestmark = pytest.mark.asyncio

_KEYBOARD = {"inline_keyboard": [[{"text": "\U0001F44D", "callback_data": "apr:trace-9:positive"}]]}


def _settings() -> TelegramSettings:
    return TelegramSettings(bot_token="x" * 20, allowed_user_ids=frozenset({42}))


def _adapter(message_id: int = 777) -> tuple[TelegramChannelAdapter, MagicMock]:
    adapter = TelegramChannelAdapter(_settings())
    bot = MagicMock()
    bot.send_message = AsyncMock(
        return_value=types.SimpleNamespace(message_id=message_id)
    )
    adapter._bot_app = types.SimpleNamespace(bot=bot)
    return adapter, bot


async def _stream(*chunks: ResponseChunk) -> AsyncIterator[ResponseChunk]:
    for c in chunks:
        yield c


async def test_send_with_raw_keyboard_backfills_tracker() -> None:
    adapter, bot = _adapter(message_id=777)
    tracker = MagicMock()
    tracker.backfill_message = MagicMock()
    token = set_services(StepServices(approach_rating_tracker=tracker))
    try:
        await adapter.send(
            _stream(
                ResponseChunk(
                    content="x" * 250,
                    is_final=True,
                    chunk_index=0,
                    trace_id="trace-9",
                    owl_name="secretary",
                    target=321,
                    is_floor=False,
                    raw_keyboard=_KEYBOARD,
                )
            )
        )
    finally:
        reset_services(token)

    tracker.backfill_message.assert_called_once_with(
        trace_id="trace-9", chat_id=321, message_id=777
    )
    # Delivered via the raw-keyboard path (send_message with a reply_markup),
    # never through build_command_keyboard.
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["reply_markup"] is not None


async def test_send_without_raw_keyboard_does_not_touch_tracker() -> None:
    adapter, bot = _adapter(message_id=1)
    tracker = MagicMock()
    tracker.backfill_message = MagicMock()
    token = set_services(StepServices(approach_rating_tracker=tracker))
    try:
        await adapter.send(
            _stream(
                ResponseChunk(
                    content="a short reply",
                    is_final=True,
                    chunk_index=0,
                    trace_id="trace-10",
                    owl_name="o",
                    target=321,
                )
            )
        )
    finally:
        reset_services(token)

    tracker.backfill_message.assert_not_called()
    bot.send_message.assert_awaited_once()
