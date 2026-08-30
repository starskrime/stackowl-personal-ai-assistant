"""The learned `stable_context` block, frozen for the life of one incarnation.

WHY THIS EXISTS (ESC-67). `stable_context` is learned preferences plus learned
lessons (`classify.py:820`), and it is composed into the FROZEN half of the system
prompt (`assemble.py`, listed in PROMPT_PART_NAMES beside base/persona/skills).
Learning is exactly the thing that changes, so the frozen half was not frozen.

MEASURED: 203 of 259 `[cache] breakpoints: prompt part CHANGED` warnings name
`stable_context` — the single largest invalidator, and roughly 78 times the count
of anything the other fixes addressed. The other two named parts, `profile` (89)
and `skills` (22), were bugs and are fixed; this one was a design tradeoff, and
Bakir's answer on 2026-08-30 was to freeze it per conversation rather than move it
out of the prefix.

WHAT IT COSTS, stated plainly: a preference learned mid-conversation reaches the
prompt on the next `/new` rather than the next turn. That is the SAME tradeoff
`profile`, `skills` and the tool ordering already make, and assemble.py records
the contract it follows — "a write made mid-session lands on disk immediately but
does not move the prompt until the next /new". The recall path is untouched:
`memory_context` is volatile by design and still re-reads every turn, so a
mid-session lesson is still AVAILABLE to the turn, just not baked into the cached
prefix.

WHAT CANNOT BE PROVEN HERE, and is not claimed: this deployment's backend reports
no cache statistics (`cache_stats_reported()` returns "not_reported"), so the
saving cannot be measured directly. The evidence is the invalidation count going
to zero, not a cache-hit rate going up. That is a weaker claim and is left as one.

KEYED ON (incarnation, owl), matching the catalogue snapshot: D01.6 records that
one lane can run several owls, and preferences/lessons are recalled per owl, so
keying on the lane alone would hand the second owl the first owl's block.

EMPTY IS NOT CACHED. An empty `stable_context` is the normal cold state before
anything has been learned; freezing it would pin a whole conversation to "no
learned context" if the first turn happened to precede any recall.
"""

from __future__ import annotations

from stackowl.infra.bounded_mru import DEFAULT_MAX_TRACKED, BoundedMRU
from stackowl.infra.observability import log


class StableContextSnapshot:
    """Per-incarnation freeze of the learned prompt block."""

    def __init__(self, max_tracked: int = DEFAULT_MAX_TRACKED) -> None:
        self._by_incarnation: BoundedMRU[str, str] = BoundedMRU(
            max_tracked,
            on_evict=lambda evicted: log.engine.debug(
                "[pipeline] stable_context snapshot: evicted the oldest incarnation",
                extra={"_fields": {"evicted_incarnation": evicted}},
            ),
        )

    def tracked(self) -> int:
        """How many incarnations are retained — the bound, observable."""
        return len(self._by_incarnation)

    def get(self, incarnation: str, current: str) -> str:
        """The frozen block for this incarnation, taking `current` on first use."""
        cached = self._by_incarnation.peek(incarnation)
        if cached:
            return cached
        if not current:
            return current
        log.engine.info(
            "[pipeline] stable_context: learned block frozen for this incarnation",
            extra={"_fields": {"incarnation": incarnation, "chars": len(current),
                               "tracked": len(self._by_incarnation) + 1}},
        )
        return self._by_incarnation.put(incarnation, current)


_SHARED: StableContextSnapshot | None = None


def shared_stable_context_snapshot() -> StableContextSnapshot:
    """The process-wide stable_context snapshot.

    Anything composing the frozen half of the system prompt must come through
    here, or it will re-read learned content mid-session and move the prefix
    underneath itself — the defect this module exists to remove.
    """
    global _SHARED  # noqa: PLW0603 — one process-wide snapshot, deliberately
    if _SHARED is None:
        _SHARED = StableContextSnapshot()
    return _SHARED
