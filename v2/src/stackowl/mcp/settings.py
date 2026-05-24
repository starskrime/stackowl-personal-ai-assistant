"""McpClientSettings — frozen Pydantic configuration for the MCP client subsystem."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stackowl.mcp.allowlist import McpServerConfig


class McpClientSettings(BaseModel):
    """Configuration for the MCP client subsystem (Epic 10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    servers: tuple[McpServerConfig, ...] = Field(default_factory=tuple)
    tool_cache_ttl_seconds: float = Field(default=300.0, gt=0.0)
    allowed_uri_prefixes: tuple[str, ...] = ("http://localhost:", "stdio://")
    auto_discover_on_startup: bool = True
