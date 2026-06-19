"""Dispatch tests — /memory delete resolves prefixes like /memory forget.

The original _delete() passed the raw arg directly to forget_fact without
prefix-resolution, while _forget() used find_staged_by_id.  This meant:
  - /memory delete <prefix> silently called bridge.delete(<prefix>) which
    would do nothing (or delete a wrong fact), yet could still echo success.
  - /memory delete bogus YES returned "✓ Deleted bogus" — false success.

The fix: resolve the id via find_staged_by_id before acting, and only report
success when a real fact was found and deleted.  Bogus prefixes get an honest
not-found response.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from tests._story_6_7_helpers import (
    FakeBridge,
    make_settings,
    make_staged,
    make_state,
    no_test_mode_guard,  # noqa: F401
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


@pytest.fixture()
async def db(tmp_path: Path) -> DbPool:
    db_path = tmp_path / "mem_del_test.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    yield pool
    await pool.close()


def _make_deps(bridge: FakeBridge, db: DbPool) -> CommandDeps:
    return CommandDeps(
        bridge=bridge,
        settings=make_settings(),
        db=db,
        event_bus=EventBus(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_memory_delete_prefix_resolves_and_deletes(db: DbPool) -> None:
    """Stage a fact, dispatch 'memory delete <prefix> YES' → real deletion."""
    bridge = FakeBridge()
    fact = make_staged(fact_id="aabbccdd-0000-0000-0000-000000000001", content="alpha bravo")
    bridge.seed("staged", fact)

    deps = _make_deps(bridge, db)
    register_all_commands(deps, registry=CommandRegistry.instance())

    # Use an 8-char prefix
    result = await CommandRegistry.instance().dispatch(
        "memory", "delete aabbccdd YES", make_state()
    )

    assert "✓" in result
    assert "Deleted" in result
    # The full fact_id must be echoed (not just the prefix)
    assert fact.fact_id in result
    # Bridge.delete must have been called with the full ID
    assert fact.fact_id in bridge.delete_calls


async def test_memory_delete_bogus_prefix_returns_not_found(db: DbPool) -> None:
    """Bogus prefix returns honest not-found — no false '✓ Deleted'."""
    bridge = FakeBridge()  # empty — no facts staged

    deps = _make_deps(bridge, db)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "memory", "delete deadbeef YES", make_state()
    )

    # Must NOT claim success
    assert "✓" not in result
    assert "Deleted" not in result
    # Must be honest about the miss
    assert "no fact" in result.lower() or "not found" in result.lower() or "✗" in result
    # Bridge.delete must NOT have been called
    assert bridge.delete_calls == []


async def test_memory_delete_without_yes_shows_confirmation(db: DbPool) -> None:
    """Without YES confirmation, /memory delete shows the confirmation prompt."""
    bridge = FakeBridge()
    fact = make_staged(fact_id="ccddee00-0000-0000-0000-000000000002", content="confirm me please")
    bridge.seed("staged", fact)

    deps = _make_deps(bridge, db)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "memory", "delete ccddee00", make_state()
    )

    # No deletion yet
    assert bridge.delete_calls == []
    # Prompt should contain the full fact_id for the follow-up command
    assert fact.fact_id in result or "YES" in result


async def test_memory_delete_parity_with_forget(db: DbPool) -> None:
    """Both 'memory delete' and 'memory forget' accept a prefix and delete the same fact."""
    bridge_del = FakeBridge()
    bridge_fgt = FakeBridge()

    fact_del = make_staged(fact_id="ff001122-0000-0000-0000-000000000003", content="delete me")
    fact_fgt = make_staged(fact_id="ff001122-0000-0000-0000-000000000003", content="delete me")
    bridge_del.seed("staged", fact_del)
    bridge_fgt.seed("staged", fact_fgt)

    # Test /memory delete
    deps_del = _make_deps(bridge_del, db)
    register_all_commands(deps_del, registry=CommandRegistry.instance())
    result_del = await CommandRegistry.instance().dispatch(
        "memory", "delete ff001122 YES", make_state()
    )
    CommandRegistry.reset()

    # Test /memory forget
    deps_fgt = _make_deps(bridge_fgt, db)
    register_all_commands(deps_fgt, registry=CommandRegistry.instance())
    result_fgt = await CommandRegistry.instance().dispatch(
        "memory", "forget ff001122 YES", make_state()
    )

    # Both succeed and both call bridge.delete with the full fact_id
    assert "✓" in result_del
    assert "✓" in result_fgt
    assert fact_del.fact_id in bridge_del.delete_calls
    assert fact_fgt.fact_id in bridge_fgt.delete_calls
