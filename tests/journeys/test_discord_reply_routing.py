"""F001 merge-gate — Discord reply target threading (no cross-deliver).

Two CONCURRENT inbound messages arrive on DIFFERENT Discord channels. Each
turn's reply (driven through the real ``send()`` path, with the chunk carrying
the originating channel-id as ``target`` — exactly what ``deliver.py`` stamps)
MUST reach ITS OWN channel's ``channel.send`` — never the other's, and never
the shared ``_last_channel_id``.

This is the exact cross-deliver scenario Telegram/Slack already cover, applied
to the Discord channel after it joins the target-threading chain.
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.channels.discord.adapter import DiscordChannelAdapter
from stackowl.channels.discord.settings import DiscordSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.pipeline.streaming import ResponseChunk


def _adapter() -> DiscordChannelAdapter:
    return DiscordChannelAdapter(
        DiscordSettings(bot_token="x" * 8, allowed_user_ids=[11, 22])
    )


def _message(text: str, user_id: int, channel_id: int) -> tuple[Any, AsyncMock]:
    """Mock a discord.Message whose channel records its own send() calls."""
    send = AsyncMock()
    channel = types.SimpleNamespace(id=channel_id, send=send)
    author = types.SimpleNamespace(id=user_id)
    return types.SimpleNamespace(content=text, author=author, channel=channel), send


async def _chunks(*chunks: ResponseChunk) -> Any:
    for c in chunks:
        yield c


def _chunk(content: str, target: int | str | None) -> ResponseChunk:
    return ResponseChunk(
        content=content,
        is_final=True,
        chunk_index=0,
        trace_id="t",
        owl_name="owl",
        target=target,
    )


@pytest.mark.asyncio
async def test_discord_stamps_channel_id_and_resolves_target() -> None:
    """handle_message stamps message.channel.id (NOT author.id); resolve_target reads it."""
    adapter = _adapter()
    msg, _ = _message("hello", user_id=11, channel_id=7777)
    await adapter.handle_message(msg)
    ingress = await adapter._queue.get()
    # chat_id is the CHANNEL id, session_id stays the user id.
    assert ingress.chat_id == 7777
    assert ingress.session_id == "11"
    assert adapter.resolve_target("11") == 7777
    # Unknown session is never guessed.
    assert adapter.resolve_target("999") is None


@pytest.mark.asyncio
async def test_concurrent_inbound_no_cross_deliver() -> None:
    """Two channels in flight: each reply reaches ITS OWN channel.send, not the other."""
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        msg_a, send_a = _message("from A", user_id=11, channel_id=1001)
        msg_b, send_b = _message("from B", user_id=22, channel_id=2002)

        # Both inbound arrive; B is newest so _last_channel_id now points at B.
        await adapter.handle_message(msg_a)
        await adapter.handle_message(msg_b)

        # Reply to A's turn — its chunk carries A's channel id (deliver.py stamp).
        await adapter.send(_chunks(_chunk("reply to A", target=1001)))
        # Reply to B's turn.
        await adapter.send(_chunks(_chunk("reply to B", target=2002)))

        send_a.assert_awaited()
        send_b.assert_awaited()
        a_text = "".join(str(c.args[0]) for c in send_a.await_args_list)
        b_text = "".join(str(c.args[0]) for c in send_b.await_args_list)
        assert "reply to A" in a_text
        assert "reply to B" in b_text
        # No cross-deliver: A's channel never got B's reply.
        assert "reply to B" not in a_text
        assert "reply to A" not in b_text
    finally:
        TestModeGuard.deactivate()
