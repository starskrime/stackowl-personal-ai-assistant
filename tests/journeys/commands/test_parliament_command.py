"""Dispatch test — /parliament is wired through CommandRegistry."""
from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401

_NO_STORE = "Session store not configured."
_SUGGESTIONS_RESET = "Parliament suggestion mode re-enabled."


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_parliament_no_args_returns_usage() -> None:
    """/parliament with no args returns the usage block."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = (await CommandRegistry.instance().dispatch("parliament", "", make_state())).text
    assert "Usage:" in result


async def test_parliament_log_no_store() -> None:
    """/parliament log with no session store returns the not-configured message."""
    deps = CommandDeps(parliament_session_store=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = (await CommandRegistry.instance().dispatch("parliament", "log", make_state())).text
    assert _NO_STORE in result


async def test_parliament_unsuppress_returns_reset() -> None:
    """/parliament unsuppress always returns the suggestions-reset message."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = (
        await CommandRegistry.instance().dispatch("parliament", "unsuppress", make_state())
    ).text
    assert _SUGGESTIONS_RESET in result


async def test_parliament_not_found_when_not_registered() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("parliament", "", make_state())
