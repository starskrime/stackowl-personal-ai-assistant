"""McpClientSettings — frozen Pydantic configuration for the MCP client subsystem."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stackowl.mcp.allowlist import McpServerConfig


class McpClientSettings(BaseModel):
    """Configuration for the MCP client subsystem (Epic 10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    servers: tuple[McpServerConfig, ...] = Field(default_factory=tuple)
    tool_cache_ttl_seconds: float = Field(default=300.0, gt=0.0)
    # Prefixes are matched against the FULL server URI (e.g. "sse://http://localhost:8080/sse",
    # "stdio:///path/to/server"). The default permits local stdio servers and
    # localhost SSE only; remote SSE must be added explicitly. (E1-S3 party-mode MAJOR #2 —
    # the prior "http://localhost:" never matched an "sse://..." URI and silently denied SSE.)
    allowed_uri_prefixes: tuple[str, ...] = ("stdio://", "sse://http://localhost:")
    auto_discover_on_startup: bool = True
