"""Tests for DiscordChannelAdapter and its helpers."""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from stackowl.channels.discord.adapter import DiscordChannelAdapter
from stackowl.channels.discord.helpers import (
    DiscordMarkdownFormatter,
    hash_user_id,
    is_authorized,
    strip_bot_mention,
)
from stackowl.channels.discord.settings import DiscordSettings


def _make_message(text: str, user_id: int) -> Any:
    """Mock a discord.Message via SimpleNamespace duck-typing."""
    author = types.SimpleNamespace(id=user_id)
    return types.SimpleNamespace(content=text, author=author, channel=None)


def _adapter(allowed: list[int] | None = None) -> DiscordChannelAdapter:
    settings = DiscordSettings(
        bot_token="x" * 8,
        allowed_user_ids=allowed or [42],
    )
    return DiscordChannelAdapter(settings)


def test_adapter_channel_name() -> None:
    adapter = _adapter()
    assert adapter.channel_name == "discord"


def test_is_authorized_true() -> None:
    assert is_authorized(42, [1, 42, 99]) is True


def test_is_authorized_false() -> None:
    assert is_authorized(7, [1, 42, 99]) is False


def test_strip_bot_mention() -> None:
    assert strip_bot_mention("<@123> hello", 123) == "hello"


def test_strip_bot_mention_with_exclaim() -> None:
    assert strip_bot_mention("<@!123> hi", 123) == "hi"


def test_strip_bot_mention_preserves_other_mentions() -> None:
    out = strip_bot_mention("<@123> ping <@999>", 123)
    assert out == "ping <@999>"


def test_hash_user_id_length() -> None:
    digest = hash_user_id(123_456_789)
    assert len(digest) == 8
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_user_id_stable() -> None:
    assert hash_user_id(42) == hash_user_id(42)


@pytest.mark.asyncio
async def test_handle_message_unauthorized_drop() -> None:
    adapter = _adapter(allowed=[42])
    await adapter.handle_message(_make_message("hello", user_id=99))
    assert adapter._queue.empty() is True


@pytest.mark.asyncio
async def test_handle_message_authorized_enqueue() -> None:
    adapter = _adapter(allowed=[42])
    await adapter.handle_message(_make_message("hello world", user_id=42))
    assert adapter._queue.qsize() == 1
    msg = await adapter._queue.get()
    assert msg.text == "hello world"
    assert msg.channel == "discord"
    assert msg.session_id == "42"
    assert len(msg.trace_id) > 0


@pytest.mark.asyncio
async def test_handle_message_strips_bot_mention_when_known() -> None:
    adapter = _adapter(allowed=[42])
    adapter._bot_id = 555
    await adapter.handle_message(_make_message("<@555> ping", user_id=42))
    msg = await adapter._queue.get()
    assert msg.text == "ping"


@pytest.mark.asyncio
async def test_handle_message_empty_after_strip_drops() -> None:
    adapter = _adapter(allowed=[42])
    adapter._bot_id = 555
    await adapter.handle_message(_make_message("<@555>   ", user_id=42))
    assert adapter._queue.empty() is True


def test_discord_markdown_formatter_bold_passthrough() -> None:
    formatter = DiscordMarkdownFormatter()
    text = "this is **bold** text"
    assert "**bold**" in formatter.format_response(text)


def test_discord_markdown_formatter_code_fence_preserved() -> None:
    formatter = DiscordMarkdownFormatter()
    text = "before\n```python\nx = 1\n```\nafter"
    out = formatter.format_response(text)
    assert "```python\nx = 1\n```" in out


def test_evolution_notification_format() -> None:
    formatter = DiscordMarkdownFormatter()
    line = formatter.format_evolution_notification(
        "Architect",
        [("verbosity", 0.4, 0.6), ("challenge", 0.2, 0.5)],
    )
    assert "Architect" in line
    assert "verbosity" in line
    assert "0.40" in line
    assert "0.60" in line
    # Single line — compact summary.
    assert "\n" not in line


def test_settings_frozen() -> None:
    s = DiscordSettings(bot_token="t", allowed_user_ids=[1])
    with pytest.raises(ValidationError):
        s.bot_token = "other"  # type: ignore[misc]


def test_settings_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        DiscordSettings(unknown_field="x")  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_health_check_no_heartbeat_is_degraded() -> None:
    adapter = _adapter()
    status = await adapter.health_check()
    assert status.status == "degraded"
    assert status.name == "discord"


@pytest.mark.asyncio
async def test_health_check_recent_heartbeat_is_ok() -> None:
    import time as _time

    adapter = _adapter()
    # F004-part1: ok now requires a live client (liveness gate), not just a
    # fresh heartbeat — a stand-in client satisfies the gate.
    adapter._client = object()  # type: ignore[assignment]
    adapter._last_heartbeat_at = _time.monotonic()
    status = await adapter.health_check()
    assert status.status == "ok"


@pytest.mark.asyncio
async def test_health_check_stale_heartbeat_is_degraded() -> None:
    import time as _time

    adapter = _adapter()
    adapter._client = object()  # type: ignore[assignment]  # live client → past the liveness gate
    adapter._last_heartbeat_at = _time.monotonic() - 120.0
    status = await adapter.health_check()
    assert status.status == "degraded"
    assert status.message == "heartbeat stale"


# --------------------------------------------------------------------------- #
# Progress-chunk filtering
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_filters_progress_chunks() -> None:
    """Progress chunks should never be added to the delivered answer buffer."""
    from collections.abc import AsyncIterator

    from stackowl.config.test_mode import TestModeGuard
    from stackowl.pipeline.streaming import ResponseChunk

    adapter = _adapter(allowed=[42])
    TestModeGuard.deactivate()

    async def _drain(*chunks: ResponseChunk) -> AsyncIterator[ResponseChunk]:
        for c in chunks:
            yield c

    # Mix of progress and answer chunks
    progress_chunk = ResponseChunk(
        content="[thinking...]",
        is_final=False,
        chunk_index=0,
        trace_id="t-test",
        owl_name="owl",
        target=42,
        kind="progress",  # Live status, should NOT appear in delivered message
    )
    answer_chunk = ResponseChunk(
        content="final answer",
        is_final=True,
        chunk_index=1,
        trace_id="t-test",
        owl_name="owl",
        target=42,
        kind="answer",  # Real answer, should appear in delivered message
    )

    # Mock a channel that captures what's sent
    send_mock = AsyncMock()
    channel_mock = types.SimpleNamespace(id=42, send=send_mock)

    # Inject the mock client
    adapter._client = types.SimpleNamespace(
        get_channel=lambda cid: channel_mock if cid == 42 else None
    )

    try:
        await adapter.send(_drain(progress_chunk, answer_chunk))
        send_mock.assert_awaited_once()
        # Extract the sent text
        sent_text = str(send_mock.await_args_list[0].args[0])
        # The delivered text must contain ONLY the answer, not the progress
        assert "final answer" in sent_text
        assert "[thinking...]" not in sent_text
    finally:
        TestModeGuard.deactivate()
