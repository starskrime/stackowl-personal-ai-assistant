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
