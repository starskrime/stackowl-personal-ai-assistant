"""WhisperSttBackend — the local OSS speech-to-text engine.

Wraps the ``openai-whisper`` package (already a project dependency). Mirrors the
:class:`stackowl.media.tts.piper.PiperBackend` lazy-load idiom:

* the Whisper model is loaded LAZILY on the first transcribe call (never at
  startup) and held on the instance for reuse;
* transcription (CPU/GPU work) runs via ``run_in_executor`` so the event loop
  stays free during the seconds a Jetson may take;
* a model-load failure (e.g. torch/CUDA incompatible on an incapable host) is
  NEGATIVE-CACHED → ``is_available()`` reports False with a structured reason for
  the rest of the process, self-healing on restart, never a repeated crash.

is_local=True → the audio never leaves the box. Only the audio byte LENGTH is
logged, never the bytes themselves.

The transcriber body is lifted from the original
``channels/telegram/voice.py::WhisperLocalTranscriber`` so the proven path is
preserved; the only additions are the structured-error / availability contract.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.media.stt.base import SttAvailability, SttBackend, SttResult

__all__ = ["WhisperSttBackend"]

_DEFAULT_MODEL = "base"


class WhisperSttBackend(SttBackend):
    """Local OSS STT: lazy-load Whisper, transcribe off the event loop."""

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name = model_name or _DEFAULT_MODEL
        self._model: Any = None
        self._unavailable_reason: str | None = None
        log.tool.debug(
            "[stt.whisper] init",
            extra={"_fields": {"model_name": self._model_name}},
        )

    @property
    def name(self) -> str:
        return "whisper"

    @property
    def is_local(self) -> bool:
        return True

    # ------------------------------------------------------------- availability
    async def is_available(self) -> SttAvailability:
        """Lazily ensure the Whisper model can load. Never raises (B5)."""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._ensure_loaded)
        except Exception as exc:  # defense in depth — _ensure_loaded already catches.
            log.tool.error("[stt.whisper] is_available: unexpected failure", exc_info=exc)
            return SttAvailability.no(f"local STT engine unavailable: {type(exc).__name__}")

    def _ensure_loaded(self) -> SttAvailability:
        """Load the Whisper model (sync, executor). Negative-cache on failure."""
        if self._model is not None:
            return SttAvailability.ok()
        if self._unavailable_reason is not None:
            # Negative cache: a prior load failure persists for this process.
            # Self-heal on restart (a fresh process re-attempts the load).
            return SttAvailability.no(
                f"local STT engine could not initialize ({self._unavailable_reason})"
            )
        try:
            import whisper

            log.tool.debug(
                "[stt.whisper] _ensure_loaded: loading model",
                extra={"_fields": {"model_name": self._model_name}},
            )
            self._model = whisper.load_model(self._model_name)
            log.tool.info(
                "[stt.whisper] _ensure_loaded: model ready",
                extra={"_fields": {"model_name": self._model_name}},
            )
            return SttAvailability.ok()
        except Exception as exc:  # import/load failure → structured, never raise.
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            log.tool.error(
                "[stt.whisper] _ensure_loaded: failed — backend unavailable",
                exc_info=exc,
                extra={"_fields": {"model_name": self._model_name}},
            )
            return SttAvailability.no(
                f"local STT engine could not initialize ({self._unavailable_reason})"
            )

    # ------------------------------------------------------------- transcription
    async def transcribe(
        self, audio_bytes: bytes, *, audio_format: str = "ogg"
    ) -> SttResult | str:
        """Transcribe ``audio_bytes`` with the local Whisper model.

        4-point logging: entry / decision / step / exit. Returns an
        :class:`SttResult` (``text`` may be empty) on success, or a ``str`` reason
        on failure. Never raises for an operational error.
        """
        log.tool.debug(
            "[stt.whisper] transcribe: entry",
            extra={"_fields": {"audio_len": len(audio_bytes), "format": audio_format}},
        )
        # Guard FIRST so a test environment fails loud (a real model load would be
        # both slow and non-deterministic in tests).
        TestModeGuard.assert_not_test_mode("whisper.transcribe")

        suffix = f".{audio_format.lstrip('.')}" if audio_format else ".ogg"
        tmp_path: str | None = None
        try:
            log.tool.debug(
                "[stt.whisper] transcribe: decision write_tempfile",
                extra={"_fields": {"audio_len": len(audio_bytes), "suffix": suffix}},
            )
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(None, self._transcribe_sync, tmp_path)
        except Exception as exc:  # operational failure → structured str, never raise.
            log.tool.error("[stt.whisper] transcribe: failed", exc_info=exc)
            return f"transcription failed: {type(exc).__name__}: {exc}"
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError as exc:
                    log.tool.error(
                        "[stt.whisper] transcribe: tempfile cleanup failed",
                        exc_info=exc,
                    )

        log.tool.debug(
            "[stt.whisper] transcribe: exit",
            extra={"_fields": {"transcript_len": len(text)}},
        )
        return SttResult(text=text, backend=self.name, is_local=True)

    def _transcribe_sync(self, path: str) -> str:
        """Synchronous transcription — called via ``run_in_executor``."""
        avail = self._ensure_loaded()
        if not avail.available:
            # Surface the structured load failure to the async caller as an error.
            raise RuntimeError(avail.reason or "Whisper model unavailable")
        result: dict[str, Any] = self._model.transcribe(path)
        text: str = str(result.get("text", "")).strip()
        return text
