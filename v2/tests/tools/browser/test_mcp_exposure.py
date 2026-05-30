"""Tests for MCP tool-exposure policy — default deny consequential browser tools."""

from __future__ import annotations

from typing import Any

from stackowl.mcp.tool_exposure import (
    DEFAULT_MCP_BROWSER_DENYLIST,
    McpToolExposurePolicy,
)


class _StubManifest:
    def __init__(self, action_severity: str) -> None:
        self.action_severity = action_severity


class _StubTool:
    def __init__(self, name: str, action_severity: str = "read") -> None:
        self._name = name
        self._severity = action_severity

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return ""

    @property
    def parameters(self) -> dict[str, Any]:
        return {}

    @property
    def manifest(self) -> _StubManifest:
        return _StubManifest(self._severity)


class TestDefaultDeny:
    def test_browse_meta_denied_by_default(self) -> None:
        policy = McpToolExposurePolicy()
        assert not policy.is_exposed(_StubTool("browser_browse"))

    def test_eval_js_denied_by_default(self) -> None:
        policy = McpToolExposurePolicy()
        assert not policy.is_exposed(_StubTool("browser_eval_js"))

    def test_web_fetch_always_allowed(self) -> None:
        policy = McpToolExposurePolicy()
        assert policy.is_exposed(_StubTool("web_fetch"))

    def test_browser_navigate_allowed(self) -> None:
        # navigate is read-severity → exposed.
        policy = McpToolExposurePolicy()
        assert policy.is_exposed(_StubTool("browser_navigate"))


class TestOptIn:
    def test_allow_browser_writes_unlocks_browse(self) -> None:
        policy = McpToolExposurePolicy(allow_browser_writes=True)
        assert policy.is_exposed(_StubTool("browser_browse"))
        assert policy.is_exposed(_StubTool("browser_eval_js"))

    def test_extra_deny_list_always_blocks(self) -> None:
        policy = McpToolExposurePolicy(
            allow_browser_writes=True,
            extra_denylist=frozenset({"web_fetch"}),
        )
        assert not policy.is_exposed(_StubTool("web_fetch"))


class TestConsequentialDeny:
    """A consequential tool must not cross the headless MCP boundary by default."""

    def test_consequential_tool_denied_by_default(self) -> None:
        policy = McpToolExposurePolicy()
        # e.g. send_message / skill_manage — consequential, NOT in the browser denylist.
        assert not policy.is_exposed(_StubTool("send_message", action_severity="consequential"))

    def test_allow_consequential_opt_in_exposes(self) -> None:
        policy = McpToolExposurePolicy(allow_consequential=True)
        assert policy.is_exposed(_StubTool("send_message", action_severity="consequential"))

    def test_read_and_write_tools_unaffected(self) -> None:
        policy = McpToolExposurePolicy()
        assert policy.is_exposed(_StubTool("web_fetch", action_severity="read"))
        assert policy.is_exposed(_StubTool("edit", action_severity="write"))


class TestFilterTools:
    def test_returns_only_allowed(self) -> None:
        policy = McpToolExposurePolicy()
        tools = [_StubTool("web_fetch"), _StubTool("browser_browse"), _StubTool("browser_navigate")]
        filtered = policy.filter_tools(tools)  # type: ignore[arg-type]
        names = {t.name for t in filtered}
        assert "browser_browse" not in names
        assert names == {"web_fetch", "browser_navigate"}


class TestDenialMessage:
    def test_mentions_setting_name(self) -> None:
        policy = McpToolExposurePolicy()
        msg = policy.denial_message("browser_browse")
        assert "allow_browser_writes" in msg
        assert "browser_browse" in msg


class TestDenylistShape:
    def test_consequential_set_includes_destructive_tools(self) -> None:
        # Sanity: the denylist must contain at least the destructive set.
        for tool in ("browser_eval_js", "browser_upload", "browser_download", "browser_browse"):
            assert tool in DEFAULT_MCP_BROWSER_DENYLIST
