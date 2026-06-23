"""Microphone capture for TUI push-to-talk dictation.

A :class:`MicRecorder` records a short clip and returns raw WAV bytes that the
shared STT selector can transcribe. :class:`ShellMicRecorder` shells out to a
system capture tool (``arecord`` on ALSA, else ``ffmpeg``) detected via
``shutil.which`` — chosen over a native binding (``sounddevice``/``pyaudio``) so
there is NO new Python dependency and NO PortAudio build on a Jetson/ARM host.

Headless-friendly + fail-safe: when neither tool is present (e.g. a box reached
over SSH with no audio device) :meth:`is_available` returns False and the TUI
binding degrades to a status line — it NEVER raises into the UI. A capture or
read failure yields empty bytes, logged, never an exception.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import tempfile
from typing import Protocol, runtime_checkable

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log

__all__ = ["MicRecorder", "ShellMicRecorder"]

# 16 kHz mono 16-bit PCM — what Whisper wants, and small.
_SAMPLE_RATE = "16000"


@runtime_checkable
class MicRecorder(Protocol):
    """Push-to-talk recorder: start capture, then stop and collect WAV bytes."""

    def is_available(self) -> bool:
        """True when a capture mechanism exists on this host. Never raises."""
        ...

    async def start(self) -> bool:
        """Begin capture. Returns True if recording actually started."""
        ...

    async def stop(self) -> bytes:
        """Stop capture and return the recorded WAV bytes (``b""`` on failure)."""
        ...


class ShellMicRecorder:
    """Records via a detected system tool (``arecord`` preferred, else ``ffmpeg``)."""

    def __init__(self) -> None:
        # Detect once; a missing tool → is_available() False (headless-safe).
        self._arecord = shutil.which("arecord")
        self._ffmpeg = shutil.which("ffmpeg") if self._arecord is None else None
        self._proc: asyncio.subprocess.Process | None = None
        self._wav_path: str | None = None
        log.tui.debug(
            "[tui] voice.recorder.init",
            extra={"_fields": {"arecord": bool(self._arecord), "ffmpeg": bool(self._ffmpeg)}},
        )

    def is_available(self) -> bool:
        return self._arecord is not None or self._ffmpeg is not None

    def _build_cmd(self, wav_path: str) -> list[str] | None:
        """Build the capture argv for the detected tool (16 kHz mono WAV)."""
        if self._arecord is not None:
            return [self._arecord, "-q", "-f", "S16_LE", "-r", _SAMPLE_RATE, "-c", "1", wav_path]
        if self._ffmpeg is not None:
            # Default ALSA input → 16 kHz mono WAV; -y overwrites the temp file.
            return [
                self._ffmpeg, "-y", "-loglevel", "error",
                "-f", "alsa", "-i", "default",
                "-ar", _SAMPLE_RATE, "-ac", "1", wav_path,
            ]
        return None

    async def start(self) -> bool:
        """Spawn the capture process writing to a temp WAV. Never raises."""
        log.tui.debug("[tui] voice.recorder.start: entry")
        TestModeGuard.assert_not_test_mode("mic.record")
        if self._proc is not None:
            log.tui.warning("[tui] voice.recorder.start: already recording")
            return True
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            self._wav_path = tmp.name
        cmd = self._build_cmd(self._wav_path)
        if cmd is None:
            log.tui.warning("[tui] voice.recorder.start: no capture tool available")
            return False
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception as exc:  # spawn failure → degrade, never crash the UI.
            log.tui.error("[tui] voice.recorder.start: spawn failed", exc_info=exc)
            self._proc = None
            self._cleanup_file()
            return False
        log.tui.debug("[tui] voice.recorder.start: recording")
        return True

    async def stop(self) -> bytes:
        """Stop the capture process and read back the WAV bytes. Never raises."""
        log.tui.debug("[tui] voice.recorder.stop: entry")
        proc, wav_path = self._proc, self._wav_path
        self._proc = None
        if proc is None or wav_path is None:
            self._cleanup_file()
            return b""
        # arecord/ffmpeg flush + finalize the WAV header on SIGINT/terminate.
        try:
            proc.send_signal(signal.SIGINT)
        except Exception:  # noqa: BLE001 — fall back to terminate below.
            with contextlib.suppress(Exception):
                proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (TimeoutError, Exception):  # noqa: BLE001
            with contextlib.suppress(Exception):
                proc.kill()
                await proc.wait()
        data = b""
        try:
            with open(wav_path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            log.tui.error("[tui] voice.recorder.stop: read failed", exc_info=exc)
        finally:
            self._wav_path = wav_path
            self._cleanup_file()
        log.tui.debug(
            "[tui] voice.recorder.stop: exit",
            extra={"_fields": {"audio_len": len(data)}},
        )
        return data

    def _cleanup_file(self) -> None:
        path, self._wav_path = self._wav_path, None
        if path is not None:
            with contextlib.suppress(OSError):
                os.unlink(path)
