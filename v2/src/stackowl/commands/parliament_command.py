"""ParliamentCommand — ``/parliament`` slash command for multi-owl debates.

Subcommands:

* ``/parliament <topic>``           — start a new debate session
* ``/parliament log [session_id]``  — list recent sessions or show transcript
* ``/parliament push <message>``    — queue an interjection on the active session
* ``/parliament expand <claim>``    — re-debate a specific claim from last session
* ``/parliament unsuppress``        — re-enable proactive Parliament suggestions

All dependencies are constructor-injected so the wiring layer decides what
is real vs. ``None``. The command is safe to register when no orchestrator
is configured — handlers respond with an informative not-configured message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.parliament_helpers import (
    format_rollcall,
    format_session_table,
    format_session_transcript,
)
from stackowl.commands.registry import CommandRegistry
from stackowl.exceptions import ManifestValidationError
from stackowl.infra.observability import log
from stackowl.parliament.models import make_critic_persona

if TYPE_CHECKING:
    from stackowl.events.bus import EventBus
    from stackowl.owls.registry import OwlRegistry
    from stackowl.parliament.orchestrator import ParliamentOrchestrator
    from stackowl.parliament.session_store import SessionStore
    from stackowl.pipeline.state import PipelineState


_NO_ORCH = "Parliament orchestrator not configured."
_NO_STORE = "Session store not configured."
_NO_ACTIVE = (
    "No active Parliament session — start one with /parliament <topic>"
)
_NO_HISTORY = "No completed Parliament sessions to expand from."
_SUGGESTIONS_RESET = "Parliament suggestion mode re-enabled."
_INTERJECTION_QUEUED = "Interjection queued for next round."

_USAGE = (
    "Usage:\n"
    "  /parliament <topic>             — start a multi-owl debate\n"
    "  /parliament log [session_id]    — list sessions or show transcript\n"
    "  /parliament push <message>      — interject on the active session\n"
    "  /parliament expand <claim>      — re-debate a claim\n"
    "  /parliament unsuppress          — re-enable proactive suggestions"
)


class ParliamentCommand(SlashCommand):
    """Implements ``/parliament [log|push|expand|unsuppress|<topic>]``."""

    def __init__(
        self,
        orchestrator: ParliamentOrchestrator | None = None,
        session_store: SessionStore | None = None,
        owl_registry: OwlRegistry | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._store = session_store
        self._registry = owl_registry
        self._bus = event_bus

    @property
    def command(self) -> str:
        return "parliament"

    @property
    def description(self) -> str:
        return "Start or manage Parliament multi-owl debate sessions."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.gateway.debug(
            "[commands] parliament.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        stripped = args.strip()
        if not stripped:
            log.gateway.debug(
                "[commands] parliament.handle: empty args — showing usage",
            )
            return _USAGE
        parts = stripped.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        try:
            if sub == "log":
                result = await self._log(rest.strip())
            elif sub == "push":
                result = await self._push(rest.strip())
            elif sub == "expand":
                result = await self._expand(rest.strip(), state)
            elif sub == "unsuppress":
                result = self._unsuppress()
            else:
                # Treat the entire arg string as the topic.
                result = await self._start(stripped, state)
        except Exception as exc:
            log.gateway.error(
                "[commands] parliament.handle: subcommand crashed",
                exc_info=exc,
                extra={"_fields": {"sub": sub}},
            )
            return f"✗ /parliament {sub}: {exc}"
        log.gateway.debug(
            "[commands] parliament.handle: exit",
            extra={"_fields": {"sub": sub}},
        )
        return result

    async def _start(self, topic: str, state: PipelineState) -> str:
        """Start a new Parliament debate on ``topic``."""
        log.gateway.debug(
            "[commands] parliament.start: entry",
            extra={"_fields": {"topic_len": len(topic), "session": state.session_id}},
        )
        if self._orchestrator is None:
            return _NO_ORCH

        owl_names = self._resolve_owls()
        log.gateway.debug(
            "[commands] parliament.start: owls resolved",
            extra={"_fields": {"owl_count": len(owl_names), "owls": owl_names}},
        )
        session = await self._orchestrator.run(
            topic,
            owl_names,
            session_id=state.session_id,
        )
        rollcall = format_rollcall(owl_names)
        if session.synthesis:
            log.gateway.info(
                "[commands] parliament.start: exit — synthesis returned",
                extra={
                    "_fields": {
                        "session_id": session.session_id,
                        "synthesis_len": len(session.synthesis),
                    }
                },
            )
            return f"{rollcall}\n\n{session.synthesis}"
        log.gateway.info(
            "[commands] parliament.start: exit — no synthesis",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "status": session.status,
                }
            },
        )
        # PARL-3 (F081) — distinguish a DEGRADED finish (synthesis raised) from a
        # benign "no synthesizer wired" completion, so the user is told honestly
        # the debate ran but no conclusion formed rather than a vague no-op.
        if session.status == "completed_no_synthesis":
            return (
                f"{rollcall}\n\n"
                "[Parliament ran to completion, but synthesis failed — no "
                "conclusion was formed. The debate transcript is available via "
                "`/parliament log`.]"
            )
        return (
            f"{rollcall}\n\n"
            f"[Parliament session complete — no synthesis produced]"
        )

    async def _log(self, session_id_filter: str) -> str:
        """List recent sessions or show a single session's full transcript."""
        log.gateway.debug(
            "[commands] parliament.log: entry",
            extra={"_fields": {"has_filter": bool(session_id_filter)}},
        )
        if self._store is None:
            return _NO_STORE
        if session_id_filter:
            session = await self._store.get_by_id(session_id_filter)
            if session is None:
                return f"No session found with id '{session_id_filter}'."
            return format_session_transcript(session)
        sessions = await self._store.list_recent(limit=5)
        return format_session_table(sessions)

    async def _push(self, message: str) -> str:
        """Queue an interjection on the active session."""
        log.gateway.debug(
            "[commands] parliament.push: entry",
            extra={"_fields": {"msg_len": len(message)}},
        )
        if self._orchestrator is None:
            return _NO_ORCH
        if not message:
            return "Usage: /parliament push <message>"
        accepted = await self._orchestrator.inject_interjection(message)
        if not accepted:
            log.gateway.debug(
                "[commands] parliament.push: no active session",
            )
            return _NO_ACTIVE
        log.gateway.info(
            "[commands] parliament.push: queued",
            extra={"_fields": {"msg_len": len(message)}},
        )
        return _INTERJECTION_QUEUED

    async def _expand(self, claim: str, state: PipelineState) -> str:
        """Re-debate ``claim`` using the participants from the most recent session."""
        log.gateway.debug(
            "[commands] parliament.expand: entry",
            extra={"_fields": {"claim_len": len(claim)}},
        )
        if self._orchestrator is None:
            return _NO_ORCH
        if self._store is None:
            return _NO_STORE
        if not claim:
            return "Usage: /parliament expand <claim>"
        recent = await self._store.list_recent(limit=1)
        if not recent or recent[0].status != "completed":
            return _NO_HISTORY
        previous = recent[0]
        session = await self._orchestrator.run(
            claim,
            previous.owl_names,
            session_id=state.session_id,
        )
        rollcall = format_rollcall(previous.owl_names)
        body = session.synthesis or "[expanded — no synthesis produced]"
        log.gateway.info(
            "[commands] parliament.expand: exit",
            extra={"_fields": {"session_id": session.session_id}},
        )
        return f"{rollcall}\n\n{body}"

    def _unsuppress(self) -> str:
        """Emit the suggestion-unsuppress event for the heartbeat subsystem."""
        log.gateway.debug("[commands] parliament.unsuppress: entry")
        if self._bus is not None:
            try:
                self._bus.emit("parliament_suggestions_unsuppressed")
            except Exception as exc:
                log.gateway.warning(
                    "[commands] parliament.unsuppress: event emit failed",
                    exc_info=exc,
                )
        log.gateway.info("[commands] parliament.unsuppress: exit")
        return _SUGGESTIONS_RESET

    def _resolve_owls(self) -> list[str]:
        """Return the participant list for a /parliament <topic> invocation.

        Excludes the Secretary from the active debate (it is the user-facing
        coordinator, not a debate participant). Falls back to a Secretary +
        Critic mini-parliament when fewer than two non-Secretary owls exist.
        """
        if self._registry is None:
            return ["secretary", "critic"]
        all_owls = [m.name for m in self._registry.list() if m.name != "secretary"]
        if len(all_owls) >= 2:
            return all_owls
        # Mini-parliament fallback — register critic on demand.
        try:
            self._registry.register(make_critic_persona())
        except ManifestValidationError as exc:
            log.gateway.warning(
                "[commands] parliament._resolve_owls: critic already registered",
                exc_info=exc,
            )
        return ["secretary", "critic"]

    @classmethod
    def create_and_register(
        cls,
        orchestrator: ParliamentOrchestrator | None = None,
        session_store: SessionStore | None = None,
        owl_registry: OwlRegistry | None = None,
        event_bus: EventBus | None = None,
    ) -> ParliamentCommand:
        """Construct a :class:`ParliamentCommand` and register it on the singleton."""
        cmd = cls(
            orchestrator=orchestrator,
            session_store=session_store,
            owl_registry=owl_registry,
            event_bus=event_bus,
        )
        CommandRegistry.instance().register(cmd)
        return cmd
