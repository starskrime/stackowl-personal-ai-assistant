"""McpServerAllowlist and McpServerConfig — URI access control for MCP servers."""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("stackowl.mcp")


class McpServerConfig(BaseModel):
    """Frozen Pydantic model describing a single MCP server connection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="Human label, e.g. 'filesystem'")
    uri: str = Field(description="'stdio:///path/to/server' or 'sse://http://host:port/sse'")
    description: str = ""
    enabled: bool = True
    timeout_seconds: float = Field(default=3.0, gt=0.0, description="Liveness probe timeout in seconds")


class McpServerAllowlist:
    """Runtime-mutable allowlist of server URI prefixes.

    Empty list → deny all.  Call add() at runtime for hot registration.
    """

    def __init__(self, allowed_servers: list[str]) -> None:
        # entry — store allowed prefixes
        log.debug(
            "mcp.allowlist.__init__: entry",
            extra={"_fields": {"prefix_count": len(allowed_servers)}},
        )
        self._prefixes: list[str] = list(allowed_servers)
        log.debug(
            "mcp.allowlist.__init__: exit",
            extra={"_fields": {"prefixes": self._prefixes}},
        )

    def is_allowed(self, server_uri: str) -> bool:
        """Return True if server_uri starts with any allowed prefix."""
        # entry
        log.debug(
            "mcp.allowlist.is_allowed: entry",
            extra={"_fields": {"uri_len": len(server_uri)}},
        )
        if not self._prefixes:
            log.debug("mcp.allowlist.is_allowed: deny — empty allowlist")
            return False
        result = any(server_uri.startswith(prefix) for prefix in self._prefixes)
        # exit
        log.debug(
            "mcp.allowlist.is_allowed: exit",
            extra={"_fields": {"allowed": result}},
        )
        return result

    def add(self, prefix: str) -> None:
        """Add a new allowed prefix at runtime (hot registration)."""
        log.debug(
            "mcp.allowlist.add: entry",
            extra={"_fields": {"prefix": prefix}},
        )
        self._prefixes.append(prefix)
        log.debug(
            "mcp.allowlist.add: exit",
            extra={"_fields": {"total_prefixes": len(self._prefixes)}},
        )
