"""Bakir: "Agent stuck on asterisks ... always format output for the channel."

He asked the assistant to stop sending markdown and got back "✓ done in 9s" and a
promise — "no asterisks, no raw tables". The assistant treated a FORMATTER DEFECT as
a preference to remember. It is not: three of these are reproducible through
``TelegramMarkdownFormatter.format_response``, the exact method ``send_text`` calls
on every outbound Telegram message.

    'Use **`code`** here'   -> 'Use *\\ue0000\\ue000* here'   <- the code is GONE
    '***triple***'          -> '*\\*triple*\\*'                <- literal asterisks
    '2 * 3 * 4 = 24'        -> '2 _ 3 _ 4 \\= 24'              <- arithmetic rewritten

THE FIRST IS CONTENT LOSS, and it is the worst of the three. Code spans are stashed
behind a private-use sentinel, then ``**...**`` matches the STASHED text and stashes
it again — so the outer placeholder holds an inner placeholder. The restore pass runs
``restore_re.sub`` ONCE and is not recursive, so the inner sentinel is delivered to
Telegram raw. Every ``**`code`**`` the assistant writes arrives as two invisible
characters.

THE SECOND AND THIRD ARE ONE FAULT: the emphasis patterns have no delimiter
discipline. ``\\*\\*(.+?)\\*\\*`` happily matches the first two of three asterisks, and
``\\*(.+?)\\*`` pairs any two bare asterisks on a line — so a multiplication becomes
italics and a triple-emphasis leaves its leftovers on screen.

WHAT THIS IS NOT. It is not an output-style preference. ``OutputStyle`` defaults to
``markdown="full"`` and is resolved from PREFERENCES, and the assistant's answer was
to write a preference down. The channel renders what it renders regardless of what
the user prefers, which is what "format output for the channel" means.
"""

from __future__ import annotations

import pytest

from stackowl.channels.telegram.formatter import TelegramMarkdownFormatter

_SENTINEL = ""


@pytest.fixture
def fmt() -> TelegramMarkdownFormatter:
    return TelegramMarkdownFormatter()


def test_CODE_INSIDE_BOLD_is_not_lost(fmt: TelegramMarkdownFormatter) -> None:
    """The content-loss case. Nested stashes need a recursive restore."""
    out = fmt.format_response("Use **`code`** here")

    assert _SENTINEL not in out, f"a placeholder reached Telegram raw: {out!r}"
    assert "code" in out


def test_no_sentinel_ever_survives(fmt: TelegramMarkdownFormatter) -> None:
    """The general rule the above is one instance of."""
    for text in (
        "**`a`** and **`b`**",
        "*`x`*",
        "~~`y`~~",
        "[**`z`**](https://example.com)",
    ):
        assert _SENTINEL not in fmt.format_response(text), text


def test_TRIPLE_emphasis_leaves_no_literal_asterisk(fmt: TelegramMarkdownFormatter) -> None:
    """The report, verbatim: asterisks on screen."""
    out = fmt.format_response("***triple***")

    assert "triple" in out
    assert "\\*" not in out, f"literal asterisk delivered: {out!r}"


def test_a_MULTIPLICATION_is_not_turned_into_italics(fmt: TelegramMarkdownFormatter) -> None:
    """'2 * 3 * 4 = 24' became '2 _ 3 _ 4' — the arithmetic was rewritten."""
    out = fmt.format_response("2 * 3 * 4 = 24")

    assert "_" not in out, f"bare asterisks were paired as emphasis: {out!r}"


def test_REAL_bold_and_italic_still_render(fmt: TelegramMarkdownFormatter) -> None:
    """The expensive direction. Escaping everything is the behaviour F009 replaced,
    and it is what made replies arrive as literal backslashed characters."""
    assert fmt.format_response("**bold**") == "*bold*"
    assert fmt.format_response("a *word* here").startswith("a _word_")


def test_a_lone_asterisk_is_still_escaped(fmt: TelegramMarkdownFormatter) -> None:
    """Unchanged: a single reserved char must never reach Telegram unescaped or the
    send 400s."""
    assert fmt.format_response("a * b") == "a \\* b"


def test_code_spans_stay_verbatim(fmt: TelegramMarkdownFormatter) -> None:
    out = fmt.format_response("run `a**b` now")

    assert "a**b" in out, f"code content was altered: {out!r}"


def test_an_ordinary_reply_is_unharmed(fmt: TelegramMarkdownFormatter) -> None:
    """The shape most of Bakir's replies actually have."""
    out = fmt.format_response("**Done.** I checked 3 things and 2 passed.")

    assert _SENTINEL not in out
    assert "\\*" not in out
    assert "Done" in out and "checked 3 things" in out
