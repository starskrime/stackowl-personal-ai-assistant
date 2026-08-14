"""Slack B2 — SlackActionRouter dispatch + idempotency tests.

Mirrors the Telegram CallbackRouter behavior: prefix→handler registry,
longest-prefix match, idempotent at-least-once dispatch (Slack may re-deliver a
block_actions payload). The router routes ``consent:`` to the consent prompter,
``clarify:`` to the clarify resolver. The memory approve/reject cases that used
to live here went with their handler in D08.2 slice B.
"""

from __future__ import annotations

import pytest

from stackowl.channels.slack.callbacks import SlackActionRouter


class _Recorder:
    """Captures (action_id) calls so a test can assert which handler fired."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[str] = []

    async def handle(self, action_id: str) -> None:
        self.calls.append(action_id)


class _Raiser:
    def __init__(self) -> None:
        self.calls = 0

    async def handle(self, action_id: str) -> None:
        self.calls += 1
        raise RuntimeError("handler boom")


@pytest.mark.asyncio
async def test_route_dispatches_consent_prefix() -> None:
    router = SlackActionRouter()
    consent = _Recorder("consent")
    clarify = _Recorder("clarify")
    router.register("consent:", consent.handle)
    router.register("clarify:", clarify.handle)

    await router.route("consent:abc123:once", delivery_id="d1")

    assert consent.calls == ["consent:abc123:once"]
    assert clarify.calls == []


@pytest.mark.asyncio
async def test_route_dispatches_clarify_prefix() -> None:
    router = SlackActionRouter()
    clarify = _Recorder("clarify")
    router.register("clarify:", clarify.handle)

    await router.route("clarify:cid:2", delivery_id="d2")

    assert clarify.calls == ["clarify:cid:2"]


@pytest.mark.asyncio
async def test_route_longest_prefix_wins() -> None:
    """A more specific (longer) prefix must beat a shorter overlapping one."""
    router = SlackActionRouter()
    short = _Recorder("short")
    long = _Recorder("long")
    router.register("memory_", short.handle)
    router.register("memory_approve_", long.handle)

    await router.route("memory_approve_xyz", delivery_id="lp1")

    assert long.calls == ["memory_approve_xyz"]
    assert short.calls == []


@pytest.mark.asyncio
async def test_route_idempotent_duplicate_does_not_double_fire() -> None:
    """A re-delivered action (same delivery_id) must not fire the handler twice."""
    router = SlackActionRouter()
    consent = _Recorder("consent")
    router.register("consent:", consent.handle)

    await router.route("consent:abc:once", delivery_id="dup")
    await router.route("consent:abc:once", delivery_id="dup")

    assert consent.calls == ["consent:abc:once"]


@pytest.mark.asyncio
async def test_route_distinct_delivery_ids_both_fire() -> None:
    router = SlackActionRouter()
    consent = _Recorder("consent")
    router.register("consent:", consent.handle)

    await router.route("consent:abc:once", delivery_id="x1")
    await router.route("consent:abc:once", delivery_id="x2")

    assert consent.calls == ["consent:abc:once", "consent:abc:once"]


@pytest.mark.asyncio
async def test_route_unknown_prefix_is_noop() -> None:
    router = SlackActionRouter()
    consent = _Recorder("consent")
    router.register("consent:", consent.handle)

    # Must not raise and must not fire any handler.
    await router.route("totally_unknown_action", delivery_id="u1")

    assert consent.calls == []


@pytest.mark.asyncio
async def test_route_handler_exception_is_contained() -> None:
    """A raising handler must not propagate out of route (fail-open)."""
    router = SlackActionRouter()
    raiser = _Raiser()
    router.register("consent:", raiser.handle)

    # Should not raise.
    await router.route("consent:abc:once", delivery_id="e1")

    assert raiser.calls == 1
    # Even though the handler raised, the delivery is marked processed so a
    # re-delivery is not re-fired (fail-open idempotency, mirrors Telegram).
    await router.route("consent:abc:once", delivery_id="e1")
    assert raiser.calls == 1
