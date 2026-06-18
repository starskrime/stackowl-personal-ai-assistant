"""Integration test — `/memory` is wired through the CommandRegistry.

Reproduces the production wiring bug: `MemoryCommand.create_and_register` was
defined but never called by the startup orchestrator, so `/memory remember X`
silently did nothing. This drives the command through the SAME registration
path the orchestrator now uses (``create_and_register`` on the real
:class:`CommandRegistry` singleton, over a real :class:`SqliteMemoryBridge` +
real :class:`FactPromoter` on a tmp DB), and asserts the user OUTCOME:

  1. ``/memory remember <text>`` persists a committed fact, and
  2. ``/memory search <text>`` recalls it.

It also proves the guard bites: without registration the dispatch raises
``CommandNotFoundError`` (i.e. the test exercises wiring, not the class).
"""

from __future__ import annotations

import pytest

from stackowl.commands.memory_command import MemoryCommand
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from stackowl.db.pool import DbPool
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from tests._story_6_7_helpers import (  # noqa: F401 — fixture re-exports
    EventBus,
    db,
    make_settings,
    make_state,
    no_test_mode_guard,
)

_MARKER = "the great wall of china is visible from low orbit"


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Each test starts from a clean registry singleton."""
    CommandRegistry.reset()


async def test_memory_command_registered_remember_then_search(db: DbPool) -> None:  # noqa: F811 — pytest fixture injection
    """`/memory remember` persists and `/memory search` recalls — via the registry.

    Mirrors the orchestrator wiring exactly: a real bridge + promoter over the
    tmp DB, registered through ``create_and_register``. Dispatch goes through the
    registry singleton (not the class instance), so this fails if the orchestrator
    never registers the command.
    """
    # Real stores — no AI provider involved (FTS5 recall, no embeddings needed).
    bridge = SqliteMemoryBridge(db=db, embedding_registry=None, lancedb=None)
    promoter = FactPromoter(db=db)

    MemoryCommand.create_and_register(
        bridge=bridge,
        settings=make_settings(),
        db=db,
        event_bus=EventBus(),
        lancedb=None,
        promoter=promoter,
        embedding_registry=None,
    )

    registry = CommandRegistry.instance()
    assert any(c.command == "memory" for c in registry.list()), (
        "MemoryCommand was not registered on the singleton"
    )

    # 1) remember through the registry dispatch path
    remember_out = await registry.dispatch("memory", f"remember {_MARKER}", make_state())
    assert remember_out.startswith("✓ Remembered"), remember_out

    # 2) verify persistence via an INDEPENDENT bridge over the same DB
    independent = SqliteMemoryBridge(db=db, embedding_registry=None, lancedb=None)
    committed = await independent.list_staged(status="committed")
    assert any(_MARKER in f.content for f in committed), (
        f"remembered fact not found in committed_facts: {[f.content for f in committed]}"
    )

    # 3) search through the registry recalls it
    search_out = await registry.dispatch("memory", "search great wall", make_state())
    assert _MARKER in search_out, search_out


async def test_memory_dispatch_fails_when_not_registered(db: DbPool) -> None:  # noqa: F811 — pytest fixture injection
    """Guard: without registration the registry cannot dispatch `/memory`.

    This is what production looked like before the orchestrator fix — proves the
    test above exercises real wiring, not the class in isolation.
    """
    registry = CommandRegistry.instance()
    assert not any(c.command == "memory" for c in registry.list())
    with pytest.raises(CommandNotFoundError):
        await registry.dispatch("memory", f"remember {_MARKER}", make_state())
