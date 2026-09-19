"""``ClarifyRaisedAttrs``, its registration, and the ONE recording helper
``clarify.raised`` needs (Story 3.3, AD-28).

SHAPED LIKE ``consent_events.py``'s ``record_consent_requested`` exactly:
``ClarifyGateway.ask()`` has no existing ASYNC DB mutation of its own to
piggyback a journal write onto (its state is an in-memory ``_pending`` dict),
so this module owns :func:`record_clarify_raised`, which opens its OWN
transaction, records ``clarify.raised`` (Story 3.1's generic ``NEEDS_YOU``
wiring inside ``journal.record()`` opens the durable ``question`` item as a
side effect of THAT write), then binds the calling coroutine's own in-memory
wait as the item's ``waiter_kind="turn"`` waiter -- all inside that SAME
transaction (AD-24).

Only the BLOCKING clarify path ever calls this (spec Boundaries: never for a
turn-yield ``ask(blocking=False)`` -- no in-flight turn waiter exists to
bind -- and never for an F-71 pre-resolved/auto-answered clarify -- nothing
is ever shown to a human).

Importing this module registers the type as a side effect;
``journal/__init__.py`` imports it for exactly that reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from pydantic import Field

from stackowl.infra.observability import log
from stackowl.journal import needs_you
from stackowl.journal.enums import ActorKind, AttentionClass, Intensity, NeedsYouKind, Outcome, RecordKind
from stackowl.journal.models import JournalAttrsBase, JournalEvent
from stackowl.journal.recorder import record as journal_record
from stackowl.journal.registry import EventTypeSpec, get_registry

if TYPE_CHECKING:  # pragma: no cover -- typing-only
    from stackowl.db.pool import DbPool

#: AD-4, verbatim: "attrs hold ids, numbers, closed enums and bounded labels
#: of at most 64 characters" -- mirrors every other journal attrs module's
#: own constant.
_MAX_LABEL_LEN = 64

_EMITTING_PROCESS = "interaction.clarify_gateway"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ClarifyRaisedAttrs(JournalAttrsBase):
    """``clarify.raised`` -- a BLOCKING clarify question began waiting on the
    owner (Story 3.3). Opens a ``question`` needs_you item."""

    session_key: str = Field(max_length=_MAX_LABEL_LEN)
    channel: str = Field(max_length=_MAX_LABEL_LEN)


def _narrate_clarify_raised(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- no resolver registered for RecordKind.INTERACTION (out of scope)
    a = cast(ClarifyRaisedAttrs, attrs)
    return f"A question was raised on {a.channel}."


def _register() -> None:
    get_registry().register(EventTypeSpec(
        type="clarify.raised", schema_version=1, attrs_model=ClarifyRaisedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.INTERACTION,
        attention_class=AttentionClass.NEEDS_YOU, intensity=Intensity.NORMAL,
        needs_you_kind=NeedsYouKind.QUESTION,
        # No single owning row -- like consent.requested, the request IS the
        # signal; the id itself (clarify_id, used as target_id below) is the
        # only locator that matters, and it already lives on the event row.
        table=None, narrate=_narrate_clarify_raised,
    ))


_register()


async def record_clarify_raised(
    db_pool: DbPool | None,
    *,
    clarify_id: str,
    session_key: str,
    channel: str,
    expires_at: str,
) -> str | None:
    """Record ``clarify.raised`` and bind THIS wait's own ``waiter_kind="turn"``
    waiter onto the durable ``question`` item it opens -- in ONE transaction
    (AD-24). Called from ``ClarifyGateway.ask()`` for a BLOCKING clarify only,
    the moment its own :class:`asyncio.Event` is constructed (spec Intent:
    "the moment ... starts waiting").

    ``clarify_id`` is already a fresh, non-sequential id (``ask()`` mints it
    via ``secrets.token_urlsafe`` before this is ever called) -- reused
    directly as the item's ``target_id`` rather than minting a second one
    (review pass 1 Group C: a per-request-unique target_id, never the shared
    ``session_key``, so two concurrent questions never collide onto one
    item).

    Returns the bound item's id on success, or ``None`` on: no ``db_pool``
    wired (mirrors ``consent_events.record_consent_requested``'s convention
    -- every unwired test/caller stays byte-identical); any failure along the
    way (never raises -- the question is still delivered/registered
    regardless of whether its durable record landed).
    """
    # 1. ENTRY
    log.gateway.debug(
        "[journal] interaction_events.record_clarify_raised: entry",
        extra={"_fields": {"clarify_id": clarify_id, "channel": channel}},
    )
    # 2. DECISION -- no DbPool wired is a silent no-op.
    if db_pool is None:
        log.gateway.debug(
            "[journal] interaction_events.record_clarify_raised: exit -- no "
            "db_pool wired",
            extra={"_fields": {"clarify_id": clarify_id}},
        )
        return None
    try:
        occurred_at = _now_iso()
        # 3. STEP -- record the triggering event (record()'s own NEEDS_YOU
        # wiring opens the item), then bind THIS wait's waiter onto it, both
        # inside the same transaction (AD-24).
        async with db_pool.transaction() as conn:
            await journal_record(conn, JournalEvent(
                type="clarify.raised", schema_version=1, occurred_at=occurred_at,
                actor_kind=ActorKind.AUTONOMOUS, actor_id=_EMITTING_PROCESS,
                target_kind=ActorKind.OWNER, target_id=clarify_id,
                outcome=Outcome.OK,
                attrs=ClarifyRaisedAttrs(session_key=session_key, channel=channel),
            ))
            item_id = await needs_you.bind_waiter(
                conn, kind=NeedsYouKind.QUESTION, target_kind=ActorKind.OWNER.value,
                target_id=clarify_id, waiter_kind=needs_you.WAITER_KIND_TURN,
                waiter_id=clarify_id, expires_at=expires_at,
            )
    except Exception as exc:
        log.gateway.error(
            "[journal] interaction_events.record_clarify_raised: FAILED -- "
            "the question is still delivered; only its durable item is missing",
            exc_info=exc,
            extra={"_fields": {"clarify_id": clarify_id, "channel": channel}},
        )
        return None
    # 4. EXIT
    log.gateway.debug(
        "[journal] interaction_events.record_clarify_raised: exit",
        extra={"_fields": {"clarify_id": clarify_id, "item_id": item_id}},
    )
    return item_id
