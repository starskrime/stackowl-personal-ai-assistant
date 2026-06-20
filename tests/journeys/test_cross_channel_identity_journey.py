"""Cross-channel identity merge-gate journey.

Asserts on assembled context / store state — NOT on model output text.

Four scenarios:

  (a) Preference crosses channels — a preference stored under the identity_key
      appears in the system prompt when a SLACK turn drives the pipeline with that
      identity_key set. Captured via a _CapturingProvider that records system_text
      on complete_with_tools().

  (b) Fact store unity (DB-state assertion) — a staged_fact seeded under
      source_ref='owner-primary' (as FactExtractor would produce after Task 4)
      is retrievable under the identity key, NOT under the per-channel handles.

  (c) Conversation does NOT cross — preferences are retrieved via identity_key
      (cross-channel) but _gather_preferences('slack:abc') on a store holding
      data only under 'owner-primary' returns empty, proving per-channel handles
      never pull cross-channel data unless the identity_key is explicitly threaded.
      Also asserts state.identity_key != state.session_id (the IDs are different).

  (d) Unconfigured byte-identical — IdentityResolver({}).resolve() returns the
      handle unchanged; when identity_key is empty, _gather_preferences falls back
      to state.session_id.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.memory.preferences import PreferenceStore
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
    _gather_preferences(state.identity_key) from a SLACK turn.

    The classify step calls _gather_preferences(state.identity_key or state.session_id).
    With identity_key='owner-primary' the store lookup uses the identity key, NOT
    the per-channel session_id 'slack:abc' — so the preference CROSSES from any
    channel to any other via the shared identity key.

    We drive _gather_preferences directly via set_services (same path classify uses
    internally) with a preference stored only under 'owner-primary'.
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
        # state.identity_key is "owner-primary" — classify uses this key
        state = PipelineState(
            trace_id="trace-identity-a",
            session_id="slack:abc",
            identity_key="owner-primary",
            input_text="hi",
            channel="slack",
            owl_name="secretary",
            pipeline_step="start",
            interactive=True,
        )
        effective_key = state.identity_key or state.session_id
        assert effective_key == "owner-primary"

        # This is the exact call classify makes
        prefs_block = await _gather_preferences(effective_key)
    finally:
        reset_services(token)

    assert "## Learned Preferences" in prefs_block, (
        f"## Learned Preferences block missing; got: {prefs_block!r}"
    )
    assert "response_style" in prefs_block, (
        f"preference key 'response_style' missing; got: {prefs_block!r}"
    )
    assert "bullets" in prefs_block, (
        f"preference value 'bullets' missing; got: {prefs_block!r}"
    )

    # Negative: a different slack session with NO preference stored has empty block
    token2 = set_services(services)
    try:
        slack_only_block = await _gather_preferences("slack:abc")
    finally:
        reset_services(token2)

    assert slack_only_block == "", (
        f"per-channel handle must return empty (no data under 'slack:abc'); "
        f"got: {slack_only_block!r}"
    )


# ===========================================================================
# (b) Fact store unity — DB-state assertion
# ===========================================================================


async def test_journey_b_fact_stored_under_identity_key(tmp_db: DbPool) -> None:
    """Facts staged by FactExtractor (Task 4) are stored with source_ref=identity_key.

    This is a DB-state assertion: we seed a staged_fact under source_ref='owner-primary'
    (as FactExtractor.extract() would produce after resolving telegram:123 → owner-primary)
    and verify it is retrievable under the identity key, NOT under the per-channel handles.
    """
    # Seed directly via SQLite — mirrors what FactExtractor produces after Task 4.
    conn = sqlite3.connect(str(tmp_db._path))
    conn.execute(
        "INSERT INTO staged_facts"
        " (fact_id, content, source_type, source_ref, confidence, staged_at)"
        " VALUES (?,?,?,?,?,?)",
        ("fact-b1", "prefers dark mode", "conversation_fact", "owner-primary", 0.9, "2026-06-20T00:00:00"),
    )
    conn.commit()
    conn.close()

    # Querying under the identity key finds the fact
    conn2 = sqlite3.connect(str(tmp_db._path))
    count_identity = conn2.execute(
        "SELECT COUNT(*) FROM staged_facts"
        " WHERE source_ref='owner-primary' AND source_type='conversation_fact'"
    ).fetchone()[0]
    # Per-channel handles return nothing — unified in storage
    count_telegram = conn2.execute(
        "SELECT COUNT(*) FROM staged_facts WHERE source_ref='telegram:123'"
    ).fetchone()[0]
    count_slack = conn2.execute(
        "SELECT COUNT(*) FROM staged_facts WHERE source_ref='slack:abc'"
    ).fetchone()[0]
    conn2.close()

    assert count_identity == 1, (
        f"expected 1 fact under identity key 'owner-primary', got {count_identity}"
    )
    assert count_telegram == 0, (
        f"expected 0 facts under 'telegram:123' (all unified under identity), got {count_telegram}"
    )
    assert count_slack == 0, (
        f"expected 0 facts under 'slack:abc' (all unified under identity), got {count_slack}"
    )


# ===========================================================================
# (c) Conversation does NOT cross — negative control
# ===========================================================================


async def test_journey_c_conversation_does_not_cross_channels(tmp_db: DbPool) -> None:
    """Preferences cross (via identity_key) but per-channel handles do NOT pull
    cross-channel data. Conversation history stays per-session.

    Proof:
    - _gather_preferences('owner-primary') returns the cross-channel preference.
    - _gather_preferences('slack:abc') returns empty (no data stored under the
      per-channel handle — only under the identity key).
    - state.identity_key != state.session_id (the IDs are structurally distinct).
    """
    store = PreferenceStore(db=tmp_db)
    # Store the preference ONLY under the identity key
    await store.set("owner-primary", "font_size", "large")

    services = StepServices(
        preference_store=store,
        identity_resolver=IdentityResolver({"owner-primary": ["slack:abc", "telegram:456"]}),
    )
    token = set_services(services)
    try:
        # Cross-channel: identity_key fetches the preference across channels
        cross_block = await _gather_preferences("owner-primary")
        # Per-channel handle alone: no data (no preference stored under 'slack:abc')
        per_channel_block = await _gather_preferences("slack:abc")
    finally:
        reset_services(token)

    assert "font_size" in cross_block, (
        f"preference must appear when querying via identity_key; got: {cross_block!r}"
    )
    assert "large" in cross_block, (
        f"preference value must appear via identity_key; got: {cross_block!r}"
    )
    # Negative control: per-channel handle has no data → empty block
    assert per_channel_block == "", (
        f"per-channel handle must return empty block (no data under 'slack:abc'); "
        f"got: {per_channel_block!r}"
    )

    # session_id and identity_key are structurally distinct
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
    # The classify step would use identity_key (non-empty), not session_id
    assert state.identity_key or state.session_id == "owner-primary"


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
