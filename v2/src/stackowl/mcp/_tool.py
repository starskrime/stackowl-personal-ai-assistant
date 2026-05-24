"""McpTool — Tool ABC wrapper for MCP-discovered tools."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from stackowl.mcp.cache import McpToolDefinition
from stackowl.mcp.allowlist import McpServerConfig
from stackowl.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from stackowl.mcp.client import McpClient

log = logging.getLogger("stackowl.mcp")


class McpTool(Tool):
    """Wrapper that exposes an MCP-discovered tool through the standard Tool interface."""

    def __init__(
        self,
        definition: McpToolDefinition,
        client: McpClient,
        server_config: McpServerConfig,
    ) -> None:
        self._definition = definition
        self._client = client
        self._server_config = server_config

    @property
    def name(self) -> str:
        return self._definition.name

    @property
    def description(self) -> str:
        return self._definition.description

    @property
    def parameters(self) -> dict[str, object]:
        return dict(self._definition.input_schema)

    async def execute(self, **kwargs: object) -> ToolResult:
        log.debug(
            "mcp_tool.execute: entry",
            extra={"_fields": {"tool": self.name, "arg_keys": list(kwargs.keys())}},
        )
        t0 = time.monotonic()
        try:
            result_str = await self._client.call_tool(
                self._server_config, self.name, dict(kwargs)
            )
            duration_ms = (time.monotonic() - t0) * 1000
            log.debug(
                "mcp_tool.execute: exit",
                extra={"_fields": {"tool": self.name, "duration_ms": duration_ms}},
            )
            return ToolResult(success=True, output=result_str, duration_ms=duration_ms)
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.error(
                "mcp_tool.execute: call failed",
                exc_info=exc,
                extra={"_fields": {"tool": self.name, "duration_ms": duration_ms}},
            )
            return ToolResult(success=False, output="", error=str(exc), duration_ms=duration_ms)
