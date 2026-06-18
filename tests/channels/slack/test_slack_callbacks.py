"""Slack B2 — SlackActionRouter dispatch + idempotency tests.

Mirrors the Telegram CallbackRouter behavior: prefix→handler registry,
longest-prefix match, idempotent at-least-once dispatch (Slack may re-deliver a
block_actions payload). The router routes ``consent:`` to the consent prompter,
``clarify:`` to the clarify resolver, and ``memory_approve_``/``memory_reject_``
to the memory handlers.
"""

from __future__ import annotations

import pytest

from stackowl.channels.slack.callbacks import SlackActionRouter
from stackowl.channels.slack.helpers import SlackBlockKitFormatter
from stackowl.channels.slack.memory_callbacks import SlackMemoryActionHandler
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.models import StagedFact
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.memory.trust import trust_for_source


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
async def test_route_dispatches_memory_approve_and_reject() -> None:
    router = SlackActionRouter()
    approve = _Recorder("approve")
    reject = _Recorder("reject")
    router.register("memory_approve_", approve.handle)
    router.register("memory_reject_", reject.handle)

    await router.route("memory_approve_deadbeef", delivery_id="a1")
    await router.route("memory_reject_cafebabe", delivery_id="r1")

    assert approve.calls == ["memory_approve_deadbeef"]
    assert reject.calls == ["memory_reject_cafebabe"]


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


# --------------------------------------------------------------------------- #
# Memory approve/reject → REAL MemoryBridge + FactPromoter over a tmp DB.
#
# B2 review I-1: the formatter must encode the FULL fact_id into the action_id
# (NOT a truncated 8-char prefix). Both FactPromoter.force_promote and
# SqliteMemoryBridge.delete do EXACT-MATCH SQL on the full UUID — so a truncated
# prefix is a SILENT NO-OP (the fact is never promoted/deleted). These tests
# stage a REAL fact, render the nudge through the production formatter, route the
# resulting action_id through the handler, and assert the fact ACTUALLY MOVED.
# --------------------------------------------------------------------------- #

class _RealMemoryBridge:
    """Production-shaped duck-typed bridge for the Slack memory handler.

    The handler probes ``hasattr(bridge, "force_promote")`` and calls
    ``delete(fact_id)``. ``force_promote`` lives on :class:`FactPromoter` and
    ``delete`` on :class:`SqliteMemoryBridge`, so this thin adapter exposes both
    over the SAME tmp DB — exercising the real EXACT-MATCH SQL (no fake that
    happily accepts a truncated id).
    """

    def __init__(self, bridge: SqliteMemoryBridge, promoter: FactPromoter) -> None:
        self._bridge = bridge
        self._promoter = promoter

    async def force_promote(self, fact_id: str) -> bool:
        return await self._promoter.force_promote(fact_id)

    async def delete(self, fact_id: str) -> None:
        await self._bridge.delete(fact_id)


def _extract_action_id(blocks: list[dict[str, object]], prefix: str) -> str:
    """Pull the approve/reject ``action_id`` out of the rendered nudge blocks."""
    for block in blocks:
        if block.get("type") != "actions":
            continue
        for element in block["elements"]:  # type: ignore[index]
            action_id = element["action_id"]  # type: ignore[index]
            if action_id.startswith(prefix):
                return action_id  # type: ignore[no-any-return]
    raise AssertionError(f"no action_id with prefix {prefix!r} in nudge blocks")


async def _stage_fact(bridge: SqliteMemoryBridge, content: str) -> str:
    """Stage a real fact with a full-UUID fact_id and return that id."""
    fact = StagedFact(
        content=content,
        source_type="manual",
        source_ref="slack:test",
        confidence=1.0,
        trust=trust_for_source("manual"),
    )
    await bridge.stage(fact)
    return fact.fact_id


def _build_real_bridge(tmp_db: DbPool) -> _RealMemoryBridge:
    bridge = SqliteMemoryBridge(tmp_db, semantic_search_enabled=False)
    promoter = FactPromoter(tmp_db)
    return _RealMemoryBridge(bridge, promoter)


@pytest.mark.asyncio
async def test_memory_approve_actually_promotes_the_staged_fact(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approve → the staged fact ACTUALLY moves to committed_facts.

    RED on the truncated-id code: the action_id carries only the first 8 chars,
    FactPromoter.force_promote exact-matches the full UUID → no match → the fact
    is NOT promoted (committed_facts stays empty).
    """
    monkeypatch.setattr(
        TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None)
    )
    real = _build_real_bridge(tmp_db)
    bridge = real._bridge
    fact_id = await _stage_fact(bridge, "The user prefers dark mode")

    blocks = SlackBlockKitFormatter().format_memory_nudge(fact_id, "The user prefers dark mode")
    action_id = _extract_action_id(blocks, "memory_approve_")

    router = SlackActionRouter()
    handler = SlackMemoryActionHandler(real)  # type: ignore[arg-type]
    handler.register(router)
    await router.route(action_id, delivery_id="m1")

    committed = await tmp_db.fetch_all(
        "SELECT fact_id FROM committed_facts WHERE fact_id = ?", (fact_id,)
    )
    assert committed, "approved fact must be promoted into committed_facts"
    staged = await bridge.list_staged(status="staged")
    assert all(f.fact_id != fact_id for f in staged), "fact must leave the staged queue"


@pytest.mark.asyncio
async def test_memory_reject_actually_deletes_the_staged_fact(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject → the staged fact is ACTUALLY gone.

    RED on the truncated-id code: delete exact-matches the full UUID, the
    truncated prefix never matches → the fact survives in staged_facts.
    """
    monkeypatch.setattr(
        TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None)
    )
    real = _build_real_bridge(tmp_db)
    bridge = real._bridge
    fact_id = await _stage_fact(bridge, "The user dislikes spam")

    blocks = SlackBlockKitFormatter().format_memory_nudge(fact_id, "The user dislikes spam")
    action_id = _extract_action_id(blocks, "memory_reject_")

    router = SlackActionRouter()
    handler = SlackMemoryActionHandler(real)  # type: ignore[arg-type]
    handler.register(router)
    await router.route(action_id, delivery_id="m2")

    staged = await bridge.list_staged(status="staged")
    assert all(f.fact_id != fact_id for f in staged), "rejected fact must be deleted"
    rows = await tmp_db.fetch_all(
        "SELECT fact_id FROM staged_facts WHERE fact_id = ?", (fact_id,)
    )
    assert not rows, "rejected fact must be gone from staged_facts entirely"


@pytest.mark.asyncio
async def test_memory_approve_unknown_fact_logs_not_found_and_does_not_crash(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Approving an unknown (full-id) fact must surface a loud not-found warning.

    M-2 (no-hidden-errors): force_promote returns False for a missing fact; the
    handler must log it as a warning rather than treat it as success — and must
    not crash.
    """
    import logging

    monkeypatch.setattr(
        TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None)
    )
    real = _build_real_bridge(tmp_db)
    unknown_id = "00000000-0000-4000-8000-000000000000"
    action_id = f"memory_approve_{unknown_id}"

    router = SlackActionRouter()
    handler = SlackMemoryActionHandler(real)  # type: ignore[arg-type]
    handler.register(router)

    with caplog.at_level(logging.WARNING):
        await router.route(action_id, delivery_id="m3")  # must not raise

    committed = await tmp_db.fetch_all("SELECT fact_id FROM committed_facts")
    assert committed == [], "no fact should be promoted for an unknown id"
    assert any(
        "not found" in rec.getMessage().lower() or "not promoted" in rec.getMessage().lower()
        for rec in caplog.records
    ), "handler must log a loud not-found warning for an unknown fact"
