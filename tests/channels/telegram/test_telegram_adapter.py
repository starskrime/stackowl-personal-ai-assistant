"""Tests for TelegramChannelAdapter and its helpers."""

from __future__ import annotations

import time
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.helpers import hash_user_id, is_authorized, strip_bot_mention
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard

# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------


def _settings(allowed: frozenset[int] | None = None) -> TelegramSettings:
    return TelegramSettings(
        bot_token="test_token_x" * 3,
        allowed_user_ids=allowed if allowed is not None else frozenset({42}),
    )


def _adapter(allowed: frozenset[int] | None = None) -> TelegramChannelAdapter:
    return TelegramChannelAdapter(_settings(allowed))


def _make_update(text: str, user_id: int, chat_id: int = 100) -> Any:
    """Build a duck-typed Update-like object for handle_update tests."""
    user = types.SimpleNamespace(id=user_id)
    message = types.SimpleNamespace(text=text)
    chat = types.SimpleNamespace(id=chat_id)
    update = types.SimpleNamespace(
        effective_message=message,
        effective_user=user,
        effective_chat=chat,
    )
    return update


async def _call_handle_update(adapter: TelegramChannelAdapter, update: Any) -> None:
    """Invoke the private _handle_update method directly (bypasses PTB dispatcher)."""
    ctx: Any = types.SimpleNamespace()
    await adapter._handle_update(update, ctx)


# ---------------------------------------------------------------------------
# 1. channel_name
# ---------------------------------------------------------------------------


def test_channel_name_is_telegram() -> None:
    adapter = _adapter()
    assert adapter.channel_name == "telegram"


# ---------------------------------------------------------------------------
# 2. handle_update with authorized user enqueues IngressMessage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_update_authorized_enqueues() -> None:
    adapter = _adapter(allowed=frozenset({42}))
    update = _make_update("hello world", user_id=42, chat_id=100)
    await _call_handle_update(adapter, update)

    assert adapter._queue.qsize() == 1
    msg = await adapter._queue.get()
    assert msg.text == "hello world"
    assert msg.channel == "telegram"
    assert msg.session_id == "42"
    assert len(msg.trace_id) > 0


# ---------------------------------------------------------------------------
# 3. handle_update with unauthorized user drops silently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_update_unauthorized_drops_silently() -> None:
    adapter = _adapter(allowed=frozenset({42}))
    update = _make_update("hello", user_id=99)
    await _call_handle_update(adapter, update)

    assert adapter._queue.empty()


# ---------------------------------------------------------------------------
# 4. handle_update with empty frozenset drops all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_update_empty_allowed_drops_all() -> None:
    adapter = _adapter(allowed=frozenset())
    update = _make_update("hello", user_id=42)
    await _call_handle_update(adapter, update)

    assert adapter._queue.empty()


# ---------------------------------------------------------------------------
# 5. handle_update updates _last_chat_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_update_sets_last_chat_id() -> None:
    adapter = _adapter(allowed=frozenset({42}))
    assert adapter._last_chat_id is None

    update = _make_update("ping", user_id=42, chat_id=9001)
    await _call_handle_update(adapter, update)

    assert adapter._last_chat_id == 9001


# ---------------------------------------------------------------------------
# 6. handle_update updates _last_update_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_update_sets_last_update_at() -> None:
    adapter = _adapter(allowed=frozenset({42}))
    assert adapter._last_update_at is None

    before = time.monotonic()
    update = _make_update("ping", user_id=42)
    await _call_handle_update(adapter, update)

    assert adapter._last_update_at is not None
    assert adapter._last_update_at >= before


# ---------------------------------------------------------------------------
# 7. handle_update strips bot mention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_update_strips_bot_mention() -> None:
    adapter = _adapter(allowed=frozenset({42}))
    adapter._bot_username = "MyBot"

    update = _make_update("@MyBot hello there", user_id=42)
    await _call_handle_update(adapter, update)

    msg = await adapter._queue.get()
    assert msg.text == "hello there"


# ---------------------------------------------------------------------------
# 8. start() raises TestModeViolation in test mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_raises_in_test_mode() -> None:
    from stackowl.config.test_mode import TestModeViolation

    TestModeGuard.activate()
    try:
        adapter = _adapter()
        with pytest.raises(TestModeViolation):
            await adapter.start()
    finally:
        TestModeGuard.deactivate()


# ---------------------------------------------------------------------------
# 9. send() raises TestModeViolation in test mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_raises_in_test_mode() -> None:
    from stackowl.config.test_mode import TestModeViolation

    async def _empty_chunks():  # type: ignore[return]
        return
        yield  # make it an async generator

    TestModeGuard.activate()
    try:
        adapter = _adapter()
        with pytest.raises(TestModeViolation):
            await adapter.send(_empty_chunks())
    finally:
        TestModeGuard.deactivate()


# ---------------------------------------------------------------------------
# 10. send_text() raises TestModeViolation in test mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_text_raises_in_test_mode() -> None:
    from stackowl.config.test_mode import TestModeViolation

    TestModeGuard.activate()
    try:
        adapter = _adapter()
        with pytest.raises(TestModeViolation):
            await adapter.send_text("hello")
    finally:
        TestModeGuard.deactivate()


# ---------------------------------------------------------------------------
# 11. health_check() returns degraded when no update received
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_no_update_is_degraded() -> None:
    adapter = _adapter()
    status = await adapter.health_check()
    assert status.status == "degraded"
    assert status.name == "telegram"


# ---------------------------------------------------------------------------
# 12. health_check() returns ok after handle_update populates _last_update_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_ok_after_update() -> None:
    adapter = _adapter(allowed=frozenset({42}))
    update = _make_update("ping", user_id=42)
    await _call_handle_update(adapter, update)

    status = await adapter.health_check()
    assert status.status == "ok"
    assert status.name == "telegram"


# ---------------------------------------------------------------------------
# Helpers unit tests
# ---------------------------------------------------------------------------


def test_hash_user_id_length() -> None:
    digest = hash_user_id(123_456_789)
    assert len(digest) == 8
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_user_id_stable() -> None:
    assert hash_user_id(42) == hash_user_id(42)


def test_is_authorized_true() -> None:
    assert is_authorized(42, frozenset({1, 42, 99})) is True


def test_is_authorized_false() -> None:
    assert is_authorized(7, frozenset({1, 42, 99})) is False


def test_is_authorized_empty_frozenset_denies_all() -> None:
    assert is_authorized(42, frozenset()) is False


def test_strip_bot_mention_removes_prefix() -> None:
    assert strip_bot_mention("@MyBot hello", "MyBot") == "hello"


def test_strip_bot_mention_case_insensitive() -> None:
    assert strip_bot_mention("@mybot hi", "MyBot") == "hi"


def test_strip_bot_mention_empty_username_passthrough() -> None:
    assert strip_bot_mention("hello world", "") == "hello world"


# ---------------------------------------------------------------------------
# edit_message — rewrites a message + (by default) removes the inline keyboard
# ---------------------------------------------------------------------------


def _adapter_with_bot() -> tuple[TelegramChannelAdapter, MagicMock]:
    """Adapter wired to a fake bot whose edit_message_text records its kwargs."""
    adapter = _adapter()
    bot = MagicMock()
    bot.edit_message_text = AsyncMock()
    adapter._bot_app = types.SimpleNamespace(bot=bot)
    return adapter, bot


@pytest.mark.asyncio
async def test_edit_message_calls_edit_message_text_with_raw_text() -> None:
    adapter, bot = _adapter_with_bot()
    ok = await adapter.edit_message(555, 17, "✅ done", reply_markup=None)
    assert ok is True
    bot.edit_message_text.assert_awaited_once()
    kwargs = bot.edit_message_text.await_args.kwargs
    assert kwargs["chat_id"] == 555
    assert kwargs["message_id"] == 17
    assert kwargs["text"] == "✅ done"
    # parse_mode is None — the decision text is sent raw (no MarkdownV2 escaping).
    assert kwargs["parse_mode"] is None
    # Default reply_markup=None → removes the inline keyboard.
    assert kwargs["reply_markup"] is None


@pytest.mark.asyncio
async def test_edit_message_passes_reply_markup_through() -> None:
    adapter, bot = _adapter_with_bot()
    markup = object()
    await adapter.edit_message(1, 2, "x", reply_markup=markup)
    assert bot.edit_message_text.await_args.kwargs["reply_markup"] is markup


@pytest.mark.asyncio
async def test_edit_message_treats_not_modified_as_benign() -> None:
    """A 'message is not modified' error is benign — no raise, returns False."""
    adapter, bot = _adapter_with_bot()
    bot.edit_message_text = AsyncMock(
        side_effect=RuntimeError("Bad Request: message is not modified")
    )
    # Must NOT raise — a benign no-op edit cannot break the consent flow.
    ok = await adapter.edit_message(1, 2, "same text")
    assert ok is False


@pytest.mark.asyncio
async def test_edit_message_swallows_errors_and_logs() -> None:
    """A genuine edit failure is logged and returns False — never raises."""
    adapter, bot = _adapter_with_bot()
    bot.edit_message_text = AsyncMock(side_effect=RuntimeError("network down"))
    with patch("stackowl.channels.telegram.adapter.log") as mock_log:
        ok = await adapter.edit_message(1, 2, "text")
        assert ok is False
        mock_log.telegram.error.assert_called()


@pytest.mark.asyncio
async def test_edit_message_noop_when_bot_uninitialised() -> None:
    adapter = _adapter()
    assert adapter._bot_app is None
    ok = await adapter.edit_message(1, 2, "text")
    assert ok is False


# ---------------------------------------------------------------------------
# send_text — MarkdownV2 rejection falls back to plain text (never lose the msg)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_text_falls_back_to_plain_on_markdownv2_rejection() -> None:
    """A malformed-MarkdownV2 BadRequest must not drop the message — the same
    content is re-sent as plain text so the user always receives it."""
    from telegram.error import BadRequest

    adapter = _adapter()
    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=[BadRequest("can't parse entities"), None]
    )
    adapter._bot_app = types.SimpleNamespace(bot=bot)

    await adapter.send_text("oops *bad markup", chat_id=555)

    assert bot.send_message.await_count == 2
    first = bot.send_message.await_args_list[0].kwargs
    second = bot.send_message.await_args_list[1].kwargs
    assert first["parse_mode"] == "MarkdownV2"
    # Fallback: same chat, same text, markup dropped.
    assert second["parse_mode"] is None
    assert second["chat_id"] == 555
    assert second["text"] == first["text"] == "oops *bad markup"


@pytest.mark.asyncio
async def test_send_text_propagates_non_parse_errors() -> None:
    """A non-parse error (network/auth/chat-not-found) is a real delivery
    failure — it propagates, it is NOT swallowed by the plain-text fallback."""
    from telegram.error import NetworkError

    adapter = _adapter()
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=NetworkError("connection reset"))
    adapter._bot_app = types.SimpleNamespace(bot=bot)

    with pytest.raises(NetworkError):
        await adapter.send_text("hello", chat_id=555)
    # Only the MarkdownV2 attempt — no plain-text retry on a non-parse error.
    assert bot.send_message.await_count == 1
