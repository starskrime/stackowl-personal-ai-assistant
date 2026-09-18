"""``journal/`` -- the platform's append-only record of what actually happened.

PLACEMENT: a new top-level package, not nested under ``pipeline/durable/`` or
any other subsystem, even though the only wired emitter today (Story 2.1)
lives in ``pipeline/durable/store.py``. Rejected alternative: nesting it there,
since that is the obvious first home for a feature with exactly one caller.
That was rejected because AD-2's own list of what this table will eventually
hold -- jobs, owls, memory, consent, heals, deliveries, providers, health,
channels, tool calls, model calls, delegation hops, alongside tasks -- means
every one of those subsystems needs to import the recorder later. Nesting it
under ``pipeline/durable/`` would make memory, health and the rest import
across a sibling-subsystem boundary for what AD-7 calls out explicitly as a
cross-cutting concern every subsystem may import, importing nothing from any
of them back -- the same shape ``infra/bounded_mru.py`` and
``skills/catalogue_snapshot.py`` already document rejecting for their own
callers. This package depends only on ``infra/`` (logging, the redactor) and
``health/status`` (the shared ``HealthStatus`` vocabulary) -- never on a
subsystem.

Importing this module imports ``task_events``, ``job_events``, ``heal_events``,
``health_events``, ``turn_events``, ``memory_events`` and ``consent_events``
for their side effect (registering the task, job, heal, health, turn, memory
and consent event types), so any importer of ``journal`` gets a working
registry with no separate registration step to remember.
"""

from __future__ import annotations

from stackowl.journal import consent_events as _consent_events  # noqa: F401 -- registration side effect
from stackowl.journal import heal_events as _heal_events  # noqa: F401 -- registration side effect
from stackowl.journal import health_events as _health_events  # noqa: F401 -- registration side effect
from stackowl.journal import job_events as _job_events  # noqa: F401 -- registration side effect
from stackowl.journal import memory_events as _memory_events  # noqa: F401 -- registration side effect
from stackowl.journal import task_events as _task_events  # noqa: F401 -- registration side effect
from stackowl.journal import turn_events as _turn_events  # noqa: F401 -- registration side effect
from stackowl.journal.attention import ATTENTION_POLICY_VERSION, classify
from stackowl.journal.digest import compute_registry_digest
from stackowl.journal.enums import ActorKind, AttentionClass, Intensity, Outcome, RecordKind
from stackowl.journal.fanout import (
    JournalRow,
    RowFetcher,
    current_max_cursor,
    notify_committed,
    read_since,
    wait_for_commit,
)
from stackowl.journal.ids import new_event_id
from stackowl.journal.models import JournalAttrsBase, JournalEvent, RecordRef
from stackowl.journal.narrator import (
    NameResolver,
    NarrationResult,
    narrate,
    narrate_full,
    narrate_public,
    register_name_resolver,
    reset_name_resolvers_for_tests,
)
from stackowl.journal.recorder import record
from stackowl.journal.registry import EventRegistry, EventTypeSpec, get_registry
from stackowl.journal.write_gate import pause_writes, resume_writes, writes_paused
from stackowl.journal.write_gate import reset_for_tests as reset_write_gate_for_tests

__all__ = [
    "ATTENTION_POLICY_VERSION",
    "ActorKind",
    "AttentionClass",
    "EventRegistry",
    "EventTypeSpec",
    "Intensity",
    "JournalAttrsBase",
    "JournalEvent",
    "JournalRow",
    "NameResolver",
    "NarrationResult",
    "Outcome",
    "RecordKind",
    "RecordRef",
    "RowFetcher",
    "classify",
    "compute_registry_digest",
    "current_max_cursor",
    "get_registry",
    "narrate",
    "narrate_full",
    "narrate_public",
    "new_event_id",
    "notify_committed",
    "pause_writes",
    "read_since",
    "record",
    "register_name_resolver",
    "reset_name_resolvers_for_tests",
    "reset_write_gate_for_tests",
    "resume_writes",
    "wait_for_commit",
    "writes_paused",
]
