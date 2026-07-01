"""TelegramCanaryHandler — synthetic send-path round-trip heartbeat (PB-CANARY).

There is no way to script a scheduled human reply, so "round trip" here means a
REAL send through the live Telegram Bot API that comes back confirmed
(``ProactiveDeliveryOutcome.rollup == "delivered"``) — a genuine end-to-end proof
that exercises the exact adapter/network path a real user-facing send does. This
is the SEND-side sibling of PB0b's ``ChannelLivenessContributor`` receive-loop
heartbeat: PB0b proves the poll loop is alive, this proves sending actually
works. The two signals are complementary, not duplicates.

Delivers through the SAME single seam (``ProactiveJobDeliverer``) every other
cron-born handler uses — no special-casing, and it automatically gets PB7b's
undeliverable-outbox coverage for free. On a CONFIRMED ``delivered`` outcome it
stamps a distinct ``channel_liveness`` row (``"telegram_canary"``, separate from
PB0b's ``"telegram"`` receive row — same channel-agnostic table, no migration)
that the generalized ``ChannelLivenessContributor`` (``kind="send"``) reads to
alert on absence via the existing ``HealthSweepHandler``/``AlertSink`` path.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.notifications.proactive_job import (
    ProactiveDeliveryOutcome,
    job_success_for_rollup,
)
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.channels.liveness import ChannelLivenessStore
    from stackowl.notifications.proactive_job import ProactiveJobDeliverer

_CATEGORY = "canary"
_URGENCY = "low"
# Deterministic marker — never templatized/varied per run (nothing to render).
_MARKER = "🔎 canary — ignore"
# Distinct channel key from PB0b's "telegram" receive row (same table).
_LIVENESS_CHANNEL = "telegram_canary"


class TelegramCanaryHandler(JobHandler):
    """Sends a small marker through the real Telegram Bot API on a cadence."""

    def __init__(
        self,
        job_deliverer: ProactiveJobDeliverer | None = None,
        liveness_store: ChannelLivenessStore | None = None,
    ) -> None:
        self._job_deliverer = job_deliverer
        self._liveness_store = liveness_store

    @property
    def handler_name(self) -> str:
        return "telegram_canary"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] telegram_canary.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("telegram_canary.execute")
        t0 = time.monotonic()

        # 2. DECISION — no deliverer wired (legacy/unit construction, never hit in
        # production) => skip HONESTLY, mirroring morning_brief/check_in's pattern.
        if self._job_deliverer is None:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.warning(
                "[scheduler] telegram_canary.execute: no deliverer wired — canary "
                "NOT sent (no fake 'delivered')",
                extra={"_fields": {"job_id": job.job_id}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="delivery",
                success=True,
                output=None,
                error=None,
                duration_ms=duration_ms,
                metadata={"delivery_status": "skipped", "reason": "no_deliverer"},
            )

        # 3. STEP — send through the SAME seam every delivery handler uses (a real
        # Telegram Bot API round trip, not an assertion).
        # CANARY-LEAK — this synthetic probe's own marker is not lost user
        # content: opt out of the undelivered-outbox NACK so a failed canary
        # send (the exact outage it exists to detect) never surfaces in the
        # user-facing next-contact banner. Operator alerting still happens via
        # the liveness/health-sweep path below, unaffected by this flag.
        outcome: ProactiveDeliveryOutcome = await self._job_deliverer.deliver_for_job(
            job,
            message=_MARKER,
            category=_CATEGORY,
            urgency=_URGENCY,
            surface_undelivered=False,
        )
        duration_ms = (time.monotonic() - t0) * 1000

        # Only a CONFIRMED real send stamps the send-path liveness signal — never
        # on undeliverable/failed/batched (F109 — no fake 'alive').
        if outcome.rollup == "delivered" and self._liveness_store is not None:
            try:
                await self._liveness_store.mark_alive(channel=_LIVENESS_CHANNEL)
            except Exception as exc:  # B5 — never silent; a stamp failure must
                # not flip an honest confirmed delivery into a reported failure.
                log.scheduler.error(
                    "[scheduler] telegram_canary.execute: liveness stamp failed",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job.job_id, "channel": _LIVENESS_CHANNEL}},
                )

        # 4. EXIT
        log.scheduler.info(
            "[scheduler] telegram_canary.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "status": outcome.rollup,
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="delivery",
            success=job_success_for_rollup(outcome.rollup),
            output=_MARKER,
            error=None,
            duration_ms=duration_ms,
            metadata={
                "delivery_status": outcome.rollup,
                "per_channel": outcome.per_channel,
                "undeliverable": list(outcome.undeliverable),
            },
        )
