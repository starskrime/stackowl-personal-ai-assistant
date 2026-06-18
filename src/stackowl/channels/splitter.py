"""BaseMessageSplitter and per-channel chunk size implementations.

Each channel (Telegram, Discord, Slack, WhatsApp) imposes a maximum message
length. The splitter divides long replies along the highest-quality boundary
that still fits — paragraph → sentence → grapheme — while never tearing a
fenced code block in half.

Note on UTF-16 counting (Telegram): the Bot API actually counts UTF-16 code
units, but pulling in ``pyicu`` is heavyweight; we use Python character count
here, which is conservative for the BMP and slightly *over*-counts surrogate
pairs (we always stay within the API limit). This is a known simplification.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from stackowl.infra.observability import log

_PARAGRAPH_RE = re.compile(r"\n\n+", re.UNICODE)
_SENTENCE_RE = re.compile(r"(?<=[.?!。？！])\s+", re.UNICODE)
_FENCE_RE = re.compile(r"```", re.UNICODE)

# Atomic link spans that must never be cut mid-way (CHAN-3 / F007):
#   * Slack mrkdwn ``<url|text>`` (and bare ``<url>``) — no ``<``/``>`` inside.
#   * Telegram/GFM ``[text](url)`` — text excludes ``]``, url excludes ``)``.
# A cut landing inside any such span retreats to just before it opens, the same
# mechanism as :meth:`_avoid_open_fence`.
_LINK_SPAN_RE = re.compile(r"<[^<>]+>|\[[^\]]+\]\([^)\s]+\)", re.UNICODE)

# When looking back for a paragraph break, scan the last N chars of the
# candidate chunk before giving up and looking for a sentence break.
_PARAGRAPH_LOOKBACK = 400


class BaseMessageSplitter(ABC):
    """Splits long text into channel-safe chunks.

    The split priority is paragraph → sentence → hard. Code fences are never
    severed: if a chosen split point falls inside an open ``` fence, the
    splitter retreats to the byte just before that fence opens.
    """

    @property
    @abstractmethod
    def char_limit(self) -> int:
        """Maximum characters per output chunk."""

    def split(self, text: str) -> list[str]:
        """Return ``text`` cut into chunks each at most :attr:`char_limit` long.

        Concatenating the returned chunks reproduces the input minus the
        whitespace that lived exactly at split boundaries.
        """
        log.gateway.debug(
            "[splitter] split: entry",
            extra={
                "_fields": {
                    "splitter": type(self).__name__,
                    "text_len": len(text),
                    "char_limit": self.char_limit,
                }
            },
        )
        if not text:
            return []
        if len(text) <= self.char_limit:
            log.gateway.debug(
                "[splitter] split: exit — single chunk",
                extra={"_fields": {"chunks": 1}},
            )
            return [text]

        chunks: list[str] = []
        remaining = text
        while len(remaining) > self.char_limit:
            cut = self._choose_cut(remaining)
            head = remaining[:cut].rstrip()
            if head:
                chunks.append(head)
            remaining = remaining[cut:].lstrip()
        if remaining:
            chunks.append(remaining)
        log.gateway.debug(
            "[splitter] split: exit",
            extra={"_fields": {"chunks": len(chunks)}},
        )
        return chunks

    def _choose_cut(self, text: str) -> int:
        """Pick the best cut index ≤ char_limit, respecting fences."""
        window = text[: self.char_limit]
        cut = self._find_paragraph_cut(window)
        if cut is None:
            cut = self._find_sentence_cut(window)
        if cut is None:
            cut = self.char_limit
        cut = self._avoid_open_fence(text, cut)
        cut = self._avoid_split_link(text, cut)
        # Defensive lower bound: never produce empty heads — fall back to a
        # hard split if fence/link-avoidance dragged the cut to zero (e.g. a
        # single link longer than the whole limit: it cannot be kept atomic, so
        # make progress with a hard cut rather than loop forever).
        if cut <= 0:
            log.gateway.warning(
                "[splitter] _choose_cut: fence/link-avoidance produced zero cut, hard splitting",
                extra={"_fields": {"limit": self.char_limit}},
            )
            cut = self.char_limit
        return cut

    @staticmethod
    def _find_paragraph_cut(window: str) -> int | None:
        """Return the index just *after* the last paragraph break in window."""
        search_from = max(0, len(window) - _PARAGRAPH_LOOKBACK)
        best: int | None = None
        for match in _PARAGRAPH_RE.finditer(window, search_from):
            best = match.end()
        return best

    @staticmethod
    def _find_sentence_cut(window: str) -> int | None:
        """Return the index just after the last sentence boundary in window."""
        best: int | None = None
        for match in _SENTENCE_RE.finditer(window):
            best = match.end()
        return best

    @staticmethod
    def _avoid_open_fence(text: str, cut: int) -> int:
        """If ``cut`` lands inside an open ``` fence, retreat to before it."""
        fence_indices = [m.start() for m in _FENCE_RE.finditer(text, 0, cut)]
        if len(fence_indices) % 2 == 1:
            # Odd number of fences before cut → cut is inside a fence.
            # Retreat to just before the most recent (opening) fence.
            return fence_indices[-1]
        return cut

    @staticmethod
    def _avoid_split_link(text: str, cut: int) -> int:
        """If ``cut`` lands inside a link span, retreat to before it (CHAN-3).

        Scans for an atomic link span (Slack ``<url|text>`` or GFM/Telegram
        ``[text](url)``) that STRADDLES ``cut`` — starts before ``cut`` and ends
        after it. Such a span is retreated to its start so the link stays intact
        in one chunk, mirroring :meth:`_avoid_open_fence`. A span that begins at
        index 0 cannot be retreated without an empty head (it is longer than the
        whole limit) — the caller's zero-cut guard then hard-splits to make
        progress rather than loop forever.
        """
        for match in _LINK_SPAN_RE.finditer(text):
            start, end = match.start(), match.end()
            if start >= cut:
                break  # spans are ordered; none further can straddle the cut
            if end > cut:
                log.gateway.debug(
                    "[splitter] _avoid_split_link: retreating cut before link span",
                    extra={"_fields": {"span_start": start, "old_cut": cut}},
                )
                return start
        return cut


class TelegramMessageSplitter(BaseMessageSplitter):
    """Telegram Bot API: 4096 UTF-16 code units; we use 3800 chars as a safe budget."""

    @property
    def char_limit(self) -> int:
        return 3800


class DiscordMessageSplitter(BaseMessageSplitter):
    """Discord: 2000 chars per message; 1900 keeps room for code-fence wrappers."""

    @property
    def char_limit(self) -> int:
        return 1900


class SlackMessageSplitter(BaseMessageSplitter):
    """Slack chat.postMessage: 4000 chars; 3900 keeps room for formatting."""

    @property
    def char_limit(self) -> int:
        return 3900


class WhatsAppMessageSplitter(BaseMessageSplitter):
    """WhatsApp Business Cloud API: 4096 chars; 4000 leaves margin."""

    @property
    def char_limit(self) -> int:
        return 4000
