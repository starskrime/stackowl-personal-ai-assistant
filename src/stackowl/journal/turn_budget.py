"""Provisional per-``trace_id`` journal write budget (Story 2.7, AD-38).

AD-38: "Each turn and each command has a journal write budget (events per
``trace_id``) … Exceeding it logs a WARNING and degrades the journal health
contributor; it never drops events." This module owns exactly that check —
NOT the real budget spike B2/Story 2.12 will produce. Mirrors
``journal/retention.py``'s own provisional-constant precedent: a placeholder
that makes the check HONEST (it genuinely fires on a real runaway loop)
without pretending to be a measured number.

WHY 100, NOT A MEASURED VALUE. No measured per-turn model/tool/delegation
count exists anywhere in this codebase yet — this story is what FIRST wires
the three call sites that would produce one. A round, deliberately generous
ceiling: high enough that no known-ordinary turn (a handful of tool calls, at
most a few delegation hops, one model round per tool-loop iteration) should
ever cross it, low enough to still catch a genuine runaway (an infinite
tool-call loop, a delegation cycle). Story 2.12's benchmark replaces this
with a real number — logged to
``_bmad-output/implementation-artifacts/deferred-work.md``.
"""

from __future__ import annotations

import threading

from stackowl.infra.bounded_mru import BoundedMRU
from stackowl.infra.observability import log
from stackowl.journal.health import note_budget_exceeded

#: PROVISIONAL — see module docstring. Story 2.12's benchmark sets the real
#: value; nothing here is a measured production number.
PROVISIONAL_TURN_EVENT_BUDGET = 100

#: Distinct traces tracked before the least-recently-touched is evicted.
#: Mirrors `BoundedMRU`'s own `DEFAULT_MAX_TRACKED` shape — bounded so a
#: long-lived server process cannot grow this map without limit across many
#: turns. A turn old enough to be evicted is long finished, so losing its
#: counter is correct: there is no live turn left to warn about.
_MAX_TRACKED_TRACES = 4096

_counts: BoundedMRU[str, int] = BoundedMRU(max_tracked=_MAX_TRACKED_TRACES)
_lock = threading.Lock()


def check_and_note(trace_id: str) -> bool:
    """Increment this trace's event counter and report whether it is now
    OVER budget. Called by each of the three ``turn_events.py`` recording
    helpers BEFORE their own insert — recording proceeds regardless of the
    return value (AD-38: never drops an event, only warns and degrades).

    On the transition into over-budget (not on every subsequent call — a
    runaway loop must not flood the log or the health contributor with one
    WARNING per event), logs once and calls
    :func:`~stackowl.journal.health.note_budget_exceeded`.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] turn_budget.check_and_note: entry",
        extra={"_fields": {"trace_id": trace_id}},
    )
    with _lock:
        prior = _counts.peek(trace_id) or 0
        count = prior + 1
        _counts.put(trace_id, count)
    # 2. DECISION -- only the crossing tick (prior at budget, this one over)
    # fires the warning; every later event on the same trace stays silently
    # recorded (still never dropped) without re-warning.
    over_budget = count > PROVISIONAL_TURN_EVENT_BUDGET
    if count == PROVISIONAL_TURN_EVENT_BUDGET + 1:
        log.journal.warning(
            "[journal] turn_budget.check_and_note: per-turn write budget "
            "exceeded — recording continues (AD-38 never drops an event)",
            extra={"_fields": {
                "trace_id": trace_id, "count": count,
                "budget": PROVISIONAL_TURN_EVENT_BUDGET,
            }},
        )
        note_budget_exceeded(trace_id)
    # 4. EXIT
    log.journal.debug(
        "[journal] turn_budget.check_and_note: exit",
        extra={"_fields": {"trace_id": trace_id, "count": count, "over_budget": over_budget}},
    )
    return over_budget


def reset_for_tests() -> None:
    """Clear every tracked trace's counter. Test-only."""
    with _lock:
        _counts.clear()
