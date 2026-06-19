"""Dispatch test — /brief is wired through CommandRegistry."""
from __future__ import annotations
import pytest
from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401


class _FakeJobResult:
    success = True
    output = "Good morning"
    error = None
    metadata = {"section_count": 2}


class _FakeHandler:
    async def execute(self, job: object) -> _FakeJobResult:
        return _FakeJobResult()


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_brief_delivers_output() -> None:
    deps = CommandDeps(morning_brief_handler=_FakeHandler())
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("brief", "", make_state())
    assert result == "Good morning"


async def test_brief_not_configured_when_handler_none() -> None:
    deps = CommandDeps(morning_brief_handler=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("brief", "", make_state())
    assert "not configured" in result


async def test_brief_not_found_when_not_registered() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("brief", "", make_state())
