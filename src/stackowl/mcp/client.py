"""McpClient — connects to MCP servers, discovers and invokes tools."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from stackowl.config.test_mode import TestModeGuard
from stackowl.mcp._tool import McpTool
from stackowl.mcp.allowlist import McpServerAllowlist, McpServerConfig
from stackowl.mcp.cache import McpToolCache, McpToolDefinition
from stackowl.mcp.probe import McpLivenessProbe

if TYPE_CHECKING:
    from stackowl.tools.registry import ToolRegistry

log = logging.getLogger("stackowl.mcp")

__all__ = ["McpCallError", "McpClient", "McpTool"]

# Failure kinds carried by McpCallError so the caller can react differently
# (transport/init failures may be retried; a blocked call never is).
McpCallErrorKind = str  # one of: "transport", "blocked", "not_installed"


class McpCallError(Exception):
    """Typed failure of an MCP tool call.

    F-82 (S1): the client used to swallow every exception and ``return ""``, so a
    failed/blocked call was indistinguishable from a genuinely empty-but-successful
    result. Raising this instead lets ``McpTool.execute`` report ``success=False``
    with an actionable error rather than a false empty success.

    ``kind`` is one of: ``"transport"`` (connection/init/call failure — retryable),
    ``"blocked"`` (allowlist-denied — never retried), ``"not_installed"`` (the mcp
    SDK is unavailable).
    """

    def __init__(self, kind: McpCallErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class McpClient:
    """Manages connections to external MCP servers, discovers tools, and executes calls."""

    def __init__(
        self,
        allowlist: McpServerAllowlist,
        cache: McpToolCache,
        probe: McpLivenessProbe,
    ) -> None:
        log.debug("mcp.client.__init__: entry")
        self._allowlist = allowlist
        self._cache = cache
        self._probe = probe
        log.debug("mcp.client.__init__: exit")

    async def discover_tools(self, config: McpServerConfig) -> list[McpToolDefinition]:
        """Discover tools from an MCP server, using cache when fresh."""
        log.debug("mcp.client.discover_tools: entry", extra={"_fields": {"server": config.name}})
        TestModeGuard.assert_not_test_mode("mcp.discover_tools")
        if not self._allowlist.is_allowed(config.uri):
            log.warning("mcp.client.discover_tools: not in allowlist", extra={"_fields": {"server": config.name}})
            return []
        cached = self._cache.get(config.name)
        if cached is not None:
            log.debug("mcp.client.discover_tools: cache hit", extra={"_fields": {"server": config.name, "count": len(cached)}})
            return cached
        tools = await self._fetch_tools(config)
        self._cache.put(config.name, tools)
        log.debug("mcp.client.discover_tools: exit", extra={"_fields": {"server": config.name, "count": len(tools)}})
        return tools

    async def _fetch_tools(self, config: McpServerConfig) -> list[McpToolDefinition]:
        """Connect to the MCP server and retrieve the tool list."""
        try:
            from mcp.client.session import ClientSession  # type: ignore[import]
            from mcp.client.sse import sse_client  # type: ignore[import]
            from mcp.client.stdio import stdio_client  # type: ignore[import]
        except ImportError as exc:
            log.error("mcp.client._fetch_tools: mcp not installed", exc_info=exc, extra={"_fields": {"server": config.name}})
            return []
        uri = config.uri
        try:
            if uri.startswith("sse://"):
                async with sse_client(uri[len("sse://"):]) as (r, w):
                    async with ClientSession(r, w) as session:
                        await session.initialize()
                        result = await session.list_tools()
                        return self._convert_tools(result.tools, config.name)
            elif uri.startswith("stdio://"):
                async with stdio_client(uri[len("stdio://"):]) as (r, w):
                    async with ClientSession(r, w) as session:
                        await session.initialize()
                        result = await session.list_tools()
                        return self._convert_tools(result.tools, config.name)
            else:
                log.warning("mcp.client._fetch_tools: unknown scheme", extra={"_fields": {"server": config.name}})
                return []
        except Exception as exc:
            log.error("mcp.client._fetch_tools: connection failed", exc_info=exc, extra={"_fields": {"server": config.name}})
            return []

    def _convert_tools(self, mcp_tools: list[object], server_name: str) -> list[McpToolDefinition]:
        """Convert MCP SDK tool objects to McpToolDefinition."""
        result: list[McpToolDefinition] = []
        for t in mcp_tools:
            try:
                schema = getattr(t, "inputSchema", {}) or {}
                result.append(McpToolDefinition(
                    name=str(getattr(t, "name", "")),
                    description=str(getattr(t, "description", "")),
                    server_name=server_name,
                    input_schema=dict(schema),
                ))
            except Exception as exc:
                log.error("mcp.client._convert_tools: skip malformed", exc_info=exc, extra={"_fields": {"server": server_name}})
        return result

    async def call_tool(self, config: McpServerConfig, tool_name: str, args: dict[str, object]) -> str:
        """Call a named tool on an MCP server and return its result as string.

        On failure this raises :class:`McpCallError` (F-82, S1) rather than returning
        ``""`` — so a transport/blocked failure can never masquerade as a genuinely
        empty-but-successful result. A transport failure is retried once (bounded)
        before surfacing; a blocked call is never retried.
        """
        log.debug("mcp.client.call_tool: entry", extra={"_fields": {"server": config.name, "tool": tool_name, "arg_keys": list(args.keys())}})
        TestModeGuard.assert_not_test_mode("mcp.call_tool")
        if not self._allowlist.is_allowed(config.uri):
            log.warning("mcp.client.call_tool: not in allowlist", extra={"_fields": {"server": config.name}})
            raise McpCallError("blocked", f"server '{config.name}' is not in the MCP allowlist")
        t0 = time.monotonic()
        try:
            result_str = await self._invoke_tool(config, tool_name, args)
        except McpCallError as exc:
            # Bounded retry-once on a transport failure only — blocked/not_installed
            # are deterministic and never retried.
            if exc.kind == "transport":
                log.warning("mcp.client.call_tool: transport failure, retrying once", extra={"_fields": {"server": config.name, "tool": tool_name}})
                result_str = await self._invoke_tool(config, tool_name, args)
            else:
                raise
        log.info("mcp.client.call_tool: exit", extra={"_fields": {"server": config.name, "tool": tool_name, "execution_ms": (time.monotonic() - t0) * 1000}})
        return result_str

    async def _invoke_tool(self, config: McpServerConfig, tool_name: str, args: dict[str, object]) -> str:
        """Inner tool invocation — wraps MCP SDK call_tool, raising on failure."""
        try:
            from mcp.client.session import ClientSession  # type: ignore[import]  # noqa: F401
            from mcp.client.sse import sse_client  # type: ignore[import]  # noqa: F401
            from mcp.client.stdio import stdio_client  # type: ignore[import]  # noqa: F401
        except ImportError as exc:
            log.error("mcp.client._invoke_tool: mcp not installed", exc_info=exc, extra={"_fields": {"server": config.name}})
            raise McpCallError("not_installed", "the 'mcp' SDK is not installed") from exc
        uri = config.uri
        if not (uri.startswith("sse://") or uri.startswith("stdio://")):
            log.warning("mcp.client._invoke_tool: unknown scheme", extra={"_fields": {"server": config.name}})
            raise McpCallError("transport", f"unsupported MCP URI scheme for server '{config.name}'")
        try:
            return await self._invoke_once(config, tool_name, args)
        except Exception as exc:
            log.error("mcp.client._invoke_tool: call failed", exc_info=exc, extra={"_fields": {"server": config.name, "tool": tool_name}})
            raise McpCallError("transport", f"MCP call to '{tool_name}' on '{config.name}' failed: {exc}") from exc

    async def _invoke_once(self, config: McpServerConfig, tool_name: str, args: dict[str, object]) -> str:
        """Single transport attempt against the MCP server (no retry, no catch)."""
        from mcp.client.session import ClientSession  # type: ignore[import]
        from mcp.client.sse import sse_client  # type: ignore[import]
        from mcp.client.stdio import stdio_client  # type: ignore[import]

        uri = config.uri
        if uri.startswith("sse://"):
            async with sse_client(uri[len("sse://"):]) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    return _extract_content(await session.call_tool(tool_name, args))
        async with stdio_client(uri[len("stdio://"):]) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                return _extract_content(await session.call_tool(tool_name, args))

    async def register_server_tools(self, config: McpServerConfig, tool_registry: ToolRegistry) -> int:
        """Discover tools and register each as McpTool in tool_registry."""
        log.debug("mcp.client.register_server_tools: entry", extra={"_fields": {"server": config.name}})
        tools = await self.discover_tools(config)
        count = 0
        for definition in tools:
            tool = McpTool(definition, self, config)
            # Fail-soft per tool: a name collision (assert-unique registry) or any
            # registration refusal skips that one tool, never the whole server.
            try:
                tool_registry.register(tool)
                count += 1
            except Exception as exc:
                log.warning(
                    "mcp.client.register_server_tools: skipped tool (collision/refused)",
                    exc_info=exc,
                    extra={"_fields": {"server": config.name, "tool": tool.name}},
                )
        log.debug(
            "mcp.client.register_server_tools: exit",
            extra={"_fields": {"server": config.name, "registered": count}},
        )
        return count


def _extract_content(resp: object) -> str:
    """Extract string content from an MCP call_tool response."""
    content = getattr(resp, "content", None)
    if content is None:
        return str(resp)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(str(text))
        return "\n".join(parts)
    return str(content)
