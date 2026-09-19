"""``ConsentDecisionAttrs``, its registration, and the ONE recording helper
``consent.decided`` needs (Story 2.8).

SHAPED LIKE ``turn_events.py``'s three ``record_*`` helpers, not like the
registration-only modules (``task_events.py`` etc.): ``ConsentPolicy._finalize``
has no existing ASYNC DB mutation of its own to piggyback a journal write onto
-- its one existing write, ``audit_logger.append()``, is synchronous and stays
that way (Design Notes: migrating it is explicitly deferred). So this module
owns :func:`record_consent_decision`, which opens its OWN transaction, inserts
a ``consent_decision_records`` row, then calls ``journal.record()`` inside the
SAME commit (AD-24) -- mirroring ``turn_events.py::_record_turn_event``'s shape
exactly: own transaction, the raw insert's own failure path calls
``note_failure`` directly (before ``journal.record()`` ever runs, so recorder.py
never gets a chance to degrade health for THAT failure), then
``journal.record()`` inside the same commit, and an outer ``try/except`` that
only logs -- never re-degrading health a second time for whichever failure
``journal.record()``'s own path (recorder.py) already reported.

Unlike Story 2.7's three types (which skip entirely with no ``trace_id`` --
"a health-probe... call must never be journaled with an empty trace"), a
consent decision is recorded REGARDLESS of trace presence: ``owl_build.py``'s
direct ``gate.policy.request()`` call site may have none, and "did the owner's
gate allow or deny this" is worth recording even then. ``trace_id`` is
therefore optional (``None`` when absent), never a skip condition.

Importing this module registers the type as a side effect;
``journal/__init__.py`` imports it for exactly that reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from stackowl.health.status import remedy_for
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.journal import needs_you
from stackowl.journal.enums import (
    ActorKind,
    AttentionClass,
    Intensity,
    NeedsYouKind,
    Outcome,
    RecordKind,
)
from stackowl.journal.health import note_failure
from stackowl.journal.ids import new_event_id
from stackowl.journal.models import JournalAttrsBase, JournalEvent, RecordRef
from stackowl.journal.recorder import record as journal_record
from stackowl.journal.records import (
    ExpiredRecord,
    get_record_reader_registry,
    read_sqlite_record,
    refuse_unless_owner,
)
from stackowl.journal.registry import EventTypeSpec, get_registry

if TYPE_CHECKING:  # pragma: no cover -- typing-only
    from stackowl.db.pool import DbPool

#: AD-4, verbatim: "attrs hold ids, numbers, closed enums and bounded labels
#: of at most 64 characters" -- the one bound every open string field below
#: enforces, mirroring every other journal attrs module's own constant.
_MAX_LABEL_LEN = 64

_TABLE = "consent_decision_records"

_INSERT_CONSENT_DECISION_SQL = (
    "INSERT INTO consent_decision_records ("
    "id, tool_name, channel, reason, decision, occurred_at"
    ") VALUES (?, ?, ?, ?, ?, ?)"
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ConsentDecisionAttrs(JournalAttrsBase):
    """``consent.decided`` -- one ``ConsentPolicy._finalize`` call completed."""

    tool_name: str = Field(max_length=_MAX_LABEL_LEN)
    channel: str = Field(max_length=_MAX_LABEL_LEN)
    reason: str = Field(max_length=_MAX_LABEL_LEN)
    scope: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)
    category: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)
    decision: Literal["allow", "deny"]


def _narrate_consent_decided(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- no resolver registered for RecordKind.CONSENT (out of scope)
    a = cast(ConsentDecisionAttrs, attrs)
    verb = "allowed" if a.decision == "allow" else "denied"
    return f"Consent {verb} for {a.tool_name} ({a.reason})."


class ConsentRequestedAttrs(JournalAttrsBase):
    """``consent.requested`` (Story 3.3, AD-28) -- a consent request began
    waiting on the owner. Opens an ``approval`` needs_you item."""

    tool_name: str = Field(max_length=_MAX_LABEL_LEN)
    channel: str = Field(max_length=_MAX_LABEL_LEN)
    session_key: str = Field(max_length=_MAX_LABEL_LEN)
    category: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)


def _narrate_consent_requested(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- no resolver registered for RecordKind.CONSENT (out of scope)
    a = cast(ConsentRequestedAttrs, attrs)
    return f"Consent requested for {a.tool_name} on {a.channel}."


class ConsentChannelUnreachableAttrs(JournalAttrsBase):
    """``consent.channel_unreachable`` (Story 3.4) -- a consent request
    arrived on a channel with no registered prompter (and no default). Opens
    an ``incident`` needs_you item, deduplicated per channel."""

    channel: str = Field(max_length=_MAX_LABEL_LEN)
    tool_name: str = Field(max_length=_MAX_LABEL_LEN)


def _narrate_channel_unreachable(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- no resolver registered for RecordKind.CONSENT (out of scope)
    a = cast(ConsentChannelUnreachableAttrs, attrs)
    return (
        f"A consent request for {a.tool_name} on {a.channel} could not be "
        "asked -- no prompter is wired for that channel."
    )


def _register() -> None:
    get_registry().register(EventTypeSpec(
        type="consent.decided", schema_version=1, attrs_model=ConsentDecisionAttrs,
        emitting_process="tools.consent", record_kind=RecordKind.CONSENT,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_consent_decided,
    ))
    get_registry().register(EventTypeSpec(
        type="consent.requested", schema_version=1, attrs_model=ConsentRequestedAttrs,
        emitting_process="tools.consent", record_kind=RecordKind.CONSENT,
        attention_class=AttentionClass.NEEDS_YOU, intensity=Intensity.NORMAL,
        needs_you_kind=NeedsYouKind.APPROVAL,
        # No single owning row -- the request IS the signal; the fresh
        # per-request id (used as target_id below) is the only locator that
        # matters, and it already lives on the event row (spec Design Notes,
        # mirrors budget.warning's/link.hello_mismatch_standdown's own
        # table=None rationale).
        table=None, narrate=_narrate_consent_requested,
    ))
    get_registry().register(EventTypeSpec(
        type="consent.channel_unreachable", schema_version=1,
        attrs_model=ConsentChannelUnreachableAttrs,
        emitting_process="tools.consent", record_kind=RecordKind.CONSENT,
        attention_class=AttentionClass.NEEDS_YOU, intensity=Intensity.HIGH,
        needs_you_kind=NeedsYouKind.INCIDENT,
        # No single owning row -- like `link.hello_mismatch_standdown`, this
        # describes a wiring fault over a channel name, not a table row
        # (spec Code Map, `EventTypeSpec.table`'s own docstring).
        table=None, narrate=_narrate_channel_unreachable,
    ))


_register()


async def record_consent_requested(
    db_pool: DbPool | None,
    *,
    tool_name: str,
    channel: str,
    session_key: str,
    category: str | None,
    expires_at: str,
) -> str | None:
    """Record ``consent.requested`` and bind THIS wait's own
    ``waiter_kind="turn"`` waiter onto the durable ``approval`` item it opens
    -- in ONE transaction (AD-24). Called from ``ConsentPolicy.request()``
    the moment it is about to await its prompter (spec Intent: "the moment
    ... starts waiting").

    Mints a FRESH per-request id, used as BOTH the triggering event's own
    ``target_id`` and the item's dedupe-key target (spec Code Map/Design
    Notes, review pass 1 Group C) -- NEVER ``session_key``, which would
    collapse two distinct, concurrent decisions (including two session-less
    callers that both default to ``session_key=""``) onto the SAME item, so
    whichever ``resolve()`` call lands second would silently adopt the
    first's unrelated answer.

    Returns the bound item's id on success, or ``None`` on: no ``db_pool``
    wired (mirrors :func:`record_consent_decision`'s convention -- every
    unwired test/caller stays byte-identical); any failure along the way
    (never raises -- B5, the decision itself must never block on this).
    """
    # 1. ENTRY
    log.tool.debug(
        "[journal] consent_events.record_consent_requested: entry",
        extra={"_fields": {"tool": tool_name, "channel": channel}},
    )
    # 2. DECISION -- no DbPool wired is a silent no-op.
    if db_pool is None:
        log.tool.debug(
            "[journal] consent_events.record_consent_requested: exit -- no "
            "db_pool wired",
            extra={"_fields": {"tool": tool_name}},
        )
        return None
    try:
        request_id = new_event_id()
        occurred_at = _now_iso()
        # 3. STEP -- record the triggering event (record()'s own NEEDS_YOU
        # wiring opens the item), then bind THIS wait's waiter onto it, both
        # inside the same transaction (AD-24).
        async with db_pool.transaction() as conn:
            await journal_record(conn, JournalEvent(
                type="consent.requested", schema_version=1, occurred_at=occurred_at,
                actor_kind=ActorKind.AUTONOMOUS, actor_id="tools.consent",
                target_kind=ActorKind.OWNER, target_id=request_id,
                outcome=Outcome.OK,
                attrs=ConsentRequestedAttrs(
                    tool_name=tool_name, channel=channel, session_key=session_key,
                    category=category,
                ),
            ))
            item_id = await needs_you.bind_waiter(
                conn, kind=NeedsYouKind.APPROVAL, target_kind=ActorKind.OWNER.value,
                target_id=request_id, waiter_kind=needs_you.WAITER_KIND_TURN,
                waiter_id=request_id, expires_at=expires_at,
            )
    except Exception as exc:
        log.tool.error(
            "[journal] consent_events.record_consent_requested: FAILED -- "
            "the prompt still proceeds; only its durable item is missing",
            exc_info=exc,
            extra={"_fields": {"tool": tool_name, "channel": channel}},
        )
        return None
    # 4. EXIT
    log.tool.debug(
        "[journal] consent_events.record_consent_requested: exit",
        extra={"_fields": {"tool": tool_name, "item_id": item_id}},
    )
    return item_id


async def record_channel_unreachable(
    db_pool: DbPool | None, *, channel: str, tool_name: str,
) -> None:
    """Record ``consent.channel_unreachable`` -- ``RoutingPrompter`` found no
    prompter for ``channel`` (and no default). Never opens a waiter (nobody
    waits on an incident) -- ``record()``'s own NEEDS_YOU wiring opens the
    deduplicated ``incident`` item automatically, keyed by ``channel`` (spec
    Code Map: "one deduplicated `incident` needs_you item per channel,
    mirrors Story 3.1's `ON CONFLICT ... DO NOTHING` dedupe").

    A no-op (``db_pool is None``) mirrors every other helper in this module
    -- the DENY this accompanies still happens regardless (Boundaries:
    "``db_pool=None`` ... is a no-op journal write ... DENY still happens").
    Never raises (B5): a journaling failure must never turn a fail-closed
    deny into something louder.
    """
    # 1. ENTRY
    log.tool.debug(
        "[journal] consent_events.record_channel_unreachable: entry",
        extra={"_fields": {"channel": channel, "tool": tool_name}},
    )
    # 2. DECISION -- no DbPool wired is a silent no-op.
    if db_pool is None:
        log.tool.debug(
            "[journal] consent_events.record_channel_unreachable: exit -- "
            "no db_pool wired",
            extra={"_fields": {"channel": channel}},
        )
        return
    try:
        # 3. STEP
        async with db_pool.transaction() as conn:
            await journal_record(conn, JournalEvent(
                type="consent.channel_unreachable", schema_version=1,
                occurred_at=_now_iso(),
                actor_kind=ActorKind.AUTONOMOUS, actor_id="tools.consent",
                target_kind=ActorKind.OWNER, target_id=channel,
                outcome=Outcome.FAILED,
                attrs=ConsentChannelUnreachableAttrs(
                    channel=channel, tool_name=tool_name,
                ),
            ))
    except Exception as exc:
        log.tool.error(
            "[journal] consent_events.record_channel_unreachable: FAILED -- "
            "the deny still stands; only its durable incident is missing",
            exc_info=exc,
            extra={"_fields": {"channel": channel, "tool": tool_name}},
        )
        return
    # 4. EXIT
    log.tool.debug(
        "[journal] consent_events.record_channel_unreachable: exit -- recorded",
        extra={"_fields": {"channel": channel}},
    )


async def record_consent_decision(
    db_pool: DbPool | None,
    *,
    tool_name: str,
    channel: str,
    session_key: str,
    category: str | None,
    reason: str,
    scope: str | None,
    allowed: bool,
) -> None:
    """Record one ``consent.decided`` event, in its OWN transaction (AD-24).

    Called from ``ConsentPolicy._finalize`` AFTER the existing (untouched)
    synchronous ``audit_logger.append()`` call. Never raises (B5): the
    decision's ``allowed`` return value must never block on this -- any
    failure (no ``db_pool``, the raw insert, ``journal.record()`` itself) is
    logged at ERROR and swallowed.

    Recorded regardless of ``trace_id`` presence -- unlike Story 2.7's
    turn-scoped events, a consent decision is always worth recording, since
    ``owl_build.py``'s direct ``gate.policy.request()`` call site may have
    none.

    A no-op (decision unaffected, nothing journaled) when ``db_pool`` is
    ``None`` -- the fallback ``ConsentPolicy()`` used by ``registry.py`` and
    every test that does not wire one.
    """
    # 1. ENTRY
    log.tool.debug(
        "[journal] consent_events.record_consent_decision: entry",
        extra={"_fields": {"tool": tool_name, "channel": channel, "allowed": allowed}},
    )
    # 2. DECISION -- no DbPool wired is a silent no-op.
    if db_pool is None:
        log.tool.debug(
            "[journal] consent_events.record_consent_decision: exit -- no db_pool wired",
            extra={"_fields": {"tool": tool_name}},
        )
        return
    trace_id = TraceContext.get().get("trace_id")
    decision: Literal["allow", "deny"] = "allow" if allowed else "deny"
    outcome = Outcome.OK if allowed else Outcome.FAILED
    try:
        row_id = new_event_id()
        occurred_at = _now_iso()
        async with db_pool.transaction() as conn:
            try:
                await conn.execute(_INSERT_CONSENT_DECISION_SQL, (
                    row_id, tool_name, channel, reason, decision, occurred_at,
                ))
            except Exception as exc:
                # The raw insert failed BEFORE journal.record() ever ran, so
                # recorder.py's own failure path never fires for this one --
                # degrade health here, the only place that will.
                remedy = remedy_for(exc) or f"{_TABLE} insert failed: {exc}"
                note_failure(remedy)
                raise
            await journal_record(conn, JournalEvent(
                type="consent.decided", schema_version=1, occurred_at=occurred_at,
                actor_kind=ActorKind.OWNER, actor_id=session_key,
                target_kind=ActorKind.OWNER, target_id=tool_name, outcome=outcome,
                record_ref=RecordRef(kind="sqlite", locator={"table": _TABLE, "id": row_id}),
                attrs=ConsentDecisionAttrs(
                    tool_name=tool_name, channel=channel, reason=reason,
                    scope=scope, category=category, decision=decision,
                ),
                trace_id=str(trace_id) if trace_id else None,
            ))
    except Exception as exc:
        # journal.record() already degraded health itself on its own failure
        # path (recorder.py) whenever IT is the one that raised -- this catch
        # exists so NEITHER that nor the raw-insert/attrs-validation failure
        # above can ever propagate into `ConsentPolicy._finalize`, which must
        # return its `allowed` bool regardless (B5).
        log.tool.error(
            "[journal] consent_events.record_consent_decision: FAILED -- the "
            "decision itself already happened; only its journal row is missing",
            exc_info=exc,
            extra={"_fields": {"tool": tool_name, "channel": channel}},
        )
        return
    # 4. EXIT
    log.tool.debug(
        "[journal] consent_events.record_consent_decision: exit -- recorded",
        extra={"_fields": {"tool": tool_name, "decision": decision}},
    )


class ConsentDecisionRecordView(BaseModel):
    """Typed view of one ``consent_decision_records`` row -- the registered
    reader's return shape for ``RecordKind.CONSENT``/``sqlite`` (AD-4)."""

    model_config = ConfigDict(frozen=True)

    id: str
    tool_name: str
    channel: str
    reason: str
    decision: str
    occurred_at: str


async def read_consent_decision_record(
    db_pool: DbPool | None, locator: dict[str, str], *, owner_id: str,
) -> ConsentDecisionRecordView | ExpiredRecord:
    """The registered reader for ``RecordKind.CONSENT``/``sqlite`` (AD-4):
    opens the ``consent_decision_records`` row a ``consent.decided`` event's
    ``record_ref`` points at."""
    refuse_unless_owner(RecordKind.CONSENT, owner_id)
    row = await read_sqlite_record(
        db_pool, table=_TABLE, id_column="id",
        id_value=locator.get("id", ""), view_model=ConsentDecisionRecordView,
    )
    if row is None:
        return ExpiredRecord(
            record_kind=RecordKind.CONSENT, locator=locator,
            reason="the consent_decision_records row this event referenced is gone",
        )
    return cast(ConsentDecisionRecordView, row)


get_record_reader_registry().register(
    RecordKind.CONSENT, "sqlite", read_consent_decision_record,
)
