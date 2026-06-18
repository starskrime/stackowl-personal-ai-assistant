"""Tests for browser_snapshot (E2-S1) + browser_click(ref=) — engine-native aria refs.

The ref scheme is Playwright's cross-browser ``aria-ref`` selector engine (the
DevTools-protocol scheme was infeasible on the Firefox-derived backend — see
E2-LOCKED-DECISIONS.md). These tests fake the page so they run without a live
browser; a live click-by-ref is proven in the per-story smoke.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.browser.snapshot import (
    _SNAPSHOT_SUMMARIZE_THRESHOLD,
    BrowserSnapshotTool,
    truncate_snapshot,
)
from stackowl.tools.browser.tools import BrowserClickTool

# A small AI-format aria snapshot with two interactive refs + static text.
_SAMPLE_SNAPSHOT = (
    "- document:\n"
    '  - heading "Welcome" [level=1]\n'
    "  - paragraph: Some descriptive static body text on the page.\n"
    '  - button "Submit" [ref=e7]\n'
    '  - link "Home" [ref=e8]\n'
)


class _FakeLocator:
    def __init__(self, page: _FakePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    async def aria_snapshot(self, *, mode: str | None = None, depth: int | None = None) -> str:
        self._page.aria_calls.append({"mode": mode, "depth": depth})
        return self._page.snapshot_text

    async def click(self, *, timeout: float | None = None) -> None:
        self._page.clicked_selectors.append(self._selector)


class _FakePage:
    def __init__(self, snapshot_text: str = _SAMPLE_SNAPSHOT) -> None:
        self.url = "https://example.test/page"
        self.snapshot_text = snapshot_text
        self.aria_calls: list[dict[str, Any]] = []
        self.clicked_selectors: list[str] = []

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)


class _FakeSessions:
    """Minimal stand-in for BrowserSessionRegistry.get_page."""

    def __init__(self, page: _FakePage | None, *, raise_on_get: bool = False) -> None:
        self._page = page
        self._raise = raise_on_get

    async def get_page(self, session_id: str, page_handle: str | None = None) -> tuple[Any, Any, str]:
        if self._raise:
            raise RuntimeError("session purged by runtime recycle")
        return object(), self._page, page_handle or "h1"


def _services(page: _FakePage | None, *, runtime: object | None = object(), raise_on_get: bool = False) -> StepServices:
    return StepServices(
        browser_runtime=runtime,  # type: ignore[arg-type]
        browser_sessions=_FakeSessions(page, raise_on_get=raise_on_get),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- truncate helper


class TestTruncateSnapshot:
    def test_under_threshold_is_untouched(self) -> None:
        r = truncate_snapshot(_SAMPLE_SNAPSHOT, threshold=10_000)
        assert r.text == _SAMPLE_SNAPSHOT
        assert r.truncated is False
        assert r.dropped_static_lines == 0
        assert r.refs_kept == 2  # authoritative ref count, no re-scan
        assert r.refs_omitted == 0

    def test_over_threshold_preserves_every_ref(self) -> None:
        # Build a page that is mostly static filler with a couple of refs near the end.
        filler = "\n".join(f"  - text: static line {i} padding padding padding" for i in range(500))
        big = filler + '\n  - button "Buy" [ref=e42]\n  - link "Next" [ref=e43]\n'
        r = truncate_snapshot(big, threshold=2_000)
        assert r.truncated is True
        assert r.dropped_static_lines > 0
        assert r.refs_omitted == 0  # refs kept, only static dropped
        # Both interactive refs survive even though they came AFTER the budget ran out.
        assert "[ref=e42]" in r.text
        assert "[ref=e43]" in r.text
        # And the omission is announced, never silent.
        assert "omitted" in r.text

    def test_empty_input(self) -> None:
        r = truncate_snapshot("", threshold=100)
        assert r.text == ""
        assert r.truncated is False
        assert r.dropped_static_lines == 0
        assert r.refs_kept == 0

    def test_all_ref_page_is_bounded_with_visible_marker(self) -> None:
        # A pathological all-interactive page must NOT be unbounded: beyond
        # max_refs, ref lines are dropped too — but loudly, never silently.
        big = "\n".join(f'  - button "B{i}" [ref=e{i}]' for i in range(1_000))
        r = truncate_snapshot(big, threshold=2_000, max_refs=50)
        assert r.truncated is True
        assert r.refs_kept == 50  # hard cap honoured
        assert r.refs_omitted == 950  # surfaced as a structured field, not just text
        assert "interactive refs omitted" in r.text  # announced, not silent

    def test_marker_does_not_balloon_static_budget(self) -> None:
        # Static text stays within threshold (the marker reserve prevents overshoot).
        body = "\n".join(f"  - text: filler line {i}" for i in range(2_000))
        r = truncate_snapshot(body, threshold=1_000)
        assert r.truncated is True
        assert len(r.text) <= 1_000


# --------------------------------------------------------------------------- snapshot tool


class TestBrowserSnapshotTool:
    def test_manifest_is_read_and_grouped(self) -> None:
        m = BrowserSnapshotTool().manifest
        assert m.action_severity == "read"
        assert m.toolset_group == "browser"
        assert m.name == "browser_snapshot"

    async def test_happy_returns_refs(self) -> None:
        page = _FakePage()
        token = set_services(_services(page))
        try:
            result = await BrowserSnapshotTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is True
        assert '"ref_count": 2' in result.output
        assert "[ref=e7]" in result.output
        # AI mode requested with a depth cap.
        assert page.aria_calls[0]["mode"] == "ai"
        assert page.aria_calls[0]["depth"] == 25

    async def test_custom_depth_forwarded(self) -> None:
        page = _FakePage()
        token = set_services(_services(page))
        try:
            await BrowserSnapshotTool().execute(session_id="s1", depth=5)
        finally:
            reset_services(token)
        assert page.aria_calls[0]["depth"] == 5

    async def test_huge_page_truncated_under_budget(self) -> None:
        filler = "\n".join(f"  - text: filler {i}" for i in range(5_000))
        page = _FakePage(snapshot_text=filler + '\n  - button "Go" [ref=e1]\n')
        token = set_services(_services(page))
        try:
            result = await BrowserSnapshotTool().execute(session_id="s1", max_chars=1_000)
        finally:
            reset_services(token)
        assert result.success is True
        assert '"truncated": true' in result.output
        assert "[ref=e1]" in result.output  # interactive ref kept

    async def test_empty_page_minimal_result(self) -> None:
        page = _FakePage(snapshot_text="")
        token = set_services(_services(page))
        try:
            result = await BrowserSnapshotTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is True
        assert '"ref_count": 0' in result.output

    async def test_no_runtime_is_unavailable_not_raise(self) -> None:
        token = set_services(_services(None, runtime=None))
        try:
            result = await BrowserSnapshotTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is False
        assert result.error is not None

    async def test_session_purge_degrades_gracefully(self) -> None:
        token = set_services(_services(_FakePage(), raise_on_get=True))
        try:
            result = await BrowserSnapshotTool().execute(session_id="dead")
        finally:
            reset_services(token)
        assert result.success is False
        assert "unavailable" in (result.error or "")

    def test_default_threshold_constant(self) -> None:
        # Guards against an accidental zero/None default that would truncate everything.
        assert _SNAPSHOT_SUMMARIZE_THRESHOLD > 1_000

    async def test_malformed_depth_does_not_raise(self) -> None:
        # Self-healing: a non-numeric depth must fall back to the default, not raise.
        page = _FakePage()
        token = set_services(_services(page))
        try:
            result = await BrowserSnapshotTool().execute(session_id="s1", depth="abc", max_chars="xyz")
        finally:
            reset_services(token)
        assert result.success is True
        assert page.aria_calls[0]["depth"] == 25  # fell back to default

    async def test_version_degradation_path(self) -> None:
        # Older engine without mode/depth kwargs → fall back to bare aria_snapshot.
        class _OldLocator(_FakeLocator):
            async def aria_snapshot(self, *, mode: str | None = None, depth: int | None = None) -> str:
                if mode is not None or depth is not None:
                    raise TypeError("aria_snapshot() got an unexpected keyword argument")
                return self._page.snapshot_text

        class _OldPage(_FakePage):
            def locator(self, selector: str) -> _OldLocator:
                return _OldLocator(self, selector)

        page = _OldPage()
        token = set_services(_services(page))
        try:
            result = await BrowserSnapshotTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is True
        assert "[ref=e7]" in result.output


# --------------------------------------------------------------------------- click-by-ref


class TestBrowserClickByRef:
    async def test_click_by_ref_uses_aria_ref_selector(self) -> None:
        page = _FakePage()
        token = set_services(_services(page))
        try:
            result = await BrowserClickTool().execute(session_id="s1", ref="e7")
        finally:
            reset_services(token)
        assert result.success is True
        assert '"click_target": "ref"' in result.output
        assert page.clicked_selectors == ["aria-ref=e7"]

    async def test_invalid_ref_rejected_before_click(self) -> None:
        page = _FakePage()
        token = set_services(_services(page))
        try:
            result = await BrowserClickTool().execute(session_id="s1", ref="e7; DROP")
        finally:
            reset_services(token)
        assert result.success is False
        assert "Invalid ref" in (result.error or "")
        assert page.clicked_selectors == []  # never attempted

    async def test_missing_both_targets_errors(self) -> None:
        page = _FakePage()
        token = set_services(_services(page))
        try:
            result = await BrowserClickTool().execute(session_id="s1")
        finally:
            reset_services(token)
        assert result.success is False
        assert "ref" in (result.error or "")

    def test_click_manifest_grouped(self) -> None:
        m = BrowserClickTool().manifest
        assert m.toolset_group == "browser"
        assert m.action_severity == "write"

    def test_ref_and_selector_both_optional_in_schema(self) -> None:
        params = BrowserClickTool().parameters
        assert params["required"] == ["session_id"]
        assert "ref" in params["properties"]


# --------------------------------------------------------------------------- registry integration


class TestRegistryIntegration:
    def test_snapshot_registered_with_defaults(self) -> None:
        from stackowl.tools.registry import ToolRegistry

        reg = ToolRegistry.with_defaults()
        assert reg.get("browser_snapshot") is not None

    @pytest.mark.parametrize("protocol", ["anthropic", "openai"])
    def test_snapshot_in_provider_schema(self, protocol: str) -> None:
        from stackowl.tools.registry import ToolRegistry

        reg = ToolRegistry.with_defaults()
        schema = reg.to_provider_schema(protocol)
        names = [
            (s.get("name") or s.get("function", {}).get("name")) for s in schema
        ]
        assert "browser_snapshot" in names

    def test_browser_profile_groups_snapshot_and_click(self) -> None:
        # A browser-profiled owl sees snapshot + click via the shared toolset group.
        from stackowl.tools.registry import ToolRegistry

        reg = ToolRegistry.with_defaults()
        schema = reg.to_provider_schema("anthropic", profile=["browser"], pins=[])
        names = {s["name"] for s in schema}
        assert "browser_snapshot" in names
        assert "browser_click" in names
