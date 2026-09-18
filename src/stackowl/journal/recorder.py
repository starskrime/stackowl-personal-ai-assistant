"""``journal.record`` -- the ONE append-only insert API (AD-2, AD-3, AD-24).

Runs inside the CALLER's own open transaction: takes the live
``aiosqlite.Connection`` straight from ``DbPool.transaction()`` (or any other
already-open transaction on the same connection) and never opens or commits
one of its own. A task's state UPDATE and its journal row therefore commit or
roll back together -- the transactional-outbox shape this story exists to
prove (AC's forced-rollback / committed-write tests).
"""

from __future__ import annotations

import json

import aiosqlite

from stackowl.exceptions import JournalAttentionSetByEmitterError, JournalInvalidAttrsError
from stackowl.health.status import remedy_for
from stackowl.infra.observability import log, redact_secret_shapes
from stackowl.journal.health import note_failure, note_success
from stackowl.journal.ids import new_event_id
from stackowl.journal.leak_guard import scan_attrs
from stackowl.journal.models import JournalEvent, RecordRef
from stackowl.journal.registry import get_registry

_INSERT_SQL = (
    "INSERT INTO journal_events ("
    "event_id, type, schema_version, occurred_at, actor_kind, actor_id, "
    "device_id, target_kind, target_id, outcome, attention, intensity, "
    "record_ref, attrs, trace_id, duration_ms"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _scan_optional(value: str | None) -> tuple[str | None, bool]:
    """``redact_secret_shapes`` lifted over ``str | None`` -- most envelope
    fields are optional, and ``None`` is never a secret."""
    if value is None:
        return None, False
    return redact_secret_shapes(value)


def _scan_record_ref(record_ref: RecordRef | None) -> tuple[str | None, bool]:
    """Scan every string in ``record_ref.locator`` (e.g. a table/row id) too --
    AD-4 says the leak guard scans "every string value" in an event, not just
    ``attrs``, and a locator is exactly the kind of value that could carry an
    id-shaped secret if a future record kind's locator ever did."""
    if record_ref is None:
        return None, False
    changed_any = False
    scanned_locator: dict[str, str] = {}
    for key, value in record_ref.locator.items():
        new_value, changed = redact_secret_shapes(value)
        scanned_locator[key] = new_value
        changed_any = changed_any or changed
    scanned_ref = RecordRef(kind=record_ref.kind, locator=scanned_locator)
    return scanned_ref.model_dump_json(), changed_any


async def record(conn: aiosqlite.Connection, event: JournalEvent) -> str:
    """Insert one journal row on ``conn``, inside the caller's own transaction.

    Never commits -- whatever opened ``conn`` (``DbPool.transaction()`` in
    every wired caller today) owns that. Raises loudly, before any SQL runs,
    on an unregistered ``event.type``, an ``attrs`` instance that fails its
    registered model, or an emitter that pre-set ``attention``/``intensity``
    (:class:`~stackowl.exceptions.JournalAttentionSetByEmitterError` -- AD-5:
    emitters never classify). Computes ``attention``/``intensity`` itself from
    the already-fetched :class:`~stackowl.journal.registry.EventTypeSpec` --
    the same classification :func:`~stackowl.journal.attention.classify` would
    return, reused here rather than looked up a second time. Redacts
    secret-shaped strings in ``attrs`` before the row is built. On ANY
    failure: 4-point-logs it, degrades
    :class:`~stackowl.journal.health.JournalHealthContributor` with a remedy,
    and always re-raises -- a caller's ``transaction()`` block then rolls back
    the state change too, so the two can never diverge.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] recorder.record: entry",
        extra={"_fields": {"type": event.type, "target_id": event.target_id}},
    )
    event_id = new_event_id()
    try:
        # 2. DECISION -- the type must be declared, and attrs must match it.
        spec = get_registry().get(event.type)
        if not isinstance(event.attrs, spec.attrs_model):
            raise JournalInvalidAttrsError(
                event.type, type(event.attrs).__name__, spec.attrs_model.__name__,
            )

        # AD-5: emitters never classify -- attention/intensity are computed by
        # THIS function from the registry (`spec`, already fetched above --
        # the same values `attention.classify()` would return, reused rather
        # than looked up a second time), not supplied by the caller. An
        # emitter that pre-set either field is making its own ambient/needs-you
        # judgment, which is exactly the per-surface disagreement this policy
        # exists to prevent. Refused before any SQL runs.
        if event.attention is not None or event.intensity is not None:
            raise JournalAttentionSetByEmitterError(event.type)
        attention_class, intensity = spec.attention_class, spec.intensity

        # AD-4: the leak guard scans EVERY string value in the event, not just
        # `attrs` -- `actor_id`/`target_id`/`device_id`/`trace_id` and
        # `record_ref.locator`'s values get the same treatment.
        redacted_attrs, attrs_redacted = scan_attrs(event.attrs)
        actor_id, actor_redacted = redact_secret_shapes(event.actor_id)
        target_id, target_redacted = redact_secret_shapes(event.target_id)
        device_id, device_redacted = _scan_optional(event.device_id)
        trace_id, trace_redacted = _scan_optional(event.trace_id)
        record_ref_json, record_ref_redacted = _scan_record_ref(event.record_ref)

        was_redacted = (
            attrs_redacted or actor_redacted or target_redacted
            or device_redacted or trace_redacted or record_ref_redacted
        )
        if was_redacted:
            redacted_attrs["_redacted"] = True
            log.journal.warning(
                "[journal] recorder.record: secret-shaped string redacted "
                "before storage",
                extra={"_fields": {"type": event.type, "target_id": event.target_id}},
            )

        # 3. STEP -- the one insert, on the CALLER's own connection/transaction.
        await conn.execute(
            _INSERT_SQL,
            (
                event_id, event.type, event.schema_version, event.occurred_at,
                event.actor_kind.value, actor_id, device_id,
                event.target_kind.value, target_id, event.outcome.value,
                attention_class.value, intensity.value if intensity is not None else None,
                record_ref_json,
                json.dumps(redacted_attrs), trace_id, event.duration_ms,
            ),
        )
    except Exception as exc:
        remedy = remedy_for(exc) or f"journal.record failed for type={event.type!r}: {exc}"
        log.journal.error(
            "[journal] recorder.record: FAILED -- this action's state change "
            "may still commit with no journal row to prove it happened",
            exc_info=exc,
            extra={"_fields": {"type": event.type, "target_id": event.target_id}},
        )
        note_failure(remedy)
        raise
    # 4. EXIT
    note_success()
    log.journal.info(
        "[journal] recorder.record: exit",
        extra={"_fields": {
            "type": event.type, "event_id": event_id, "target_id": event.target_id,
        }},
    )
    return event_id
