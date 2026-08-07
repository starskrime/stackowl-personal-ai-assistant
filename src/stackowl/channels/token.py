"""Channel credential resolution — the seam channel adapters were missing.

THE BUG THIS EXISTS FOR, found live 2026-08-07 when a Telegram message got no
reply and nothing in the logs said why:

``telegram_channel.bot_token`` held ``file:/…`` — a SecretResolver reference,
exactly the form ``config/secret_writer.py`` is designed to WRITE. The referenced
file existed and contained a perfectly valid 46-character token. But
``TelegramChannelAdapter.start`` passed ``self._settings.bot_token`` straight to
the Bot API, so the platform authenticated with the literal string
``file:/…`` and Telegram answered 404 to every poll, forever, in silence.

The startup log said ``bot_token_present: true, bot_token_len: 51`` — a
PRESENCE check reported as if it were a validity check, which is the
assert-don't-measure shape ADR-19 is about. The adapter logged "Telegram adapter
started" and "telegram loop started" with zero errors while being completely deaf.

WHY IT WAS ONLY THE CHANNELS. Provider ``api_key`` is resolved
(``providers/registry.py``), webhook ``secret`` is resolved
(``webhooks/receiver_helpers.py``) — but no channel adapter resolved anything.
Telegram, Slack and Discord all read their token raw, so the defect was never
Telegram-specific: any operator storing a channel credential the way
``secret_writer`` writes it got a silently dead channel.

WHY A SHARED HELPER RATHER THAN THREE FIXES. Fixing the one that was reported
would leave the identical bug in the other two, waiting for whoever enables them
next.
"""

from __future__ import annotations

from stackowl.config.secret_resolver import SecretResolver
from stackowl.exceptions import ConfigurationError
from stackowl.infra.observability import log

__all__ = ["resolve_channel_token"]

#: Reference schemes SecretResolver understands. A value starting with one of
#: these is an INDIRECTION, never the credential itself.
_REFERENCE_PREFIXES = ("file:", "env:", "keychain:")


def resolve_channel_token(raw: str | None, *, channel: str) -> str:
    """Resolve a channel credential that may be a SecretResolver reference.

    Returns the literal value unchanged when it is not a reference, so every
    deployment that stores the token inline is byte-identical to before.

    Raises :class:`ConfigurationError` when a reference cannot be resolved.
    FAIL LOUD is the whole point: the failure this replaces was a channel that
    started cleanly, reported itself healthy, and silently received nothing. An
    unusable credential must stop startup with a readable message, not become a
    deaf adapter nobody can tell apart from a quiet day.
    """
    if not raw:
        return ""
    if not raw.startswith(_REFERENCE_PREFIXES):
        return raw
    try:
        resolved = SecretResolver.resolve(raw)
    except Exception as exc:
        # The reference itself is safe to name — it is a pointer, not a secret —
        # and naming it is what makes this diagnosable in one read.
        scheme = raw.split(":", 1)[0]
        log.telegram.error(
            "[channels] token resolution FAILED — the channel cannot authenticate",
            exc_info=exc,
            extra={"_fields": {"channel": channel, "scheme": scheme}},
        )
        raise ConfigurationError(
            f"{channel}: could not resolve its credential reference "
            f"({scheme}:…) — {exc}"
        ) from exc
    if not resolved:
        raise ConfigurationError(
            f"{channel}: credential reference resolved to an EMPTY value"
        )
    log.telegram.info(
        "[channels] resolved a credential reference",
        extra={"_fields": {
            "channel": channel,
            "scheme": raw.split(":", 1)[0],
            # Length only, never the value — and length is what proves the
            # resolution actually happened (the reference and the secret are
            # different sizes).
            "resolved_len": len(resolved),
        }},
    )
    return resolved
