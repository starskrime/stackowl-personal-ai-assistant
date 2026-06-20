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
    "to_telegram_markdownv2",
]

# MarkdownV2 reserved characters (Telegram Bot API specification).
# Compiled once at module load; never re-instantiated per call.
_MD_ESCAPE_RE = re.compile(r"([_*\[\]()~`#\+\-=|{}.!\\])")
# Inside a MarkdownV2 link/code URL only ``)`` and ``\`` are special and must be
# escaped (Bot API spec); the rest of the URL stays verbatim.
_MD_URL_ESCAPE_RE = re.compile(r"([)\\])")


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


# --------------------------------------------------------------------------- #
# GFM → Telegram MarkdownV2 conversion (CHAN-1 / F009)
# --------------------------------------------------------------------------- #
#
# The assistant emits GitHub-flavored Markdown (GFM); Telegram renders
# *MarkdownV2*, where bold is ``*text*`` (GFM ``**text**``), italic ``_text_``
# (GFM ``_text_`` or ``*text*``), strike ``~text~`` (GFM ``~~text~~``), inline
# link ``[text](url)`` (same surface form as GFM), and 18 reserved characters
# (``_*[]()~`>#+-=|{}.!``) MUST be backslash-escaped *outside* markup or the API
# rejects the send with HTTP 400.
#
# Strategy mirrors ``slack.helpers.to_slack_mrkdwn``: protect code spans/fences
# AND the converted markup spans (bold/italic/strike/link) to placeholders FIRST
# so their contents are never escaped, then escape every reserved char in the
# remaining plain text, then restore the protected spans verbatim. This is the
# inverse of the old ``escape_md``-everything behaviour that flattened all
# markup to literal backslashed characters (F009).

# Order matters: fences before inline code; ``**``/``__`` bold before single-
# delimiter italic so ``**`` is not mis-read as two italic markers; ``~~`` strike
# before any single ``~``. Links last (their brackets/parens are not markup).
_GFM_FENCE_RE = re.compile(r"```.*?```", re.DOTALL | re.UNICODE)
_GFM_INLINE_CODE_RE = re.compile(r"`[^`]*`", re.UNICODE)
_GFM_BOLD_STAR_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL | re.UNICODE)
_GFM_BOLD_UNDER_RE = re.compile(r"__(.+?)__", re.DOTALL | re.UNICODE)
_GFM_ITALIC_STAR_RE = re.compile(r"\*(.+?)\*", re.UNICODE)
_GFM_ITALIC_UNDER_RE = re.compile(r"_(.+?)_", re.UNICODE)
_GFM_STRIKE_RE = re.compile(r"~~(.+?)~~", re.UNICODE)
_GFM_LINK_RE = re.compile(r"\[([^\]]+)\]\((\S+?)\)", re.UNICODE)
_GFM_HEADER_RE = re.compile(
    r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.UNICODE
)

# Private-use sentinel; stripped from input on entry so it can never collide
# with real content (same neutralization as the Slack converter).
_TG_PLACEHOLDER = ""


def to_telegram_markdownv2(text: str) -> str:
    """Convert assistant GitHub-flavored Markdown to Telegram MarkdownV2.

    Conversions applied OUTSIDE code spans/fences:

    * ``**bold**`` / ``__bold__`` → ``*bold*``
    * ``*italic*`` / ``_italic_`` → ``_italic_``
    * ``~~strike~~`` → ``~strike~``
    * ``[text](url)`` → MarkdownV2 link ``[text](url)`` (url ``)``/``\\`` escaped)
    * GFM ATX headers (``# H`` … ``###### H``) → ``*H*`` (Telegram has no headers)

    Every reserved MarkdownV2 character in the remaining PLAIN text is escaped
    (so a stray ``.``/``+``/``-`` can never 400), while the emitted markup
    delimiters and code spans are preserved verbatim. Replaces the old
    escape-everything behaviour that rendered the assistant's markup as literal
    backslashed characters (F009).
    """
    log.telegram.debug(
        "[telegram] to_telegram_markdownv2: entry",
        extra={"_fields": {"text_len": len(text)}},
    )
    if not text:
        return text

    if _TG_PLACEHOLDER in text:
        stripped = text.count(_TG_PLACEHOLDER)
        text = text.replace(_TG_PLACEHOLDER, "")
        log.telegram.debug(
            "[telegram] to_telegram_markdownv2: step stripped sentinel from input",
            extra={"_fields": {"stripped_count": stripped}},
        )

    from stackowl.channels._format import flatten_gfm_tables
    text = flatten_gfm_tables(text)

    protected: list[str] = []

    def _stash(rendered: str) -> str:
        protected.append(rendered)
        return f"{_TG_PLACEHOLDER}{len(protected) - 1}{_TG_PLACEHOLDER}"

    def _stash_code(match: re.Match[str]) -> str:
        # Code is preserved byte-for-byte (including any ``**``/``_`` inside it).
        return _stash(match.group(0))

    def _stash_bold(match: re.Match[str]) -> str:
        return _stash(f"*{escape_md(match.group(1))}*")

    def _stash_italic(match: re.Match[str]) -> str:
        return _stash(f"_{escape_md(match.group(1))}_")

    def _stash_strike(match: re.Match[str]) -> str:
        return _stash(f"~{escape_md(match.group(1))}~")

    def _stash_link(match: re.Match[str]) -> str:
        label = escape_md(match.group(1))
        url = _MD_URL_ESCAPE_RE.sub(r"\\\1", match.group(2))
        return _stash(f"[{label}]({url})")

    # 1. Protect code first so its contents are never re-matched/escaped.
    work = _GFM_FENCE_RE.sub(_stash_code, text)
    work = _GFM_INLINE_CODE_RE.sub(_stash_code, work)
    # 2. Headers → bold (per line), then bold/strike/italic/link → placeholders
    #    holding already-rendered, inner-escaped MarkdownV2 markup.
    work = _GFM_HEADER_RE.sub(lambda m: _stash(f"*{escape_md(m.group(1))}*"), work)
    work = _GFM_BOLD_STAR_RE.sub(_stash_bold, work)
    work = _GFM_BOLD_UNDER_RE.sub(_stash_bold, work)
    work = _GFM_STRIKE_RE.sub(_stash_strike, work)
    work = _GFM_LINK_RE.sub(_stash_link, work)
    work = _GFM_ITALIC_STAR_RE.sub(_stash_italic, work)
    work = _GFM_ITALIC_UNDER_RE.sub(_stash_italic, work)
    log.telegram.debug(
        "[telegram] to_telegram_markdownv2: decision protected spans",
        extra={"_fields": {"protected_count": len(protected)}},
    )

    # 3. Escape every reserved char in the remaining PLAIN text. The placeholder
    #    sentinel is not a reserved char, so it survives; its digits are escaped
    #    by escape_md only if reserved — digits are not, so the index is intact.
    work = escape_md(work)

    # 4. Restore protected spans verbatim (rendered markup + code).
    restore_re = re.compile(
        f"{_TG_PLACEHOLDER}(\\d+){_TG_PLACEHOLDER}", re.UNICODE
    )

    def _restore(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        if 0 <= idx < len(protected):
            return protected[idx]
        log.telegram.debug(
            "[telegram] to_telegram_markdownv2: step restore index out of range",
            extra={"_fields": {"idx": idx, "protected_count": len(protected)}},
        )
        return match.group(0)

    result = restore_re.sub(_restore, work)
    log.telegram.debug(
        "[telegram] to_telegram_markdownv2: exit",
        extra={"_fields": {"result_len": len(result)}},
    )
    return result


class TelegramMarkdownFormatter:
    """Converts internal StackOwl responses to Telegram MarkdownV2."""

    def format_response(self, text: str) -> str:
        """Render assistant GFM as Telegram MarkdownV2 (CHAN-1 / F009).

        The assistant emits GitHub-flavored Markdown; this converts it so
        bold/italic/links RENDER instead of arriving as literal backslashed
        characters, while still escaping every reserved char outside markup so a
        formatted reply never triggers an HTTP 400. Code spans/fences are
        preserved verbatim. Use :meth:`format_plain` for untrusted text that must
        stay literal.

        4-point logging: entry / decision / step / exit.
        """
        log.telegram.debug(
            "[telegram] formatter.format_response: entry",
            extra={"_fields": {"text_len": len(text)}},
        )
        result = to_telegram_markdownv2(text)
        log.telegram.debug(
            "[telegram] formatter.format_response: decision gfm_converted",
            extra={"_fields": {"result_len": len(result)}},
        )
        log.telegram.debug(
            "[telegram] formatter.format_response: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result

    def format_plain(self, text: str) -> str:
        """Escape everything in ``text`` for safe MarkdownV2 send.

        The literal-text path: every reserved char is backslash-escaped so the
        text renders verbatim with NO markup interpretation (unlike
        :meth:`format_response`, which converts GFM markup). Use this for
        untrusted/echoed text where ``**x**`` must stay literal.
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
