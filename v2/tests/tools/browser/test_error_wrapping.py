"""Regression — atomic browser tools must return structured failures.

Live log showed ``ERROR tool.__call__: unhandled exception — wrapping`` from the
base Tool wrapper, originating in ``browser_type``'s ``sessions.get_page`` and
``browser_download``'s ``page.click``. Playwright / session exceptions escaped
``execute`` and bubbled to the generic base wrapper, so the agent received a
crash instead of an actionable OBSERVATION. Every atomic browser tool must
catch its own expected Playwright/session failures and return
``ToolResult(success=False, error=...)`` so the base wrapper is never reached.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.browser.tools import (
    BrowserClickTool,
    BrowserCookiesGetTool,
    BrowserDownloadTool,
    BrowserExtractTool,
    BrowserScreenshotTool,
    BrowserScrollTool,
    BrowserTabListTool,
    BrowserTypeTool,
    BrowserUploadTool,
    BrowserWaitForTool,
)


class _RaisingPage:
    """A page whose every action raises a Playwright Error."""

    url = "https://example.test/page"

    def __init__(self, exc: BaseException | None = None) -> None:
        self._exc = exc or PlaywrightError("stale element / detached frame")

    def __getattr__(self, _name: str) -> Any:
        async def _boom(*_a: Any, **_k: Any) -> Any:
            raise self._exc

        return _boom

    def expect_download(self) -> Any:  # async context manager that blows up on enter
        page = self

        class _CM:
            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *_a: Any) -> None:
                return None

            async def click(self, *_a: Any, **_k: Any) -> Any:
                raise page._exc

        return _CM()


class _SessionsGetPageRaises:
    """get_page / get raise — mirrors a purged/stale session."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def get_page(self, *_a: Any, **_k: Any) -> Any:
        raise self._exc

    async def get(self, *_a: Any, **_k: Any) -> Any:
        raise self._exc


class _SessionsWithRaisingPage:
    """get_page succeeds but the returned page raises on every call."""

    def __init__(self, page: _RaisingPage) -> None:
        self._page = page

    async def get_page(self, *_a: Any, **_k: Any) -> Any:
        return object(), self._page, "h1"

    async def get(self, *_a: Any, **_k: Any) -> Any:
        # session-level cookie/tab ops read .context off the session
        sess = type("S", (), {})()
        page = self._page

        class _Ctx:
            async def cookies(self, *_a: Any, **_k: Any) -> Any:
                raise page._exc

        sess.context = _Ctx()
        sess.pages = {}
        return sess


class _FakeRuntime:
    class _Settings:
        from pathlib import Path as _P

        screenshots_dir = _P("/tmp/_so_shots")
        downloads_dir = _P("/tmp/_so_dl")

    settings = _Settings()


def _services(sessions: Any) -> StepServices:
    return StepServices(
        browser_runtime=_FakeRuntime(),  # type: ignore[arg-type]
        browser_sessions=sessions,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# get_page raises (purged/stale session) → structured failure, no raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        (BrowserTypeTool(), {"session_id": "s1", "selector": "#q", "text": "hi"}),
        (BrowserClickTool(), {"session_id": "s1", "selector_or_text": ".btn"}),
        (BrowserExtractTool(), {"session_id": "s1"}),
        (BrowserScreenshotTool(), {"session_id": "s1"}),
        (BrowserScrollTool(), {"session_id": "s1"}),
        (BrowserWaitForTool(), {"session_id": "s1", "selector": "#x"}),
        (BrowserUploadTool(), {"session_id": "s1", "selector": "#f", "file_path": __file__}),
        (BrowserDownloadTool(), {"session_id": "s1", "trigger_selector": ".dl"}),
        (BrowserCookiesGetTool(), {"session_id": "s1"}),
        (BrowserTabListTool(), {"session_id": "s1"}),
    ],
)
async def test_session_lookup_failure_returns_structured_error(tool: Any, kwargs: dict[str, Any]) -> None:
    sessions = _SessionsGetPageRaises(PlaywrightError("Target page, context or browser has been closed"))
    token = set_services(_services(sessions))
    try:
        result = await tool.execute(**kwargs)  # must NOT raise
    finally:
        reset_services(token)
    assert result.success is False
    assert result.error  # non-empty, actionable message for the agent


# ---------------------------------------------------------------------------
# page action raises (stale element / nav error) → structured failure, no raise
# ---------------------------------------------------------------------------


async def test_browser_type_page_action_failure_is_structured() -> None:
    sessions = _SessionsWithRaisingPage(_RaisingPage())
    token = set_services(_services(sessions))
    try:
        result = await BrowserTypeTool().execute(session_id="s1", selector="#q", text="hi")
    finally:
        reset_services(token)
    assert result.success is False
    assert result.error


async def test_browser_download_click_failure_is_structured() -> None:
    """browser_download: page.click inside expect_download raises (the :669 path)."""
    sessions = _SessionsWithRaisingPage(_RaisingPage())
    token = set_services(_services(sessions))
    try:
        result = await BrowserDownloadTool().execute(session_id="s1", trigger_selector=".dl")
    finally:
        reset_services(token)
    assert result.success is False
    assert result.error


async def test_browser_extract_page_action_failure_is_structured() -> None:
    sessions = _SessionsWithRaisingPage(_RaisingPage())
    token = set_services(_services(sessions))
    try:
        result = await BrowserExtractTool().execute(session_id="s1")
    finally:
        reset_services(token)
    assert result.success is False
    assert result.error


async def test_browser_cookies_get_session_failure_is_structured() -> None:
    sessions = _SessionsWithRaisingPage(_RaisingPage())
    token = set_services(_services(sessions))
    try:
        result = await BrowserCookiesGetTool().execute(session_id="s1")
    finally:
        reset_services(token)
    assert result.success is False
    assert result.error
