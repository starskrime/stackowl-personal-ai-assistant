"""Integration test — `/owls` is wired through the CommandRegistry.

Mirrors ``test_memory_command_registration.py`` exactly: calls the SAME
``create_and_register`` factory the orchestrator uses (after T11 lands), over a
real :class:`OwlRegistry` with builtin personas, and asserts the user OUTCOME:

  1. ``/owls list`` returns owl rows (registry not empty), and
  2. ``/owls health`` returns a health-OK response.

Also proves the guard bites: without registration the dispatch raises
``CommandNotFoundError`` (i.e. the test exercises wiring, not the class).
"""

from __future__ import annotations

import pytest

from stackowl.commands.owls_command import OwlsCommand
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from stackowl.config.settings import Settings
from stackowl.owls.registry import OwlRegistry
from tests._story_6_7_helpers import (  # noqa: F401 — fixture re-exports
    EventBus,
    db,
    make_state,
    no_test_mode_guard,
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Each test starts from a clean registry singleton."""
    CommandRegistry.reset()


async def test_owls_command_registered_list_and_health(db) -> None:  # noqa: F811 — pytest fixture injection
    """`/owls list` returns owl rows and `/owls health` reports OK — via the registry.

    Mirrors the orchestrator wiring exactly: a real OwlRegistry (with builtin
    personas), registered through ``create_and_register``.  Dispatch goes
    through the registry singleton (not the class instance), so this fails if
    the orchestrator never registers the command.
    """
    owl_registry = OwlRegistry.from_settings(Settings())
    owl_registry.register_builtin_personas()

    OwlsCommand.create_and_register(
        owl_registry=owl_registry,
        db=db,
        event_bus=EventBus(),
        tool_registry=None,
    )

    registry = CommandRegistry.instance()
    assert any(c.command == "owls" for c in registry.list()), (
        "OwlsCommand was not registered on the singleton"
    )

    # 1) list through the registry dispatch path — must return owl rows
    list_out = await registry.dispatch("owls", "list", make_state())
    assert "secretary" in list_out.lower(), (
        f"secretary owl not in /owls list output: {list_out!r}"
    )

    # 2) health through the registry — must not be an error
    health_out = await registry.dispatch("owls", "health", make_state())
    assert "✗" not in health_out, f"/owls health returned error: {health_out!r}"


async def test_owls_dispatch_fails_when_not_registered() -> None:
    """Guard: without registration the registry cannot dispatch `/owls`.

    This is what production looks like before the orchestrator wiring — proves
    the test above exercises real wiring, not the class in isolation.
    """
    registry = CommandRegistry.instance()
    assert not any(c.command == "owls" for c in registry.list())
    state = make_state()
    with pytest.raises(CommandNotFoundError):
        await registry.dispatch("owls", "list", state)
