"""F000 — Discord send_text actually calls channel.send() per split part.

Previously send_text only LOGGED each part (silent drop). After the fix it
resolves the live channel on demand from the discord.py client
(``self._client.get_channel(target)``) and calls ``channel.send(part)`` for
every split part. Fail-loud on an explicit-but-unresolvable target
(``DeliveryError("discord","no_target")``) and on an unknown channel id
(``DeliveryError("discord","no_channel")``) — never a silent drop.
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.channels.discord.adapter import DiscordChannelAdapter
from stackowl.channels.discord.settings import DiscordSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import DeliveryError
from stackowl.pipeline.streaming import ResponseChunk


def _adapter() -> DiscordChannelAdapter:
    return DiscordChannelAdapter(
        DiscordSettings(bot_token="x" * 8, allowed_user_ids=[42])
    )


def _channel(channel_id: int) -> tuple[Any, AsyncMock]:
    send = AsyncMock()
    return types.SimpleNamespace(id=channel_id, send=send), send


def _wire_client(adapter: DiscordChannelAdapter, channel_id: int, channel: Any) -> None:
    """Attach a stub discord.py client whose get_channel returns ``channel``.

    The live channel is resolved on demand via ``self._client.get_channel`` —
    there is no adapter-side channel cache.
    """
    adapter._client = types.SimpleNamespace(
        get_channel=lambda cid: channel if cid == channel_id else None
    )


@pytest.mark.asyncio
async def test_send_text_calls_channel_send_for_each_part() -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        channel, send = _channel(123)
        _wire_client(adapter, 123, channel)
        await adapter.send_text("hi there", channel_id=123)
        send.assert_awaited()
        sent = "".join(str(c.args[0]) for c in send.await_args_list)
        assert "hi there" in sent
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_text_long_text_splits_and_sends_all_parts() -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        channel, send = _channel(5)
        _wire_client(adapter, 5, channel)
        # > 2000 chars forces a split — every part must be sent (none merely logged).
        long_text = "x" * 4500
        await adapter.send_text(long_text, channel_id=5)
        assert send.await_count >= 2
        total = "".join(str(c.args[0]) for c in send.await_args_list)
        assert len(total) == len(long_text)
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_explicit_unresolvable_target_raises_no_target() -> None:
    """On-turn send() with an unresolvable target (stray-narrowed to None, no
    _last_channel_id) raises no_target — an answer is never silently dropped."""
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        assert adapter._last_channel_id is None

        async def _stray() -> Any:
            # A str target cannot reach Discord (int-only) → narrowed to None on
            # the on-turn path with no fallback → no_target.
            yield ResponseChunk(
                content="hi", is_final=True, chunk_index=0,
                trace_id="t", owl_name="o", target="C-not-int",
            )

        with pytest.raises(DeliveryError) as ei:
            await adapter.send(_stray())
        assert ei.value.channel == "discord"
        assert ei.value.reason == "no_target"
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_text_unknown_channel_raises_no_channel() -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        # No live client (get_channel unavailable) → no_channel.
        with pytest.raises(DeliveryError) as ei:
            await adapter.send_text("hi", channel_id=999)
        assert ei.value.channel == "discord"
        assert ei.value.reason == "no_channel"
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_text_no_explicit_target_best_effort_noop() -> None:
    """Best-effort (no explicit target) + no _last_channel_id → logged no-op, no raise."""
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        assert adapter._last_channel_id is None
        # Must NOT raise (preserves proactive never-raises contract).
        await adapter.send_text("proactive ping")
    finally:
        TestModeGuard.deactivate()
