"""AD-3's coverage tripwire: "every migration-created table is listed in the
registry with its event types, or marked ``unjournaled`` with a reason, and
the tripwire diffs the migrated table set against the registry" (Story 2.10).

PLACEMENT: inside ``journal/`` itself, not a new package -- this module is
one more piece of the read-model's own self-check machinery (AD-2's coverage
guarantee), sitting alongside ``registry.py`` the same way
``health/store_cadence.py`` sits alongside the health sweep it backs. It
imports nothing but ``registry.py`` (still `journal/`'s own package: AD-7's
"imports nothing from any subsystem" stays true here too).

``UNJOURNALED_TABLES`` is the other operand of the coverage diff:
``registry.tables_covered()`` names every table at least one registered event
type's ``record_ref`` points into; this dict names every OTHER live table and
states, explicitly and reviewably, why it is not a gap. Three shapes:

* the ~50 tables that pre-date Epic 2 entirely (every store
  ``health/store_cadence.py::DECLARATIONS`` already enumerates that this
  story's nine ``*_events.py`` registrations do not cover) -- one shared
  categorical reason, mirroring ``store_cadence.py``'s own bucketed
  ``Cadence`` classes rather than inventing a bespoke sentence per table;
* ``audit_log`` -- DW-25's own resolution: ``BatchAuditor`` writes it and is
  explicitly out of this story's scope (spec Boundaries: "Do not touch
  BatchAuditor/AuditLogger/audit_log (DW-25)"), so it is marked excused here
  instead of journaled;
* ``journal_events`` -- the ledger table itself, not a target anything's
  ``record_ref`` ever points AT.

WHY A DICT AND NOT A DERIVED SET. The same reason ``store_cadence.py``'s own
``DECLARATIONS`` is a literal tuple rather than a query over the schema: the
REASON is the point. A table that silently stops needing an excuse (because a
later story journals it) must be deleted from here by a person reading why it
was here, not by a query that stops finding it.
"""

from __future__ import annotations

from collections.abc import Iterable

from stackowl.journal.registry import get_registry

#: Shared reason for every table that pre-dates Epic 2's journal and has no
#: registered event type covering it yet. Mirrors
#: ``health/store_cadence.py``'s own bucketed-``Cadence`` precedent: one
#: reviewable class, not fifty hand-typed sentences that would say the same
#: thing fifty different ways. Story 2.10 ships the coverage tripwire and the
#: registration API these nine ``*_events.py`` modules already use -- it does
#: not retroactively journal every pre-existing table, which is future-epic
#: scope (Epic 2's own Requirements text: "a coverage check fails on any
#: migrated table with no registered events or 'unjournaled' excuse" -- an
#: excuse, stated here, is exactly what this class is).
_REASON_PRE_EPOCH = (
    "pre-Epic-2 table: no journal event type covers it yet. Not a silent "
    "gap -- explicitly excused here, the same 'declared rather than "
    "guessed' move health/store_cadence.py's own Cadence registry makes, "
    "so a future epic that DOES journal it removes this entry rather than "
    "the coverage tripwire quietly starting to pass around it."
)

#: DW-25's resolution: ``BatchAuditor.grant``/``reject``/``action``
#: (``tools/interaction/_batch_support.py``) writes ``audit_log`` and is a
#: structurally distinct, lower-severity (``write``, not ``consequential``)
#: flow the consequential-action consent gate (``consent.decided``,
#: Story 2.8) does not decide -- the spec's own Boundaries text names it out
#: of scope rather than an oversight. Left unjournaled, excused here instead
#: of silently dropped.
_REASON_AUDIT_LOG = (
    "audit_log is BatchAuditor's own hash-chained evidence store "
    "(tools/interaction/_batch_support.py), structurally distinct from the "
    "consequential-action consent gate consent.decided journals -- DW-25's "
    "resolution: excused here rather than journaled by this story."
)

#: The ledger table itself is not a TARGET any event's ``record_ref`` ever
#: points at -- it is what every ``record_ref`` and every registered type
#: together produce. Excusing it here (rather than pretending some event
#: type "covers" it) keeps ``tables_covered()`` meaning exactly what it says:
#: tables a record_ref can open, never the journal table that opens them.
_REASON_JOURNAL_EVENTS = (
    "journal_events is the append-only ledger itself (AD-2), not a target "
    "any event's record_ref points at -- there is no 'event type that "
    "covers the journal', only events the journal contains."
)

#: Every currently-live table this story's nine ``*_events.py`` registrations
#: do not cover, mapped to a stated, reviewable reason it is not a coverage
#: gap (AD-3). Registry-covered tables (``tasks``, ``jobs``, ``heal_attempts``,
#: ``health_status_changes``, ``turn_action_records``, ``reflections``,
#: ``consent_decision_records``, ``delivery_records``,
#: ``channel_ingress_records``) are deliberately ABSENT from this dict --
#: they are covered by ``registry.tables_covered()`` instead, and a table
#: that is both covered and excused is exactly the double-booking
#: ``test_coverage_tripwire.py``'s sanity check catches.
UNJOURNALED_TABLES: dict[str, str] = {
    "approach_rating_pending": _REASON_PRE_EPOCH,
    "cache_breakpoint_probes": _REASON_PRE_EPOCH,
    "callback_log": _REASON_PRE_EPOCH,
    "channel_liveness": _REASON_PRE_EPOCH,
    "command_sequence_edges": _REASON_PRE_EPOCH,
    "command_sequence_last": _REASON_PRE_EPOCH,
    "committed_facts": _REASON_PRE_EPOCH,
    "contradiction_scan_state": _REASON_PRE_EPOCH,
    "conversation_summaries": _REASON_PRE_EPOCH,
    "conversations": _REASON_PRE_EPOCH,
    "cost_records": _REASON_PRE_EPOCH,
    # Pre-Epic-2 legacy table -- distinct from `delivery_records` (Story 2.9,
    # `delivery_events.py`, registry-covered below via `tables_covered()`),
    # which the near-identical name could otherwise be mistaken for.
    "delivery_attempts": _REASON_PRE_EPOCH,
    "dna_checkpoints": _REASON_PRE_EPOCH,
    "job_results": _REASON_PRE_EPOCH,
    "job_runs": _REASON_PRE_EPOCH,
    "learning_artifacts": _REASON_PRE_EPOCH,
    "lessons": _REASON_PRE_EPOCH,
    "message_ledger": _REASON_PRE_EPOCH,
    "messages": _REASON_PRE_EPOCH,
    "notification_log": _REASON_PRE_EPOCH,
    "notification_overrides": _REASON_PRE_EPOCH,
    "notification_queue": _REASON_PRE_EPOCH,
    "objective_events": _REASON_PRE_EPOCH,
    "objective_subgoals": _REASON_PRE_EPOCH,
    "objectives": _REASON_PRE_EPOCH,
    "onboarding": _REASON_PRE_EPOCH,
    "onboarding_events": _REASON_PRE_EPOCH,
    "owl_dna": _REASON_PRE_EPOCH,
    "owl_dna_authored": _REASON_PRE_EPOCH,
    "owl_profiles": _REASON_PRE_EPOCH,
    "owls": _REASON_PRE_EPOCH,
    "parliament_sessions": _REASON_PRE_EPOCH,
    "plugins": _REASON_PRE_EPOCH,
    "principals": _REASON_PRE_EPOCH,
    "schema_migrations": _REASON_PRE_EPOCH,
    "session_prompts": _REASON_PRE_EPOCH,
    "sessions": _REASON_PRE_EPOCH,
    "side_effect_ledger": _REASON_PRE_EPOCH,
    "skill_audit": _REASON_PRE_EPOCH,
    "skill_ownership": _REASON_PRE_EPOCH,
    "skills": _REASON_PRE_EPOCH,
    "stackowl_meta": _REASON_PRE_EPOCH,
    "staged_facts": _REASON_PRE_EPOCH,
    "task_outcomes": _REASON_PRE_EPOCH,
    "thread_registry": _REASON_PRE_EPOCH,
    "tool_heuristics": _REASON_PRE_EPOCH,
    "turn_decisions": _REASON_PRE_EPOCH,
    "undelivered_outbox": _REASON_PRE_EPOCH,
    "user_preferences": _REASON_PRE_EPOCH,
    "webhook_events_log": _REASON_PRE_EPOCH,
    "audit_log": _REASON_AUDIT_LOG,
    "journal_events": _REASON_JOURNAL_EVENTS,
}


def coverage_gap(live_tables: Iterable[str]) -> frozenset[str]:
    """Every live table neither registry-covered nor excused (AD-3).

    An empty result is the tripwire passing. A non-empty one names exactly
    the tables a new migration added with no registration and no excuse --
    AC4's "the coverage tripwire fails and names it".
    """
    return frozenset(live_tables) - get_registry().tables_covered() - UNJOURNALED_TABLES.keys()
