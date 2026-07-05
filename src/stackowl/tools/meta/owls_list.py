"""owls_list — enumerate configured owls (terse, token-cheap).

Mirrors ``skills_list``'s shape for a different domain: owl_build has no
query/list action (create/edit/retire only, per ``OwlBuildSpec``), so a
"check what owls already exist" request had nowhere to go except a failed
owl_build call missing required fields. This is the read-only survey seam —
one terse line per owl (name, role, lifecycle + schedule, model tier) — so
that check can succeed without ever touching owl_build.
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult

_TOOLSET_GROUP = "owl_admin"


class OwlsListTool(Tool):
    """Enumerate configured owls (terse one-line projection)."""

    @property
    def name(self) -> str:
        return "owls_list"

    @property
    def description(self) -> str:
        return (
            "Enumerate the owls (named agent personas) already configured on "
            "this platform, one terse line each (name, role, lifecycle, model "
            "tier, and schedule if scheduled). "
            "LANE: checking WHICH owls already exist before creating, editing, "
            "or delegating to one. "
            "ANTI-LANE: do NOT use this to create/edit/retire an owl (use "
            "owl_build); do NOT use it to hand off a task to an owl (use "
            "delegate_task)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group=_TOOLSET_GROUP,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        log.tool.info("owls_list.execute: entry", extra={"_fields": {}})

        registry = get_services().owl_registry
        if registry is None:
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.warning(
                "owls_list.execute: no owl registry configured",
                extra={"_fields": {"duration_ms": duration_ms}},
            )
            return ToolResult(
                success=False, output="",
                error="owls unavailable: no owl registry is configured",
                duration_ms=duration_ms,
            )

        try:
            owls = registry.list()
            output = self._format(owls)
        except Exception as exc:  # B5 / self-healing — degrade, never raise.
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.error(
                "owls_list.execute: listing failed — degrading to structured error",
                exc_info=exc,
                extra={"_fields": {"duration_ms": duration_ms}},
            )
            return ToolResult(
                success=False, output="",
                error=f"owls unavailable: {type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )

        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "owls_list.execute: exit",
            extra={"_fields": {"success": True, "count": len(owls), "duration_ms": duration_ms}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _format(owls: list[OwlAgentManifest]) -> str:
        if not owls:
            return "(no owls configured)"
        lines = [f"{len(owls)} owl(s):"]
        for m in owls:
            schedule = ""
            if m.lifecycle == "scheduled" and m.trigger is not None:
                schedule = f" — schedule: {m.trigger.schedule}"
            display = m.display_name or m.name
            lines.append(
                f"  - {m.name} ({display})  role: {m.role}  "
                f"tier: {m.model_tier}  lifecycle: {m.lifecycle}{schedule}"
            )
        return "\n".join(lines)
