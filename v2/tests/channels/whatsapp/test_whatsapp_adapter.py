"""Tests for WhatsAppChannelAdapter — Story 9.8."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stackowl.channels.whatsapp.adapter import WhatsAppChannelAdapter
from stackowl.channels.whatsapp.settings import WhatsAppSettings
from stackowl.config.test_mode import TestModeGuard, TestModeViolation


# --------------------------------------------------------------------------- #
# Factories
# --------------------------------------------------------------------------- #


def _settings(allowed: frozenset[str] | None = None) -> WhatsAppSettings:
    return WhatsAppSettings(
        allowed_phone_numbers=allowed if allowed is not None else frozenset(["15551234567"]),
        session_dir="/tmp/test_whatsapp_session",
    )


def _adapter(allowed: frozenset[str] | None = None) -> WhatsAppChannelAdapter:
    return WhatsAppChannelAdapter(_settings(allowed), data_dir="/tmp/test_data")


# --------------------------------------------------------------------------- #
# 1. channel_name
# --------------------------------------------------------------------------- #


def test_channel_name_is_whatsapp() -> None:
    """channel_name property returns the string 'whatsapp'."""
    adapter = _adapter()
    assert adapter.channel_name == "whatsapp"


# --------------------------------------------------------------------------- #
# 2. handle_message — authorized JID enqueues IngressMessage
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_handle_message_authorized_enqueues() -> None:
    """Authorized sender gets an IngressMessage enqueued."""
    adapter = _adapter(allowed=frozenset(["15551234567"]))
    await adapter.handle_message("15551234567@s.whatsapp.net", "hello world")

    assert adapter._queue.qsize() == 1
    msg = await adapter._queue.get()
    assert msg.text == "hello world"
    assert msg.channel == "whatsapp"
    assert msg.session_id.startswith("whatsapp:")
    assert "15551234567" not in msg.session_id  # never raw phone in session_id


# --------------------------------------------------------------------------- #
# 3. handle_message — unauthorized JID drops silently
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_handle_message_unauthorized_drops_silently() -> None:
    """Unauthorized sender's message is silently dropped (fail-closed)."""
    adapter = _adapter(allowed=frozenset(["15551234567"]))
    await adapter.handle_message("15559999999@s.whatsapp.net", "intrusion attempt")
    assert adapter._queue.empty()


# --------------------------------------------------------------------------- #
# 4. handle_message — empty allowed set drops all
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_handle_message_empty_allowed_drops_all() -> None:
    """Empty frozenset means no sender is authorized — fail-closed."""
    adapter = _adapter(allowed=frozenset())
    await adapter.handle_message("15551234567@s.whatsapp.net", "hello")
    assert adapter._queue.empty()


# --------------------------------------------------------------------------- #
# 5. handle_message — creates correct trace_id
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_handle_message_creates_trace_id() -> None:
    """Each handle_message call produces a unique non-empty trace_id."""
    adapter = _adapter(allowed=frozenset(["15551234567"]))
    await adapter.handle_message("15551234567@s.whatsapp.net", "first message")
    await adapter.handle_message("15551234567@s.whatsapp.net", "second message")

    msg1 = await adapter._queue.get()
    msg2 = await adapter._queue.get()
    assert len(msg1.trace_id) > 0
    assert len(msg2.trace_id) > 0
    assert msg1.trace_id != msg2.trace_id


# --------------------------------------------------------------------------- #
# 6. handle_message — updates _last_poll_at
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_handle_message_updates_last_poll_at() -> None:
    """handle_message sets _last_poll_at after successful enqueue."""
    adapter = _adapter(allowed=frozenset(["15551234567"]))
    assert adapter._last_poll_at is None

    before = time.monotonic()
    await adapter.handle_message("15551234567@s.whatsapp.net", "ping")
    assert adapter._last_poll_at is not None
    assert adapter._last_poll_at >= before


# --------------------------------------------------------------------------- #
# 7. start() raises TestModeViolation in test mode
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_start_raises_in_test_mode() -> None:
    """start() is blocked in test mode — live browser launch is prevented."""
    TestModeGuard.activate()
    try:
        adapter = _adapter()
        with pytest.raises(TestModeViolation):
            await adapter.start()
    finally:
        TestModeGuard.deactivate()


# --------------------------------------------------------------------------- #
# 8. send() raises TestModeViolation in test mode
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_raises_in_test_mode() -> None:
    """send() is blocked in test mode — no browser interaction allowed."""

    async def _empty_chunks():  # type: ignore[return]
        return
        yield  # pragma: no cover

    TestModeGuard.activate()
    try:
        adapter = _adapter()
        with pytest.raises(TestModeViolation):
            await adapter.send(_empty_chunks())
    finally:
        TestModeGuard.deactivate()


# --------------------------------------------------------------------------- #
# 9. send_text() raises TestModeViolation in test mode
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_text_raises_in_test_mode() -> None:
    """send_text() is blocked in test mode."""
    TestModeGuard.activate()
    try:
        adapter = _adapter()
        with pytest.raises(TestModeViolation):
            await adapter.send_text("Hello!")
    finally:
        TestModeGuard.deactivate()


# --------------------------------------------------------------------------- #
# 10. health_check() — degraded when _last_poll_at is None
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_health_check_degraded_when_no_poll() -> None:
    """health_check() returns 'degraded' before any messages are processed."""
    adapter = _adapter()
    status = await adapter.health_check()
    assert status.status == "degraded"
    assert status.name == "whatsapp"


# --------------------------------------------------------------------------- #
# 11. health_check() — ok after handle_message runs
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_health_check_ok_after_handle_message() -> None:
    """health_check() returns 'ok' after a message has been processed."""
    adapter = _adapter(allowed=frozenset(["15551234567"]))
    await adapter.handle_message("15551234567@s.whatsapp.net", "test")

    status = await adapter.health_check()
    assert status.status == "ok"
    assert status.name == "whatsapp"


# --------------------------------------------------------------------------- #
# 12. register_with_registry() adds adapter to ChannelRegistry
# --------------------------------------------------------------------------- #


def test_register_with_registry_adds_adapter() -> None:
    """register_with_registry() registers the adapter in the ChannelRegistry."""
    from stackowl.channels.registry import ChannelRegistry

    registry = ChannelRegistry.instance()
    # Ensure a clean registry state for this test.
    registry.reset()
    try:
        adapter = _adapter()
        adapter.register_with_registry()
        assert registry.get("whatsapp") is adapter
    finally:
        registry.reset()


# --------------------------------------------------------------------------- #
# 13. health_check() — degraded when _last_poll_at is stale (>60s)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_health_check_degraded_when_stale_poll() -> None:
    """health_check() returns 'degraded' when last poll was >60s ago."""
    adapter = _adapter()
    adapter._last_poll_at = time.monotonic() - 120.0
    status = await adapter.health_check()
    assert status.status == "degraded"
    assert "stale" in (status.message or "")


# --------------------------------------------------------------------------- #
# 14. session_id uses hashed jid, not raw phone
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_session_id_does_not_contain_raw_phone() -> None:
    """IngressMessage.session_id must not contain the raw phone number."""
    adapter = _adapter(allowed=frozenset(["15551234567"]))
    await adapter.handle_message("15551234567@s.whatsapp.net", "private message")
    msg = await adapter._queue.get()
    assert "15551234567" not in msg.session_id
    assert msg.session_id.startswith("whatsapp:")
