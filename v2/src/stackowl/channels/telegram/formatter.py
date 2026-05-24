"""TelegramMarkdownFormatter and specialised formatters for the Telegram adapter.

Telegram's Bot API MarkdownV2 mode requires escaping 18 reserved characters.
All formatters in this module produce MarkdownV2-safe output. User-facing
string labels are obtained exclusively via :func:`localize` — no hardcoded
English output.
"""

from __future__ import annotations

import re

from stackowl.infra.observability import log
from stackowl.tui.i18n import localize

__all__ = [
    "TelegramBriefFormatter",
    "TelegramEvolutionFormatter",
    "TelegramMarkdownFormatter",
    "TelegramMemoryFormatter",
    "TelegramParliamentFormatter",
    "escape_md",
]

# MarkdownV2 reserved characters (Telegram Bot API specification).
# Compiled once at module load; never re-instantiated per call.
_MD_ESCAPE_RE = re.compile(r"([_*\[\]()~`#\+\-=|{}.!\\])")


def escape_md(text: str) -> str:
    """Escape all MarkdownV2 reserved characters in ``text``.

    Telegram MarkdownV2 requires every reserved character to be preceded by a
    backslash. This function uses a single compiled regex for efficiency and
    correctness.

    Args:
        text: Arbitrary text, possibly containing MarkdownV2 special chars.

    Returns:
        Text safe for use as a MarkdownV2 message body.
    """
    return _MD_ESCAPE_RE.sub(r"\\\1", text)


class TelegramMarkdownFormatter:
    """Converts internal StackOwl responses to Telegram MarkdownV2."""

    def format_response(self, text: str) -> str:
        """Escape ``text`` for safe MarkdownV2 delivery.

        4-point logging: entry / decision / step / exit.
        """
        log.telegram.debug(
            "[telegram] formatter.format_response: entry",
            extra={"_fields": {"text_len": len(text)}},
        )
        result = escape_md(text)
        log.telegram.debug(
            "[telegram] formatter.format_response: decision escape_only",
            extra={"_fields": {"result_len": len(result)}},
        )
        log.telegram.debug(
            "[telegram] formatter.format_response: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result

    def format_plain(self, text: str) -> str:
        """Escape everything in ``text`` for safe MarkdownV2 send.

        Identical to :meth:`format_response`; provided as a named alias so
        callers can distinguish intent at the call site.
        """
        log.telegram.debug(
            "[telegram] formatter.format_plain: entry",
            extra={"_fields": {"text_len": len(text)}},
        )
        result = escape_md(text)
        log.telegram.debug(
            "[telegram] formatter.format_plain: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result


class TelegramParliamentFormatter:
    """Formats Parliament session output for Telegram delivery."""

    def format_synthesis(
        self,
        synthesis: str,
        owl_names: list[str],
        round_count: int,
    ) -> str:
        """Render a Parliament synthesis as a MarkdownV2 message.

        4-point logging: entry / decision / step / exit.

        Args:
            synthesis: The Parliament synthesis text.
            owl_names: Names of owls that participated.
            round_count: Number of rounds completed.

        Returns:
            MarkdownV2-safe formatted string.
        """
        log.telegram.debug(
            "[telegram] parliament_formatter.format_synthesis: entry",
            extra={"_fields": {"owl_count": len(owl_names), "rounds": round_count}},
        )
        header_label = localize("telegram.parliament.synthesis_header")
        owls_label = localize("telegram.parliament.owls_label")
        rounds_label = localize("telegram.parliament.rounds_label")

        log.telegram.debug(
            "[telegram] parliament_formatter.format_synthesis: decision build_sections",
            extra={"_fields": {"has_owl_names": bool(owl_names)}},
        )

        header_line = f"*{escape_md(header_label)}*"
        owls_line = f"{escape_md(owls_label)}: {escape_md(', '.join(owl_names))}"
        rounds_line = f"{escape_md(rounds_label)}: {escape_md(str(round_count))}"
        body = escape_md(synthesis)

        result = "\n".join([header_line, owls_line, rounds_line, "", body])
        log.telegram.debug(
            "[telegram] parliament_formatter.format_synthesis: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result


class TelegramBriefFormatter:
    """Formats morning brief output for Telegram delivery."""

    def format_morning_brief(self, sections: dict[str, str]) -> str:
        """Render the morning brief as a single MarkdownV2 message.

        4-point logging: entry / decision / step / exit.

        Args:
            sections: Mapping of section title to section body text.

        Returns:
            MarkdownV2-safe formatted string.
        """
        log.telegram.debug(
            "[telegram] brief_formatter.format_morning_brief: entry",
            extra={"_fields": {"section_count": len(sections)}},
        )
        header_label = localize("telegram.brief.header")
        log.telegram.debug(
            "[telegram] brief_formatter.format_morning_brief: decision build_header",
            extra={"_fields": {"header_label": header_label}},
        )

        lines: list[str] = [f"*{escape_md(header_label)}*", ""]
        for title, body in sections.items():
            lines.append(f"*{escape_md(title)}*")
            lines.append(escape_md(body))
            lines.append("")

        result = "\n".join(lines).rstrip()
        log.telegram.debug(
            "[telegram] brief_formatter.format_morning_brief: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result


class TelegramMemoryFormatter:
    """Formats memory-suggestion nudges with inline keyboards."""

    def format_memory_nudge(
        self,
        fact_content: str,
        fact_id: str,
    ) -> tuple[str, dict[str, object]]:
        """Render a memory suggestion with approve/reject keyboard.

        4-point logging: entry / decision / step / exit.

        Args:
            fact_content: The candidate fact text to suggest.
            fact_id: Stable identifier used in callback_data.

        Returns:
            ``(text, keyboard)`` where text is MarkdownV2-safe and keyboard
            is a dict compatible with Telegram's InlineKeyboardMarkup JSON.
        """
        log.telegram.debug(
            "[telegram] memory_formatter.format_memory_nudge: entry",
            extra={"_fields": {"fact_id": fact_id, "fact_len": len(fact_content)}},
        )
        header_label = localize("telegram.memory.header")
        approve_label = localize("telegram.memory.approve")
        reject_label = localize("telegram.memory.reject")

        log.telegram.debug(
            "[telegram] memory_formatter.format_memory_nudge: decision build_keyboard",
            extra={"_fields": {"fact_id": fact_id}},
        )

        text = f"*{escape_md(header_label)}*\n{escape_md(fact_content)}"
        keyboard: dict[str, object] = {
            "inline_keyboard": [
                [
                    {
                        "text": approve_label,
                        "callback_data": f"mem:approve:{fact_id}",
                    },
                    {
                        "text": reject_label,
                        "callback_data": f"mem:reject:{fact_id}",
                    },
                ]
            ]
        }

        log.telegram.debug(
            "[telegram] memory_formatter.format_memory_nudge: exit",
            extra={"_fields": {"text_len": len(text), "fact_id": fact_id}},
        )
        return text, keyboard


class TelegramEvolutionFormatter:
    """Formats owl evolution events for Telegram delivery."""

    def format_evolution_event(
        self,
        owl_name: str,
        trait_deltas: dict[str, float],
    ) -> str:
        """Render an owl-evolution delta summary as MarkdownV2.

        4-point logging: entry / decision / step / exit.

        Args:
            owl_name: Name of the owl whose DNA mutated.
            trait_deltas: Mapping of trait name to signed delta value.

        Returns:
            MarkdownV2-safe string showing which traits changed.
        """
        log.telegram.debug(
            "[telegram] evolution_formatter.format_evolution_event: entry",
            extra={"_fields": {"owl_name": owl_name, "trait_count": len(trait_deltas)}},
        )
        header_label = localize("telegram.evolution.header")
        log.telegram.debug(
            "[telegram] evolution_formatter.format_evolution_event: decision format_deltas",
            extra={"_fields": {"has_deltas": bool(trait_deltas)}},
        )

        lines: list[str] = [f"*{escape_md(header_label)}*"]
        lines.append(escape_md(owl_name))
        for trait, delta in trait_deltas.items():
            sign = "+" if delta >= 0 else ""
            lines.append(escape_md(f"  {trait}: {sign}{delta:.3f}"))

        result = "\n".join(lines)
        log.telegram.debug(
            "[telegram] evolution_formatter.format_evolution_event: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result
