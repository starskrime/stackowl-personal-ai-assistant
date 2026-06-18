"""Voice transcription for the Telegram channel.

:class:`WhisperLocalTranscriber` wraps the ``openai-whisper`` package to
transcribe audio bytes (OGG from Telegram voice messages) using a
locally-loaded Whisper model.

:class:`TelegramVoiceHandler` glues the PTB callback into the adapter's
ingress queue: it downloads the voice file, transcribes it, and enqueues the
resulting text as an :class:`IngressMessage`.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from stackowl.channels.telegram.helpers import hash_user_id, is_authorized
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import IngressMessage
from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter

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
            import whisper  # type: ignore[import]

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
    """Handles Telegram voice messages by transcribing and enqueuing them.

    The handler is registered as a PTB callback. On each voice message it:
    1. Downloads the voice file via the adapter.
    2. Transcribes the audio.
    3. Enqueues an :class:`IngressMessage` identical to a text message.
    """

    def __init__(
        self,
        transcriber: WhisperLocalTranscriber,
        adapter: "TelegramChannelAdapter",
    ) -> None:
        self._transcriber = transcriber
        self._adapter = adapter
        log.telegram.debug("[telegram] voice.handler.init: entry")

    async def handle_voice(self, update: Any, context: Any) -> None:
        """PTB callback for Filters.VOICE messages.

        4-point logging: entry / decision / step / exit.

        Args:
            update: python-telegram-bot ``Update`` object.
            context: python-telegram-bot ``ContextTypes.DEFAULT_TYPE``.
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

        log.telegram.debug(
            "[telegram] voice.handler.handle_voice: decision download_and_transcribe",
            extra={"_fields": {"user_hash": user_hash}},
        )

        try:
            audio_bytes = await self._adapter.download_media(file_id)
        except Exception as exc:
            log.telegram.error(
                "[telegram] voice.handler.handle_voice: download_media failed",
                exc,
                extra={"_fields": {"user_hash": user_hash}},
            )
            return

        try:
            transcript = await self._transcriber.transcribe(audio_bytes)
        except Exception as exc:
            log.telegram.error(
                "[telegram] voice.handler.handle_voice: transcribe failed",
                exc,
                extra={"_fields": {"user_hash": user_hash}},
            )
            return

        if not transcript:
            log.telegram.debug(
                "[telegram] voice.handler.handle_voice: empty transcript — skip"
            )
            return

        chat = update.effective_chat
        chat_id = int(chat.id) if chat is not None else 0

        ingress = IngressMessage(
            text=transcript,
            session_id=str(user_id),
            channel=self._adapter.channel_name,
            trace_id=uuid4().hex,
        )
        self._adapter._queue.put_nowait(ingress)
        self._adapter._last_chat_id = chat_id

        log.telegram.debug(
            "[telegram] voice.handler.handle_voice: exit",
            extra={"_fields": {"user_hash": user_hash, "trace_id": ingress.trace_id}},
        )
