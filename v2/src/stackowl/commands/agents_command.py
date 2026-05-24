"""AgentsCommand — /agents slash command for background-agent lifecycle.

Story 7.1 ships the ``acknowledge`` subcommand; Story 7.2 completes the
remaining surface (``list / pause / resume / stop / log``) so users can
manage agents created via ``/agent create`` without touching SQL directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.agents_helpers import format_jobs_table, format_results_table
from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.exceptions import CommandParseError
from stackowl.infra.observability import log
from stackowl.scheduler.scheduler_helpers import compute_next_run, write_audit

if TYPE_CHECKING:
    from stackowl.db.pool import DbPool
    from stackowl.events.bus import EventBus
    from stackowl.pipeline.state import PipelineState
    from stackowl.scheduler.scheduler import JobScheduler


_USAGE = (
    "Usage: /agents <list|acknowledge|pause|resume|stop|log> [args]\n"
    "  /agents list                 — show registered background agents\n"
    "  /agents acknowledge <job_id> — clear failures and re-arm an agent\n"
    "  /agents pause <job_id>       — pause an agent\n"
    "  /agents resume <job_id>      — resume a paused agent\n"
    "  /agents stop <job_id>        — permanently remove an agent (asks YES)\n"
    "  /agents log <job_id>         — show the last 10 recorded runs"
)

_NO_SCHEDULER = "(scheduler not wired — cannot manage agents)"
_NO_DB = "(no database wired — cannot read agent history)"


class AgentsCommand(SlashCommand):
    """Implements ``/agents [list|acknowledge|pause|resume|stop|log]``."""

    def __init__(
        self,
        scheduler: JobScheduler | None = None,
        db: DbPool | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._db = db
        self._bus = event_bus

    @property
    def command(self) -> str:
        return "agents"

    @property
    def description(self) -> str:
        return "Manage background agents: list, acknowledge, pause, resume, stop, log."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.scheduler.debug(
            "[commands] agents.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        try:
            if sub == "acknowledge":
                result = await self._acknowledge(rest)
            elif sub == "list":
                result = await self._list()
            elif sub == "pause":
                result = await self._pause(rest)
            elif sub == "resume":
                result = await self._resume(rest)
            elif sub == "stop":
                result = await self._stop(rest)
            elif sub == "log":
                result = await self._log(rest)
            else:
                log.scheduler.debug(
                    "[commands] agents.handle: unknown subcommand",
                    extra={"_fields": {"sub": sub}},
                )
                return _USAGE
        except CommandParseError as exc:
            log.scheduler.warning(
                "[commands] agents.handle: parse error",
                extra={"_fields": {"sub": sub, "error": str(exc)}},
            )
            return f"✗ {exc}\n\n{_USAGE}"
        except Exception as exc:
            log.scheduler.error(
                "[commands] agents.handle: subcommand crashed",
                exc_info=exc,
                extra={"_fields": {"sub": sub}},
            )
            return f"✗ /agents {sub}: {exc}"
        log.scheduler.debug("[commands] agents.handle: exit", extra={"_fields": {"sub": sub}})
        return result

    # ----------------------------------------------------------- acknowledge
    async def _acknowledge(self, rest: str) -> str:
        log.scheduler.debug(
            "[commands] agents.acknowledge: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._db is None:
            return "(no database wired — cannot acknowledge agents)"
        job_id = rest.strip()
        if not job_id:
            raise CommandParseError("agents", "missing <job_id>")
        rows = await self._db.fetch_all(
            "SELECT schedule FROM jobs WHERE job_id = ?", (job_id,)
        )
        if not rows:
            log.scheduler.warning(
                "[commands] agents.acknowledge: job not found",
                extra={"_fields": {"job_id": job_id}},
            )
            return f"✗ /agents acknowledge: no job with id '{job_id}'"
        next_run = compute_next_run(rows[0]["schedule"])
        await self._db.execute(
            "UPDATE jobs SET status = 'pending', failure_count = 0, "
            "last_error = NULL, enabled = 1, next_run_at = ? WHERE job_id = ?",
            (next_run, job_id),
        )
        await write_audit(
            self._db,
            "job_resumed",
            job_id,
            details={"trigger": "agents_acknowledge", "next_run_at": next_run},
        )
        if self._bus is not None:
            self._bus.emit("agent_acknowledged", {"job_id": job_id})
        log.scheduler.info(
            "[commands] agents.acknowledge: exit",
            extra={"_fields": {"job_id": job_id, "next_run_at": next_run}},
        )
        return f"✓ agent '{job_id}' acknowledged — next run {next_run}"

    # ------------------------------------------------------------------ list
    async def _list(self) -> str:
        log.scheduler.debug("[commands] agents.list: entry")
        if self._scheduler is None:
            return _NO_SCHEDULER
        jobs = await self._scheduler.list_jobs()
        rendered = format_jobs_table(jobs)
        log.scheduler.debug(
            "[commands] agents.list: exit",
            extra={"_fields": {"count": len(jobs)}},
        )
        return rendered

    # ----------------------------------------------------------------- pause
    async def _pause(self, rest: str) -> str:
        log.scheduler.debug(
            "[commands] agents.pause: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._scheduler is None:
            return _NO_SCHEDULER
        job_id = rest.strip()
        if not job_id:
            raise CommandParseError("agents", "missing <job_id>")
        await self._scheduler.pause(job_id)
        if self._bus is not None:
            try:
                self._bus.emit("agent_paused", {"job_id": job_id})
            except Exception as exc:  # B5
                log.scheduler.warning(
                    "[commands] agents.pause: event emit failed",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job_id}},
                )
        log.scheduler.info(
            "[commands] agents.pause: exit",
            extra={"_fields": {"job_id": job_id}},
        )
        return f"✓ agent '{job_id}' paused"

    # ---------------------------------------------------------------- resume
    async def _resume(self, rest: str) -> str:
        log.scheduler.debug(
            "[commands] agents.resume: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._scheduler is None:
            return _NO_SCHEDULER
        job_id = rest.strip()
        if not job_id:
            raise CommandParseError("agents", "missing <job_id>")
        await self._scheduler.resume(job_id)
        if self._bus is not None:
            try:
                self._bus.emit("agent_resumed", {"job_id": job_id})
            except Exception as exc:  # B5
                log.scheduler.warning(
                    "[commands] agents.resume: event emit failed",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job_id}},
                )
        log.scheduler.info(
            "[commands] agents.resume: exit",
            extra={"_fields": {"job_id": job_id}},
        )
        return f"✓ agent '{job_id}' resumed"

    # ------------------------------------------------------------------ stop
    async def _stop(self, rest: str) -> str:
        log.scheduler.debug(
            "[commands] agents.stop: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._scheduler is None:
            return _NO_SCHEDULER
        tokens = rest.split()
        if not tokens:
            raise CommandParseError("agents", "missing <job_id>")
        job_id = tokens[0]
        confirmed = len(tokens) > 1 and tokens[1] == "YES"
        if not confirmed:
            log.scheduler.debug(
                "[commands] agents.stop: awaiting confirmation",
                extra={"_fields": {"job_id": job_id}},
            )
            return (
                f"⚠ Stop agent {job_id[:8]}? This permanently removes the schedule.\n"
                f"   Type: /agents stop {job_id} YES to confirm."
            )
        await self._scheduler.stop_job(job_id)
        if self._bus is not None:
            try:
                self._bus.emit("agent_stopped", {"job_id": job_id})
            except Exception as exc:  # B5
                log.scheduler.warning(
                    "[commands] agents.stop: event emit failed",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job_id}},
                )
        log.scheduler.info(
            "[commands] agents.stop: exit",
            extra={"_fields": {"job_id": job_id}},
        )
        return f"✓ agent '{job_id}' stopped"

    # ------------------------------------------------------------------- log
    async def _log(self, rest: str) -> str:
        log.scheduler.debug(
            "[commands] agents.log: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._db is None:
            return _NO_DB
        job_id = rest.strip()
        if not job_id:
            raise CommandParseError("agents", "missing <job_id>")
        rows = await self._db.fetch_all(
            "SELECT run_at, status, result_text, duration_ms "
            "FROM job_results WHERE job_id = ? "
            "ORDER BY run_at DESC LIMIT 10",
            (job_id,),
        )
        rendered = format_results_table(job_id, rows)
        log.scheduler.debug(
            "[commands] agents.log: exit",
            extra={"_fields": {"job_id": job_id, "rows": len(rows)}},
        )
        return rendered

    # ---------------------------------------------------------------- factory
    @classmethod
    def create_and_register(
        cls,
        scheduler: JobScheduler | None = None,
        db: DbPool | None = None,
        event_bus: EventBus | None = None,
    ) -> AgentsCommand:
        """Construct an :class:`AgentsCommand` and register it on the singleton."""
        cmd = cls(scheduler=scheduler, db=db, event_bus=event_bus)
        CommandRegistry.instance().register(cmd)
        return cmd
