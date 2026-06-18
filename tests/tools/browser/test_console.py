"""Tests for browser_console (E2-S4) + the sessions.py console/error substrate.

Uses the REAL BrowserSessionRegistry with a fake runtime/context/page so the
eager page.on() wiring is exercised end-to-end (events fire → bounded buffer →
tool reads the split messages/errors shape).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from stackowl.config.browser import BrowserSettings
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.browser.console import BrowserConsoleTool
from stackowl.tools.browser.sessions import _PAGE_LOG_BUFFER_MAX, BrowserSessionRegistry


class _ConsoleMsg:
    def __init__(self, type_: str, text: str) -> None:
        self.type = type_
        self.text = text


class _FakePage:
    def __init__(self) -> None:
        self.url = "https://x.test/"
        self._handlers: dict[str, Callable[[Any], None]] = {}

    def on(self, event: str, cb: Callable[[Any], None]) -> None:
        self._handlers[event] = cb

    def fire_console(self, type_: str, text: str) -> None:
        self._handlers["console"](_ConsoleMsg(type_, text))

    def fire_error(self, message: str, name: str = "Error") -> None:
        # Mimic Playwright's pageerror payload: an Error-like with .name/.message.
        class _Err:
            def __init__(self) -> None:
                self.name = name
                self.message = message

            def __str__(self) -> str:
                return message

        self._handlers["pageerror"](_Err())


class _FakeContext:
    async def new_page(self) -> _FakePage:
        return _FakePage()

    async def close(self) -> None:
        pass


class _FakeRuntime:
    available = True

    async def open_context(self, **kwargs: Any) -> _FakeContext:
        return _FakeContext()

    def register_on_recycled(self, cb: Any) -> None:
        pass


@pytest.fixture
def settings(tmp_path: Any) -> BrowserSettings:
    return BrowserSettings(
        max_concurrent_sessions=3,
        max_concurrent_pages_per_session=3,
        session_idle_timeout_minutes=30,
        profiles_dir=tmp_path / "p",
        screenshots_dir=tmp_path / "s",
        downloads_dir=tmp_path / "d",
        browser_cache_dir=tmp_path / "c",
    )


async def _open_with_page(settings: BrowserSettings) -> tuple[BrowserSessionRegistry, str, str, _FakePage]:
    runtime = _FakeRuntime()
    reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
    sid = await reg.open("local")
    sess, page, handle = await reg.get_page(sid)
    return reg, sid, handle, page


class TestConsoleSubstrate:
    async def test_observers_wired_at_page_creation(self, settings: BrowserSettings) -> None:
        reg, sid, handle, page = await _open_with_page(settings)
        sess = await reg.get(sid)
        # Eagerly wired — buffer exists before any console tool call.
        assert handle in sess.observers
        # Firing events fills the right buckets.
        page.fire_console("log", "hello")
        page.fire_console("error", "boom in console")
        page.fire_error("uncaught TypeError")
        assert len(sess.observers[handle].console) == 2
        assert len(sess.observers[handle].errors) == 1

    async def test_pageerror_captures_name_and_message(self, settings: BrowserSettings) -> None:
        reg, sid, handle, page = await _open_with_page(settings)
        sess = await reg.get(sid)
        page.fire_error("x is not defined", name="ReferenceError")
        rec = sess.observers[handle].errors[0]
        assert rec["name"] == "ReferenceError"
        assert rec["message"] == "x is not defined"

    async def test_tab_close_drops_observers(self, settings: BrowserSettings) -> None:
        from stackowl.pipeline.services import StepServices, reset_services, set_services
        from stackowl.tools.browser.tools import BrowserTabCloseTool

        reg, sid, handle, page = await _open_with_page(settings)
        sess = await reg.get(sid)
        page.fire_console("log", "x")
        assert handle in sess.observers
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            result = await BrowserTabCloseTool().execute(session_id=sid, page_handle=handle)
        finally:
            reset_services(token)
        assert result.success is True
        assert handle not in sess.observers  # cleaned up, no leak
        assert handle not in sess.pages

    async def test_buffer_is_bounded(self, settings: BrowserSettings) -> None:
        reg, sid, handle, page = await _open_with_page(settings)
        sess = await reg.get(sid)
        for i in range(_PAGE_LOG_BUFFER_MAX + 50):
            page.fire_console("log", f"m{i}")
        assert len(sess.observers[handle].console) == _PAGE_LOG_BUFFER_MAX  # oldest dropped


class TestBrowserConsoleTool:
    def test_manifest(self) -> None:
        m = BrowserConsoleTool().manifest
        assert m.action_severity == "read"
        assert m.toolset_group == "browser"

    async def test_returns_split_messages_and_errors(self, settings: BrowserSettings) -> None:
        reg, sid, handle, page = await _open_with_page(settings)
        page.fire_console("log", "a log line")
        page.fire_console("warning", "a warning")
        page.fire_error("uncaught boom")
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            result = await BrowserConsoleTool().execute(session_id=sid, page_handle=handle)
        finally:
            reset_services(token)
        assert result.success is True
        assert '"message_count": 2' in result.output
        assert '"error_count": 1' in result.output
        assert "a log line" in result.output
        assert "uncaught boom" in result.output

    async def test_no_activity_empty_arrays(self, settings: BrowserSettings) -> None:
        reg, sid, handle, _page = await _open_with_page(settings)
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            result = await BrowserConsoleTool().execute(session_id=sid, page_handle=handle)
        finally:
            reset_services(token)
        assert '"message_count": 0' in result.output
        assert '"error_count": 0' in result.output

    async def test_clear_empties_buffer(self, settings: BrowserSettings) -> None:
        reg, sid, handle, page = await _open_with_page(settings)
        page.fire_console("log", "x")
        token = set_services(StepServices(browser_runtime=_FakeRuntime(), browser_sessions=reg))  # type: ignore[arg-type]
        try:
            r1 = await BrowserConsoleTool().execute(session_id=sid, page_handle=handle, clear=True)
            r2 = await BrowserConsoleTool().execute(session_id=sid, page_handle=handle)
        finally:
            reset_services(token)
        assert '"message_count": 1' in r1.output
        assert '"message_count": 0' in r2.output  # cleared after first read

    async def test_no_runtime_unavailable(self) -> None:
        token = set_services(StepServices())
        try:
            result = await BrowserConsoleTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is False

    def test_registered(self) -> None:
        from stackowl.tools.registry import ToolRegistry

        assert ToolRegistry.with_defaults().get("browser_console") is not None
