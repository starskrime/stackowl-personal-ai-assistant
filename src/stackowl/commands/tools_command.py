"""ToolsCommand — /tools slash command listing the registered tool surface.

Closes a long-standing introspection gap: the LLM had a tool list via
``ToolRegistry.to_provider_schema()`` but no user-facing way to see what's
loaded or which tools are flagged consequential.
"""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import register_command
from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState

_SEVERITY_GLYPH = {
    "read": "·",
    "write": "*",
    "consequential": "!",
}


class ToolsCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "tools"

    @property
    def description(self) -> str:
        return "List every registered tool with action severity (· read, * write, ! consequential)."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.gateway.debug(
            "[commands] tools.handle: entry",
            extra={"_fields": {"session": state.session_id}},
        )
        registry = get_services().tool_registry
        if registry is None:
            return "Tool registry unavailable."
        tools = registry.all()
        if not tools:
            return "(no tools registered)"
        lines = [f"Registered tools ({len(tools)}):", ""]
        for t in sorted(tools, key=lambda x: x.name):
            try:
                severity = t.manifest.action_severity
            except Exception as exc:
                log.gateway.warning(
                    "[commands] tools.handle: manifest.action_severity read failed — "
                    "defaulting to 'read' (least-privilege fallback)",
                    exc_info=exc,
                    extra={"_fields": {"tool": t.name}},
                )
                severity = "read"
            glyph = _SEVERITY_GLYPH.get(severity, "?")
            short_desc = t.description.split("\n", 1)[0]
            lines.append(f"  {glyph} {t.name:<28} {short_desc}")
        lines.append("")
        lines.append("Legend: · read   * write   ! consequential (operator confirmation required)")
        log.gateway.debug(
            "[commands] tools.handle: exit",
            extra={"_fields": {"count": len(tools)}},
        )
        return "\n".join(lines)


_CMD = register_command(ToolsCommand())
