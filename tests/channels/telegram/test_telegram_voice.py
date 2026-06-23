"""Tests for voice transcription and InlineKeyboardBuilder.

Covers:
1. WhisperLocalTranscriber.transcribe raises TestModeViolation in test mode
2. TelegramVoiceHandler.handle_voice calls download_media with file_id
3. TelegramVoiceHandler passes transcribed text to queue
4. WhisperLocalTranscriber lazy loads model (model is None before first transcribe)
5. InlineKeyboardBuilder add_button raises ValueError if callback_data > 64 chars
6. InlineKeyboardBuilder.build() returns correct structure
7. InlineKeyboardBuilder.from_memory_fact returns inline_keyboard with 2 buttons
8. InlineKeyboardBuilder chaining (multiple add_button calls work)
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.channels.telegram.keyboard import InlineKeyboardBuilder
from stackowl.channels.telegram.voice import TelegramVoiceHandler, WhisperLocalTranscriber
from stackowl.channels.telegram.voice_confirm import PendingTranscriptStore
from stackowl.config.settings import TranscriptionSettings
from stackowl.config.test_mode import TestModeGuard, TestModeViolation
from stackowl.media.stt.base import SttAvailability, SttBackend, SttResult
from stackowl.media.stt.selector import SttSelector


class _StubBackend(SttBackend):
    """A deterministic STT backend — returns a fixed transcript, no model load."""

    def __init__(self, text: str) -> None:
        self._text = text

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
        return SttResult(text=self._text, backend="stub", is_local=True)


def _make_handler(adapter: Any, text: str) -> tuple[TelegramVoiceHandler, PendingTranscriptStore]:
    selector = SttSelector(TranscriptionSettings(enabled=True), local=_StubBackend(text))
    store = PendingTranscriptStore()
    return TelegramVoiceHandler(selector, adapter, store), store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_voice_update(file_id: str, user_id: int, chat_id: int = 100) -> Any:
    """Build a duck-typed Update-like object with a voice message."""
    voice = types.SimpleNamespace(file_id=file_id)
    message = types.SimpleNamespace(voice=voice, text=None)
    user = types.SimpleNamespace(id=user_id)
    chat = types.SimpleNamespace(id=chat_id)
    return types.SimpleNamespace(
        effective_message=message,
        effective_user=user,
        effective_chat=chat,
    )


def _make_adapter(allowed: frozenset[int] | None = None) -> Any:
    """Build a minimal mock adapter."""
    from stackowl.channels.telegram.settings import TelegramSettings

    settings = TelegramSettings(
        bot_token="test_token_x" * 3,
        allowed_user_ids=allowed if allowed is not None else frozenset({42}),
    )
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter

    return TelegramChannelAdapter(settings)


# ---------------------------------------------------------------------------
# 1. WhisperLocalTranscriber raises TestModeViolation in test mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcriber_raises_in_test_mode() -> None:
    """transcribe() must raise TestModeViolation when test mode is active."""
    transcriber = WhisperLocalTranscriber(model_name="base")
    TestModeGuard.activate()
    try:
        with pytest.raises(TestModeViolation):
            await transcriber.transcribe(b"fake audio")
    finally:
        TestModeGuard.deactivate()


# ---------------------------------------------------------------------------
# 2. TelegramVoiceHandler.handle_voice calls download_media with file_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_voice_calls_download_media_with_file_id() -> None:
    """handle_voice must call adapter.download_media with the voice's file_id."""
    adapter = _make_adapter(allowed=frozenset({42}))
    adapter.download_media = AsyncMock(return_value=b"audio")
    adapter.send_typing = AsyncMock()
    adapter.send_inline_keyboard = AsyncMock(return_value=None)

    handler, _ = _make_handler(adapter, "hello world")
    update = _make_voice_update(file_id="FILE_ID_123", user_id=42)

    await handler.handle_voice(update, None)

    adapter.download_media.assert_called_once_with("FILE_ID_123")


# ---------------------------------------------------------------------------
# 3. TelegramVoiceHandler shows a confirm prompt and does NOT auto-enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_voice_shows_confirm_and_does_not_enqueue() -> None:
    """handle_voice must present a Send/Discard prompt, enqueuing NOTHING yet."""
    adapter = _make_adapter(allowed=frozenset({42}))
    adapter.download_media = AsyncMock(return_value=b"audio bytes")
    adapter.send_typing = AsyncMock()
    adapter.send_inline_keyboard = AsyncMock(return_value=None)

    handler, _ = _make_handler(adapter, "transcribed speech")
    update = _make_voice_update(file_id="F1", user_id=42, chat_id=999)

    await handler.handle_voice(update, None)

    # Confirm-before-send: nothing is injected until the user taps Send.
    assert adapter._queue.qsize() == 0
    adapter.send_inline_keyboard.assert_called_once()
    # The prompt carries the transcript and a two-button keyboard.
    call = adapter.send_inline_keyboard.call_args
    assert "transcribed speech" in call.args[0]
    keyboard = call.args[1]
    buttons = [btn for row in keyboard["inline_keyboard"] for btn in row]
    actions = {btn["callback_data"].split(":")[1] for btn in buttons}
    assert actions == {"send", "discard"}


# ---------------------------------------------------------------------------
# 4. WhisperLocalTranscriber lazy loads model (model is None before first call)
# ---------------------------------------------------------------------------


def test_transcriber_model_is_none_before_first_use() -> None:
    """_model must be None before any transcribe call (lazy loading)."""
    transcriber = WhisperLocalTranscriber(model_name="tiny")
    assert transcriber._model is None


# ---------------------------------------------------------------------------
# 5. InlineKeyboardBuilder raises ValueError if callback_data > 64 chars
# ---------------------------------------------------------------------------


def test_add_button_raises_for_long_callback_data() -> None:
    """add_button must raise ValueError when callback_data exceeds 64 characters."""
    builder = InlineKeyboardBuilder()
    too_long = "x" * 65
    with pytest.raises(ValueError, match="64"):
        builder.add_button("Label", too_long)


# ---------------------------------------------------------------------------
# 6. InlineKeyboardBuilder.build() returns correct structure
# ---------------------------------------------------------------------------


def test_build_returns_correct_structure() -> None:
    """build() must return a dict with 'inline_keyboard' key and proper nesting."""
    result = (
        InlineKeyboardBuilder()
        .add_button("Yes", "action:yes")
        .add_button("No", "action:no")
        .build()
    )
    assert "inline_keyboard" in result
    kb = result["inline_keyboard"]
    assert isinstance(kb, list)
    assert len(kb) == 1  # one row
    row = kb[0]
    assert len(row) == 2
    assert row[0] == {"text": "Yes", "callback_data": "action:yes"}
    assert row[1] == {"text": "No", "callback_data": "action:no"}


# ---------------------------------------------------------------------------
# 7. InlineKeyboardBuilder.from_memory_fact returns keyboard with 2 buttons
# ---------------------------------------------------------------------------


def test_from_memory_fact_returns_two_buttons() -> None:
    """from_memory_fact must produce an inline_keyboard with exactly 2 buttons."""
    result = InlineKeyboardBuilder.from_memory_fact("fact-abc")
    assert "inline_keyboard" in result
    kb = result["inline_keyboard"]
    # At least one row, at least 2 buttons total.
    buttons = [btn for row in kb for btn in row]
    assert len(buttons) == 2
    callback_values = {btn["callback_data"] for btn in buttons}
    assert "mem:approve:fact-abc" in callback_values
    assert "mem:reject:fact-abc" in callback_values


# ---------------------------------------------------------------------------
# 8. InlineKeyboardBuilder chaining (multiple add_button calls)
# ---------------------------------------------------------------------------


def test_builder_chaining_multiple_buttons() -> None:
    """Multiple chained add_button calls must all appear in the final keyboard."""
    result = (
        InlineKeyboardBuilder()
        .add_button("A", "cb:a")
        .add_button("B", "cb:b")
        .add_button("C", "cb:c")
        .build()
    )
    kb = result["inline_keyboard"]
    row = kb[0]
    assert len(row) == 3
    texts = [btn["text"] for btn in row]
    assert texts == ["A", "B", "C"]
