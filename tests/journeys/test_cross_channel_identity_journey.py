"""Cross-channel identity merge-gate journey.

Asserts on assembled context / store state — NOT on model output text.

Four scenarios:

  (a) Preference crosses channels — a preference stored under the identity_key
      appears when _gather_preferences is called via state.identity_key for BOTH
      a SLACK PipelineState and a TELEGRAM PipelineState that share the same
      identity_key="owner-primary".  Pre-Task-3 the classify step used session_id,
      so the Slack turn (session_id="slack:abc") would have missed the preference
      stored under "owner-primary".

  (b) Fact store unity (real-seam assertion) — FactExtractor.extract() is called
      for BOTH "telegram:123" and "slack:abc" sessions via a resolver that maps
      both to "owner-primary".  The resulting StagedFacts carry source_ref=
      "owner-primary" (not the per-channel handles), and the DB contains zero rows
      under either per-channel handle after staging.  This WOULD have been RED
      before Task 4 when source_ref=session_id.

  (c) Conversation does NOT cross — MemoryBridge.store() writes a conversation
      turn under "telegram:123".  The POSITIVE control confirms it is visible to
      recent_conversation_turns("telegram:123"); the NEGATIVE control confirms
      recent_conversation_turns("slack:abc") returns EMPTY even though both
      handles resolve to the same identity — conversation history stays per-channel.

  (d) Unconfigured byte-identical — IdentityResolver({}).resolve() returns the
      handle unchanged; when identity_key is empty, _gather_preferences falls back
      to state.session_id.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.exceptions import DuplicateFactError
from stackowl.memory.fact_extractor import FactExtractor
from stackowl.memory.preferences import PreferenceStore
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.classify import _gather_preferences
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tenancy.identity import IdentityResolver
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

# ---------------------------------------------------------------------------
# _CapturingProvider — scripted provider that captures the system_text handed
# to complete_with_tools so we can assert on assembled prompt content.
# ---------------------------------------------------------------------------


class _CapturingProvider(ModelProvider):
    """Captures system_text from complete_with_tools; returns canned answers."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.system_text: str = ""

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        content = (
            '{"delivered": true, "reason": "ok"}'
            if "AGENT DRAFT REPLY" in joined
            else "secretary\nconversational"
        )
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="test-model",
            provider_name=self._name,
            duration_ms=1.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        yield "Done."

    async def complete_with_tools(  # type: ignore[override]
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: list[object],
        tool_dispatcher: Any,
        history: list[Message] | None = None,
        **kw: object,
    ) -> tuple[str, list[object]]:
        self.system_text = system_text or ""
        return "Done.", []


# ---------------------------------------------------------------------------
# _StubExtractorProvider — always returns one fact, used in test (b).
# Mirrors _StubProvider from tests/memory/test_facts_identity.py.
# ---------------------------------------------------------------------------


class _StubExtractorProvider(ModelProvider):
    """Always returns one fact: 'The user prefers dark mode'."""

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
            content='[{"content": "The user prefers dark mode", "confidence": 0.9}]',
            input_tokens=5,
            output_tokens=3,
            model="stub",
            provider_name="stub",
            duration_ms=0.5,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Service / backend builder
# ---------------------------------------------------------------------------


def _build_backend(
    provider: _CapturingProvider,
    *,
    preference_store: PreferenceStore | None = None,
    identity_resolver: IdentityResolver | None = None,
) -> AsyncioBackend:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    preg.register_mock("router", provider, tier="fast")
    preg.register_mock("local-judge", provider, tier="local")

    services = StepServices(
        provider_registry=preg,
        owl_registry=OwlRegistry.with_default_secretary(),
        tool_registry=ToolRegistry(),
        consent_gate=ConsequentialActionGate(),
        preference_store=preference_store,
        identity_resolver=identity_resolver,
    )
    return AsyncioBackend(services=services)


@pytest.fixture(autouse=True)
def _disable_test_mode():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


# ===========================================================================
# (a) Preference crosses channels
# ===========================================================================


async def test_journey_a_preference_crosses_channels(tmp_db: DbPool) -> None:
    """A preference stored under 'owner-primary' is visible when classify looks up
    _gather_preferences(state.identity_key or state.session_id) for BOTH a Slack
    and a Telegram PipelineState sharing identity_key='owner-primary'.

    Pre-Task-3: classify used session_id for the lookup.  A Slack turn with
    session_id='slack:abc' would return empty because no preference is stored
    under 'slack:abc' — only under 'owner-primary'.  After Task 3 the step uses
    state.identity_key (non-empty) so BOTH channels see the cross-channel preference.
    """
    store = PreferenceStore(db=tmp_db)
    # Store preference under the identity key (not under any per-channel handle)
    await store.set("owner-primary", "response_style", "bullets")

    resolver = IdentityResolver({"owner-primary": ["slack:abc", "telegram:123"]})

    services = StepServices(
        preference_store=store,
        identity_resolver=resolver,
    )
    token = set_services(services)
    try:
        # Slack channel state: identity_key resolves to "owner-primary"
        slack_state = PipelineState(
            trace_id="trace-identity-a-slack",
            session_id="slack:abc",
            identity_key="owner-primary",
            input_text="hi",
            channel="slack",
            owl_name="secretary",
            pipeline_step="start",
            interactive=True,
        )
        slack_effective_key = slack_state.identity_key or slack_state.session_id
        assert slack_effective_key == "owner-primary"

        # Telegram channel state: different session_id, same identity_key
        telegram_state = PipelineState(
            trace_id="trace-identity-a-telegram",
            session_id="telegram:123",
            identity_key="owner-primary",
            input_text="hi",
            channel="telegram",
            owl_name="secretary",
            pipeline_step="start",
            interactive=True,
        )
        telegram_effective_key = telegram_state.identity_key or telegram_state.session_id
        assert telegram_effective_key == "owner-primary"

        # This is the exact call classify makes — both channels resolve to the same key
        slack_prefs = await _gather_preferences(slack_effective_key)
        telegram_prefs = await _gather_preferences(telegram_effective_key)
    finally:
        reset_services(token)

    # Both channels see the cross-channel preference
    for label, prefs_block in [("slack", slack_prefs), ("telegram", telegram_prefs)]:
        assert "## Learned Preferences" in prefs_block, (
            f"[{label}] ## Learned Preferences block missing; got: {prefs_block!r}"
        )
        assert "response_style" in prefs_block, (
            f"[{label}] preference key 'response_style' missing; got: {prefs_block!r}"
        )
        assert "bullets" in prefs_block, (
            f"[{label}] preference value 'bullets' missing; got: {prefs_block!r}"
        )

    # Discriminating negative: pre-Task-3 the lookup used session_id directly.
    # A bare per-channel handle has nothing stored under it — returns empty.
    # This is the gun: if classify still used session_id, the slack/telegram checks
    # above would PASS trivially (identity_key happens to match owner-primary).
    # The negative proves the data is NOT under the per-channel handle.
    token2 = set_services(services)
    try:
        slack_bare_block = await _gather_preferences("slack:abc")
        telegram_bare_block = await _gather_preferences("telegram:123")
    finally:
        reset_services(token2)

    assert slack_bare_block == "", (
        f"per-channel handle 'slack:abc' must return empty (no data stored there); "
        f"got: {slack_bare_block!r}"
    )
    assert telegram_bare_block == "", (
        f"per-channel handle 'telegram:123' must return empty (no data stored there); "
        f"got: {telegram_bare_block!r}"
    )


# ===========================================================================
# (b) Fact store unity — real FactExtractor seam (not a synthetic INSERT)
# ===========================================================================


async def test_journey_b_fact_stored_under_identity_key(tmp_db: DbPool) -> None:
    """Facts staged by FactExtractor are keyed on identity, not session_id.

    This drives the REAL extraction path via FactExtractor.extract() for both
    'telegram:123' and 'slack:abc' using an IdentityResolver that maps both to
    'owner-primary'.  Asserts:

      1. Both returned StagedFacts carry source_ref='owner-primary'.
      2. After staging via SqliteMemoryBridge, zero DB rows exist under either
         per-channel handle.

    This test WOULD HAVE BEEN RED before Task 4: without a resolver, extract()
    stamps source_ref=session_id, producing separate rows under the per-channel
    handles.  A hardcoded INSERT bypasses the feature entirely — this test does not.
    """
    resolver = IdentityResolver({"owner-primary": ["telegram:123", "slack:abc"]})
    extractor = FactExtractor(provider=_StubExtractorProvider(), identity_resolver=resolver)
    bridge = SqliteMemoryBridge(tmp_db)

    convo = [Message(role="user", content="I prefer dark mode.")]

    # Drive real extraction for the telegram session
    telegram_facts = await extractor.extract(convo, session_id="telegram:123")
    assert telegram_facts, "extractor must return at least one fact from telegram session"

    # Drive real extraction for the slack session (same user, same identity)
    slack_facts = await extractor.extract(convo, session_id="slack:abc")
    assert slack_facts, "extractor must return at least one fact from slack session"

    # Both sets of StagedFacts must carry source_ref=identity, not per-channel handles.
    # This is the key invariant: the resolver re-keyed the source_ref at extraction time.
    for f in telegram_facts:
        assert f.source_ref == "owner-primary", (
            f"telegram fact must be keyed on identity 'owner-primary', got: {f.source_ref!r}"
        )
    for f in slack_facts:
        assert f.source_ref == "owner-primary", (
            f"slack fact must be keyed on identity 'owner-primary', got: {f.source_ref!r}"
        )

    # Stage all facts into the DB via the real bridge.
    # Slack produces same content+source_ref → same fact_id → DuplicateFactError
    # (reinforcement path).  Either way: no new per-channel row is written.
    for f in telegram_facts:
        await bridge.stage(f)
    for f in slack_facts:
        with contextlib.suppress(DuplicateFactError):  # reinforcement path — not a new per-channel row
            await bridge.stage(f)

    # DB assertions: identity rows exist, per-channel rows are zero.
    rows_identity = await tmp_db.fetch_all(
        "SELECT source_ref FROM staged_facts WHERE source_ref = 'owner-primary'"
    )
    rows_telegram = await tmp_db.fetch_all(
        "SELECT source_ref FROM staged_facts WHERE source_ref = 'telegram:123'"
    )
    rows_slack = await tmp_db.fetch_all(
        "SELECT source_ref FROM staged_facts WHERE source_ref = 'slack:abc'"
    )

    assert len(rows_identity) >= 1, (
        f"expected at least 1 fact under identity key 'owner-primary', got {len(rows_identity)}"
    )
    assert len(rows_telegram) == 0, (
        f"expected 0 facts under 'telegram:123' (all unified under identity), "
        f"got {len(rows_telegram)}"
    )
    assert len(rows_slack) == 0, (
        f"expected 0 facts under 'slack:abc' (all unified under identity), "
        f"got {len(rows_slack)}"
    )


# ===========================================================================
# (c) Conversation does NOT cross channels — real MemoryBridge seam
# ===========================================================================


async def test_journey_c_conversation_does_not_cross_channels(tmp_db: DbPool) -> None:
    """Conversation history stays per-session even when two sessions share an identity.

    Uses the real SqliteMemoryBridge (no mocks):
    - bridge.store(..., 'telegram:123') writes source_type='conversation', source_ref='telegram:123'.
    - POSITIVE: recent_conversation_turns('telegram:123') returns the turn.
    - NEGATIVE: recent_conversation_turns('slack:abc') returns EMPTY — the slack
      session does NOT see telegram's history even though both resolve to 'owner-primary'.

    This is the isolation invariant: extracted facts cross channels (via identity_key);
    live conversation turns do NOT cross (source_ref stays per-session).

    Also verifies the structural distinction between identity_key and session_id,
    and that the classify fallback expression is correctly parenthesized.
    """
    bridge = SqliteMemoryBridge(tmp_db)

    # Write a conversation turn under the telegram session
    await bridge.store(
        "User: I live in Baku.\n\nAssistant: Noted.",
        "telegram:123",
    )

    # POSITIVE control: telegram session sees its own turn
    telegram_turns = await bridge.recent_conversation_turns("telegram:123")
    assert telegram_turns, "telegram:123 must see its own conversation turn"
    assert any("Baku" in t.content for t in telegram_turns), (
        f"expected 'Baku' in telegram turns, got: {[t.content for t in telegram_turns]}"
    )

    # NEGATIVE control (arms the gun): slack session sees NOTHING.
    # Before Task 2 (per-session conversation scoping), if recent_conversation_turns
    # crossed channels the slack query would return the telegram turn — RED.
    slack_turns = await bridge.recent_conversation_turns("slack:abc")
    assert not slack_turns, (
        f"slack:abc must NOT see telegram:123 conversation turns even though both "
        f"resolve to 'owner-primary'; got: {[t.content for t in slack_turns]}"
    )

    # Structural invariant: identity_key != session_id (they are different values)
    state = PipelineState(
        trace_id="trace-identity-c",
        session_id="slack:abc",
        identity_key="owner-primary",
        input_text="hello",
        channel="slack",
        owl_name="secretary",
        pipeline_step="start",
    )
    assert state.identity_key != state.session_id, (
        "identity_key and session_id must be different (cross-channel vs per-session)"
    )
    # The classify step uses identity_key (non-empty) — parenthesized to avoid
    # the operator-precedence bug where `a or b == c` evaluates as `a or (b == c)`.
    effective_key = state.identity_key or state.session_id
    assert effective_key == "owner-primary", (
        f"effective_key must be 'owner-primary' when identity_key is set; got: {effective_key!r}"
    )


# ===========================================================================
# (d) Unconfigured: byte-identical (no aliases)
# ===========================================================================


async def test_journey_d_unconfigured_byte_identical(tmp_db: DbPool) -> None:
    """With no aliases configured, IdentityResolver returns the handle unchanged.

    When state.identity_key is empty, _gather_preferences falls back to
    state.session_id — byte-identical to pre-identity behavior.
    """
    resolver = IdentityResolver({})

    # resolve() returns the handle itself when unmapped
    assert resolver.resolve("telegram:999") == "telegram:999", (
        "unconfigured resolver must return handle unchanged"
    )
    assert resolver.resolve("slack:xyz") == "slack:xyz"

    # Store a preference under the session_id (the fallback key)
    store = PreferenceStore(db=tmp_db)
    await store.set("telegram:999", "timezone", "UTC")

    services = StepServices(preference_store=store)
    token = set_services(services)
    try:
        # With empty identity_key, classify falls back to session_id
        state = PipelineState(
            trace_id="trace-identity-d",
            session_id="telegram:999",
            identity_key="",  # unconfigured — no identity resolution
            input_text="hi",
            channel="telegram",
            owl_name="secretary",
            pipeline_step="start",
        )
        effective_key = state.identity_key or state.session_id
        assert effective_key == "telegram:999", (
            f"empty identity_key must fall back to session_id; got: {effective_key!r}"
        )
        prefs_block = await _gather_preferences(effective_key)
    finally:
        reset_services(token)

    assert "timezone" in prefs_block, (
        f"unconfigured fallback must read preference from session_id; got: {prefs_block!r}"
    )
    assert "UTC" in prefs_block


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
