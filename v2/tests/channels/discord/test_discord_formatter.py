"""Tests for DiscordMarkdownFormatter rendering paths."""

from __future__ import annotations

from stackowl.channels.discord.helpers import DiscordMarkdownFormatter


def test_format_parliament_synthesis_has_round_headers() -> None:
    formatter = DiscordMarkdownFormatter()
    synthesis = "Round 1\nposition a\n\nRound 2\nposition b\n"
    out = formatter.format_parliament_synthesis(synthesis)
    assert "**Round 1:**" in out
    assert "**Round 2:**" in out


def test_format_morning_brief_returns_multiple_messages() -> None:
    formatter = DiscordMarkdownFormatter()
    sections = ["alpha body", "beta body", "gamma body"]
    out = formatter.format_morning_brief(sections)
    assert len(out) == 3
    for msg, body in zip(out, sections, strict=True):
        assert body in msg
        # Each section starts with a bold header.
        assert msg.startswith("**")


def test_format_response_preserves_markdown() -> None:
    formatter = DiscordMarkdownFormatter()
    text = "**bold** *italic* `code` and ```py\nx\n```"
    out = formatter.format_response(text)
    assert "**bold**" in out
    assert "*italic*" in out
    assert "`code`" in out
    assert "```py\nx\n```" in out


def test_format_response_empty() -> None:
    formatter = DiscordMarkdownFormatter()
    assert formatter.format_response("") == ""
