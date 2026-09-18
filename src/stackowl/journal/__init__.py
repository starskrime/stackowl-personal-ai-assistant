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

Importing this module imports ``task_events`` for its side effect (registering
the four task-lifecycle event types), so any importer of ``journal`` gets a
working registry with no separate registration step to remember.
"""

from __future__ import annotations

from stackowl.journal import task_events as _task_events  # noqa: F401 -- registration side effect
from stackowl.journal.attention import classify
from stackowl.journal.enums import ActorKind, AttentionClass, Intensity, Outcome, RecordKind
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

__all__ = [
    "ActorKind",
    "AttentionClass",
    "EventRegistry",
    "EventTypeSpec",
    "Intensity",
    "JournalAttrsBase",
    "JournalEvent",
    "NameResolver",
    "NarrationResult",
    "Outcome",
    "RecordKind",
    "RecordRef",
    "classify",
    "get_registry",
    "narrate",
    "narrate_full",
    "narrate_public",
    "new_event_id",
    "record",
    "register_name_resolver",
    "reset_name_resolvers_for_tests",
]
