"""Story 7.4 — command surface, NotificationDigestJob, and migration smoke tests.

Companion to :mod:`tests.test_story_7_4` (router + model). Split to keep both
files under the B2 300-line cap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stackowl.commands.focus_command import FocusCommand
from stackowl.commands.notifications_command import NotificationsMissedCommand
from stackowl.commands.quiet_command import QuietHoursCommand
from stackowl.commands.urgent_command import UrgentCommand
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.events.bus import EventBus
from stackowl.notifications.digest_job import NotificationDigestJob
from stackowl.notifications.router import NotificationRouter
from tests._story_7_4_helpers import (
    disable_guard,
    frozen_clock,
    make_job,
    make_settings,
    make_state,
    open_db,
)


# ---------------------------------------------------------------------------
# 11-14. FocusCommand
# ---------------------------------------------------------------------------


async def test_focus_command_default_sets_soft(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        router = NotificationRouter(db=db, settings=make_settings())
        cmd = FocusCommand(router=router, event_bus=EventBus())
        out = await cmd.handle("", make_state())
        assert out == "focus_mode:soft"
        assert router.get_focus_mode() == "soft"
    finally:
        await db.close()


async def test_focus_command_hard_sets_hard(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        router = NotificationRouter(db=db, settings=make_settings())
        cmd = FocusCommand(router=router, event_bus=EventBus())
        out = await cmd.handle("--hard", make_state())
        assert out == "focus_mode:hard"
        assert router.get_focus_mode() == "hard"
    finally:
        await db.close()


async def test_focus_command_off_sets_off(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        router = NotificationRouter(db=db, settings=make_settings())
        router.set_focus_mode("hard")
        cmd = FocusCommand(router=router, event_bus=EventBus())
        out = await cmd.handle("off", make_state())
        assert out == "focus_mode:off"
        assert router.get_focus_mode() == "off"
    finally:
        await db.close()


async def test_focus_command_emits_event(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        router = NotificationRouter(db=db, settings=make_settings())
        bus = EventBus()
        captured: list[Any] = []
        bus.subscribe("focus_mode_changed", lambda payload: captured.append(payload))
        cmd = FocusCommand(router=router, event_bus=bus)
        await cmd.handle("--hard", make_state())
        assert captured == [{"mode": "hard"}]
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 15. QuietHoursCommand
# ---------------------------------------------------------------------------


async def test_quiet_command_writes_override_row(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        cmd = QuietHoursCommand(db=db)
        out = await cmd.handle("22:00 08:00", make_state())
        assert "global" in out
        rows = await db.fetch_all(
            "SELECT start_time, end_time, category FROM notification_overrides", ()
        )
        assert len(rows) == 1
        assert rows[0]["start_time"] == "22:00"
        assert rows[0]["end_time"] == "08:00"
        assert rows[0]["category"] is None
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 16-17. UrgentCommand
# ---------------------------------------------------------------------------


async def test_urgent_command_delivers_critical(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings(quiet_enabled=True)  # critical bypasses
        midnight = datetime(2026, 5, 23, 23, 0, tzinfo=UTC)
        router = NotificationRouter(db=db, settings=settings, clock=frozen_clock(midnight))
        cmd = UrgentCommand(router=router, channels=["cli", "telegram"])
        out = await cmd.handle("server down", make_state())
        assert "broadcast to 2 channels" in out
        rows = await db.fetch_all(
            "SELECT urgency, delivery_status FROM notification_log", ()
        )
        assert len(rows) == 2
        assert all(r["urgency"] == "critical" for r in rows)
        assert all(r["delivery_status"] == "delivered" for r in rows)
    finally:
        await db.close()


async def test_urgent_command_empty_message_returns_error(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        router = NotificationRouter(db=db, settings=make_settings())
        cmd = UrgentCommand(router=router)
        out = await cmd.handle("   ", make_state())
        assert "urgent: message required" in out
        assert "<message>" in out
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 18. NotificationsMissedCommand
# ---------------------------------------------------------------------------


async def test_notifications_missed_filters_correctly(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        seed = [
            ("a", "normal", "test", "cli", "delivered"),
            ("b", "low", "test", "cli", "suppressed"),
            ("c", "normal", "test", "cli", "batched"),
            ("d", "critical", "test", "cli", "failed"),
        ]
        for nid, urg, cat, ch, status in seed:
            await db.execute(
                "INSERT INTO notification_log "
                "(notification_id, urgency, category, channel, delivery_status, "
                "created_at, message_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (nid, urg, cat, ch, status, "2026-05-23T00:00:00+00:00", "hash000000000000"),
            )
        cmd = NotificationsMissedCommand(db=db)
        out = await cmd.handle("missed", make_state())
        assert out.startswith("missed:3")  # delivered excluded
        assert "suppressed" in out
        assert "batched" in out
        assert "failed" in out
        for line in out.splitlines()[1:]:
            assert "delivered" not in line
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 19-20. NotificationDigestJob
# ---------------------------------------------------------------------------


async def test_digest_job_flushes_due_rows(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        past = "2020-01-01T00:00:00+00:00"
        future = "2099-01-01T00:00:00+00:00"
        rows = [("p1", past), ("p2", past), ("f1", future)]
        for nid, sched in rows:
            await db.execute(
                "INSERT INTO notification_queue "
                "(notification_id, message_hash, urgency, category, channel, scheduled_for) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (nid, "hash000000000000", "normal", "test", "cli", sched),
            )
        handler = NotificationDigestJob(db=db)
        result = await handler.execute(make_job())
        assert result.success is True
        assert result.metadata["flushed"] == 2

        remaining = await db.fetch_all(
            "SELECT notification_id FROM notification_queue", ()
        )
        assert {r["notification_id"] for r in remaining} == {"f1"}

        logs = await db.fetch_all(
            "SELECT delivery_status FROM notification_log WHERE delivery_status = ?",
            ("delivered",),
        )
        assert len(logs) == 2
    finally:
        await db.close()


async def test_digest_job_returns_flushed_count_in_metadata(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        handler = NotificationDigestJob(db=db)
        result = await handler.execute(make_job())
        assert result.success is True
        assert "flushed" in result.metadata
        assert result.metadata["flushed"] == 0
        assert result.output == "flushed:0"
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 21-22. Migration 0019
# ---------------------------------------------------------------------------


def test_migration_0019_file_exists() -> None:
    migrations_dir = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
    )
    assert (migrations_dir / "0019_notification_overrides.sql").exists()


def test_migration_count_is_19(tmp_path: Path) -> None:
    # Historical name kept for log searchability; expected count is now derived
    # dynamically from the actual .sql files on disk (no more manual bumps).
    migrations_dir = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
    )
    expected = len(sorted(migrations_dir.glob("*.sql")))
    runner = MigrationRunner(db_path=tmp_path / "mig.db")
    results = runner.run()
    assert len(results) == expected
