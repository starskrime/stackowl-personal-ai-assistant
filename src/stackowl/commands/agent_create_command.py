"""AgentCommand — the unified ``/agent`` slash command for background agents.

Merges the former ``/agent`` (create) and ``/agents`` (manage) commands into a
single surface so users don't have to remember a singular/plural distinction.
Two lifecycle halves, no overlapping subcommands:

CREATE (two-step handshake, scoped per ``session_id``):
* ``/agent create <intent>`` — sends the intent through an LLM with the
  ``agent_intent.j2`` template, parses the structured response, and **proposes**
  the agent definition (handler / schedule / params). No job row is written
  until confirmed.
* ``/agent confirm`` — turns the pending proposal into a real ``jobs`` row via
  :meth:`JobScheduler.create_job`.
* ``/agent cancel`` — discards the pending (unconfirmed) proposal. NOTE: this is
  distinct from ``stop`` — ``cancel`` drops a proposal that was never created;
  ``stop`` removes an agent that already exists.

MANAGE (operate on already-created agents):
* ``/agent list`` — show registered agents.
* ``/agent log <job_id>`` — last 10 recorded runs.
* ``/agent pause|resume <job_id>`` — pause / resume an agent.
* ``/agent acknowledge <job_id>`` — clear failures and re-arm an agent.
* ``/agent stop <job_id>`` — permanently remove an agent (asks YES).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Template, select_autoescape

from stackowl.commands.agent_create_helpers import (
    format_proposal,
    parse_intent_response,
)
from stackowl.commands.agents_helpers import format_jobs_table, format_results_table
from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.exceptions import CommandParseError
from stackowl.infra.observability import log
from stackowl.providers.base import Message
from stackowl.scheduler.scheduler_helpers import compute_next_run, write_audit

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.db.pool import DbPool
    from stackowl.events.bus import EventBus
    from stackowl.pipeline.state import PipelineState
    from stackowl.providers.registry import ProviderRegistry
    from stackowl.scheduler.scheduler import JobScheduler


_PROMPT_DIR = (
    Path(__file__).resolve().parent.parent / "scheduler" / "prompts"
)
_TEMPLATE_NAME = "agent_intent.j2"
_FAST_TIER = "fast"

_USAGE = (
    "Usage: /agent <subcommand> [args]\n"
    "  create <intent>      — propose a new background agent from natural language\n"
    "  confirm              — confirm the pending proposal and create the agent\n"
    "  cancel               — discard the pending (unconfirmed) proposal\n"
    "  list                 — show registered background agents\n"
    "  log <job_id>         — show the last 10 recorded runs\n"
    "  pause <job_id>       — pause an agent\n"
    "  resume <job_id>      — resume a paused agent\n"
    "  acknowledge <job_id> — clear failures and re-arm an agent\n"
    "  stop <job_id>        — permanently remove an agent (asks YES)"
)

_NO_PENDING = "No pending agent proposal for this session — run /agent create <intent> first."
_NO_PROVIDER = "Provider registry not configured — cannot create agents."
_NO_SCHEDULER = "✗ scheduler not wired — cannot manage agents."
_NO_DB = "✗ no database wired — cannot read agent history."


class AgentCommand(SlashCommand):
    """Implements the unified ``/agent`` (create + manage) command."""

    def __init__(
        self,
        scheduler: JobScheduler | None = None,
        provider_registry: ProviderRegistry | None = None,
        db: DbPool | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._providers = provider_registry
        self._db = db
        self._bus = event_bus
        self._pending: dict[str, dict[str, Any]] = {}
        self._template_env = Environment(
            loader=FileSystemLoader(str(_PROMPT_DIR)),
            autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        self._template: Template | None = None  # loaded lazily on first _create() call

    @property
    def command(self) -> str:
        return "agent"

    @property
    def description(self) -> str:
        return "Create and manage background agents (create/confirm/cancel, list/pause/resume/stop/log/acknowledge)."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.scheduler.debug(
            "[commands] agent.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        try:
            # --- create half (proposal handshake) ---
            if sub == "create":
                result = await self._create(rest.strip(), state)
            elif sub == "confirm":
                result = await self._confirm(state)
            elif sub == "cancel":
                result = self._cancel(state)
            # --- manage half (existing agents) ---
            elif sub == "list":
                result = await self._list()
            elif sub == "log":
                result = await self._log(rest)
            elif sub == "pause":
                result = await self._pause(rest)
            elif sub == "resume":
                result = await self._resume(rest)
            elif sub == "acknowledge":
                result = await self._acknowledge(rest)
            elif sub == "stop":
                result = await self._stop(rest)
            else:
                log.scheduler.debug(
                    "[commands] agent.handle: unknown subcommand",
                    extra={"_fields": {"sub": sub}},
                )
                return _USAGE
        except CommandParseError as exc:
            log.scheduler.warning(
                "[commands] agent.handle: parse error",
                extra={"_fields": {"sub": sub, "error": str(exc)}},
            )
            return f"✗ {exc}\n\n{_USAGE}"
        except Exception as exc:
            log.scheduler.error(
                "[commands] agent.handle: subcommand crashed",
                exc_info=exc,
                extra={"_fields": {"sub": sub}},
            )
            return f"✗ /agent {sub}: {exc}"
        log.scheduler.debug(
            "[commands] agent.handle: exit", extra={"_fields": {"sub": sub}}
        )
        return result

    # =====================================================================
    # CREATE half
    # =====================================================================

    async def _create(self, intent: str, state: PipelineState) -> str:
        log.scheduler.debug(
            "[commands] agent.create: entry",
            extra={
                "_fields": {
                    "intent_len": len(intent),
                    "session": state.session_id,
                }
            },
        )
        if not intent:
            raise CommandParseError("agent", "missing <intent>")
        if self._providers is None:
            log.scheduler.warning(
                "[commands] agent.create: provider registry not wired",
            )
            return _NO_PROVIDER

        if self._template is None:
            try:
                self._template = self._template_env.get_template(_TEMPLATE_NAME)
            except Exception as exc:
                log.scheduler.error(
                    "[commands] agent._create: template load failed",
                    exc_info=exc,
                    extra={"_fields": {"template": _TEMPLATE_NAME, "prompt_dir": str(_PROMPT_DIR)}},
                )
                raise
        prompt = self._template.render(user_intent=intent)
        provider = self._providers.get_by_tier(_FAST_TIER)
        log.scheduler.debug(
            "[commands] agent.create: provider selected",
            extra={"_fields": {"provider": provider.name}},
        )
        completion = await provider.complete(
            [Message(role="user", content=prompt)],
            model="",
        )
        log.scheduler.debug(
            "[commands] agent.create: provider response received",
            extra={
                "_fields": {
                    "response_len": len(completion.content),
                    "provider": completion.provider_name,
                }
            },
        )

        parsed = parse_intent_response(completion.content)
        self._pending[state.session_id] = parsed
        log.scheduler.info(
            "[commands] agent.create: proposal staged",
            extra={
                "_fields": {
                    "session": state.session_id,
                    "handler": parsed.get("handler_name"),
                    "schedule": parsed.get("schedule"),
                }
            },
        )
        return format_proposal(parsed)

    async def _confirm(self, state: PipelineState) -> str:
        log.scheduler.debug(
            "[commands] agent.confirm: entry",
            extra={"_fields": {"session": state.session_id}},
        )
        if self._scheduler is None:
            log.scheduler.warning("[commands] agent.confirm: scheduler not wired")
            return _NO_SCHEDULER
        proposal = self._pending.pop(state.session_id, None)
        if proposal is None:
            log.scheduler.debug(
                "[commands] agent.confirm: no pending proposal",
                extra={"_fields": {"session": state.session_id}},
            )
            return _NO_PENDING

        handler_name = str(proposal.get("handler_name", ""))
        schedule = str(proposal.get("schedule", ""))
        params_raw = proposal.get("params", {}) or {}
        primary_channel = proposal.get("primary_channel")
        params = dict(params_raw) if isinstance(params_raw, dict) else {}
        channel = str(primary_channel) if isinstance(primary_channel, str) else None

        job = await self._scheduler.create_job(
            handler_name=handler_name,
            schedule=schedule,
            params=params,
            primary_channel=channel,
        )
        if self._bus is not None:
            try:
                self._bus.emit(
                    "agent_created",
                    {"job_id": job.job_id, "handler": handler_name},
                )
            except Exception as exc:  # B5
                log.scheduler.warning(
                    "[commands] agent.confirm: event emit failed",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job.job_id}},
                )
        log.scheduler.info(
            "[commands] agent.confirm: exit — agent created",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "handler": handler_name,
                    "schedule": schedule,
                }
            },
        )
        return (
            f"✓ Agent '{job.job_id}' created\n"
            f"  Handler: {handler_name}\n"
            f"  Schedule: {schedule}\n"
            f"  Next run: {job.next_run_at}"
        )

    def _cancel(self, state: PipelineState) -> str:
        log.scheduler.debug(
            "[commands] agent.cancel: entry",
            extra={"_fields": {"session": state.session_id}},
        )
        proposal = self._pending.pop(state.session_id, None)
        if proposal is None:
            log.scheduler.debug(
                "[commands] agent.cancel: nothing to cancel",
                extra={"_fields": {"session": state.session_id}},
            )
            return _NO_PENDING
        log.scheduler.info(
            "[commands] agent.cancel: discarded pending proposal",
            extra={"_fields": {"session": state.session_id}},
        )
        return "✓ Pending agent proposal discarded."

    # =====================================================================
    # MANAGE half
    # =====================================================================

    async def _list(self) -> str:
        log.scheduler.debug("[commands] agent.list: entry")
        if self._scheduler is None:
            return _NO_SCHEDULER
        jobs = await self._scheduler.list_jobs()
        rendered = format_jobs_table(jobs)
        log.scheduler.debug(
            "[commands] agent.list: exit",
            extra={"_fields": {"count": len(jobs)}},
        )
        return rendered

    async def _acknowledge(self, rest: str) -> str:
        log.scheduler.debug(
            "[commands] agent.acknowledge: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._db is None:
            return _NO_DB
        job_id = rest.strip()
        if not job_id:
            raise CommandParseError("agent", "missing <job_id>")
        rows = await self._db.fetch_all(
            "SELECT schedule FROM jobs WHERE job_id = ?", (job_id,)
        )
        if not rows:
            log.scheduler.warning(
                "[commands] agent.acknowledge: job not found",
                extra={"_fields": {"job_id": job_id}},
            )
            return f"✗ /agent acknowledge: no job with id '{job_id}'"
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
            details={"trigger": "agent_acknowledge", "next_run_at": next_run},
        )
        if self._bus is not None:
            self._bus.emit("agent_acknowledged", {"job_id": job_id})
        log.scheduler.info(
            "[commands] agent.acknowledge: exit",
            extra={"_fields": {"job_id": job_id, "next_run_at": next_run}},
        )
        return f"✓ agent '{job_id}' acknowledged — next run {next_run}"

    async def _pause(self, rest: str) -> str:
        log.scheduler.debug(
            "[commands] agent.pause: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._scheduler is None:
            return _NO_SCHEDULER
        job_id = rest.strip()
        if not job_id:
            raise CommandParseError("agent", "missing <job_id>")
        await self._scheduler.pause(job_id)
        if self._bus is not None:
            try:
                self._bus.emit("agent_paused", {"job_id": job_id})
            except Exception as exc:  # B5
                log.scheduler.warning(
                    "[commands] agent.pause: event emit failed",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job_id}},
                )
        log.scheduler.info(
            "[commands] agent.pause: exit",
            extra={"_fields": {"job_id": job_id}},
        )
        return f"✓ agent '{job_id}' paused"

    async def _resume(self, rest: str) -> str:
        log.scheduler.debug(
            "[commands] agent.resume: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._scheduler is None:
            return _NO_SCHEDULER
        job_id = rest.strip()
        if not job_id:
            raise CommandParseError("agent", "missing <job_id>")
        await self._scheduler.resume(job_id)
        if self._bus is not None:
            try:
                self._bus.emit("agent_resumed", {"job_id": job_id})
            except Exception as exc:  # B5
                log.scheduler.warning(
                    "[commands] agent.resume: event emit failed",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job_id}},
                )
        log.scheduler.info(
            "[commands] agent.resume: exit",
            extra={"_fields": {"job_id": job_id}},
        )
        return f"✓ agent '{job_id}' resumed"

    async def _stop(self, rest: str) -> str:
        log.scheduler.debug(
            "[commands] agent.stop: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._scheduler is None:
            return _NO_SCHEDULER
        tokens = rest.split()
        if not tokens:
            raise CommandParseError("agent", "missing <job_id>")
        job_id = tokens[0]
        confirmed = len(tokens) > 1 and tokens[1] == "YES"
        if not confirmed:
            log.scheduler.debug(
                "[commands] agent.stop: awaiting confirmation",
                extra={"_fields": {"job_id": job_id}},
            )
            return (
                f"⚠ Stop agent {job_id[:8]}? This permanently removes the schedule.\n"
                f"   Type: /agent stop {job_id} YES to confirm."
            )
        await self._scheduler.stop_job(job_id)
        if self._bus is not None:
            try:
                self._bus.emit("agent_stopped", {"job_id": job_id})
            except Exception as exc:  # B5
                log.scheduler.warning(
                    "[commands] agent.stop: event emit failed",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job_id}},
                )
        log.scheduler.info(
            "[commands] agent.stop: exit",
            extra={"_fields": {"job_id": job_id}},
        )
        return f"✓ agent '{job_id}' stopped"

    async def _log(self, rest: str) -> str:
        log.scheduler.debug(
            "[commands] agent.log: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._db is None:
            return _NO_DB
        job_id = rest.strip()
        if not job_id:
            raise CommandParseError("agent", "missing <job_id>")
        rows = await self._db.fetch_all(
            "SELECT run_at, status, result_text, duration_ms "
            "FROM job_results WHERE job_id = ? "
            "ORDER BY run_at DESC LIMIT 10",
            (job_id,),
        )
        rendered = format_results_table(job_id, rows)
        log.scheduler.debug(
            "[commands] agent.log: exit",
            extra={"_fields": {"job_id": job_id, "rows": len(rows)}},
        )
        return rendered

    # =====================================================================
    # factory
    # =====================================================================

    @classmethod
    def create_and_register(
        cls,
        scheduler: JobScheduler | None = None,
        provider_registry: ProviderRegistry | None = None,
        db: DbPool | None = None,
        event_bus: EventBus | None = None,
    ) -> AgentCommand:
        """Construct an :class:`AgentCommand` and register it on the singleton."""
        cmd = cls(
            scheduler=scheduler,
            provider_registry=provider_registry,
            db=db,
            event_bus=event_bus,
        )
        CommandRegistry.instance().register(cmd)
        return cmd
