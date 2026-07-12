"""Layer 2 — state-level coverage of the command/sub dropdown decision.

Drives :func:`command_dropdown_items` (the single decision point the widget
calls) with raw buffer strings and asserts the resolved level + the candidate
names/labels.  No Textual app needed — this is the seam between the pure parser
and the widget.
"""

from __future__ import annotations

import pytest

from stackowl.commands.metadata import Arg, CommandMeta, SubCommand
from stackowl.tui.widgets.compose_helpers import (
    CommandInfo,
    CompletionLevel,
    command_dropdown_items,
)

pytestmark = pytest.mark.tui

_MEMORY_META = CommandMeta(
    grammar="verb",
    subcommands=(
        SubCommand("stats", "Show memory stats"),
        SubCommand("search", "Search facts", args=(Arg("query"),)),
        SubCommand("export", "Export memory"),
    ),
)
_BROWSER_META = CommandMeta(
    grammar="verb",
    subcommands=(
        SubCommand(
            "profile",
            "Manage browser profiles",
            children=(SubCommand("list", "List profiles"),),
        ),
    ),
)
_QUIET_META = CommandMeta(grammar="flag", args=(Arg("minutes", required=False),))


def _infos() -> list[CommandInfo]:
    return [
        CommandInfo("memory", "Memory management", meta=_MEMORY_META),
        CommandInfo("browser", "Browser control", meta=_BROWSER_META),
        CommandInfo("quiet", "Mute notifications", meta=_QUIET_META),
    ]


def test_command_mode_returns_command_rows_with_description() -> None:
    level, items = command_dropdown_items("/me", _infos())
    assert level is CompletionLevel.COMMAND
    assert items == (("memory", "Memory management"),)


def test_sub_mode_returns_subcommand_rows_with_summary() -> None:
    level, items = command_dropdown_items("/memory ", _infos())
    assert level is CompletionLevel.SUB
    names = [n for n, _ in items]
    assert names == ["stats", "search", "export"]
    labels = {n: lbl for n, lbl in items}
    assert labels["stats"] == "Show memory stats"


def test_sub_mode_partial_filters() -> None:
    level, items = command_dropdown_items("/memory st", _infos())
    assert level is CompletionLevel.SUB
    assert [n for n, _ in items] == ["stats"]


def test_sub_with_children_gets_marker() -> None:
    level, items = command_dropdown_items("/browser ", _infos())
    assert level is CompletionLevel.SUB
    labels = {n: lbl for n, lbl in items}
    assert labels["profile"] == "Manage browser profiles ›"


def test_two_level_descent() -> None:
    level, items = command_dropdown_items("/browser profile ", _infos())
    assert level is CompletionLevel.SUB
    assert [n for n, _ in items] == ["list"]


def test_flag_grammar_yields_arg_hint_not_subcommands() -> None:
    # No selectable sub-command rows — but /quiet's free-text `minutes` arg now
    # surfaces as one non-selectable tip row (ARG_HINT), not silence.
    level, items = command_dropdown_items("/quiet ", _infos())
    assert level is CompletionLevel.ARG_HINT
    assert items == (("[minutes]", None),)


def test_past_args_yields_none() -> None:
    level, items = command_dropdown_items("/memory search foo ", _infos())
    assert level is CompletionLevel.NONE
    assert items == ()
