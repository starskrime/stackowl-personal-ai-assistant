"""owls_list — enumerate configured owls and what each is doing.

A04.1 ADDED THE RUNTIME HALF, and it landed here rather than on a new tool
because this is already THE list surface and A04.1's title is "an agent card as
the one source for list, route and status". Each line now carries `in-flight`
and `pending` (from ``tasks``, which has a status column) beside `turns/3d`
(from ``task_outcomes``, which has none and holds only terminal records). They
are rendered as separate named facts, NEVER fused into one word like "idle": a
stage owl running inside another owl's turn legitimately holds no task row, and
calling that idle would assert liveness from history.

Mirrors ``skills_list``'s shape for a different domain: owl_build has no
query/list action (create/edit/retire only, per ``OwlBuildSpec``), so a
"check what owls already exist" request had nowhere to go except a failed
owl_build call missing required fields. This is the read-only survey seam —
one terse line per owl (name, role, lifecycle + schedule, model tier) — so
that check can succeed without ever touching owl_build.


Registration note (moved verbatim from ToolRegistry.with_defaults by D05.1,
when auto-discovery replaced the hand-written register() calls; the rationale
belongs with the tool, not with the line that used to construct it):

    owls_list — read-only survey of already-configured owls (mirrors
    skills_list), so a "check what owls exist" request never has to
    misuse owl_build's create/edit/retire-only surface just to look.
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.owls.activity import OwlActivity, read_owl_activity
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.pipeline.services import get_services
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.base import Tool, ToolManifest, ToolResult

#: How far back `recent turns` looks. Three days is the window the operator's own
#: activity actually spans — MEASURED 2026-09-11, eight of eleven owls have at
#: least one outcome inside it and the quietest (english_tutor, mailbutler) have
#: exactly one, so it separates "quiet" from "silent" without being so wide that
#: every owl looks busy.
_ACTIVITY_WINDOW_S = 3 * 24 * 60 * 60

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
            "this platform, one terse line each: name, role, lifecycle, model "
            "tier, schedule if scheduled, and what each is DOING — in-flight and "
            "pending durable tasks, and turns completed in the last three days. "
            "LANE: checking WHICH owls already exist, and which are busy or "
            "quiet, before creating, editing, or delegating to one. "
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
            activity = await self._activity(owls)
            output = self._format(owls, activity)
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
    async def _activity(owls: list[OwlAgentManifest]) -> dict[str, OwlActivity]:
        """Runtime facts per owl, or an empty map when no db is wired.

        DEGRADES TO CONFIGURATION, never to an error: this tool's job is to say
        which owls exist, and it answered that for weeks before it could say
        anything about now. A read failure here must not cost the caller the
        list — that is the "one corrupt owl must not cost the user the other
        eleven" rule `OwlStore.list_all` already states, one level up.
        """
        db = get_services().db_pool
        if db is None:
            log.tool.info(
                "owls_list._activity: no db wired — listing configuration only",
                extra={"_fields": {"owls": len(owls)}},
            )
            return {}
        try:
            acts, gaps = await read_owl_activity(
                db, owls,
                owner_id=DEFAULT_PRINCIPAL_ID,
                since_epoch=time.time() - _ACTIVITY_WINDOW_S,
            )
        except Exception as exc:  # B5 / no-hidden-errors — degrade loudly.
            log.tool.error(
                "owls_list._activity: runtime read failed — listing configuration only",
                exc_info=exc,
                extra={"_fields": {"owls": len(owls)}},
            )
            return {}
        if gaps.orphaned_tasks or gaps.unattributed_tasks or gaps.orphaned_outcomes:
            log.tool.info(
                "owls_list._activity: work that attaches to no configured owl",
                extra={"_fields": {
                    "unattributed_tasks": gaps.unattributed_tasks,
                    "orphaned_tasks": gaps.orphaned_tasks,
                    "orphaned_outcomes": gaps.orphaned_outcomes,
                }},
            )
        return {a.manifest.name: a for a in acts}

    @staticmethod
    def _format(
        owls: list[OwlAgentManifest], activity: dict[str, OwlActivity] | None = None
    ) -> str:
        if not owls:
            return "(no owls configured)"
        activity = activity or {}
        lines = [f"{len(owls)} owl(s):"]
        for m in owls:
            schedule = ""
            if m.lifecycle == "scheduled" and m.trigger is not None:
                schedule = f" — schedule: {m.trigger.schedule}"
            display = m.display_name or m.name
            lines.append(
                f"  - {m.name} ({display})  role: {m.role}  "
                f"tier: {m.model_tier}  lifecycle: {m.lifecycle}{schedule}"
                f"{OwlsListTool._runtime(activity.get(m.name))}"
            )
        return "\n".join(lines)

    @staticmethod
    def _runtime(a: OwlActivity | None) -> str:
        """NOW and EVER, named separately, never fused into one word.

        `in_flight` comes from `tasks`, which has a status column; `turns` comes
        from `task_outcomes`, which has none and holds only terminal records. A
        single rendered word like "idle" would assert liveness from history, and
        a stage owl that runs inside another owl's turn legitimately holds no
        task row — it is not idle and this must not say it is.
        """
        if a is None:
            return ""
        parts = []
        if a.in_flight:
            parts.append(f"in-flight: {a.in_flight}")
        if a.pending:
            parts.append(f"pending: {a.pending}")
        parts.append(f"turns/3d: {a.recent_turns}")
        return "  " + "  ".join(parts)
