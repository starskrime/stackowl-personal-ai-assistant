"""Story 9.7 — SlackBlockKitFormatter unit tests."""

from __future__ import annotations

import inspect

from stackowl.channels.slack import helpers as slack_helpers
from stackowl.channels.slack.helpers import SlackBlockKitFormatter


def test_parliament_synthesis_has_header() -> None:
    formatter = SlackBlockKitFormatter()
    blocks = formatter.format_parliament_synthesis(
        synthesis="Para one.\n\nPara two.",
        owls=["athena", "hermes"],
    )
    assert blocks[0]["type"] == "header"


def test_parliament_synthesis_blocks_are_dicts() -> None:
    formatter = SlackBlockKitFormatter()
    blocks = formatter.format_parliament_synthesis(
        synthesis="A.\n\nB.\n\nC.",
        owls=["a"],
    )
    assert isinstance(blocks, list)
    for blk in blocks:
        assert isinstance(blk, dict)
        assert "type" in blk
    # Three paragraphs → 1 header + 3 sections + 2 dividers = 6 blocks.
    section_count = sum(1 for b in blocks if b["type"] == "section")
    divider_count = sum(1 for b in blocks if b["type"] == "divider")
    assert section_count == 3
    assert divider_count == 2


def test_morning_brief_structure() -> None:
    formatter = SlackBlockKitFormatter()
    blocks = formatter.format_morning_brief(
        sections=["Top priorities for today.", "Memory highlights."]
    )
    assert blocks[0]["type"] == "header"
    section_count = sum(1 for b in blocks if b["type"] == "section")
    assert section_count == 2


def test_morning_brief_skips_blank_sections() -> None:
    formatter = SlackBlockKitFormatter()
    blocks = formatter.format_morning_brief(sections=["Real content.", "", "   "])
    section_count = sum(1 for b in blocks if b["type"] == "section")
    assert section_count == 1


def test_memory_nudge_has_buttons() -> None:
    formatter = SlackBlockKitFormatter()
    fact_id = "abcdef0123456789abcdef0123456789"
    blocks = formatter.format_memory_nudge(fact_id=fact_id, content="Promote me?")
    # Last block is the ActionsBlock containing two button elements.
    actions = blocks[-1]
    assert actions["type"] == "actions"
    elements = actions["elements"]
    assert isinstance(elements, list)
    assert len(elements) == 2
    action_ids = [el["action_id"] for el in elements]
    assert f"memory_approve_{fact_id[:8]}" in action_ids
    assert f"memory_reject_{fact_id[:8]}" in action_ids


def test_all_labels_via_localize() -> None:
    """Formatter code must call localize() rather than embed raw strings.

    We inspect the source of the helpers module — every user-facing label
    must come from localize(); the test is a guardrail against future
    regressions that bypass i18n.
    """
    src = inspect.getsource(slack_helpers)
    # The formatter constructs labels exclusively through localize() helpers.
    # We assert the localize calls exist and that the well-known label keys
    # we render are not hard-coded as plain literals.
    assert "localize(" in src
    # Hard-coded labels we explicitly forbid (would indicate i18n bypass).
    forbidden_literals = [
        '"Parliament Synthesis"',
        '"Morning Brief"',
        '"Approve"',
        '"Reject"',
    ]
    for lit in forbidden_literals:
        assert lit not in src, f"hard-coded label leaked into helpers.py: {lit}"
