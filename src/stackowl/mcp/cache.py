"""McpToolCache and McpToolDefinition — in-memory TTL cache for MCP tool discovery."""

from __future__ import annotations

import logging
import time

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("stackowl.mcp")


class McpToolDefinition(BaseModel):
    """Frozen descriptor for a single tool discovered from an MCP server."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    server_name: str
    input_schema: dict[str, object] = Field(default_factory=dict)


class McpToolCache:
    """In-memory cache for discovered MCP tools with a configurable TTL."""

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        log.debug(
            "mcp.cache.__init__: entry",
            extra={"_fields": {"ttl_seconds": ttl_seconds}},
        )
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, list[McpToolDefinition]]] = {}
        log.debug("mcp.cache.__init__: exit")

    def get(self, server_name: str) -> list[McpToolDefinition] | None:
        """Return cached tools if within TTL, else None."""
        log.debug(
            "mcp.cache.get: entry",
            extra={"_fields": {"server": server_name}},
        )
        entry = self._store.get(server_name)
        if entry is None:
            log.debug("mcp.cache.get: miss — no entry", extra={"_fields": {"server": server_name}})
            return None
        stored_at, tools = entry
        if time.monotonic() - stored_at > self._ttl:
            log.debug("mcp.cache.get: miss — TTL expired", extra={"_fields": {"server": server_name}})
            return None
        log.debug(
            "mcp.cache.get: hit",
            extra={"_fields": {"server": server_name, "tool_count": len(tools)}},
        )
        return tools

    def put(self, server_name: str, tools: list[McpToolDefinition]) -> None:
        """Store tools with current timestamp."""
        log.debug(
            "mcp.cache.put: entry",
            extra={"_fields": {"server": server_name, "tool_count": len(tools)}},
        )
        self._store[server_name] = (time.monotonic(), list(tools))
        log.debug(
            "mcp.cache.put: exit",
            extra={"_fields": {"server": server_name}},
        )

    def invalidate(self, server_name: str) -> None:
        """Remove a single server's tools from cache."""
        log.debug(
            "mcp.cache.invalidate: entry",
            extra={"_fields": {"server": server_name}},
        )
        removed = self._store.pop(server_name, None)
        log.debug(
            "mcp.cache.invalidate: exit",
            extra={"_fields": {"server": server_name, "removed": removed is not None}},
        )

    def invalidate_all(self) -> None:
        """Clear all cached tools."""
        log.debug("mcp.cache.invalidate_all: entry")
        count = len(self._store)
        self._store.clear()
        log.debug(
            "mcp.cache.invalidate_all: exit",
            extra={"_fields": {"cleared": count}},
        )

    def is_stale(self, server_name: str) -> bool:
        """Return True if no entry exists or TTL has been exceeded."""
        log.debug(
            "mcp.cache.is_stale: entry",
            extra={"_fields": {"server": server_name}},
        )
        entry = self._store.get(server_name)
        if entry is None:
            log.debug("mcp.cache.is_stale: stale — no entry", extra={"_fields": {"server": server_name}})
            return True
        stored_at, _ = entry
        stale = time.monotonic() - stored_at > self._ttl
        log.debug(
            "mcp.cache.is_stale: exit",
            extra={"_fields": {"server": server_name, "stale": stale}},
        )
        return stale
