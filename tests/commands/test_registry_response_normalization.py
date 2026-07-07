from __future__ import annotations

import pytest
from tests._story_6_7_helpers import make_state

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.response import Action, CommandResponse


class _PlainStringCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "plainstr"

    @property
    def description(self) -> str:
        return "returns a bare str"

    async def handle(self, args: str, state) -> str:
        return "hello world"


class _ButtonCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "withbuttons"

    @property
    def description(self) -> str:
        return "returns a CommandResponse"

    async def handle(self, args: str, state) -> CommandResponse:
        return CommandResponse(
            text="pick one",
            actions=(Action(label="Go", command="/plainstr"),),
        )


@pytest.fixture(autouse=True)
def _reset_registry():
    CommandRegistry.reset()


async def test_dispatch_normalizes_bare_str_to_command_response():
    registry = CommandRegistry.instance()
    registry.register(_PlainStringCommand())

    result = await registry.dispatch("plainstr", "", make_state())

    assert isinstance(result, CommandResponse)
    assert result.text == "hello world"
    assert result.actions == ()


async def test_dispatch_passes_through_command_response_untouched():
    registry = CommandRegistry.instance()
    registry.register(_ButtonCommand())

    result = await registry.dispatch("withbuttons", "", make_state())

    assert isinstance(result, CommandResponse)
    assert result.text == "pick one"
    assert len(result.actions) == 1
    assert result.actions[0].command == "/plainstr"
