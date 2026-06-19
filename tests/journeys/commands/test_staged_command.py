"""Dispatch tests — /staged command (Epic B, Commit 1).

Drives CommandRegistry.dispatch() through register_all_commands() with a
FakeBridge/FakePromoter.  Key assertions:
  1. Real deletion on valid reject
  2. Honest not-found on bogus reject (NOT false success)
"""

from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from tests._story_6_7_helpers import (
    FakeBridge,
    FakePromoter,
    make_staged,
    make_state,
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


@pytest.fixture()
def bridge() -> FakeBridge:
    return FakeBridge()


@pytest.fixture()
def promoter() -> FakePromoter:
    return FakePromoter(success=True)


@pytest.fixture()
def reg(bridge: FakeBridge, promoter: FakePromoter) -> CommandRegistry:
    deps = CommandDeps(bridge=bridge, promoter=promoter)
    return register_all_commands(deps, registry=CommandRegistry.instance())


async def test_staged_reject_real_fact_deletes_and_confirms(
    reg: CommandRegistry, bridge: FakeBridge
) -> None:
    """dispatch 'staged reject <id> YES' with a real fact → real deletion + success message."""
    fact = make_staged(fact_id="aabbccdd-0000-0000-0000-000000000001", content="delete me")
    bridge.seed("staged", fact)

    state = make_state()
    result = await reg.dispatch("staged", f"reject {fact.fact_id} YES", state)

    assert "✓" in result
    assert "Rejected" in result
    # Side-effect: fact is gone from the bucket
    assert fact not in bridge._by_status["staged"]
    assert fact.fact_id in bridge.delete_calls


async def test_staged_reject_bogus_id_returns_not_found(
    reg: CommandRegistry, bridge: FakeBridge
) -> None:
    """dispatch 'staged reject bogus-id YES' → honest not-found, NOT '✓ Rejected'."""
    state = make_state()
    result = await reg.dispatch("staged", "reject bogus-id-does-not-exist YES", state)

    # Must NOT claim success
    assert "✓" not in result
    assert "Rejected" not in result
    # Must give honest signal
    assert "not found" in result.lower() or "✗" in result
    # Must NOT have called delete
    assert "bogus-id-does-not-exist" not in bridge.delete_calls


async def test_staged_list_returns_table(
    reg: CommandRegistry, bridge: FakeBridge
) -> None:
    """dispatch 'staged list' → table with the seeded fact."""
    fact = make_staged(content="important knowledge")
    bridge.seed("staged", fact)

    state = make_state()
    result = await reg.dispatch("staged", "list", state)

    assert "important knowledge" in result or fact.fact_id[:8] in result


async def test_staged_not_configured_when_bridge_none() -> None:
    """dispatch 'staged list' with no bridge → honest not-configured message."""
    CommandRegistry.reset()
    deps = CommandDeps(bridge=None, promoter=None)
    reg = register_all_commands(deps, registry=CommandRegistry.instance())
    state = make_state()

    result = await reg.dispatch("staged", "list", state)

    # Must not crash; must surface an honest not-configured message
    assert "not configured" in result.lower() or "✗" in result
