"""AgentCreateCommand — ``/agent`` slash command for natural-language agent creation.

Story 7.2 ships a two-step handshake:

* ``/agent create <intent>`` — sends the intent through an LLM with the
  ``agent_intent.j2`` template, parses the structured response, and **proposes**
  the resulting agent definition (handler / schedule / params). No job row
  is written until the user confirms.
* ``/agent confirm`` — turns the proposal stored under the current session
  into a real ``jobs`` row via :meth:`JobScheduler.create_job`.
* ``/agent cancel`` — discards the pending proposal.

Pending proposals are scoped per ``session_id`` so concurrent CLI / Telegram
sessions don't collide.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Template, select_autoescape

from stackowl.commands.agent_create_helpers import (
    format_proposal,
    parse_intent_response,
)
from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.exceptions import CommandParseError
from stackowl.infra.observability import log
from stackowl.providers.base import Message

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
    "Usage: /agent <create|confirm|cancel> [args]\n"
    "  /agent create <intent>  — propose a new background agent from natural language\n"
    "  /agent confirm          — confirm the pending proposal and create the job\n"
    "  /agent cancel           — discard the pending proposal"
)

_NO_PENDING = "No pending agent proposal for this session — run /agent create <intent> first."
_NO_PROVIDER = "Provider registry not configured — cannot create agents."
_NO_SCHEDULER = "Scheduler not configured — cannot create agents."


class AgentCreateCommand(SlashCommand):
    """Implements ``/agent [create|confirm|cancel]``."""

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
        return "Create background agents from natural-language intents."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.scheduler.debug(
            "[commands] agent.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        try:
            if sub == "create":
                result = await self._create(rest.strip(), state)
            elif sub == "confirm":
                result = await self._confirm(state)
            elif sub == "cancel":
                result = self._cancel(state)
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

    # --------------------------------------------------------------- create
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

    # -------------------------------------------------------------- confirm
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

    # --------------------------------------------------------------- cancel
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

    # --------------------------------------------------------------- factory
    @classmethod
    def create_and_register(
        cls,
        scheduler: JobScheduler | None = None,
        provider_registry: ProviderRegistry | None = None,
        db: DbPool | None = None,
        event_bus: EventBus | None = None,
    ) -> AgentCreateCommand:
        """Construct an :class:`AgentCreateCommand` and register it on the singleton."""
        cmd = cls(
            scheduler=scheduler,
            provider_registry=provider_registry,
            db=db,
            event_bus=event_bus,
        )
        CommandRegistry.instance().register(cmd)
        return cmd
