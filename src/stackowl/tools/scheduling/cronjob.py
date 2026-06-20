"""CronjobTool — the agent-callable interface to scheduled agent-goal jobs.

A single action-oriented tool (create / list / update / pause / resume / remove /
run) that lets an owl schedule a natural-language GOAL to run on a recurrence. The
prompt becomes ``params['goal']`` on a ``goal_execution`` job, which runs the
standard pipeline with ``interactive=False`` (a cron tick has no user to answer a
clarify). It reuses the existing ``goal_execution`` handler — no new handler (B9).

Safety: every create AND update re-scans the prompt (:func:`scan_cron_prompt`) and
blocks a flagged prompt; a malformed schedule is rejected pre-persist; a per-owl
SOFT CAP returns a structured nudge; scheduler/DB-down and unknown ``job_id`` come
back as structured results (never raises). EVERY by-job_id action (update / run /
pause / resume / remove) is OWNERSHIP-gated: a job owned by another owl is rejected
identically to a missing one (no existence oracle). Owner owl derives from the
session's ``conversations.owl_name``. Severity ``write``; group ``scheduling``.
"""

from __future__ import annotations

import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.notifications.recipient import resolve_owner_addresses
from stackowl.pipeline.services import get_services
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.scheduling.cron_helpers import (
    CREATED_BY_TAG,
    filter_owl_jobs,
    find_owned_job,
    is_valid_schedule,
    job_summary,
    resolve_owl,
)
from stackowl.tools.scheduling.cron_security import scan_cron_prompt

_TOOLSET_GROUP = "scheduling"
_DEFAULT_SOFT_CAP = 20
_HANDLER = "goal_execution"
_ACTIONS = ("create", "list", "update", "pause", "resume", "remove", "run")


class CronjobArgs(BaseModel):
    """Validated arguments for one ``cronjob`` invocation (action-discriminated)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["create", "list", "update", "pause", "resume", "remove", "run"]
    prompt: str | None = Field(default=None, description="The goal to schedule (create/update).")
    schedule: str | None = Field(default=None, description="cron / 'every Nm' / 'daily@HH:MM'.")
    job_id: str | None = Field(default=None, description="Target job (update/pause/resume/remove/run).")


class CronjobTool(Tool):
    """Schedule a natural-language goal to run on a recurrence (create/list/...)."""

    def __init__(self, *, soft_cap: int = _DEFAULT_SOFT_CAP) -> None:
        self._soft_cap = soft_cap

    @property
    def name(self) -> str:
        return "cronjob"

    @property
    def description(self) -> str:
        return (
            "SCHEDULE a natural-language goal to run automatically on a recurrence. "
            "Actions: create (needs 'prompt' + 'schedule'), list (your scheduled "
            "jobs), update (by 'job_id'; re-checks the prompt), pause, resume, "
            "remove, run (execute one job now) — the last four take 'job_id'. "
            "'schedule' accepts 5-field cron ('0 9 * * *'), 'every 30m'/'every 2h', "
            "or 'daily@09:00'. A flagged prompt (injection/exfil) is BLOCKED with a "
            "reason; relay it and do not retry verbatim. LANE: durable, recurring "
            "background work the user wants to happen on a clock. ANTI-LANE: do NOT "
            "use this to wait for a user reply (use clarify) or to run a one-off "
            "command right now (just do it)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "prompt": {"type": "string", "description": "Goal to schedule (create/update)."},
                "schedule": {
                    "type": "string",
                    "description": "cron '0 9 * * *', 'every 30m'/'every 2h', or 'daily@09:00'.",
                },
                "job_id": {
                    "type": "string",
                    "description": "Target job id (update/pause/resume/remove/run).",
                },
            },
            "required": ["action"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            commit_coupling="transactional",
            toolset_group=_TOOLSET_GROUP,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        # 1. ENTRY
        t0 = time.monotonic()
        try:
            args = CronjobArgs(**kwargs)  # type: ignore[arg-type]
        except ValidationError as exc:
            log.tool.warning(
                "cronjob.execute: invalid args",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return self._err(f"invalid arguments — 'action' must be one of {', '.join(_ACTIONS)}", t0)
        log.tool.info(
            "cronjob.execute: entry",
            extra={"_fields": {"action": args.action, "has_prompt": args.prompt is not None}},
        )

        # 2. DECISION — resolve the db + scheduler facade; self-heal if absent.
        db = get_services().db_pool
        if db is None:
            log.tool.warning("cronjob.execute: db unavailable — scheduling unavailable")
            return self._err("scheduling unavailable (no database configured)", t0)
        scheduler = JobScheduler(db=db)

        ctx = TraceContext.get()
        session_id = ctx.get("session_id")
        channel = ctx.get("channel")
        owl = await resolve_owl(db, session_id if isinstance(session_id, str) else None)

        try:
            return await self._dispatch(args, scheduler, owl, channel, t0)
        except Exception as exc:  # B5 — never raise out of a tool
            log.tool.error(
                "cronjob.execute: scheduler error — degrading",
                exc_info=exc,
                extra={"_fields": {"action": args.action}},
            )
            return self._err("scheduling unavailable (a scheduler error occurred)", t0)

    # --------------------------------------------------------------- dispatch

    async def _dispatch(
        self,
        args: CronjobArgs,
        scheduler: JobScheduler,
        owl: str,
        channel: str | None,
        t0: float,
    ) -> ToolResult:
        if args.action == "create":
            return await self._create(args, scheduler, owl, channel, t0)
        if args.action == "list":
            return await self._list(scheduler, owl, t0)
        if args.action == "update":
            return await self._update(args, scheduler, owl, t0)
        if args.action == "run":
            return await self._run(args, scheduler, owl, t0)
        return await self._lifecycle(args, scheduler, owl, t0)  # pause/resume/remove

    async def _create(
        self,
        args: CronjobArgs,
        scheduler: JobScheduler,
        owl: str,
        channel: str | None,
        t0: float,
    ) -> ToolResult:
        prompt = (args.prompt or "").strip()
        schedule = (args.schedule or "").strip()
        if not prompt or not schedule:
            return self._err("create requires both 'prompt' and 'schedule'", t0)

        ok, reason = scan_cron_prompt(prompt)
        if not ok:
            log.tool.warning("cronjob.create: prompt blocked", extra={"_fields": {"reason": reason}})
            return self._err(f"blocked: {reason}", t0)

        if not is_valid_schedule(schedule):
            return self._err(
                f"unparseable schedule {schedule!r} — use 5-field cron, "
                "'every Nm'/'every Nh', or 'daily@HH:MM'",
                t0,
            )

        # Soft cap is an advisory NUDGE (fork C), not enforcement (hint, no block).
        existing = filter_owl_jobs(await scheduler.list_jobs(), owl)
        if len(existing) >= self._soft_cap:
            return self._ok(
                {
                    "nudge": f"you already have {len(existing)} scheduled job(s) "
                    f"(soft cap {self._soft_cap}). Review or remove some before adding more.",
                    "active_count": len(existing),
                    "created": False,
                },
                t0,
            )

        # WS-B/C1 — capture the ORIGIN delivery target at create time so the
        # goal_execution handler can route its produced answer back to the chat
        # the goal was scheduled from. A cron poll has no live session, so the
        # recipient MUST be persisted on the job row now (durable target columns).
        target_channels, target_addresses = self._resolve_durable_target(channel)
        unreachable = not target_channels

        job = await scheduler.create_job(
            handler_name=_HANDLER,
            schedule=schedule,
            params={"goal": prompt, "created_by": CREATED_BY_TAG, "owl": owl},
            primary_channel=channel,
            target_channels=target_channels,
            target_addresses=target_addresses,
        )
        payload: dict[str, object] = {"created": True, **job_summary(job)}
        if unreachable:
            # HONESTY — never a bare "scheduled ✓". The job is created (plumbing
            # success preserved), but its answer can't be auto-delivered on this
            # channel, so the user is told plainly.
            payload["created_but_unreachable"] = True
            payload["warning"] = (
                "Scheduled — but results can't be auto-delivered on this channel. "
                "Use /agents log to read each run, or schedule from a chat channel "
                "(e.g. Telegram) to receive the answer."
            )
            log.tool.warning(
                "cronjob.create: no durable delivery target — results unreachable",
                extra={"_fields": {"job_id": job.job_id, "channel": channel}},
            )
        return self._ok(payload, t0)

    def _resolve_durable_target(
        self, channel: str | None
    ) -> tuple[list[str], dict[str, str | int]]:
        """Resolve the job's durable ``(target_channels, target_addresses)``.

        Precedence:
        1. The live request's ``reply_target`` (the exact chat the goal was
           scheduled from) — native int/str preserved, never stringified.
        2. The shared owner fallback (:func:`resolve_owner_addresses`) using
           ``settings`` from services, when no per-request target is available.
        3. Neither resolvable → empty target (caller signals "unreachable").
        """
        ctx = TraceContext.get()
        reply_target = ctx.get("reply_target")
        if reply_target is not None and channel:
            log.tool.debug(
                "cronjob.create: durable target from request reply_target",
                extra={"_fields": {"channel": channel}},
            )
            return [channel], {channel: reply_target}

        settings = get_services().settings
        if settings is not None and channel:
            addresses = resolve_owner_addresses(settings, [channel])
            if addresses:
                log.tool.debug(
                    "cronjob.create: durable target from owner fallback",
                    extra={"_fields": {"channel": channel}},
                )
                return [channel], dict(addresses)

        log.tool.debug(
            "cronjob.create: no durable target resolved",
            extra={"_fields": {"channel": channel}},
        )
        return [], {}

    async def _list(self, scheduler: JobScheduler, owl: str, t0: float) -> ToolResult:
        jobs = filter_owl_jobs(await scheduler.list_jobs(), owl)
        return self._ok(
            {"count": len(jobs), "jobs": [job_summary(j) for j in jobs]}, t0
        )

    async def _update(self, args: CronjobArgs, scheduler: JobScheduler, owl: str, t0: float) -> ToolResult:
        if not args.job_id:
            return self._err("update requires 'job_id'", t0)
        # Ownership gate FIRST (no existence oracle), before scan/validation.
        if find_owned_job(await scheduler.list_jobs(), args.job_id, owl) is None:
            return self._err(f"no such job: {args.job_id!r}", t0)
        prompt = args.prompt.strip() if args.prompt else None
        if prompt is not None:
            ok, reason = scan_cron_prompt(prompt)
            if not ok:
                log.tool.warning(
                    "cronjob.update: prompt blocked", extra={"_fields": {"reason": reason}}
                )
                return self._err(f"blocked: {reason}", t0)
        schedule = args.schedule.strip() if args.schedule else None
        if schedule is not None and not is_valid_schedule(schedule):
            return self._err(f"unparseable schedule {schedule!r}", t0)

        updated = await scheduler.update_job(args.job_id, schedule=schedule, goal=prompt)
        if updated is None:
            return self._err(f"no such job: {args.job_id!r}", t0)
        return self._ok({"updated": True, **job_summary(updated)}, t0)

    async def _run(self, args: CronjobArgs, scheduler: JobScheduler, owl: str, t0: float) -> ToolResult:
        if not args.job_id:
            return self._err("run requires 'job_id'", t0)
        # Ownership gate FIRST — a foreign/missing job_id is "no such job".
        if find_owned_job(await scheduler.list_jobs(), args.job_id, owl) is None:
            return self._err(f"no such job: {args.job_id!r}", t0)
        result = await scheduler.run_now(args.job_id)
        if result is None:
            return self._err(f"no such job: {args.job_id!r}", t0)
        return self._ok(
            {
                "ran": True,
                "job_id": args.job_id,
                "success": result.success,
                "output": result.output,
                "error": result.error,
            },
            t0,
        )

    async def _lifecycle(self, args: CronjobArgs, scheduler: JobScheduler, owl: str, t0: float) -> ToolResult:
        if not args.job_id:
            return self._err(f"{args.action} requires 'job_id'", t0)
        # Ownership gate — foreign/missing job_id rejected identically (no oracle).
        if find_owned_job(await scheduler.list_jobs(), args.job_id, owl) is None:
            return self._err(f"no such job: {args.job_id!r}", t0)
        if args.action == "pause":
            await scheduler.pause(args.job_id)
        elif args.action == "resume":
            await scheduler.resume(args.job_id)
        else:  # remove
            await scheduler.stop_job(args.job_id)
        return self._ok({args.action: True, "job_id": args.job_id}, t0)

    # ---------------------------------------------------------------- helpers

    def _ok(self, payload: dict[str, object], t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "cronjob.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": duration_ms}},
        )
        return ToolResult(
            success=True,
            output=json.dumps(payload, ensure_ascii=False),
            error=None,
            duration_ms=duration_ms,
        )

    def _err(self, msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "cronjob.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
