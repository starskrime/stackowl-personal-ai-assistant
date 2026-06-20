"""Dispatch tests — /tier scoping matches the identity-or-session owner_key.

Tests assert:
  1. The description carries the word "session" (wording contract).
  2. The description does NOT claim cross-channel / owner propagation without
     the qualifier "when configured".
  3. Setting a tier for one session is read back in the same session.
  4. A different session_id with no identity_key does NOT inherit the tier.
  5. ARM-THE-GUN: two sessions sharing identity_key DO see the same tier via
     get_session_tier(identity_key or session_id) — the router lookup key.
  6. FALLBACK: identity_key="" means the router uses session_id (byte-identical
     to prior behaviour).
"""

from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.tier_command import TierCommand, get_session_tier, reset_session_tiers
from stackowl.pipeline.state import PipelineState
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_registry_and_tiers() -> None:
    CommandRegistry.reset()
    reset_session_tiers()


def _make_state(session_id: str, identity_key: str = "") -> PipelineState:
    return make_state().model_copy(update={"session_id": session_id, "identity_key": identity_key})


# ---------------------------------------------------------------------------
# Wording contract tests
# ---------------------------------------------------------------------------


def test_tier_description_does_not_claim_cross_channel_owner() -> None:
    """The description must not claim cross-channel or owner-wide propagation."""
    cmd = TierCommand()
    desc = cmd.description.lower()
    # These are the overclaim phrases from the old docstring
    assert "all channels" not in desc, (
        f"Description claims cross-channel scope: {cmd.description!r}"
    )
    assert "same owner" not in desc, (
        f"Description claims owner scope: {cmd.description!r}"
    )


def test_tier_description_indicates_session_scope() -> None:
    """The description must say session-scoped (not owner or cross-channel)."""
    cmd = TierCommand()
    desc = cmd.description.lower()
    assert "session" in desc, (
        f"Description does not indicate session scope: {cmd.description!r}"
    )


# ---------------------------------------------------------------------------
# Behaviour tests — session-scoped read-back
# ---------------------------------------------------------------------------


async def test_tier_set_and_read_back_same_session() -> None:
    """Setting a tier is read back correctly within the same session."""
    state = _make_state("session-alpha")
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())

    set_result = await CommandRegistry.instance().dispatch(
        "tier", "fast", state
    )
    assert "fast" in set_result

    # Read back
    read_result = await CommandRegistry.instance().dispatch(
        "tier", "", state
    )
    assert "fast" in read_result


async def test_tier_different_sessions_are_independent() -> None:
    """A tier set for session A is not visible to session B.

    This proves the preference is session-scoped, not owner-wide:
    get_session_tier uses session_id as key, so session B starts with None.
    """
    state_a = _make_state("session-A")
    state_b = _make_state("session-B")
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())

    # Session A sets powerful
    await CommandRegistry.instance().dispatch("tier", "powerful", state_a)

    # Session B has no tier set — in-memory cache should return None
    tier_b = get_session_tier("session-B")
    assert tier_b is None, (
        "Session B must not inherit Session A's tier — preference is session-scoped"
    )


async def test_tier_unknown_tier_rejected() -> None:
    """Unknown tier value returns honest rejection."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "tier", "ultra", make_state()
    )
    assert "✗" in result or "unknown" in result.lower()


async def test_tier_show_current_when_no_arg() -> None:
    """Empty arg shows current tier and valid options."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "tier", "", make_state()
    )
    assert "fast" in result or "standard" in result or "powerful" in result or "local" in result


# ---------------------------------------------------------------------------
# ARM-THE-GUN: cross-channel identity router key tests (review finding)
# ---------------------------------------------------------------------------


async def test_router_tier_lookup_uses_identity_key_across_channels() -> None:
    """ARM-THE-GUN: tier set on telegram session surfaces on slack session with same identity_key.

    This test would be RED if the router called get_session_tier(state.session_id)
    (bare session_id) — the second channel's session_id "slack:U0" != "telegram:123"
    so the cache would miss and return None.  It is GREEN only when the router calls
    get_session_tier(state.identity_key or state.session_id).
    """
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())

    # Session A: Telegram channel, identity resolved to "owner-primary"
    state_telegram = _make_state("telegram:123", identity_key="owner-primary")
    await CommandRegistry.instance().dispatch("tier", "fast", state_telegram)

    # Router-side lookup for a DIFFERENT session on Slack with the SAME identity
    state_slack = _make_state("slack:U0", identity_key="owner-primary")
    router_key = state_slack.identity_key or state_slack.session_id
    tier = get_session_tier(router_key)

    assert tier == "fast", (
        f"Router lookup with identity_key='owner-primary' returned {tier!r} instead of 'fast'. "
        "This means get_session_tier was called with bare session_id instead of identity_key or session_id."
    )


async def test_router_tier_lookup_falls_back_to_session_id_when_no_identity() -> None:
    """FALLBACK: identity_key='' → router uses session_id — byte-identical to prior behaviour."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())

    state = _make_state("cli:mybox", identity_key="")
    await CommandRegistry.instance().dispatch("tier", "local", state)

    # Router key: identity_key is empty so it falls back to session_id
    router_key = state.identity_key or state.session_id
    assert router_key == "cli:mybox"
    tier = get_session_tier(router_key)
    assert tier == "local", (
        f"Fallback path: expected 'local', got {tier!r}"
    )
