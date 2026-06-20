"""Cross-channel identity: preferences scoped on identity_key, not session_id.

TDD tests (failing-first) for Task 3 of the cross-channel identity feature.

Invariants under test:
  1. READ cross-channel: a pref set under identity "owner-primary" surfaces for
     BOTH a telegram session AND a slack session when both resolve to that identity.
  2. CONTROL: a different identity does NOT see owner-primary's pref.
  3. FALLBACK byte-identical: identity_key="" falls back to session_id, preserving
     today's behavior for unconfigured channels.
  4. TIER set+get cross-channel: a tier written via one session_id+identity is
     readable via a DIFFERENT session_id sharing the same identity.
"""

from __future__ import annotations

import pytest

from stackowl.commands.tier_command import (
    TierCommand,
    _owner_key_for_state,
    _read_tier,
    _write_tier,
    reset_session_tiers,
)
from stackowl.db.pool import DbPool
from stackowl.memory.preferences import PreferenceStore
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import classify

pytestmark = pytest.mark.asyncio


def _make_state(
    session_id: str,
    identity_key: str = "",
    channel: str = "telegram",
) -> PipelineState:
    return PipelineState(
        trace_id=f"trace-{session_id}",
        session_id=session_id,
        identity_key=identity_key,
        input_text="hi",
        channel=channel,
        owl_name="secretary",
        pipeline_step="start",
    )


# ---------------------------------------------------------------------------
# Test 1: cross-channel pref read (the core invariant — was RED before Task 3)
# ---------------------------------------------------------------------------


async def test_cross_channel_pref_telegram_and_slack_see_same_identity(
    tmp_db: DbPool,
) -> None:
    """Pref stored under identity 'owner-primary' surfaces for both telegram and slack sessions."""
    store = PreferenceStore(db=tmp_db)
    # Write the pref directly keyed on the shared identity
    await store.set("owner-primary", "response_style", "bullets")

    bridge = SqliteMemoryBridge(db=tmp_db)
    token = set_services(StepServices(memory_bridge=bridge, preference_store=store))
    try:
        # Telegram session resolving to owner-primary
        tg_state = _make_state(
            session_id="telegram:123", identity_key="owner-primary", channel="telegram"
        )
        tg_out = await classify.run(tg_state)

        # Slack session resolving to the same owner-primary
        sl_state = _make_state(
            session_id="slack:U0", identity_key="owner-primary", channel="slack"
        )
        sl_out = await classify.run(sl_state)
    finally:
        reset_services(token)

    for label, out in [("telegram", tg_out), ("slack", sl_out)]:
        ctx = out.memory_context or ""
        assert "## Learned Preferences" in ctx, (
            f"{label}: expected Learned Preferences block, got: {ctx[:200]}"
        )
        assert "response_style: bullets" in ctx, (
            f"{label}: expected response_style pref, got: {ctx[:200]}"
        )


# ---------------------------------------------------------------------------
# Test 2: CONTROL — different identities are isolated
# ---------------------------------------------------------------------------


async def test_different_identities_are_isolated(tmp_db: DbPool) -> None:
    """A session with a different identity must NOT see owner-primary's pref."""
    store = PreferenceStore(db=tmp_db)
    await store.set("owner-primary", "response_style", "bullets")
    # owner-other has no prefs
    bridge = SqliteMemoryBridge(db=tmp_db)
    token = set_services(StepServices(memory_bridge=bridge, preference_store=store))
    try:
        other_state = _make_state(
            session_id="telegram:999", identity_key="owner-other", channel="telegram"
        )
        out = await classify.run(other_state)
    finally:
        reset_services(token)

    ctx = out.memory_context or ""
    assert "response_style" not in ctx, (
        f"owner-other must not see owner-primary's pref, got: {ctx[:200]}"
    )


# ---------------------------------------------------------------------------
# Test 3: FALLBACK byte-identical — identity_key="" uses session_id
# ---------------------------------------------------------------------------


async def test_fallback_to_session_id_when_identity_key_empty(tmp_db: DbPool) -> None:
    """When identity_key is empty, owner_key falls back to session_id (today's behavior)."""
    store = PreferenceStore(db=tmp_db)
    # Pref written under the session_id directly (today's format)
    await store.set("telegram:9", "language", "Spanish")

    bridge = SqliteMemoryBridge(db=tmp_db)
    token = set_services(StepServices(memory_bridge=bridge, preference_store=store))
    try:
        state = _make_state(
            session_id="telegram:9", identity_key="", channel="telegram"
        )
        out = await classify.run(state)
    finally:
        reset_services(token)

    ctx = out.memory_context or ""
    assert "language: Spanish" in ctx, (
        f"Fallback to session_id must surface session-keyed pref, got: {ctx[:200]}"
    )


# ---------------------------------------------------------------------------
# Test 4: TIER cross-channel — _owner_key_for_state follows identity
# ---------------------------------------------------------------------------


def test_owner_key_for_state_uses_identity_when_set() -> None:
    """_owner_key_for_state returns identity_key when non-empty."""
    state = _make_state(session_id="telegram:123", identity_key="owner-primary")
    key = _owner_key_for_state(state)
    assert key == "owner-primary", (
        f"Expected identity_key 'owner-primary', got: {key!r}"
    )


def test_owner_key_for_state_falls_back_to_session_id() -> None:
    """_owner_key_for_state returns session_id when identity_key is empty."""
    state = _make_state(session_id="telegram:123", identity_key="")
    key = _owner_key_for_state(state)
    assert key == "telegram:123", (
        f"Expected session_id fallback 'telegram:123', got: {key!r}"
    )


async def test_tier_cross_channel_same_identity_shares_tier(tmp_db: DbPool) -> None:
    """A tier written via telegram session is read back via slack session sharing the same identity."""
    reset_session_tiers()
    store = PreferenceStore(db=tmp_db)

    tg_state = _make_state(
        session_id="telegram:123", identity_key="owner-primary", channel="telegram"
    )
    sl_state = _make_state(
        session_id="slack:U0", identity_key="owner-primary", channel="slack"
    )

    # Write via telegram
    tg_key = _owner_key_for_state(tg_state)
    await _write_tier(store, tg_key, "powerful")

    # Read back via slack — different session_id, same identity_key
    sl_key = _owner_key_for_state(sl_state)
    tier = await _read_tier(store, sl_key)

    assert tier == "powerful", (
        f"Slack session must see tier set by telegram session (same identity), got: {tier!r}"
    )


async def test_tier_different_identity_does_not_inherit(tmp_db: DbPool) -> None:
    """A tier set for owner-primary is not visible to a state with owner-other identity."""
    reset_session_tiers()
    store = PreferenceStore(db=tmp_db)

    tg_state = _make_state(
        session_id="telegram:123", identity_key="owner-primary", channel="telegram"
    )
    other_state = _make_state(
        session_id="telegram:123", identity_key="owner-other", channel="telegram"
    )

    # Write via owner-primary
    tg_key = _owner_key_for_state(tg_state)
    await _write_tier(store, tg_key, "fast")

    # Read via owner-other — same session_id but DIFFERENT identity_key
    other_key = _owner_key_for_state(other_state)
    tier = await _read_tier(store, other_key)

    assert tier is None, (
        f"owner-other must not inherit owner-primary's tier, got: {tier!r}"
    )
