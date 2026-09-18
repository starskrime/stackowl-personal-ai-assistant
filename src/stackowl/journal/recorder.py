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

from stackowl.exceptions import (
    JournalAttentionSetByEmitterError,
    JournalInvalidAttrsError,
    JournalWritesPausedError,
)
from stackowl.health.status import remedy_for
from stackowl.infra.observability import log, redact_secret_shapes
from stackowl.journal import needs_you
from stackowl.journal.enums import AttentionClass, Outcome
from stackowl.journal.health import note_failure, note_success
from stackowl.journal.ids import new_event_id
from stackowl.journal.leak_guard import scan_attrs
from stackowl.journal.models import JournalEvent, RecordRef
from stackowl.journal.registry import get_registry
from stackowl.journal.write_gate import writes_paused

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


async def record(
    conn: aiosqlite.Connection, event: JournalEvent, *, bypass_write_gate: bool = False,
) -> str:
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

    ``bypass_write_gate`` (Story 3.1, AD-28) -- keyword-only, defaults
    ``False``. When ``True``, skips ONLY the ``writes_paused()`` refusal
    below; every other check (registry lookup, attrs validation, attention/
    intensity computation, leak-guard redaction) still runs unchanged. The
    write-gate (``write_gate.py``'s own docstring) exists because "the
    gateway cannot prove its schema/registry match the core actually writing
    journal rows right now" -- that rationale does NOT apply to a write that
    is gateway-self-originated, built from the gateway's own already-loaded
    registry, describing the gateway's own decision, with no core involvement
    in producing it. Used by exactly ONE call site today:
    ``startup.orchestrator._supervise_core``'s ``link.hello_mismatch_standdown``
    write -- the write-gate is UNCONDITIONALLY paused at that exact point
    (every Hello mismatch pauses it, ``runtime/gateway_link.py``'s
    ``GatewayLink._route``, resumed only by a LATER compatible Hello, never
    before 3 consecutive mismatches trigger that stand-down), so a bare call
    there would always raise :class:`~stackowl.exceptions.JournalWritesPausedError`.
    Do NOT reach for ``write_gate.resume_writes()`` instead of this parameter
    anywhere in that flow -- that would reopen the gate for every OTHER
    concurrent writer in the gateway process for the duration of the
    transaction, reintroducing the exact schema-safety hole Spec 2.3 built
    the gate to close. This scoped parameter is the only sanctioned mechanism.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] recorder.record: entry",
        extra={"_fields": {
            "type": event.type, "target_id": event.target_id,
            "bypass_write_gate": bypass_write_gate,
        }},
    )
    event_id = new_event_id()
    try:
        # 2. DECISION -- Spec 2.3: refused BEFORE the registry lookup, so a
        # paused write-gate (a gateway/core Hello mismatch or a lost link)
        # never even resolves the type. Skipped only when the caller passed
        # `bypass_write_gate=True` (see docstring above for the ONE sanctioned
        # call site and why).
        if writes_paused() and not bypass_write_gate:
            raise JournalWritesPausedError(event.type)
        # the type must be declared, and attrs must match it.
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
        insert_cursor = await conn.execute(
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
        journal_cursor = insert_cursor.lastrowid
        if journal_cursor is None:
            # journal_events.cursor is INTEGER PRIMARY KEY AUTOINCREMENT -- a
            # successful INSERT always sets lastrowid. Explicit, loud raise
            # rather than `assert` (stripped under `python -O`; no existing
            # precedent for bare `assert`-as-narrowing anywhere in
            # `journal/*.py`) -- unreachable in practice, but must fail LOUD
            # if it somehow ever is.
            raise RuntimeError(
                f"journal.record: INSERT for type={event.type!r} returned no "
                "lastrowid -- journal_events.cursor is AUTOINCREMENT and this "
                "should be impossible"
            )

        # Story 3.1 (AD-28) -- open a durable Needs-you item in the SAME
        # transaction as the event that triggers it (AD-24), driven entirely
        # by registry metadata (spec Design Notes: "one generic hook ... a
        # later story adding its sixth NEEDS_YOU type needs zero new
        # plumbing"). needs_you.opened/resolved are themselves AMBIENT
        # (asserted by registration -- see needs_you.py), so this recursive
        # record() call never recurses a second level.
        if attention_class is AttentionClass.NEEDS_YOU:
            # Registry __post_init__ refuses a NEEDS_YOU type with no
            # needs_you_kind at REGISTRATION time -- both are guaranteed
            # non-None here. Explicit raise, not `assert` -- see above.
            if spec.needs_you_kind is None:
                raise RuntimeError(
                    f"journal.record: type={event.type!r} is attention_class="
                    "NEEDS_YOU with no needs_you_kind -- EventTypeSpec."
                    "__post_init__ should have refused this at registration"
                )
            if intensity is None:
                raise RuntimeError(
                    f"journal.record: type={event.type!r} is attention_class="
                    "NEEDS_YOU with no intensity -- EventTypeSpec."
                    "__post_init__ should have refused this at registration"
                )
            open_dedupe_key = needs_you._dedupe_key(
                spec.needs_you_kind.value, event.target_kind.value, target_id,
            )
            new_item_id = await needs_you.open_item(
                conn, kind=spec.needs_you_kind, intensity=intensity,
                dedupe_key=open_dedupe_key, record_ref_json=record_ref_json,
                opened_cursor=int(journal_cursor),
            )
            if new_item_id is not None:
                # bypass_write_gate=bypass_write_gate -- MUST propagate to
                # this nested call. A caller that passed True to open the
                # OUTER event (e.g. `_supervise_core`'s stand-down write,
                # under a genuinely paused gate) needs the SAME bypass here,
                # or this recursive call re-checks `writes_paused()` fresh,
                # raises, and rolls back the whole transaction -- including
                # the outer row the caller's bypass was meant to land.
                await record(
                    conn,
                    JournalEvent(
                        type="needs_you.opened", schema_version=1,
                        actor_kind=event.actor_kind, actor_id=event.actor_id,
                        target_kind=event.target_kind, target_id=event.target_id,
                        outcome=Outcome.OK,
                        record_ref=RecordRef(
                            kind="sqlite", locator={"table": "needs_you", "id": new_item_id},
                        ),
                        attrs=needs_you.NeedsYouOpenedAttrs(
                            item_id=new_item_id, kind=spec.needs_you_kind, intensity=intensity,
                        ),
                        trace_id=event.trace_id,
                    ),
                    bypass_write_gate=bypass_write_gate,
                )

        # Story 3.1 (AD-28) -- close every open item a registered `resolves`
        # name points at, through the ONE internal resolver. `record()`
        # itself looks each name up LIVE (not at registration time -- import
        # order across `*_events.py` modules is not guaranteed) and raises
        # the existing JournalEventTypeUnregisteredError on a bad name, the
        # same as any other live registry lookup.
        for opening_type_name in spec.resolves:
            opening_spec = get_registry().get(opening_type_name)
            if opening_spec.needs_you_kind is None:
                # Defensive only: `resolves` should only ever name a type
                # that itself opens Needs-you items. A registered-but-wrong
                # name is loud (not a silent no-op) so it is never mistaken
                # for "nothing was open".
                log.journal.warning(
                    "[journal] recorder.record: resolves names a type with "
                    "no needs_you_kind -- cannot compute its dedupe key, "
                    "skipping",
                    extra={"_fields": {
                        "type": event.type, "opening_type": opening_type_name,
                    }},
                )
                continue
            resolve_dedupe_key = needs_you._dedupe_key(
                opening_spec.needs_you_kind.value, event.target_kind.value, target_id,
            )
            # Truncated ONCE, here -- reused for both the DB write and the
            # attrs construction below, so the two can never diverge.
            # `NeedsYouResolvedAttrs.resolved_by` carries the same
            # `Field(max_length=_MAX_LABEL_LEN)` bound `_resolve_open_item`'s
            # own DB-row write truncates to; passing the UNTRUNCATED value to
            # the attrs model would raise `ValidationError` (rolling back the
            # whole transaction) for any registered type name long enough to
            # push `"system:" + type` past the bound, instead of truncating
            # gracefully like the DB write does.
            resolved_by = f"system:{event.type}"[:needs_you._MAX_LABEL_LEN]
            resolved_item_id = await needs_you._resolve_open_item(
                conn, dedupe_key=resolve_dedupe_key,
                resolved_cursor=int(journal_cursor), resolved_by=resolved_by,
            )
            if resolved_item_id is not None:
                # bypass_write_gate propagated -- same reasoning as the
                # needs_you.opened nested call above.
                await record(
                    conn,
                    JournalEvent(
                        type="needs_you.resolved", schema_version=1,
                        actor_kind=event.actor_kind, actor_id=event.actor_id,
                        target_kind=event.target_kind, target_id=event.target_id,
                        outcome=Outcome.OK,
                        record_ref=RecordRef(
                            kind="sqlite",
                            locator={"table": "needs_you", "id": resolved_item_id},
                        ),
                        attrs=needs_you.NeedsYouResolvedAttrs(
                            item_id=resolved_item_id, kind=opening_spec.needs_you_kind,
                            resolved_by=resolved_by,
                        ),
                        trace_id=event.trace_id,
                    ),
                    bypass_write_gate=bypass_write_gate,
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
