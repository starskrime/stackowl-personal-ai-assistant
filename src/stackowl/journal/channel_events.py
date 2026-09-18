"""``ChannelMessageReceivedAttrs``, its registration, and the ONE recording
helper the gateway's 5 per-channel receive loops need (Story 2.9).

SHAPED LIKE ``delivery_events.py``/``consent_events.py``: the gateway-role
``_message_loop``/``_telegram_loop``/``_slack_loop``/``_discord_loop``/
``_whatsapp_loop`` bodies in ``startup/orchestrator.py`` have no existing
async DB mutation of their own to piggyback a journal write onto, so
:func:`record_channel_message_received` opens its OWN transaction (AD-9: the
GATEWAY's own local ``DbPool``, never core's), inserts a
``channel_ingress_records`` row, then calls ``journal.record()`` inside the
SAME commit (AD-24).

Fires once per ``IngressMessage`` a loop's own ``adapter.receive()`` actually
produced -- never for a ``receive()`` that raised (that path already retries
without producing a message).

Importing this module registers the type as a side effect;
``journal/__init__.py`` imports it for exactly that reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from pydantic import Field

from stackowl.health.status import remedy_for
from stackowl.infra.observability import log, redact_secret_shapes
from stackowl.journal.enums import ActorKind, AttentionClass, Outcome, RecordKind
from stackowl.journal.health import note_failure
from stackowl.journal.ids import new_event_id
from stackowl.journal.models import JournalAttrsBase, JournalEvent, RecordRef
from stackowl.journal.recorder import record as journal_record
from stackowl.journal.registry import EventTypeSpec, get_registry

if TYPE_CHECKING:  # pragma: no cover -- typing-only
    from stackowl.db.pool import DbPool

#: AD-4, verbatim: "attrs hold ids, numbers, closed enums and bounded labels
#: of at most 64 characters" -- the one bound every other journal attrs
#: module's own constant enforces.
_MAX_LABEL_LEN = 64

_TABLE = "channel_ingress_records"

_INSERT_CHANNEL_INGRESS_RECORD_SQL = (
    "INSERT INTO channel_ingress_records ("
    "id, channel, session_key, occurred_at"
    ") VALUES (?, ?, ?, ?)"
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ChannelMessageReceivedAttrs(JournalAttrsBase):
    """``channel.message_received`` -- one real ``IngressMessage`` a
    gateway-role receive loop's own ``adapter.receive()`` produced."""

    channel: str = Field(max_length=_MAX_LABEL_LEN)
    session_key: str = Field(max_length=_MAX_LABEL_LEN)


def _narrate_channel_message_received(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- no resolver registered for RecordKind.CHANNEL (out of scope)
    a = cast(ChannelMessageReceivedAttrs, attrs)
    return f"A message arrived on {a.channel}."


def _register() -> None:
    get_registry().register(EventTypeSpec(
        type="channel.message_received", schema_version=1,
        attrs_model=ChannelMessageReceivedAttrs,
        emitting_process="startup.orchestrator", record_kind=RecordKind.CHANNEL,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        narrate=_narrate_channel_message_received,
    ))


_register()


async def record_channel_message_received(
    db_pool: DbPool | None,
    *,
    channel: str,
    session_key: str,
    trace_id: str,
) -> None:
    """Record one ``channel.message_received`` event, in its OWN transaction
    (AD-24).

    Called from each of the 5 gateway-role receive loops right after
    ``msg = await adapter.receive()`` succeeds, using the shared gateway
    ``db_pool`` local from ``_phase_gateway``'s own scope (AD-9: the gateway
    records its own action sites through its OWN DbPool, never core's).
    Never raises (B5): the inbound message has ALREADY arrived by the time
    this runs; only its journal row can ever be at risk here.

    A no-op when ``db_pool`` is ``None`` (e.g. a test construction), mirroring
    every other journal recording helper's own guard.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] channel_events.record_channel_message_received: entry",
        extra={"_fields": {"channel": channel, "trace_id": trace_id}},
    )
    # 2. DECISION -- no DbPool wired is a silent no-op.
    if db_pool is None:
        log.journal.debug(
            "[journal] channel_events.record_channel_message_received: exit "
            "-- no db_pool wired",
            extra={"_fields": {"channel": channel}},
        )
        return
    try:
        row_id = new_event_id()
        occurred_at = _now_iso()
        # 3. STEP -- `channel_ingress_records` is its OWN table, never scanned
        # by `journal.record()` (recorder.py only scans the JournalEvent it
        # is given). Same rule `turn_events.py`'s/`delivery_events.py`'s own
        # redaction documents: every raw INSERT value here passes through
        # the leak guard first (NFR33).
        redacted_channel, _ = redact_secret_shapes(channel)
        redacted_session_key, _ = redact_secret_shapes(session_key)
        async with db_pool.transaction() as conn:
            try:
                await conn.execute(_INSERT_CHANNEL_INGRESS_RECORD_SQL, (
                    row_id, redacted_channel, redacted_session_key, occurred_at,
                ))
            except Exception as exc:
                # The raw insert failed BEFORE journal.record() ever ran, so
                # recorder.py's own failure path never fires for this one --
                # degrade health here, the only place that will.
                remedy = remedy_for(exc) or f"{_TABLE} insert failed: {exc}"
                note_failure(remedy)
                raise
            await journal_record(conn, JournalEvent(
                type="channel.message_received", schema_version=1,
                occurred_at=occurred_at,
                actor_kind=ActorKind.OWNER, actor_id=session_key,
                target_kind=ActorKind.OWNER, target_id=channel, outcome=Outcome.OK,
                record_ref=RecordRef(kind="sqlite", locator={"table": _TABLE, "id": row_id}),
                attrs=ChannelMessageReceivedAttrs(channel=channel, session_key=session_key),
                trace_id=trace_id,
            ))
    except Exception as exc:
        # journal.record() already degraded health itself on its own failure
        # path (recorder.py) whenever IT is the one that raised -- this catch
        # exists so NEITHER that nor the raw-insert/attrs-validation failure
        # above can ever propagate into the receive loop, which must go on
        # to `turn_client.submit(msg)` regardless (B5).
        remedy = (
            remedy_for(exc)
            or f"channel_events failed for 'channel.message_received': {exc}"
        )
        note_failure(remedy)
        log.journal.error(
            "[journal] channel_events.record_channel_message_received: FAILED "
            "-- the message itself already arrived; only its journal row is missing",
            exc_info=exc,
            extra={"_fields": {"channel": channel, "trace_id": trace_id}},
        )
        return
    # 4. EXIT
    log.journal.debug(
        "[journal] channel_events.record_channel_message_received: exit -- recorded",
        extra={"_fields": {"channel": channel, "trace_id": trace_id}},
    )
