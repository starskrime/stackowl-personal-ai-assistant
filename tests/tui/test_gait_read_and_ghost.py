"""Pure-layer tests for the gait-read + ghost-text + row-tagging helpers."""

from __future__ import annotations

import pytest

from stackowl.commands.metadata import Arg, CommandMeta, SubCommand
from stackowl.tui.widgets.compose_helpers import (
    ROW_SEMANTIC,
    ROW_SUGGESTED,
    CommandInfo,
    DropdownRow,
    is_path_prefix,
    mark_rows,
    predict_next_token,
)

pytestmark = pytest.mark.tui


def _infos() -> list[CommandInfo]:
    return [
        CommandInfo(
            name="memory",
            description="manage durable memory",
            meta=CommandMeta(
                grammar="verb",
                subcommands=(
                    SubCommand(name="remember", summary="store a fact", args=(Arg(name="t"),)),
                    SubCommand(name="forget", summary="drop a fact"),
                ),
            ),
        ),
        CommandInfo(
            name="meet",
            description="schedule a meeting",
            meta=CommandMeta(grammar="leaf"),
        ),
        CommandInfo(
            name="tier",
            description="set provider tier",
            meta=CommandMeta(grammar="flag", args=(Arg(name="value"),)),
        ),
    ]


# --- is_path_prefix --------------------------------------------------------


@pytest.mark.parametrize(
    ("buffer", "expected"),
    [
        ("", True),
        ("/", True),
        ("/memory", True),
        ("  /memory", True),
        ("@max hello", True),
        ("forget what I said about my sister", False),
        ("remind me tomorrow", False),
        ("memory", False),  # no slash → prose, not a command path
    ],
)
def test_is_path_prefix(buffer: str, expected: bool) -> None:
    assert is_path_prefix(buffer) is expected


# --- predict_next_token ----------------------------------------------------


def test_predict_completes_command_partial() -> None:
    # "/mem" → both memory & meet? "mem" only prefixes "memory".
    assert predict_next_token("/mem", _infos()) == "ory"


def test_predict_first_candidate_when_ambiguous() -> None:
    # "/me" prefixes "meet" and "memory"; first in declared order is "memory".
    assert predict_next_token("/me", _infos()) == "mory"


def test_predict_completes_subcommand_partial() -> None:
    assert predict_next_token("/memory rem", _infos()) == "ember"


def test_predict_none_when_no_partial() -> None:
    # Right after a space there is no partial token to complete.
    assert predict_next_token("/memory ", _infos()) is None


def test_predict_none_for_unknown() -> None:
    assert predict_next_token("/zzz", _infos()) is None
    assert predict_next_token("hello there", _infos()) is None


def test_predict_none_when_exact() -> None:
    # Fully typed → nothing left to ghost.
    assert predict_next_token("/memory", _infos()) is None


# --- mark_rows / DropdownRow ----------------------------------------------


def test_mark_rows_tags_kind_and_keeps_index_access() -> None:
    rows = mark_rows([("/memory remember", "you usually do this next")], ROW_SUGGESTED)
    assert rows[0].kind == ROW_SUGGESTED
    # legacy index access still works (NamedTuple)
    assert rows[0][0] == "/memory remember"
    assert rows[0][1] == "you usually do this next"


def test_dropdown_row_defaults() -> None:
    r = DropdownRow(name="memory", description="manage memory")
    assert r.kind == "item"
    assert r[0] == "memory"


def test_mark_rows_semantic() -> None:
    rows = mark_rows([("/memory forget", "drop a fact")], ROW_SEMANTIC)
    assert all(r.kind == ROW_SEMANTIC for r in rows)
