"""MCP response Pydantic schemas — field-1-first ordering (content always leads).

The SSE field-order invariant: the first key in every JSON payload must be
``content`` so that streaming MCP clients can begin rendering before the full
JSON object arrives.  Tests in tests/mcp/test_mcp_schemas.py enforce this.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SynthesisResponse(BaseModel):
    """Parliament synthesis result delivered over MCP."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str  # field 1 — synthesis text (must be first)
    consensus: str = ""
    recommendation: str = ""
    disagreement: str = ""
    confidence: float = 0.0
    round_count: int = 0
    owl_names: tuple[str, ...] = Field(default_factory=tuple)


class BriefResponse(BaseModel):
    """Morning brief delivered over MCP."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str  # field 1 — formatted brief text
    sections: tuple[str, ...] = Field(default_factory=tuple)
    date: str = ""


class AgentStatusResponse(BaseModel):
    """Background agent status delivered over MCP."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str  # field 1 — human-readable status summary
    job_id: str = ""
    handler_name: str = ""
    status: str = ""
    last_run_at: str = ""


class ToolCallResponse(BaseModel):
    """Tool execution result delivered over MCP."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str  # field 1 — tool output
    tool_name: str = ""
    success: bool = True
    error: str = ""
    duration_ms: float = 0.0
