"""E10-S3 — Piper + Cloud backend contracts (network-free, install-free).

NEVER installs real Piper or hits a network. The local engine's lazy-loader is
PATCHED to either fail (→ structured unavailable) or to load a fake voice object
that writes a tiny REAL WAV — so we assert a PATH under media_dir() (not bytes),
the file exists, and metadata is surfaced, without any heavy dep. The cloud
backend's disabled-by-default + opt-in availability is asserted with no HTTP.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.media.tts.base import TtsResult
from stackowl.media.tts.cloud import CloudTtsBackend
from stackowl.media.tts.piper import PiperBackend
from stackowl.paths import StackowlHome

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):  # noqa: ANN202
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _FakeVoice:
    """Stands in for the loaded local-engine voice — writes a tiny real WAV."""

    def synthesize(self, text: str, wav: wave.Wave_write) -> None:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)  # 0.1s of silence


class _FakeVoiceLoader:
    """Stands in for the engine's PiperVoice class with a .load() classmethod-ish."""

    @staticmethod
    def load(path: str) -> _FakeVoice:
        return _FakeVoice()


# --------------------------------------------------------------------- Piper
async def test_piper_success_returns_path_not_bytes(monkeypatch) -> None:
    backend = PiperBackend(voice="en_US-lessac-medium")
    # Patch the lazy-loader internals: skip pip + voice download, load a fake voice.
    monkeypatch.setattr(backend, "_import_engine", lambda: _FakeVoiceLoader)
    monkeypatch.setattr(backend, "_ensure_voice_files", lambda voice: Path("ignored.onnx"))

    out = await backend.synthesize("hello world", voice=None)
    assert isinstance(out, TtsResult)  # a structured result, not bytes
    assert out.is_local is True
    assert out.backend == "piper"
    assert out.voice == "en_US-lessac-medium"
    p = Path(out.path)
    # PATH lives under media_dir()/tts and the file really exists.
    assert p.exists()
    assert StackowlHome.media_dir() in p.parents
    assert out.duration_ms > 0  # derived from the real WAV frame count


async def test_piper_install_failure_is_unavailable_never_raises(monkeypatch) -> None:
    backend = PiperBackend(voice="en_US-lessac-medium")

    def _boom() -> object:
        raise RuntimeError("pip wheel build failed on this arch")

    monkeypatch.setattr(backend, "_import_engine", _boom)
    avail = await backend.is_available()
    assert avail.available is False
    assert "could not initialize" in (avail.reason or "")
    # synthesize must also degrade to a structured str, never raise.
    out = await backend.synthesize("hello", voice=None)
    assert isinstance(out, str)
    assert "could not initialize" in out


async def test_piper_unknown_voice_is_unavailable(monkeypatch) -> None:
    backend = PiperBackend(voice="zz_NONE-bogus")
    monkeypatch.setattr(backend, "_import_engine", lambda: _FakeVoiceLoader)
    avail = await backend.is_available("zz_NONE-bogus")
    assert avail.available is False
    assert "unknown voice" in (avail.reason or "")


async def test_piper_test_mode_is_unavailable_no_shell_out_no_network(monkeypatch) -> None:
    """Under an ACTIVE TestModeGuard the REAL (un-mocked) loader must report
    unavailable WITHOUT shelling out to pip OR opening a network connection."""
    import subprocess as _subprocess
    import urllib.request as _urllib

    subprocess_calls: list[object] = []
    urlopen_calls: list[object] = []
    monkeypatch.setattr(
        _subprocess, "run", lambda *a, **k: subprocess_calls.append((a, k))
    )
    monkeypatch.setattr(
        _urllib, "urlopen", lambda *a, **k: urlopen_calls.append((a, k))
    )
    # Force the install path: pretend the engine import fails so _import_engine
    # would otherwise pip-install — the guard must stop it first.
    monkeypatch.setattr(
        "stackowl.media.tts.piper.PiperBackend._import_engine",
        PiperBackend._import_engine,
    )

    backend = PiperBackend(voice="en_US-lessac-medium")
    TestModeGuard.activate()
    try:
        avail = await backend.is_available()
    finally:
        TestModeGuard.deactivate()

    assert avail.available is False
    assert "could not initialize" in (avail.reason or "")
    assert subprocess_calls == []  # the pip install never fired
    assert urlopen_calls == []  # no network egress


async def test_piper_oversize_download_refused_no_final_file(monkeypatch, tmp_path) -> None:
    """An oversized response is refused (cap exceeded) without buffering
    unbounded, the .part temp is cleaned up, and no final file is written."""
    import urllib.request as _urllib

    from stackowl.media.tts import piper as _piper

    monkeypatch.setattr(_piper, "_MAX_VOICE_BYTES", 4)
    monkeypatch.setattr(_piper, "_DOWNLOAD_CHUNK_BYTES", 2)

    class _FakeResp:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}  # no Content-Length → stream-cap path
            self._chunks = [b"ab", b"cd", b"ef"]  # 6 bytes > cap 4

        def read(self, n: int = -1) -> bytes:
            return self._chunks.pop(0) if self._chunks else b""

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(_urllib, "urlopen", lambda *a, **k: _FakeResp())

    dest = tmp_path / "voice.onnx"
    with pytest.raises(ValueError, match="exceeded cap"):
        PiperBackend._download("https://x/voice.onnx", dest)

    assert not dest.exists()  # no final file
    assert not dest.with_suffix(dest.suffix + ".part").exists()  # temp cleaned up


# --------------------------------------------------------------------- Cloud
async def test_cloud_disabled_by_default() -> None:
    # No key reference → disabled regardless of base_url.
    backend = CloudTtsBackend(base_url="https://x/v1", api_key_ref="", voice="alloy")
    avail = await backend.is_available()
    assert avail.available is False
    assert "disabled" in (avail.reason or "")
    out = await backend.synthesize("hi", voice=None)
    assert isinstance(out, str)
    assert "disabled" in out


async def test_cloud_available_when_key_resolves(monkeypatch) -> None:
    monkeypatch.setenv("FAKE_TTS_KEY", "sk-fake")
    backend = CloudTtsBackend(base_url="https://x/v1", api_key_ref="FAKE_TTS_KEY")
    avail = await backend.is_available()
    assert avail.available is True


async def test_cloud_unresolvable_key_is_unavailable() -> None:
    # Env var not set → reference fails to resolve → unavailable, never raises.
    backend = CloudTtsBackend(base_url="https://x/v1", api_key_ref="MISSING_TTS_KEY_VAR")
    avail = await backend.is_available()
    assert avail.available is False
    assert "did not resolve" in (avail.reason or "")
