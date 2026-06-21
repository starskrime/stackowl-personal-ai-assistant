"""/settings sub-command metadata — mirrors test_audit_meta.py.

Asserts the declared metadata matches the real dispatch ladder (a single
``autonomy`` sub with a choice arg) and that an empty/unknown sub-command
surfaces the auto-generated usage block.
"""

from __future__ import annotations

import pytest

from stackowl.commands.metadata import render_usage
from stackowl.commands.settings_command import SettingsCommand

_EXPECTED = {"autonomy"}


def _state():  # type: ignore[no-untyped-def]
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


def test_settings_declares_all_subcommands() -> None:
    cmd = SettingsCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == _EXPECTED


def test_settings_grammar_is_verb() -> None:
    assert SettingsCommand().meta.grammar == "verb"
    assert SettingsCommand().meta.group == "Configuration"


def test_autonomy_declares_level_choices() -> None:
    cmd = SettingsCommand()
    autonomy = cmd.meta.subcommands[0]
    assert autonomy.name == "autonomy"
    level = next(a for a in autonomy.args if a.name == "level")
    assert level.choices == ("low", "medium", "high")


def test_every_settings_subcommand_has_nonempty_summary() -> None:
    cmd = SettingsCommand()
    for sub in cmd.meta.subcommands:
        assert sub.summary.strip(), f"/settings {sub.name} has a blank summary"


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage() -> None:
    """`/settings bogus` shows the auto-generated usage."""
    cmd = SettingsCommand()
    out = await cmd.handle("bogus whatever", _state())
    assert out == render_usage("settings", cmd.meta)
    assert "autonomy" in out


@pytest.mark.asyncio
async def test_empty_args_returns_usage() -> None:
    cmd = SettingsCommand()
    out = await cmd.handle("", _state())
    assert out == render_usage("settings", cmd.meta)
