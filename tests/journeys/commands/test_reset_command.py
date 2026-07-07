"""Dispatch test — /reset actually clears session conversation history (FR214).

Drives CommandRegistry.dispatch() through register_all_commands() with a real
SqliteMemoryBridge on a temp DB to assert the side-effect (rows deleted), not
just the return string.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.models import StagedFact
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from tests._story_6_7_helpers import make_state  # noqa: F401


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def memory_db(tmp_path: Path) -> AsyncGenerator[DbPool, None]:
    """DbPool with all migrations applied."""
    db_path = tmp_path / "reset_test.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture()
def bridge(memory_db: DbPool) -> SqliteMemoryBridge:
    return SqliteMemoryBridge(memory_db)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def _stage_conversation_turn(bridge: SqliteMemoryBridge, session_id: str, content: str) -> None:
    """Helper — stage one conversation turn for a given session."""
    fact = StagedFact(
        content=content,
        source_type="conversation",
        source_ref=session_id,
        confidence=0.9,
    )
    await bridge.stage(fact)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_reset_clears_turns_and_reports_count(bridge: SqliteMemoryBridge) -> None:
    """dispatch('reset') deletes conversation turns and reports the real count."""
    session_id = "sess-reset-test"

    # Stage 2 conversation turns for our session
    await _stage_conversation_turn(bridge, session_id, "Turn one")
    await _stage_conversation_turn(bridge, session_id, "Turn two")

    # Confirm turns exist before reset
    turns_before = await bridge.recent_conversation_turns(session_id, limit=10)
    assert len(turns_before) == 2

    deps = CommandDeps(bridge=bridge)
    register_all_commands(deps, registry=CommandRegistry.instance())
    state = make_state()
    state = state.model_copy(update={"session_id": session_id})

    result = (await CommandRegistry.instance().dispatch("reset", "", state)).text

    # Message reflects real count
    assert "2" in result
    assert "turn" in result.lower()

    # Side-effect: turns are actually gone
    turns_after = await bridge.recent_conversation_turns(session_id, limit=10)
    assert turns_after == []


async def test_reset_scoped_to_session(bridge: SqliteMemoryBridge) -> None:
    """dispatch('reset') does NOT delete turns belonging to a different session."""
    session_a = "sess-alpha"
    session_b = "sess-beta"

    await _stage_conversation_turn(bridge, session_a, "Alpha turn")
    await _stage_conversation_turn(bridge, session_b, "Beta turn 1")
    await _stage_conversation_turn(bridge, session_b, "Beta turn 2")

    deps = CommandDeps(bridge=bridge)
    register_all_commands(deps, registry=CommandRegistry.instance())
    state = make_state()
    state = state.model_copy(update={"session_id": session_a})

    await CommandRegistry.instance().dispatch("reset", "", state)

    # session_a turns gone
    assert await bridge.recent_conversation_turns(session_a, limit=10) == []
    # session_b turns intact
    beta_turns = await bridge.recent_conversation_turns(session_b, limit=10)
    assert len(beta_turns) == 2


async def test_reset_empty_session_returns_nothing_to_clear(bridge: SqliteMemoryBridge) -> None:
    """dispatch('reset') on an empty session returns an honest 'nothing to clear' message."""
    deps = CommandDeps(bridge=bridge)
    register_all_commands(deps, registry=CommandRegistry.instance())
    state = make_state()
    state = state.model_copy(update={"session_id": "sess-empty"})

    result = (await CommandRegistry.instance().dispatch("reset", "", state)).text

    assert "Nothing to clear" in result or "0" in result


async def test_reset_not_configured_when_bridge_none() -> None:
    """dispatch('reset') with no bridge returns an honest not-configured message."""
    deps = CommandDeps(bridge=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    state = make_state()

    result = (await CommandRegistry.instance().dispatch("reset", "", state)).text

    assert "not configured" in result
    assert "✗" in result
