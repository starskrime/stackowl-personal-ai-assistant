"""owl_schedule — natural-language STOP / SNOOZE / RESUME for a scheduled owl (TS11).

The user's off-ramp for a proactive owl: ``pause`` (stop the pokes), ``snooze`` (go
quiet for a while, then auto-resume) and ``resume``. PAUSE ≠ DELETE — it disables the
owl's projected scheduler row (``enabled=0``, recoverable), never touches the owl
itself, and SURVIVES reconcile (the owl manifest is unchanged, so the lifecycle
projection leaves the disabled row alone — it only re-enables on a real manifest edit).

INTENT RECOGNITION (project rule: no hardcoded English keyword list, multilingual): the
owl's LLM classifies the user's natural phrasing ("stop Brain", "Brain stop", "snooze
Brain 8h", "resume Brain", or the same in any language) into a structured call to THIS
tool. There is no English verb wordlist anywhere on the path — the model maps meaning →
``action``, and the deterministic effect lives here. It REUSES the existing pieces:
vocative routing already resolves "Brain" → the owl; :func:`_job_id_for` keys the
projected row; :meth:`JobScheduler.pause`/``resume``/``snooze`` toggle it.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from stackowl.exceptions import OwlNotFoundError
from stackowl.infra.observability import log
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import get_services
from stackowl.scheduler.job import Job
from stackowl.scheduler.owl_lifecycle import _job_id_for
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import parse_every
from stackowl.tools.base import Tool, ToolManifest, ToolResult

_VALID_ACTIONS: tuple[str, ...] = ("pause", "resume", "snooze")


class OwlScheduleTool(Tool):
    """Pause / snooze / resume a scheduled owl's proactive pokes (recoverable, no delete)."""

    @property
    def name(self) -> str:
        return "owl_schedule"

    @property
    def description(self) -> str:
        return (
            "Pause, snooze, or resume a SCHEDULED owl's proactive pokes when the user "
            "asks to stop / quiet / snooze / resume a named agent (e.g. 'stop Brain', "
            "'snooze Brain for 8h', 'resume Brain'). action='pause' stops the pokes "
            "(recoverable — the owl is NOT deleted); 'snooze' goes quiet for a duration "
            "then auto-resumes; 'resume' starts the pokes again. Use this for the user's "
            "off-ramp — it is the honest, reversible alternative to retiring the owl."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_VALID_ACTIONS),
                    "description": "pause | snooze | resume",
                },
                "name": {
                    "type": "string",
                    "description": "The scheduled owl's name (or display name) to control.",
                },
                "snooze_for": {
                    "type": "string",
                    "description": (
                        "Snooze duration for action='snooze', e.g. '8h', '30m', '2d'. "
                        "Omit for pause/resume."
                    ),
                },
            },
            "required": ["action", "name"],
        }

    @property
    def manifest(self) -> ToolManifest:
        # write, NOT consequential: the off-ramp must be instant (no consent gate) and
        # is always recoverable. effect_class stays None — it toggles an EXISTING job's
        # enabled flag, it does not mint a persistent entity / send / install a new job,
        # so the overclaim gate (TS3) has nothing to verify here.
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        action = str(kwargs.get("action") or "").strip().lower()
        name = str(kwargs.get("name") or "").strip()
        # 1. ENTRY
        log.tool.info(
            "owl_schedule.execute: entry",
            extra={"_fields": {"action": action, "name": name}},
        )
        if action not in _VALID_ACTIONS:
            return self._err(f"unknown action '{action}' — use pause | snooze | resume.", t0)
        if not name:
            return self._err("which owl? give the owl's name to pause/snooze/resume.", t0)

        svc = get_services()
        registry = svc.owl_registry
        db = svc.db_pool
        if registry is None or db is None:
            log.tool.error(
                "owl_schedule.execute: registry/db unavailable — cannot control schedule",
                exc_info=None,
                extra={"_fields": {"action": action, "name": name}},
            )
            return self._err("schedule control is unavailable right now.", t0)

        # 2. DECISION — resolve the named owl (exact, then display name) and its row.
        manifest = self._resolve_owl(registry, name)
        if manifest is None:
            return self._err(
                f"no owl named '{name}' — nothing to {action} (check the name).", t0
            )
        display = manifest.display
        owl_name = manifest.name

        tz = "UTC"
        settings = svc.settings
        if settings is not None:
            tz = settings.system.timezone or "UTC"
        sched = JobScheduler(db=db, tz=tz)
        job_id = _job_id_for(owl_name)
        job = await self._find_job(sched, job_id)
        if job is None:
            return self._err(
                f"{display} has no active schedule to {action} — it isn't a proactive owl.",
                t0,
            )

        # 3. STEP — apply the recoverable lifecycle change.
        try:
            if action == "pause":
                await sched.pause(job_id)
                msg = (
                    f"Paused {display} — no more pokes until you say 'resume {display}'. "
                    "Nothing lost; the owl still exists."
                )
            elif action == "resume":
                await sched.resume(job_id)
                next_run = await self._next_run(sched, job_id)
                when = f" Next run: {next_run}." if next_run else ""
                msg = f"Resumed {display} — it will reach you proactively again.{when}"
            else:  # snooze
                msg = await self._do_snooze(sched, job_id, display, kwargs.get("snooze_for"))
        except Exception as exc:  # B5 — never raise out of the tool
            log.tool.error(
                "owl_schedule.execute: lifecycle change failed",
                exc_info=exc,
                extra={"_fields": {"action": action, "owl": owl_name}},
            )
            return self._err(f"could not {action} {display}: {exc}", t0)

        # 4. EXIT
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "owl_schedule.execute: exit",
            extra={"_fields": {"success": True, "action": action, "owl": owl_name,
                               "duration_ms": duration_ms}},
        )
        return ToolResult(success=True, output=msg, duration_ms=duration_ms)

    @staticmethod
    def _resolve_owl(registry: OwlRegistry, name: str) -> OwlAgentManifest | None:
        """Resolve ``name`` (routing slug first, then human display name) to a manifest."""
        try:
            return registry.get(name)
        except OwlNotFoundError:  # fall through to a display-name scan
            pass
        for m in registry.all():
            if m.display == name or m.name == name:
                return m
        return None

    @staticmethod
    async def _find_job(sched: JobScheduler, job_id: str) -> Job | None:
        try:
            jobs = await sched.list_jobs()
        except Exception as exc:  # B5
            log.tool.warning(
                "owl_schedule.execute: job read-back failed",
                exc_info=exc, extra={"_fields": {"job_id": job_id}},
            )
            return None
        for j in jobs:
            if j.job_id == job_id:
                return j
        return None

    async def _next_run(self, sched: JobScheduler, job_id: str) -> str | None:
        job = await self._find_job(sched, job_id)
        return job.next_run_at if job is not None else None

    async def _do_snooze(
        self, sched: JobScheduler, job_id: str, display: str, snooze_for: object,
    ) -> str:
        """Snooze with auto-resume when the duration parses; else pause + note (TS11).

        Reuses the scheduler's ``every <n><unit>`` parser so the duration grammar is
        identical to the schedule DSL (no second parser). An unparseable/absent
        duration falls back to a plain pause and SAYS SO honestly — never a silent
        guess at how long to stay quiet."""
        token = str(snooze_for or "").strip()
        delta = parse_every(f"every {token}") if token else None
        if delta is None:
            await sched.pause(job_id)
            log.tool.info(
                "owl_schedule.execute: snooze duration missing/unparseable — paused instead",
                extra={"_fields": {"job_id": job_id, "snooze_for": token}},
            )
            return (
                f"Paused {display} (I couldn't read a snooze duration from "
                f"'{token}', so I stopped the pokes instead). Say 'resume {display}' "
                "to start them again."
            )
        until_iso = (datetime.now(UTC) + delta).isoformat()
        await sched.snooze(job_id, until_iso)
        return (
            f"Snoozed {display} until {until_iso} — it'll go quiet until then and "
            "auto-resume on its own. Nothing lost."
        )

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "owl_schedule.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
