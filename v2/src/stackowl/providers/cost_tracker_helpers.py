"""TurnCostLedger — bounded in-memory per-trace running spend total (B2 split).

Extracted from :class:`stackowl.providers.cost_tracker.CostTracker` so the tracker
stays under the B2 line cap. Owns the ONE concern: a bounded ``trace_id -> USD``
running total that the soft cost-pause check (CostPauseGuard) reads on the hot path
WITHOUT a SQLite query. CostTracker composes one instance and folds each recorded
call's cost into it; the guard reads it back via :meth:`turn_cost_usd`.

FIFO-evicts the oldest trace past ``_MAX_TRACKED_TURNS`` so it cannot grow without
limit across a long-lived server process — a turn that old is already finished, so
a later 0.0 read is correct (there is no live turn to pause).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.providers.cost_tracker import CostTracker


def inject_cost_tracker(provider: object, tracker: CostTracker | None) -> None:
    """Set the shared CostTracker on one provider, if it accepts one.

    Guarded by ``getattr``: duck-typed test fakes (not ``ModelProvider``
    subclasses) lack ``set_cost_tracker`` and simply opt out of recording, so the
    providers stay the SINGLE cost-recording site without breaking those fakes.
    """
    setter = getattr(provider, "set_cost_tracker", None)
    if callable(setter):
        setter(tracker)

# Bound on the in-memory per-trace running-total map so it cannot grow without
# limit across a long-lived server process. Past this many DISTINCT trace_ids
# the oldest entry is FIFO-evicted (a turn that old is already finished — its
# total is no longer needed for a live cost-pause check).
_MAX_TRACKED_TURNS = 4096


class TurnCostLedger:
    """Bounded ``trace_id -> accumulated USD`` running total (in-memory only)."""

    def __init__(self, max_tracked_turns: int = _MAX_TRACKED_TURNS) -> None:
        self._max_tracked_turns = max_tracked_turns
        self._turn_totals: OrderedDict[str, float] = OrderedDict()

    def add(self, trace_id: str, cost_usd: float) -> None:
        """Fold ``cost_usd`` into the bounded per-trace running total (hot path).

        Moves the trace to the MRU end (so eviction drops genuinely-old turns,
        not a long-running active one) and FIFO-evicts the oldest entry once the
        map exceeds ``max_tracked_turns``. In-memory only — never touches SQLite.
        """
        prior = self._turn_totals.get(trace_id, 0.0)
        self._turn_totals[trace_id] = prior + cost_usd
        self._turn_totals.move_to_end(trace_id)
        while len(self._turn_totals) > self._max_tracked_turns:
            evicted_id, _evicted_cost = self._turn_totals.popitem(last=False)
            log.engine.debug(
                "[cost_tracker] turn_ledger.add: evicted oldest turn total (bounded)",
                extra={"_fields": {"evicted_trace_id": evicted_id}},
            )

    def total(self, trace_id: str) -> float:
        """Return the accumulated USD spend for ``trace_id`` this server lifetime.

        Returns ``0.0`` for an unknown/empty/evicted trace (no spend recorded, or
        a turn so old it was FIFO-evicted — its work is long finished, so a 0.0
        read is correct: there is no live turn to pause).
        """
        if not trace_id:
            return 0.0
        return self._turn_totals.get(trace_id, 0.0)
