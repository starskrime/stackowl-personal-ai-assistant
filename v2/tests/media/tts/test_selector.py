"""E10-S3 — TtsSelector: local-first, opt-in cloud fallback, structured-unavailable.

Network-free / install-free: the real Piper/Cloud backends are NEVER constructed
with live deps — the selector is driven with injected FAKE backends, so no pip
install or HTTP ever happens. Asserts:

* a healthy local engine is chosen even when a cloud is also configured (local-first);
* engine='piper' NEVER falls back to cloud (cloud-disabled), even if cloud is up;
* engine='auto' falls back to a configured cloud ONLY when local is unavailable;
* nothing available → a structured, ACTIONABLE unavailable (never raises).
"""

from __future__ import annotations

import pytest

from stackowl.config.settings import TtsSettings
from stackowl.media.tts.base import TtsAvailability, TtsBackend, TtsResult
from stackowl.media.tts.selector import TtsSelector

pytestmark = pytest.mark.asyncio


class _FakeBackend(TtsBackend):
    """A deterministic backend with no heavy deps — controls availability + locality."""

    def __init__(self, name: str, *, local: bool, available: bool, reason: str | None = None) -> None:
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

    async def is_available(self, voice: str | None = None) -> TtsAvailability:
        return TtsAvailability.ok() if self._available else TtsAvailability.no(self._reason or "down")

    async def synthesize(self, text: str, *, voice: str | None) -> TtsResult | str:
        return TtsResult(
            path="/tmp/x.wav", duration_ms=1.0, voice=voice or "v",
            backend=self._name, is_local=self._local,
        )


def _settings(*, engine: str = "auto") -> TtsSettings:
    return TtsSettings(engine=engine)  # type: ignore[arg-type]


async def test_local_first_even_when_cloud_configured() -> None:
    local = _FakeBackend("piper", local=True, available=True)
    cloud = _FakeBackend("cloud", local=False, available=True)
    sel = await TtsSelector(_settings(engine="auto"), local=local, cloud=cloud).select()
    assert sel.available is True
    assert sel.backend is local  # local preferred over an available cloud


async def test_engine_piper_never_falls_back_to_cloud() -> None:
    local = _FakeBackend("piper", local=True, available=False, reason="not installed")
    cloud = _FakeBackend("cloud", local=False, available=True)
    sel = await TtsSelector(_settings(engine="piper"), local=local, cloud=cloud).select()
    # engine='piper' = local-only: cloud is NOT consulted even though it is up.
    assert sel.available is False
    assert "cloud fallback disabled" in (sel.reason or "")


async def test_auto_falls_back_to_cloud_when_local_down() -> None:
    local = _FakeBackend("piper", local=True, available=False, reason="not installed")
    cloud = _FakeBackend("cloud", local=False, available=True)
    sel = await TtsSelector(_settings(engine="auto"), local=local, cloud=cloud).select()
    assert sel.available is True
    assert sel.backend is cloud
    assert sel.backend.is_local is False  # cloud → egress will be disclosed


async def test_nothing_available_is_structured_actionable() -> None:
    local = _FakeBackend("piper", local=True, available=False, reason="install failed: ARM wheel")
    cloud = _FakeBackend("cloud", local=False, available=False, reason="no key")
    sel = await TtsSelector(_settings(engine="auto"), local=local, cloud=cloud).select()
    assert sel.available is False
    reason = (sel.reason or "").lower()
    assert "tts unavailable" in reason
    assert "install failed" in reason  # surfaces the local reason
    assert "no key" in reason  # surfaces the cloud reason
    assert "cloud_enabled" in reason  # actionable next step
