"""Story 4.8, AC5 -- a scheduled (unattended) delivery under grandfathered/
seeded standing authority runs with NO new approval; the same job with its
authority removed parks instead of delivering.

Gateway-driven: a REAL ``JobScheduler._poll()`` claims a due job and drives
it through the REAL ``MorningBriefHandler``/``GoalExecutionHandler`` ->
``commands/spec/submit.py::submit_command`` -> ``notifications/commands.py``'s
registered handler -> the REAL ``ProactiveDeliverer``/``NotificationRouter``
against a migrated sqlite db (mirrors ``tests/journeys/test_morning_brief_
delivers.py``'s own harness). Only the channel transport (adapter.send_text)
is faked.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from stackowl.authz.delivery_grandfather import grandfather_existing_job_delivery_authority
from stackowl.channels.registry import ChannelRegistry
from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import BriefSettings, Settings, SystemSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.delivery_ledger import DeliveryLedger
from stackowl.notifications.router import NotificationRouter
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
from stackowl.scheduler.handlers.morning_brief import MorningBriefHandler
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job
from tests._schema_template import seed_schema
from tests._story_7_2_helpers import StubBackend

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
            raise RuntimeError("no chat target (fresh process, _last_chat_id is None)")
        self.sends.append((text, chat_id if chat_id is not None else self._last_chat_id))


def _settings() -> Settings:
    return Settings(
        notifications=NotificationSettings(), brief=BriefSettings(),
        system=SystemSettings(timezone="UTC"),
    )


class _StubBridge:
    async def recall(self, *_a: Any, **_k: Any) -> list[Any]:
        return []

    async def list_staged(self, *_a: Any, **_k: Any) -> list[Any]:
        return []


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "sched.db"
    seed_schema(db_path)
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


def _wire_morning_brief(
    db: DbPool, settings: Settings, adapter: _RecordingTelegramAdapter,
) -> tuple[JobScheduler, ProactiveDeliverer]:
    ChannelRegistry.instance().register(cast(Any, adapter))
    router = NotificationRouter(db=db, settings=settings)
    deliverer = ProactiveDeliverer(router=router, registry=ChannelRegistry.instance(), settings=settings)
    ledger = DeliveryLedger(db=db)
    scheduler = JobScheduler(db=db)
    handler = MorningBriefHandler(
        memory_bridge=cast(Any, _StubBridge()), scheduler=scheduler, db=db,
        event_bus=EventBus(), settings=settings,
        proactive_deliverer=deliverer, delivery_ledger=ledger,
    )
    HandlerRegistry.instance().register(handler)
    return scheduler, deliverer


async def _seed_enabled_brief_job(db: DbPool) -> Job:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    job = Job(
        job_id="morning_brief-existing01",
        handler_name="morning_brief",
        schedule="daily@08:00",
        idempotency_key="morning_brief:daily",
        last_run_at=None,
        next_run_at=past,
        status="pending",
        target_channels=["telegram"],
        target_addresses={"telegram": 12345},
    )
    await insert_job(db, job)
    return job


async def test_grandfathered_morning_brief_delivers_with_no_new_approval(
    migrated_db: DbPool,
) -> None:
    """The morning-brief job existed BEFORE Story 4.8 (an `enabled=1` row in
    `jobs`); the grandfathering routine runs once at boot; the next
    unattended poll delivers the brief with no approval item."""
    settings = _settings()
    adapter = _RecordingTelegramAdapter()
    scheduler, deliverer = _wire_morning_brief(migrated_db, settings, adapter)
    await _seed_enabled_brief_job(migrated_db)

    # Boot-time grandfathering (startup/orchestrator.py's own call site).
    granted = await grandfather_existing_job_delivery_authority(migrated_db)
    assert granted == 1

    token = set_services(StepServices(db_pool=migrated_db, proactive_deliverer=deliverer))
    try:
        await scheduler._poll()
    finally:
        reset_services(token)

    assert len(adapter.sends) == 1, "grandfathered authority let the brief deliver at once"
    text, chat_id = adapter.sends[0]
    assert chat_id == 12345
    assert text

    # No approval item was opened — the task ran to completion, never parked.
    rows = await migrated_db.fetch_all(
        "SELECT status FROM tasks WHERE command_type = 'notifications.deliver_brief'",
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


async def test_same_job_without_grandfathering_parks_instead_of_delivering(
    migrated_db: DbPool,
) -> None:
    """AC5's control: remove the grandfathering step (never call it) and the
    SAME unattended run parks for an approval instead of delivering — proving
    the FIRST test's success actually depends on the grant, not on the
    delivery mechanism alone."""
    settings = _settings()
    adapter = _RecordingTelegramAdapter()
    scheduler, deliverer = _wire_morning_brief(migrated_db, settings, adapter)
    await _seed_enabled_brief_job(migrated_db)
    # Deliberately NOT calling grandfather_existing_job_delivery_authority.

    token = set_services(StepServices(db_pool=migrated_db, proactive_deliverer=deliverer))
    try:
        await scheduler._poll()
    finally:
        reset_services(token)

    assert adapter.sends == [], "no matching authority — the brief must NOT deliver"
    rows = await migrated_db.fetch_all(
        "SELECT status FROM tasks WHERE command_type = 'notifications.deliver_brief'",
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "parked"


async def _seed_enabled_goal_job(db: DbPool) -> Job:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    job = Job(
        job_id="goal_execution-existing01",
        handler_name="goal_execution",
        schedule="daily@09:00",
        idempotency_key="goal:daily",
        last_run_at=None,
        next_run_at=past,
        status="pending",
        target_channels=["telegram"],
        target_addresses={"telegram": 99999},
        params={"goal": "daily digest"},
    )
    await insert_job(db, job)
    return job


async def test_grandfathered_goal_execution_delivers_with_no_new_approval(
    migrated_db: DbPool,
) -> None:
    settings = _settings()
    adapter = _RecordingTelegramAdapter()
    ChannelRegistry.instance().register(cast(Any, adapter))
    router = NotificationRouter(db=migrated_db, settings=settings)
    deliverer = ProactiveDeliverer(
        router=router, registry=ChannelRegistry.instance(), settings=settings,
    )
    scheduler = JobScheduler(db=migrated_db)
    backend = StubBackend(response_text="here is your daily digest")
    handler = GoalExecutionHandler(
        backend=backend, db=migrated_db, settings=settings, job_deliverer=object(),  # type: ignore[arg-type]
    )
    HandlerRegistry.instance().register(handler)
    await _seed_enabled_goal_job(migrated_db)

    granted = await grandfather_existing_job_delivery_authority(migrated_db)
    assert granted == 1

    token = set_services(StepServices(db_pool=migrated_db, proactive_deliverer=deliverer))
    try:
        await scheduler._poll()
    finally:
        reset_services(token)

    assert len(adapter.sends) == 1
    text, chat_id = adapter.sends[0]
    assert chat_id == 99999
    assert "daily digest" in text
