"""Story 7.4 — Notification model, NotificationRouter routing decisions.

Command surface, digest job, and migration tests live in
:mod:`tests.test_story_7_4b` to keep each file under the B2 300-line cap.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from stackowl.notifications.router import Notification, NotificationRouter
from tests._story_7_4_helpers import (
    disable_guard,
    frozen_clock,
    make_settings,
    open_db,
)


# ---------------------------------------------------------------------------
# 1. Notification model
# ---------------------------------------------------------------------------


def test_notification_is_frozen_and_forbid_extra() -> None:
    n = Notification(message="hi", urgency="normal", category="general")
    with pytest.raises(ValidationError):
        n.message = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Notification(  # type: ignore[call-arg]
            message="hi", urgency="normal", category="general", unknown_field="x"
        )


# ---------------------------------------------------------------------------
# 2-6. Routing decisions
# ---------------------------------------------------------------------------


async def test_deliver_critical_in_quiet_hours_still_delivers(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings(quiet_enabled=True)
        midnight = datetime(2026, 5, 23, 23, 0, tzinfo=UTC)
        router = NotificationRouter(db=db, settings=settings, clock=frozen_clock(midnight))
        status = await router.deliver(
            Notification(message="boom", urgency="critical", category="alarm")
        )
        assert status == "delivered"
    finally:
        await db.close()


async def test_deliver_normal_in_quiet_hours_is_batched(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings(quiet_enabled=True)
        midnight = datetime(2026, 5, 23, 23, 0, tzinfo=UTC)
        router = NotificationRouter(db=db, settings=settings, clock=frozen_clock(midnight))
        status = await router.deliver(
            Notification(message="hi", urgency="normal", category="general")
        )
        assert status == "batched"
        rows = await db.fetch_all("SELECT COUNT(*) AS n FROM notification_queue", ())
        assert rows[0]["n"] == 1
    finally:
        await db.close()


async def test_deliver_low_in_quiet_hours_is_batched(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings(quiet_enabled=True)
        midnight = datetime(2026, 5, 23, 23, 0, tzinfo=UTC)
        router = NotificationRouter(db=db, settings=settings, clock=frozen_clock(midnight))
        status = await router.deliver(
            Notification(message="trivia", urgency="low", category="general")
        )
        assert status == "batched"
    finally:
        await db.close()


async def test_deliver_normal_in_soft_focus_outside_quiet_is_batched(
    tmp_path: Path,
) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings(quiet_enabled=False)
        noon = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
        router = NotificationRouter(db=db, settings=settings, clock=frozen_clock(noon))
        router.set_focus_mode("soft")
        status = await router.deliver(
            Notification(message="hi", urgency="normal", category="general")
        )
        assert status == "batched"
    finally:
        await db.close()


async def test_deliver_low_in_hard_focus_outside_quiet_is_suppressed(
    tmp_path: Path,
) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings(quiet_enabled=False)
        noon = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
        router = NotificationRouter(db=db, settings=settings, clock=frozen_clock(noon))
        router.set_focus_mode("hard")
        status = await router.deliver(
            Notification(message="trivia", urgency="low", category="general")
        )
        assert status == "suppressed"
        rows = await db.fetch_all("SELECT COUNT(*) AS n FROM notification_queue", ())
        assert rows[0]["n"] == 0
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 7-8. notification_log + hash semantics
# ---------------------------------------------------------------------------


async def test_deliver_writes_notification_log_row(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings()
        noon = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
        router = NotificationRouter(db=db, settings=settings, clock=frozen_clock(noon))
        await router.deliver(
            Notification(message="hi", urgency="normal", category="general")
        )
        rows = await db.fetch_all(
            "SELECT delivery_status, urgency, category, channel FROM notification_log",
            (),
        )
        assert len(rows) == 1
        assert rows[0]["delivery_status"] == "delivered"
        assert rows[0]["urgency"] == "normal"
        assert rows[0]["channel"] == "cli"
    finally:
        await db.close()


async def test_log_stores_message_hash_not_raw_message(
    tmp_path: Path, capture_logs: list[dict[str, Any]]
) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        settings = make_settings()
        secret = "PII-SECRET-NEVER-LEAK-9999"
        router = NotificationRouter(db=db, settings=settings)
        await router.deliver(
            Notification(message=secret, urgency="normal", category="general")
        )
        rows = await db.fetch_all("SELECT message_hash FROM notification_log", ())
        assert len(rows) == 1
        stored = rows[0]["message_hash"]
        expected = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]
        assert stored == expected
        # Raw message must not leak into any log record
        for rec in capture_logs:
            joined = " ".join(str(v) for v in rec.get("fields", {}).values())
            assert secret not in rec["msg"]
            assert secret not in joined
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 9-10. Health probe
# ---------------------------------------------------------------------------


async def test_health_returns_ok_when_queue_empty(tmp_path: Path) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        router = NotificationRouter(db=db, settings=make_settings())
        report = await router.health()
        assert report.status == "ok"
        assert report.details["queue_depth"] == 0
    finally:
        await db.close()


async def test_health_returns_degraded_when_queue_over_threshold(
    tmp_path: Path,
) -> None:
    disable_guard()
    db = await open_db(tmp_path)
    try:
        for i in range(101):
            await db.execute(
                "INSERT INTO notification_queue "
                "(notification_id, message_hash, urgency, category, channel, scheduled_for) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"id-{i}", f"h-{i:04x}"[:16], "normal", "test", "cli", "2026-01-01T00:00:00+00:00"),
            )
        router = NotificationRouter(db=db, settings=make_settings())
        report = await router.health()
        assert report.status == "degraded"
        assert report.details["queue_depth"] == 101
    finally:
        await db.close()
