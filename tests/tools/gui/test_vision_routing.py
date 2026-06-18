"""Desktop vision routing — local-first; refuse-cloud-for-desktop-frames guarantee.

The vision side is REAL (real ProviderRegistry + VisionSelector + analyzer); only
the provider's ``complete()`` is canned. A LOCAL vision backend → routes. A CLOUD
backend → desktop vision UNAVAILABLE and the screen frame is NEVER sent to it
(asserted: the cloud provider's complete() is never called).
"""

from __future__ import annotations

from typing import Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.gui.models import CaptureResult
from stackowl.tools.gui.vision_routing import DesktopVisionRouter

pytestmark = pytest.mark.asyncio


class _VisionMock(MockProvider):
    def __init__(self, name: str, *, description: str = "a Save button and a text field") -> None:
        super().__init__(name=name)
        self._description = description
        self.complete_calls = 0

    @property
    def supports_vision(self) -> bool:  # type: ignore[override]
        return True

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        self.complete_calls += 1
        return CompletionResult(
            content=self._description, input_tokens=1, output_tokens=1,
            model="vision-mock", provider_name=self.name, duration_ms=1.0,
        )


def _registry_with(locality: Literal["local", "cloud"]) -> tuple[ProviderRegistry, _VisionMock]:
    reg = ProviderRegistry()
    mock = _VisionMock("vision")
    base_url = "http://localhost:11434/v1" if locality == "local" else "https://api.cloud.example/v1"
    reg.register_mock("vision", mock, tier="fast", base_url=base_url)
    return reg, mock


def _redacted_capture() -> CaptureResult:
    return CaptureResult(frame=b"\x89PNG\r\n\x1a\nDESKTOP", width=1920, height=1080, redacted=True)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):  # noqa: ANN202
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class TestLocalRoutes:
    async def test_local_backend_routes(self) -> None:
        reg, mock = _registry_with("local")
        out = await DesktopVisionRouter(reg).route(_redacted_capture())
        assert out.available and out.routed_local
        assert out.description is not None and "Save" in out.description
        assert out.non_persistable is True
        assert mock.complete_calls == 1  # local backend WAS used


class TestCloudRefused:
    async def test_cloud_backend_makes_desktop_vision_unavailable(self) -> None:
        reg, mock = _registry_with("cloud")
        out = await DesktopVisionRouter(reg).route(_redacted_capture())
        assert not out.available
        assert "LOCAL" in (out.reason or "") or "local" in (out.reason or "")

    async def test_screen_frame_never_sent_to_cloud(self) -> None:
        """The hard guarantee: a desktop frame NEVER reaches a cloud vision backend."""
        reg, mock = _registry_with("cloud")
        await DesktopVisionRouter(reg).route(_redacted_capture())
        assert mock.complete_calls == 0  # the cloud provider was NEVER called


class TestNoVision:
    async def test_no_registry_unavailable(self) -> None:
        out = await DesktopVisionRouter(None).route(_redacted_capture())
        assert not out.available and out.description is None

    async def test_empty_registry_unavailable(self) -> None:
        out = await DesktopVisionRouter(ProviderRegistry()).route(_redacted_capture())
        assert not out.available


class TestUnredactedRefused:
    async def test_unredacted_frame_not_routed(self) -> None:
        reg, mock = _registry_with("local")
        cap = CaptureResult(frame=b"x", width=10, height=10, redacted=False)
        out = await DesktopVisionRouter(reg).route(cap)
        assert not out.available
        assert mock.complete_calls == 0  # never routed an un-redacted frame
