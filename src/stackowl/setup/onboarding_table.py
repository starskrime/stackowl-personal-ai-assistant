"""OnboardingTable — records first-run events in the `onboarding` SQLite table."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log


class OnboardingTable:
    """Tracks one-time setup events so the UI knows which steps have already been shown.

    Each event is a short string key (e.g. "welcome_shown", "provider_configured").
    Events are stored uniquely — recording the same event twice is a no-op.
    """

    @staticmethod
    async def record_event(db: DbPool, event: str) -> None:
        """Insert *event* into the onboarding table.

        If the event already exists the upsert is a no-op (UNIQUE constraint).
        """
        # 1. ENTRY
        log.infra.debug(
            "[onboarding] record_event: entry",
            extra={"_fields": {"event": event}},
        )
        t0 = time.monotonic()

        now_iso = datetime.now(tz=UTC).isoformat()
        # 2. DECISION — use INSERT OR IGNORE so duplicates are silent
        sql = (
            "INSERT OR IGNORE INTO onboarding_events (event, recorded_at) VALUES (?, ?)"
        )
        # 3. STEP — execute
        try:
            await db.execute(sql, (event, now_iso))
        except Exception as exc:
            log.infra.error(
                "[onboarding] record_event: db execute failed",
                exc_info=exc,
                extra={"_fields": {"event": event}},
            )
            raise

        # 4. EXIT
        duration_ms = (time.monotonic() - t0) * 1000
        log.infra.debug(
            "[onboarding] record_event: exit",
            extra={"_fields": {"event": event, "duration_ms": duration_ms}},
        )

    @staticmethod
    async def has_event(db: DbPool, event: str) -> bool:
        """Return True if *event* has already been recorded."""
        # 1. ENTRY
        log.infra.debug(
            "[onboarding] has_event: entry",
            extra={"_fields": {"event": event}},
        )
        t0 = time.monotonic()

        # 2. DECISION — simple existence check
        rows = await db.fetch_all(
            "SELECT event FROM onboarding_events WHERE event = ?",
            (event,),
        )
        found = len(rows) > 0

        # 4. EXIT
        duration_ms = (time.monotonic() - t0) * 1000
        log.infra.debug(
            "[onboarding] has_event: exit",
            extra={"_fields": {"event": event, "found": found, "duration_ms": duration_ms}},
        )
        return found
