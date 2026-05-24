"""Pure helpers and DiscordMarkdownFormatter for the Discord channel adapter.

All user-facing strings flow through :func:`localize` so the platform stays
multilingual; no English literals are embedded.
"""

from __future__ import annotations

import hashlib
import re

from stackowl.infra.observability import log
from stackowl.tui.i18n import localize

__all__ = [
    "DiscordMarkdownFormatter",
    "hash_user_id",
    "is_authorized",
    "strip_bot_mention",
]


def hash_user_id(user_id: int) -> str:
    """Return the first 8 hex chars of sha256(user_id) — safe to log.

    Never log the raw Discord user_id; always pass it through this helper
    before recording.
    """
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:8]


def is_authorized(user_id: int, allowed_ids: list[int]) -> bool:
    """Membership check against the bot's allow-list (fail-closed)."""
    return user_id in allowed_ids


def strip_bot_mention(text: str, bot_id: int) -> str:
    """Remove ``<@bot_id>`` or ``<@!bot_id>`` mentions from text and trim.

    Discord renders mentions as ``<@123>`` (plain) or ``<@!123>`` (nickname
    form). The adapter strips both before forwarding to the gateway so the
    routing layer never sees them.
    """
    pattern = rf"<@!?{bot_id}>"
    return re.sub(pattern, "", text).strip()


_FENCE_RE = re.compile(r"```[\s\S]*?```")


class DiscordMarkdownFormatter:
    """Converts internal StackOwl responses to Discord-flavoured Markdown.

    Discord's Markdown dialect is close to CommonMark with a few differences:
    ``**bold**``, ``*italic*``, ``` `code` ``` and fenced ``` ```lang …``` ```
    blocks are honoured natively, so most StackOwl responses pass through
    unchanged.
    """

    def format_response(self, text: str) -> str:
        """Pass-through formatter — preserves fenced code blocks verbatim.

        4-point logging: entry / decision / step / exit.
        """
        log.discord.debug(
            "[discord] formatter.format_response: entry",
            extra={"_fields": {"text_len": len(text)}},
        )
        fences = _FENCE_RE.findall(text)
        log.discord.debug(
            "[discord] formatter.format_response: decision preserve_fences",
            extra={"_fields": {"fence_count": len(fences)}},
        )
        # Discord already understands StackOwl's Markdown; nothing to rewrite.
        result = text
        log.discord.debug(
            "[discord] formatter.format_response: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result

    def format_parliament_synthesis(self, synthesis: str) -> str:
        """Bold each ``Round N`` header and wrap the synthesis block."""
        log.discord.debug(
            "[discord] formatter.format_parliament_synthesis: entry",
            extra={"_fields": {"text_len": len(synthesis)}},
        )
        bolded = re.sub(
            r"(?m)^(Round\s+\d+)\s*[:\-]?\s*$",
            r"**\1:**",
            synthesis,
        )
        header = localize("discord.parliament.synthesis_header")
        rendered = f"**{header}**\n{bolded}"
        log.discord.debug(
            "[discord] formatter.format_parliament_synthesis: exit",
            extra={"_fields": {"result_len": len(rendered)}},
        )
        return rendered

    def format_morning_brief(self, sections: list[str]) -> list[str]:
        """Render the morning brief as one Discord message per section.

        Each message is prefixed with a bold header keyed by section index so
        translations can swap the label per locale.
        """
        log.discord.debug(
            "[discord] formatter.format_morning_brief: entry",
            extra={"_fields": {"section_count": len(sections)}},
        )
        messages: list[str] = []
        for idx, body in enumerate(sections, start=1):
            header = localize(f"discord.brief.section_{idx}")
            messages.append(f"**{header}**\n{body}")
        log.discord.debug(
            "[discord] formatter.format_morning_brief: exit",
            extra={"_fields": {"message_count": len(messages)}},
        )
        return messages

    def format_evolution_notification(
        self, owl_name: str, trait_changes: list[tuple[str, float, float]]
    ) -> str:
        """Render a compact one-line owl-evolution notice.

        Args:
            owl_name: Name of the owl whose DNA mutated.
            trait_changes: ``[(trait_name, old_value, new_value), …]``.
        """
        log.discord.debug(
            "[discord] formatter.format_evolution_notification: entry",
            extra={"_fields": {"owl_name": owl_name, "change_count": len(trait_changes)}},
        )
        prefix = localize("discord.evolution.prefix")
        verb = localize("discord.evolution.verb_evolved")
        parts = [f"{name} {old:.2f}→{new:.2f}" for name, old, new in trait_changes]
        line = f"{prefix} {owl_name} {verb}: " + ", ".join(parts)
        log.discord.debug(
            "[discord] formatter.format_evolution_notification: exit",
            extra={"_fields": {"line_len": len(line)}},
        )
        return line
