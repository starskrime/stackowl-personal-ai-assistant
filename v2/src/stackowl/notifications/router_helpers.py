"""Pure helpers for :class:`NotificationRouter` — quiet-hours math + persistence.

Kept in a separate module so :mod:`stackowl.notifications.router` stays under
the B2 300-line cap.  All functions are stateless and unit-testable on their own.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from stackowl.config.notification_settings import QuietHoursSettings
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool

_INSERT_LOG_SQL = (
    "INSERT INTO notification_log "
    "(notification_id, urgency, category, channel, job_id, delivery_status, "
    "created_at, delivered_at, message_hash) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_COUNT_RECENT_DELIVERED_SQL = (
    "SELECT COUNT(*) AS n FROM notification_log "
    "WHERE job_id = ? AND channel = ? "
    "AND delivery_status = 'delivered' "
    "AND created_at > ?"
)


async def count_recent_deliveries(
    db: DbPool, *, job_id: str, channel: str, since: datetime
) -> int:
    """Return how many ``delivered`` rows exist for (job_id, channel) since ``since``.

    Used by :class:`NotificationRouter` to enforce the outbound frequency cap
    (Story 7.5 Section E).  Failure is logged and treated as zero — the cap is
    a soft guard, never a hard blocker for genuine alerts.
    """
    try:
        rows = await db.fetch_all(
            _COUNT_RECENT_DELIVERED_SQL,
            (job_id, channel, since.isoformat()),
        )
    except Exception as exc:  # B5 — never silent
        log.notifications.warning(
            "[notifications] count_recent_deliveries: query failed — treating as 0",
            exc_info=exc,
            extra={"_fields": {"job_id": job_id, "channel": channel}},
        )
        return 0
    return int(rows[0]["n"]) if rows else 0


def compute_message_hash(message: str) -> str:
    """Return the deterministic 16-char hash used in place of the raw message."""
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
    return digest[:16]


# Channels on which the originating ``session_id`` IS the recipient ``chat_id``.
# For a Telegram PRIVATE chat the session_id (== str(user_id)) equals the chat_id
# (private chat_id == user_id), so a numeric session_id resolves directly to the
# send target. Group chats break this (chat_id != user_id), so a non-numeric or
# non-private session is intentionally NOT resolved (returns None → back-compat
# ``_last_chat_id`` fallback). Other channels are text-only / single-terminal and
# have no per-session chat_id.
_SESSION_IS_CHAT_ID_CHANNELS = frozenset({"telegram"})


def resolve_target_chat_id(channel: str | None, session_id: str | None) -> int | None:
    """Resolve the explicit recipient ``chat_id`` for a proactive/heartbeat send.

    A proactive send with no recipient rides the channel adapter's shared mutable
    ``_last_chat_id`` and, under concurrency, can cross-deliver to whoever messaged
    last. This pure helper recovers the genuine recipient from the originating
    ``session_id`` WHERE that is safe to do — i.e. only on channels where the
    session id is the chat id (Telegram private chats: session_id == str(user_id)
    == chat_id). It returns:

    * the numeric ``chat_id`` when ``channel`` is such a channel and ``session_id``
      is a clean integer (the common, correct case), or
    * ``None`` for any other channel, a missing/blank ``session_id``, or a
      non-numeric ``session_id`` (e.g. a Telegram group, whose chat_id != user_id)
      — the deliverer then falls back to ``_last_chat_id`` (back-compat), and that
      AMBIGUITY is logged loudly (no silent guess at the target).

    Pure: no I/O, no clock. Never raises.
    """
    if not channel or channel not in _SESSION_IS_CHAT_ID_CHANNELS:
        return None
    sid = (session_id or "").strip()
    if not sid:
        log.notifications.warning(
            "[notifications] resolve_target_chat_id: no session_id on a "
            "chat-addressable channel — recipient unknown, falling back to "
            "_last_chat_id (possible cross-delivery under concurrency)",
            extra={"_fields": {"channel": channel}},
        )
        return None
    try:
        return int(sid)
    except ValueError:
        # Non-numeric session id on a chat-addressable channel (e.g. a group chat
        # whose session id is not the user/chat id). Do NOT guess a target.
        log.notifications.warning(
            "[notifications] resolve_target_chat_id: session_id is not a chat id "
            "— recipient unresolved, falling back to _last_chat_id (possible "
            "cross-delivery under concurrency)",
            extra={"_fields": {"channel": channel, "session_id": sid}},
        )
        return None


def in_quiet_hours(settings: QuietHoursSettings, now: datetime) -> bool:
    """Return ``True`` if ``now`` falls inside the configured quiet-hours window.

    Supports overnight windows (e.g. 22:00 → 08:00) by detecting wrap-around.
    Falls back to ``False`` when the timezone cannot be resolved — fail-open
    so notifications keep flowing rather than getting silently batched.
    """
    if not settings.enabled:
        return False
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception as exc:  # B5 — never silent
        log.notifications.warning(
            "[notifications] in_quiet_hours: unknown timezone — defaulting to UTC",
            exc_info=exc,
            extra={"_fields": {"timezone": settings.timezone}},
        )
        tz = ZoneInfo("UTC")
    try:
        local_now = now.astimezone(tz).time()
        start = time.fromisoformat(settings.start)
        end = time.fromisoformat(settings.end)
    except ValueError as exc:  # B5 — never silent
        log.notifications.warning(
            "[notifications] in_quiet_hours: invalid HH:MM — disabling window",
            exc_info=exc,
            extra={"_fields": {"start": settings.start, "end": settings.end}},
        )
        return False
    if start <= end:
        return start <= local_now < end
    return local_now >= start or local_now < end


def next_scheduled_for(settings: QuietHoursSettings, now: datetime) -> datetime:
    """Compute the datetime at which a batched notification should fire.

    * Inside quiet hours → the end of the current window (in the configured tz).
    * Outside quiet hours → the next top-of-hour boundary.

    Always returns a tz-aware datetime in UTC so callers can serialise it
    directly without further conversion.
    """
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception as exc:  # B5 — never silent
        log.notifications.warning(
            "[notifications] next_scheduled_for: unknown timezone — defaulting to UTC",
            exc_info=exc,
            extra={"_fields": {"timezone": settings.timezone}},
        )
        tz = ZoneInfo("UTC")
    aware_now = now if now.tzinfo is not None else now.replace(tzinfo=ZoneInfo("UTC"))

    if in_quiet_hours(settings, aware_now):
        try:
            end_time = time.fromisoformat(settings.end)
        except ValueError as exc:  # B5 — never silent
            log.notifications.warning(
                "[notifications] next_scheduled_for: bad end time — using +1h",
                exc_info=exc,
                extra={"_fields": {"end": settings.end}},
            )
            return _next_hour_boundary_utc(aware_now)
        local_now = aware_now.astimezone(tz)
        candidate = local_now.replace(
            hour=end_time.hour,
            minute=end_time.minute,
            second=0,
            microsecond=0,
        )
        # Overnight window — if the end time has already passed today, jump to tomorrow.
        if candidate <= local_now:
            candidate = candidate + timedelta(days=1)
        return candidate.astimezone(ZoneInfo("UTC"))

    return _next_hour_boundary_utc(aware_now)


def _next_hour_boundary_utc(now: datetime) -> datetime:
    """Return the next top-of-hour boundary in UTC."""
    aware = now if now.tzinfo is not None else now.replace(tzinfo=ZoneInfo("UTC"))
    utc_now = aware.astimezone(ZoneInfo("UTC"))
    floored = utc_now.replace(minute=0, second=0, microsecond=0)
    return floored + timedelta(hours=1)


async def write_log_row(
    db: DbPool,
    *,
    notification_id: str,
    urgency: str,
    category: str,
    channel: str,
    job_id: str | None,
    status: Literal["delivered", "batched", "suppressed", "failed"],
    created_at: datetime,
    delivered_at: datetime | None,
    message_hash: str,
) -> None:
    """Insert a row into ``notification_log``; warn-and-continue on failure."""
    try:
        await db.execute(
            _INSERT_LOG_SQL,
            (
                notification_id,
                urgency,
                category,
                channel,
                job_id,
                status,
                created_at.isoformat(),
                delivered_at.isoformat() if delivered_at is not None else None,
                message_hash,
            ),
        )
    except Exception as exc:  # B5 — never silent
        log.notifications.warning(
            "[notifications] write_log_row: insert failed",
            exc_info=exc,
            extra={"_fields": {"notification_id": notification_id, "status": status}},
        )
