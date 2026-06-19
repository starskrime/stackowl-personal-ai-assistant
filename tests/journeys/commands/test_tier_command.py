"""Dispatch tests — /tier docstring matches owner scoping.

The original module docstring claimed the preference "propagates across all
channels for the same owner" but _owner_key_for_state returns state.session_id
— the preference is session-scoped, not cross-channel.

The fix corrects the module docstring and description to say session-scoped.
Tests assert:
  1. The description does NOT claim cross-channel / owner propagation.
  2. Setting a tier for one session is read back in the same session.
  3. A different session_id does NOT inherit the first session's tier.
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


def _make_state(session_id: str) -> PipelineState:
    return make_state().model_copy(update={"session_id": session_id})


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
