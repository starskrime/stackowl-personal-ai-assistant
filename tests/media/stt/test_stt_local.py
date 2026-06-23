"""WhisperSttBackend — local STT backend behavior without loading a real model.

Never loads the real Whisper model (slow + non-deterministic). Instead:

* the TestModeGuard makes ``transcribe`` raise TestModeViolation in tests — proving
  the guard is wired on the real path;
* a monkeypatched ``_transcribe_sync`` exercises the structured-result and
  empty-transcript success paths (empty text is SUCCESS, not an error);
* a forced load failure exercises the negative-cache availability contract.
"""

from __future__ import annotations

import pytest

from stackowl.config.test_mode import TestModeGuard, TestModeViolation
from stackowl.media.stt.base import SttResult
from stackowl.media.stt.local import WhisperSttBackend

pytestmark = pytest.mark.asyncio


async def test_transcribe_blocked_in_test_mode() -> None:
    # The real path guards against running a heavy model in tests.
    TestModeGuard.activate()
    try:
        backend = WhisperSttBackend()
        with pytest.raises(TestModeViolation):
            await backend.transcribe(b"\x00\x01", audio_format="ogg")
    finally:
        TestModeGuard.deactivate()


async def test_transcribe_returns_structured_result(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = WhisperSttBackend()
    # Bypass the test-mode guard and the real model: stub the sync transcribe.
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda op: None))
    monkeypatch.setattr(backend, "_transcribe_sync", lambda path: "hello world")
    result = await backend.transcribe(b"audio-bytes", audio_format="ogg")
    assert isinstance(result, SttResult)
    assert result.text == "hello world"
    assert result.backend == "whisper"
    assert result.is_local is True


async def test_empty_transcript_is_success_not_error(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = WhisperSttBackend()
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda op: None))
    monkeypatch.setattr(backend, "_transcribe_sync", lambda path: "")
    result = await backend.transcribe(b"silence", audio_format="ogg")
    # Empty text is a valid SUCCESS (heard nothing), NOT a str error.
    assert isinstance(result, SttResult)
    assert result.text == ""


async def test_transcribe_failure_returns_str_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = WhisperSttBackend()
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda op: None))

    def _boom(path: str) -> str:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(backend, "_transcribe_sync", _boom)
    result = await backend.transcribe(b"x", audio_format="ogg")
    assert isinstance(result, str)
    assert "transcription failed" in result


async def test_is_available_negative_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = WhisperSttBackend(model_name="base")

    calls = {"n": 0}

    def _fake_load_model(name: str) -> object:
        calls["n"] += 1
        raise RuntimeError("no torch")

    import sys
    import types

    fake_whisper = types.ModuleType("whisper")
    fake_whisper.load_model = _fake_load_model  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)

    first = await backend.is_available()
    second = await backend.is_available()
    assert first.available is False
    assert second.available is False
    assert "could not initialize" in (first.reason or "")
    # Negative cache: load is attempted once, not re-attempted on the second probe.
    assert calls["n"] == 1
