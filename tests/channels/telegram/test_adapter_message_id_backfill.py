"""Telegram adapter must capture the sent ``telegram.Message`` (not discard it)
so a floored turn's retry_queue row can be backfilled with the real
``channel_chat_id``/``channel_message_id`` — otherwise nothing can ever edit
that message later (see task-4 brief)."""

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


def _settings() -> TelegramSettings:
    return TelegramSettings(bot_token="x" * 20, allowed_user_ids=frozenset({42}))


def _adapter(message_id: int = 4242) -> tuple[TelegramChannelAdapter, MagicMock]:
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


async def test_send_part_returns_message() -> None:
    adapter, _bot = _adapter(message_id=4242)

    result = await adapter._send_part(target=111, part="hello", idx=0)

    assert result is not None
    assert result.message_id == 4242


async def test_deliver_returns_last_part_message() -> None:
    adapter, _bot = _adapter(message_id=99)

    result = await adapter._deliver("hello", chat_id=555)

    assert result is not None
    assert result.message_id == 99


async def test_send_text_returns_message() -> None:
    adapter, _bot = _adapter(message_id=17)

    result = await adapter.send_text("hi", chat_id=555)

    assert result is not None
    assert result.message_id == 17


async def test_send_text_or_actions_returns_message_no_actions() -> None:
    adapter, _bot = _adapter(message_id=21)

    result = await adapter.send_text_or_actions("hi", (), chat_id=555)

    assert result is not None
    assert result.message_id == 21


async def test_send_backfills_retry_queue_for_floor_turn() -> None:
    adapter, bot = _adapter(message_id=8675)
    retry_store = MagicMock()
    retry_store.backfill_channel_message = AsyncMock()
    token = set_services(StepServices(retry_queue_store=retry_store))
    try:
        await adapter.send(
            _stream(
                ResponseChunk(
                    content="the honest floor answer",
                    is_final=True,
                    chunk_index=0,
                    trace_id="trace-floor-1",
                    owl_name="o",
                    target=555,
                    is_floor=True,
                )
            )
        )
    finally:
        reset_services(token)

    retry_store.backfill_channel_message.assert_awaited_once_with(
        trace_id="trace-floor-1", channel_chat_id=555, channel_message_id=8675
    )
    bot.send_message.assert_awaited()


async def test_send_does_not_backfill_non_floor_turn() -> None:
    adapter, _bot = _adapter(message_id=1)
    retry_store = MagicMock()
    retry_store.backfill_channel_message = AsyncMock()
    token = set_services(StepServices(retry_queue_store=retry_store))
    try:
        await adapter.send(
            _stream(
                ResponseChunk(
                    content="a genuine answer",
                    is_final=True,
                    chunk_index=0,
                    trace_id="trace-genuine-1",
                    owl_name="o",
                    target=555,
                    is_floor=False,
                )
            )
        )
    finally:
        reset_services(token)

    retry_store.backfill_channel_message.assert_not_awaited()


async def test_send_backfill_failure_does_not_break_delivery() -> None:
    adapter, bot = _adapter(message_id=1)
    retry_store = MagicMock()
    retry_store.backfill_channel_message = AsyncMock(side_effect=RuntimeError("db down"))
    token = set_services(StepServices(retry_queue_store=retry_store))
    try:
        await adapter.send(
            _stream(
                ResponseChunk(
                    content="floor answer",
                    is_final=True,
                    chunk_index=0,
                    trace_id="trace-floor-2",
                    owl_name="o",
                    target=555,
                    is_floor=True,
                )
            )
        )
    finally:
        reset_services(token)

    bot.send_message.assert_awaited()
