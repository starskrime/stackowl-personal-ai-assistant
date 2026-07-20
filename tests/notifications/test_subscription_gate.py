"""NotificationSettings.subscriptions — a per-category opt-out that was
defined and documented ("Per-category opt-in map keyed by category name")
but never read anywhere in the router's decision path. Setting a category to
False had zero effect; this wires it as the first check in
NotificationRouter.deliver, ahead of the urgency/quiet-hours/focus table.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.notifications.router import Notification, NotificationRouter

pytestmark = pytest.mark.asyncio


def _settings(subscriptions: dict[str, bool] | None = None) -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            notifications=NotificationSettings(subscriptions=subscriptions or {})
        ),
    )


async def test_opted_out_category_is_suppressed_even_at_normal_urgency(
    tmp_db: DbPool,
) -> None:
    TestModeGuard.deactivate()
    try:
        router = NotificationRouter(
            db=tmp_db,
            settings=_settings({"digest_summary": False}),
            clock=lambda: datetime(2026, 5, 30, tzinfo=UTC),
        )
        note = Notification(message="hi", urgency="normal", category="digest_summary")
        status = await router.deliver(note)
        assert status == "suppressed"
    finally:
        TestModeGuard.activate()


async def test_opted_out_category_suppresses_even_critical_urgency(
    tmp_db: DbPool,
) -> None:
    """An explicit per-category mute is an operator choice about THAT
    category — it must win over urgency, or "mute this category" would
    silently not work for anything tagged critical."""
    TestModeGuard.deactivate()
    try:
        router = NotificationRouter(
            db=tmp_db,
            settings=_settings({"noisy_alert": False}),
            clock=lambda: datetime(2026, 5, 30, tzinfo=UTC),
        )
        note = Notification(message="hi", urgency="critical", category="noisy_alert")
        status = await router.deliver(note)
        assert status == "suppressed"
    finally:
        TestModeGuard.activate()


async def test_unlisted_category_is_unaffected_default_true(tmp_db: DbPool) -> None:
    """No subscriptions configured (the default empty dict) must behave
    byte-identically to before this change — nothing opted out."""
    TestModeGuard.deactivate()
    try:
        router = NotificationRouter(
            db=tmp_db, settings=_settings(), clock=lambda: datetime(2026, 5, 30, tzinfo=UTC),
        )
        note = Notification(message="hi", urgency="critical", category="anything")
        status = await router.deliver(note)
        assert status == "delivered"
    finally:
        TestModeGuard.activate()


async def test_explicit_true_subscription_is_unaffected(tmp_db: DbPool) -> None:
    TestModeGuard.deactivate()
    try:
        router = NotificationRouter(
            db=tmp_db,
            settings=_settings({"digest_summary": True}),
            clock=lambda: datetime(2026, 5, 30, tzinfo=UTC),
        )
        note = Notification(message="hi", urgency="critical", category="digest_summary")
        status = await router.deliver(note)
        assert status == "delivered"
    finally:
        TestModeGuard.activate()
