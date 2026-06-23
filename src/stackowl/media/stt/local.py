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
import contextlib
import io
import os
import shutil
import tempfile
import wave
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

        try:
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(
                None, self._transcribe_sync, audio_bytes, audio_format
            )
        except Exception as exc:  # operational failure → structured str, never raise.
            log.tool.error("[stt.whisper] transcribe: failed", exc_info=exc)
            return f"transcription failed: {type(exc).__name__}: {exc}"

        log.tool.debug(
            "[stt.whisper] transcribe: exit",
            extra={"_fields": {"transcript_len": len(text)}},
        )
        return SttResult(text=text, backend=self.name, is_local=True)

    def _transcribe_sync(self, audio_bytes: bytes, audio_format: str) -> str:
        """Synchronous decode + transcription — called via ``run_in_executor``."""
        avail = self._ensure_loaded()
        if not avail.available:
            # Surface the structured load failure to the async caller as an error.
            raise RuntimeError(avail.reason or "Whisper model unavailable")
        # Decode to a float32 waveform OURSELVES so WAV needs no ffmpeg (openai-
        # whisper's load_audio shells out to ffmpeg even for WAV). The TUI records
        # 16 kHz mono WAV via arecord, so this removes the ffmpeg dependency for
        # the dictation path entirely.
        audio = self._decode_audio(audio_bytes, audio_format)
        result: dict[str, Any] = self._model.transcribe(audio)
        text: str = str(result.get("text", "")).strip()
        return text

    def _decode_audio(self, audio_bytes: bytes, audio_format: str) -> Any:
        """Decode raw audio bytes to a 16 kHz mono float32 numpy array.

        WAV is decoded in-process (stdlib :mod:`wave` + numpy) — no ffmpeg. Other
        containers (OGG/Opus from Telegram, mp3, …) need a codec; we fall back to
        whisper's ffmpeg-based loader and raise an ACTIONABLE error when ffmpeg is
        absent rather than a cryptic FileNotFoundError.
        """
        fmt = audio_format.lstrip(".").lower()
        is_wav = fmt == "wav" or audio_bytes[:4] == b"RIFF"
        if is_wav:
            return self._decode_wav(audio_bytes)

        # Non-WAV container → needs ffmpeg (whisper.load_audio).
        if shutil.which("ffmpeg") is None:
            raise RuntimeError(
                f"cannot decode {fmt or 'this'} audio without ffmpeg "
                f"(install ffmpeg: 'sudo apt install ffmpeg')"
            )
        import whisper

        with tempfile.NamedTemporaryFile(suffix=f".{fmt or 'ogg'}", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        try:
            return whisper.load_audio(tmp_path)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

    @staticmethod
    def _decode_wav(audio_bytes: bytes) -> Any:
        """Decode a PCM WAV to a 16 kHz mono float32 numpy array (no ffmpeg)."""
        import numpy as np

        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())

        if sampwidth == 2:
            data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        elif sampwidth == 4:
            data = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif sampwidth == 1:  # 8-bit PCM is unsigned, centered at 128.
            data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            raise RuntimeError(f"unsupported WAV sample width: {sampwidth} bytes")

        if n_channels > 1:  # mix down to mono.
            data = data.reshape(-1, n_channels).mean(axis=1)
        if rate != 16000 and len(data) > 0:  # linear resample to whisper's 16 kHz.
            target = int(round(len(data) * 16000 / rate))
            if target > 0:
                x_old = np.linspace(0.0, 1.0, num=len(data), endpoint=False)
                x_new = np.linspace(0.0, 1.0, num=target, endpoint=False)
                data = np.interp(x_new, x_old, data).astype(np.float32)
        return np.ascontiguousarray(data, dtype=np.float32)
