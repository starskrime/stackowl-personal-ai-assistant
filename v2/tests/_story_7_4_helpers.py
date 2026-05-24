"""Shared helpers for Story 7.4 tests — kept in a non-``test_`` module.

Lets both :mod:`tests.test_story_7_4` and :mod:`tests.test_story_7_4b` stay
under the B2 300-line cap without duplicating fixture code.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from stackowl.config.notification_settings import (
    NotificationSettings,
    QuietHoursSettings,
)
from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.state import PipelineState
from stackowl.scheduler.job import Job


def disable_guard() -> None:
    TestModeGuard.deactivate()


def make_settings(
    *,
    quiet_enabled: bool = False,
    start: str = "22:00",
    end: str = "08:00",
    tz: str = "UTC",
    default_channel: str = "cli",
) -> Settings:
    """Construct a Settings-like object with the requested notifications config.

    We bypass :class:`Settings` construction because its
    ``settings_customise_sources`` drops ``init_settings`` — constructor kwargs
    have no effect.  A :class:`SimpleNamespace` matches the structural contract
    consumed by :class:`NotificationRouter` (only ``settings.notifications``)
    and keeps the tests hermetic from the on-disk ``stackowl.yaml``.
    """
    ns = SimpleNamespace(
        notifications=NotificationSettings(
            default_channel=default_channel,
            quiet_hours=QuietHoursSettings(
                enabled=quiet_enabled,
                start=start,
                end=end,
                timezone=tz,
            ),
        )
    )
    return cast(Settings, ns)


def make_settings_dict(**kwargs: Any) -> dict[str, Any]:
    """Lower-level escape hatch that just returns the kwargs verbatim."""
    return dict(kwargs)


def make_state() -> PipelineState:
    return PipelineState(
        trace_id="t-1",
        session_id="s-1",
        input_text="",
        channel="cli",
        owl_name="secretary",
        pipeline_step="",
    )


def make_job(handler: str = "notification_digest") -> Job:
    return Job(
        job_id=f"job-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="hourly",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
    )


async def open_db(tmp_path: Path) -> DbPool:
    db_path = tmp_path / f"test-{uuid.uuid4().hex[:6]}.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    return pool


def frozen_clock(when: datetime) -> Callable[[], datetime]:
    return lambda: when
