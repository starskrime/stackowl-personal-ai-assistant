"""Pure helpers for the Telegram channel adapter.

All user-facing strings flow through :func:`localize` so the platform stays
multilingual; no English literals are embedded in output.
"""

from __future__ import annotations

import hashlib
import re

__all__ = [
    "hash_user_id",
    "is_authorized",
    "strip_bot_mention",
    "strip_command_bot_suffix",
]


def hash_user_id(user_id: int) -> str:
    """Return the first 8 hex chars of sha256(user_id) — safe to log.

    Never log the raw Telegram user_id; always pass it through this helper
    before recording to logs.
    """
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:8]


def is_authorized(user_id: int, allowed: frozenset[int]) -> bool:
    """Membership check against the bot's allow-list (fail-closed).

    An empty frozenset denies all users, including user_id 0.
    """
    if not allowed:
        return False
    return user_id in allowed


def strip_command_bot_suffix(text: str, bot_username: str | None) -> str:
    """Turn a leading "/cmd@BotName" into "/cmd" (Telegram group convention).

    Only touches a command token at the very start of the message; ordinary text
    containing "@BotName" is left alone.
    """
    if not bot_username or not text.startswith("/"):
        return text
    head, sep, rest = text.partition(" ")
    suffix = f"@{bot_username}"
    if head.endswith(suffix):
        head = head[: -len(suffix)]
    return head + sep + rest


def strip_bot_mention(text: str, bot_username: str) -> str:
    """Remove ``@bot_username`` prefix from text and strip whitespace.

    The removal is case-insensitive. Only the leading mention is removed;
    mentions elsewhere in the message are preserved.

    Args:
        text: Raw message text from Telegram.
        bot_username: The bot's username without the leading ``@``.

    Returns:
        Text with the leading bot mention removed and whitespace stripped.
    """
    if not bot_username:
        return text.strip()
    pattern = re.compile(rf"^@{re.escape(bot_username)}\s*", re.IGNORECASE)
    return pattern.sub("", text).strip()
