"""CHAN-1 (F009) — Telegram GFM → MarkdownV2 conversion.

The assistant emits GitHub-flavored Markdown. Telegram MarkdownV2 must RENDER
bold/italic/links, not show literal ``**bold**`` / ``[t](u)``. The converter
protects code spans/fences, maps GFM markup to MarkdownV2, and escapes only the
remaining reserved chars OUTSIDE markup (so a stray ``.`` still can't 400).
"""

from __future__ import annotations

from stackowl.channels.telegram.formatter import (
    TelegramMarkdownFormatter,
    to_telegram_markdownv2,
)


def test_bold_renders_not_literal() -> None:
    # GFM **bold** must become MarkdownV2 *bold*, NOT escaped literals.
    out = to_telegram_markdownv2("hello **world**")
    assert "*world*" in out
    assert r"\*\*" not in out


def test_italic_underscore_renders() -> None:
    # GFM _italic_ is MarkdownV2 _italic_ — must survive unescaped.
    out = to_telegram_markdownv2("an _emphasis_ here")
    assert "_emphasis_" in out
    assert r"\_emphasis\_" not in out


def test_link_renders_as_markdownv2_link() -> None:
    out = to_telegram_markdownv2("see [the docs](https://example.com/p)")
    # MarkdownV2 link keeps [text](url); the url's reserved chars are NOT escaped
    # inside the parens, the link text is preserved.
    assert "[the docs](https://example.com/p)" in out
    assert r"\[the docs\]" not in out


def test_reserved_chars_outside_markup_still_escaped() -> None:
    # A period / plus outside any markup MUST still be escaped (no 400).
    out = to_telegram_markdownv2("version 1.2 + extras")
    assert r"1\.2" in out
    assert r"\+" in out


def test_code_span_contents_preserved_verbatim() -> None:
    # Markup INSIDE a code span must never be converted; code spans stay intact.
    out = to_telegram_markdownv2("run `a_b**c**` now")
    assert "`a_b**c**`" in out


def test_fenced_block_preserved() -> None:
    out = to_telegram_markdownv2("```\n**not bold** here.\n```")
    assert "**not bold** here." in out


def test_format_response_now_converts_gfm() -> None:
    # The adapter-facing entrypoint must render markup, closing F009.
    out = TelegramMarkdownFormatter().format_response("a **bold** word")
    assert "*bold*" in out
    assert r"\*\*" not in out


def test_format_plain_still_escapes_everything() -> None:
    # format_plain is the escape-only path (untrusted text); markup stays literal.
    out = TelegramMarkdownFormatter().format_plain("a **bold** word")
    assert r"\*\*bold\*\*" in out


def test_table_does_not_leak_raw_pipes() -> None:
    src = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    out = to_telegram_markdownv2(src)
    assert "1" in out and "2" in out and "A" in out
    assert r"\|" not in out          # no escaped-pipe table wreckage
    assert "```" in out              # table must be wrapped in a fenced block
