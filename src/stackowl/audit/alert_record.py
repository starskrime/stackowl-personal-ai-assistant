"""Durable record of what the operator has already been paged about.

WHY THIS EXISTS. ``HealthSweepHandler`` re-alerts on an hour-long heartbeat while
an incident is ongoing, and enforced that with an in-memory dict whose comment
read: "it doesn't need to survive a restart (a fresh process re-alerts once on
the next unhealthy tick, which is fine)". That was an assumption about restart
frequency, and it was never measured.

MEASURED 2026-09-03 over one continuous 13-hour provider outage: 37 critical
Telegram pages where the one-hour heartbeat intends 13, with 11 landing within
three minutes of a boot. CodeWatcher exec-replaces the core on every code change,
so "once per restart" was the dominant source rather than a rare extra.

NO NEW STORE. ``audit_log`` is append-only, integrity-chained, and already home
to ``consent.decision``, ``capability.escalated`` and ``incident.diagnosed`` —
"the operator was paged about X" is exactly that kind of event. The sibling
handler ``capability_gap_escalation`` fixed the same shape one scope narrower
(per-turn state doing a durable job) with the same store, and its docstring says
why: one existing store, one existing loop, no second engine.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.audit.logger import AuditLogger

#: The event this writes and reads back. One name, one owner.
ALERTED_EVENT = "health.alerted"

#: Who the audit row is attributed to.
_ACTOR = "health_sweep"


class AuditAlertRecord:
    """``audit_log``-backed implementation of the handler's ``AlertRecord``."""

    def __init__(self, db: Any, audit: AuditLogger) -> None:
        self._db = db
        self._audit = audit

    async def load_recent_alerts(self, within_s: float) -> dict[str, tuple[str, float]]:
        """``name -> (status, seconds since it was last alerted)`` inside the window.

        Only the MOST RECENT row per subsystem matters: the handler asks "how long
        since I last paged about this", and an older row would answer a question
        nobody asked. Never raises — the caller degrades to per-process dedup.
        """
        cutoff = time.time() - float(within_s)
        rows = await self._db.fetch_all(
            "SELECT target, details, timestamp FROM audit_log "
            "WHERE event_type = ? AND timestamp >= ? ORDER BY timestamp ASC",
            (ALERTED_EVENT, cutoff),
        )
        out: dict[str, tuple[str, float]] = {}
        now = time.time()
        for row in rows:
            name = str(row["target"] or "")
            if not name:
                continue
            try:
                status = str((json.loads(row["details"] or "{}") or {}).get("status") or "")
            except Exception:  # noqa: BLE001 — a malformed row may not hide the rest
                log.scheduler.warning(
                    "[audit] alert_record: unreadable details — skipping the row",
                    extra={"_fields": {"target": name}},
                )
                continue
            if not status:
                continue
            # ASC order means a later row overwrites an earlier one, leaving the
            # most recent per subsystem.
            out[name] = (status, max(0.0, now - float(row["timestamp"])))
        return out

    def record_alert(self, name: str, status: str) -> None:
        """Note that the operator was just paged about ``name`` at ``status``."""
        self._audit.append(
            event_type=ALERTED_EVENT, actor=_ACTOR, target=name,
            details={"status": status},
        )
