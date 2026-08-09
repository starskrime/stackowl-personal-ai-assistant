"""NewConversationCommand — ``/new`` starts a fresh conversation (D01.7).

``/new`` and ``/reset`` are deliberately different verbs:

* ``/new``   ends this conversation and begins another. Nothing is destroyed —
  the transcript stays queryable and the rollover is announced, so the memory
  summary and background review get their chance at it.
* ``/reset`` is the DESTRUCTIVE one: it deletes the conversation history.

Bakir's Q8 answer, and the reference platform' meaning too — their READMEs document ``/new`` as
"start a fresh conversation". The mid-turn "queue this separately instead of
steering" signal that previously owned this token moved to ``/queue``; the
capability was renamed, never removed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.pipeline.state import PipelineState
    from stackowl.sessions.store import SessionStore


class NewConversationCommand(SlashCommand):
    """``/new`` — end this conversation and start a fresh one."""

    def __init__(self, store: SessionStore | None = None) -> None:
        log.gateway.debug("[commands] new.init: entry")
        self._store = store
        log.gateway.debug("[commands] new.init: exit")

    @property
    def command(self) -> str:
        return "new"

    @property
    def description(self) -> str:
        return "Start a fresh conversation (keeps your history)."

    async def handle(self, args: str, state: PipelineState) -> str:  # noqa: ARG002
        # 1. ENTRY
        log.gateway.debug(
            "[commands] new.handle: entry",
            extra={"_fields": {"session_key": state.session_key}},
        )
        # 2. DECISION — refuse honestly rather than claim a boundary that did not happen
        if self._store is None:
            log.gateway.error(
                "[commands] new.handle: no session store — cannot start a conversation",
                extra={"_fields": {"session_key": state.session_key}},
            )
            return "Sessions are not configured, so I cannot start a new conversation."

        # 3. STEP — RECORD the intent; the next lane resolution performs it.
        #
        # This used to call start_new_incarnation(state.session_key) directly and
        # it NEVER worked (found live 2026-07-27). Commands dispatch at the
        # gateway, BEFORE routing, and the composite lane is keyed on the OWL —
        # a routing OUTPUT. So state.session_key here is still the channel-native
        # id ("72055773") while the stored lane is
        # "owl:secretary:telegram:dm:72055773". The lookup found nothing, the
        # command returned "You're already in a new conversation", and the
        # conversation carried on unchanged.
        #
        # The reset now happens in resolve_for, the one place that already knows
        # how to end a lane, at the first moment the composite key exists.
        try:
            await self._store.request_new_incarnation(state.session_key)
        except Exception as exc:
            log.gateway.error(
                "[commands] new.handle: could not record the request",
                exc_info=exc,
                extra={"_fields": {"session_key": state.session_key}},
            )
            return "I could not start a new conversation just now."

        # 4. EXIT
        log.gateway.info(
            "[commands] new.handle: exit — next turn starts a fresh conversation",
            extra={"_fields": {"session_key": state.session_key}},
        )
        return "Starting a new conversation. Your history is still here if I need it."
