"""The rendered skill catalogue, frozen for the life of one incarnation.

WHY THIS EXISTS. `catalogue_order_key` sorts by ``(-n_executions, lifecycle_rank,
name)``, and ``n_executions`` increments every time a skill runs. So RUNNING a
skill reorders the catalogue, the rendered block changes bytes, and every live
session's cached prefix is lost.

MEASURED 2026-08-30: 22 of the 259 `[cache] breakpoints: prompt part CHANGED`
warnings name the `skills` part, 15 of them inside one 50-minute window on
2026-08-26 across many short-lived recovery and objective sessions — exactly when
skills were executing heavily.

THE GAP IN THE EXISTING ARGUMENT, named because the code is otherwise careful.
Both the key and its caller argue Law 1 is safe: the key is "equally deterministic
and ... TOTAL: two distinct skills can never compare equal, so the rendered block
can never depend on the order SQLite happened to return rows in", therefore
"byte-identity and Law 1 are untouched". That proves independence from ROW ORDER.
It does not prove stability over TIME, because an INPUT changes. Deterministic is
not stable: a pure function of mutable state still moves when the state moves.

THE SHAPE IS BORROWED, not invented. `CuratedMemory.snapshot_for_prompt` already
freezes the `profile` part per incarnation, and assemble.py states the contract
this follows: "a write made mid-session lands on disk immediately but does not
move the prompt until the next /new". Bounded and MRU-evicting for the same reason
as TurnCostLedger, PlanStore and the curated snapshot — an unbounded
per-conversation map leaks for the life of the process, and evicting the
conversation currently running would restore the bug being removed.

ESC-44 IS UNTOUCHED. Ordering is still by measured value; it is merely sampled
once per incarnation rather than once per turn.

PLACEMENT: its own module rather than inside the injector (which renders and does
not cache) or inside the assemble step (process-global state in a step is the
shape this session has fixed three times). One small file, trivially reversible.
"""

from __future__ import annotations

from collections.abc import Callable

from stackowl.infra.bounded_mru import DEFAULT_MAX_TRACKED, BoundedMRU
from stackowl.infra.observability import log


class CatalogueSnapshot:
    """Per-conversation cache of the RENDERED catalogue block."""

    def __init__(self, max_tracked: int = DEFAULT_MAX_TRACKED) -> None:
        self._by_conversation: BoundedMRU[str, str] = BoundedMRU(
            max_tracked,
            on_evict=lambda evicted: log.skills.debug(
                "[skills] catalogue snapshot: evicted the oldest conversation",
                extra={"_fields": {"evicted_conversation_id": evicted}},
            ),
        )

    def tracked(self) -> int:
        """How many conversations are retained — the bound, observable."""
        return len(self._by_conversation)

    def get(self, conversation_id: str, render: Callable[[], str]) -> str:
        """The frozen block for this incarnation, rendering once on first use.

        An EMPTY render is deliberately not cached: freezing "" would strip the
        catalogue from every remaining turn of that conversation, which is far
        worse than one extra render. A transient store failure must not become a
        session-long capability loss.
        """
        cached = self._by_conversation.peek(conversation_id)
        if cached:
            return cached

        rendered = render()
        if not rendered:
            return rendered

        self._by_conversation.put(conversation_id, rendered)
        log.skills.debug(
            "[skills] catalogue snapshot: frozen for this incarnation",
            extra={"_fields": {
                "conversation_id": conversation_id, "chars": len(rendered),
            }},
        )
        return rendered


_SHARED: CatalogueSnapshot | None = None


def shared_catalogue_snapshot() -> CatalogueSnapshot:
    """The process-wide catalogue snapshot.

    Anything building a system prompt must come through here, or it will re-render
    the catalogue mid-session and move the prompt underneath itself — the same
    warning `shared_memory` carries, for the same reason.
    """
    global _SHARED  # noqa: PLW0603 — one process-wide snapshot, deliberately
    if _SHARED is None:
        _SHARED = CatalogueSnapshot()
    return _SHARED
