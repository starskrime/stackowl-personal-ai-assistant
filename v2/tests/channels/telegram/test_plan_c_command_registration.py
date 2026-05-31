import pytest
from stackowl.channels.telegram.commands_registration import build_bot_commands, register_commands


class _Cmd:
    def __init__(self, command, description):
        self.command = command
        self.description = description


def test_build_bot_commands_sanitizes_names():
    cmds = [_Cmd("Help", "Show help"), _Cmd("co$t", "Show cost"),
            _Cmd("a_very_long_command_name_exceeding_thirty_two", "x")]
    out = build_bot_commands(cmds)
    names = [c.command for c in out]
    assert "help" in names
    assert all(all(ch.isalnum() or ch == "_" for ch in n) for n in names)
    assert all(1 <= len(n) <= 32 for n in names)
    assert all(c.description for c in out)


def test_build_bot_commands_truncates_description():
    out = build_bot_commands([_Cmd("ok", "d" * 300)])
    assert len(out[0].description) <= 256


@pytest.mark.asyncio
async def test_register_commands_calls_set_my_commands():
    class _Bot:
        def __init__(self): self.pushed = None
        async def set_my_commands(self, cmds): self.pushed = cmds
    bot = _Bot()
    await register_commands(bot, [_Cmd("help", "Show help")])
    assert bot.pushed and bot.pushed[0].command == "help"


def test_build_bot_commands_dedupes_colliding_names():
    cmds = [_Cmd("help", "first"), _Cmd("Help", "second-collides"), _Cmd("cost", "ok")]
    out = build_bot_commands(cmds)
    names = [c.command for c in out]
    assert names.count("help") == 1
    assert "cost" in names
    # first occurrence wins
    assert next(c for c in out if c.command == "help").description == "first"
