"""F005 — Discord clarify preserves the ORIGINAL choice index across blanks.

When some choices are blank, the rendered button ``custom_id`` must still carry
each choice's ORIGINAL index so ``clarify:{id}:{idx}`` indexes the gateway's
stored ``entry.choices[idx]`` — a re-numbered list would map a tap to the wrong
choice. Mirrors the Telegram/Slack guarantee.

Also asserts the rich path degrades to the base numbered-text fallback when no
channel resolves for the session (never crashes the turn).
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.channels.discord.adapter import DiscordChannelAdapter
from stackowl.channels.discord.settings import DiscordSettings
from stackowl.config.test_mode import TestModeGuard


def _adapter() -> DiscordChannelAdapter:
    return DiscordChannelAdapter(
        DiscordSettings(bot_token="x" * 8, allowed_user_ids=[11])
    )


async def _seed(adapter: DiscordChannelAdapter, channel_id: int) -> AsyncMock:
    send = AsyncMock(return_value=types.SimpleNamespace(id=1))
    channel = types.SimpleNamespace(id=channel_id, send=send)
    author = types.SimpleNamespace(id=11)
    await adapter.handle_message(
        types.SimpleNamespace(content="hi", author=author, channel=channel)
    )
    await adapter._queue.get()
    # The live channel object is resolved on demand via the client (no adapter
    # cache) — wire a stub client whose get_channel returns the seeded channel.
    adapter._client = types.SimpleNamespace(
        get_channel=lambda cid: channel if cid == channel_id else None
    )
    return send


@pytest.mark.asyncio
async def test_clarify_preserves_index_across_blanks() -> None:
    """A blank middle choice must NOT shift the later choice's custom_id index."""
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        send = await _seed(adapter, channel_id=4242)
        # index 0 = "red", index 1 = "" (blank), index 2 = "blue".
        await adapter.send_clarify("11", "pick one", ["red", "", "blue"], "cid")

        send.assert_awaited()
        view = send.await_args.kwargs.get("view")
        assert view is not None
        ids = [getattr(item, "custom_id", None) for item in view.children]
        # Two buttons drawn (the blank is skipped) but indices stay ORIGINAL.
        assert "clarify:cid:0" in ids  # red
        assert "clarify:cid:2" in ids  # blue (NOT renumbered to 1)
        assert "clarify:cid:1" not in ids  # the blank index is never drawn
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_clarify_degrades_to_text_when_no_channel() -> None:
    """No resolved channel → base numbered-text fallback (no crash)."""
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        sent: list[str] = []

        async def _capture(text: str, **_kw: Any) -> None:
            sent.append(text)

        adapter.send_text = _capture  # type: ignore[method-assign]
        await adapter.send_clarify("unknown", "pick", ["a", "b"], "cid")
        assert sent and "1. a" in sent[0] and "2. b" in sent[0]
    finally:
        TestModeGuard.deactivate()
