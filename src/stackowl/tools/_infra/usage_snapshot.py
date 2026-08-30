"""Per-owl tool usage scores, frozen for the life of one conversation.

WHY THIS EXISTS. D05.2 replaced a lexical `request_text` ranker with MEASURED
per-owl usage, and both `rank_candidates` and `_tool_usage_scores` state that
this makes the presented array "stable for the life of a session by
construction". It does not. It makes the array independent of the QUESTION,
which was the defect D01.3 measured — but the scores are read live from
`TaskOutcomeStore` on EVERY turn, and they move. Same trap the skills catalogue
had: deterministic is not stable, because a pure function of mutable state still
moves when the state moves.

AND IT IS WORSE THAN "MOVES WHEN A TOOL RUNS". `score_tools_for_owl` computes
`now = time.time()` and applies a `half_life_days` decay, so the score is a
continuous function of WALL-CLOCK TIME. Measured 2026-08-30 against the live
database: two reads seconds apart, with no intervening activity, returned
different scores for all 37 scored tools. The ordering can therefore change
between any two turns whenever two decayed scores are close enough to swap — not
occasionally, but structurally. "Stable for the life of a session by
construction" is exactly backwards for a value derived from `time.time()`.

D05.2's own recorded decision was stricter than what shipped: "LEARNED PER-OWL
TOOLSETS, SESSION-BOUNDARY ONLY. Each owl's core set is derived from MEASURED
usage and recomputed at rollover, never mid-session — so cache stability holds
because it changes only where D01.1 already permits change." The session-boundary
half was never implemented. This is it.

MEASURED 2026-08-30, by running D05.2's own closing query with the denominator
read first, as that item instructs: 12 (conversation, owl) pairs have had 2+
turns, so the audit had real opportunity — and it fired TWICE, both in the
operator's live Telegram lane, once dropping 20+ tools and once adding 3 back.
The item predicted "expect silence for repeat pairs".

WHAT THIS DOES NOT FIX, stated because the two are easy to conflate: the presented
set ALSO shrinks as history grows, which D05.2's own
`test_the_real_registry_and_real_budgeter_are_stable_across_turns` confirms is
real. Freezing the ORDERING input cannot stabilise a budget that binds harder each
turn. Which of the two caused those two events is NOT established here — there is
no production-visible record of the presented set at all, because the presentation
path logs at DEBUG and a 19MB log contains no `presentation` line.

THE SHAPE IS BORROWED, not invented — `CatalogueSnapshot` and
`CuratedMemory.snapshot_for_prompt` freeze the `skills` and `profile` prompt parts
the same way, bounded and MRU-evicting for the same reason. This is the THIRD
instance; see DEBT-39 on consolidating them into one primitive.

KEYED ON (conversation_id, owl) — deliberately the same key
`pipeline/cache_audit.py` uses, because that is the unit the prompt cache is
keyed by. Keying on session_key instead would freeze across conversations that
never shared a cached prefix.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping

from stackowl.infra.observability import log

#: How many (conversation, owl) pairs' frozen scores are retained.
_MAX_TRACKED = 64


class UsageScoreSnapshot:
    """Per-conversation freeze of the measured per-owl tool usage scores."""

    def __init__(self, max_tracked: int = _MAX_TRACKED) -> None:
        self._max_tracked = max_tracked
        self._by_key: OrderedDict[tuple[str, str], Mapping[str, float]] = OrderedDict()

    def tracked(self) -> int:
        """How many pairs are retained — the bound, observable."""
        return len(self._by_key)

    async def get(
        self,
        conversation_id: str,
        owl: str,
        read: Callable[[], Awaitable[Mapping[str, float]]],
    ) -> Mapping[str, float]:
        """The frozen scores for this conversation, reading once on first use.

        An EMPTY read is deliberately NOT cached. `_tool_usage_scores` returns {}
        on no-db, no-owl or any read failure, and {} means "order by name" — a
        sound fallback for one turn, but freezing it would pin a whole
        conversation to cold-start order because of a single transient store
        error. Same reasoning as the catalogue snapshot's empty render.
        """
        # 1. ENTRY
        key = (conversation_id, owl)
        cached = self._by_key.get(key)
        if cached:
            self._by_key.move_to_end(key)
            log.engine.debug(
                "[tools] usage snapshot: hit",
                extra={"_fields": {"conversation_id": conversation_id, "owl": owl,
                                   "tools_scored": len(cached)}},
            )
            return cached

        # 2. DECISION — first turn of this conversation: read once, then freeze.
        scores = await read()
        if not scores:
            return {}

        # 3. STEP — store, bound, evict oldest.
        self._by_key[key] = scores
        self._by_key.move_to_end(key)
        while len(self._by_key) > self._max_tracked:
            evicted, _ = self._by_key.popitem(last=False)
            log.engine.debug(
                "[tools] usage snapshot: evicted the oldest conversation",
                extra={"_fields": {"evicted": f"{evicted[0]}:{evicted[1]}"}},
            )
        # 4. EXIT — INFO, not debug. This line is the evidence that the ordering
        #    input was sampled ONCE rather than per turn, and production runs at
        #    INFO. The presentation path's own logging is DEBUG, which is why
        #    there is no production record of the presented set today.
        log.engine.info(
            "[tools] usage snapshot: tool-usage ordering frozen for this conversation",
            extra={"_fields": {"conversation_id": conversation_id, "owl": owl,
                               "tools_scored": len(scores), "tracked": len(self._by_key)}},
        )
        return scores


_SHARED: UsageScoreSnapshot | None = None


def shared_usage_snapshot() -> UsageScoreSnapshot:
    """The process-wide usage-score snapshot.

    Anything ordering the discretionary presented set must come through here, or
    it will re-read the scores mid-conversation and move the tools array
    underneath a cached prefix — the defect this module exists to remove.
    """
    global _SHARED  # noqa: PLW0603 — one process-wide snapshot, deliberately
    if _SHARED is None:
        _SHARED = UsageScoreSnapshot()
    return _SHARED
