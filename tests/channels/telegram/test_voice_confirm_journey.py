"""INTEGRATION journey — Telegram voice → transcribe → confirm → pipeline.

Drives the REAL path end-to-end with a fake transcriber injected (never the real
Whisper model): a voice update flows through the real TelegramVoiceHandler, and a
Send/Discard tap flows through the REAL CallbackRouter into the adapter's ingress
queue. This proves handler → router → queue REACHABILITY (the "registered ≠
reachable" guardrail), not just isolated units.

The only mocked surfaces are the adapter's network I/O (download_media,
send_inline_keyboard, edit_message, acknowledge_callback) — the message
construction, routing, idempotency, and enqueue are all the real code.
"""

from __future__ import annotations

import types
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.callbacks import CallbackRouter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.channels.telegram.voice import TelegramVoiceHandler
from stackowl.channels.telegram.voice_confirm import (
    CALLBACK_PREFIX,
    PendingTranscriptStore,
    VoiceConfirmHandler,
)
from stackowl.config.settings import TranscriptionSettings
from stackowl.db.pool import DbPool
from stackowl.media.stt.base import SttAvailability, SttBackend, SttResult
from stackowl.media.stt.selector import SttSelector

pytestmark = pytest.mark.asyncio


class _StubBackend(SttBackend):
    """Returns a fixed transcript (or a structured error) — no model load."""

    def __init__(self, text: str = "", *, error: str | None = None) -> None:
        self._text = text
        self._error = error

    @property
    def name(self) -> str:
        return "stub"

    @property
    def is_local(self) -> bool:
        return True

    async def is_available(self) -> SttAvailability:
        return SttAvailability.ok()

    async def transcribe(
        self, audio_bytes: bytes, *, audio_format: str = "ogg"
    ) -> SttResult | str:
        if self._error is not None:
            return self._error
        return SttResult(text=self._text, backend="stub", is_local=True)


@pytest.fixture
async def db_pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    pool = DbPool(db_path=tmp_path / "test_voice_journey.db")
    await pool.open()
    yield pool
    await pool.close()


def _make_adapter() -> Any:
    settings = TelegramSettings(
        bot_token="test_token_x" * 3,
        allowed_user_ids=frozenset({42}),
    )
    adapter = TelegramChannelAdapter(settings)
    # Mock only the network I/O — everything else is the real adapter.
    adapter.download_media = AsyncMock(return_value=b"ogg-bytes")
    adapter.send_typing = AsyncMock()
    adapter.acknowledge_callback = AsyncMock()
    adapter.edit_message = AsyncMock(return_value=True)
    # send_inline_keyboard returns a Message-like object carrying message_id.
    adapter.send_inline_keyboard = AsyncMock(
        return_value=types.SimpleNamespace(message_id=555)
    )
    return adapter


def _make_voice_update(file_id: str, user_id: int, chat_id: int) -> Any:
    voice = types.SimpleNamespace(file_id=file_id)
    message = types.SimpleNamespace(voice=voice, text=None, reply_to_message=None)
    return types.SimpleNamespace(
        effective_message=message,
        effective_user=types.SimpleNamespace(id=user_id),
        effective_chat=types.SimpleNamespace(id=chat_id),
    )


def _make_callback_update(callback_id: str, callback_data: str) -> Any:
    cq = types.SimpleNamespace(id=callback_id, data=callback_data)
    return types.SimpleNamespace(callback_query=cq)


async def _wire(adapter: Any, db_pool: DbPool, backend: SttBackend) -> tuple[
    TelegramVoiceHandler, CallbackRouter
]:
    """Wire the REAL handler + router exactly as the orchestrator does."""
    selector = SttSelector(TranscriptionSettings(enabled=True), local=backend)
    pending = PendingTranscriptStore()
    handler = TelegramVoiceHandler(selector, adapter, pending)
    router = CallbackRouter(db_pool, adapter)
    await router.ensure_table()
    confirm = VoiceConfirmHandler(adapter, pending)
    router.register(f"{CALLBACK_PREFIX}:", confirm.handle_callback)
    return handler, router


def _last_callback_data(adapter: Any, action: str) -> str:
    """Extract the vtx:{action}:{id} callback_data from the sent keyboard."""
    keyboard = adapter.send_inline_keyboard.call_args.args[1]
    buttons = [btn for row in keyboard["inline_keyboard"] for btn in row]
    for btn in buttons:
        if btn["callback_data"].split(":")[1] == action:
            return str(btn["callback_data"])
    raise AssertionError(f"no {action} button in keyboard")


# ---------------------------------------------------------------------------
# Send → an IngressMessage with the transcript reaches the queue (chat_id stamped)
# ---------------------------------------------------------------------------
async def test_send_tap_injects_transcript_into_queue(db_pool: DbPool) -> None:
    adapter = _make_adapter()
    handler, router = await _wire(adapter, db_pool, _StubBackend("book a table for two"))

    # 1. Voice note arrives → transcript presented for confirmation, NOTHING queued.
    await handler.handle_voice(_make_voice_update("F1", user_id=42, chat_id=999), None)
    assert adapter._queue.qsize() == 0
    adapter.send_inline_keyboard.assert_called_once()

    # 2. User taps ✅ Send → route through the REAL router → message enqueued.
    send_data = _last_callback_data(adapter, "send")
    await router.route(_make_callback_update("cb-1", send_data), None)

    assert adapter._queue.qsize() == 1
    msg = await adapter._queue.get()
    assert msg.text == "book a table for two"
    assert msg.session_id == "42"
    assert msg.channel == "telegram"
    assert msg.chat_id == 999  # STAMPED — routes the answer back to this chat
    # The router acknowledged the tap (handler must not ack itself).
    adapter.acknowledge_callback.assert_awaited()


# ---------------------------------------------------------------------------
# Discard → nothing is injected
# ---------------------------------------------------------------------------
async def test_discard_tap_injects_nothing(db_pool: DbPool) -> None:
    adapter = _make_adapter()
    handler, router = await _wire(adapter, db_pool, _StubBackend("delete everything"))

    await handler.handle_voice(_make_voice_update("F2", user_id=42, chat_id=7), None)
    discard_data = _last_callback_data(adapter, "discard")
    await router.route(_make_callback_update("cb-2", discard_data), None)

    assert adapter._queue.qsize() == 0


# ---------------------------------------------------------------------------
# A second tap on an already-resolved transcript is a no-op (no double-inject)
# ---------------------------------------------------------------------------
async def test_double_send_tap_injects_once(db_pool: DbPool) -> None:
    adapter = _make_adapter()
    handler, router = await _wire(adapter, db_pool, _StubBackend("hello"))

    await handler.handle_voice(_make_voice_update("F3", user_id=42, chat_id=5), None)
    send_data = _last_callback_data(adapter, "send")
    await router.route(_make_callback_update("cb-3a", send_data), None)
    # A duplicate delivery with a NEW callback_id (router idempotency keys on id,
    # but the pending store has already popped the transcript).
    await router.route(_make_callback_update("cb-3b", send_data), None)

    assert adapter._queue.qsize() == 1


# ---------------------------------------------------------------------------
# Empty transcript → friendly glyph, no keyboard, nothing queued
# ---------------------------------------------------------------------------
async def test_empty_transcript_no_prompt_no_queue(db_pool: DbPool) -> None:
    adapter = _make_adapter()
    adapter.send_text = AsyncMock()
    handler, _ = await _wire(adapter, db_pool, _StubBackend(""))

    await handler.handle_voice(_make_voice_update("F4", user_id=42, chat_id=1), None)

    assert adapter._queue.qsize() == 0
    adapter.send_inline_keyboard.assert_not_called()
    adapter.send_text.assert_awaited()  # the "heard nothing" glyph


# ---------------------------------------------------------------------------
# Transcription error → friendly glyph, nothing queued
# ---------------------------------------------------------------------------
async def test_transcription_error_no_queue(db_pool: DbPool) -> None:
    adapter = _make_adapter()
    adapter.send_text = AsyncMock()
    handler, _ = await _wire(adapter, db_pool, _StubBackend(error="boom"))

    await handler.handle_voice(_make_voice_update("F5", user_id=42, chat_id=1), None)

    assert adapter._queue.qsize() == 0
    adapter.send_inline_keyboard.assert_not_called()
    adapter.send_text.assert_awaited()  # the error glyph


# ---------------------------------------------------------------------------
# Unauthorized sender → dropped (no download, no prompt, no queue)
# ---------------------------------------------------------------------------
async def test_unauthorized_sender_dropped(db_pool: DbPool) -> None:
    adapter = _make_adapter()
    handler, _ = await _wire(adapter, db_pool, _StubBackend("hi"))

    await handler.handle_voice(_make_voice_update("F6", user_id=999, chat_id=1), None)

    adapter.download_media.assert_not_called()
    assert adapter._queue.qsize() == 0
