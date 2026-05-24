"""Story 8.4 (part A) — parliament messages + pure formatting helpers.

Widget-level, coordinator, TCSS, migration, and onboarding tests live in
``test_story_8_4b.py`` to keep each file under the 300-line limit.
"""

from __future__ import annotations

import dataclasses

import pytest

from stackowl.tui.glyphs import GLYPH_PARLIAMENT, GLYPH_SEPARATOR
from stackowl.tui.messages import (
    ParliamentClosedMessage,
    ParliamentRoundStartedMessage,
    ParliamentStartedMessage,
    SynthesisArrivedMessage,
)
from stackowl.tui.widgets.parliament_panel_helpers import (
    build_synthesis_sections,
    format_rollcall,
    format_round_header,
    synthesis_lines,
)

pytestmark = pytest.mark.tui


# ---------------------------------------------------------------------------
# A. Message dataclasses — fields, frozen-ness
# ---------------------------------------------------------------------------


def test_parliament_started_message_has_required_fields() -> None:
    msg = ParliamentStartedMessage(
        session_id="s1", owl_names=("a", "b"), trigger="multi_mention"
    )
    assert msg.session_id == "s1"
    assert msg.owl_names == ("a", "b")
    assert msg.trigger == "multi_mention"
    assert dataclasses.is_dataclass(msg)


def test_parliament_started_message_is_frozen() -> None:
    msg = ParliamentStartedMessage(session_id="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.session_id = "y"  # type: ignore[misc]


def test_parliament_round_started_message_has_round_number() -> None:
    msg = ParliamentRoundStartedMessage(session_id="s1", round_number=2)
    assert msg.session_id == "s1"
    assert msg.round_number == 2


def test_synthesis_arrived_message_has_required_fields() -> None:
    msg = SynthesisArrivedMessage(
        session_id="s1",
        consensus="agree on X",
        recommendation="do Y",
        confidence=0.75,
        disagreements=("d1", "d2"),
    )
    assert msg.session_id == "s1"
    assert msg.consensus == "agree on X"
    assert msg.recommendation == "do Y"
    assert msg.confidence == pytest.approx(0.75)
    assert msg.disagreements == ("d1", "d2")


def test_parliament_closed_message_has_session_id() -> None:
    msg = ParliamentClosedMessage(session_id="s1")
    assert msg.session_id == "s1"


# ---------------------------------------------------------------------------
# B. Pure formatting helpers
# ---------------------------------------------------------------------------


def test_format_rollcall_includes_every_owl_name() -> None:
    out = format_rollcall(("Owl1", "Owl2", "Owl3"), str(GLYPH_PARLIAMENT))
    assert "Owl1" in out
    assert "Owl2" in out
    assert "Owl3" in out


def test_format_rollcall_uses_parliament_glyph_separator() -> None:
    glyph = str(GLYPH_PARLIAMENT)
    out = format_rollcall(("A", "B"), glyph)
    # Glyph precedes each owl name.
    assert f"{glyph} A" in out
    assert f"{glyph} B" in out
    # Names separated by " · " bullet.
    assert " · " in out
    assert out.startswith("Parliament:")


def test_format_round_header_renders_round_label_and_number() -> None:
    out = format_round_header("round", 2)
    assert "round" in out
    assert "2" in out


def test_build_synthesis_sections_includes_all_pieces_when_disagreements() -> None:
    sections = build_synthesis_sections(
        consensus="agree",
        recommendation="do Y",
        confidence=0.9,
        disagreements=("d1",),
        consensus_label="parliament.consensus",
        disagreements_label="parliament.disagreements",
        recommendation_label="parliament.recommendation",
        separator=str(GLYPH_SEPARATOR),
    )
    lines = synthesis_lines(sections)
    assert any("parliament.consensus" in ln for ln in lines)
    assert any("parliament.recommendation" in ln for ln in lines)
    assert any("parliament.disagreements" in ln for ln in lines)
    assert any("agree" in ln for ln in lines)
    assert any("do Y" in ln for ln in lines)
    assert any("90%" in ln for ln in lines)
    assert any("d1" in ln for ln in lines)


def test_build_synthesis_sections_omits_disagreements_when_empty() -> None:
    sections = build_synthesis_sections(
        consensus="agree",
        recommendation="do Y",
        confidence=0.5,
        disagreements=(),
        consensus_label="parliament.consensus",
        disagreements_label="parliament.disagreements",
        recommendation_label="parliament.recommendation",
        separator=str(GLYPH_SEPARATOR),
    )
    assert sections.disagreements_header is None
    assert sections.disagreements_lines == ()
    lines = synthesis_lines(sections)
    assert not any("parliament.disagreements" in ln for ln in lines)


def test_build_synthesis_sections_clamps_confidence_out_of_bounds() -> None:
    too_high = build_synthesis_sections(
        consensus="",
        recommendation="",
        confidence=2.5,
        disagreements=(),
        consensus_label="c",
        disagreements_label="d",
        recommendation_label="r",
        separator="◆",
    )
    too_low = build_synthesis_sections(
        consensus="",
        recommendation="",
        confidence=-1.0,
        disagreements=(),
        consensus_label="c",
        disagreements_label="d",
        recommendation_label="r",
        separator="◆",
    )
    assert "100%" in too_high.confidence_line
    assert "0%" in too_low.confidence_line


def test_synthesis_lines_begins_with_separator() -> None:
    sections = build_synthesis_sections(
        consensus="x",
        recommendation="y",
        confidence=0.1,
        disagreements=(),
        consensus_label="c",
        disagreements_label="d",
        recommendation_label="r",
        separator="◆",
    )
    assert synthesis_lines(sections)[0] == "◆"
