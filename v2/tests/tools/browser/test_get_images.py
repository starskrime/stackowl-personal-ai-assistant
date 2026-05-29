"""Tests for browser_get_images (E2-S5) — image enumeration + data: filtering."""

from __future__ import annotations

from typing import Any

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.browser.get_images import BrowserGetImagesTool

_THREE_IMAGES = [
    {"src": "https://a.test/1.png", "alt": "one", "width": 100, "height": 50},
    {"src": "https://a.test/2.jpg", "alt": "", "width": 200, "height": 100},
    {"src": "data:image/png;base64,AAAA", "alt": "inline", "width": 1, "height": 1},
]


class _FakePage:
    def __init__(self, images: list[dict[str, Any]]) -> None:
        self._images = images

    async def evaluate(self, script: str) -> list[dict[str, Any]]:
        return self._images


class _FakeSessions:
    def __init__(self, page: _FakePage | None, *, raise_on_get: bool = False) -> None:
        self._page = page
        self._raise = raise_on_get

    async def get_page(self, session_id: str, page_handle: str | None = None) -> tuple[Any, Any, str]:
        if self._raise:
            raise RuntimeError("gone")
        return object(), self._page, page_handle or "h1"


def _services(page: _FakePage | None, *, raise_on_get: bool = False) -> StepServices:
    return StepServices(
        browser_runtime=object(),  # type: ignore[arg-type]
        browser_sessions=_FakeSessions(page, raise_on_get=raise_on_get),  # type: ignore[arg-type]
    )


class TestBrowserGetImagesTool:
    def test_manifest(self) -> None:
        m = BrowserGetImagesTool().manifest
        assert m.action_severity == "read"
        assert m.toolset_group == "browser"

    async def test_lists_non_data_images(self) -> None:
        token = set_services(_services(_FakePage(_THREE_IMAGES)))
        try:
            result = await BrowserGetImagesTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is True
        assert '"count": 2' in result.output  # data: URI filtered out
        assert "1.png" in result.output
        assert "base64" not in result.output

    async def test_include_data_uris_flag(self) -> None:
        token = set_services(_services(_FakePage(_THREE_IMAGES)))
        try:
            result = await BrowserGetImagesTool().execute(session_id="s1", include_data_uris=True)
        finally:
            reset_services(token)
        assert '"count": 3' in result.output

    async def test_max_count_truncates(self) -> None:
        token = set_services(_services(_FakePage(_THREE_IMAGES)))
        try:
            result = await BrowserGetImagesTool().execute(session_id="s1", max_count=1)
        finally:
            reset_services(token)
        assert '"count": 1' in result.output
        assert '"truncated": true' in result.output

    async def test_max_count_string_is_honored(self) -> None:
        # Models often emit numeric params as JSON strings — must NOT fall back to default.
        token = set_services(_services(_FakePage(_THREE_IMAGES)))
        try:
            result = await BrowserGetImagesTool().execute(session_id="s1", max_count="1")
        finally:
            reset_services(token)
        assert '"count": 1' in result.output
        assert '"truncated": true' in result.output

    async def test_non_list_evaluate_result(self) -> None:
        class _NullPage:
            async def evaluate(self, script: str) -> None:
                return None

        token = set_services(_services(_NullPage()))  # type: ignore[arg-type]
        try:
            result = await BrowserGetImagesTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is True
        assert '"count": 0' in result.output

    async def test_malformed_items_do_not_crash(self) -> None:
        malformed = [
            {"src": "https://a.test/x.png"},  # missing dims/alt
            {"src": "https://a.test/y.png", "width": None, "height": None, "alt": None},
            "not-a-dict",
            {"alt": "no src"},  # dropped (no src)
        ]
        token = set_services(_services(_FakePage(malformed)))  # type: ignore[arg-type]
        try:
            result = await BrowserGetImagesTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is True
        assert '"count": 2' in result.output  # two with a usable src
        assert '"width": 0' in result.output  # None/missing coerced to 0

    async def test_no_images_empty_list(self) -> None:
        token = set_services(_services(_FakePage([])))
        try:
            result = await BrowserGetImagesTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is True
        assert '"count": 0' in result.output

    async def test_only_data_uris_empty(self) -> None:
        only_data = [{"src": "data:image/png;base64,XX", "alt": "", "width": 1, "height": 1}]
        token = set_services(_services(_FakePage(only_data)))
        try:
            result = await BrowserGetImagesTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert '"count": 0' in result.output

    async def test_no_runtime_unavailable(self) -> None:
        token = set_services(StepServices())
        try:
            result = await BrowserGetImagesTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is False

    async def test_dead_session_degrades(self) -> None:
        token = set_services(_services(_FakePage([]), raise_on_get=True))
        try:
            result = await BrowserGetImagesTool().execute(session_id="dead")
        finally:
            reset_services(token)
        assert result.success is False
        assert "unavailable" in (result.error or "")

    def test_registered(self) -> None:
        from stackowl.tools.registry import ToolRegistry

        assert ToolRegistry.with_defaults().get("browser_get_images") is not None
