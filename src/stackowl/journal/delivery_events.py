"""``DeliveryAttemptedAttrs``/``ProviderReroutedAttrs``, their registration,
and the TWO recording helpers ``notifications.deliverer`` needs (Story 2.9).

SHAPED LIKE ``consent_events.py``/``turn_events.py``, not like the
registration-only modules (``task_events.py`` etc.): none of this story's
THREE call sites (``ProactiveDeliverer.deliver``'s two return points,
``transport()``'s digest-flush path, and ``_maybe_reroute``'s recovered
branch) has an existing async DB mutation of its own to piggyback a journal
write onto, so this module owns two ``record_*`` helpers, each opening its
OWN transaction, inserting a ``delivery_records`` row, then calling
``journal.record()`` inside the SAME commit (AD-24) -- mirroring
``turn_events.py::_record_turn_event``'s shape exactly, including a shared
private body both public helpers call through.

``delivery.attempted.channel`` is ALWAYS the originally-addressed channel,
never reassigned after a reroute -- ``deliver()``'s local ``channel``
variable is never touched by ``_maybe_reroute`` (Design Notes, spec). The
fallback channel a reroute actually used lives only in
``provider.rerouted.to_channel``; a reader correlates the two rows via
``notification_id``/``job_id`` when both are present.

Importing this module registers both types as a side effect;
``journal/__init__.py`` imports it for exactly that reason.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from stackowl.health.status import remedy_for
from stackowl.infra.observability import log, redact_secret_shapes
from stackowl.journal.enums import ActorKind, AttentionClass, Outcome, RecordKind
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

#: Mirrors ``notifications/router.py::DeliveryStatus``'s own closed Literal --
#: COPIED, not imported: ``journal/`` imports nothing from any subsystem
#: (AD-7), the same "mirror the shape, not the import" rule
#: ``turn_events.py::_ActionSeverity`` already established for exactly this
#: situation (a value already closed at its source needing to appear as a
#: closed Literal inside ``journal/`` too).
_DeliveryStatus = Literal["delivered", "batched", "suppressed", "failed"]

_TABLE = "delivery_records"

_INSERT_DELIVERY_RECORD_SQL = (
    "INSERT INTO delivery_records ("
    "id, kind, channel, notification_id, outcome, occurred_at"
    ") VALUES (?, ?, ?, ?, ?, ?)"
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


#: The full closed ``DeliveryStatus`` collapses to the envelope's coarser
#: ``Outcome`` -- documented mapping (spec Boundaries): delivered→OK
#: (a real send completed), failed→FAILED (transport never landed), batched→
#: PENDING (queued for the digest flush, not yet decided), suppressed→PARKED
#: (deliberately held back by focus/quiet-hours, not an error).
_DELIVERY_OUTCOME_MAP: dict[_DeliveryStatus, Outcome] = {
    "delivered": Outcome.OK,
    "failed": Outcome.FAILED,
    "batched": Outcome.PENDING,
    "suppressed": Outcome.PARKED,
}


class DeliveryAttemptedAttrs(JournalAttrsBase):
    """``delivery.attempted`` -- one ``deliver()``/``transport()`` call
    resolved.

    ``channel`` is always the ORIGINALLY-addressed channel (never the
    post-reroute one -- see module docstring). ``delivery_status`` carries
    the FULL closed status (the envelope's own ``outcome`` only has room for
    the 4-way collapse above); a reader who wants "batched" vs "suppressed"
    needs this field, not ``outcome``.
    """

    channel: str = Field(max_length=_MAX_LABEL_LEN)
    delivery_status: _DeliveryStatus
    category: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)
    job_id: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)
    notification_id: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)


class ProviderReroutedAttrs(JournalAttrsBase):
    """``provider.rerouted`` -- ``_maybe_reroute``'s
    ``RecoveryActuator.recover(...)`` actually switched channels
    (``outcome.recovered=True``). Never fires on a reroute that stays
    failed -- no state changed, so there is nothing to record beyond the
    outer ``delivery.attempted`` row's own ``outcome=failed``.
    """

    from_channel: str = Field(max_length=_MAX_LABEL_LEN)
    to_channel: str = Field(max_length=_MAX_LABEL_LEN)
    notification_id: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)


def _narrate_delivery_attempted(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- no resolver registered for RecordKind.DELIVERY (out of scope)
    a = cast(DeliveryAttemptedAttrs, attrs)
    return f"A delivery to {a.channel} {a.delivery_status}."


def _narrate_provider_rerouted(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001
    a = cast(ProviderReroutedAttrs, attrs)
    return f"Delivery rerouted from {a.from_channel} to {a.to_channel}."


def _register() -> None:
    registry = get_registry()
    registry.register(EventTypeSpec(
        type="delivery.attempted", schema_version=1, attrs_model=DeliveryAttemptedAttrs,
        emitting_process="notifications.deliverer", record_kind=RecordKind.DELIVERY,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_delivery_attempted,
    ))
    registry.register(EventTypeSpec(
        type="provider.rerouted", schema_version=1, attrs_model=ProviderReroutedAttrs,
        emitting_process="notifications.deliverer", record_kind=RecordKind.PROVIDER,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_provider_rerouted,
    ))


_register()


async def _record_delivery_event(
    db_pool: DbPool | None,
    *,
    event_type: str,
    channel: str,
    notification_id: str | None,
    outcome: Outcome,
    attrs_factory: Callable[[], JournalAttrsBase],
) -> None:
    """Shared body: one ``delivery_records`` row, then ``journal.record()``
    inside the SAME transaction (AD-24). Never raises (B5, mirroring
    ``turn_events.py::_record_turn_event``'s own contract exactly, ``attrs``
    built INSIDE the guarded region included): any failure is logged at
    ERROR with ``exc_info``, degrades journal health, and returns. The
    delivery/reroute this describes has ALREADY happened by the time this is
    called; only its journal row can ever be at risk here.
    """
    # 1. ENTRY
    log.notifications.debug(
        "[journal] delivery_events._record_delivery_event: entry",
        extra={"_fields": {"type": event_type, "channel": channel}},
    )
    # 2. DECISION -- no DbPool wired (tests / standalone construction) is a
    # silent no-op, mirroring `_record_turn_event`'s `db_pool is None` skip.
    if db_pool is None:
        log.notifications.debug(
            "[journal] delivery_events._record_delivery_event: exit -- no db_pool wired",
            extra={"_fields": {"type": event_type}},
        )
        return
    try:
        row_id = new_event_id()
        occurred_at = _now_iso()
        # 3. STEP -- `delivery_records` is its OWN table, never scanned by
        # `journal.record()` (recorder.py only scans the JournalEvent it is
        # given -- `attrs`/actor/target ids/record_ref -- never a sibling raw
        # table's own columns). Same rule `turn_events.py`'s own redaction of
        # `target_id` documents: every raw INSERT value here passes through
        # the leak guard first (NFR33), so the same string cannot be
        # redacted in `journal_events` and left raw here.
        redacted_channel, _ = redact_secret_shapes(channel)
        redacted_notification_id, _ = (
            redact_secret_shapes(notification_id) if notification_id is not None
            else (None, False)
        )
        attrs = attrs_factory()
        async with db_pool.transaction() as conn:
            try:
                await conn.execute(_INSERT_DELIVERY_RECORD_SQL, (
                    row_id, event_type, redacted_channel, redacted_notification_id,
                    outcome.value, occurred_at,
                ))
            except Exception as exc:
                # The raw insert failed BEFORE journal.record() ever ran, so
                # recorder.py's own failure path never fires for this one --
                # degrade health here, the only place that will.
                remedy = remedy_for(exc) or f"{_TABLE} insert failed for {event_type!r}: {exc}"
                note_failure(remedy)
                raise
            await journal_record(conn, JournalEvent(
                type=event_type, schema_version=1, occurred_at=occurred_at,
                actor_kind=ActorKind.OWNER, actor_id=channel,
                target_kind=ActorKind.OWNER, target_id=channel, outcome=outcome,
                record_ref=RecordRef(
                    kind="sqlite", locator={"table": _TABLE, "id": row_id},
                ),
                attrs=attrs,
            ))
    except Exception as exc:
        # journal.record() already degraded health itself on its own
        # failure path (recorder.py) -- this catch exists so NEITHER that
        # nor a raw-insert/attrs-validation failure above can ever
        # propagate into the delivery/reroute call site that awaited this
        # helper.
        remedy = remedy_for(exc) or f"delivery_events failed for {event_type!r}: {exc}"
        note_failure(remedy)
        log.notifications.error(
            "[journal] delivery_events._record_delivery_event: FAILED -- the "
            "delivery itself already happened; only its journal row is missing",
            exc_info=exc,
            extra={"_fields": {"type": event_type, "channel": channel}},
        )
        return
    # 4. EXIT
    log.notifications.debug(
        "[journal] delivery_events._record_delivery_event: exit -- recorded",
        extra={"_fields": {"type": event_type, "channel": channel}},
    )


async def record_delivery_attempted(
    db_pool: DbPool | None,
    *,
    channel: str,
    delivery_status: _DeliveryStatus,
    category: str | None,
    job_id: str | None,
    notification_id: str | None,
) -> None:
    """Record one ``delivery.attempted`` event.

    Called from THREE ``ProactiveDeliverer`` call sites: ``deliver()``'s
    router-decided early return (``batched``/``suppressed``), ``deliver()``'s
    final return (after ``_maybe_reroute``), and ``transport()``'s
    digest-flush path (``category=None``, ``job_id=None`` -- no
    ``Notification`` in scope there).
    """
    await _record_delivery_event(
        db_pool, event_type="delivery.attempted", channel=channel,
        notification_id=notification_id,
        outcome=_DELIVERY_OUTCOME_MAP.get(delivery_status, Outcome.FAILED),
        attrs_factory=lambda: DeliveryAttemptedAttrs(
            channel=channel, delivery_status=delivery_status, category=category,
            job_id=job_id, notification_id=notification_id,
        ),
    )


async def record_provider_rerouted(
    db_pool: DbPool | None,
    *,
    from_channel: str,
    to_channel: str,
    notification_id: str | None = None,
) -> None:
    """Record one ``provider.rerouted`` event.

    Called from ``_maybe_reroute`` ONLY inside its ``if outcome.recovered:``
    branch -- a genuine channel switch happened, ``outcome=Outcome.OK``
    always (Boundaries). ``notification_id`` lets a reader correlate this row
    back to the ``delivery.attempted`` row it concerns (module docstring).
    """
    await _record_delivery_event(
        db_pool, event_type="provider.rerouted", channel=to_channel,
        notification_id=notification_id, outcome=Outcome.OK,
        attrs_factory=lambda: ProviderReroutedAttrs(
            from_channel=from_channel, to_channel=to_channel,
            notification_id=notification_id,
        ),
    )


class DeliveryRecordView(BaseModel):
    """Typed view of one ``delivery_records`` row -- the registered readers'
    shared return shape for BOTH ``RecordKind.DELIVERY``/``sqlite`` AND
    ``RecordKind.PROVIDER``/``sqlite`` (AD-4): ``delivery.attempted`` and
    ``provider.rerouted`` share this one table (module docstring), so one
    view model backs both readers -- two registrations (different
    ``RecordKind``), the same shape and the same underlying row."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    channel: str
    notification_id: str | None = None
    outcome: str
    occurred_at: str


async def _read_delivery_record(
    record_kind: RecordKind, db_pool: DbPool | None, locator: dict[str, str], *, owner_id: str,
) -> DeliveryRecordView | ExpiredRecord:
    """Shared body for both delivery-table readers -- differs only in which
    ``RecordKind`` is being asked for and refused/excused as."""
    refuse_unless_owner(record_kind, owner_id)
    row = await read_sqlite_record(
        db_pool, table=_TABLE, id_column="id",
        id_value=locator.get("id", ""), view_model=DeliveryRecordView,
    )
    if row is None:
        return ExpiredRecord(
            record_kind=record_kind, locator=locator,
            reason="the delivery_records row this event referenced is gone",
        )
    return cast(DeliveryRecordView, row)


async def read_delivery_attempted_record(
    db_pool: DbPool | None, locator: dict[str, str], *, owner_id: str,
) -> DeliveryRecordView | ExpiredRecord:
    """The registered reader for ``RecordKind.DELIVERY``/``sqlite`` (AD-4):
    opens the ``delivery_records`` row a ``delivery.attempted`` event's
    ``record_ref`` points at."""
    return await _read_delivery_record(RecordKind.DELIVERY, db_pool, locator, owner_id=owner_id)


async def read_provider_rerouted_record(
    db_pool: DbPool | None, locator: dict[str, str], *, owner_id: str,
) -> DeliveryRecordView | ExpiredRecord:
    """The registered reader for ``RecordKind.PROVIDER``/``sqlite`` (AD-4):
    opens the ``delivery_records`` row a ``provider.rerouted`` event's
    ``record_ref`` points at."""
    return await _read_delivery_record(RecordKind.PROVIDER, db_pool, locator, owner_id=owner_id)


get_record_reader_registry().register(
    RecordKind.DELIVERY, "sqlite", read_delivery_attempted_record,
)
get_record_reader_registry().register(
    RecordKind.PROVIDER, "sqlite", read_provider_rerouted_record,
)
