"""QuietHoursCommand — ``/quiet`` slash command (Story 7.4).

Inserts a session-scoped row into ``notification_overrides``. The override is
honoured by the :class:`NotificationRouter` until ``expires_at`` (default:
24 hours from now).

Forms:

* ``/quiet 22:00 08:00``                       — global override
* ``/quiet --category <name> 22:00 08:00``     — per-category override
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.pipeline.state import PipelineState


_INSERT_OVERRIDE_SQL = (
    "INSERT INTO notification_overrides "
    "(override_id, start_time, end_time, expires_at, category) "
    "VALUES (?, ?, ?, ?, ?)"
)
_DEFAULT_TTL_HOURS = 24
_USAGE = (
    "Usage:\n"
    "  /quiet HH:MM HH:MM                           — global override\n"
    "  /quiet --category <name> HH:MM HH:MM         — per-category override"
)


class QuietHoursCommand(SlashCommand):
    """Insert a per-session quiet-hours override row."""

    def __init__(self, db: DbPool) -> None:
        self._db = db

    @property
    def command(self) -> str:
        return "quiet"

    @property
    def description(self) -> str:
        return "Override quiet hours for the current session."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.notifications.debug(
            "[notifications] quiet.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        parts = args.strip().split()
        if not parts:
            log.notifications.debug("[notifications] quiet.handle: usage shown — empty args")
            return _USAGE

        category: str | None = None
        if parts[0] == "--category":
            if len(parts) < 4:  # noqa: PLR2004 — "--category NAME HH:MM HH:MM"
                log.notifications.debug(
                    "[notifications] quiet.handle: usage shown — missing category args"
                )
                return _USAGE
            category = parts[1]
            start_raw, end_raw = parts[2], parts[3]
        elif len(parts) >= 2:  # noqa: PLR2004
            start_raw, end_raw = parts[0], parts[1]
        else:
            log.notifications.debug("[notifications] quiet.handle: usage shown — too few parts")
            return _USAGE

        try:
            time.fromisoformat(start_raw)
            time.fromisoformat(end_raw)
        except ValueError as exc:  # B5 — never silent
            log.notifications.warning(
                "[notifications] quiet.handle: invalid HH:MM",
                exc_info=exc,
                extra={"_fields": {"start": start_raw, "end": end_raw}},
            )
            return f"quiet: invalid time format ({exc})"

        override_id = uuid.uuid4().hex
        expires_at = (datetime.now(UTC) + timedelta(hours=_DEFAULT_TTL_HOURS)).isoformat()
        log.notifications.debug(
            "[notifications] quiet.handle: inserting override",
            extra={
                "_fields": {
                    "override_id": override_id,
                    "category": category,
                    "start": start_raw,
                    "end": end_raw,
                }
            },
        )
        try:
            await self._db.execute(
                _INSERT_OVERRIDE_SQL,
                (override_id, start_raw, end_raw, expires_at, category),
            )
        except Exception as exc:  # B5 — never silent
            log.notifications.error(
                "[notifications] quiet.handle: insert failed",
                exc_info=exc,
                extra={"_fields": {"override_id": override_id}},
            )
            return f"quiet: failed to apply override ({exc})"

        scope = f"category:{category}" if category else "global"
        log.notifications.info(
            "[notifications] quiet.handle: exit",
            extra={"_fields": {"override_id": override_id, "scope": scope}},
        )
        return f"quiet: {scope} {start_raw}-{end_raw} until {expires_at}"

    @classmethod
    def create_and_register(cls, db: DbPool) -> QuietHoursCommand:
        cmd = cls(db=db)
        CommandRegistry.instance().register(cmd)
        return cmd
