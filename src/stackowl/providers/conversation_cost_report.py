"""Report what a conversation cost, at the moment it ends.

DEBT-7 (Bakir, 2026-07-26): the budget signal is INFORMATIVE ONLY — it tells
him what a conversation is costing and never blocks, gates, throttles or aborts
a turn. He chose per-conversation reporting over a daily threshold, because
"what did that conversation cost" is the question he actually has; a daily cap
answers a different one and, as ``CostTracker.record()`` used to prove, invites
a refusal nobody asked for.

This rides D01.7's ``session.rollover`` seam rather than adding a scheduler:
one boundary, many consumers — dedup target X3's principle, and the reason that
seam was built as an event rather than a direct call. The rollover summary
consumer in ``memory/rollover_summary_handler.py`` is the sibling that
established the pattern, including its rule that a consumer must NEVER break
the boundary: the conversation starting matters more than its receipt.

Every dollar this reports inherits DEBT-15 — the live model is absent from
``pricing.yaml``, so the figures are fallback-derived. The call COUNT is exact.
"""

from __future__ import annotations

from typing import Any

from stackowl.infra.observability import log

#: Emitted once per priced boundary. Declared as a publisher in
#: ``startup/orchestrator.py`` so the wiring audit can tell wired from dangling.
COST_REPORT_EVENT = "conversation_cost_report"


def register_conversation_cost_consumer(event_bus: object, tracker: object) -> None:
    """Subscribe a cost report to the session-rollover boundary.

    ``tracker`` is anything exposing ``async session_total(session_key,
    conversation_id)`` — duck-typed rather than imported so this module does not
    depend on ``CostTracker``'s construction order at startup.
    """
    from stackowl.sessions.store import SessionStore

    async def _on_rollover(payload: dict[str, Any] | None) -> None:
        data = payload or {}
        lane = str(data.get("session_key") or "")
        ended = str(data.get("old_conversation_id") or "")
        # The sweeper legitimately publishes new_conversation_id=None (it finalises
        # without minting) — that is fine. A missing OLD id is what means
        # nothing actually finished, so there is nothing to price.
        if not lane or not ended:
            log.engine.debug(
                "[cost] conversation_cost: nothing ended — not reporting",
                extra={"_fields": {"session_key": lane, "old_conversation_id": ended}},
            )
            return
        try:
            summary = await tracker.session_total(lane, ended)  # type: ignore[attr-defined]
        except Exception as exc:
            # Never break the boundary for a receipt.
            log.engine.warning(
                "[cost] conversation_cost: could not price the boundary — skipped",
                exc_info=exc,
                extra={"_fields": {"session_key": lane, "ended_conversation_id": ended}},
            )
            return
        if summary.call_count == 0 or summary.total_usd <= 0:
            log.engine.debug(
                "[cost] conversation_cost: nothing spent — not reporting",
                extra={"_fields": {"session_key": lane, "ended_conversation_id": ended}},
            )
            return
        # DEBT-15 — hedge ONLY when the total contains guesses. This message can
        # be delivered to the user, so a fallback-derived figure stated as a
        # receipt is the most damaging place for false precision. The hedge has
        # to be earned, or the caveat becomes noise everyone learns to skip.
        estimated = not getattr(summary, "all_priced", True)
        message = (
            f"That conversation cost {'approximately ' if estimated else ''}"
            f"${summary.total_usd:.4f} over {summary.call_count} model call(s)."
            + (" (This model has no published price, so the figure is a "
               "conservative estimate.)" if estimated else "")
        )
        log.engine.info(
            "[cost] conversation_cost: exit — reporting a finished conversation",
            extra={"_fields": {
                "session_key": lane,
                "conversation_id": ended,
                "total_usd": summary.total_usd,
                "call_count": summary.call_count,
                "owl_name": data.get("owl_name"),
                "channel": data.get("channel"),
            }},
        )
        event_bus.emit(COST_REPORT_EVENT, {  # type: ignore[attr-defined]
            "session_key": lane,
            "conversation_id": ended,
            "total_usd": summary.total_usd,
            "call_count": summary.call_count,
            "owl_name": data.get("owl_name"),
            "channel": data.get("channel"),
            "estimated": estimated,
            "message": message,
        })

    event_bus.subscribe(SessionStore.ROLLOVER_EVENT, _on_rollover)  # type: ignore[attr-defined]
    log.engine.info(
        "[cost] conversation_cost: subscribed",
        extra={"_fields": {
            "event": SessionStore.ROLLOVER_EVENT,
            "emits": COST_REPORT_EVENT,
        }},
    )
