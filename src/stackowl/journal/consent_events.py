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

from pydantic import Field

from stackowl.health.status import remedy_for
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.journal.enums import ActorKind, AttentionClass, Outcome, RecordKind
from stackowl.journal.health import note_failure
from stackowl.journal.ids import new_event_id
from stackowl.journal.models import JournalAttrsBase, JournalEvent, RecordRef
from stackowl.journal.recorder import record as journal_record
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


def _register() -> None:
    get_registry().register(EventTypeSpec(
        type="consent.decided", schema_version=1, attrs_model=ConsentDecisionAttrs,
        emitting_process="tools.consent", record_kind=RecordKind.CONSENT,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        narrate=_narrate_consent_decided,
    ))


_register()


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
