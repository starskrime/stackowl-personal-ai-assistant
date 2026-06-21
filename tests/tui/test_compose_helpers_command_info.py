"""Story 2 — CommandInfo dataclass + filter_command_infos pure logic."""

from __future__ import annotations

import dataclasses

import pytest

from stackowl.commands.metadata import CommandMeta
from stackowl.tui.widgets.compose_helpers import CommandInfo, filter_command_infos

pytestmark = pytest.mark.tui


# ---------------------------------------------------------------------------
# A. CommandInfo dataclass
# ---------------------------------------------------------------------------


def test_command_info_is_frozen_dataclass() -> None:
    ci = CommandInfo(name="help", description="List commands")
    assert dataclasses.is_dataclass(ci)
    params = dataclasses.fields(ci)
    # `meta` was added to carry the command's sub-command tree; it defaults to
    # an empty CommandMeta so name-only callers are unaffected.
    assert {f.name for f in params} == {"name", "description", "meta"}
    assert ci.meta == CommandMeta()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ci.name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# B. filter_command_infos
# ---------------------------------------------------------------------------


def _infos() -> list[CommandInfo]:
    return [
        CommandInfo("help", "List commands"),
        CommandInfo("heat", "Heat map"),
        CommandInfo("history", "Show history"),
        CommandInfo("memory", "Memory mgmt"),
    ]


def test_filter_command_infos_prefix_match() -> None:
    out = filter_command_infos("he", _infos())
    names = [ci.name for ci in out]
    assert names == ["help", "heat"]
    # 'history' starts with 'h' but not 'he' — filtered out.
    assert "history" not in names


def test_filter_command_infos_case_insensitive() -> None:
    out = filter_command_infos("HE", _infos())
    assert [ci.name for ci in out] == ["help", "heat"]


def test_filter_command_infos_empty_prefix_returns_first_limit() -> None:
    out = filter_command_infos("", _infos(), limit=2)
    assert [ci.name for ci in out] == ["help", "heat"]


def test_filter_command_infos_limit_zero_returns_empty() -> None:
    out = filter_command_infos("he", _infos(), limit=0)
    assert out == ()


def test_filter_command_infos_negative_limit_returns_empty() -> None:
    out = filter_command_infos("he", _infos(), limit=-3)
    assert out == ()


def test_filter_command_infos_preserves_input_order() -> None:
    infos = [
        CommandInfo("hb", "second"),
        CommandInfo("ha", "first"),
    ]
    out = filter_command_infos("h", infos)
    assert [ci.name for ci in out] == ["hb", "ha"]


def test_filter_command_infos_carries_descriptions_through() -> None:
    out = filter_command_infos("mem", _infos())
    assert len(out) == 1
    assert out[0].name == "memory"
    assert out[0].description == "Memory mgmt"


def test_filter_command_infos_returns_tuple() -> None:
    out = filter_command_infos("he", _infos())
    assert isinstance(out, tuple)
