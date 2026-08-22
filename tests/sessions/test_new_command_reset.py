"""D01.7 — /new must actually end the conversation.

THE DEFECT (found 2026-07-27, by Bakir sending /new on Telegram). The command
routed, ran, and silently did nothing:

    [commands] new.handle: exit — no lane to end
    fields.session_key = "72055773"

while the stored lane was "owl:secretary:telegram:dm:72055773". Both turns
carried straight through on the previous day's incarnation (branch=existing,
reason=null). It had never worked.

WHY. Commands dispatch at the GATEWAY, before routing. The composite lane is
keyed on the OWL, and the owl is a routing OUTPUT — which is exactly why
_resolve_incarnation runs after routing. So at command time the composite lane
does not exist yet and all /new has is the channel-native id.

THE FIX (Bakir, 2026-07-27). /new records a PENDING reset against the key it
does have; the next lane resolution consumes it and mints a fresh incarnation.
The reset therefore happens in the one place that already knows how to end a
lane, rather than teaching a second place to do it — the same shape as the
was_auto_reset flag this item already carries.
"""

from __future__ import annotations

import datetime

from stackowl.db.pool import DbPool
from stackowl.sessions.models import ChatType, SessionSource
from stackowl.sessions.store import SessionStore

CHAT = "72055773"


def _source(owl: str = "secretary") -> SessionSource:
    return SessionSource(
        owl_name=owl, channel="telegram", chat_type=ChatType.DM, chat_id=CHAT,
    )


def _at(hour: int, day: int = 27) -> datetime.datetime:
    return datetime.datetime(2026, 7, day, hour, 0, tzinfo=datetime.UTC).astimezone()


async def test_new_ends_the_lane_it_could_not_previously_find(tmp_db: DbPool) -> None:
    """The whole defect, in one test: a pending reset requested with the
    CHANNEL-NATIVE key must end the COMPOSITE lane."""
    store = SessionStore(tmp_db)
    first, _b, _r = await store.resolve_for(_source(), _at(10))

    await store.request_new_incarnation(CHAT)
    second, branch, _r2 = await store.resolve_for(_source(), _at(11))

    assert second.session_key == first.session_key, "same lane — /new does not re-key"
    assert second.conversation_id != first.conversation_id, "but a NEW conversation"
    assert branch.value != "existing"


async def test_the_pending_reset_is_consumed_exactly_once(tmp_db: DbPool) -> None:
    """One /new ends one conversation. A flag left set would silently start a
    fresh conversation on every later message — the opposite failure, and a far
    more annoying one."""
    store = SessionStore(tmp_db)
    await store.resolve_for(_source(), _at(10))

    await store.request_new_incarnation(CHAT)
    second, _b, _r = await store.resolve_for(_source(), _at(11))
    third, branch, _r2 = await store.resolve_for(_source(), _at(12))

    assert third.conversation_id == second.conversation_id
    assert branch.value == "existing"


async def test_a_reset_for_one_chat_leaves_another_alone(tmp_db: DbPool) -> None:
    """The key is the chat, not the process."""
    store = SessionStore(tmp_db)
    other = SessionSource(
        owl_name="secretary", channel="telegram", chat_type=ChatType.DM, chat_id="999",
    )
    mine, _b, _r = await store.resolve_for(_source(), _at(10))
    theirs, _b2, _r2 = await store.resolve_for(other, _at(10))

    await store.request_new_incarnation(CHAT)
    mine2, _b3, _r3 = await store.resolve_for(_source(), _at(11))
    theirs2, _b4, _r4 = await store.resolve_for(other, _at(11))

    assert mine2.conversation_id != mine.conversation_id
    assert theirs2.conversation_id == theirs.conversation_id


async def test_new_is_reported_as_deliberate_not_as_an_expiry(tmp_db: DbPool) -> None:
    """Invariant I5's other half: the user typed /new, so they must never be
    told their conversation 'expired'. is_fresh_reset is what keeps those two
    apart."""
    from stackowl.sessions.policy import reset_notice

    store = SessionStore(tmp_db)
    await store.resolve_for(_source(), _at(10))
    await store.request_new_incarnation(CHAT)

    entry, _b, _r = await store.resolve_for(_source(), _at(11))

    assert entry.is_fresh_reset is True
    assert entry.was_auto_reset is False
    assert reset_notice(entry) is None, "a deliberate /new is not an 'expired' notice"


async def test_a_reset_requested_before_any_lane_exists_still_works(
    tmp_db: DbPool,
) -> None:
    """/new on a brand-new chat has no lane to end. That must be a quiet no-op
    that still leaves the user in a working conversation, not an error."""
    store = SessionStore(tmp_db)

    await store.request_new_incarnation(CHAT)
    entry, _b, _r = await store.resolve_for(_source(), _at(10))

    assert entry.conversation_id, "the turn still gets a conversation"
