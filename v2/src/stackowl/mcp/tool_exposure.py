"""McpToolExposurePolicy — filter which registry tools are visible / callable via MCP.

External MCP clients (Claude Desktop, Cursor, Cline) can compose atomic browser
tools to do anything. Consequential operations (eval_js, downloads, form fills,
the inner-LLM browse meta-tool) need an explicit operator opt-in to cross the
MCP boundary, even when the local pipeline LLM may use them freely.
"""

from __future__ import annotations

import logging

from stackowl.tools.base import Tool

log = logging.getLogger("stackowl.mcp")

# Tool names that always require allow_browser_writes=True to traverse the MCP boundary.
DEFAULT_MCP_BROWSER_DENYLIST: frozenset[str] = frozenset({
    "browser_browse",
    "browser_eval_js",
    "browser_upload",
    "browser_download",
    "browser_cookies_set",
    "browser_cookies_clear",
    "browser_storage_set",
    "browser_close",
    "browser_set_proxy",
    "browser_tab_close",
    "browser_click",
    "browser_type",
    "browser_scroll",
})


class McpToolExposurePolicy:
    """Decides whether a Tool may be listed/called via MCP."""

    def __init__(
        self,
        *,
        allow_browser_writes: bool = False,
        allow_consequential: bool = False,
        extra_denylist: frozenset[str] = frozenset(),
    ) -> None:
        self._allow_browser_writes = allow_browser_writes
        self._allow_consequential = allow_consequential
        self._extra = extra_denylist
        log.debug(
            "mcp.tool_exposure.__init__: ready",
            extra={"_fields": {
                "allow_browser_writes": allow_browser_writes,
                "allow_consequential": allow_consequential,
                "extra_deny_count": len(extra_denylist),
            }},
        )

    def is_exposed(self, tool: Tool) -> bool:
        name = tool.name
        if name in self._extra:
            return False
        # Severity-aware boundary: the MCP server is headless — there is no
        # interactive consent channel out here, so a `consequential` tool would
        # run UNGATED for an external MCP client. Deny it unless the operator has
        # explicitly opted in. This protects every consequential tool, not just
        # the browser denylist (which predates the severity field).
        if tool.manifest.action_severity == "consequential" and not self._allow_consequential:
            return False
        return not (name in DEFAULT_MCP_BROWSER_DENYLIST and not self._allow_browser_writes)

    def filter_tools(self, tools: list[Tool]) -> list[Tool]:
        return [t for t in tools if self.is_exposed(t)]

    def denial_message(self, name: str) -> str:
        return (
            f"Tool '{name}' is not exposed over MCP. Consequential tools (and the "
            f"browser-write set) are denied across the MCP boundary by default — "
            f"the operator must opt in (mcp_server.allow_consequential / "
            f"allow_browser_writes in stackowl.yaml) to enable them for MCP clients."
        )
