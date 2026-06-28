"""DeliveryDecision — the ONE authoritative give-up verdict for a turn (PA0).

"Did this turn give up on a consequential outcome?" was re-derived at several sites,
each re-calling the predicates over the consequential snapshot, so they could drift.
This frozen record bundles that verdict plus its supporting derived data behind ONE
function — :func:`stackowl.pipeline.giveup_floor.decide_delivery` — so the give-up
verdict has a single owner every site calls. The seam later stories (escalation
ladder) hook into.

The single source of truth is that FUNCTION (computed from the FINAL state at read
time), not a stamped field: a memoized verdict would go stale when a later evolve sets
``budget_capped`` (which flips the success tally and thus the verdict). Pure data — no
logic, no heavy imports.
"""

from __future__ import annotations

from pydantic import BaseModel


class DeliveryDecision(BaseModel, frozen=True):
    """The single, computed-once give-up verdict for a turn."""

    #: True iff a consequential/write action was attempted-and-failed with NO
    #: consequential success AND at least one such failure was not bridged this turn.
    #: Equals ``is_consequential_giveup_now(state)`` — THE give-up verdict.
    consequential_giveup: bool = False
    #: Names of consequential failures NOT bridged by a substitution this turn.
    #: Equals ``_unrecovered_consequential_failures(state)``. Read by the overclaim gate.
    unrecovered_failures: frozenset[str] = frozenset()
    #: The consequential failure to name in the honest floor (first unrecovered, in
    #: snapshot/ledger order), or None. Equals the floor's ``failed_name``.
    failed_capability: str | None = None
