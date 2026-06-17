"""CHAN-3 (F007) — a link span is never severed across a chunk boundary.

The splitter retreats a cut that lands inside an atomic span (a Slack
``<url|text>`` link or a Telegram ``[text](url)`` link) to just before the
span opens — the same mechanism already used for an open code fence.
"""

from __future__ import annotations

from stackowl.channels.splitter import (
    SlackMessageSplitter,
    TelegramMessageSplitter,
)


class _TinySlackSplitter(SlackMessageSplitter):
    @property
    def char_limit(self) -> int:
        return 80


class _TinyTelegramSplitter(TelegramMessageSplitter):
    @property
    def char_limit(self) -> int:
        return 80


def _reassemble(chunks: list[str]) -> str:
    return "".join(chunks)


def test_slack_link_not_severed() -> None:
    # The <url|text> link straddles the 80-char limit; it must stay intact in
    # ONE chunk, never split across two.
    link = "<https://example.com/p|click here>"  # 34 chars, fits in 80
    text = "prefix words to push the cut near the boundary here " + link + " tail"
    chunks = _TinySlackSplitter().split(text)
    assert any(link in c for c in chunks), f"link severed across chunks: {chunks}"
    # No chunk contains a half-open link (a '<' with a '|' but no closing '>').
    for c in chunks:
        if "<http" in c:
            assert ">" in c, f"chunk has an unterminated link span: {c!r}"


def test_telegram_link_not_severed() -> None:
    link = "[click the docs here](https://example.com/p)"  # 44 chars, fits in 80
    text = "some leading words before the link span lands " + link + " trailing"
    chunks = _TinyTelegramSplitter().split(text)
    assert any(link in c for c in chunks), f"link severed across chunks: {chunks}"


def test_no_link_unaffected() -> None:
    # Regression: plain text with no link splits exactly as before.
    text = "word " * 40
    chunks = _TinySlackSplitter().split(text)
    assert _reassemble([c.replace(" ", "") for c in chunks]) == text.replace(" ", "")


def test_link_longer_than_limit_still_emitted() -> None:
    # A single link longer than the whole limit cannot be kept atomic; the
    # splitter must still make progress (no infinite loop, no empty head).
    link = "[x](" + "https://example.com/" + "a" * 200 + ")"
    chunks = _TinyTelegramSplitter().split(link)
    assert chunks
    assert _reassemble(chunks).replace(" ", "") != ""
