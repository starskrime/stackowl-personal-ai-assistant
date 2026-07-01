"""ChannelLivenessStore — cross-process receive-loop liveness signal (PB0b / RC0).

The single seam both processes use: the gateway (which owns the real receive
loop) UPSERTs ``last_receive_at`` for its channel every heartbeat, and the core
health sweep reads it back. Because the timestamp is persisted as ISO-8601 UTC
wall clock in the shared SQLite DB, the signal survives the process boundary that
an in-memory flag cannot cross. Channel-agnostic on purpose — it never hardcodes
"telegram"; only the health-aggregator registration site names a channel.
"""

from __future__ import annotations

import logging
from datetime import datetime

from stackowl.db.pool import DbPool
from stackowl.infra.clock import Clock, WallClock

log = logging.getLogger("stackowl.channels")


class ChannelLivenessStore:
    """Persist/read a channel's last-receive wall-clock timestamp."""

    def __init__(self, db_pool: DbPool, clock: Clock | None = None) -> None:
        self._db_pool = db_pool
        self._clock = clock or WallClock()

    async def mark_alive(self, channel: str) -> None:
        """UPSERT ``(channel, now)`` — the receive loop is alive right now."""
        now_iso = self._clock.now().isoformat()
        log.debug("[channels] liveness.mark_alive: entry channel=%s at=%s", channel, now_iso)
        try:
            await self._db_pool.execute(
                "INSERT INTO channel_liveness (channel, last_receive_at) VALUES (?, ?) "
                "ON CONFLICT(channel) DO UPDATE SET last_receive_at=excluded.last_receive_at",
                (channel, now_iso),
            )
        except Exception as exc:
            # No-hidden-errors: a swallowed write here reintroduces RC0 (sweep
            # would read a stale/absent row and misreport). Log loudly + propagate.
            log.error("[channels] liveness.mark_alive: write failed channel=%s", channel, exc_info=exc)
            raise
        log.debug("[channels] liveness.mark_alive: exit channel=%s", channel)

    async def read_last_receive_at(self, channel: str) -> datetime | None:
        """Return the stored tz-aware timestamp for ``channel``, or None."""
        log.debug("[channels] liveness.read_last_receive_at: entry channel=%s", channel)
        rows = await self._db_pool.fetch_all(
            "SELECT last_receive_at FROM channel_liveness WHERE channel = ?",
            (channel,),
        )
        if not rows:
            log.debug("[channels] liveness.read_last_receive_at: exit channel=%s no_row", channel)
            return None
        last = datetime.fromisoformat(str(rows[0]["last_receive_at"]))
        log.debug("[channels] liveness.read_last_receive_at: exit channel=%s at=%s", channel, last)
        return last
