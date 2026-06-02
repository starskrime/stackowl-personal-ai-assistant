"""E10-S4 — ImageSelector: local-first, opt-in cloud fallback, structured-unavailable.

Network-free / install-free: the real Local/Cloud backends are NEVER constructed
with live deps — the selector is driven with injected FAKE backends, so no probe,
pip install or HTTP ever happens. Asserts:

* a viable local model is chosen even when a cloud is also configured (local-first);
* engine='local' NEVER falls back to cloud, even if cloud is up;
* engine='auto' falls back to a configured cloud ONLY when local is unavailable;
* engine='cloud' skips local entirely and uses the cloud;
* nothing available → a structured, ACTIONABLE unavailable (never raises).
"""

from __future__ import annotations

import pytest

from stackowl.config.settings import ImageSettings
from stackowl.media.image.base import ImageAvailability, ImageBackend, ImageResult
from stackowl.media.image.selector import ImageSelector

pytestmark = pytest.mark.asyncio


class _FakeBackend(ImageBackend):
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

    async def is_available(self) -> ImageAvailability:
        return ImageAvailability.ok() if self._available else ImageAvailability.no(self._reason or "down")

    async def generate(self, prompt: str, *, size: str | None = None) -> ImageResult | str:
        return ImageResult(path="/tmp/x.png", size=size or "1024x1024", backend=self._name, is_local=self._local)


def _settings(*, engine: str = "auto") -> ImageSettings:
    return ImageSettings(engine=engine)  # type: ignore[arg-type]


async def test_local_first_even_when_cloud_configured() -> None:
    local = _FakeBackend("local-sdxl", local=True, available=True)
    cloud = _FakeBackend("cloud", local=False, available=True)
    sel = await ImageSelector(_settings(engine="auto"), local=local, cloud=cloud).select()
    assert sel.available is True
    assert sel.backend is local


async def test_engine_local_never_falls_back_to_cloud() -> None:
    local = _FakeBackend("local-sdxl", local=True, available=False, reason="probe: Tegra")
    cloud = _FakeBackend("cloud", local=False, available=True)
    sel = await ImageSelector(_settings(engine="local"), local=local, cloud=cloud).select()
    assert sel.available is False
    assert "cloud fallback disabled" in (sel.reason or "")


async def test_auto_falls_back_to_cloud_when_local_down() -> None:
    local = _FakeBackend("local-sdxl", local=True, available=False, reason="probe: no CUDA")
    cloud = _FakeBackend("cloud", local=False, available=True)
    sel = await ImageSelector(_settings(engine="auto"), local=local, cloud=cloud).select()
    assert sel.available is True
    assert sel.backend is cloud
    assert sel.backend.is_local is False  # cloud → egress will be disclosed


async def test_engine_cloud_skips_local() -> None:
    local = _FakeBackend("local-sdxl", local=True, available=True)
    cloud = _FakeBackend("cloud", local=False, available=True)
    sel = await ImageSelector(_settings(engine="cloud"), local=local, cloud=cloud).select()
    assert sel.available is True
    assert sel.backend is cloud  # local never consulted


async def test_nothing_available_is_structured_actionable() -> None:
    local = _FakeBackend("local-sdxl", local=True, available=False, reason="probe: Tegra unified-memory")
    cloud = _FakeBackend("cloud", local=False, available=False, reason="no key")
    sel = await ImageSelector(_settings(engine="auto"), local=local, cloud=cloud).select()
    assert sel.available is False
    reason = (sel.reason or "").lower()
    assert "image generation unavailable" in reason
    assert "local gpu not available" in reason
    assert "tegra" in reason  # surfaces the probe reason
    assert "no key" in reason  # surfaces the cloud reason
    assert "cloud_enabled" in reason  # actionable next step
