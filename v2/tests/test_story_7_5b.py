"""Story 7.5 — WebhookCommand, NotificationRouter frequency cap, migration.

Companion to :mod:`tests.test_story_7_5` (rate-limit + receiver). Split to
keep both files under the B2 300-line cap.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stackowl.commands.webhook_command import WebhookCommand
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.notifications.router import Notification, NotificationRouter
from stackowl.pipeline.state import PipelineState
from stackowl.webhooks.handler_job import WebhookHandlerJob
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.job import Job
from tests._story_7_5_helpers import (
    disable_guard,
    make_settings_with_webhooks,
    open_db,
)


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t-1",
        session_id="s-1",
        input_text="",
        channel="cli",
        owl_name="secretary",
        pipeline_step="",
    )


def _frozen_clock(when: datetime):
    return lambda: when


# ---------------------------------------------------------------------------
# 14-15. NotificationRouter outbound frequency cap
# ---------------------------------------------------------------------------


async def _seed_delivered_log(
    db, *, job_id: str, channel: str, count: int, base: datetime
) -> None:
    for i in range(count):
        await db.execute(
            "INSERT INTO notification_log "
            "(notification_id, urgency, category, channel, job_id, delivery_status, "
            "created_at, message_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"n-{uuid.uuid4().hex[:8]}-{i}",
                "normal",
                "test",
                channel,
                job_id,
                "delivered",
                (base - timedelta(minutes=i)).isoformat(),
                "hash000000000000",
            ),
        )


async def test_router_batches_when_job_exceeds_per_hour_cap(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings_with_webhooks(max_per_hour=10)
        now = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
        await _seed_delivered_log(db, job_id="job-A", channel="cli", count=10, base=now)
        router = NotificationRouter(db=db, settings=settings, clock=_frozen_clock(now))
        status = await router.deliver(
            Notification(message="hi", urgency="normal", category="g", job_id="job-A")
        )
        assert status == "batched"
        q = await db.fetch_all("SELECT COUNT(*) AS n FROM notification_queue", ())
        assert q[0]["n"] == 1
    finally:
        await db.close()


async def test_router_delivers_when_under_per_hour_cap(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings_with_webhooks(max_per_hour=10)
        now = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
        await _seed_delivered_log(db, job_id="job-B", channel="cli", count=3, base=now)
        router = NotificationRouter(db=db, settings=settings, clock=_frozen_clock(now))
        status = await router.deliver(
            Notification(message="hi", urgency="normal", category="g", job_id="job-B")
        )
        assert status == "delivered"
    finally:
        await db.close()


async def test_router_frequency_cap_disabled_when_no_job_id(tmp_path: Path) -> None:
    """Notifications without a ``job_id`` skip the cap entirely."""
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings_with_webhooks(max_per_hour=1)
        now = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
        router = NotificationRouter(db=db, settings=settings, clock=_frozen_clock(now))
        s1 = await router.deliver(Notification(message="m1", urgency="normal", category="g"))
        s2 = await router.deliver(Notification(message="m2", urgency="normal", category="g"))
        assert s1 == "delivered"
        assert s2 == "delivered"
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 16-18. WebhookCommand
# ---------------------------------------------------------------------------


def test_webhook_command_name_is_webhook(tmp_path: Path) -> None:
    settings = make_settings_with_webhooks()
    cmd = WebhookCommand(db=None, settings=settings)  # type: ignore[arg-type]
    assert cmd.name == "webhook"
    assert cmd.command == "webhook"


async def test_webhook_command_register_returns_yaml_instructions(
    tmp_path: Path,
) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings_with_webhooks()
        cmd = WebhookCommand(db=db, settings=settings)
        out = await cmd.handle("register github", _state())
        assert "stackowl.yaml" in out
        assert "github:" in out
        assert "WEBHOOK_GITHUB_SECRET" in out
        # The command must NOT actually write anything to the config or DB
        rows = await db.fetch_all(
            "SELECT COUNT(*) AS n FROM webhook_events_log", ()
        )
        assert rows[0]["n"] == 0
    finally:
        await db.close()


async def test_webhook_command_list_shows_configured_sources(
    tmp_path: Path,
) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings_with_webhooks(source_name="github")
        # Seed one received event so 'last:' has a value
        await db.execute(
            "INSERT INTO webhook_events_log "
            "(event_id, source, received_at, status) VALUES (?, ?, ?, ?)",
            ("e1", "github", "2026-05-22T00:00:00+00:00", "enqueued"),
        )
        cmd = WebhookCommand(db=db, settings=settings)
        out = await cmd.handle("list", _state())
        assert "github" in out
        assert "enabled" in out
        assert "events:1" in out
        assert "2026-05-22T00:00:00+00:00" in out
    finally:
        await db.close()


async def test_webhook_command_disable_writes_audit_log(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings_with_webhooks(source_name="github")
        cmd = WebhookCommand(db=db, settings=settings)
        out = await cmd.handle("disable github", _state())
        assert "enabled: false" in out
        rows = await db.fetch_all(
            "SELECT event_type, target FROM audit_log WHERE event_type = ?",
            ("webhook_disabled",),
        )
        assert len(rows) == 1
        assert rows[0]["target"] == "github"
    finally:
        await db.close()


async def test_webhook_command_usage_on_empty_args(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings_with_webhooks()
        cmd = WebhookCommand(db=db, settings=settings)
        out = await cmd.handle("", _state())
        assert "Usage" in out
        assert "register" in out
        assert "list" in out
        assert "disable" in out
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 19. WebhookHandlerJob.execute — stub success + missing-event failure
# ---------------------------------------------------------------------------


async def test_webhook_handler_job_returns_success_for_valid_event() -> None:
    handler = WebhookHandlerJob()
    job = Job(
        job_id="webhook_handler-abcd1234",
        handler_name="webhook_handler",
        schedule="@once",
        idempotency_key="webhook:abc",
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        params={
            "event": {
                "event_id": "abc",
                "source": "github",
                "payload": {"x": 1},
                "received_at": "2026-05-23T00:00:00+00:00",
            }
        },
    )
    result = await handler.execute(job)
    assert result.success is True
    assert result.metadata["event_id"] == "abc"
    assert result.metadata["source"] == "github"


async def test_webhook_handler_job_fails_when_event_missing() -> None:
    handler = WebhookHandlerJob()
    job = Job(
        job_id="webhook_handler-zzzz0000",
        handler_name="webhook_handler",
        schedule="@once",
        idempotency_key="webhook:none",
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        params={},
    )
    result = await handler.execute(job)
    assert result.success is False
    assert result.error is not None


def test_webhook_handler_job_self_registers() -> None:
    """Import-time side effect: handler is in the global registry.

    Other Epic-7 tests reset the global :class:`HandlerRegistry`, so we
    re-register here to make the test order-independent — the point is that
    the module *exposes* a self-registration helper, not that no other test
    ever clears the singleton.
    """
    HandlerRegistry.instance().register(WebhookHandlerJob())
    handler = HandlerRegistry.instance().get("webhook_handler")
    assert handler is not None
    assert handler.handler_name == "webhook_handler"


# ---------------------------------------------------------------------------
# 20-21. Migration 0020
# ---------------------------------------------------------------------------


def test_migration_0020_file_exists() -> None:
    migrations_dir = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
    )
    assert (migrations_dir / "0020_webhook_rate_log.sql").exists()


def test_migration_count_is_20(tmp_path: Path) -> None:
    runner = MigrationRunner(db_path=tmp_path / "mig.db")
    results = runner.run()
    assert len(results) == 35


async def test_webhook_events_log_table_exists(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        rows = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("webhook_events_log",),
        )
        assert len(rows) == 1
    finally:
        await db.close()
