"""Tests for browser_press (E2-S3) — keyboard.press wrapper with chord support."""

from __future__ import annotations

from typing import Any

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.browser.press import BrowserPressTool


class _FakeKeyboard:
    def __init__(self, *, raise_on: str | None = None) -> None:
        self.pressed: list[str] = []
        self._raise_on = raise_on

    async def press(self, key: str) -> None:
        if self._raise_on is not None and key == self._raise_on:
            raise ValueError(f"Unknown key: {key}")
        self.pressed.append(key)


class _FakePage:
    def __init__(self, *, raise_on: str | None = None) -> None:
        self.keyboard = _FakeKeyboard(raise_on=raise_on)


class _FakeSessions:
    def __init__(self, page: _FakePage | None, *, raise_on_get: bool = False) -> None:
        self._page = page
        self._raise = raise_on_get

    async def get_page(self, session_id: str, page_handle: str | None = None) -> tuple[Any, Any, str]:
        if self._raise:
            raise RuntimeError("gone")
        return object(), self._page, page_handle or "h1"


def _services(page: _FakePage | None, *, runtime: object | None = object(), raise_on_get: bool = False) -> StepServices:
    return StepServices(
        browser_runtime=runtime,  # type: ignore[arg-type]
        browser_sessions=_FakeSessions(page, raise_on_get=raise_on_get),  # type: ignore[arg-type]
    )


class TestBrowserPressTool:
    def test_manifest(self) -> None:
        m = BrowserPressTool().manifest
        assert m.action_severity == "read"
        assert m.toolset_group == "browser"

    async def test_press_single_key(self) -> None:
        page = _FakePage()
        token = set_services(_services(page))
        try:
            result = await BrowserPressTool().execute(session_id="s1", key="Enter")
        finally:
            reset_services(token)
        assert result.success is True
        assert page.keyboard.pressed == ["Enter"]

    async def test_press_chord(self) -> None:
        page = _FakePage()
        token = set_services(_services(page))
        try:
            result = await BrowserPressTool().execute(session_id="s1", key="Control+A")
        finally:
            reset_services(token)
        assert result.success is True
        assert page.keyboard.pressed == ["Control+A"]

    async def test_empty_key_rejected_before_engine(self) -> None:
        page = _FakePage()
        token = set_services(_services(page))
        try:
            result = await BrowserPressTool().execute(session_id="s1", key="")
        finally:
            reset_services(token)
        assert result.success is False
        assert "Invalid key" in (result.error or "")
        assert page.keyboard.pressed == []  # never reached the engine

    async def test_oversized_key_rejected(self) -> None:
        page = _FakePage()
        token = set_services(_services(page))
        try:
            result = await BrowserPressTool().execute(session_id="s1", key="a" * 200)
        finally:
            reset_services(token)
        assert result.success is False
        assert "Invalid key" in (result.error or "")
        assert page.keyboard.pressed == []

    async def test_control_char_key_rejected(self) -> None:
        page = _FakePage()
        token = set_services(_services(page))
        try:
            result = await BrowserPressTool().execute(session_id="s1", key="a\x00b")
        finally:
            reset_services(token)
        assert result.success is False
        assert page.keyboard.pressed == []

    async def test_engine_rejection_is_structured(self) -> None:
        page = _FakePage(raise_on="Nonsense")
        token = set_services(_services(page))
        try:
            result = await BrowserPressTool().execute(session_id="s1", key="Nonsense")
        finally:
            reset_services(token)
        assert result.success is False
        assert "press failed" in (result.error or "")

    async def test_no_runtime_unavailable(self) -> None:
        token = set_services(StepServices())
        try:
            result = await BrowserPressTool().execute(session_id="s1", key="Enter")
        finally:
            reset_services(token)
        assert result.success is False

    async def test_dead_session_degrades(self) -> None:
        token = set_services(_services(_FakePage(), raise_on_get=True))
        try:
            result = await BrowserPressTool().execute(session_id="dead", key="Enter")
        finally:
            reset_services(token)
        assert result.success is False
        assert "unavailable" in (result.error or "")

    def test_registered(self) -> None:
        from stackowl.tools.registry import ToolRegistry

        assert ToolRegistry.with_defaults().get("browser_press") is not None
