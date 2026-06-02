"""Story 8.3 — ComposeArea submit / autocomplete behaviour."""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.tui.messages import ComposeSubmittedMessage
from stackowl.tui.widgets.compose_area import ComposeArea
from stackowl.tui.widgets.compose_helpers import (
    AutocompleteKind,
    CommandInfo,
    build_state,
    detect_kind,
    filter_candidates,
)

pytestmark = pytest.mark.tui


# ---------------------------------------------------------------------------
# A. ComposeArea state lifecycle
# ---------------------------------------------------------------------------


def test_compose_area_state_starts_idle() -> None:
    area = ComposeArea()
    assert area.state == "idle"


def test_compose_area_set_mcp_disabled_true_sets_mcp_disabled_state() -> None:
    area = ComposeArea()
    area.set_mcp_disabled(True)
    assert area.state == "mcp-disabled"


def test_compose_area_set_mcp_disabled_false_returns_to_idle() -> None:
    area = ComposeArea()
    area.set_mcp_disabled(True)
    area.set_mcp_disabled(False)
    assert area.state == "idle"


# ---------------------------------------------------------------------------
# B. ComposeArea submit / no-op behaviour
# ---------------------------------------------------------------------------


def test_compose_area_on_input_submitted_posts_compose_submitted_message() -> None:
    area = ComposeArea()
    posted: list[Any] = []
    area.post_message = posted.append  # type: ignore[method-assign]
    fake_event = type("E", (), {"value": "hello world  "})()
    area.on_input_submitted(fake_event)  # type: ignore[arg-type]
    assert len(posted) == 1
    msg = posted[0]
    assert isinstance(msg, ComposeSubmittedMessage)
    assert msg.text == "hello world"


def test_compose_area_on_input_submitted_noop_when_mcp_disabled() -> None:
    area = ComposeArea()
    area.set_mcp_disabled(True)
    posted: list[Any] = []
    area.post_message = posted.append  # type: ignore[method-assign]
    fake_event = type("E", (), {"value": "hi"})()
    area.on_input_submitted(fake_event)  # type: ignore[arg-type]
    assert posted == []
    assert area.state == "mcp-disabled"


def test_compose_area_on_input_submitted_noop_when_empty() -> None:
    area = ComposeArea()
    posted: list[Any] = []
    area.post_message = posted.append  # type: ignore[method-assign]
    fake_event = type("E", (), {"value": "   "})()
    area.on_input_submitted(fake_event)  # type: ignore[arg-type]
    assert posted == []


# ---------------------------------------------------------------------------
# C. ComposeArea autocomplete triggers
# ---------------------------------------------------------------------------


def test_compose_area_on_input_changed_slash_triggers_command_autocomplete() -> None:
    area = ComposeArea(
        command_names=["help", "heat", "history", "halt"], owl_names=["secretary"]
    )
    fake_event = type("E", (), {"value": "/he"})()
    area.on_input_changed(fake_event)  # type: ignore[arg-type]
    snap = area.autocomplete_state
    assert snap.kind == AutocompleteKind.COMMAND
    assert snap.prefix == "he"
    assert "help" in snap.candidates
    assert "heat" in snap.candidates
    # 'history' / 'halt' start with 'h' but not 'he' — must be filtered out.
    assert "history" not in snap.candidates
    assert "halt" not in snap.candidates


def test_compose_area_on_input_changed_at_triggers_owl_autocomplete() -> None:
    area = ComposeArea(
        command_names=["help"], owl_names=["secretary", "second", "scout"]
    )
    fake_event = type("E", (), {"value": "@sec"})()
    area.on_input_changed(fake_event)  # type: ignore[arg-type]
    snap = area.autocomplete_state
    assert snap.kind == AutocompleteKind.OWL
    assert snap.prefix == "sec"
    assert "secretary" in snap.candidates
    assert "second" in snap.candidates
    # 'scout' starts with 's' but not 'sec' — must be filtered out.
    assert "scout" not in snap.candidates


def test_compose_area_on_input_changed_no_trigger_hides_autocomplete() -> None:
    area = ComposeArea(command_names=["help"], owl_names=["secretary"])
    fake_event = type("E", (), {"value": "hello world"})()
    area.on_input_changed(fake_event)  # type: ignore[arg-type]
    assert area.autocomplete_state.kind == AutocompleteKind.NONE
    assert area.autocomplete_state.candidates == ()


def test_compose_area_on_input_changed_at_after_space_triggers_owl_autocomplete() -> None:
    area = ComposeArea(owl_names=["secretary", "parrot"])
    fake_event = type("E", (), {"value": "hello @par"})()
    area.on_input_changed(fake_event)  # type: ignore[arg-type]
    snap = area.autocomplete_state
    assert snap.kind == AutocompleteKind.OWL
    assert snap.prefix == "par"
    assert "parrot" in snap.candidates


# ---------------------------------------------------------------------------
# C2. ComposeArea command_infos threading (Story 2)
# ---------------------------------------------------------------------------


def test_compose_area_command_infos_populates_desc_by_name() -> None:
    area = ComposeArea(command_infos=[CommandInfo("memory", "Memory mgmt")])
    assert area._desc_by_name == {"memory": "Memory mgmt"}
    # Names derived from infos when command_names not given.
    assert area._command_names == ["memory"]


def test_compose_area_explicit_command_names_wins_over_infos() -> None:
    area = ComposeArea(
        command_names=["help"],
        command_infos=[CommandInfo("memory", "Memory mgmt")],
    )
    # Explicit names preserved for back-compat; descriptions still threaded.
    assert area._command_names == ["help"]
    assert area._desc_by_name == {"memory": "Memory mgmt"}


# ---------------------------------------------------------------------------
# D. Pure helper functions
# ---------------------------------------------------------------------------


def test_detect_kind_slash_returns_command() -> None:
    kind, prefix = detect_kind("/abc")
    assert kind == AutocompleteKind.COMMAND
    assert prefix == "abc"


def test_detect_kind_at_returns_owl() -> None:
    kind, prefix = detect_kind("@xyz")
    assert kind == AutocompleteKind.OWL
    assert prefix == "xyz"


def test_filter_candidates_case_insensitive() -> None:
    out = filter_candidates("SE", ["secretary", "second", "scout"])
    assert "secretary" in out
    assert "second" in out
    assert "scout" not in out


def test_filter_candidates_empty_prefix_returns_all_up_to_limit() -> None:
    out = filter_candidates("", ["a", "b", "c"], limit=2)
    assert out == ("a", "b")


def test_build_state_none_when_no_trigger() -> None:
    state = build_state("plain text", command_names=["help"], owl_names=["sec"])
    assert state.kind == AutocompleteKind.NONE
