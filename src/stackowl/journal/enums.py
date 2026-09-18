"""Closed vocabularies for journal events (AD-3, AD-5).

Every enum here is a CLOSED LIST, extended only by adding a member in this
file -- CLAUDE.md's "two copies of one rule" applies to vocabularies as much
as to logic: a second hand-written ``Literal["ok", "failed", ...]`` anywhere
else in the tree is the defect ``health/status.py``'s own ``HealthState``
comment already names.
"""

from __future__ import annotations

from enum import StrEnum


class ActorKind(StrEnum):
    """Who (or what) performed the recorded action -- and, doubling as
    ``JournalEvent.target_kind``, what domain the event's target belongs to.

    AD-3: "Actor and target kinds come from ONE closed list that includes
    ``device``, ``voice_worker`` and ``autonomous``" -- singular, one list for
    both fields, not two. An earlier draft of this module declared a second,
    byte-for-byte identical ``TargetKind`` enum "so the two can diverge later" —
    exactly the "two copies of one rule" this module's own docstring warns
    against, with nothing yet needing the divergence. Collapsed back to one.
    ``CommandContext`` (the richer actor/device/requester-kind carrier) does
    not exist yet -- Epic 4 -- so Story 2.1's six durable-engine transitions
    all record ``AUTONOMOUS`` as actor, which is exactly the case this value
    anticipates.
    """

    OWNER = "owner"
    DEVICE = "device"
    VOICE_WORKER = "voice_worker"
    AUTONOMOUS = "autonomous"


class RecordKind(StrEnum):
    """Which subsystem domain an event type belongs to (AD-2's table list).

    AD-2 names the eventual full set: tasks, jobs, owls, memory, consent,
    heals, deliveries, providers, health, channels, tool calls, model calls,
    delegation hops. Only ``TASK`` is declared here -- Story 2.1 wires only the
    task lifecycle; a later story adds its own member when it wires its own
    emitter, per AD-3's additive-only evolution. Pre-declaring the rest now
    would be vocabulary with no registrant.
    """

    TASK = "task"


class Outcome(StrEnum):
    """The closed-set result every journal row carries."""

    OK = "ok"
    FAILED = "failed"
    HEALED = "healed"
    PARKED = "parked"
    PENDING = "pending"
    DEAD_LETTERED = "dead_lettered"
    EXPIRED = "expired"


class AttentionClass(StrEnum):
    """AD-5's classification: does this event need the owner, or is it ambient.

    Story 2.1 declares this on every :class:`~stackowl.journal.registry.EventTypeSpec`
    (metadata the registry carries) but does NOT compute or write the journal
    row's ``attention``/``intensity`` columns -- that policy is Story 2.2's.
    """

    AMBIENT = "ambient"
    NEEDS_YOU = "needs_you"


class Intensity(StrEnum):
    """AD-5's closed intensity vocabulary -- required when ``attention_class``
    is ``NEEDS_YOU``, forbidden (``None``) when ``AMBIENT``.
    """

    NORMAL = "normal"
    HIGH = "high"
