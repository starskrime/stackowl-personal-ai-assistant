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
    #: Story 2.7 — the owl actually RUNNING a turn is the actor of its own
    #: model calls, tool calls and delegation hops (AD-2's "the owl as
    #: actor"). Additive: every existing emitter keeps using OWNER/AUTONOMOUS/
    #: DEVICE/VOICE_WORKER exactly as before.
    OWL = "owl"


class RecordKind(StrEnum):
    """Which subsystem domain an event type belongs to (AD-2's table list).

    AD-2 names the eventual full set: tasks, jobs, owls, memory, consent,
    heals, deliveries, providers, health, channels, tool calls, model calls,
    delegation hops. ``TASK`` was declared by Story 2.1; Story 2.6 adds
    ``JOB``/``HEAL``/``HEALTH`` for the scheduler's job lifecycle, the
    healer's attempt/heal/exhaust cycle and health-status transitions. Each
    later story adds its own member when it wires its own emitter, per AD-3's
    additive-only evolution. Pre-declaring the rest now would be vocabulary
    with no registrant.
    """

    TASK = "task"
    JOB = "job"
    HEAL = "heal"
    HEALTH = "health"
    #: Story 2.7 — model calls, tool calls and delegation hops: the
    #: highest-volume consumer the journal will ever have.
    TURN = "turn"
    #: Story 2.8 — curated md writes (``memory.written``) and the SQLite
    #: reflections learning store (``memory.reflection_recorded``).
    MEMORY = "memory"
    #: Story 2.8 — the consequential-action consent gate's decisions
    #: (``consent.decided``).
    CONSENT = "consent"
    #: Story 2.9 — outbound proactive/scheduled deliveries
    #: (``delivery.attempted``), emitted from ``notifications.deliverer``.
    DELIVERY = "delivery"
    #: Story 2.9 — a genuine fallback-channel reroute (``provider.rerouted``),
    #: emitted from ``notifications.deliverer._maybe_reroute``.
    PROVIDER = "provider"
    #: Story 2.9 — gateway-side channel ingress (``channel.message_received``),
    #: emitted from ``startup.orchestrator``'s gateway-role receive loops.
    CHANNEL = "channel"
    #: Story 3.1 — the cost tracker's daily-budget-warning signal
    #: (``budget.warning``), emitted from ``providers.cost_tracker``.
    COST = "cost"
    #: Story 3.1 — the gateway↔core link's repeated-Hello-mismatch stand-down
    #: (``link.hello_mismatch_standdown``), emitted from
    #: ``startup.orchestrator``.
    LINK = "link"
    #: Story 3.1 — durable Needs-you items themselves (``needs_you.opened``,
    #: ``needs_you.resolved``), emitted from ``journal.recorder.record()``'s
    #: own generic open/close wiring, never from a subsystem call site.
    NEEDS_YOU = "needs_you"
    #: Story 3.3 — the blocking-clarify domain (``clarify.raised``), emitted
    #: from ``interaction.clarify_gateway``. No existing member fits: a
    #: clarifying question is neither a consent decision (``CONSENT``) nor a
    #: durable Needs-you item itself (``NEEDS_YOU``, reserved for the
    #: ``needs_you.opened``/``.resolved`` bookkeeping types).
    INTERACTION = "interaction"
    #: Story 4.6 — standing authority (``authority.granted``/``.revoked``),
    #: emitted from ``authz.standing_authority``'s ``grant``/``revoke``. No
    #: existing member fits: this is neither a per-turn consent decision
    #: (``CONSENT``) nor a job's own lifecycle (``JOB``) — it is the
    #: authz-owned record of WHO may act without the owner attending,
    #: scoped by command type (AD-27, NFR30).
    AUTHORITY = "authority"


class NeedsYouKind(StrEnum):
    """AD-28's closed Needs-you item vocabulary: what KIND of durable item a
    ``NEEDS_YOU`` event opens. Required on ``EventTypeSpec.needs_you_kind``
    when ``attention_class`` is ``NEEDS_YOU``, forbidden otherwise -- the same
    cross-field shape ``intensity`` already has (``registry.py``'s
    ``__post_init__``).

    ``device`` is declared here (Epic 3 context: "device items are declared
    as a kind in this epic") even though no event type registers it yet --
    Telegram delivery for it ships in Epic 5. A closed enum with a currently
    vacuous member is the same "ships correct, zero real callers" shape
    ``RetentionHoldRegistry`` (Story 2.11) already established.
    """

    APPROVAL = "approval"
    QUESTION = "question"
    INCIDENT = "incident"
    ALERT = "alert"
    DEVICE = "device"


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


class HealthErrorCode(StrEnum):
    """Closed vocabulary for ``health.changed``'s ``error_code`` (Story 2.6,
    FR84). AD-4 forbids exception text in ``attrs`` -- this is the bounded,
    closed-enum substitute a health transition's cause is reduced to before it
    ever reaches a journal row.

    Deliberately a SMALL, coarse set rather than one member per exception
    type: a reader of the journal needs "what kind of thing went wrong", not
    a re-hydrated stack trace -- that is what ``remedy_for``'s free-text advice
    is already for, on the ``HealthStatus`` object itself, never stored here.
    """

    TIMEOUT = "timeout"
    CONNECTION_REFUSED = "connection_refused"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


#: Phrase table mirroring ``health/status.py::_SQLITE_REMEDIES``'s own shape --
#: matched in order, first hit wins. Reused structure (type/errno/phrase table),
#: not reused text: `remedy_for` returns operator ADVICE (free text, never
#: stored in the journal); this returns a closed CODE (bounded, journal-safe).
_HEALTH_ERROR_PHRASES: tuple[tuple[str, HealthErrorCode], ...] = (
    ("timed out", HealthErrorCode.TIMEOUT),
    ("timeout", HealthErrorCode.TIMEOUT),
    ("connection refused", HealthErrorCode.CONNECTION_REFUSED),
    ("econnrefused", HealthErrorCode.CONNECTION_REFUSED),
    ("permission denied", HealthErrorCode.PERMISSION_DENIED),
    ("readonly database", HealthErrorCode.PERMISSION_DENIED),
    ("not writable", HealthErrorCode.PERMISSION_DENIED),
    ("no space", HealthErrorCode.RESOURCE_EXHAUSTED),
    ("disk full", HealthErrorCode.RESOURCE_EXHAUSTED),
    ("enospc", HealthErrorCode.RESOURCE_EXHAUSTED),
    ("circuit open", HealthErrorCode.RESOURCE_EXHAUSTED),
    ("out of file descriptors", HealthErrorCode.RESOURCE_EXHAUSTED),
    ("not found", HealthErrorCode.NOT_FOUND),
    ("no such table", HealthErrorCode.NOT_FOUND),
    ("unable to open database file", HealthErrorCode.NOT_FOUND),
)


class ToolCallErrorCode(StrEnum):
    """Closed vocabulary for ``tool.called``'s ``error_code`` (Story 2.7).

    Derived from BRANCH SHAPE at the ``_guarded_dispatch`` call site, never
    from ``ToolResult.error`` -- that is free-form tool-author prose, not
    classifiable safely (AD-4: never exception/free text in ``attrs``). Small
    and closed, matching :class:`HealthErrorCode`'s own "kind of thing went
    wrong, not a re-hydrated message" shape.
    """

    #: The tool exceeded ``_TOOL_DEADLINE_S`` and was cancelled.
    TIMEOUT = "timeout"
    #: The tool reported success but its claimed effect was not observed
    #: (``r.success=True, r.verified=False``) -- B4a's reality check.
    UNVERIFIED_EFFECT = "unverified_effect"
    #: The tool ran and reported failure (``r.success=False``), verified or not.
    TOOL_FAILED = "tool_failed"


def classify_health_error(message: str | None) -> HealthErrorCode:
    """Classify a ``HealthStatus.message`` into a closed :class:`HealthErrorCode`.

    Mirrors ``health/status.py::remedy_for``'s branch structure (a fixed
    phrase table, matched in order) rather than its inputs: ``remedy_for``
    classifies a live exception (type / errno / SQLite phrase); this classifies
    whatever a health contributor already reduced its failure to -- a
    ``HealthStatus.message`` string, the only evidence available at the health
    sweep's own call site (no raw exception survives past the contributor that
    caught it). Only the CODE returned here ever reaches a journal row --
    ``message`` itself never does (AD-4: never exception text). ``None`` or an
    unmatched message classifies as :attr:`HealthErrorCode.UNKNOWN`, never
    raises.
    """
    if not message:
        return HealthErrorCode.UNKNOWN
    text = message.lower()
    for phrase, code in _HEALTH_ERROR_PHRASES:
        if phrase in text:
            return code
    return HealthErrorCode.UNKNOWN
