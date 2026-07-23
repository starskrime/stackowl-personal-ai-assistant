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
from stackowl.media.stt.base import SttAvailability, SttResult
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
    monkeypatch.setattr(
        backend, "_transcribe_sync", lambda audio_bytes, audio_format: "hello world"
    )
    result = await backend.transcribe(b"audio-bytes", audio_format="ogg")
    assert isinstance(result, SttResult)
    assert result.text == "hello world"
    assert result.backend == "whisper"
    assert result.is_local is True


async def test_empty_transcript_is_success_not_error(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = WhisperSttBackend()
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda op: None))
    monkeypatch.setattr(
        backend, "_transcribe_sync", lambda audio_bytes, audio_format: ""
    )
    result = await backend.transcribe(b"silence", audio_format="ogg")
    # Empty text is a valid SUCCESS (heard nothing), NOT a str error.
    assert isinstance(result, SttResult)
    assert result.text == ""


async def test_transcribe_failure_returns_str_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = WhisperSttBackend()
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda op: None))

    def _boom(audio_bytes: bytes, audio_format: str) -> str:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(backend, "_transcribe_sync", _boom)
    result = await backend.transcribe(b"x", audio_format="ogg")
    assert isinstance(result, str)
    assert "transcription failed" in result


def test_stt_error_key_classifies_ffmpeg() -> None:
    from stackowl.media.stt.base import stt_error_key

    assert stt_error_key("cannot decode ogg without ffmpeg") == "voice.err.ffmpeg"
    assert stt_error_key("RuntimeError: model exploded") == "voice.err.generic"
    assert stt_error_key(None) == "voice.err.generic"


def test_decode_wav_is_ffmpeg_free() -> None:
    # arecord-style 16 kHz mono 16-bit WAV → decoded to a float32 array WITHOUT
    # ffmpeg (the bug fix: openai-whisper's load_audio shells out to ffmpeg).
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x10" * 16000)  # 1s of a constant sample
    arr = WhisperSttBackend._decode_wav(buf.getvalue())
    assert arr.dtype.name == "float32"
    assert arr.shape[0] == 16000  # 1 second at 16 kHz mono
    assert -1.0 <= float(arr.max()) <= 1.0


def test_decode_non_wav_without_ffmpeg_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    # OGG (Telegram) needs a codec; with no ffmpeg the error must be actionable.
    import stackowl.media.stt.local as local_mod

    monkeypatch.setattr(local_mod.shutil, "which", lambda _name: None)
    backend = WhisperSttBackend()
    with pytest.raises(RuntimeError, match="ffmpeg"):
        backend._decode_audio(b"OggS....", audio_format="ogg")


def test_fds_to_keep_race_retries_once_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live incident 2026-07-23: whisper.load_audio()'s ffmpeg subprocess spawn
    can hit CPython's documented fds_to_keep race under concurrent subprocess
    use elsewhere in the process. First call raises the exact ValueError;
    the retry must succeed and return its result — never raise."""
    calls = {"n": 0}

    class _FakeWhisperModule:
        @staticmethod
        def load_audio(tmp_path: str) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("bad value(s) in fds_to_keep: {-1}")
            return "decoded-audio"

    result = WhisperSttBackend._load_audio_with_retry(_FakeWhisperModule(), "/tmp/x.ogg")

    assert result == "decoded-audio"
    assert calls["n"] == 2


def test_fds_to_keep_race_persists_after_retry_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry is bounded, not infinite — if the race (implausibly) recurs on
    the second attempt too, the ValueError must still propagate rather than
    retry forever."""
    class _AlwaysRacesModule:
        @staticmethod
        def load_audio(tmp_path: str) -> str:
            raise ValueError("bad value(s) in fds_to_keep: {-1}")

    with pytest.raises(ValueError, match="fds_to_keep"):
        WhisperSttBackend._load_audio_with_retry(_AlwaysRacesModule(), "/tmp/x.ogg")


def test_unrelated_value_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the specific fds_to_keep race is treated as transient — any other
    ValueError (a real decode/argument problem) must propagate immediately,
    unretried, so a genuine bug is never silently masked by a retry."""
    calls = {"n": 0}

    class _GenuinelyBrokenModule:
        @staticmethod
        def load_audio(tmp_path: str) -> str:
            calls["n"] += 1
            raise ValueError("invalid sample rate")

    with pytest.raises(ValueError, match="invalid sample rate"):
        WhisperSttBackend._load_audio_with_retry(_GenuinelyBrokenModule(), "/tmp/x.ogg")
    assert calls["n"] == 1  # no retry for an unrelated ValueError


def test_fds_to_keep_race_in_model_transcribe_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry-once wrapper around model.transcribe() stays as defense-in-
    depth for any subprocess spawn other than tqdm's now-eliminated
    multiprocessing lock (see test_init_forces_tqdm_onto_a_threading_lock)."""
    backend = WhisperSttBackend()
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda op: None))
    monkeypatch.setattr(backend, "_ensure_loaded", lambda: SttAvailability.ok())
    monkeypatch.setattr(backend, "_decode_audio", lambda audio_bytes, audio_format: "decoded")

    calls = {"n": 0}

    class _FakeModel:
        @staticmethod
        def transcribe(audio: str) -> dict[str, str]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("bad value(s) in fds_to_keep: {-1}")
            return {"text": "hello"}

    backend._model = _FakeModel()
    text = backend._transcribe_sync(b"x", "wav")

    assert text == "hello"
    assert calls["n"] == 2


def test_init_forces_tqdm_onto_a_threading_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live incident 2026-07-23 (third occurrence): two separate bounded
    retry-once mitigations around tqdm's lazy multiprocessing.RLock
    construction both still failed under real concurrent transcribe() load —
    one of them even negative-cached the whole backend as permanently
    unavailable. Root-caused instead: this backend never needs CROSS-PROCESS
    progress-bar coordination (a single dedicated executor thread does all
    transcription), so __init__ must force tqdm onto a plain threading lock,
    removing the subprocess-spawning fork_exec path entirely."""
    import sys
    import types

    calls: list[object] = []
    fake_tqdm_module = types.ModuleType("tqdm")
    fake_tqdm_module.tqdm = types.SimpleNamespace(  # type: ignore[attr-defined]
        set_lock=lambda lock: calls.append(lock)
    )
    monkeypatch.setitem(sys.modules, "tqdm", fake_tqdm_module)

    WhisperSttBackend(model_name="base")

    assert len(calls) == 1
    assert "multiprocessing" not in type(calls[0]).__module__


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
