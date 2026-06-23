"""SttSelector — local-first, opt-in cloud fallback, structured-unavailable.

Network-free / install-free: the real Whisper backend is NEVER constructed with
live deps — the selector is driven with injected FAKE backends, so no pip install
or model load ever happens. Mirrors tests/media/tts/test_selector.py. Asserts:

* a healthy local engine is chosen even when a cloud is also configured (local-first);
* engine='local' NEVER falls back to cloud (cloud-disabled), even if cloud is up;
* engine='auto' falls back to a configured cloud ONLY when local is unavailable;
* nothing available → a structured, ACTIONABLE unavailable (never raises).
"""

from __future__ import annotations

import pytest

from stackowl.config.settings import TranscriptionSettings
from stackowl.media.stt.base import SttAvailability, SttBackend, SttResult
from stackowl.media.stt.selector import SttSelector

pytestmark = pytest.mark.asyncio


class _FakeBackend(SttBackend):
    """A deterministic backend with no heavy deps — controls availability + locality."""

    def __init__(
        self, name: str, *, local: bool, available: bool, reason: str | None = None
    ) -> None:
        self._name = name
        self._local = local
        self._available = available
        self._reason = reason

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_local(self) -> bool:
        return self._local

    async def is_available(self) -> SttAvailability:
        return SttAvailability.ok() if self._available else SttAvailability.no(self._reason or "down")

    async def transcribe(
        self, audio_bytes: bytes, *, audio_format: str = "ogg"
    ) -> SttResult | str:
        return SttResult(text="hi", backend=self._name, is_local=self._local)


def _settings(*, engine: str = "auto") -> TranscriptionSettings:
    return TranscriptionSettings(enabled=True, engine=engine)  # type: ignore[arg-type]


async def test_local_first_even_when_cloud_configured() -> None:
    local = _FakeBackend("whisper", local=True, available=True)
    cloud = _FakeBackend("cloud", local=False, available=True)
    sel = await SttSelector(_settings(engine="auto"), local=local, cloud=cloud).select()
    assert sel.available is True
    assert sel.backend is local  # local preferred over an available cloud


async def test_engine_local_never_falls_back_to_cloud() -> None:
    local = _FakeBackend("whisper", local=True, available=False, reason="not installed")
    cloud = _FakeBackend("cloud", local=False, available=True)
    sel = await SttSelector(_settings(engine="local"), local=local, cloud=cloud).select()
    # engine='local' = local-only: cloud is NOT consulted even though it is up.
    assert sel.available is False
    assert "cloud fallback disabled" in (sel.reason or "")


async def test_auto_falls_back_to_cloud_when_local_down() -> None:
    local = _FakeBackend("whisper", local=True, available=False, reason="load failed")
    cloud = _FakeBackend("cloud", local=False, available=True)
    sel = await SttSelector(_settings(engine="auto"), local=local, cloud=cloud).select()
    assert sel.available is True
    assert sel.backend is cloud


async def test_nothing_available_is_structured_not_raised() -> None:
    local = _FakeBackend("whisper", local=True, available=False, reason="no torch")
    cloud = _FakeBackend("cloud", local=False, available=False, reason="not configured")
    sel = await SttSelector(_settings(engine="auto"), local=local, cloud=cloud).select()
    assert sel.available is False
    assert "transcription unavailable" in (sel.reason or "")
    assert "no torch" in (sel.reason or "")


async def test_default_cloud_backend_is_unavailable() -> None:
    # With no injected cloud, the built-in placeholder cloud is always unavailable.
    local = _FakeBackend("whisper", local=True, available=False, reason="down")
    sel = await SttSelector(_settings(engine="auto"), local=local).select()
    assert sel.available is False
    assert "no cloud STT backend is configured" in (sel.reason or "")
