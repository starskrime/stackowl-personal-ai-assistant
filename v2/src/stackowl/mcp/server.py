"""McpServer — exposes StackOwl capabilities over MCP protocol (2024-11-05).

Supports SSE and stdio transports.  Tool list and call handlers are wired to
the process-level ToolRegistry so all registered tools are automatically
available to MCP clients (e.g. Claude Code).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING, Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.mcp.server_settings import McpServerSettings
from stackowl.mcp.sse_encoder import McpSseEncoder
from stackowl.mcp.tool_exposure import McpToolExposurePolicy

if TYPE_CHECKING:
    from stackowl.tools.base import Tool
    from stackowl.tools.registry import ToolRegistry

log = logging.getLogger("stackowl.mcp")


class McpServer:
    """MCP server that bridges StackOwl tools to MCP clients."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        settings: McpServerSettings | None = None,
        global_settings: Any = None,
        event_bus: Any = None,
    ) -> None:
        log.debug("mcp.server.__init__: entry")
        self._registry = tool_registry
        self._settings = settings or McpServerSettings()
        self._global_settings = global_settings
        self._event_bus: Any = event_bus
        self._encoder = McpSseEncoder()
        self._mcp_server: Any = None
        self._extra_capabilities: dict[str, Any] = {}
        self._exposure = McpToolExposurePolicy(
            allow_browser_writes=self._settings.allow_browser_writes,
            allow_consequential=self._settings.allow_consequential,
        )
        self._setup_handlers()
        # Advertise the browser capability so MCP clients can detect support.
        self._extra_capabilities["browser"] = {
            "engines": ["camoufox"],
            "stealth": True,
            "downloads": self._settings.allow_browser_writes,
            "profiles": True,
            "writes_allowed": self._settings.allow_browser_writes,
        }
        log.debug(
            "mcp.server.__init__: exit",
            extra={"_fields": {"name": self._settings.server_name, "transport": self._settings.transport}},
        )

    def _setup_handlers(self) -> None:
        """Register tool list and call handlers on the mcp.Server instance."""
        log.debug("mcp.server._setup_handlers: entry")
        try:
            from mcp.server import Server  # type: ignore[import]
            self._mcp_server = Server(self._settings.server_name)
            _wire_handlers(self._mcp_server, self._registry, self._exposure)
            log.debug("mcp.server._setup_handlers: handlers registered")
        except ImportError as exc:
            log.warning(
                "mcp.server._setup_handlers: mcp package not installed — server is a no-op",
                exc_info=exc,
            )
        log.debug("mcp.server._setup_handlers: exit")

    async def start_stdio(self) -> None:
        """Run the MCP server over stdio (blocking until client disconnects)."""
        log.debug("mcp.server.start_stdio: entry")
        if sys.stdin.isatty():
            msg = "stdio transport requires non-TTY stdin (typically launched by an MCP client process)"
            sys.stderr.write(msg + "\n")
            log.warning("mcp.server.start_stdio: TTY detected — refusing stdio transport")
            sys.exit(2)
        TestModeGuard.assert_not_test_mode("mcp.server.start_stdio")
        if self._mcp_server is None:
            log.warning("mcp.server.start_stdio: mcp package not available — skipping")
            return
        try:
            from mcp.server.stdio import stdio_server  # type: ignore[import]
            log.debug("mcp.server.start_stdio: decision — stdio transport")
            async with stdio_server() as (read_stream, write_stream):
                log.debug("mcp.server.start_stdio: step — starting server loop")
                await self._mcp_server.run(
                    read_stream,
                    write_stream,
                    self._mcp_server.create_initialization_options(),
                )
        except Exception as exc:
            log.error("mcp.server.start_stdio: server error", exc_info=exc)
            raise
        log.debug("mcp.server.start_stdio: exit")

    async def start_sse(self) -> None:
        """Start the MCP server on SSE transport at configured host:port."""
        log.debug("mcp.server.start_sse: entry")
        TestModeGuard.assert_not_test_mode("mcp.server.start_sse")
        if self._mcp_server is None:
            log.warning("mcp.server.start_sse: mcp package not available — skipping")
            return
        host = self._settings.host
        port = self._settings.port
        log.info(
            "mcp.server.start_sse: decision — binding SSE endpoint",
            extra={"_fields": {"host": host, "port": port}},
        )
        try:
            from mcp.server.sse import SseServerTransport  # type: ignore[import]

            sse_transport = SseServerTransport("/sse")

            async def handle_sse(scope: Any, receive: Any, send: Any) -> None:
                async with sse_transport.connect_sse(scope, receive, send) as (r, w):
                    await self._mcp_server.run(
                        r, w, self._mcp_server.create_initialization_options()
                    )

            log.debug("mcp.server.start_sse: step — server bound", extra={"_fields": {"host": host, "port": port}})
            from starlette.applications import Starlette  # type: ignore[import]
            from starlette.routing import Route  # type: ignore[import]
            import uvicorn  # type: ignore[import]

            app = Starlette(routes=[Route("/sse", handle_sse)])
            config = uvicorn.Config(app, host=host, port=port, log_level="warning")
            server = uvicorn.Server(config)
            await server.serve()
        except ImportError as exc:
            log.error("mcp.server.start_sse: missing SSE dependencies", exc_info=exc)
        except Exception as exc:
            log.error("mcp.server.start_sse: server error", exc_info=exc)
            raise
        log.debug("mcp.server.start_sse: exit")

    def negotiate(self, client_capabilities: dict[str, Any]) -> dict[str, Any]:
        """Return ServerCapabilities dict after reviewing client_capabilities."""
        log.debug("mcp.server.negotiate: entry", extra={"_fields": {"client_caps": list(client_capabilities)}})
        tools_list = [
            {"name": t.name, "description": t.description}
            for t in self._exposure.filter_tools(self._registry.all())
        ]
        log.debug("mcp.server.negotiate: decision — building capability dict", extra={"_fields": {"tool_count": len(tools_list)}})
        caps: dict[str, Any] = {
            "protocol_version": "2024-11-05",
            "streaming": True,
            "tools": tools_list,
        }
        gs = self._global_settings
        if gs is not None and hasattr(gs, "parliament"):
            parliament_settings = gs.parliament
            parliament_enabled = getattr(parliament_settings, "enabled", True)
            caps["parliament"] = bool(parliament_enabled)
            log.debug("mcp.server.negotiate: step — parliament capability", extra={"_fields": {"parliament": caps["parliament"]}})
        if gs is not None and hasattr(gs, "memory"):
            memory_settings = gs.memory
            memory_enabled = getattr(memory_settings, "enabled", True)
            caps["memory_search"] = bool(memory_enabled)
            log.debug("mcp.server.negotiate: step — memory_search capability", extra={"_fields": {"memory_search": caps["memory_search"]}})
        caps.update(self._extra_capabilities)
        log.debug("mcp.server.negotiate: exit", extra={"_fields": {"cap_keys": list(caps)}})
        return caps

    def register_capability(self, name: str, schema: dict[str, Any]) -> None:
        """Hot-add a capability to the server's extra capabilities."""
        log.debug("mcp.server.register_capability: entry", extra={"_fields": {"name": name}})
        log.debug("mcp.server.register_capability: decision — adding capability", extra={"_fields": {"capability": name}})
        self._extra_capabilities[name] = schema
        log.debug("mcp.server.register_capability: exit", extra={"_fields": {"name": name, "total_extra": len(self._extra_capabilities)}})

    def register_tool(self, tool: Tool) -> None:
        """Hot-add a tool to the registry and notify MCP clients of capability change."""
        log.debug("mcp.server.register_tool: entry", extra={"_fields": {"tool": tool.name}})
        self._registry.register(tool)
        log.debug("mcp.server.register_tool: decision — tool registered, checking for live mcp server")
        if self._mcp_server is not None:
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(
                    self._send_capabilities_changed()
                )
                log.debug("mcp.server.register_tool: step — scheduled notifications/capabilities/changed")
            except RuntimeError as exc:
                log.debug("mcp.server.register_tool: step — no event loop, skipping notification", exc_info=exc)
        log.debug("mcp.server.register_tool: exit", extra={"_fields": {"tool": tool.name}})

    async def _send_capabilities_changed(self) -> None:
        """Send MCP notifications/capabilities/changed to all connected clients."""
        try:
            if hasattr(self._mcp_server, "send_notification"):
                await self._mcp_server.send_notification("notifications/capabilities/changed", {})
                log.debug("mcp.server._send_capabilities_changed: notification sent")
            else:
                log.debug("mcp.server._send_capabilities_changed: mcp_server has no send_notification method")
        except Exception as exc:
            log.error("mcp.server._send_capabilities_changed: failed to send notification", exc_info=exc)

    def _emit_spectator_active(self, client_id: str, client_name: str, transport: str) -> None:
        """Publish a spectator_active event on the event bus (no-op if bus is None)."""
        log.debug("mcp.server._emit_spectator_active: entry", extra={"_fields": {"client_id": client_id, "transport": transport}})
        if self._event_bus is None:
            log.debug("mcp.server._emit_spectator_active: decision — no event bus, skipping")
            return
        log.debug("mcp.server._emit_spectator_active: decision — publishing event")
        try:
            self._event_bus.publish({
                "event": "mcp_spectator_active",
                "client_id": client_id,
                "client_name": client_name,
                "transport": transport,
            })
            log.debug("mcp.server._emit_spectator_active: exit — published")
        except Exception as exc:
            log.error("mcp.server._emit_spectator_active: failed to publish event", exc_info=exc)

    def _emit_spectator_disconnected(self, client_id: str) -> None:
        """Publish a spectator_disconnected event on the event bus (no-op if bus is None)."""
        log.debug("mcp.server._emit_spectator_disconnected: entry", extra={"_fields": {"client_id": client_id}})
        if self._event_bus is None:
            log.debug("mcp.server._emit_spectator_disconnected: decision — no event bus, skipping")
            return
        log.debug("mcp.server._emit_spectator_disconnected: decision — publishing event")
        try:
            self._event_bus.publish({
                "event": "mcp_spectator_disconnected",
                "client_id": client_id,
            })
            log.debug("mcp.server._emit_spectator_disconnected: exit — published")
        except Exception as exc:
            log.error("mcp.server._emit_spectator_disconnected: failed to publish event", exc_info=exc)

    def list_tools_response(self) -> list[dict[str, object]]:
        """Return the tool manifest as a list of dicts (for testing without live MCP)."""
        return [
            {"name": t.name, "description": t.description, "inputSchema": t.parameters}
            for t in self._exposure.filter_tools(self._registry.all())
        ]


def _wire_handlers(
    mcp_server: Any, registry: ToolRegistry, exposure: McpToolExposurePolicy,
) -> None:
    """Register list_tools and call_tool handlers on the mcp.Server."""
    from mcp.types import CallToolResult, TextContent, Tool as McpSdkTool  # type: ignore[import]

    @mcp_server.list_tools()  # type: ignore[misc]
    async def _list_tools() -> list[McpSdkTool]:
        return [
            McpSdkTool(name=t.name, description=t.description, inputSchema=t.parameters)
            for t in exposure.filter_tools(registry.all())
        ]

    @mcp_server.call_tool()  # type: ignore[misc]
    async def _call_tool(name: str, arguments: dict[str, object]) -> list[TextContent] | CallToolResult:
        tool = registry.get(name)
        if tool is None:
            log.warning("mcp.server.call_tool: unknown tool", extra={"_fields": {"tool": name}})
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name}")], isError=True
            )
        if not exposure.is_exposed(tool):
            log.warning(
                "mcp.server.call_tool: tool denied by exposure policy",
                extra={"_fields": {"tool": name}},
            )
            return CallToolResult(
                content=[TextContent(type="text", text=exposure.denial_message(name))], isError=True
            )
        result = await tool.execute(**arguments)
        if result.success:
            return [TextContent(type="text", text=result.output)]
        # Surface failures with the MCP error convention so clients can tell a
        # failed tool from a successful empty result; never an empty error text.
        return CallToolResult(
            content=[TextContent(type="text", text=result.error or "tool failed (no detail)")],
            isError=True,
        )
