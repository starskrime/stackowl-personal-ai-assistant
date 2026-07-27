"""Resolve which RUN of a conversation an inbound turn belongs to (D01.7).

This is the INGRESS SEQUENCE: build the source, resolve the lane, render the
boundary notice, consume it. The order matters more than any single step —
"shown exactly once" (invariant I5) is a property of the sequence, and getting
it wrong shows the note forever or never while every unit test still passes.

It lived as a nested closure inside ``startup/orchestrator.py``'s gateway phase
until 2026-07-26, reachable only by constructing the whole orchestrator, so
nothing tested it. Extracted here so the requirement Bakir actually stated —
"a short one-line note, shown once; a boundary the user cannot see is one they
experience as amnesia" — can be pinned by a test. The orchestrator now holds a
thin closure that delegates, and behaviour is unchanged.
"""

from __future__ import annotations

import datetime
from typing import Any, Protocol

from stackowl.infra.observability import log
from stackowl.sessions.models import ChatType, SessionSource
from stackowl.sessions.policy import reset_notice


class _Message(Protocol):
    """The fields ingress reads off an ``IngressMessage``."""

    channel: str
    is_direct: bool
    session_key: str
    chat_id: int | None


async def resolve_turn_session(
    msg: Any,
    *,
    owl_name: str,
    session_store: Any,
    session_settings: Any,
    services: Any,
    now: datetime.datetime | None = None,
) -> tuple[str, str, str | None]:
    """Return ``(session_key, session_id, notice)`` for this turn.

    Called AFTER routing because the lane is keyed on the owl (Bakir's Q1: a
    different owl is a different conversation) and the owl is a routing OUTPUT
    — so no lane can exist at ``IngressMessage`` time.

    The daily boundary is a LOCAL wall-clock hour (4 AM by default), so ``now``
    is local-aware rather than UTC; a UTC "4 AM" would fire in the middle of the
    afternoon for most of the world.

    The notice is CONSUMED here, so it is shown exactly once (invariant I5) even
    if the turn later fails: a boundary the user cannot see is one they
    experience as amnesia, and one they see twice reads as a bug.

    FAILS OPEN, LOUDLY: a session-store problem must never cost the user their
    reply. The turn then falls back to the channel-native lane with no
    incarnation, which is exactly the pre-D01.7 behaviour — degraded, never
    broken, and never a fabricated conversation.
    """
    from stackowl.pipeline.services import resolve_identity_key

    stamp = now or datetime.datetime.now().astimezone()
    try:
        source = SessionSource(
            owl_name=owl_name,
            channel=msg.channel,
            chat_type=ChatType.DM if msg.is_direct else ChatType.GROUP,
            chat_id=msg.session_key,
            chat_target=str(msg.chat_id) if msg.chat_id is not None else None,
            # Stamped HERE because this is where the identity is already
            # resolved. The sweeper publishes rollovers at 4 AM with no ingress
            # context, so a summary consumer that had to re-derive the owner
            # would have nothing to derive it from — and a summary filed under
            # the owl-prefixed lane is one recall never sees.
            identity_key=resolve_identity_key(services, msg.session_key),
        )
        entry, branch, reason = await session_store.resolve_for(
            source,
            stamp,
            group_per_user=session_settings.group_sessions_per_user,
            thread_per_user=session_settings.thread_sessions_per_user,
        )
        log.gateway.info(
            "[startup] gateway: session resolved",
            extra={"_fields": {"session_key": entry.session_key,
                               "session_id": entry.session_id,
                               "branch": branch.value,
                               "reason": reason.value if reason else None,
                               "owl": owl_name}},
        )
        notice = reset_notice(entry) if session_settings.notify_on_reset else None
        if notice:
            entry = await session_store.consume_reset_notice(entry)
        return entry.session_key, entry.session_id, notice
    except Exception as exc:
        log.gateway.error(
            "[startup] gateway: session resolution failed — turn continues "
            "without an incarnation",
            exc_info=exc,
            extra={"_fields": {"owl": owl_name, "channel": getattr(msg, "channel", "")}},
        )
        return msg.session_key, "", None
