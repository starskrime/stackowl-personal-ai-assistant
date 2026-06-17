"""Pure helpers and Block Kit formatter for the Slack channel adapter.

All user-facing strings flow through :func:`localize` so the platform stays
multilingual; no English literals are embedded directly.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from stackowl.infra.observability import log
from stackowl.tui.i18n import localize

__all__ = [
    "ButtonElement",
    "DividerBlock",
    "HeaderBlock",
    "PlainText",
    "SectionBlock",
    "SlackBlockKitFormatter",
    "hash_user_id",
    "is_authorized",
    "strip_bot_mention",
    "to_slack_mrkdwn",
]


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def hash_user_id(user_id: str) -> str:
    """Return the first 8 hex chars of sha256(user_id) — safe to log.

    Never log raw Slack user IDs; always pass through this helper first.
    """
    return hashlib.sha256(user_id.encode()).hexdigest()[:8]


def is_authorized(user_id: str, allowed_ids: list[str]) -> bool:
    """Membership check against the bot's allow-list (fail-closed)."""
    return user_id in allowed_ids


def strip_bot_mention(text: str, bot_user_id: str) -> str:
    """Remove ``<@{bot_user_id}>`` mentions from text and trim whitespace.

    Slack renders user mentions in the literal form ``<@U0123ABCD>`` within
    the event payload text. The adapter strips them before forwarding to the
    gateway so the routing layer never sees them.
    """
    needle = f"<@{bot_user_id}>"
    return text.replace(needle, "").strip()


# --------------------------------------------------------------------------- #
# GFM → Slack mrkdwn conversion
# --------------------------------------------------------------------------- #
#
# The assistant emits GitHub-flavored Markdown (GFM); Slack renders *mrkdwn*,
# which differs: bold is ``*single*`` (GFM ``**double**``), strike ``~tilde~``
# (GFM ``~~double~~``), links ``<url|text>`` (GFM ``[text](url)``). Inline code
# (`` `code` ``) and fenced (```` ```code``` ````) are identical and MUST be left
# byte-for-byte untouched — including any markup INSIDE them (a code block that
# literally shows ``**x**`` must not be re-formatted). Slack has no headers, so
# GFM headers render as bold.
#
# Strategy: fenced blocks and inline code spans are extracted to placeholders
# FIRST (so their contents are never touched), the remaining text is converted,
# then the placeholders are restored. Placeholders use a private-use codepoint;
# any pre-existing occurrence of that codepoint is stripped from the input on
# entry (U+E000 is a real PUA glyph that CAN appear in fonts/LLM output), and
# the restore lookup is index-tolerant, so placeholders can never collide with
# user content.

# Matches fenced ```...``` blocks (greedy-safe, non-overlapping) then inline
# `code` spans. Unicode-aware; no English literals.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL | re.UNICODE)
_INLINE_CODE_RE = re.compile(r"`[^`]*`", re.UNICODE)
# GFM bold: **text** or __text__ → *text* (mrkdwn). Non-greedy, no nested fence.
_BOLD_STAR_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL | re.UNICODE)
_BOLD_UNDER_RE = re.compile(r"__(.+?)__", re.DOTALL | re.UNICODE)
# GFM single-asterisk italic: *text* → _text_ (mrkdwn). CHAN-2 / F008. Matched
# ONLY after bold has been stashed out (so a ``*`` here can never be part of a
# ``**bold**`` run); the inner text excludes ``*`` so it can't span two markers.
_ITALIC_STAR_RE = re.compile(r"\*([^*\n]+?)\*", re.UNICODE)
# GFM strike: ~~text~~ → ~text~ (mrkdwn).
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL | re.UNICODE)
# GFM inline link: [text](url) → <url|text>. ``text`` excludes brackets, ``url``
# excludes whitespace and the closing paren.
_LINK_RE = re.compile(r"\[([^\]]+)\]\((\S+?)\)", re.UNICODE)
# GFM ATX header: 1–6 leading ``#`` then the heading text → bold (Slack has no
# headers). Multiline so each line is matched independently.
_HEADER_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.UNICODE)

# Private-use codepoint sentinel. It CAN appear in real text (a printable PUA
# glyph), so any occurrence is stripped from the input on entry and the restore
# lookup is index-tolerant — together these neutralize all collisions.
_PLACEHOLDER = ""


def to_slack_mrkdwn(text: str) -> str:
    """Convert assistant GitHub-flavored Markdown to Slack *mrkdwn*.

    Conversions applied OUTSIDE code spans/fences:

    * ``**bold**`` / ``__bold__`` → ``*bold*``
    * ``~~strike~~`` → ``~strike~``
    * ``[text](url)`` → ``<url|text>`` (bare autolinks are left alone)
    * GFM ATX headers (``# H`` … ``###### H``) → ``*H*`` (Slack has no headers)

    Inline code (`` `code` ``) and fenced (```` ```code``` ````) blocks — and any
    markup INSIDE them — are preserved literally. Underscore italic (``_x_``)
    already matches mrkdwn and is left untouched.

    Italic disambiguation (CHAN-2 / F008): ``**bold**``/``__bold__`` is converted
    and STASHED to a placeholder FIRST, so the subsequent single-asterisk italic
    pass (``*italic*`` → ``_italic_``) can never see a ``*`` that belongs to a
    ``**bold**`` run — bold stays correct AND single-asterisk italic now renders.
    This is the ONE place the GFM→mrkdwn contract lives.
    """
    log.slack.debug(
        "[slack] to_slack_mrkdwn: entry",
        extra={"_fields": {"text_len": len(text)}},
    )
    if not text:
        return text

    # 0. Strip any pre-existing sentinel codepoint from the input so a
    #    placeholder built from it can never collide with real content (U+E000
    #    is a printable PUA glyph that can occur in fonts / LLM output). Losing
    #    a non-semantic PUA control char is far safer than a crash or silent
    #    code corruption.
    if _PLACEHOLDER in text:
        stripped = text.count(_PLACEHOLDER)
        text = text.replace(_PLACEHOLDER, "")
        log.slack.debug(
            "[slack] to_slack_mrkdwn: step stripped sentinel from input",
            extra={"_fields": {"stripped_count": stripped}},
        )

    # 1. Protect code (fences first, then inline) so their contents are never
    #    transformed and never accidentally re-matched.
    protected: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"{_PLACEHOLDER}{len(protected) - 1}{_PLACEHOLDER}"

    work = _FENCE_RE.sub(_stash, text)
    work = _INLINE_CODE_RE.sub(_stash, work)
    log.slack.debug(
        "[slack] to_slack_mrkdwn: decision protected code segments",
        extra={"_fields": {"protected_count": len(protected)}},
    )

    # 2. Convert markup on the unprotected remainder. Order matters:
    #    headers per-line; then bold is STASHED (not converted in place) so the
    #    single-asterisk italic pass cannot mistake a bold ``*`` for italic
    #    (CHAN-2 / F008); then strike, link, and finally single-asterisk italic.
    def _stash_bold(match: re.Match[str]) -> str:
        # Stash the rendered ``*bold*`` so the italic regex below never sees it.
        protected.append(f"*{match.group(1)}*")
        return f"{_PLACEHOLDER}{len(protected) - 1}{_PLACEHOLDER}"

    # Headers render as bold and are stashed too — the single-asterisk italic
    # pass must not re-read the emitted ``*Header*`` as italic (CHAN-2).
    work = _HEADER_RE.sub(_stash_bold, work)
    work = _BOLD_STAR_RE.sub(_stash_bold, work)
    work = _BOLD_UNDER_RE.sub(_stash_bold, work)
    work = _STRIKE_RE.sub(r"~\1~", work)
    work = _LINK_RE.sub(r"<\2|\1>", work)
    work = _ITALIC_STAR_RE.sub(r"_\1_", work)
    log.slack.debug("[slack] to_slack_mrkdwn: step markup converted")

    # 3. Restore the protected code segments verbatim.
    def _restore(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        # Fail-safe: an index with no protected segment means the placeholder
        # pattern did not originate from _stash. Leave the literal match in
        # place rather than indexing out of range (belt-and-suspenders with the
        # entry strip above).
        if 0 <= idx < len(protected):
            return protected[idx]
        log.slack.debug(
            "[slack] to_slack_mrkdwn: step restore index out of range, keeping literal",
            extra={"_fields": {"idx": idx, "protected_count": len(protected)}},
        )
        return match.group(0)

    restore_re = re.compile(
        f"{_PLACEHOLDER}(\\d+){_PLACEHOLDER}", re.UNICODE
    )
    result = restore_re.sub(_restore, work)
    log.slack.debug(
        "[slack] to_slack_mrkdwn: exit",
        extra={"_fields": {"result_len": len(result)}},
    )
    return result


# --------------------------------------------------------------------------- #
# Block Kit element models — frozen Pydantic so structure is enforced.
# --------------------------------------------------------------------------- #


class PlainText(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["plain_text"] = "plain_text"
    text: str
    emoji: bool = True


class HeaderBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["header"] = "header"
    text: PlainText


class SectionBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["section"] = "section"
    text: PlainText


class DividerBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["divider"] = "divider"


class ButtonElement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["button"] = "button"
    text: PlainText
    action_id: str
    value: str = ""


class ActionsBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["actions"] = "actions"
    elements: list[ButtonElement]


# --------------------------------------------------------------------------- #
# Formatter
# --------------------------------------------------------------------------- #


def _section(body: str) -> SectionBlock:
    return SectionBlock(text=PlainText(text=body))


def _header(label_key: str) -> HeaderBlock:
    return HeaderBlock(text=PlainText(text=localize(label_key, lang="en")))


def _interleave_dividers(blocks: list[BaseModel]) -> list[BaseModel]:
    """Insert a DividerBlock between adjacent blocks."""
    if len(blocks) <= 1:
        return blocks
    interleaved: list[BaseModel] = []
    for idx, block in enumerate(blocks):
        if idx > 0:
            interleaved.append(DividerBlock())
        interleaved.append(block)
    return interleaved


class SlackBlockKitFormatter:
    """Builds Slack Block Kit message payloads for owl, parliament, and brief outputs."""

    def format_parliament_synthesis(
        self, synthesis: str, owls: list[str]
    ) -> list[dict[str, object]]:
        """Render a parliament synthesis as Block Kit blocks.

        Each non-empty paragraph becomes its own SectionBlock; dividers
        separate them so screen-reader users hear a clear cadence.
        """
        log.slack.debug(
            "[slack] formatter.format_parliament_synthesis: entry",
            extra={"_fields": {"text_len": len(synthesis), "owl_count": len(owls)}},
        )
        paragraphs = [p.strip() for p in synthesis.split("\n\n") if p.strip()]
        log.slack.debug(
            "[slack] formatter.format_parliament_synthesis: decision split",
            extra={"_fields": {"paragraph_count": len(paragraphs)}},
        )
        blocks: list[BaseModel] = [_header("slack.parliament.synthesis_header")]
        section_blocks: list[BaseModel] = [_section(p) for p in paragraphs]
        blocks.extend(_interleave_dividers(section_blocks))
        log.slack.debug(
            "[slack] formatter.format_parliament_synthesis: step rendered",
            extra={"_fields": {"block_count": len(blocks)}},
        )
        dumped = [b.model_dump() for b in blocks]
        log.slack.debug(
            "[slack] formatter.format_parliament_synthesis: exit",
            extra={"_fields": {"block_count": len(dumped)}},
        )
        return dumped

    def format_morning_brief(self, sections: list[str]) -> list[dict[str, object]]:
        """Render the morning brief — one SectionBlock per section, dividers between."""
        log.slack.debug(
            "[slack] formatter.format_morning_brief: entry",
            extra={"_fields": {"section_count": len(sections)}},
        )
        blocks: list[BaseModel] = [_header("slack.brief.header")]
        section_blocks: list[BaseModel] = [
            _section(body) for body in sections if body.strip()
        ]
        log.slack.debug(
            "[slack] formatter.format_morning_brief: decision sections kept",
            extra={"_fields": {"kept": len(section_blocks)}},
        )
        blocks.extend(_interleave_dividers(section_blocks))
        dumped = [b.model_dump() for b in blocks]
        log.slack.debug(
            "[slack] formatter.format_morning_brief: exit",
            extra={"_fields": {"block_count": len(dumped)}},
        )
        return dumped

    def format_memory_nudge(
        self, fact_id: str, content: str
    ) -> list[dict[str, object]]:
        """Render a memory-promotion nudge with approve/reject buttons.

        The button ``action_id`` carries the FULL ``fact_id`` so the action
        router can route it straight back to the memory bridge, which exact-
        matches the full UUID. (A truncated prefix never matches and would
        silently no-op the promote/delete.) Slack permits action_ids up to 255
        chars, so a 36-char UUID fits comfortably.
        """
        log.slack.debug(
            "[slack] formatter.format_memory_nudge: entry",
            extra={"_fields": {"fact_id_short": fact_id[:8], "content_len": len(content)}},
        )
        approve = ButtonElement(
            text=PlainText(text=localize("slack.memory.approve", lang="en")),
            action_id=f"memory_approve_{fact_id}",
            value=fact_id,
        )
        reject = ButtonElement(
            text=PlainText(text=localize("slack.memory.reject", lang="en")),
            action_id=f"memory_reject_{fact_id}",
            value=fact_id,
        )
        blocks: list[BaseModel] = [
            _section(content),
            ActionsBlock(elements=[approve, reject]),
        ]
        log.slack.debug(
            "[slack] formatter.format_memory_nudge: step assembled",
            extra={"_fields": {"block_count": len(blocks)}},
        )
        dumped = [b.model_dump() for b in blocks]
        log.slack.debug(
            "[slack] formatter.format_memory_nudge: exit",
            extra={"_fields": {"block_count": len(dumped)}},
        )
        return dumped
