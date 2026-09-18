"""Attrs models for ``model.called``, ``tool.called`` and ``delegation.hopped``,
their registration, and the THREE recording helpers that call them (Story 2.7).

WHY THIS MODULE IS SHAPED DIFFERENTLY FROM ``task_events.py``/``job_events.py``/
``heal_events.py``/``health_events.py``. Those four are registration-only: the
subsystem that owns the state change already opens its own
``async with self._db.transaction() as conn:`` block for that change, and calls
``journal.record(conn, ...)`` directly inside it. The three call sites this
story wires -- ``ModelProvider._dispatch_post_llm`` (providers/base.py),
``_guarded_dispatch`` (pipeline/steps/execute.py), ``A2ADelegator.delegate``
(owls/a2a_delegation.py) -- have NO existing state mutation of their own to
piggyback a journal write onto (the Boundaries contract says so explicitly:
"each ... opens its OWN ``DbPool.transaction()``"). So this module also owns
the three ``record_*`` helpers those call sites invoke directly, each opening
its own transaction, inserting the ``turn_action_records`` row, then calling
``journal.record()`` inside the SAME commit (AD-24).

ONE emitting process PER TYPE, not one for the whole module: ``model.called``
only ever fires from ``providers.base``, ``tool.called`` only from
``pipeline.steps.execute``, ``delegation.hopped`` only from
``owls.a2a_delegation`` -- three distinct chokepoints, matching AD-3's "one
emitting process" field taken literally per type.

Importing this module registers all three types as a side effect;
``journal/__init__.py`` imports it for exactly that reason.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from pydantic import Field

from stackowl.health.status import remedy_for
from stackowl.infra.observability import log, redact_secret_shapes
from stackowl.infra.trace import TraceContext
from stackowl.journal.enums import ActorKind, AttentionClass, Outcome, RecordKind
from stackowl.journal.health import note_failure
from stackowl.journal.ids import new_event_id
from stackowl.journal.models import JournalAttrsBase, JournalEvent, RecordRef
from stackowl.journal.recorder import record as journal_record
from stackowl.journal.registry import EventTypeSpec, get_registry
from stackowl.journal.turn_budget import check_and_note

if TYPE_CHECKING:  # pragma: no cover -- typing-only
    from stackowl.db.pool import DbPool

#: AD-4, verbatim: "attrs hold ids, numbers, closed enums and bounded labels
#: of at most 64 characters" -- the one bound every open string field below
#: enforces, mirroring every other journal attrs module's own constant.
_MAX_LABEL_LEN = 64

#: Mirrors ``tools/base.py::ToolManifest.action_severity``'s own closed
#: Literal -- COPIED, not imported: ``journal/`` imports nothing from any
#: subsystem (AD-7, this module's own package docstring), so a real import of
#: ``ToolManifest`` would be exactly the reverse dependency AD-7 forbids.
#: Kept in sync by hand, the same "reused structure, not reused values" shape
#: ``journal/enums.py::HealthErrorCode`` already documents for the identical
#: reason. If ``ToolManifest`` ever adds a fourth severity, this drifts until
#: someone notices -- an accepted, narrow cost against the alternative of a
#: cross-subsystem import.
_ActionSeverity = Literal["read", "write", "consequential"]
#: Same mirroring rule, for ``ToolManifest.effect_class``.
_EffectClass = Literal["creates_persistent_entity", "sends_message", "schedules"]

#: The record_ref target this story's three event types share -- see
#: ``db/migrations/0146_turn_action_records.sql`` for why it is named
#: ``turn_action_records`` rather than the spec's own ``turn_decisions``
#: (that name collides with the pre-existing ADR-7 ``/explain`` ledger table,
#: migration 0071).
_TABLE = "turn_action_records"

_INSERT_TURN_ACTION_RECORD_SQL = (
    "INSERT INTO turn_action_records ("
    "id, trace_id, kind, identifier, outcome, duration_ms, error_code, occurred_at"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _actor_owl_name() -> str:
    """The owl actually running the current turn (AD-2's "the owl as
    actor"). Empty when no owl is in context -- never worth losing the row
    over, mirroring ``providers/base.py::_record_cost``'s same read."""
    return str(TraceContext.get().get("owl_name") or "")


class ModelCalledAttrs(JournalAttrsBase):
    """``model.called`` -- one remote round completed (``_dispatch_post_llm``).

    Deliberately carries only ``provider`` -- not ``model``/``tier``/``purpose``
    -- see the spec's Design Notes for why none of those three reach this
    call site without growing ``_resilient_round``'s signature across every
    concrete provider for a field the AC does not require.
    """

    provider: str = Field(max_length=_MAX_LABEL_LEN)
    error_code: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)


class ToolCalledAttrs(JournalAttrsBase):
    """``tool.called`` -- one REAL dispatch ran (``_guarded_dispatch``).

    Never fires for a pre-execution refusal (denied_this_run / deterministic_dead
    / circuit-open / missing-param) -- those already "record nothing" by the
    P0 honesty-invariant convention ``execute.py`` documents inline.
    """

    tool_name: str = Field(max_length=_MAX_LABEL_LEN)
    #: Closed Literal, not an open bounded string -- AD-4's "closed enums"
    #: requirement, for a value that is ALREADY closed at the point of
    #: capture (ToolManifest.action_severity). See ``_ActionSeverity`` above.
    action_severity: _ActionSeverity
    effect_class: _EffectClass | None = None
    error_code: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)


class DelegationHoppedAttrs(JournalAttrsBase):
    """``delegation.hopped`` -- one Secretary-to-specialist round trip settled
    (``A2ADelegator.delegate``).

    ``status`` carries the FULL closed ``DelegationStatus`` value -- the
    envelope's own ``outcome`` only has room for ok/failed, so the detail a
    reader would want ("timeout" vs "child_error" vs "off_topic") lives here
    instead of being lost to the collapse.
    """

    from_owl: str = Field(max_length=_MAX_LABEL_LEN)
    to_owl: str = Field(max_length=_MAX_LABEL_LEN)
    status: str = Field(max_length=_MAX_LABEL_LEN)


def _narrate_model_called(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- no resolver registered for RecordKind.TURN (out of scope)
    a = cast(ModelCalledAttrs, attrs)
    if a.error_code:
        return f"A model call to {a.provider} failed ({a.error_code})."
    return f"A model call to {a.provider} completed."


def _narrate_tool_called(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001
    a = cast(ToolCalledAttrs, attrs)
    if a.error_code:
        return f"The {a.tool_name} tool failed ({a.error_code})."
    return f"The {a.tool_name} tool ran."


def _narrate_delegation_hopped(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001
    a = cast(DelegationHoppedAttrs, attrs)
    return f"{a.from_owl} delegated to {a.to_owl} ({a.status})."


def _register() -> None:
    registry = get_registry()
    registry.register(EventTypeSpec(
        type="model.called", schema_version=1, attrs_model=ModelCalledAttrs,
        emitting_process="providers.base", record_kind=RecordKind.TURN,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        narrate=_narrate_model_called,
    ))
    registry.register(EventTypeSpec(
        type="tool.called", schema_version=1, attrs_model=ToolCalledAttrs,
        emitting_process="pipeline.steps.execute", record_kind=RecordKind.TURN,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        narrate=_narrate_tool_called,
    ))
    registry.register(EventTypeSpec(
        type="delegation.hopped", schema_version=1, attrs_model=DelegationHoppedAttrs,
        emitting_process="owls.a2a_delegation", record_kind=RecordKind.TURN,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        narrate=_narrate_delegation_hopped,
    ))


_register()


async def _record_turn_event(
    db_pool: DbPool | None,
    *,
    trace_id: str,
    event_type: str,
    target_kind: ActorKind,
    target_id: str,
    outcome: Outcome,
    duration_ms: float,
    error_code: str | None,
    attrs_factory: Callable[[], JournalAttrsBase],
) -> None:
    """Shared body: one ``turn_action_records`` row, then ``journal.record()``
    inside the SAME transaction (AD-24). Never raises (B5, mirroring
    ``providers/base.py::_record_cost``'s own contract): any failure --
    building ``attrs``, the raw insert, or ``journal.record()`` itself -- is
    logged at ERROR with ``exc_info``, degrades journal health, and returns.
    The model/tool/delegation action this describes has ALREADY happened by
    the time this is called; only its journal row can ever be at risk here.

    ``attrs_factory`` is called INSIDE the guarded region (not by the caller)
    so a ``pydantic.ValidationError`` from an over-length label can never
    escape into the turn either -- the same B5 guarantee as every other
    failure mode this function absorbs.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] turn_events._record_turn_event: entry",
        extra={"_fields": {"type": event_type, "trace_id": trace_id, "target_id": target_id}},
    )
    # 2. DECISION -- no DbPool wired (tests / standalone construction) is a
    # silent no-op, mirroring `_record_cost`'s `tracker is None` skip.
    if db_pool is None:
        log.journal.debug(
            "[journal] turn_events._record_turn_event: exit -- no db_pool wired",
            extra={"_fields": {"type": event_type}},
        )
        return
    try:
        # AD-38: the write-budget counter is incremented BEFORE the insert,
        # unconditionally -- the action happened regardless of whether the
        # journal write below succeeds.
        check_and_note(trace_id)
        row_id = new_event_id()
        occurred_at = _now_iso()
        duration_ms_int = int(round(duration_ms))
        # 3. STEP -- same leak-guard scan `journal.record()` already runs on
        # its own copy of `target_id` (recorder.py), so the identical value
        # cannot be redacted in `journal_events` and left raw here.
        redacted_target_id, _ = redact_secret_shapes(target_id)
        attrs = attrs_factory()
        async with db_pool.transaction() as conn:
            try:
                await conn.execute(_INSERT_TURN_ACTION_RECORD_SQL, (
                    row_id, trace_id, event_type, redacted_target_id, outcome.value,
                    duration_ms_int, error_code, occurred_at,
                ))
            except Exception as exc:
                # The raw insert failed BEFORE journal.record() ever ran, so
                # recorder.py's own failure path never fires for this one --
                # degrade health here, the only place that will.
                remedy = (
                    remedy_for(exc)
                    or f"{_TABLE} insert failed for {event_type!r}: {exc}"
                )
                note_failure(remedy)
                raise
            await journal_record(conn, JournalEvent(
                type=event_type, schema_version=1, occurred_at=occurred_at,
                actor_kind=ActorKind.OWL, actor_id=_actor_owl_name(),
                target_kind=target_kind, target_id=target_id, outcome=outcome,
                record_ref=RecordRef(
                    kind="sqlite", locator={"table": _TABLE, "id": row_id},
                ),
                attrs=attrs, trace_id=trace_id, duration_ms=duration_ms_int,
            ))
    except Exception as exc:
        # journal.record() already degraded health itself on its own
        # failure path (recorder.py) -- this catch exists so NEITHER that
        # nor a raw-insert/attrs-validation failure above can ever
        # propagate into the model/tool/delegation call site that awaited
        # this helper.
        log.journal.error(
            "[journal] turn_events._record_turn_event: FAILED -- the action "
            "itself already happened; only its journal row is missing",
            exc_info=exc,
            extra={"_fields": {
                "type": event_type, "trace_id": trace_id, "target_id": target_id,
            }},
        )
        return
    # 4. EXIT
    log.journal.debug(
        "[journal] turn_events._record_turn_event: exit -- recorded",
        extra={"_fields": {"type": event_type, "trace_id": trace_id, "target_id": target_id}},
    )


async def record_model_call(
    db_pool: DbPool | None,
    *,
    provider: str,
    duration_ms: float,
    ok: bool,
    error_code: str | None,
) -> None:
    """Record one ``model.called`` event. Called from
    ``ModelProvider._dispatch_post_llm`` after every remote round.

    Skipped entirely (no row, no journal event) when
    ``TraceContext.get()["trace_id"]`` is falsy -- a health-probe
    ``complete()`` call or other backgroundless call must never be journaled
    with an empty trace.
    """
    trace_id = TraceContext.get().get("trace_id")
    if not trace_id:
        return
    await _record_turn_event(
        db_pool, trace_id=str(trace_id), event_type="model.called",
        target_kind=ActorKind.OWNER, target_id=provider,
        outcome=Outcome.OK if ok else Outcome.FAILED,
        duration_ms=duration_ms, error_code=error_code,
        attrs_factory=lambda: ModelCalledAttrs(provider=provider, error_code=error_code),
    )


async def record_tool_call(
    db_pool: DbPool | None,
    *,
    tool_name: str,
    action_severity: _ActionSeverity,
    effect_class: _EffectClass | None,
    duration_ms: float,
    outcome: Outcome,
    error_code: str | None,
) -> None:
    """Record one ``tool.called`` event. Called from ``_guarded_dispatch``
    on both its timeout and normal-completion branches -- never on a
    pre-execution refusal.

    Skipped entirely when no ``trace_id`` is in context, same as
    :func:`record_model_call`.
    """
    trace_id = TraceContext.get().get("trace_id")
    if not trace_id:
        return
    await _record_turn_event(
        db_pool, trace_id=str(trace_id), event_type="tool.called",
        target_kind=ActorKind.OWNER, target_id=tool_name,
        outcome=outcome, duration_ms=duration_ms, error_code=error_code,
        attrs_factory=lambda: ToolCalledAttrs(
            tool_name=tool_name, action_severity=action_severity,
            effect_class=effect_class, error_code=error_code,
        ),
    )


#: `DelegationStatus` values the Boundaries contract collapses to `Outcome.OK`
#: -- everything else (timeout, child_error, and every other closed status:
#: truncated, refused, cycle, target_not_found, off_topic) is `Outcome.FAILED`.
#: Only "ok"/"empty" and "timeout"/"child_error" are named explicitly by the
#: spec; the remaining statuses are never NOT an ok-shaped hop, so they fall
#: on the FAILED side of the same two-way split rather than inventing a third
#: envelope outcome the conventions do not have room for.
_DELEGATION_OK_STATUSES = frozenset({"ok", "empty"})


async def record_delegation_hop(
    db_pool: DbPool | None,
    *,
    from_owl: str,
    to_owl: str,
    status: str,
    duration_ms: float,
) -> None:
    """Record one ``delegation.hopped`` event. Called from
    ``A2ADelegator.delegate()`` at each of its three return points (timeout,
    child_error, fall-through).

    Skipped entirely when no ``trace_id`` is in context, same as
    :func:`record_model_call`.
    """
    trace_id = TraceContext.get().get("trace_id")
    if not trace_id:
        return
    outcome = Outcome.OK if status in _DELEGATION_OK_STATUSES else Outcome.FAILED
    await _record_turn_event(
        db_pool, trace_id=str(trace_id), event_type="delegation.hopped",
        # AD-3's one closed actor/target list: a delegation hop's target
        # genuinely IS another owl (Design Notes) -- the one place this
        # story's three types uses ActorKind.OWL as target_kind rather than
        # the OWNER fallback model.called/tool.called use.
        target_kind=ActorKind.OWL, target_id=to_owl,
        outcome=outcome, duration_ms=duration_ms, error_code=None,
        attrs_factory=lambda: DelegationHoppedAttrs(
            from_owl=from_owl, to_owl=to_owl, status=status,
        ),
    )
