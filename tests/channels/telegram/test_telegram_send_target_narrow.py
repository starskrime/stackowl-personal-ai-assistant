"""Telegram adapter.send target-narrowing (A1 widen completion).

After Slack-A1 widened ``ResponseChunk.target`` to ``int | str | None``, the
Telegram ``send`` path must NARROW that target back to ``int | None`` — Telegram
delivers ONLY to int chat_ids (``send_text(chat_id: int | None)`` is NOT widened).

A genuine ``str`` target (a Slack channel/thread_ts) cannot reach the Telegram
adapter by construction (each turn is delivered by its OWN channel adapter), so a
stray ``str`` is a loud-but-recoverable anomaly: log a warning and fall back to
``_last_chat_id`` (target → None), never crash. An ``int`` target flows through
untouched.
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.exceptions import DeliveryError
from stackowl.pipeline.streaming import ResponseChunk


def _settings() -> TelegramSettings:
    return TelegramSettings(bot_token="test_token_x" * 3, allowed_user_ids=frozenset({42}))


def _adapter_with_bot() -> tuple[TelegramChannelAdapter, MagicMock]:
    """Adapter wired to a fake bot whose send_message records its kwargs."""
    adapter = TelegramChannelAdapter(_settings())
    bot = MagicMock()
    bot.send_message = AsyncMock()
    adapter._bot_app = types.SimpleNamespace(bot=bot)
    return adapter, bot


def _chunk(content: str, target: int | str | None) -> ResponseChunk:
    return ResponseChunk(
        content=content,
        is_final=True,
        chunk_index=0,
        trace_id="t-1",
        owl_name="owl",
        target=target,
    )


async def _chunks(*chunks: ResponseChunk) -> Any:
    for c in chunks:
        yield c


@pytest.mark.asyncio
async def test_send_int_target_delivers_to_that_chat() -> None:
    """An int target (Telegram chat_id) flows through to send_message unchanged."""
    adapter, bot = _adapter_with_bot()
    await adapter.send(_chunks(_chunk("hello", 456)))
    bot.send_message.assert_awaited()
    assert bot.send_message.await_args.kwargs["chat_id"] == 456


@pytest.mark.asyncio
async def test_send_str_target_narrows_to_none_and_raises() -> None:
    """A stray str target (Slack) narrows to None and, on the on-turn path with no
    ``_last_chat_id``, FAILS LOUD (C6 / C-1) — a turn's answer is never silently
    dropped. (Intentional contract change from the old silent-drop behaviour.)

    The unexpected str is still warned; then send_text raises DeliveryError
    because the explicit on-turn target is unresolvable. No str chat_id is ever
    passed to send_message.
    """
    adapter, bot = _adapter_with_bot()
    assert adapter._last_chat_id is None  # no fallback chat available
    with patch("stackowl.channels.telegram.adapter.log") as mock_log:
        with pytest.raises(DeliveryError) as ei:
            await adapter.send(_chunks(_chunk("hi", "C123")))
        # Loud, not silent: the unexpected str target is warned.
        mock_log.telegram.warning.assert_called()
    assert ei.value.channel == "telegram"
    assert ei.value.reason == "no_target"
    # Never a str chat_id sent.
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_str_target_falls_back_to_last_chat_id() -> None:
    """A stray str target falls back to _last_chat_id when one exists (recoverable)."""
    adapter, bot = _adapter_with_bot()
    adapter._last_chat_id = 9001
    with patch("stackowl.channels.telegram.adapter.log") as mock_log:
        await adapter.send(_chunks(_chunk("hi", "C999")))
        mock_log.telegram.warning.assert_called()
    # Fell back to the last known int chat, not the str target.
    bot.send_message.assert_awaited()
    assert bot.send_message.await_args.kwargs["chat_id"] == 9001
