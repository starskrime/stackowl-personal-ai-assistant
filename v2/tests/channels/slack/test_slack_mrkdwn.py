"""Slack C2 — GFM→mrkdwn converter (``to_slack_mrkdwn``) unit tests.

The assistant emits GitHub-flavored Markdown; Slack renders mrkdwn, which uses
a single-asterisk for bold, ``~`` for strike, and ``<url|text>`` links. These
tests pin the conversion and — critically — that markup INSIDE code spans /
fenced blocks is left literal.
"""

from __future__ import annotations

from stackowl.channels.slack.helpers import to_slack_mrkdwn


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
