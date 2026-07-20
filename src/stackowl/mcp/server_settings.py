"""McpServerSettings — frozen Pydantic configuration for the MCP server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class McpServerSettings(BaseModel):
    """Configuration for the MCP server subsystem (Epic 10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    transport: Literal["sse", "stdio", "both"] = "sse"
    server_name: str = "stackowl"
    server_version: str = "2.0.0"
    max_connections: int = Field(default=10, ge=1)
    capability_negotiation_timeout_ms: int = Field(default=500, ge=0)
    allow_browser_writes: bool = Field(
        default=False,
        description=(
            "When False (default), the MCP-exposed tool surface denies "
            "consequential browser tools (eval_js, upload, download, "
            "cookies_set/clear, storage_set, close, set_proxy, tab_close, "
            "click, type, scroll, and the browse meta-tool). Set True only "
            "when the MCP client is trusted."
        ),
    )
    allow_consequential: bool = Field(
        default=False,
        description=(
            "When False (default), ANY consequential tool (e.g. send_message, "
            "skill_manage) is denied across the MCP boundary — the headless MCP "
            "server has no interactive consent channel, so a consequential tool "
            "would run ungated for an external client. Set True only when the MCP "
            "client is fully trusted to invoke consequential actions without consent."
        ),
    )
    auth_token: str | None = Field(
        default=None,
        description=(
            "FX-06 — shared-secret token required from SSE clients as "
            "'Authorization: Bearer <token>'. The SSE transport binds to a real "
            "network host:port with no other gate (no interactive consent channel "
            "exists for an external client), so an unset token means ANY client "
            "that can reach the port has the exposed tool surface. None (default) "
            "leaves the transport unauthenticated for backward compatibility with "
            "existing configs — a loud warning is logged on start_sse either way. "
            "Sensitive: auto-redacted in logs by the *token key-pattern."
        ),
    )
