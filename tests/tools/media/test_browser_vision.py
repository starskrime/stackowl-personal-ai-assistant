"""E10-S5 — browser_vision tool: screenshot the current page + analyze it.

Unit coverage of the tool's contract points. The BROWSER is mocked (no real
Camoufox/Playwright launch): a fake ``sessions.get_page`` returns a fake page
whose ``screenshot(path=...)`` writes a real tiny PNG to disk under
``screenshots_dir``. The vision side is REAL (real ProviderRegistry + real
VisionSelector); only the vision provider's ``complete()`` output is canned.

Asserted:

* description returned + screenshot_path surfaced (in the JSON payload);
* no browser runtime/page → structured failure, NO vision call;
* a screenshot capture error → structured failure, NO vision call;
* no vision provider → ACTIONABLE structured failure;
* a CLOUD vision backend → egress header PREPENDED to the description;
* a LOCAL vision backend → the description with NO egress header;
* a provider that raises inside complete() → structured failure, NEVER raises;
* registered in ToolRegistry.with_defaults() with severity read / group media.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import Literal

import pytest

import stackowl.tools.media.browser_vision as bv_mod
from stackowl.config.test_mode import TestModeGuard
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.media.browser_vision import BrowserVisionTool

pytestmark = pytest.mark.asyncio


# --- a minimal VALID 1x1 PNG written with stdlib (no Pillow) --------------------
def _png_bytes() -> bytes:
    """A genuine 1x1 opaque-red PNG (magic-byte sniffable as image/png)."""

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit, RGB
    raw = b"\x00\xff\x00\x00"  # one filtered scanline: red pixel
    idat = zlib.compress(raw)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


# --- fake browser plumbing: a page whose screenshot() writes a real PNG ---------
class _FakePage:
    def __init__(self, *, raise_on_shot: bool = False, oversize: bool = False) -> None:
        self._raise = raise_on_shot
        self._oversize = oversize
        self.shot_calls = 0

    async def screenshot(self, *, path: str, full_page: bool = False) -> None:
        self.shot_calls += 1
        if self._raise:
            from playwright.async_api import Error as PlaywrightError

            raise PlaywrightError("page navigated away mid-screenshot")
        if self._oversize:
            Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (11 * 1024 * 1024))
            return
        Path(path).write_bytes(_png_bytes())


class _FakeSessions:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.get_page_calls = 0

    async def get_page(self, session_id: str, page_handle: str | None = None):  # noqa: ANN201
        self.get_page_calls += 1
        return object(), self._page, "ph1"


class _FakeSettings:
    def __init__(self, screenshots_dir: Path) -> None:
        self.screenshots_dir = screenshots_dir


class _FakeRuntime:
    def __init__(self, screenshots_dir: Path) -> None:
        self.settings = _FakeSettings(screenshots_dir)


# --- the ONLY vision mock: a vision-capable provider with canned complete() -----
class _VisionMock(MockProvider):
    def __init__(self, name: str, *, description: str = "a login form is visible", raises: bool = False) -> None:
        super().__init__(name=name)
        self._description = description
        self._raises = raises
        self.seen_image_bytes = False
        self.complete_calls = 0

    @property
    def supports_vision(self) -> bool:  # type: ignore[override]
        return True

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        self.complete_calls += 1
        if self._raises:
            raise RuntimeError("vision backend exploded")
        for m in messages:
            for d in m.documents:
                if d.media_type.startswith("image/"):
                    self.seen_image_bytes = True
        return CompletionResult(
            content=self._description, input_tokens=1, output_tokens=1,
            model="vision-mock", provider_name=self.name, duration_ms=1.0,
        )


def _registry_with(
    *, locality: Literal["local", "cloud"], raises: bool = False, description: str = "a login form is visible"
) -> tuple[ProviderRegistry, _VisionMock]:
    reg = ProviderRegistry()
    mock = _VisionMock("vision", description=description, raises=raises)
    base_url = "http://localhost:11434/v1" if locality == "local" else "https://api.cloud.example/v1"
    reg.register_mock("vision", mock, tier="fast", base_url=base_url)
    return reg, mock


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):  # noqa: ANN202
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _services(registry: ProviderRegistry | None):  # noqa: ANN202
    return set_services(StepServices(provider_registry=registry))


def _wire_browser(monkeypatch, tmp_path: Path, *, page: _FakePage) -> _FakeSessions:
    """Patch the E2 services accessor browser_vision imports so capture is mocked."""
    shots = tmp_path / "home" / "screenshots"
    runtime = _FakeRuntime(shots)
    sessions = _FakeSessions(page)
    monkeypatch.setattr(
        bv_mod, "_services_or_unavailable", lambda: (runtime, sessions, None)
    )
    return sessions


async def test_describe_returns_description_and_screenshot_path(tmp_path, monkeypatch) -> None:
    page = _FakePage()
    sessions = _wire_browser(monkeypatch, tmp_path, page=page)
    reg, mock = _registry_with(locality="local")
    token = _services(reg)
    try:
        res = await BrowserVisionTool().execute(session_id="sess1234", question="what's here?")
    finally:
        reset_services(token)
    assert res.success is True
    payload = json.loads(res.output)
    assert payload["description"] == "a login form is visible"
    assert payload["screenshot_path"].endswith("-vision.png")
    assert Path(payload["screenshot_path"]).is_file()  # the PNG really landed on disk
    assert payload["backend"] == "vision"
    assert payload["local"] is True
    assert mock.seen_image_bytes is True  # the screenshot bytes rode along
    assert sessions.get_page_calls == 1
    assert page.shot_calls == 1


async def test_no_browser_runtime_is_structured_no_vision_call(tmp_path, monkeypatch) -> None:
    # No browser runtime wired → unavailable; the vision provider is NEVER called.
    monkeypatch.setattr(
        bv_mod, "_services_or_unavailable", lambda: (None, None, "Browser runtime not initialized")
    )
    reg, mock = _registry_with(locality="local")
    token = _services(reg)
    try:
        res = await BrowserVisionTool().execute(session_id="sess1234")
    finally:
        reset_services(token)
    assert res.success is False
    assert "no browser page" in (res.error or "").lower()
    assert mock.complete_calls == 0  # NO vision call happened


async def test_screenshot_failure_is_structured_no_vision_call(tmp_path, monkeypatch) -> None:
    page = _FakePage(raise_on_shot=True)
    _wire_browser(monkeypatch, tmp_path, page=page)
    reg, mock = _registry_with(locality="local")
    token = _services(reg)
    try:
        res = await BrowserVisionTool().execute(session_id="sess1234")  # must NOT raise
    finally:
        reset_services(token)
    assert res.success is False
    assert "screenshot" in (res.error or "").lower()
    assert mock.complete_calls == 0  # capture failed before any vision call


async def test_oversize_screenshot_is_refused_no_vision_call(tmp_path, monkeypatch) -> None:
    """A full_page capture that balloons past the cap is refused BEFORE any send."""
    page = _FakePage(oversize=True)
    _wire_browser(monkeypatch, tmp_path, page=page)
    reg, mock = _registry_with(locality="local")
    token = _services(reg)
    try:
        res = await BrowserVisionTool().execute(session_id="sess1234")
    finally:
        reset_services(token)
    assert res.success is False
    assert "too large" in (res.error or "").lower()
    assert mock.complete_calls == 0  # never sent the giant image to the model


async def test_no_vision_provider_is_actionable(tmp_path, monkeypatch) -> None:
    page = _FakePage()
    _wire_browser(monkeypatch, tmp_path, page=page)
    # A registry with only a TEXT provider (supports_vision False) → actionable.
    reg = ProviderRegistry()
    reg.register_mock("text", MockProvider("text"), tier="fast", base_url="http://localhost:1/v1")
    token = _services(reg)
    try:
        res = await BrowserVisionTool().execute(session_id="sess1234")
    finally:
        reset_services(token)
    assert res.success is False
    err = (res.error or "").lower()
    assert "vision" in err and ("install" in err or "configure" in err)


async def test_cloud_backend_prepends_egress_header(tmp_path, monkeypatch) -> None:
    page = _FakePage()
    _wire_browser(monkeypatch, tmp_path, page=page)
    reg, mock = _registry_with(locality="cloud", description="a checkout page")
    token = _services(reg)
    try:
        res = await BrowserVisionTool().execute(session_id="sess1234", question="what is this?")
    finally:
        reset_services(token)
    assert res.success is True
    payload = json.loads(res.output)
    assert mock.seen_image_bytes is True
    assert payload["local"] is False
    desc = payload["description"]
    assert desc.startswith("[Cloud vision:")
    assert "'vision'" in desc  # names the provider
    assert "left this machine" in desc
    assert "a checkout page" in desc
    # The screenshot path is STILL surfaced even on a cloud backend.
    assert Path(payload["screenshot_path"]).is_file()


async def test_local_backend_has_no_egress_header(tmp_path, monkeypatch) -> None:
    page = _FakePage()
    _wire_browser(monkeypatch, tmp_path, page=page)
    reg, mock = _registry_with(locality="local", description="a checkout page")
    token = _services(reg)
    try:
        res = await BrowserVisionTool().execute(session_id="sess1234", question="describe")
    finally:
        reset_services(token)
    assert res.success is True
    payload = json.loads(res.output)
    assert mock.seen_image_bytes is True
    assert "[Cloud vision:" not in payload["description"]  # stayed on-box → NO note
    assert payload["description"] == "a checkout page"


async def test_provider_raise_degrades_never_raises(tmp_path, monkeypatch) -> None:
    page = _FakePage()
    _wire_browser(monkeypatch, tmp_path, page=page)
    reg, _mock = _registry_with(locality="local", raises=True)
    token = _services(reg)
    try:
        res = await BrowserVisionTool().execute(session_id="sess1234")  # must NOT raise
    finally:
        reset_services(token)
    assert res.success is False
    assert "failed" in (res.error or "").lower()


async def test_registered_in_with_defaults() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = ToolRegistry.with_defaults()
    tool = reg.get("browser_vision")
    assert tool is not None
    assert tool.manifest.action_severity == "read"
    assert tool.manifest.toolset_group == "media"
