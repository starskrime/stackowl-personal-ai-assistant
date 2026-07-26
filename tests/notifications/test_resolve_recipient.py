"""D01.7 — a proactive send resolves its recipient from the LANE, not from luck.

Before this, delivery recovered the recipient by ``int(session_key)``, which only
worked because the lane happened to BE the Telegram chat id. A composite lane
("owl:secretary:telegram:dm:72055773") is not int()-able, so every proactive send
would have fallen through to the adapter's shared ``_last_chat_id`` — the
cross-delivery bug this codebase already fixed once.

The store is authoritative; the old heuristic stays as a fallback so nothing that
worked before stops working.
"""

from __future__ import annotations

import pytest

from stackowl.notifications.router_helpers import resolve_recipient


class _Store:
    """Duck-typed stand-in for SessionStore.resolve_send_target."""

    def __init__(self, target: str | None = None, boom: bool = False) -> None:
        self._target = target
        self._boom = boom
        self.asked: list[str] = []

    async def resolve_send_target(self, session_key: str) -> str | None:
        self.asked.append(session_key)
        if self._boom:
            raise RuntimeError("store unavailable")
        return self._target


@pytest.mark.asyncio
async def test_a_composite_lane_resolves_through_the_store() -> None:
    """THE regression this exists to prevent: int() cannot parse a composite lane."""
    store = _Store("72055773")
    got = await resolve_recipient("telegram", "owl:secretary:telegram:dm:72055773", store)
    assert got == 72055773
    assert store.asked == ["owl:secretary:telegram:dm:72055773"]


@pytest.mark.asyncio
async def test_a_composite_lane_without_a_store_is_unresolved_not_guessed() -> None:
    """Falls through to the heuristic, which correctly refuses to parse it.
    None means 'say so loudly' — never a fabricated recipient."""
    assert await resolve_recipient("telegram", "owl:secretary:telegram:dm:72055773") is None


@pytest.mark.asyncio
async def test_a_bare_chat_id_still_resolves_with_no_store() -> None:
    """Byte-identical to the old behaviour for every caller that has not changed."""
    assert await resolve_recipient("telegram", "72055773") == 72055773


@pytest.mark.asyncio
async def test_an_unknown_lane_falls_back_to_the_heuristic() -> None:
    """Store knows nothing (a lane created before D01.7) → the old path still works."""
    store = _Store(None)
    assert await resolve_recipient("telegram", "72055773", store) == 72055773


@pytest.mark.asyncio
async def test_a_store_failure_never_blocks_a_send() -> None:
    """Recover loudly: the error is logged and the heuristic still runs."""
    store = _Store(boom=True)
    assert await resolve_recipient("telegram", "72055773", store) == 72055773


@pytest.mark.asyncio
async def test_a_non_numeric_native_target_is_not_forced_into_a_chat_id() -> None:
    """A Slack channel id is a legitimate target that is simply not a Telegram
    chat_id. It must not be coerced, and must not be guessed at either."""
    store = _Store("C01ABCDEF")
    assert await resolve_recipient("slack", "owl:secretary:slack:channel:C01ABCDEF", store) is None


@pytest.mark.asyncio
async def test_a_blank_lane_resolves_to_nothing() -> None:
    store = _Store("72055773")
    assert await resolve_recipient("telegram", "", store) is None
    assert store.asked == [], "a blank lane must not even be looked up"
