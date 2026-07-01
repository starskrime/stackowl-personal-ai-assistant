"""NotificationRouter — focus-mode / quiet-hours aware dispatch (Story 7.4).

Routes Notifications via a pure decision table to one of ``delivered`` /
``batched`` / ``suppressed``. Every call writes a row to ``notification_log``
recording only ``sha256(message)[:16]`` (the ``message_hash``) — the audit log
never stores the raw body.

PERSISTENCE & RETENTION (STEER-6/F112 — the honest contract). A ``batched``
decision (quiet-hours / focus mode) MUST insert the raw message BODY into
``notification_queue`` so the later ``NotificationDigestJob`` flush can transport
the exact text the user expects — the body is genuinely persisted for batched
notifications (it cannot be reconstructed from the hash). That persistence is
BOUNDED and cleaned up: the digest deletes the queue row on a successful flush, on
exactly-once reconciliation, and on dead-letter past the bounded attempt cap — so
a body lives in ``notification_queue`` only while a delivery is pending, never
indefinitely. ``delivered`` and ``suppressed`` decisions persist no body at all.
"""

from __future__ import annotations

import time as _time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.memory.bridge import HealthReport
from stackowl.notifications.router_helpers import (
    compute_message_hash,
    count_recent_deliveries,
    in_quiet_hours,
    next_scheduled_for,
    write_log_row,
)
from stackowl.notifications.undelivered_outbox import UndeliveredOutbox
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool


# ``RouterDecision`` is the closed set the router's pure decision table can
# produce. ``DeliveryStatus`` is the broader transport-layer outcome: it adds
# ``failed``, a terminal status produced ONLY by the outbound transport layer
# (ProactiveDeliverer) when a channel is unknown or a send fails after retry.
RouterDecision = Literal["delivered", "batched", "suppressed"]
DeliveryStatus = Literal["delivered", "batched", "suppressed", "failed"]
FocusMode = Literal["off", "soft", "hard"]

_QUEUE_DEGRADED_THRESHOLD = 100

_INSERT_QUEUE_SQL = (
    "INSERT INTO notification_queue "
    "(notification_id, message_hash, urgency, category, channel, job_id, scheduled_for, message) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_COUNT_QUEUE_SQL = "SELECT COUNT(*) AS n FROM notification_queue"


class Notification(BaseModel):
    """A single notification request handed to :class:`NotificationRouter`."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    message: str
    urgency: Literal["critical", "normal", "low"]
    category: str
    channel_name: str | None = None
    job_id: str | None = None
    idempotency_key: str | None = None
    # Optional outbound file/media attachment (E8 send_file). When set, the
    # ProactiveDeliverer routes to the channel adapter's ``send_file`` instead of
    # the text path, using ``message`` as the (optional) caption. None preserves
    # the pure-text behaviour for every existing caller.
    file_path: str | None = None
    # Explicit channel-native recipient for this send (C1/F104). Carries the
    # destination in the channel's OWN type: a telegram ``int`` chat_id or a
    # slack ``str`` channel id. A proactive/heartbeat send with no recipient
    # would ride the channel adapter's shared mutable ``_last_*`` and could
    # cross-deliver to whoever messaged last; the proactive source stamps the
    # resolved destination here and the ProactiveDeliverer threads it through to
    # the adapter so the message reaches THAT recipient. None keeps the
    # back-compat ``_last_*`` fallback for text-only / single-terminal channels.
    #
    # ``target_chat_id`` is the DEPRECATED former name — kept for one release as a
    # construction alias (``Notification(target_chat_id=...)`` still works) and as
    # a read property below, per the minimal-change / no-break rule. New callers
    # should use ``target``.
    target: str | int | None = Field(
        default=None,
        validation_alias=AliasChoices("target", "target_chat_id"),
    )

    @property
    def target_chat_id(self) -> str | int | None:
        """Deprecated read alias for :attr:`target` (kept one release, no-break)."""
        return self.target


class NotificationRouter:
    """Decides whether each notification is delivered, batched, or suppressed.

    Driven by ``notification.urgency`` (critical always wins), the configured
    quiet-hours window, and the in-memory focus mode (set via ``/focus``).
    Never touches a channel adapter — real transport lands in Epic 8/9.
    """

    def __init__(
        self,
        db: DbPool,
        settings: Settings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._db = db
        self._settings = settings
        self._clock = clock
        self._focus_mode: FocusMode = "off"
        # PA5(b) — the durable NACK store for the `suppressed` silent-drop seam.
        self._outbox = UndeliveredOutbox(db)

    def set_focus_mode(self, mode: FocusMode) -> None:
        """Update the in-memory focus mode. Persistence is intentionally out of scope."""
        log.notifications.info(
            "[notifications] router.set_focus_mode: mode updated",
            extra={"_fields": {"mode": mode, "previous": self._focus_mode}},
        )
        self._focus_mode = mode

    def get_focus_mode(self) -> FocusMode:
        return self._focus_mode

    async def deliver(self, notification: Notification) -> RouterDecision:
        """Route ``notification`` to the appropriate sink."""
        # 1. ENTRY
        log.notifications.debug(
            "[notifications] router.deliver: entry",
            extra={
                "_fields": {
                    "urgency": notification.urgency,
                    "category": notification.category,
                    "channel": notification.channel_name,
                }
            },
        )
        TestModeGuard.assert_not_test_mode("notifications.router.deliver")
        t0 = _time.monotonic()

        message_hash = compute_message_hash(notification.message)
        now = self._clock()
        quiet = in_quiet_hours(self._settings.notifications.quiet_hours, now)

        # 2. DECISION — table-driven routing
        decision = self._decide(notification.urgency, quiet, self._focus_mode)
        log.notifications.debug(
            "[notifications] router.deliver: routing decision",
            extra={
                "_fields": {
                    "decision": decision,
                    "urgency": notification.urgency,
                    "quiet_hours": quiet,
                    "focus_mode": self._focus_mode,
                }
            },
        )

        channel = notification.channel_name or self._settings.notifications.default_channel
        notification_id = notification.idempotency_key or uuid.uuid4().hex

        # 2b. FREQUENCY CAP — outbound rate limit per (job_id, channel)
        if decision == "delivered" and notification.job_id is not None:
            decision = await self._apply_frequency_cap(
                decision, notification.job_id, channel, now
            )

        # 3. STEP — perform the chosen action
        await self._apply_decision(
            decision, notification, channel, message_hash, notification_id, now
        )

        # 4. EXIT
        duration_ms = (_time.monotonic() - t0) * 1000
        log.notifications.debug(
            "[notifications] router.deliver: exit",
            extra={
                "_fields": {
                    "status": decision,
                    "message_hash": message_hash,
                    "duration_ms": duration_ms,
                }
            },
        )
        return decision

    async def health(self) -> HealthReport:
        """Probe the router by counting pending rows in ``notification_queue``."""
        log.notifications.debug("[notifications] router.health: entry")
        try:
            rows = await self._db.fetch_all(_COUNT_QUEUE_SQL, ())
        except Exception as exc:  # B5 — never silent
            log.notifications.error(
                "[notifications] router.health: queue count failed",
                exc_info=exc,
            )
            return HealthReport(
                name="notifications.router",
                status="down",
                details={"error": str(exc)},
            )
        depth = int(rows[0]["n"]) if rows else 0
        status: Literal["ok", "degraded", "down"] = (
            "degraded" if depth > _QUEUE_DEGRADED_THRESHOLD else "ok"
        )
        log.notifications.debug(
            "[notifications] router.health: exit",
            extra={"_fields": {"queue_depth": depth, "status": status}},
        )
        return HealthReport(
            name="notifications.router",
            status=status,
            details={"queue_depth": depth, "focus_mode": self._focus_mode},
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _decide(
        urgency: Literal["critical", "normal", "low"],
        quiet: bool,
        focus_mode: FocusMode,
    ) -> RouterDecision:
        """Pure decision table (see Story 7.4 spec)."""
        if urgency == "critical":
            return "delivered"
        if quiet:
            return "batched"
        if focus_mode == "soft":
            return "batched"
        if focus_mode == "hard":
            return "suppressed" if urgency == "low" else "batched"
        return "delivered"

    async def _apply_decision(
        self,
        decision: RouterDecision,
        notification: Notification,
        channel: str,
        message_hash: str,
        notification_id: str,
        now: datetime,
    ) -> None:
        """Execute the chosen routing decision: side-effects + log row."""
        log_fields = {
            "channel": channel,
            "message_hash": message_hash,
            "urgency": notification.urgency,
            "category": notification.category,
        }
        delivered_at: datetime | None = None

        if decision == "delivered":
            log.notifications.info(
                "[notifications] router.deliver: delivered",
                extra={"_fields": log_fields},
            )
            delivered_at = now
        elif decision == "batched":
            scheduled = next_scheduled_for(self._settings.notifications.quiet_hours, now)
            try:
                await self._db.execute(
                    _INSERT_QUEUE_SQL,
                    (
                        notification_id,
                        message_hash,
                        notification.urgency,
                        notification.category,
                        channel,
                        notification.job_id,
                        scheduled.isoformat(),
                        notification.message,
                    ),
                )
            except Exception as exc:  # B5 — never silent
                log.notifications.error(
                    "[notifications] router._apply_decision: queue insert failed",
                    exc_info=exc,
                    extra={"_fields": {"notification_id": notification_id}},
                )
                raise
            log.notifications.info(
                "[notifications] router.deliver: batched",
                extra={"_fields": {**log_fields, "scheduled_for": scheduled.isoformat()}},
            )
        else:  # suppressed
            log.notifications.debug(
                "[notifications] router.deliver: suppressed",
                extra={"_fields": log_fields},
            )
            # PA5(b) — suppressed today means audit-only (message_hash, body
            # gone). ADDITIVE: persist the durable NACK; no control-flow change.
            await self._outbox.record_undelivered(
                identity_key=(
                    str(notification.target)
                    if notification.target is not None
                    else DEFAULT_PRINCIPAL_ID
                ),
                body=notification.message,
                reason="suppressed",
                channel=channel,
                category=notification.category,
                urgency=notification.urgency,
                job_id=notification.job_id,
            )

        await write_log_row(
            self._db,
            notification_id=notification_id,
            urgency=notification.urgency,
            category=notification.category,
            channel=channel,
            job_id=notification.job_id,
            status=decision,
            created_at=now,
            delivered_at=delivered_at,
            message_hash=message_hash,
        )

    async def _apply_frequency_cap(
        self,
        decision: RouterDecision,
        job_id: str,
        channel: str,
        now: datetime,
    ) -> RouterDecision:
        """Downgrade ``delivered`` → ``batched`` once the per-hour cap is hit."""
        cap = self._settings.notifications.max_notifications_per_hour
        if cap <= 0:
            return decision
        since = now - timedelta(hours=1)
        sent = await count_recent_deliveries(
            self._db, job_id=job_id, channel=channel, since=since
        )
        if sent >= cap:
            log.notifications.warning(
                "[notifications] router.deliver: per-hour cap hit — batching",
                extra={"_fields": {"job_id": job_id, "channel": channel, "sent": sent, "cap": cap}},
            )
            return "batched"
        return decision
