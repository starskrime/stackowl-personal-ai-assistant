"""ExplainCommand — /explain answers "why did you do that?" (ADR-7 step 3).

Reads the durable per-turn DecisionLedger snapshot (table ``turn_decisions``,
migration 0071) for the current session and renders it with the ledger's own
:func:`~stackowl.infra.decision_ledger.render_why` — so the explanation is a
READ of what was decided, not a confabulation. Durable + cross-process: works
under the gateway/core split and across restarts because the snapshot lives in
SQLite, not in-memory.

Opens a short-lived :class:`DbPool` (like ``/cost``) so it runs from the
slash-command pipeline without the server's running event-loop context.
"""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import CommandMeta
from stackowl.commands.registry import register_command
from stackowl.infra.decision_ledger import render_why
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState

_EXPLAIN_META = CommandMeta(grammar="verb", group="Diagnostics")

_EMPTY = "No decisions were recorded for your last turn."
_HEADER = "Here's why I did what I did last turn:"


class ExplainCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "explain"

    @property
    def description(self) -> str:
        return "Explain why I did what I did on your last turn."

    @property
    def meta(self) -> CommandMeta:
        return _EXPLAIN_META

    async def handle(self, args: str, state: PipelineState) -> str:
        # 1. ENTRY
        log.engine.debug(
            "[commands] explain.handle: entry",
            extra={"_fields": {"session": state.session_id}},
        )
        from stackowl.db.pool import DbPool
        from stackowl.pipeline.decision_store import TurnDecisionStore

        db = DbPool()
        try:
            await db.open()
        except Exception as exc:
            log.engine.error("[commands] explain.handle: db open failed", exc_info=exc)
            return _EMPTY
        try:
            decisions = await TurnDecisionStore(db).latest(state.session_id)
        finally:
            await db.close()

        if not decisions:
            # 4. EXIT — nothing to explain
            log.engine.debug("[commands] explain.handle: exit — no decisions")
            return _EMPTY
        # 4. EXIT — render the durable snapshot
        log.engine.debug(
            "[commands] explain.handle: exit — rendered",
            extra={"_fields": {"count": len(decisions)}},
        )
        return f"{_HEADER}\n{render_why(decisions)}"


_CMD = register_command(ExplainCommand())
