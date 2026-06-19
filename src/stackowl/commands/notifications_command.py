"""NotificationsMissedCommand — ``/notifications`` slash command (Story 7.4).

Subcommands:

* ``/notifications missed`` — list the 20 most-recent non-delivered notifications
  (status in ``suppressed``, ``batched``, ``failed``) from ``notification_log``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.pipeline.state import PipelineState


_SELECT_MISSED_SQL = (
    "SELECT created_at, urgency, category, delivery_status, channel "
    "FROM notification_log "
    "WHERE delivery_status IN ('suppressed','batched','failed') "
    "ORDER BY created_at DESC LIMIT 20"
)
_USAGE = (
    "Usage:\n"
    "  /notifications missed   — show the 20 most-recent non-delivered notifications"
)


class NotificationsMissedCommand(SlashCommand):
    """View notification history pulled from ``notification_log``."""

    def __init__(self, db: DbPool | None = None) -> None:
        self._db: DbPool = db  # type: ignore[assignment]  # guarded in handle()

    @property
    def command(self) -> str:
        return "notifications"

    @property
    def description(self) -> str:
        return "View notification history."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.notifications.debug(
            "[notifications] notifications.handle: entry",
            extra={"_fields": {"args": args[:40], "session": state.session_id}},
        )
        if self._db is None:
            return "✗ /notifications: not configured"
        sub = args.strip()
        if sub != "missed":
            log.notifications.debug(
                "[notifications] notifications.handle: usage shown",
                extra={"_fields": {"sub": sub[:40]}},
            )
            return _USAGE

        try:
            rows = await self._db.fetch_all(_SELECT_MISSED_SQL, ())
        except Exception as exc:  # B5 — never silent
            log.notifications.error(
                "[notifications] notifications.handle: query failed",
                exc_info=exc,
            )
            return f"notifications: query failed ({exc})"

        log.notifications.debug(
            "[notifications] notifications.handle: rows fetched",
            extra={"_fields": {"row_count": len(rows)}},
        )
        if not rows:
            log.notifications.info(
                "[notifications] notifications.handle: exit — empty"
            )
            return "missed:0"

        lines = [f"missed:{len(rows)}"]
        for row in rows:
            lines.append(
                f"  {row['created_at']}  "
                f"{row['delivery_status']:<10}  "
                f"{row['urgency']:<8}  "
                f"{row['category']:<20}  "
                f"{row['channel']}"
            )
        log.notifications.info(
            "[notifications] notifications.handle: exit",
            extra={"_fields": {"row_count": len(rows)}},
        )
        return "\n".join(lines)

    @classmethod
    def create_and_register(cls, db: DbPool) -> NotificationsMissedCommand:
        cmd = cls(db=db)
        CommandRegistry.instance().register(cmd)
        return cmd
