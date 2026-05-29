"""Tests for browser_back (E2-S2) — thin go_back wrapper, no-history is a no-op."""

from __future__ import annotations

from typing import Any

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.browser.back import BrowserBackTool


class _FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status


class _FakePage:
    def __init__(self, *, back_response: _FakeResponse | None, url: str = "https://a.test/prev") -> None:
        self.url = url
        self._back_response = back_response
        self.go_back_calls: list[dict[str, Any]] = []

    async def go_back(self, *, wait_until: str, timeout: int) -> _FakeResponse | None:
        self.go_back_calls.append({"wait_until": wait_until, "timeout": timeout})
        return self._back_response

    async def title(self) -> str:
        return "Previous Page"


class _FakeRuntime:
    def __init__(self) -> None:
        self.nav_records = 0

    async def record_navigation(self) -> None:
        self.nav_records += 1


class _FakeSessions:
    def __init__(self, page: _FakePage | None, *, raise_on_get: bool = False) -> None:
        self._page = page
        self._raise = raise_on_get

    async def get_page(self, session_id: str, page_handle: str | None = None) -> tuple[Any, Any, str]:
        if self._raise:
            raise RuntimeError("session gone")
        return object(), self._page, page_handle or "h1"


def _services(page: _FakePage | None, *, runtime: object | None = None, raise_on_get: bool = False) -> StepServices:
    return StepServices(
        browser_runtime=runtime or _FakeRuntime(),  # type: ignore[arg-type]
        browser_sessions=_FakeSessions(page, raise_on_get=raise_on_get),  # type: ignore[arg-type]
    )


class TestBrowserBackTool:
    def test_manifest(self) -> None:
        m = BrowserBackTool().manifest
        assert m.action_severity == "read"
        assert m.toolset_group == "browser"
        assert m.name == "browser_back"

    async def test_back_navigates(self) -> None:
        page = _FakePage(back_response=_FakeResponse(200))
        runtime = _FakeRuntime()
        token = set_services(_services(page, runtime=runtime))
        try:
            result = await BrowserBackTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is True
        assert '"navigated": true' in result.output
        assert '"status": 200' in result.output
        assert runtime.nav_records == 1  # navigation recorded for recycle accounting

    async def test_no_history_is_structured_noop(self) -> None:
        page = _FakePage(back_response=None)
        token = set_services(_services(page))
        try:
            result = await BrowserBackTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is True  # no-op, NOT a failure
        assert '"navigated": false' in result.output
        assert "no previous page" in result.output

    async def test_wait_for_forwarded_and_sanitized(self) -> None:
        page = _FakePage(back_response=_FakeResponse())
        token = set_services(_services(page))
        try:
            await BrowserBackTool().execute(session_id="s1", wait_for="load")
            await BrowserBackTool().execute(session_id="s1", wait_for="bogus")
        finally:
            reset_services(token)
        assert page.go_back_calls[0]["wait_until"] == "load"
        assert page.go_back_calls[1]["wait_until"] == "domcontentloaded"  # bad value sanitized

    async def test_go_back_raise_is_structured(self) -> None:
        class _RaisingPage(_FakePage):
            async def go_back(self, *, wait_until: str, timeout: int) -> Any:
                raise TimeoutError("nav timeout")

        token = set_services(_services(_RaisingPage(back_response=None)))
        try:
            result = await BrowserBackTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is False
        assert "go_back failed" in (result.error or "")

    async def test_no_runtime_unavailable(self) -> None:
        token = set_services(StepServices())
        try:
            result = await BrowserBackTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is False

    async def test_dead_session_degrades(self) -> None:
        token = set_services(_services(_FakePage(back_response=None), raise_on_get=True))
        try:
            result = await BrowserBackTool().execute(session_id="dead")
        finally:
            reset_services(token)
        assert result.success is False
        assert "unavailable" in (result.error or "")

    def test_registered(self) -> None:
        from stackowl.tools.registry import ToolRegistry

        assert ToolRegistry.with_defaults().get("browser_back") is not None
