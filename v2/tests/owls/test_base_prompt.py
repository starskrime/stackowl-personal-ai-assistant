"""Unit tests for the agentic base prompt (Phase A2-A4).

build_base_prompt is a pure function: it takes a fixed datetime and returns the
shared agentic preamble that leads every system prompt. These tests pin the
load-bearing content so a 4B model always receives: the live date, the exact
ReAct tool-use syntax (matching the Phase A1 parser), an anti-excuse mandate,
and the when-blocked escalation ladder.
"""

from __future__ import annotations

from datetime import datetime

from stackowl.owls.base_prompt import build_base_prompt


def _fixed() -> datetime:
    return datetime(2026, 5, 31, 14, 30, 0)


def test_base_prompt_injects_iso_date() -> None:
    prompt = build_base_prompt(_fixed())
    assert _fixed().isoformat() in prompt


def test_base_prompt_has_react_action_syntax() -> None:
    prompt = build_base_prompt(_fixed())
    # The exact tokens the Phase A1 parser (_react.py) matches: an ACTION: line
    # followed by a ```json fenced args block.
    assert "ACTION:" in prompt
    assert "```json" in prompt


def test_base_prompt_few_shot_matches_a1_parser() -> None:
    """The embedded few-shot must actually parse with the real A1 parser."""
    from stackowl.providers._react import parse_react_action

    prompt = build_base_prompt(_fixed())
    parsed = parse_react_action(prompt)
    assert parsed is not None, "few-shot ACTION block must be parseable by A1 parser"
    name, args = parsed
    assert name == "web_search"
    assert "query" in args


def test_base_prompt_anti_excuse_phrase() -> None:
    prompt = build_base_prompt(_fixed())
    lowered = prompt.lower()
    assert "never refuse" in lowered or "excuses" in lowered


def test_base_prompt_when_blocked_ladder() -> None:
    prompt = build_base_prompt(_fixed())
    assert "web_search" in prompt
    assert "skill_manage" in prompt


def test_base_prompt_is_tight_for_4b_budget() -> None:
    """Keep it within the ~200-300 word budget for a weak 4B context."""
    prompt = build_base_prompt(_fixed())
    assert len(prompt.split()) <= 320
