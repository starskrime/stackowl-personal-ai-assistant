"""Pure helpers and Block Kit formatter for the Slack channel adapter.

All user-facing strings flow through :func:`localize` so the platform stays
multilingual; no English literals are embedded directly.
"""

from __future__ import annotations

import hashlib
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
