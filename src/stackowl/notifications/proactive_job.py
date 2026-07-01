"""ProactiveJobDeliverer — the shared cron-born delivery loop (C1 / F101 + F102).

A scheduled handler (morning_brief, check_in) renders a body and then must put it
in front of the user HONESTLY: through the one delivery seam
(:class:`ProactiveDeliverer`), addressed from DURABLE job state (never the
adapter's shared ``_last_*``), exactly-once across a crash (the
:class:`DeliveryLedger`), and recording ``delivered`` ONLY after a real transport
success.

That loop is identical for every cron-born surface, so it lives here once instead
of being copy-pasted into each handler:

1. Resolve ``[(channel, native_target)]`` from the job via :class:`DeliverySpec`
   (durable columns only — no request context). Channels with no durable address
   are reported as *undeliverable* — loudly, never ``delivered``.
2. For each resolvable channel: ``claim_dispatch`` the occurrence (suppress a
   replay re-send), build a :class:`Notification` carrying the channel-native
   ``target``, ``deliver`` through the seam, then ``mark`` the ledger row with the
   ACTUAL transport outcome.
3. Aggregate into an honest rollup (:class:`ProactiveDeliveryOutcome`): the job is
   ``delivered`` iff ≥1 channel actually delivered; otherwise the real
   ``failed`` / ``suppressed`` / ``batched`` / ``undeliverable`` truth (F109 — a
   ``delivered`` status is downstream of a real ``send_text`` returning, never
   upstream of it).

The occurrence key is OCCURRENCE-scoped (``idempotency_key@next_run_at``) so the
frozen-scheduler fix is preserved — a later scheduled instant is a fresh
occurrence and a legitimately new delivery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.notifications.recipient import DeliverySpec
from stackowl.notifications.router import Notification

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.notifications.deliverer import ProactiveDeliverer
    from stackowl.notifications.delivery_ledger import DeliveryLedger, LedgerState
    from stackowl.notifications.router import DeliveryStatus
    from stackowl.scheduler.job import Job


def occurrence_key(job: Job) -> str:
    """Dedup key scoped to the SCHEDULED INSTANT being serviced.

    Mirrors :meth:`JobScheduler._occurrence_key` exactly — the static
    ``idempotency_key`` means "run once ever" (wrong for a recurring job), so the
    occurrence's ``next_run_at`` is suffixed to make the same scheduled instant
    idempotent while each fresh instant is a new delivery. Module-level so the
    handlers (which write the ledger) and the scheduler (which writes
    ``job_runs``) agree on the SAME key.
    """
    return f"{job.idempotency_key}@{job.next_run_at}"


@dataclass(frozen=True)
class ProactiveDeliveryOutcome:
    """Honest aggregate of a cron-born multi-channel delivery (F109).

    ``rollup`` is ``"delivered"`` iff ≥1 channel actually delivered; otherwise the
    real outcome. ``per_channel`` and ``undeliverable`` carry the per-channel
    truth so telemetry/audit never assert a falsehood.
    """

    rollup: str
    per_channel: dict[str, str] = field(default_factory=dict)
    undeliverable: tuple[str, ...] = ()
    suppressed_replay: tuple[str, ...] = ()

    @property
    def delivered(self) -> bool:
        """True iff at least one channel actually delivered."""
        return any(status == "delivered" for status in self.per_channel.values())


class ProactiveJobDeliverer:
    """Funnels a rendered cron-born body to its durable recipients, honestly.

    Holds the single delivery seam (:class:`ProactiveDeliverer`) and the
    exactly-once :class:`DeliveryLedger`. Constructor-injected (NOT
    ``get_services()``): the scheduler poll thread has no services context, and
    injection keeps the owning handler unit-testable.
    """

    def __init__(
        self,
        deliverer: ProactiveDeliverer,
        ledger: DeliveryLedger,
    ) -> None:
        self._deliverer = deliverer
        self._ledger = ledger

    async def deliver_for_job(
        self,
        job: Job,
        *,
        message: str,
        category: str,
        urgency: str = "normal",
    ) -> ProactiveDeliveryOutcome:
        """Deliver ``message`` to every durable recipient of ``job``; never raises.

        Returns an honest :class:`ProactiveDeliveryOutcome`. A channel with no
        durable address is reported ``undeliverable`` (never sent, never
        ``delivered``). A channel whose occurrence was already dispatched (replay)
        is suppressed. ``delivered`` appears in ``per_channel`` ONLY after a real
        transport success.
        """
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] proactive_job.deliver_for_job: entry",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "category": category,
                    "message_len": len(message),
                }
            },
        )
        spec = DeliverySpec.from_job(job)
        occ_key = occurrence_key(job)

        per_channel: dict[str, str] = {}
        suppressed_replay: list[str] = []
        undeliverable = tuple(spec.unresolved_channels())

        # 2. DECISION — a channel with no durable address is undeliverable; it is
        # NEVER sent to and NEVER recorded delivered (F109). The caller surfaces it.
        if undeliverable:
            log.scheduler.warning(
                "[scheduler] proactive_job.deliver_for_job: channels undeliverable "
                "— no durable recipient (never delivered, no _last_* guess)",
                extra={
                    "_fields": {"job_id": job.job_id, "undeliverable": list(undeliverable)}
                },
            )

        for channel, target in spec.pairs():
            # 3. STEP — claim the occurrence BEFORE the side-effect. A lost claim
            # means this occurrence+channel was already dispatched (a replay), so
            # suppress the re-send (exactly-once delivery across a crash).
            try:
                won = await self._ledger.claim_dispatch(job.job_id, occ_key, channel)
            except Exception as exc:  # B5 — ledger failure must not silently send
                log.scheduler.error(
                    "[scheduler] proactive_job.deliver_for_job: ledger claim failed "
                    "— not sending (avoid an unledgered double-send)",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job.job_id, "channel": channel}},
                )
                per_channel[channel] = "failed"
                continue
            if not won:
                log.scheduler.info(
                    "[scheduler] proactive_job.deliver_for_job: replay suppressed",
                    extra={"_fields": {"job_id": job.job_id, "channel": channel}},
                )
                suppressed_replay.append(channel)
                continue

            notification = Notification(
                message=message,
                urgency=urgency,  # type: ignore[arg-type]  # validated by Notification
                category=category,
                channel_name=channel,
                job_id=job.job_id,
                target=target,
            )
            status: DeliveryStatus = await self._deliverer.deliver(notification)
            per_channel[channel] = status
            # Flip the ledger row to the ACTUAL transport outcome. A non-delivered
            # decision (batched/suppressed/failed) is marked 'failed' in the ledger
            # so a later occurrence can honestly retry — only a real 'delivered'
            # locks the occurrence as done.
            ledger_state: LedgerState = "delivered" if status == "delivered" else "failed"
            try:
                await self._ledger.mark(job.job_id, occ_key, channel, ledger_state)
            except Exception as exc:  # B5 — never silent
                log.scheduler.error(
                    "[scheduler] proactive_job.deliver_for_job: ledger mark failed",
                    exc_info=exc,
                    extra={
                        "_fields": {
                            "job_id": job.job_id,
                            "channel": channel,
                            "state": ledger_state,
                        }
                    },
                )

        rollup = self._rollup(per_channel, undeliverable, suppressed_replay)
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] proactive_job.deliver_for_job: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "rollup": rollup,
                    "per_channel": per_channel,
                    "undeliverable": list(undeliverable),
                }
            },
        )
        return ProactiveDeliveryOutcome(
            rollup=rollup,
            per_channel=per_channel,
            undeliverable=undeliverable,
            suppressed_replay=tuple(suppressed_replay),
        )

    @staticmethod
    def _rollup(
        per_channel: dict[str, str],
        undeliverable: tuple[str, ...],
        suppressed_replay: list[str],
    ) -> str:
        """Derive the honest job-level status from the per-channel truth (F109).

        Branch order (first match wins): ``delivered`` (≥1 channel delivered) >
        ``failed`` (a send was tried and failed) > ``batched`` > ``suppressed``
        (router deferred) > ``undeliverable`` (a channel had no durable address) >
        ``suppressed`` (every channel was a replay, nothing new to do) >
        ``undeliverable`` (nothing to deliver to at all).
        """
        statuses = set(per_channel.values())
        if "delivered" in statuses:
            return "delivered"
        if "failed" in statuses:
            return "failed"
        if "batched" in statuses:
            return "batched"
        if "suppressed" in statuses:
            return "suppressed"
        if undeliverable:
            return "undeliverable"
        if suppressed_replay:
            return "suppressed"
        return "undeliverable"


def job_success_for_rollup(rollup: str) -> bool:
    """Map a :class:`ProactiveDeliveryOutcome` rollup to an honest ``JobResult.success``.

    Interim shim (PB3) mirroring ``goal_execution._deliver_answer``'s rollup->retry
    semantics; the PB6a/6b ``verified``/``effect_class`` contract supersedes it.
    ``delivered``/``suppressed``/``undeliverable`` -> ``True`` (an unresolvable
    target is undeliverable, which a retry can't fix). ``partial``/``failed``/
    anything unrecognized -> ``False`` (transient or unknown, so the scheduler
    retries — fail-closed, unlike ``goal_execution``'s else-branch).
    """
    return rollup in ("delivered", "suppressed", "undeliverable")
