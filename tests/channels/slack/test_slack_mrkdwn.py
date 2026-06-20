"""Slack C2 — GFM→mrkdwn converter (``to_slack_mrkdwn``) unit tests.

The assistant emits GitHub-flavored Markdown; Slack renders mrkdwn, which uses
a single-asterisk for bold, ``~`` for strike, and ``<url|text>`` links. These
tests pin the conversion and — critically — that markup INSIDE code spans /
fenced blocks is left literal.
"""

from __future__ import annotations

from stackowl.channels.slack.helpers import _PLACEHOLDER, to_slack_mrkdwn


def test_bold_double_asterisk() -> None:
    assert to_slack_mrkdwn("**bold**") == "*bold*"


def test_bold_double_underscore() -> None:
    assert to_slack_mrkdwn("__bold__") == "*bold*"


def test_strikethrough() -> None:
    assert to_slack_mrkdwn("~~strike~~") == "~strike~"


def test_link_conversion() -> None:
    assert to_slack_mrkdwn("[text](https://example.com)") == "<https://example.com|text>"


def test_bare_autolink_left_alone() -> None:
    # A bare URL is not a GFM inline link → untouched.
    assert to_slack_mrkdwn("see https://example.com now") == "see https://example.com now"


def test_header_becomes_bold() -> None:
    assert to_slack_mrkdwn("# Head") == "*Head*"


def test_header_multi_level_becomes_bold() -> None:
    assert to_slack_mrkdwn("### Sub heading") == "*Sub heading*"


def test_code_span_markup_stays_literal() -> None:
    # The ``**x**`` inside a code span must NOT be converted to ``*x*``.
    assert to_slack_mrkdwn("`**x**`") == "`**x**`"


def test_link_inside_code_span_stays_literal() -> None:
    assert to_slack_mrkdwn("`[t](u)`") == "`[t](u)`"


def test_fenced_block_content_stays_literal() -> None:
    src = "```\n**not bold** [t](u)\n```"
    assert to_slack_mrkdwn(src) == src


def test_underscore_italic_untouched() -> None:
    # Single-underscore italic already matches Slack mrkdwn → leave as-is.
    assert to_slack_mrkdwn("_italic_") == "_italic_"


def test_inline_code_untouched() -> None:
    assert to_slack_mrkdwn("`code`") == "`code`"


def test_mixed_outside_and_inside_code() -> None:
    # Bold OUTSIDE the span converts; bold INSIDE stays literal.
    assert to_slack_mrkdwn("**a** `**b**`") == "*a* `**b**`"


def test_empty_string() -> None:
    assert to_slack_mrkdwn("") == ""


def test_multiline_mixed() -> None:
    src = "# Title\n\n**bold** and [link](http://x.io)"
    assert to_slack_mrkdwn(src) == "*Title*\n\n*bold* and <http://x.io|link>"


# --------------------------------------------------------------------------- #
# C2 review — PUA sentinel can no longer collide with user content.
# --------------------------------------------------------------------------- #


def test_sentinel_in_input_does_not_crash() -> None:
    # A raw sentinel-shaped fragment in user/assistant text (Nerd-Font / PUA
    # glyph, LLM output) used to build a colliding placeholder and crash the
    # restore step with IndexError. It must now be neutralized, never raise.
    out = to_slack_mrkdwn(f"a{_PLACEHOLDER}0{_PLACEHOLDER}bc")
    assert _PLACEHOLDER not in out
    # Surrounding text survives (sentinel chars stripped, digits/letters kept).
    assert "a" in out and "bc" in out


def test_code_span_with_sentinel_not_corrupted() -> None:
    # Two code spans where a naive restore would swap their contents because
    # the first span literally contains the sentinel placeholder pattern.
    src = f"`{_PLACEHOLDER}1{_PLACEHOLDER}` and `real`"
    out = to_slack_mrkdwn(src)
    # The second span restores to its OWN content, never the first's.
    assert "`real`" in out
    # No live sentinel leaks into the output.
    assert _PLACEHOLDER not in out


def test_no_sentinel_input_byte_for_byte_unchanged() -> None:
    # Regression: the common path (no sentinel anywhere) is untouched.
    assert to_slack_mrkdwn("**b**") == "*b*"
    assert to_slack_mrkdwn("`**x**`") == "`**x**`"


# --------------------------------------------------------------------------- #
# CHAN-2 (F008) — single-asterisk GFM italic is disambiguated from bold and
# normalized to Slack underscore italic, without corrupting the common bold.
# --------------------------------------------------------------------------- #


def test_single_asterisk_italic_becomes_underscore() -> None:
    # GFM *italic* (single) must render as Slack _italic_, not as bold *italic*.
    assert to_slack_mrkdwn("*italic*") == "_italic_"


def test_bold_still_wins_over_italic() -> None:
    # **bold** must stay *bold* (Slack bold), never be mangled by italic handling.
    assert to_slack_mrkdwn("**bold**") == "*bold*"


def test_mixed_bold_and_italic() -> None:
    # A mix in one line: bold→*bold*, italic→_italic_.
    assert to_slack_mrkdwn("**b** and *i*") == "*b* and _i_"


def test_italic_inside_code_span_stays_literal() -> None:
    assert to_slack_mrkdwn("`*x*`") == "`*x*`"


def test_underscore_italic_still_untouched_after_chan2() -> None:
    assert to_slack_mrkdwn("_italic_") == "_italic_"


def test_table_does_not_leak_raw_pipes() -> None:
    src = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    out = to_slack_mrkdwn(src)
    assert "1" in out and "2" in out and "A" in out
    assert "| --- |" not in out   # delimiter row must not survive as broken pipe syntax
