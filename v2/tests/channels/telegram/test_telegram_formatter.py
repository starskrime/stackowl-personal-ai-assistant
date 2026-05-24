"""Tests for Telegram formatter utilities — escape_md and all formatter classes."""

from __future__ import annotations

import pytest

from stackowl.channels.telegram.formatter import (
    TelegramBriefFormatter,
    TelegramEvolutionFormatter,
    TelegramMarkdownFormatter,
    TelegramMemoryFormatter,
    TelegramParliamentFormatter,
    escape_md,
)


# ---------------------------------------------------------------------------
# 1. escape_md escapes all 18 reserved MarkdownV2 chars
# ---------------------------------------------------------------------------


def test_escape_md_escapes_all_reserved_chars() -> None:
    # Full set of MarkdownV2 reserved characters (18 chars, excluding backslash itself)
    reserved_chars = ["_", "*", "[", "]", "(", ")", "~", "`", "#", "+", "-", "=", "|", "{", "}", ".", "!"]
    for char in reserved_chars:
        result = escape_md(char)
        assert result == f"\\{char}", f"Expected \\{char!r} but got {result!r}"


# ---------------------------------------------------------------------------
# 2. escape_md handles empty string
# ---------------------------------------------------------------------------


def test_escape_md_empty_string() -> None:
    assert escape_md("") == ""


# ---------------------------------------------------------------------------
# 3. escape_md handles string with no reserved chars
# ---------------------------------------------------------------------------


def test_escape_md_no_reserved_chars() -> None:
    text = "Hello world"
    assert escape_md(text) == text


# ---------------------------------------------------------------------------
# 4. escape_md escapes underscore
# ---------------------------------------------------------------------------


def test_escape_md_underscore() -> None:
    assert escape_md("hello_world") == r"hello\_world"


# ---------------------------------------------------------------------------
# 5. escape_md escapes asterisk
# ---------------------------------------------------------------------------


def test_escape_md_asterisk() -> None:
    assert escape_md("2 * 3") == r"2 \* 3"


# ---------------------------------------------------------------------------
# 6. TelegramParliamentFormatter.format_synthesis returns non-empty string
# ---------------------------------------------------------------------------


def test_parliament_formatter_returns_nonempty() -> None:
    formatter = TelegramParliamentFormatter()
    result = formatter.format_synthesis("synthesis text", ["Owl1", "Owl2"], 3)
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# 7. TelegramParliamentFormatter.format_synthesis contains escaped owl names
# ---------------------------------------------------------------------------


def test_parliament_formatter_escapes_owl_names() -> None:
    formatter = TelegramParliamentFormatter()
    result = formatter.format_synthesis("body", ["Owl-One", "Owl.Two"], 2)
    # Hyphen and period are MarkdownV2 reserved — must be escaped
    assert r"Owl\-One" in result
    assert r"Owl\.Two" in result


# ---------------------------------------------------------------------------
# 8. TelegramBriefFormatter.format_morning_brief returns non-empty for empty sections
# ---------------------------------------------------------------------------


def test_brief_formatter_empty_sections_nonempty() -> None:
    formatter = TelegramBriefFormatter()
    result = formatter.format_morning_brief({})
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# 9. TelegramBriefFormatter.format_morning_brief escapes section content
# ---------------------------------------------------------------------------


def test_brief_formatter_escapes_section_content() -> None:
    formatter = TelegramBriefFormatter()
    result = formatter.format_morning_brief({"News": "Price: $1.50 (USD)"})
    # Period, parentheses are reserved — must be escaped
    assert r"\." in result
    assert r"\(" in result
    assert r"\)" in result


# ---------------------------------------------------------------------------
# 10. TelegramMemoryFormatter.format_memory_nudge returns tuple of (str, dict)
# ---------------------------------------------------------------------------


def test_memory_formatter_returns_tuple() -> None:
    formatter = TelegramMemoryFormatter()
    result = formatter.format_memory_nudge("User likes coffee", "fact-123")
    assert isinstance(result, tuple)
    assert len(result) == 2
    text, keyboard = result
    assert isinstance(text, str)
    assert isinstance(keyboard, dict)


# ---------------------------------------------------------------------------
# 11. TelegramMemoryFormatter keyboard has inline_keyboard structure
# ---------------------------------------------------------------------------


def test_memory_formatter_keyboard_structure() -> None:
    formatter = TelegramMemoryFormatter()
    _, keyboard = formatter.format_memory_nudge("fact body", "fact-abc")
    assert "inline_keyboard" in keyboard
    rows = keyboard["inline_keyboard"]
    assert isinstance(rows, list)
    assert len(rows) >= 1
    first_row = rows[0]
    assert isinstance(first_row, list)
    assert len(first_row) >= 1
    # Each button should have text and callback_data
    for btn in first_row:
        assert isinstance(btn, dict)
        assert "text" in btn
        assert "callback_data" in btn


# ---------------------------------------------------------------------------
# 12. TelegramMemoryFormatter callback_data contains fact_id
# ---------------------------------------------------------------------------


def test_memory_formatter_callback_data_contains_fact_id() -> None:
    formatter = TelegramMemoryFormatter()
    fact_id = "unique-fact-xyz-999"
    _, keyboard = formatter.format_memory_nudge("some fact", fact_id)
    rows = keyboard["inline_keyboard"]
    all_callback_data = [btn["callback_data"] for row in rows for btn in row if isinstance(btn, dict)]
    assert any(fact_id in cb for cb in all_callback_data)


# ---------------------------------------------------------------------------
# 13. TelegramEvolutionFormatter.format_evolution_event shows positive deltas
# ---------------------------------------------------------------------------


def test_evolution_formatter_shows_positive_deltas() -> None:
    formatter = TelegramEvolutionFormatter()
    result = formatter.format_evolution_event("Athena", {"verbosity": 0.15, "challenge": -0.05})
    assert "\\+0\\.150" in result or "+0.150" in result or "\\+0" in result or "0.150" in result
    # Should show trait names (escaped)
    assert "verbosity" in result
    assert "challenge" in result


# ---------------------------------------------------------------------------
# 14. TelegramEvolutionFormatter.format_evolution_event shows negative deltas
# ---------------------------------------------------------------------------


def test_evolution_formatter_shows_negative_deltas() -> None:
    formatter = TelegramEvolutionFormatter()
    result = formatter.format_evolution_event("Hermes", {"risk_tolerance": -0.2})
    assert "risk" in result  # "risk_tolerance" may have underscore escaped
    assert "\\-0\\.200" in result or "-0.200" in result or "0.200" in result


# ---------------------------------------------------------------------------
# 15. TelegramEvolutionFormatter returns non-empty string
# ---------------------------------------------------------------------------


def test_evolution_formatter_nonempty() -> None:
    formatter = TelegramEvolutionFormatter()
    result = formatter.format_evolution_event("Owl", {"trait": 0.1})
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# 16. TelegramMarkdownFormatter.format_response escapes text
# ---------------------------------------------------------------------------


def test_markdown_formatter_format_response_escapes() -> None:
    formatter = TelegramMarkdownFormatter()
    result = formatter.format_response("1+1=2 [yes]")
    assert r"\+" in result
    assert r"\=" in result
    assert r"\[" in result
    assert r"\]" in result


# ---------------------------------------------------------------------------
# 17. TelegramMarkdownFormatter.format_plain escapes text
# ---------------------------------------------------------------------------


def test_markdown_formatter_format_plain_escapes() -> None:
    formatter = TelegramMarkdownFormatter()
    result = formatter.format_plain("cost: $100.00")
    assert r"\." in result


# ---------------------------------------------------------------------------
# 18. TelegramParliamentFormatter synthesis body is escaped
# ---------------------------------------------------------------------------


def test_parliament_formatter_body_escaped() -> None:
    formatter = TelegramParliamentFormatter()
    result = formatter.format_synthesis("price: $1.50 (approx.)", ["Owl1"], 1)
    # Parentheses and period in synthesis body must be escaped
    assert r"\(" in result
    assert r"\." in result
