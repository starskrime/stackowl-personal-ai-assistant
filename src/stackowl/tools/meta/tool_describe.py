"""tool_describe — return a named tool's full schema (the search→describe→call flow).

Provenance: see ``_bmad-output/research/tool-port-analysis.md`` (E1 tool_describe).
The sibling of tool_search: given a tool name, return its description, declared
severity/consent-category, and full JSON-Schema parameters (operator vote: JSON
output — machine-precise, matches the provider tool-schema shape). The schema is
read straight from the tool's own ``parameters`` (no hand-maintained copy).
Read-only; an unknown name returns a structured error, never raises.
"""

from __future__ import annotations

import json
import time

from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolResult

__all__ = ["ToolDescribeTool"]

_SELF_NAME = "tool_describe"


class ToolDescribeTool(Tool):
    """Describe one tool's full parameter schema by name (read-only, self-healing)."""

    @property
    def name(self) -> str:
        return _SELF_NAME

    @property
    def description(self) -> str:
        return (
            "Return the full parameter schema, description and consent severity of a single "
            "tool by name. Use after tool_search to inspect a tool before calling it."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact tool name (as returned by tool_search)."},
            },
            "required": ["name"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        name = str(kwargs.get("name", "")).strip()
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.debug("tool_describe.execute: entry", extra={"_fields": {"name": name}})

        registry = get_services().tool_registry
        # 2. DECISION — self-healing: no registry or unknown name → structured, no raise
        if registry is None:
            log.tool.warning("tool_describe.execute: no tool_registry in services")
            return ToolResult(
                success=False, output="", error="Tool registry is unavailable.",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        tool = registry.get(name)
        if tool is None:
            log.tool.info("tool_describe.execute: unknown tool", extra={"_fields": {"name": name}})
            return ToolResult(
                success=False, output="",
                error=f"No tool named {name!r} is registered. Use tool_search to find available tools.",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        # 3. STEP — assemble the description from the tool's own (Pydantic-derived)
        # schema. Isolated: a broken .manifest override must surface as a
        # structured failure, not an unhandled exception (see tool_search.py for
        # the shared root cause / matching fix).
        try:
            manifest = tool.manifest
        except Exception as exc:
            log.tool.error(
                "tool_describe.execute: manifest access failed",
                exc_info=exc, extra={"_fields": {"name": tool.name}},
            )
            return ToolResult(
                success=False, output="",
                error=f"Tool {name!r} has a broken manifest and cannot be described.",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        payload = {
            "name": tool.name,
            "description": tool.description,
            "action_severity": manifest.action_severity,
            "consent_category": manifest.consent_category,
            "parameters": tool.parameters,
        }
        output = json.dumps(payload, indent=2, ensure_ascii=False)
        # 4. EXIT
        log.tool.info(
            "tool_describe.execute: exit",
            extra={"_fields": {"name": tool.name, "severity": manifest.action_severity}},
        )
        return ToolResult(success=True, output=output, duration_ms=(time.monotonic() - t0) * 1000)
