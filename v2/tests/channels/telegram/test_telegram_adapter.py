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
