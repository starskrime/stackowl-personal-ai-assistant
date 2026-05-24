"""Pure helpers and WhatsAppMarkdownFormatter for the WhatsApp channel adapter.

All user-facing strings flow through :func:`localize` so the platform stays
multilingual; no English literals are embedded.

Security note: phone numbers and JIDs (``phone@s.whatsapp.net``) are never
logged raw — always pass through :func:`hash_jid` before recording.
"""

from __future__ import annotations

import hashlib
import re

from stackowl.infra.observability import log
from stackowl.tui.i18n import localize

__all__ = [
    "WhatsAppMarkdownFormatter",
    "hash_jid",
    "is_authorized",
    "normalize_phone",
]

# WhatsApp JID format: phone@s.whatsapp.net or group@g.us
_JID_PHONE_RE = re.compile(r"^(\+?\d+)@", re.ASCII)


def hash_jid(jid: str) -> str:
    """Return the first 8 hex chars of sha256(jid) — safe to log.

    Never log the raw WhatsApp JID or phone number; always pass it through
    this helper before recording.
    """
    return hashlib.sha256(jid.encode()).hexdigest()[:8]


def normalize_phone(phone: str) -> str:
    """Strip all non-digit characters and return a digits-only phone string.

    The result is suitable for membership checks against the E.164 allowlist
    (which is also stored as digits after stripping the leading ``+``).
    """
    return re.sub(r"\D", "", phone)


def is_authorized(jid: str, allowed: frozenset[str]) -> bool:
    """Check whether the JID's phone number is in the allow-list (fail-closed).

    An empty frozenset denies all senders. The JID phone prefix is extracted
    and compared after stripping non-digit characters so ``+1234`` and
    ``1234`` both match.
    """
    if not allowed:
        return False

    m = _JID_PHONE_RE.match(jid)
    if not m:
        log.whatsapp.warning(
            "[whatsapp] helpers.is_authorized: malformed jid — denying",
            extra={"_fields": {"jid_hash": hash_jid(jid)}},
        )
        return False

    phone_digits = normalize_phone(m.group(1))
    for allowed_number in allowed:
        if normalize_phone(allowed_number) == phone_digits:
            return True
    return False


class WhatsAppMarkdownFormatter:
    """Converts internal StackOwl responses to WhatsApp-flavoured Markdown.

    WhatsApp uses a custom subset of Markdown:
    - ``*text*`` → bold
    - ``_text_`` → italic
    - ``~text~`` → strikethrough
    - ``` ```text``` ``` → monospace

    We use a conservative pass-through approach because WhatsApp's formatting
    is opt-in and most plain-text responses look correct without transformation.
    """

    def format_response(self, text: str) -> str:
        """Pass-through formatter — returns text unchanged.

        WhatsApp's Markdown syntax is opt-in; plain text with no formatting
        markers renders cleanly, so we preserve the input verbatim.

        4-point logging: entry / decision / step / exit.
        """
        log.whatsapp.debug(
            "[whatsapp] formatter.format_response: entry",
            extra={"_fields": {"text_len": len(text)}},
        )
        # Decision: WhatsApp already understands plain text and opt-in Markdown.
        # Avoid transforming characters that might create unintended formatting.
        log.whatsapp.debug(
            "[whatsapp] formatter.format_response: decision passthrough",
            extra={"_fields": {"text_len": len(text)}},
        )
        result = text
        log.whatsapp.debug(
            "[whatsapp] formatter.format_response: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result

    def format_morning_brief(self, sections: dict[str, str]) -> str:
        """Render the morning brief with bold section titles.

        Args:
            sections: Ordered mapping of section title → section body.

        Returns:
            A single string with ``*title*\\n body`` blocks joined by blank lines.
        """
        log.whatsapp.debug(
            "[whatsapp] formatter.format_morning_brief: entry",
            extra={"_fields": {"section_count": len(sections)}},
        )
        header_key = localize("whatsapp.brief.header")
        parts: list[str] = [f"*{header_key}*"]
        for title, body in sections.items():
            parts.append(f"*{title}*\n{body}")
        result = "\n\n".join(parts)
        log.whatsapp.debug(
            "[whatsapp] formatter.format_morning_brief: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result

    def format_parliament_synthesis(self, synthesis: str, owl_names: list[str]) -> str:
        """Render a Parliament synthesis with owl names bolded in the header.

        Args:
            synthesis: The synthesized parliament output.
            owl_names: Names of owls that participated.

        Returns:
            Formatted string with owl names and synthesis content.
        """
        log.whatsapp.debug(
            "[whatsapp] formatter.format_parliament_synthesis: entry",
            extra={"_fields": {"text_len": len(synthesis), "owl_count": len(owl_names)}},
        )
        header_key = localize("whatsapp.parliament.synthesis_header")
        owls_str = ", ".join(f"*{name}*" for name in owl_names)
        header = f"*{header_key}* ({owls_str})"
        result = f"{header}\n\n{synthesis}"
        log.whatsapp.debug(
            "[whatsapp] formatter.format_parliament_synthesis: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result
