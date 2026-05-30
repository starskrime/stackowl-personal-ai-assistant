"""E7-S0: batched body persistence + digest-flush transport + migration idempotency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.digest_job import NotificationDigestJob
from stackowl.notifications.router import Notification, NotificationRouter
from stackowl.scheduler.job import Job

def _settings() -> Settings:
    return cast(Settings, SimpleNamespace(notifications=NotificationSettings()))


class _RecordingAdapter:
    def __init__(self, name: str = "cli") -> None:
        self._name = name
        self.sent: list[str] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


class _FailingAdapter:
    """Adapter whose send_text always raises — drives transport to ``failed``."""

    def __init__(self, name: str = "cli") -> None:
        self._name = name
        self.attempts = 0

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_text(self, text: str) -> None:
        self.attempts += 1
        raise RuntimeError("adapter down")


def _job() -> Job:
    return Job(
        job_id="digest-job",
        handler_name="notification_digest",
        schedule="every 5m",
        idempotency_key="k",
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
    )


@pytest.fixture(autouse=True)
def _clean_registry():  # type: ignore[no-untyped-def]
    ChannelRegistry.instance().reset()
    yield
    ChannelRegistry.instance().reset()


async def test_batched_persists_body_then_flush_transports(tmp_db: DbPool) -> None:
    TestModeGuard.deactivate()
    adapter = _RecordingAdapter("cli")
    ChannelRegistry.instance().register(adapter)

    router = NotificationRouter(
        db=tmp_db, settings=_settings(),
        clock=lambda: datetime(2026, 5, 30, tzinfo=UTC),
    )
    deliverer = ProactiveDeliverer(
        router=router, registry=ChannelRegistry.instance(), settings=_settings()
    )
    router.set_focus_mode("soft")  # forces a normal-urgency note to batch

    note = Notification(message="queued message", urgency="normal", category="c")
    status = await deliverer.deliver(note)
    assert status == "batched"
    assert adapter.sent == []  # not sent yet

    # Body persisted in the new column.
    rows = await tmp_db.fetch_all("SELECT message FROM notification_queue", ())
    assert len(rows) == 1
    assert rows[0]["message"] == "queued message"

    # Make the queued row due, then flush via the digest.
    await tmp_db.execute(
        "UPDATE notification_queue SET scheduled_for = ?",
        ((datetime(2026, 5, 30, tzinfo=UTC) - timedelta(hours=1)).isoformat(),),
    )
    digest = NotificationDigestJob(db=tmp_db, deliverer=deliverer)
    result = await digest.execute(_job())

    assert result.success is True
    assert adapter.sent == ["queued message"]  # transported on flush
    remaining = await tmp_db.fetch_all("SELECT notification_id FROM notification_queue", ())
    assert remaining == []  # row deleted


async def test_legacy_row_without_body_audit_only_flush(tmp_db: DbPool) -> None:
    TestModeGuard.deactivate()
    adapter = _RecordingAdapter("cli")
    ChannelRegistry.instance().register(adapter)

    # Insert a legacy-style row with NULL message (body column absent).
    await tmp_db.execute(
        "INSERT INTO notification_queue "
        "(notification_id, message_hash, urgency, category, channel, job_id, scheduled_for) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-1", "abcd1234", "normal", "c", "cli", None,
            (datetime(2026, 5, 30, tzinfo=UTC) - timedelta(hours=1)).isoformat(),
        ),
    )
    deliverer = ProactiveDeliverer(
        router=cast(NotificationRouter, SimpleNamespace()),
        registry=ChannelRegistry.instance(),
        settings=_settings(),
    )
    digest = NotificationDigestJob(db=tmp_db, deliverer=deliverer)
    result = await digest.execute(_job())

    assert result.success is True
    assert adapter.sent == []  # audit-only — no transport for NULL body
    remaining = await tmp_db.fetch_all("SELECT notification_id FROM notification_queue", ())
    assert remaining == []  # still deleted


async def test_flush_transport_failed_retains_row_no_false_delivered(tmp_db: DbPool) -> None:
    """Regression: a failed transport must NOT delete the row or log 'delivered'.

    Otherwise the batched message is lost and the audit trail lies. The row's
    scheduled_for stays <= now so the next digest tick retries it.
    """
    TestModeGuard.deactivate()
    adapter = _FailingAdapter("cli")
    ChannelRegistry.instance().register(adapter)

    router = NotificationRouter(
        db=tmp_db, settings=_settings(),
        clock=lambda: datetime(2026, 5, 30, tzinfo=UTC),
    )
    deliverer = ProactiveDeliverer(
        router=router, registry=ChannelRegistry.instance(), settings=_settings()
    )
    router.set_focus_mode("soft")  # batch a normal-urgency note

    note = Notification(message="undeliverable", urgency="normal", category="c")
    assert await deliverer.deliver(note) == "batched"

    await tmp_db.execute(
        "UPDATE notification_queue SET scheduled_for = ?",
        ((datetime(2026, 5, 30, tzinfo=UTC) - timedelta(hours=1)).isoformat(),),
    )
    digest = NotificationDigestJob(db=tmp_db, deliverer=deliverer)
    result = await digest.execute(_job())

    assert result.success is True
    assert adapter.attempts == 2  # one send + one bounded retry, then failed
    # Row RETAINED for the next tick — not lost.
    remaining = await tmp_db.fetch_all("SELECT notification_id FROM notification_queue", ())
    assert len(remaining) == 1
    # No lying 'delivered' audit row was written for it.
    logged = await tmp_db.fetch_all(
        "SELECT delivery_status FROM notification_log WHERE delivery_status = 'delivered'", ()
    )
    assert logged == []


async def test_flush_dead_letters_after_max_attempts(tmp_db: DbPool) -> None:
    """Bounded retry: a row that has already failed up to the cap is dead-lettered
    (a 'failed' audit row is written + the row removed) instead of looping forever."""
    TestModeGuard.deactivate()
    adapter = _FailingAdapter("cli")
    ChannelRegistry.instance().register(adapter)
    deliverer = ProactiveDeliverer(
        router=cast(NotificationRouter, SimpleNamespace()),
        registry=ChannelRegistry.instance(),
        settings=_settings(),
    )

    # Row already at attempts=4 (cap is 5): this flush is the 5th failure.
    await tmp_db.execute(
        "INSERT INTO notification_queue "
        "(notification_id, message_hash, urgency, category, channel, job_id, "
        "scheduled_for, message, attempts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "dl-1", "deadbeef", "normal", "c", "cli", None,
            (datetime(2026, 5, 30, tzinfo=UTC) - timedelta(hours=1)).isoformat(),
            "poisoned", 4,
        ),
    )
    digest = NotificationDigestJob(db=tmp_db, deliverer=deliverer)
    await digest.execute(_job())

    # Row removed (no longer hot-looping the queue).
    remaining = await tmp_db.fetch_all("SELECT notification_id FROM notification_queue", ())
    assert remaining == []
    # A 'failed' audit row records the dead-letter; no false 'delivered'.
    statuses = await tmp_db.fetch_all("SELECT delivery_status FROM notification_log", ())
    values = {r["delivery_status"] for r in statuses}
    assert "failed" in values
    assert "delivered" not in values


def test_migration_0037_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "idem.db"
    # First run applies all migrations including 0037.
    MigrationRunner(db_path=db_path).run()
    # Second run must be a clean no-op (no duplicate-column error).
    results = MigrationRunner(db_path=db_path).run()
    statuses = {r.version: r.action for r in results}
    assert statuses.get("0037") == "skipped"
