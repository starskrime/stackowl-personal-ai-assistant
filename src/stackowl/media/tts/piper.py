"""PiperBackend — the local OSS text-to-speech engine (E10-S3).

Mirrors :class:`WhisperLocalTranscriber` (the established lazy-load idiom):

* the heavy synthesis library + the ONNX voice model are loaded LAZILY on the
  first synthesize call (never at startup) and held on the instance for reuse;
* the heavy pip package is AUTO-INSTALLED at first use ([[feedback_agent_auto_install]]),
  and the configured ONNX voice is downloaded into ``models_dir()/piper/`` —
  both bounded, logged, and self-healing (a failure → ``is_available()`` False
  with a structured reason, never a crash);
* synthesis (CPU work) runs via ``run_in_executor`` so the event loop stays free.

The engine is CPU/ARM-friendly so it runs on every host (incl. the Jetson dev
box). It writes a WAV into ``media_dir()/tts/`` and returns the PATH — never raw
bytes. is_local=True → no egress.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path
from typing import Any
from uuid import uuid4

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.media.tts.base import TtsAvailability, TtsBackend, TtsResult
from stackowl.paths import StackowlHome

__all__ = ["PiperBackend"]

# The local OSS TTS engine's pip package id (a real dependency, not a vendor
# attribution in logic) and the default CPU/ARM-friendly voice model. The voice
# files (a ``.onnx`` + its ``.onnx.json`` config) are fetched on first use into
# the durable models dir. The base URL is the project's published voice host;
# it is overridable via the engine's settings for an air-gapped/self-hosted host.
_PIP_PACKAGE = "piper-tts"
_DEFAULT_VOICE = "en_US-lessac-medium"
_VOICE_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
# Map a voice id → its path segment on the voice host (lang/region/name/quality).
_VOICE_PATHS: dict[str, str] = {
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium",
}
_INSTALL_TIMEOUT_S = 600
_DOWNLOAD_TIMEOUT_S = 600
# ONNX voices are ~20–80 MiB; cap the streamed download well above that so a
# misbehaving/oversized response can't buffer unbounded into memory.
_MAX_VOICE_BYTES = 200 * 1024 * 1024
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class PiperBackend(TtsBackend):
    """Local OSS TTS: lazy-load + auto-install + voice download, off the loop."""

    def __init__(
        self,
        *,
        voice: str = _DEFAULT_VOICE,
        voice_base_url: str = _VOICE_BASE_URL,
    ) -> None:
        self._voice = voice or _DEFAULT_VOICE
        self._voice_base_url = (voice_base_url or _VOICE_BASE_URL).rstrip("/")
        self._loaded: Any = None  # the synthesis voice object, lazily loaded
        self._unavailable_reason: str | None = None
        log.tool.debug(
            "[tts.piper] init",
            extra={"_fields": {"voice": self._voice}},
        )

    @property
    def name(self) -> str:
        return "piper"

    @property
    def is_local(self) -> bool:
        return True

    # ------------------------------------------------------------- availability
    async def is_available(self, voice: str | None = None) -> TtsAvailability:
        """Lazily ensure the engine + voice are ready. Never raises (B5)."""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._ensure_loaded, voice or self._voice)
        except Exception as exc:  # defense in depth — _ensure_loaded already catches.
            log.tool.error("[tts.piper] is_available: unexpected failure", exc_info=exc)
            return TtsAvailability.no(f"local TTS engine unavailable: {type(exc).__name__}")

    def _voices_dir(self) -> Path:
        return StackowlHome.models_dir() / "piper"

    def _ensure_loaded(self, voice: str) -> TtsAvailability:
        """Install the engine + download the voice + load it. Sync (executor)."""
        if self._loaded is not None:
            return TtsAvailability.ok()
        if self._unavailable_reason is not None:
            # Negative cache: a prior install/download/load failure persists for
            # this process. Self-heal on restart (a fresh process re-attempts).
            return TtsAvailability.no(
                f"local TTS engine could not initialize ({self._unavailable_reason})"
            )
        try:
            voice_obj = self._import_engine()
            onnx_path = self._ensure_voice_files(voice)
            log.tool.debug(
                "[tts.piper] _ensure_loaded: loading voice",
                extra={"_fields": {"voice": voice}},
            )
            self._loaded = voice_obj.load(str(onnx_path))
            log.tool.info(
                "[tts.piper] _ensure_loaded: voice ready",
                extra={"_fields": {"voice": voice}},
            )
            return TtsAvailability.ok()
        except Exception as exc:  # install/download/load failure → structured, never raise.
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            log.tool.error(
                "[tts.piper] _ensure_loaded: failed — backend unavailable",
                exc_info=exc,
                extra={"_fields": {"voice": voice}},
            )
            return TtsAvailability.no(
                f"local TTS engine could not initialize ({self._unavailable_reason})"
            )

    def _import_engine(self) -> Any:
        """Import the synthesis voice class, auto-installing the package once."""
        try:
            from piper.voice import PiperVoice

            return PiperVoice
        except ImportError:
            # No live pip-install (or any heavy/live work) in test mode — the
            # TestModeViolation propagates into _ensure_loaded's broad except and
            # is reported as a clean structured-unavailable (no shell-out).
            TestModeGuard.assert_not_test_mode("tts.piper.install")
            log.tool.info(
                "[tts.piper] _import_engine: package missing — auto-installing",
                extra={"_fields": {"package": _PIP_PACKAGE}},
            )
            subprocess.run(  # noqa: S603 — fixed argv, no shell.
                [sys.executable, "-m", "pip", "install", _PIP_PACKAGE],
                check=True,
                capture_output=True,
                timeout=_INSTALL_TIMEOUT_S,
            )
            from piper.voice import PiperVoice

            return PiperVoice

    def _ensure_voice_files(self, voice: str) -> Path:
        """Download the ONNX voice + its config on first use; return the .onnx path."""
        segment = _VOICE_PATHS.get(voice)
        if segment is None:
            raise ValueError(
                f"unknown voice '{voice}' — configure a known voice id "
                f"(e.g. '{_DEFAULT_VOICE}')"
            )
        voices_dir = self._voices_dir()
        voices_dir.mkdir(parents=True, exist_ok=True)
        onnx_path = voices_dir / f"{voice}.onnx"
        config_path = voices_dir / f"{voice}.onnx.json"
        for path, suffix in ((onnx_path, ".onnx"), (config_path, ".onnx.json")):
            if path.exists() and path.stat().st_size > 0:
                continue
            url = f"{self._voice_base_url}/{segment}{suffix}"
            log.tool.info(
                "[tts.piper] _ensure_voice_files: downloading voice asset",
                extra={"_fields": {"voice": voice, "asset": suffix}},
            )
            self._download(url, path)
        return onnx_path

    @staticmethod
    def _download(url: str, dest: Path) -> None:
        """Download ``url`` → ``dest`` atomically (temp + rename), size-bounded.

        Streams in chunks and refuses past ``_MAX_VOICE_BYTES`` so a misbehaving
        or oversized response can't buffer unbounded; cleans up the ``.part`` on
        any failure so the voices dir isn't littered (B5: structured, no crash).
        """
        # No network egress in test mode — the violation propagates up to
        # _ensure_loaded's broad except and is reported structured-unavailable.
        TestModeGuard.assert_not_test_mode("tts.piper.download")
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with urllib.request.urlopen(  # noqa: S310 — fixed http(s) voice host.
                url, timeout=_DOWNLOAD_TIMEOUT_S
            ) as resp:
                declared = resp.headers.get("Content-Length")
                if declared is not None and declared.isdigit() and int(declared) > _MAX_VOICE_BYTES:
                    raise ValueError(
                        f"voice download too large: Content-Length {declared} "
                        f"exceeds cap {_MAX_VOICE_BYTES}"
                    )
                written = 0
                with tmp.open("wb") as fh:
                    while True:
                        chunk = resp.read(_DOWNLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > _MAX_VOICE_BYTES:
                            raise ValueError(
                                f"voice download exceeded cap {_MAX_VOICE_BYTES} bytes"
                            )
                        fh.write(chunk)
            tmp.replace(dest)
        except BaseException:
            # Best-effort cleanup of the partial temp on any failure path.
            try:
                tmp.unlink(missing_ok=True)
            except OSError as exc:
                log.tool.error(
                    "[tts.piper] _download: temp cleanup failed", exc_info=exc
                )
            raise

    # --------------------------------------------------------------- synthesize
    async def synthesize(self, text: str, *, voice: str | None) -> TtsResult | str:
        """Synthesize ``text`` to a WAV under ``media_dir()/tts/``. Never raises (B5)."""
        t0 = time.monotonic()
        use_voice = voice or self._voice
        log.tool.debug(
            "[tts.piper] synthesize: entry",
            extra={"_fields": {"text_len": len(text), "voice": use_voice}},
        )
        avail = await self.is_available(use_voice)
        if not avail.available:
            return avail.reason or "local TTS engine unavailable"

        out_dir = StackowlHome.media_dir() / "tts"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"tts_{uuid4().hex}.wav"
        loop = asyncio.get_event_loop()
        try:
            duration_ms = await loop.run_in_executor(
                None, self._synth_sync, text, out_path
            )
        except Exception as exc:  # synthesis failure → structured, never raise (B5).
            log.tool.error(
                "[tts.piper] synthesize: synthesis failed",
                exc_info=exc,
                extra={"_fields": {"text_len": len(text), "voice": use_voice}},
            )
            return f"local TTS synthesis failed: {type(exc).__name__}: {exc}"

        elapsed_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "[tts.piper] synthesize: exit",
            extra={"_fields": {
                "voice": use_voice, "audio_ms": duration_ms, "wall_ms": elapsed_ms,
            }},
        )
        return TtsResult(
            path=str(out_path),
            duration_ms=duration_ms,
            voice=use_voice,
            backend=self.name,
            is_local=True,
        )

    def _synth_sync(self, text: str, out_path: Path) -> float:
        """Synchronous synthesis — called via run_in_executor. Returns audio ms."""
        log.tool.debug(
            "[tts.piper] _synth_sync: entry",
            extra={"_fields": {"text_len": len(text)}},
        )
        with wave.open(str(out_path), "wb") as wav:
            self._loaded.synthesize(text, wav)
        with wave.open(str(out_path), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate() or 1
        audio_ms = (frames / rate) * 1000
        log.tool.debug(
            "[tts.piper] _synth_sync: exit",
            extra={"_fields": {"audio_ms": audio_ms}},
        )
        return audio_ms
