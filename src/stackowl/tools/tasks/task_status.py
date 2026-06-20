"""Read-only durable-task status lookup tool."""
from __future__ import annotations

import time

from stackowl.exceptions import DurableTaskNotFoundError
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.services import get_services
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.base import Tool, ToolManifest, ToolResult


class TaskStatusTool(Tool):
    """Read-only owner-scoped durable task status lookup by task id."""

    @property
    def name(self) -> str:
        return "task_status"

    @property
    def description(self) -> str:
        return (
            "Look up the status of a durable task by its exact id. "
            "Returns status, current step, and goal. "
            "Use this when you have a task id and need to check progress."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Exact task id."},
            },
            "required": ["task_id"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group="tasks",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        task_id = str(kwargs.get("task_id", "")).strip()
        log.tool.debug("task_status.execute: entry", extra={"_fields": {"task_id": task_id}})

        if not task_id:
            return self._err("task_id is required.", t0)

        db = get_services().db_pool
        if db is None:
            return self._unavailable("no database pool is configured", t0)

        owner = TraceContext.durable_owner_id() or DEFAULT_PRINCIPAL_ID
        log.tool.debug(
            "task_status.execute: looking up task",
            extra={"_fields": {"task_id": task_id, "owner": owner}},
        )

        try:
            task = await DurableTaskStore(db, owner).get(task_id)
        except DurableTaskNotFoundError:
            return self._err(f"task {task_id!r} not found", t0)
        except Exception as exc:
            log.tool.error(
                "task_status.execute: store lookup failed",
                exc_info=exc,
                extra={"_fields": {"task_id": task_id}},
            )
            return self._unavailable(f"{type(exc).__name__}: {exc}", t0)

        out = (
            f"Task {task.task_id}: status={task.status}, "
            f"step={task.current_step}, goal={task.goal}"
        )
        dt = (time.monotonic() - t0) * 1000
        log.tool.info(
            "task_status.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": dt}},
        )
        return ToolResult(success=True, output=out, duration_ms=dt)

    def _err(self, msg: str, t0: float) -> ToolResult:
        dt = (time.monotonic() - t0) * 1000
        log.tool.info(
            "task_status.execute: exit error",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": dt}},
        )
        return ToolResult(
            success=False, output="", error=msg, duration_ms=dt,
            side_effect_committed=False,
        )

    def _unavailable(self, reason: str, t0: float) -> ToolResult:
        dt = (time.monotonic() - t0) * 1000
        msg = f"task status unavailable: {reason}"
        log.tool.warning(
            "task_status.execute: store unavailable",
            extra={"_fields": {"reason": reason, "duration_ms": dt}},
        )
        return ToolResult(
            success=False, output="", error=msg, duration_ms=dt,
            side_effect_committed=False,
        )
