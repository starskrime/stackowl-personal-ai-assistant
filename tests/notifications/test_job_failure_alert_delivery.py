"""Task 2 regression — retry-exhausted job-failure alerts must reach Telegram.

Diagnostic on the live DB found `goal_execution-063ab221`'s two failure alerts
(F-61, `JobScheduler._notify_failure`) both crashed inside `Notification.__init__`
(`ValidationError: urgency` — the call passed the literal ``"high"``, but
``Notification.urgency`` only accepts ``critical``/``normal``/``low``) AFTER the
delivery ledger had already claimed the occurrence's dispatch slot. The ledger
row was permanently stranded at ``dispatched`` (never ``delivered``, never
retryable) and the exception was swallowed by `_notify_failure`'s outer B5
catch — a silent drop with no log signal beyond "failure alert delivery
raised". Fixed at ``scheduler.py`` (``_notify_failure``) by passing
``urgency="critical"`` instead. This test exercises the real
``JobScheduler._mark_failed`` -> ``_notify_failure`` path (not a re-derived
call) and asserts the ledger records an ACTUAL 'delivered' row — reusing the
exact fixture pattern (``ChannelRegistry`` + ``NotificationRouter`` +
``ProactiveDeliverer`` + ``ProactiveJobDeliverer`` + ``tmp_db``) from
``test_undelivered_outbox_gate.py`` (the 3db10755 fix's test file).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.delivery_ledger import DeliveryLedger
from stackowl.notifications.proactive_job import ProactiveJobDeliverer
from stackowl.notifications.router import NotificationRouter
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler import JobScheduler

pytestmark = pytest.mark.asyncio


def _settings(default_channel: str = "cli") -> Settings:
    ns = SimpleNamespace(
        notifications=NotificationSettings(default_channel=default_channel)
    )
    return cast(Settings, ns)


class _AlwaysSucceedAdapter:
    """A channel adapter whose send_text always succeeds (mirrors the gate file's)."""

    def __init__(self, name: str = "telegram") -> None:
        self._name = name

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_text(self, text: str, *, chat_id: str | int | None = None) -> None:
        return None


@pytest.fixture(autouse=True)
def _clean_registry():  # type: ignore[no-untyped-def]
    ChannelRegistry.instance().reset()
    yield
    ChannelRegistry.instance().reset()


async def test_retry_exhausted_job_alert_reaches_telegram(tmp_db: DbPool) -> None:
    """A recurring job's retry-exhausted re-arm (F-61) must produce a real
    'delivered' ledger row for telegram — locks in the urgency="critical" fix
    confirmed for goal_execution-063ab221 (failure_count=2, both alerts crashed
    on urgency="high" pre-fix)."""
    TestModeGuard.deactivate()
    ChannelRegistry.instance().register(_AlwaysSucceedAdapter("telegram"))
    router = NotificationRouter(
        db=tmp_db, settings=_settings(), clock=lambda: datetime(2026, 6, 30, tzinfo=UTC)
    )
    deliverer = ProactiveDeliverer(
        router=router, registry=ChannelRegistry.instance(), settings=_settings()
    )
    job_deliverer = ProactiveJobDeliverer(deliverer, DeliveryLedger(tmp_db))

    HandlerRegistry.reset()
    sched = JobScheduler(db=tmp_db, handler_registry=HandlerRegistry.instance(),
                          job_deliverer=job_deliverer)
    job = Job(
        job_id="test-job-1",
        handler_name="goal_execution",
        schedule="every 5m",
        idempotency_key="key-test-job-1",
        last_run_at=None,
        next_run_at="2026-06-30T08:00:00+00:00",
        status="pending",
        target_channels=["telegram"],
        target_addresses={"telegram": 72055773},
    )

    await sched._mark_failed(job, "execute: ProviderError: Request timed out.")

    rows = await tmp_db.fetch_all(
        "SELECT state FROM delivery_attempts WHERE job_id = ? AND channel = 'telegram'",
        (job.job_id,),
    )
    assert len(rows) == 1
    assert rows[0]["state"] == "delivered"
    HandlerRegistry.reset()
