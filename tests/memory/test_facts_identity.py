"""Cross-channel identity: extracted-fact staging scoped on identity_key.

TDD tests (failing-first) for Task 4 of the cross-channel identity feature.

Invariants under test:
  1. CROSS-CHANNEL REINFORCEMENT — the same fact content stated from telegram and
     from slack (both resolving to "owner-primary") shares a single staged row with
     source_ref="owner-primary" and reinforcement_count incremented — NOT two rows
     under two separate session_ids.
  2. NEGATIVE CONTROL — conversation rows (source_type='conversation') staged under
     telegram:123 are NOT visible to recent_conversation_turns("slack:U0"); live
     conversation stays per-channel.
  3. POSITIVE CONTROL — those same conversation rows ARE visible to
     recent_conversation_turns("telegram:123").
  4. FALLBACK BYTE-IDENTICAL — with an empty/no resolver, extract() stamps
     source_ref=session_id unchanged (today's behavior).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.conversation_miner import ConversationMiner
from stackowl.memory.fact_extractor import FactExtractor
from stackowl.memory.models import StagedFact
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.tenancy.identity import IdentityResolver

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubProvider(ModelProvider):
    """Always returns one fact: 'The user likes hiking'."""

    @property
    def name(self) -> str:
        return "stub"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content='[{"content": "The user likes hiking", "confidence": 0.9}]',
            input_tokens=5,
            output_tokens=3,
            model="stub",
            provider_name="stub",
            duration_ms=0.5,
        )

    def stream(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        raise NotImplementedError


def _bypass_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *a, **kw: None,
    )


# ---------------------------------------------------------------------------
# Test 1: Cross-channel reinforcement (the CORE invariant — RED before impl)
# ---------------------------------------------------------------------------


async def test_cross_channel_fact_shares_source_ref_under_identity(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Facts extracted from telegram and slack sessions resolving to the same identity
    share a single staged row keyed on the identity, NOT two per-channel rows.

    Today (no resolver) this would produce two separate rows under 'telegram:123' and
    'slack:U0'. After implementation it must produce one row under 'owner-primary'
    with reinforcement_count=1 (after the second extraction triggers reinforcement).
    """
    _bypass_test_mode(monkeypatch)

    resolver = IdentityResolver(
        {"owner-primary": ["telegram:123", "slack:U0"]}
    )
    extractor = FactExtractor(provider=_StubProvider(), identity_resolver=resolver)
    bridge = SqliteMemoryBridge(tmp_db)

    convo = [Message(role="user", content="I love hiking.")]

    # Stage a fact from the telegram session
    telegram_facts = await extractor.extract(convo, session_id="telegram:123")
    assert telegram_facts, "extractor must return at least one fact"
    for f in telegram_facts:
        await bridge.stage(f)

    # Stage the same fact again from the slack session (same user, same identity)
    slack_facts = await extractor.extract(convo, session_id="slack:U0")
    assert slack_facts, "extractor must return at least one fact from slack session"

    # The slack fact should share source_ref="owner-primary" — will trigger
    # reinforcement in the miner, or we can check source_ref directly.
    for f in slack_facts:
        assert f.source_ref == "owner-primary", (
            f"slack fact must be keyed on identity 'owner-primary', got: {f.source_ref!r}"
        )

    # Also verify telegram facts are keyed on identity
    for f in telegram_facts:
        assert f.source_ref == "owner-primary", (
            f"telegram fact must be keyed on identity 'owner-primary', got: {f.source_ref!r}"
        )

    # Now run the miner end-to-end via ConversationMiner to verify reinforcement:
    # Store conversation turns for both sessions, mine both, assert one row.
    await bridge.store("User: I love hiking.\n\nAssistant: Great!", "telegram:123")
    await bridge.store("User: I love hiking.\n\nAssistant: Enjoy!", "slack:U0")

    miner = ConversationMiner(
        db=tmp_db,
        extractor=extractor,
        bridge=bridge,
        message_limit=20,
    )
    # Mine telegram first — should stage 1 row under owner-primary
    count1 = await miner.mine_session("telegram:123")
    # Mine slack — same content, same source_ref → reinforcement, not a new row
    count2 = await miner.mine_session("slack:U0")

    rows = await tmp_db.fetch_all(
        "SELECT source_ref, reinforcement_count FROM staged_facts "
        "WHERE source_type='conversation_fact' AND content='The user likes hiking'",
    )
    # There must be exactly ONE row (not two per-channel rows)
    assert len(rows) == 1, (
        f"Expected 1 shared staged row under owner-primary, got {len(rows)} rows: {rows}"
    )
    assert rows[0]["source_ref"] == "owner-primary", (
        f"staged row source_ref must be 'owner-primary', got: {rows[0]['source_ref']!r}"
    )
    assert rows[0]["reinforcement_count"] >= 1, (
        f"Second extraction from slack must reinforce the row, got: {rows[0]['reinforcement_count']}"
    )


# ---------------------------------------------------------------------------
# Test 2: NEGATIVE CONTROL — conversation rows stay per-channel
# ---------------------------------------------------------------------------


async def test_conversation_rows_are_per_channel_not_cross_channel(
    tmp_db: DbPool,
) -> None:
    """source_type='conversation' rows staged under telegram:123 are NOT visible
    to recent_conversation_turns('slack:U0').
    """
    bridge = SqliteMemoryBridge(tmp_db)
    # Store a conversation turn under telegram:123
    await bridge.store("User: I live in Baku.\n\nAssistant: Noted.", "telegram:123")

    # Slack channel must NOT see telegram's conversation turns
    slack_turns = await bridge.recent_conversation_turns("slack:U0")
    assert not slack_turns, (
        f"slack must not see telegram conversation turns, got: {slack_turns}"
    )


# ---------------------------------------------------------------------------
# Test 3: POSITIVE CONTROL — conversation rows visible to same session
# ---------------------------------------------------------------------------


async def test_conversation_rows_visible_to_same_session(
    tmp_db: DbPool,
) -> None:
    """source_type='conversation' rows staged under telegram:123 ARE returned by
    recent_conversation_turns('telegram:123').
    """
    bridge = SqliteMemoryBridge(tmp_db)
    await bridge.store("User: I live in Baku.\n\nAssistant: Noted.", "telegram:123")

    turns = await bridge.recent_conversation_turns("telegram:123")
    assert turns, "telegram session must see its own conversation turns"
    assert any("Baku" in t.content for t in turns), (
        f"expected Baku in turns, got: {[t.content for t in turns]}"
    )


# ---------------------------------------------------------------------------
# Test 4: FALLBACK byte-identical — empty resolver leaves source_ref unchanged
# ---------------------------------------------------------------------------


async def test_fallback_no_resolver_leaves_source_ref_as_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no identity_resolver (or empty resolver), extract() stamps source_ref=session_id."""
    _bypass_test_mode(monkeypatch)

    extractor = FactExtractor(provider=_StubProvider())  # no resolver
    convo = [Message(role="user", content="I prefer dark mode.")]
    facts = await extractor.extract(convo, session_id="telegram:9")

    assert facts, "extractor must return at least one fact"
    for f in facts:
        assert f.source_ref == "telegram:9", (
            f"With no resolver, source_ref must equal session_id 'telegram:9', got: {f.source_ref!r}"
        )


async def test_fallback_empty_resolver_leaves_source_ref_as_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With an empty IdentityResolver (no aliases), source_ref==session_id (unconfigured = today's behavior)."""
    _bypass_test_mode(monkeypatch)

    extractor = FactExtractor(
        provider=_StubProvider(),
        identity_resolver=IdentityResolver({}),  # empty — resolve(x)==x
    )
    convo = [Message(role="user", content="I prefer dark mode.")]
    facts = await extractor.extract(convo, session_id="telegram:9")

    assert facts, "extractor must return at least one fact"
    for f in facts:
        assert f.source_ref == "telegram:9", (
            f"Empty resolver must leave source_ref unchanged, got: {f.source_ref!r}"
        )
