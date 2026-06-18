"""Slack B2 — SlackClarifyResolver tests.

Mirrors the Telegram clarify resolver: a ``clarify:{clarify_id}:{idx}`` button
tap maps ``idx`` back to the entry's choice text via ``ClarifyGateway.peek`` and
resolves the parked turn via ``try_resolve_by_id``. Fail-safe: a malformed
payload, a stale/superseded id (peek → None), or an out-of-range index is a
logged no-op.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from stackowl.channels.slack.clarify import SlackClarifyResolver


@dataclass
class _FakeEntry:
    session_id: str
    channel: str
    choices: tuple[str, ...]


class _FakeGateway:
    """Captures peek/try_resolve_by_id calls for assertion."""

    def __init__(self, entry: _FakeEntry | None) -> None:
        self._entry = entry
        self.resolved: list[tuple[str, str]] = []

    def peek(self, clarify_id: str) -> _FakeEntry | None:
        return self._entry

    def try_resolve_by_id(self, clarify_id: str, answer: str):  # noqa: ANN201
        self.resolved.append((clarify_id, answer))
        return self._entry


@pytest.mark.asyncio
async def test_handle_action_maps_index_to_choice_and_resolves() -> None:
    entry = _FakeEntry(
        session_id="slack:abcd1234",
        channel="slack",
        choices=("red", "green", "blue"),
    )
    gw = _FakeGateway(entry)
    resolver = SlackClarifyResolver(gw)  # type: ignore[arg-type]

    await resolver.handle_action("clarify:cid:2")

    assert gw.resolved == [("cid", "blue")]


@pytest.mark.asyncio
async def test_handle_action_malformed_is_noop() -> None:
    gw = _FakeGateway(_FakeEntry("s", "slack", ("a",)))
    resolver = SlackClarifyResolver(gw)  # type: ignore[arg-type]

    await resolver.handle_action("clarify:onlytwo")  # missing idx
    await resolver.handle_action("notclarify:cid:0")  # wrong prefix

    assert gw.resolved == []


@pytest.mark.asyncio
async def test_handle_action_non_int_index_is_noop() -> None:
    gw = _FakeGateway(_FakeEntry("s", "slack", ("a", "b")))
    resolver = SlackClarifyResolver(gw)  # type: ignore[arg-type]

    await resolver.handle_action("clarify:cid:notanumber")

    assert gw.resolved == []


@pytest.mark.asyncio
async def test_handle_action_stale_id_is_noop() -> None:
    gw = _FakeGateway(None)  # peek returns None → stale tap
    resolver = SlackClarifyResolver(gw)  # type: ignore[arg-type]

    await resolver.handle_action("clarify:cid:0")

    assert gw.resolved == []


@pytest.mark.asyncio
async def test_handle_action_index_out_of_range_is_noop() -> None:
    gw = _FakeGateway(_FakeEntry("s", "slack", ("only",)))
    resolver = SlackClarifyResolver(gw)  # type: ignore[arg-type]

    await resolver.handle_action("clarify:cid:5")

    assert gw.resolved == []
