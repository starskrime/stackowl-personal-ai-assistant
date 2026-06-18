"""C1 / F102 — CheckIn heartbeat actually sends (was a permanent success no-op).

A scheduled ``check_in`` job, run by the REAL scheduler poll on a fresh process,
must assemble a check-in body and deliver it through the SAME seam + DeliverySpec +
ledger as the morning brief, to the DURABLY-resolved recipient. And a check_in with
NO resolvable recipient must NOT send and must NOT be recorded as delivered — the
honest ``skipped`` outcome, never a dressed-up give-up (``success=True`` as
delivery).

Mocks ONLY the channel transport (a recording adapter). The body assembles
deterministically from the DB — no AI provider in the path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import BriefSettings, Settings, SystemSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.delivery_ledger import DeliveryLedger
from stackowl.notifications.router import NotificationRouter
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.check_in import CheckInHandler
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job

pytestmark = pytest.mark.asyncio


class _RecordingTelegramAdapter:
    def __init__(self, name: str = "telegram") -> None:
        self._name = name
        self._last_chat_id: int | None = None
        self.sends: list[tuple[str, Any]] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_text(self, text: str, *, chat_id: str | int | None = None) -> None:
        if chat_id is None and self._last_chat_id is None:
            raise RuntimeError("no chat target (fresh process)")
        self.sends.append((text, chat_id if chat_id is not None else self._last_chat_id))


def _settings() -> Settings:
    return Settings(
        notifications=NotificationSettings(),
        brief=BriefSettings(channels=["telegram"]),
        system=SystemSettings(timezone="UTC"),
    )


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "sched.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _clean() -> AsyncIterator[None]:  # type: ignore[misc]
    HandlerRegistry.reset()
    ChannelRegistry.instance().reset()
    TestModeGuard.deactivate()
    yield
    HandlerRegistry.reset()
    ChannelRegistry.instance().reset()
    TestModeGuard.deactivate()


def _wire(
    db: DbPool, settings: Settings, adapter: _RecordingTelegramAdapter
) -> JobScheduler:
    ChannelRegistry.instance().register(cast(Any, adapter))
    router = NotificationRouter(db=db, settings=settings)
    deliverer = ProactiveDeliverer(
        router=router, registry=ChannelRegistry.instance(), settings=settings
    )
    ledger = DeliveryLedger(db=db)
    scheduler = JobScheduler(db=db)

    class _StubBridge:
        async def recall(self, *_a: Any, **_k: Any) -> list[Any]:
            return []

        async def list_staged(self, *_a: Any, **_k: Any) -> list[Any]:
            return []

    handler = CheckInHandler(
        memory_bridge=cast(Any, _StubBridge()),
        scheduler=scheduler,
        db=db,
        settings=settings,
        proactive_deliverer=deliverer,
        delivery_ledger=ledger,
    )
    HandlerRegistry.instance().register(handler)
    return scheduler


async def _seed_due_check_in(
    db: DbPool,
    *,
    target_channels: list[str],
    target_addresses: dict[str, str | int],
) -> None:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    job = Job(
        job_id="check_in-fresh01",
        handler_name="check_in",
        schedule="daily@18:00",
        idempotency_key="check_in:daily",
        last_run_at=None,
        next_run_at=past,
        status="pending",
        target_channels=target_channels,
        target_addresses=target_addresses,
    )
    await insert_job(db, job)


async def test_check_in_delivers_to_durable_target(migrated_db: DbPool) -> None:
    settings = _settings()
    adapter = _RecordingTelegramAdapter()
    scheduler = _wire(migrated_db, settings, adapter)
    await _seed_due_check_in(
        migrated_db,
        target_channels=["telegram"],
        target_addresses={"telegram": 777},
    )

    await scheduler._poll()

    assert len(adapter.sends) == 1, "the check-in must reach the durable target"
    text, chat_id = adapter.sends[0]
    assert chat_id == 777
    assert text, "a non-empty check-in body is transported"
    rows = await migrated_db.fetch_all(
        "SELECT state FROM delivery_attempts WHERE job_id = ? AND channel = ?",
        ("check_in-fresh01", "telegram"),
    )
    assert rows and rows[0]["state"] == "delivered"


async def test_check_in_no_recipient_is_skipped_not_delivered(migrated_db: DbPool) -> None:
    settings = _settings()
    adapter = _RecordingTelegramAdapter()
    scheduler = _wire(migrated_db, settings, adapter)
    await _seed_due_check_in(
        migrated_db,
        target_channels=["telegram"],
        target_addresses={},  # listed but unresolved
    )

    await scheduler._poll()

    assert adapter.sends == [], "no send when the recipient is unresolved"
    res = await migrated_db.fetch_all(
        "SELECT status FROM job_results WHERE job_id = ? ORDER BY run_at DESC LIMIT 1",
        ("check_in-fresh01",),
    )
    # Honest: never 'delivered'; an unresolved recipient is undeliverable/skipped.
    assert not res or res[0]["status"] != "delivered"
