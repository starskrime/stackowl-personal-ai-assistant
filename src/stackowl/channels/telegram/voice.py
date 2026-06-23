"""Voice transcription for the Telegram channel.

:class:`TelegramVoiceHandler` glues the PTB voice callback into the channel: it
downloads the voice file, transcribes it via the shared local-first STT selector
(:mod:`stackowl.media.stt`), and — instead of auto-injecting — shows the
transcript with a Send/Discard inline keyboard so the user CONFIRMS (or edits by
retyping) before it enters the pipeline. The confirm round-trip lives in
:mod:`stackowl.channels.telegram.voice_confirm`.

:class:`WhisperLocalTranscriber` is the original local Whisper wrapper. The
transcription logic now lives in :class:`stackowl.media.stt.WhisperSttBackend`
(shared with the TUI); this class is retained as a thin backward-compatible
shim so existing imports keep working.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from typing import TYPE_CHECKING, Any

from stackowl.channels.telegram.helpers import hash_user_id, is_authorized
from stackowl.channels.telegram.keyboard import InlineKeyboardBuilder
from stackowl.channels.telegram.voice_confirm import CALLBACK_PREFIX, PendingTranscriptStore
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.media.stt.base import SttResult, stt_error_key
from stackowl.tui.i18n import localize

if TYPE_CHECKING:
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter
    from stackowl.media.stt.selector import SttSelector

__all__ = [
    "TelegramVoiceHandler",
    "WhisperLocalTranscriber",
]


class WhisperLocalTranscriber:
    """Transcribes raw audio bytes using a locally-loaded Whisper model.

    The Whisper model is loaded lazily on the first :meth:`transcribe` call to
    avoid slowing startup. The model is stored on the instance and reused for
    all subsequent calls.
    """

    def __init__(self, model_name: str = "base") -> None:
        self._model_name = model_name
        self._model: Any = None
        log.telegram.debug(
            "[telegram] voice.transcriber.init: entry",
            extra={"_fields": {"model_name": model_name}},
        )

    def _load_model(self) -> None:
        """Load the Whisper model if not already loaded."""
        if self._model is None:
            log.telegram.debug(
                "[telegram] voice.transcriber._load_model: loading Whisper model",
                extra={"_fields": {"model_name": self._model_name}},
            )
            import whisper

            self._model = whisper.load_model(self._model_name)
            log.telegram.debug(
                "[telegram] voice.transcriber._load_model: model loaded",
                extra={"_fields": {"model_name": self._model_name}},
            )

    async def transcribe(self, audio_bytes: bytes) -> str:
        """Transcribe ``audio_bytes`` to text using the local Whisper model.

        4-point logging: entry / decision / step / exit.

        Args:
            audio_bytes: Raw audio content (OGG, WAV, MP3, …).

        Returns:
            The transcribed text string, stripped of leading/trailing whitespace.

        Raises:
            TestModeViolation: When called from a test environment.
        """
        log.telegram.debug(
            "[telegram] voice.transcriber.transcribe: entry",
            extra={"_fields": {"audio_len": len(audio_bytes)}},
        )
        TestModeGuard.assert_not_test_mode("whisper.transcribe")

        log.telegram.debug(
            "[telegram] voice.transcriber.transcribe: decision write_tempfile",
            extra={"_fields": {"audio_len": len(audio_bytes)}},
        )

        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            log.telegram.debug(
                "[telegram] voice.transcriber.transcribe: step file_written",
                extra={"_fields": {"tmp_path_len": len(tmp_path)}},
            )

            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(
                None, self._transcribe_sync, tmp_path
            )
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError as exc:
                    log.telegram.error(
                        "[telegram] voice.transcriber.transcribe: tempfile cleanup failed",
                        exc,
                        extra={"_fields": {"tmp_path_len": len(tmp_path) if tmp_path else 0}},
                    )

        log.telegram.debug(
            "[telegram] voice.transcriber.transcribe: exit",
            extra={"_fields": {"transcript_len": len(transcript)}},
        )
        return transcript

    def _transcribe_sync(self, path: str) -> str:
        """Synchronous transcription — called via ``run_in_executor``.

        4-point logging: entry / decision / step / exit.

        Args:
            path: Path to the temporary audio file.

        Returns:
            Stripped transcription text.
        """
        log.telegram.debug(
            "[telegram] voice.transcriber._transcribe_sync: entry",
            extra={"_fields": {"path_len": len(path)}},
        )
        self._load_model()
        log.telegram.debug(
            "[telegram] voice.transcriber._transcribe_sync: decision model_ready",
            extra={"_fields": {"model_name": self._model_name}},
        )
        result: dict[str, Any] = self._model.transcribe(path)
        log.telegram.debug(
            "[telegram] voice.transcriber._transcribe_sync: step transcribe_called",
            extra={"_fields": {}},
        )
        text: str = result["text"].strip()
        log.telegram.debug(
            "[telegram] voice.transcriber._transcribe_sync: exit",
            extra={"_fields": {"text_len": len(text)}},
        )
        return text


class TelegramVoiceHandler:
    """Transcribes Telegram voice messages and asks the user to confirm.

    Registered as a PTB callback on ``filters.VOICE``. On each voice message it:
    1. authorizes the sender;
    2. downloads the voice file via the adapter;
    3. transcribes the audio via the shared local-first STT selector;
    4. stashes the transcript and replies with a Send/Discard inline keyboard —
       it does NOT enqueue anything. The actual injection happens only when the
       user taps Send (handled by :class:`VoiceConfirmHandler`).

    Operational failures and an empty/unintelligible transcript reply with a
    language-neutral glyph and inject nothing.
    """

    # Language-neutral glyph replies (the platform is multilingual): no English
    # copy needed — a glyph conveys the outcome. Mirrors the consent gate's
    # glyph-first philosophy and the old platform's "🔇"/"❌" voice replies.
    _EMPTY_GLYPH = "🔇"  # heard nothing intelligible
    _ERROR_GLYPH = "❌"  # download / transcription failed

    def __init__(
        self,
        selector: SttSelector,
        adapter: TelegramChannelAdapter,
        pending_store: PendingTranscriptStore,
    ) -> None:
        self._selector = selector
        self._adapter = adapter
        self._pending = pending_store
        log.telegram.debug("[telegram] voice.handler.init: entry")

    async def handle_voice(self, update: Any, context: Any) -> None:
        """PTB callback for ``filters.VOICE`` messages.

        4-point logging: entry / decision / step / exit.
        """
        log.telegram.debug("[telegram] voice.handler.handle_voice: entry")

        message = update.effective_message
        if message is None or message.voice is None:
            log.telegram.debug(
                "[telegram] voice.handler.handle_voice: no voice message — skip"
            )
            return

        file_id: str = message.voice.file_id
        user = update.effective_user
        user_id = int(user.id) if user is not None else 0
        user_hash = hash_user_id(user_id)

        if not is_authorized(user_id, self._adapter._settings.allowed_user_ids):
            log.telegram.warning(
                "[telegram] voice.handler.handle_voice: unauthorized drop",
                extra={"_fields": {"user_hash": user_hash}},
            )
            return

        chat = update.effective_chat
        chat_id = int(chat.id) if chat is not None else 0
        # A voice note that REPLIES to one of the bot's messages is a STEER signal,
        # mirroring the text path (adapter._handle_update).
        is_reply = bool(getattr(message, "reply_to_message", None))

        log.telegram.debug(
            "[telegram] voice.handler.handle_voice: decision download_and_transcribe",
            extra={"_fields": {"user_hash": user_hash}},
        )

        # Show a typing indicator while we download + transcribe (ephemeral, no
        # leftover message to clean up). Best-effort liveness cue.
        with contextlib.suppress(Exception):
            await self._adapter.send_typing(chat_id)

        try:
            audio_bytes = await self._adapter.download_media(file_id)
        except Exception as exc:
            log.telegram.error(
                "[telegram] voice.handler.handle_voice: download_media failed",
                exc,
                extra={"_fields": {"user_hash": user_hash}},
            )
            await self._reply(chat_id, self._ERROR_GLYPH)
            return

        selection = await self._selector.select()
        if not selection.available or selection.backend is None:
            log.telegram.warning(
                "[telegram] voice.handler.handle_voice: stt unavailable",
                extra={"_fields": {"reason": selection.reason}},
            )
            await self._reply(chat_id, localize(stt_error_key(selection.reason)))
            return

        result = await selection.backend.transcribe(audio_bytes, audio_format="ogg")
        if isinstance(result, str):
            # Operational failure surfaced as a structured reason — tell the user
            # WHY (e.g. ffmpeg missing for OGG) instead of a bare ❌, inject nothing.
            log.telegram.error(
                "[telegram] voice.handler.handle_voice: transcription failed",
                extra={"_fields": {"reason": result}},
            )
            await self._reply(chat_id, localize(stt_error_key(result)))
            return

        transcript = result.text if isinstance(result, SttResult) else ""
        if not transcript.strip():
            log.telegram.debug(
                "[telegram] voice.handler.handle_voice: empty transcript — heard nothing"
            )
            await self._reply(chat_id, self._EMPTY_GLYPH)
            return

        # Stash the transcript and present it for confirmation. NOTHING is enqueued
        # here — only a Send tap injects it (VoiceConfirmHandler).
        rid = self._pending.add(
            chat_id=chat_id,
            session_id=str(user_id),
            transcript=transcript,
            is_reply=is_reply,
        )
        keyboard = (
            InlineKeyboardBuilder()
            .add_button("✅", f"{CALLBACK_PREFIX}:send:{rid}")
            .add_button("🗑", f"{CALLBACK_PREFIX}:discard:{rid}")
            .build()
        )
        try:
            # parse_mode=None: a raw transcript may contain markdown-breaking chars
            # (same reason the consent gate sends plain text).
            sent = await self._adapter.send_inline_keyboard(
                f"🎤 {transcript}", keyboard, chat_id=chat_id, parse_mode=None
            )
            self._pending.set_message_id(rid, getattr(sent, "message_id", None))
        except Exception as exc:
            # The prompt could not be delivered — drop the pending entry so it can't
            # leak, and surface the failure.
            self._pending.pop(rid)
            log.telegram.error(
                "[telegram] voice.handler.handle_voice: confirm prompt send failed",
                exc,
                extra={"_fields": {"user_hash": user_hash}},
            )
            await self._reply(chat_id, self._ERROR_GLYPH)
            return

        log.telegram.debug(
            "[telegram] voice.handler.handle_voice: exit",
            extra={"_fields": {"user_hash": user_hash, "rid": rid, "text_len": len(transcript)}},
        )

    async def _reply(self, chat_id: int, text: str) -> None:
        """Best-effort plain reply (glyph status) — never raises into the callback."""
        try:
            await self._adapter.send_text(text, chat_id=chat_id)
        except Exception as exc:  # noqa: BLE001 — a status reply must not crash the handler.
            log.telegram.error(
                "[telegram] voice.handler._reply: send failed",
                exc_info=exc,
                extra={"_fields": {"chat_id": chat_id}},
            )
